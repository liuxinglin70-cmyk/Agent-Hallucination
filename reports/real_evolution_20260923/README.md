# 真实自进化实验：首次真实数据上的一轮 cycle（2026-09-23）

**结论：框架在真实数据上跑通了完整的一轮真实闭环，终态是"0 准入"，且拒绝是
机器可读的、理由具体的、由门自行给出的。** 这不是失败报告，也不是成功报告——它是一个
**有内容的负结果**，而且正是 §23.4 要求的"终态机器可读地回答五问之一"。

## 运行身份（先冻结，后运行）

| 项 | 值 |
|---|---|
| 运行目录 | `artifacts/selfevo-real-20260923/` |
| 工作包提交 | `653c0fa67d58`（"能力面清单改为带状态条目…"） |
| 源码摘要 | `97d22ef06c777ba38c0b05ef588434067e697c91adb39b8c5a9ae0ba3e14c11d`（137 文件） |
| 配置 | `evolution/configs/selfevo_real_20260923.json`，SHA `…`（见 [prerun_state.json](prerun_state.json)） |
| 检测配置 | `detector/configs/autodl.json` |
| 模型 / 端点 | `GLM-5.3-flash` / `https://www.autodl.art/api/v1` |
| 并发 | 1（见下"运行事故"） |
| 时间 | 04:50:53 启动 → 约 07:15 完成 |
| 账本 | 11 条记录，哈希链 `valid=true`（`python -m evolution verify-ledger` 独立复核） |
| 划分 | mini，种子 20260923：sentinel 6（3/3）、gain 6（4 error / 2 clean）、patrol_pool 18 |
| 冻结契约 | `7d9ff8b47ed2`(config) / `6b382bd0ac13c675`(source) / `da3131e671c5d981`(detector) |

`final_size=0`、`final_reads=0`：**本轮未创建也未读取任何 holdout**（红线：该决定属于用户）。

## 读数

机器可读结果：[readings.json](readings.json)（由 [extract_readings.py](extract_readings.py)
只读提取，不重跑、不调用服务）。

### 1. patrol（裸底座，8 例）

| tp | tn | fn | fp | unresolved |
|---|---|---|---|---|
| 3 | 1 | 1 | 3 | 0 |

### 2. 机制与能力规划（**本实验最重要的读数**）

真实数据上发现 **2 个机制子组**，两者都被规划为 `actionable`：

| 机制 | 修复操作 | 所需能力 | 目标类型 | 能力路由 |
|---|---|---|---|---|
| `M-5a89ab46e81f` | `withhold_conviction` | `guard` | detection | `withhold_guard`（`adjudicate.guard`） |
| `M-c393f57ff12f` | `constrain_localization` | `prompt` | localization | `tracer_directive`（`tracer.system`） |

两个机制的攻击面、修复操作、目标类型都不同，说明**"机制识别先于候选搜索"这条设计在真实
数据上确实区分出了东西**，而不是把一次失败换了个名字。`M-c393f57ff12f` 是
`FAM-loc-*` 定位族，走的是本地化目标路径。

### 3. 收益机会与预检

```json
{"gain_opportunity": {"recall": 1, "precision": 0},
 "blocked_families": [{"failure_family": "FAM-fp-judge_conviction-117fce1c070d",
                       "polarity": "precision", "status": "not_evaluated",
                       "reason": "no repair opportunities in gain"}],
 "sentinel": {"protected_tp": 2, "protected_tn": 2, "evaluable": true}}
```

precision 方向的族**被正确地判为 `not_evaluated`**（增益集内无可修机会），
而不是被判失败——这正是 §15.4 第 5 条要求的语义，且这次是**真实数据**上的第一次。

### 4. 提案（真实 LLM）

LLM proposer 产出 **1 个候选**：

| 项 | 值 |
|---|---|
| `intervention_id` | `IV-1-001` |
| 类型 / hook | `text_boundary` / `tracer.system` |
| 目标族 | `FAM-loc-mislocated-117fce1c070d` |
| 极性 / 目标 | `boundary` / localization |

候选内容是一段**定位指令**：要求 tracer 扫描 event 文本、工具参数、工具观测与 prose，
找到"自身内容首次呈现该缺陷"的最早交互，并据此命名 responsible step；在命名前必须验证该步
自身确实包含该缺陷内容。这是针对 `constrain_localization` 机制的合理措辞，**不是**关键词
堆砌，也没有泄漏 case id（STRUCTURAL 的 `jaccard_base=0.1098` 很低）。

### 5. 四门：STRUCTURAL 通过，HARMLESSNESS 拒绝

| 门 | 结果 | 理由 |
|---|---|---|
| STRUCTURAL | **通过** | `jaccard_base=0.1098` |
| HARMLESSNESS | **拒绝** | `sentinel harmlessness violation: new_fn=2, new_unresolved=4, health_regressed=4` |
| BEHAVIOR | 未运行 | 前门已拒（报为 `null`） |
| M | 未运行 | 前门已拒（报为 `null`） |

（下表是**这个候选**的四门；第 6 节的注入缺陷审计是另一组条目，两者的 BEHAVIOR 结果不同
是正常的——不同的候选由不同的门拦下。）

保护集配对读数（4 例）：

| 指标 | 值 |
|---|---|
| `base_all_case_correct` → `active_all_case_correct` | 4 → **0** |
| `marginal_cases` | **−4** |
| `tp_lost` / `fp_added` | 2 / 0 |
| 翻转 | `T->U` ×2（error 类）、`F->U` ×2（clean 类） |
| `unresolved_added` | 4 |
| `health_regressed` | 4 |

**读法**：这个候选把 sentinel 上 4 例全部打成 `unresolved`（2 个原本判对的 error 和 2 个
原本判对的 clean），即它**同时破坏了召回与精确两个方向**。HARMLESSNESS 以"新漏报 2、
新增未决 4、运行健康退化 4"拒绝，并且**没有**与任何收益相加抵消（该门设计如此）。
`BEHAVIOR` 与 `M` 未运行并被如实报为 `null`——**未运行不等于失败**，提取器
（`_gate_verdict`）把"没跑"与"跑了但没过"分成两种读数，因为前者没有对该候选给出任何判断，
把它记成失败就是把一个没有做出的判断算在门头上。

### 6. 缺陷注入审计（真实数据上的首次）

审计 `passed=true`，两个注入缺陷都被正确拒绝：

| 注入缺陷 | 拒绝门 | 理由 |
|---|---|---|
| `IV-DEFECT-001` | **STRUCTURAL** | `case id leak in directive/body/claim: BFCL/001.json` |
| `IV-DEFECT-002` | **BEHAVIOR** | `target polarity mismatch: boundary claim corrected no decisive decision (decisive_fixed=0, decisive_broken=0)` |

两个注入缺陷**由不同的门**拦下，这一点比"都被拦下"更有信息量：

- `IV-DEFECT-001` 在**指令文本里写了 case id** → 结构门拦截，证明标签泄漏防线在真实运行中
  生效（`IV-DEFECT-002` 的 `jaccard_base=0.0092` 说明它没有泄漏，所以走到了下一门）；
- `IV-DEFECT-002` 结构门与 HARMLESSNESS **都通过**（保护集 4/4 不变，无退化），但**声明
  `boundary` 极性却一个决定性决策都没纠正** → BEHAVIOR 门以极性不匹配拒绝。

这正是四门分层的意义：一个"无害但无效"的候选不会被前两门拦下，必须由行为门按声明方向
考核。若只有结构门与无害门，`IV-DEFECT-002` 会被误当作可准入。

### 7. 终态与存储

| 项 | 值 |
|---|---|
| `state` | `completed` |
| `active_after_cycle` | `[]`（**0 准入**） |
| `marginal_selection.selected` | `[]` |
| 账本 | 11 条记录，链 `valid=true` |
| `route_failures.jsonl` | 0 条（本轮无"触发但无收益"的路由） |
| `gaps.jsonl` | 0 条（两个机制都 actionable，没有能力缺口） |
| `candidates/`（存档） | 0 条 |
| `components/` | 0 条 |
| memory episodes | 0 条 |

**`candidates/` 为 0 是预期行为**：本轮的 `archive.on` 设为 `cycle_end`，而 §28.6 报告的
"未做"一节已经说明 `cycle_end` **已声明但未接线**——没有开发 run 目录可归档，因此没有写入。
这条读数是那个缺口的直接后果，不是新缺陷。

## 这一轮证明了什么，没证明什么

**证明了（真实数据）**：
1. 完整闭环跑通：patrol → 机制推断 → 能力规划 → 路由选择 → 真实 LLM 提案 → 四门 → 机器可读终态；
2. 机制层区分出了两个不同机制，并各自映射到不同能力路由与目标类型；
3. `not_evaluated` / 未运行门 / 拒绝理由三者的语义都正确落盘，未虚报；
4. 缺陷注入审计在真实运行中拦截标签泄漏；
5. 保护门以"永不相加抵消"的方式拒绝了一个双向破坏的候选。

**没有证明**：
1. **没有证明自进化成功**：0 准入，`G0 < G1 < G2` 无从谈起（§17）；
2. **没有证明检测/定位能力提升**：唯一候选被拒，没有任何收益读数；
3. **没有泛化结论**：693 例全部处于开发暴露范围，本轮未创建 holdout，也未读 `final`；
4. **没有跨代继承证据**：只跑了一轮，且无准入，registry 仍为空；
5. **没有验证 `route_failures` / `gaps` / 存档的运行期写入**：本轮三条路径都因
   "机制全部 actionable + 无路由反证 + `cycle_end` 未接线"而没有产生记录。**这三处的
   运行期写入在真实数据上仍未验证**（与 §26.5 记录的同一类风险）。

## 运行事故：两个并发真实运行共用同一目录

**如实记录**：本轮第一次运行（02:11 启动）在 patrol 阶段被我用**错误的存活判断**
（`tasklist | grep -c python.exe` 在本机中文输出下静默返回空）误判为"已死"，随后启动了
第二个 run，两者并发写同一目录约 90 分钟（至 04:47 手动终止）。

- **影响**：两次运行的**费用与时间被浪费**；被污染的读数**不进入任何结论**（证据保留在
  [contaminated_attempt/](contaminated_attempt/)，仅作事故证据）。
- **未影响**：原始数据集（只读）、其他运行目录、真实 registry（本轮为新建空目录）。
- **修复**：终止全部相关进程并确认进程表为空；改用
  `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 按命令行过滤；**启动后立刻确认
  "只有一个 cycle 进程"**，而不是只确认"启动了"。第三次（clean run）从头到尾只有 1 个
  cycle 进程（有多轮读数佐证：`cycle_procs='1'`）。
- 详情见 [INCIDENT_two_concurrent_runs.md](INCIDENT_two_concurrent_runs.md)。

## 复现

```bash
# 冻结：见 prerun_state.json（源码/配置/检测配置摘要 + git head）
python reports/real_evolution_20260923/extract_readings.py        # 只读提取读数
python -m evolution verify-ledger --config evolution/configs/selfevo_real_20260923.json
```
