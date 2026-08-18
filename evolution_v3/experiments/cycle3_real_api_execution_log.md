# Cycle 3 真实 AutoDL API 自进化实验日志

## 0. 日志信息

- 日期：2026-08-17
- 实验范围：已有 tuning100 自进化集（100 例）上的 cycle 3
- 验证集：已有 validation50（50 例），本次未重跑
- 实验目标：在不重复运行既有 baseline、cycle 1、cycle 2 的前提下，检查真实 LLM 候选生成、候选门禁和自进化注册流程是否可以正常工作
- 调用入口：AutoDL OpenAI-compatible API
- 模型口径：官方 API 与 AutoDL 使用的是同一 DeepSeek 模型服务；本日志不把它们称作两个不同模型，也不使用“旧 DeepSeek baseline”这一说法
- 并发：数据集的既有 20 worker 配置；另外使用 20 并发做了独立网络预检
- 消融实验：不做
- 重要约束：既有 baseline 结果只读复用；50 例验证集只有在 cycle 3 实际注册新规则后才允许重放

本日志是过程审计记录，不替代原始实验报告。原始报告仍保留在：

`evolution_v3/experiments/tuning100_validation50_experiment_report.md`

---

## 1. 固定输入和保护边界

本次实验开始前固定了以下输入，避免把缓存结果、旧实验和新实验混在一起：

| 内容 | 路径 | 本次处理 |
|---|---|---|
| tuning100 既有自进化数据 | `evolution_v3/` 下的 tuning100 manifest、缓存和工作目录 | 复用，不重跑 baseline |
| tuning100 既有 baseline 缓存 | `runs_v2/v2_100_20w` | 只读复用 |
| validation50 既有冻结结果 | `evolution_v3/work/experiments/validation50_frozen_report.json` | 只读；不因候选未注册而重复执行 |
| cycle 3 最终报告 | `evolution_v3/work/cycles/cycle003.json` | 新生成 |
| cycle 3 实验副本 | `evolution_v3/work/experiments/cycle003_tuning100.json` | 新生成 |
| 自进化 ledger | `datasets/evolution_v2/ledger.jsonl` | 追加事件，不覆盖失败重试记录 |
| 候选/审计缓存 | `evolution_v3/work/cache/` | 复用已经完成的结果 |

### 1.1 规则生成是否调用 API

旧的 template proposer 不调用 LLM API。此次明确使用 `--proposer-mode llm` 后，规则候选生成才调用了真实 AutoDL API。候选生成调用与检测器各阶段调用是两条不同路径：

1. 已有 baseline 和之前 cycle 的检测结果从缓存读取；
2. cycle 3 的 proposer 调 AutoDL API 生成候选规则草案；
3. 候选仍必须通过 V1、V2、V3、M 门禁，LLM 自己声称“有效”不算实验结论；
4. 只有通过门禁并满足边际收益的候选才会写入 registry；
5. 本次没有候选通过全部条件，因此 registry 没有变化，validation50 没有重跑。

---

## 2. 实验前框架扫描

在正式重试前对 detector、provider、evolution runner、gates、proposer、配置和测试目录进行了扫描，重点检查调用链、缓存边界、网络路径、并发和注册条件。

### 2.1 执行链路

实际链路为：

`tuning100 manifest → frozen/cached baseline stages → failure families → proposer → candidate validation → marginal selection → registry → validation50`

其中：

- baseline stages 使用已有缓存，不因 cycle 3 而重新调用模型；
- proposer 可以选择 template 或 llm；
- gates 负责候选的结构、因果和稳定性门禁；
- validation50 位于 registry 固定之后，不能参与候选生成或挑选；
- `--max-admits 1` 约束每个 cycle 最多注册一个合格候选，本次用于保护 registry 不被一次异常提案批量污染。

### 2.2 扫描发现的可调优点

| 位置 | 发现 | 处理 |
|---|---|---|
| provider 超时 | 原实现的 timeout 只限制单次重试，不能限制整个请求 | 增加全局 deadline 和剩余时间约束 |
| Windows urllib | socket read 可能在代理/系统网络层阻塞，线程 timeout 无法可靠回收 | 改为可杀死的子进程 worker，由父进程硬性 timeout |
| 代理继承 | Python 子进程会继承 `HTTP_PROXY`、`HTTPS_PROXY` 等环境变量 | 子进程启动前剥离代理变量，并使用空 `ProxyHandler` |
| 审计噪声候选 | `scanner.system/text_boundary` 会触发上游扫描器重放，违反不重跑 baseline 的约束 | 改成确定性的 `judge.post/evidence_check` 审计候选 |
| LLM schema | proposer 原提示词混合 evidence_check 和 decision_guard 字段，容易生成无效候选 | 改成 discriminated schema，收紧 hook/type/fact/action 枚举 |
| DeepSeek V4 输出 | thinking 模式会耗尽输出预算，`content` 为空，只剩 `reasoning_content` | proposer 路由增加 `thinking.type=disabled`，预算设为 4096 |
| Windows 控制台 | cycle 已保存但打印报告时 GBK 无法编码勾号，导致进程返回异常 | 先保存报告，再以 UTF-8/替换错误字符输出 |
| 容量 | 原配置 `max_width=5`、`max_decision_guards=3` | 提高为 6 和 4；未放宽 V1/V2/V3/M 阈值 |

容量提高只增加可容纳的规则数量，不改变接受标准；本次仍使用 `max-admits=1`，因此不会因容量提高而批量注册候选。

---

## 3. 失败与修复时间线

以下记录保留在 ledger 中，没有删除失败尝试。它们是实验协议和工程修复证据，不应与最终 cycle 3 结果混为一谈。

### 3.1 第一次真实 cycle 3 尝试：总超时未生效

- 现象：20 个并发 audit 请求中已有 14 个结果文件，剩余 6 个长时间不返回；没有 registry 写入。
- 证据：缓存目录 `evolution_v3/work/cache/cached-v5-evidence-guard-v2_100_20w/fac1beaade738...` 中存在 14 个完成结果；ledger 只有 `cycle_started`，随后追加 `cycle_interrupted`。
- 根因：`provider_call` 的 `total_timeout_seconds` 实际上只约束单次 retry，重试累计时间没有上限。
- 修复：使用 `time.monotonic()` 建立总 deadline；每次 backoff 和 retry 都截断到剩余时间内。

### 3.2 第二次尝试：Windows 网络层仍可能阻塞

- 现象：即使 Python 层设置 timeout，urllib 的 socket read 仍可能卡住，线程无法可靠结束。
- 根因：阻塞发生在进程内网络调用，线程级 timeout 不能杀死卡住的底层调用。
- 修复：新增 `detector_v2/provider_worker.py`。父进程为每次请求启动可回收子进程，`subprocess.run(..., timeout=...)` 负责硬截止；子进程只执行一次 urllib 请求。

### 3.3 第三次尝试：审计阶段意外重放上游 scanner

- 现象：候选审计触发 `scanner.system`，导致 20 个案例再次进入上游扫描器调用。
- 风险：这会额外消耗 token，并且违反“已有 baseline 不重复跑”的实验边界。
- 根因：`evolution_v3/gates.py` 中的 `AUDIT-NOISE_ZERO_MARGINAL` 使用了 `type=text_boundary`、`hook=scanner.system`。
- 修复：将审计噪声候选改为确定性的 `type=evidence_check`、`hook=judge.post`，只检查 `evidence_refs_resolve`，不重新调用 scanner、recall、upgrade 或 defense。
- 结果：仍能覆盖 V2/V3/M 逻辑，但不会制造新的上游模型结果。

### 3.4 Windows GBK 控制台输出异常

- 现象：cycle 引擎已经生成并保存了结果，但最终 `print(json.dumps(...))` 输出 `\u2705` 时触发 `UnicodeEncodeError`，造成控制台看起来像失败，且当时缺少实验副本文件。
- 修复：`run_tuning100.py` 改为先写入 cycle report，再输出；stdout 重新配置为 UTF-8，并对不可编码字符使用 backslash replacement。
- 结论：保存结果与控制台展示解耦，后续即使输出失败也不会丢失已完成的实验报告。

### 3.5 proposer 输出预算被 reasoning 消耗

- 现象：1024 token 和 4096 token 的 proposer 请求都出现 `content=""`，完整输出在 `reasoning_content` 中，解析得到 `invalid_json`。
- 诊断：这是 DeepSeek V4 thinking 模式的实际响应行为，不是网络失败，也不是 token 未消耗。直接预检可见 completion budget 被 reasoning 占用。
- 修复：仅对 proposer 路由设置 `extra_body={"thinking":{"type":"disabled"}}`；同时设置 `max_output_tokens=4096`。检测器其他路由保持原配置。
- 修复后证据：真实 proposer 预检返回 `parsed={"ok":true}`，`reasoning_tokens=0`。

### 3.6 proposer 生成混合 intervention schema

- 现象：模型有时把 evidence_check 的字段和 decision_guard 的字段混在同一候选中，导致结构解析失败或语义不一致。
- 修复：将 system prompt 改为两个互斥的 discriminated schema，限定允许的 hook、type、facts、actions，并禁止输出 case ID；所有无效候选记录 `invalid_contract` 原因，而不是静默丢弃。

### 3.7 最终重试

上述修复后，使用真实 AutoDL API、20 worker、LLM proposer 完成了 cycle 3。最终结果见第 6 节。

---

## 4. 直连网络和真实 API 证据

### 4.1 环境审计

实验机上发现以下代理设置：

```text
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897
http_proxy=http://127.0.0.1:7897
https_proxy=http://127.0.0.1:7897
NO_PROXY=localhost,127.0.0.1,::1,token.nuaa.edu.cn,*.nuaa.edu.cn
WinHTTP: Direct access (no proxy server)
WinINET ProxyEnable=1, ProxyServer=127.0.0.1:7897
DNS www.autodl.art -> 101.126.37.23
```

因此不能仅凭系统设置判断“没有代理”。代码层面采取了双重保证：

1. `_DIRECT_OPENER = urlrequest.build_opener(urlrequest.ProxyHandler({}))` 显式禁用 urllib 代理；
2. provider worker 启动时移除大小写两套 HTTP/HTTPS/ALL_PROXY 环境变量。

### 4.2 零 token 直连预检

```text
curl.exe --noproxy "*" --connect-timeout 10 --max-time 20 -sS -D - -o NUL https://www.autodl.art/api/v1
=> HTTP/1.1 404 Not Found
```

这里的 404 是无 token 访问 API 根路径的应用层响应，说明 DNS、TCP、TLS 和 HTTP 直连已经到达 AutoDL；它不是实验请求失败。

### 4.3 真实模型预检

| 请求 | 结果 | model | token 用量 |
|---|---|---|---:|
| Recall/Flash 严格 JSON 预检 | 成功 | `DeepSeek-V4-Flash-0731` | prompt 114 + completion 63 = total 177；reasoning 57 |
| Pro 严格 JSON 预检 | 成功，约 2.7 秒 | `deepseek-v4-pro-202606` | prompt 114 + completion 48 = total 162；reasoning 42 |
| Pro 20 并发最小请求 | 20/20 成功，wall 约 3.031 秒 | `deepseek-v4-pro-202606` | total 2931；每请求 attempts=1 |
| proposer thinking disabled 预检 | 成功，JSON 可解析 | `deepseek-v4-pro-202606` | prompt 16 + completion 5 = total 21；reasoning 0 |

这些数据证明最终实验确实经过真实 AutoDL API，并且并发请求没有因为代理导致统一失败。失败重试阶段也确实消耗过 token；上表只列有完整记录的预检和最终 proposer 用量，不声称是整个账户的完整账单。

---

## 5. 最终实验命令与协议

最终 cycle 3 使用的参数等价于：

```powershell
python evolution_v3/experiments/run_tuning100.py cycle `
  --cycle 3 `
  --max-proposals 3 `
  --max-admits 1 `
  --m-min-marginal 2 `
  --proposer-mode llm
```

协议含义：

- `--max-proposals 3`：最多生成 3 个 LLM 候选；
- `--max-admits 1`：最多注册 1 个候选；
- `--m-min-marginal 2`：要求边际收益达到 2；
- `--proposer-mode llm`：候选生成真实调用 AutoDL API；
- 没有启用 `--no-audit`，因此候选仍经过完整门禁；
- baseline 仍从已有缓存读取；
- validation50 不在该命令中运行。

---

## 6. 最终 cycle 3 结果

### 6.1 总体状态

最终报告：`evolution_v3/work/cycles/cycle003.json`

实验副本：`evolution_v3/work/experiments/cycle003_tuning100.json`

摘要：

| 字段 | 结果 |
|---|---:|
| state | `completed` |
| split | `tuning100` |
| pool | 100 |
| sentinel | 20 |
| gain | 40 |
| patrol | 40 |
| audit passed | true |
| frozen | false |
| registry 新增 | 0 |
| marginal selection reports | 0 |
| joint reports | 0 |
| removed redundant | 0 |
| library rejections | 0 |

### 6.2 真实 LLM 候选

最终共生成 3 个候选，均由 `deepseek-v4-pro-202606` 真实生成：

| intervention | 类型 | hook | 相关 failure family |
|---|---|---|---|
| `IV-3-001` | `evidence_check` | `upgrade.pre` | `FAM-fn-defense_refuted_recall_upgrade` |
| `IV-3-002` | `decision_guard` | `adjudicate.guard` | `FAM-fn-judge_false_no_recall_candidates` |
| `IV-3-003` | `decision_guard` | `adjudicate.guard` | `FAM-fn-judge_false_upgrade_no_upgrade` |

proposal ledger 记录了真实 provider 用量：

```text
generator=llm
model=deepseek-v4-pro-202606
provider_called=true
provider_ok=true
purpose=evolution_proposer
attempts=1
prompt_tokens=1412
completion_tokens=517
total_tokens=1929
reasoning_tokens=0
```

### 6.3 门禁结果

| intervention | V1 | V2 | V3 | M | 失败原因 |
|---|---|---|---|---|---|
| `IV-3-001` | true | false | 未执行 | 未执行 | sentinel harmlessness violation：`new_fn=2, new_fp=1` |
| `IV-3-002` | true | true | false | 未执行 | target polarity mismatch：候选声称 precision，但没有 FP removal，且 TP 有损失 |
| `IV-3-003` | true | false | 未执行 | 未执行 | sentinel harmlessness violation：`new_fn=2` |

解释：

- V1 通过只说明候选结构和基础契约可执行，不说明有效；
- V2 失败表示候选破坏了 sentinel harmlessness；
- `IV-3-002` 虽通过 V2，但 V3 的目标极性与实际效果不一致；
- 因为没有候选通过完整门禁和边际收益条件，所以 M 没有进入可接受状态；
- 这是一个有效的负结果：真实候选被提出、真实执行和裁决，但没有被错误晋升。

### 6.4 registry 结果

cycle 3 结束时 registry 仍为 5 个已有条目：

```text
IV-1-001
IV-1-002
IV-2-001
IV-2-002
IV-2-003
```

没有写入 `IV-3-001`、`IV-3-002` 或 `IV-3-003`。这说明“真实 API 调用成功”与“规则被接受”是两个独立事件。

---

## 7. 为什么没有重跑 validation50

本次不重跑 validation50 是协议要求，而不是实验失败：

1. validation50 只能在 registry 固定后用于验证，不能参与规则生成和选择；
2. cycle 3 没有候选通过 V1/V2/V3/M，也没有 registry 变化；
3. 因此没有新的规则需要在 50 例上验证；
4. 重跑会重复已有验证结果，并增加不必要的 API 消耗。

文件时间证据：

- `validation50_frozen_report.json`：2026-08-17 13:31:16
- `cycle003.json`、`cycle003_tuning100.json`：2026-08-17 15:20:13

validation50 文件仍是已有冻结结果，不能被描述为“cycle 3 新验证结果”。只有下一次出现新 registry ID 后，才应在同一个 50 例集合上做一次 post-evolution validation，并记录新旧 registry、manifest hash 和完整结果文件。

---

## 8. 账本和可复现证据

### 8.1 ledger

`datasets/evolution_v2/ledger.jsonl` 当前有 57 条记录，验证结果为 valid。失败重试、superseded/interrupted 和最终 cycle 3 记录均保留，形成完整时间线。

保留的中断原因包括：

- `provider_total_timeout_not_enforced`
- `windows_blocking_transport_deadline_overrun`
- `audit_upstream_replay_replaced_with_deterministic_audit`
- `unbounded_llm_proposer_output_budget`
- `proposer_reasoning_exhausted_output_budget`
- `proposer_thinking_mode_exhausted_content_budget`
- `llm_mixed_intervention_schema`

这些事件不代表最终 cycle 3 失败，而是说明每次重试具体修复了哪一个可复现问题。

### 8.2 代码和测试

本次涉及的源文件：

- `detector_v2/provider.py`
- `detector_v2/provider_worker.py`
- `detector_v2/tests/test_provider.py`
- `evolution_v3/config.py`
- `evolution_v3/configs/framework.json`
- `evolution_v3/experiments/run_tuning100.py`
- `evolution_v3/gates.py`
- `evolution_v3/proposer.py`
- `evolution_v3/tests/test_contracts_gates.py`

验证结果：

```text
python -m pytest detector_v2/tests evolution_v3/tests tests -q -p no:cacheprovider --basetemp .codex-tmp/<local-run>
160 passed in 1.66s

python -m compileall -q detector_v2 evolution_v3
通过

git diff --check
通过
```

测试通过只说明代码契约和回归测试通过；它不替代真实 API 的 token 证据，也不等同于 detector 效果提升。

---

## 9. 结论

1. 这次 cycle 3 是真实实验：proposer 确实调用 AutoDL API，且有完整 model、provider、usage 和 ledger 记录。
2. 直连网络已通过代码级禁用代理、环境审计和 `curl --noproxy "*"` 三重证据确认；初始失败主要是进程内网络阻塞、审计误触发上游调用、DeepSeek V4 thinking 输出预算和 Windows GBK 输出问题。
3. 20 并发预检 20/20 成功，说明提高并发容量本身不是最终失败原因。
4. 最终生成了 3 个候选，但分别在 V2 或 V3 被拒绝；没有新规则进入 registry。
5. 因为 registry 没有变化，validation50 本次不应重跑；已有 50 例结果继续作为冻结基线保存。
6. 本次不能宣称 cycle 3 带来了 recall、precision、因果有效性或泛化提升；可以宣称的是：真实候选生成和严格拒绝流程已跑通。

---

## 10. 下一步（只在需要继续实验时执行）

1. 继续使用同一 tuning100/validation50 manifest，不重跑 baseline。
2. 针对本次三个拒绝原因改进 proposer 的 failure-family 到 polarity 映射；不要放宽 V2/V3/M 门禁。
3. 运行下一 cycle 时仍保持 `proposer-mode llm`、直连 transport、最多 1 个 admit，并保存新的 ledger 事件。
4. 只有当一个新 intervention 真正写入 registry 后，才在原 validation50 上执行一次 post-evolution validation。
5. 若新候选再次全部被拒绝，记录为新的有效负结果，不运行无必要的 validation50 重放。

