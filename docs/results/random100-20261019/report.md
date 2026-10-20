# 随机 100 例真实运行（2026-10-19）

## 结论

预检发现并修复了 2 个会影响运行的因素；随后随机抽取 693 例中的 100 例完成真实检测，并对非权威案例做了两轮补测。最终 97/100 获得权威决策，3/100 因 defense 投票始终 `inconclusive` 保持 `degraded_judge_only`。在 97 例权威决策上准确率 75.3%（73/97），全 100 例口径 73.0%；正类召回 71.9%，正类精确率 88.5%；首错定位 33/67（49.3%）。无缺失案例、无致命供应商错误、运行期间源码与数据身份未变。

本批次与历史 100 例结果不可直接比较：样本不同（本次为新的随机抽样，含 BFCL 与 Octotools，历史机制验证样本排除了 BFCL），模型由 `GLM-5.3-flash` 切换为 `DeepSeek-V4.1-Flash-1017`，源码也不同。因此不能把差异归因于单模块或单次模型切换。

## 抽样

- 总体：`datasets/raw/agenthallu/AgentHallu`，693 例，`datasets/integrity.json` 校验 695/695 文件一致。
- 抽样：`random.Random(20261019).sample(..., 100)`，未按家族或标签分层；清单冻结于 `sample.json` / `cases.json`，样本摘要 `3b286f0e1c6607613fc0f43faab10d283db277c3c8f1b23b23386cb16b983d13`。
- 标签分布：正类 67、负类 33。
- 家族分布：Magentic_One 26、BFCL 19、OpenManus 16、Camel 13、SmolAgents 12、Octotools 7、OpenDeepSearch 7。

## 预检与修复

预检项：Python 3.13.7 与包导入、1325 项 detector 单测、配置解析、`.env` 凭据存在性、数据集逐文件 SHA256、校准谓词（3 项均 admitted）、Windows 路径长度、磁盘与内存、judge 路由真实连通性（1.4 秒返回合法 JSON）。

修复 1（配置）：部署配置的 `causal_audit.max_added_seconds_per_case=180` 低于上一轮验证实际采用的 900 秒。批次内排队会消耗该额度，180 秒会把排队延迟误记为 `budget_exhausted`。本次冻结配置改为 900 秒，并把单次预测输出上限从 4096 提到 8192；其余沿用 `detector/configs/autodl.json`。

修复 2（运行中发现的真实缺陷）：首个 100 例批次在第 10 例后停止，原因是 `action_comparison_repair` 的系统提示词为中文且未出现 “json”，而该路由继承 `judge_route.json_mode=true`，供应商对 `response_format=json_object` 强制要求提示词含 “json”，返回 HTTP 400 `invalid_request`，批处理按设计 fail-closed。修复：
- `detector/action_forecast.py`：提示词改为显式写明“只输出一个 JSON 对象”，并抽为 `COMPARISON_REPAIR_SYSTEM`。
- `detector/provider.py`：`provider_call` 在 `json_mode` 且提示词未提及 JSON 时追加 `Return a single JSON value.`，作为传输层兜底，防止其他提示词漂移再次中断长批次。
- 新增 2 项回归测试；detector 全套 1327 项通过。
- 因源码身份变化，作废首个 run 的缓存，以新 run-id 重启；首个 run 的 10 例与 400 停止原因保留在 `runs/random100_main/`，未计入最终结果。

冒烟验证：修复后先跑 3 例跨家族批次，3/3 有结果、2 例权威、1 例 defense `inconclusive` 的 degraded，无 400、无停止。

## 运行参数

| 项目 | 值 |
|---|---|
| 模型 | `DeepSeek-V4.1-Flash-1017`（供应商返回 `deepseek-v4-1-flash-261017`） |
| 端点 | `https://www.autodl.art/api/v1` |
| 并发 | `--workers 24 --provider-concurrency 24`（实测峰值 24） |
| 投票 | judge 3 + topup 2，defense 3 + topup 2 |
| 诊断 | `tracer_context` / `semantic_proxy` / `next_action_mode=judge_context` |
| 案例超时 | 10800 秒 |
| 主批次 | `random100_main2` |
| 补测批次 | `random100_recovery`（首轮非权威案例统一补测一次）与 `random100_recovery2`（用户授权的第二轮补测，4例中采用恢复为权威者） |
| 源码身份 | `bbadf4f1ae20d9633d04f62721c73e93a47084834d29dceb3a4e9f53001509c1` |
| 配置哈希 | `7d429240e7d919fd9cb5aaeda42e1a86aaa97bae61d10edb6f4870f50fa45f3e` |

## 结果

### 主批次（首轮）

100/100 返回结果；95 权威、5 `degraded_judge_only`；0 缺失、0 未启动、0 停止。主批次 5 例非权威全部是 defense 投票 `inconclusive`，不是 provider 或预算故障。

### 补测

按冻结规则对 5 例统一补测一次：1 例恢复权威（OpenManus/002），4 例仍为 `degraded_judge_only`（Magentic_One/002、Magentic_One/020、Magentic_One/052、SmolAgents/060）。这 4 例均在 `degraded` 状态下给出正类判断且方向正确，但按合同不计入权威指标。


第二轮补测（2026-10-19，用户授权）：对首轮与第一次补测后仍为 `degraded_judge_only` 的 4 例（Magentic_One/002、Magentic_One/020、Magentic_One/052、SmolAgents/060）再补测一次，1 例恢复权威（Magentic_One/020），3 例仍为 defense `inconclusive` 的 degraded。采用恢复权威者后更新最终指标；不采用的三例不计入权威口径。

### 最终指标（权威决策口径）

| 指标 | 值 |
|---|---:|
| 请求案例 | 100 |
| 权威覆盖 | 97/100（97.0%） |
| 未决（非权威 degraded） | 3 |
| 权威准确率 | 75.3%（73/97） |
| 全案例口径准确率 | 73.0%（73/100） |
| TP / FP / FN / TN | 46 / 6 / 18 / 27 |
| 正类精确率 / 召回率 / F1 | 88.5% / 71.9% / 79.3% |
| 宏平均精确率 / 召回率 / F1 | 74.2% / 76.8% / 74.3% |
| 首错定位 | 33/67（49.3%） |

若按终态口径（把 3 例 degraded 的正类判断计入），准确率为 76.0%（76/100），TP/FP/FN/TN 为 49/6/18/27；该口径只用于描述运行，不作为权威准确率。

95% Wilson 区间：权威准确率 75.3% [65.8%, 82.8%]；非 BFCL 82.1% [72.1%, 89.0%]；BFCL 47.4% [27.3%, 68.3%]；正类精确率 88.5% [77.0%, 94.6%]；特异度 81.8% [65.6%, 91.4%]；条件定位率 71.7% [57.5%, 82.7%]。单次 100 例的抽样误差约为 ±9 个百分点，分家族区间更宽。

### 分家族

| 家族 | 案例 | 权威 | 正确 | 准确率 | TP/FP/FN/TN |
|---|---:|---:|---:|---:|---|
| BFCL | 19 | 19 | 9 | 47.4% | 7/3/7/2 |
| Camel | 13 | 13 | 12 | 92.3% | 8/1/0/4 |
| Magentic_One | 26 | 24 | 20 | 83.3% | 11/1/3/9 |
| Octotools | 7 | 7 | 4 | 57.1% | 2/0/3/2 |
| OpenDeepSearch | 7 | 7 | 7 | 100.0% | 3/0/0/4 |
| OpenManus | 16 | 16 | 11 | 68.8% | 8/1/4/3 |
| SmolAgents | 12 | 11 | 10 | 90.9% | 7/0/1/3 |

剔除 BFCL 后：78 例中 64 例正确，准确率 82.1%；TP39/FP3/FN11/TN25，正类精确率 92.9%，召回率 78.0%，特异度 89.3%。BFCL 是唯一一个特异度低于 50%（2/5）且召回率只有 50%（7/14）的家族；7 例漏检占全部漏检的 39%，3 例误报占全部误报的 50%。家族准确率简单平均为 77.1%，与按案例加权的 75.3% 接近，说明总体读数不是由单一超大族拉出来的。

### 首错定位（按步骤类型）

| 类型 | 正类案例 | 命中 | 准确率 |
|---|---:|---:|---:|
| action_step | 24 | 15 | 62.5% |
| content_step | 25 | 18 | 72.0% |
| unclassified（漏检，无责任步） | 18 | 0 | — |

`unclassified` 的 18 例与 18 例漏检完全重合：检测器判为 clean，没有产生责任步，因此没有步型可归。它不是字段缺失，也不参与“判对但定位错”的比较；52 例被判阳性的案例（46 TP + 6 FP）全部带有 `responsible_step_kind`。为看清漏检画像，本次离线用同一 `step_kind` 规则对 18 例的**金标步骤**重算：action_step 12 例、content_step 6 例，说明漏检偏向动作型错误。该重算只用于描述漏检构成，不改变准确率，也不回填检测器结果。

定位误差方向：46 例权威阳性中 33 例命中（71.7%），13 例未命中里 11 例判得比金标**晚**、2 例判得比金标早；未命中按步型分为 action_step 9 例、content_step 4 例。也就是说，定位的主要偏差不是“找不到”，而是“找到了错误之后的步骤”。

### 机制运行与诊断

| 项目 | 数值 |
|---|---:|
| 有预测记录的案例 | 100 |
| Judge 实际使用预测的案例 | 68 |
| 至少产生一条模型假设 | 57（其中 55 例被采用） |
| 预测健康：ok / budget_exhausted / task_alignment_unknown / contract_error | 61 / 19 / 19 / 1 |
| 有 Recall 候选的案例 / 候选总数 | 7 / 14 |
| 因果诊断健康 | 100/100 ok |
| 发出预测请求的案例 / 队列中位 / 队列 p95 | 77 / 11.0s / 70.5s |
| 预测请求执行中位 / p95 / 最大 | 24.5s / 79.2s / 151.4s |

机制层有两个必须记录的发现：

1. **19 例 `budget_exhausted` 不是排队耗尽，而是输出额度记账使第二次预测永远无法执行。** 19 例全部为 `origin=diagnostic_reservation`，且全部先有一次因合同校验被拒的首个预测。冻结配置里 `max_added_output_tokens_per_case=8192` 与 `max_forecast_output_tokens_per_request=8192` 相等，第一次请求就预留了全部输出额度，第二个风险目标再申请时可用额度为 0，直接抛出 `AddedBudgetExceeded("output")`。代码本意是“最多两个风险目标各预测一次”，在这份配置下只能执行一次。结果这 19 例没有任何可用的模型预测。
2. **19 例 `task_alignment_unknown` 全部是 BFCL。** 该家族 `question` 是 3–7 轮的列表，而 `history` 只有 assistant 事件，既没有 `task_turn` 也没有 user 事件。现有 `action_context` 只接受非递减 `task_turn` 或与 question 原文匹配的 user 事件，因此无法把轨迹绑定到当前轮次，模型预测被跳过。BFCL 19 例准确率 47.4%，其余 81 例 82.1%，这是本批最大的单一差距来源。

其他诊断：

- 18 例漏检中，Judge 全部判 clean，16 例 confidence=high；16 例 Recall 没有产生任何候选。换句话说，漏检主要发生在“Judge 高置信度判 clean、Recall 也没有接住”的路径上，而不是基础设施故障。
- 6 例误报中，3 例来自 `recall_upgrade`（BFCL/100、Magentic_One/077、OpenManus/069），3 例来自 Judge；Defense 对 6 例全部给出 `substantiated`，没有推翻任何一例。
- 阶段合同错误很少：`step_tracer` 2 例（BFCL/110、BFCL/123，引文不在引用通道），`action_forecast` 1 例（Camel/031，引文不属于可见前缀）。
- 3 例未决的 Defense 票型分别是 1 substantiated + 2 inconclusive、0/3、0/3；这不是增加一次重试就能稳定解决的，属于模型对“是否存在可证明的推翻”本身没有形成多数意见。

### 错误案例

- 漏检 18 例：BFCL 7、OpenManus 4、Magentic_One 3、Octotools 3、SmolAgents 1。BFCL 占漏检的 39%，该家族总体准确率也最低（47.4%）。漏检的 Judge 判断 16/18 为 high confidence，且 16/18 没有 Recall 候选。
- 误报 6 例：BFCL 3、Camel 1、Magentic_One 1、OpenManus 1。其中 3 例由 `recall_upgrade` 定罪，Defense 均未推翻。
- 判对但定位错 13 例：11 例偏晚、2 例偏早；action_step 9 例、content_step 4 例。

### 成本与时长

| 项目 | 主批次 | 补测 | 合计 |
|---|---:|---:|---:|
| 批次墙钟（并发24） | 23.3 分钟 | 8.7 + 7.3 分钟 | 39.3 分钟 |
| 逐例耗时之和 | 5.84 小时 | 0.49 + 0.41 小时 | 6.74 小时 |
| 供应商请求 | 705 | 52 + 39 | 796 |
| 输入 tokens | 3,995,420 | 350,683 + 271,991 | 4,618,094 |
| 输出 tokens | 3,601,824 | 375,401 + 241,290 | 4,218,515 |
| 总 tokens | 7,597,244 | 726,084 + 513,281 | 8,836,609 |
| 缺失 usage 的请求 | 0 | 0 | 0 |

平均每例约 8.0 次请求、88,366 tokens（按 796 请求、100 例计；补测请求也计入分母）。输出 token 中 94.5% 是 reasoning tokens。另有首个中止批次的 10 例消耗 921,052 tokens，单独保留在 `runs/random100_main/`，不计入本结果。

## 文件

- `sample.json` / `cases.json`：抽样与冻结清单
- `config.json`：本次冻结配置
- `manifest.json`：源码身份、冻结哈希、修复与运行历史
- `main.log` / `recovery.log`：批次日志
- `main-evaluation.json`：主批次评测
- `recovery-evaluation.json`：第一次补测评测
- `recovery2-evaluation.json`：第二轮补测评测（用户授权）
- `final-evaluation.json`：采用补测后的最终逐例结果
- `runs/random100_main2/`：主批次运行树与 `summary.json`
- `runs/random100_recovery/`：第一次补测运行树与 `summary.json`
- `runs/random100_recovery2/`：第二轮补测运行树与 `summary.json`
- `runs/random100_main/`：修复前中止批次（保留，不作结果）
- `runs/smoke3/`、`runs/smoke3b/`：修复前后冒烟

## 可接受性判断

| 判断口径 | 结论 | 依据 |
|---|---|---|
| 作为一次测量 | 可接受 | 796 次请求全部 HTTP 200；0 缺失、0 传输错误、0 fatal stop；源码与数据身份前后一致；冻结清单可复现 |
| 作为预注册验收 | 不可接受 | 冻结标准为权威覆盖 100/100，实际 97/100；3 例 defense `inconclusive` 经两轮补测未恢复 |
| 作为效果证据 | 不足以支持部署或机制有效 | 权威准确率 75.3%（95% CI 65.8–82.8），全部阳性召回 46/67=68.7%，漏检 18 例；无同源码 off/on 配对 |

结论：运行本身可信，但读数不够好。不能据此声称检测器达到可用水平，也不能声称下一步预测机制带来净收益。

## 优化建议（按优先级）

### P0：低成本、确定能修

1. **修复可选请求的输出额度记账（19 例机制缺口）**
   - 现象：19 例 `budget_exhausted`、`origin=diagnostic_reservation`，全部先有一次预测因合同校验被拒；第二次风险目标再申请时输出额度为 0。
   - 原因：`max_added_output_tokens_per_case=8192` 与 `max_forecast_output_tokens_per_request=8192` 相等，第一次请求预留全部额度。
   - 可选方案：把总输出额度提高到 2×单次上限；或让单次预留取 `min(per_request, remaining)`；或按实际 usage 结算并释放未用额度。需保留“可选工作不得占用诊断预留”的合同。
   - 验收：新增测试证明在输出额度 8192、请求上限 2 时第二个风险目标能被受理；同时证明超额时仍被明确拒绝并记录 `origin`。
2. **预检增加两条断言**
   - 当 `max_added_provider_requests_per_case ≥ 2` 时，`max_added_output_tokens_per_case` 必须至少为 2×`max_forecast_output_tokens_per_request`，否则 BLOCKER 或 WARN。
   - 所有 `json_mode=true` 的生产提示词必须包含 "json"；可在测试中静态扫描提示词常量，传输层兜底继续保留。
   - 增加数据集—机制兼容性检查：若 `question` 为多轮列表而 `history` 无 `task_turn`/user 事件，提前报告预测机制将跳过（本批 BFCL 19 例）。
3. **`summary.json` 增加显式 `wall_clock_seconds`**
   - 当前 `measurement.elapsed_seconds` 是逐例耗时之和，不是批次墙钟，已造成一次误读；新增字段并保留旧字段兼容，更新技术合同与测试。
4. **修复 `action_comparison_repair` 的 JSON 模式提示词（本轮已修）**
   - 已改为显式 JSON 提示词并在 `provider_call` 增加兜底；需随本轮修复一并提交并保留回归测试。

### P1：准确率相关，需要实验设计

5. **BFCL 任务轮次绑定（19 例，潜在影响最大）**
   - 不建议在检测器内猜轮次。优先做数据适配：从 BFCL 原始结构注入 `task_turn`，或在加载时显式标注；拿不到可靠边界时保持跳过并记录。
   - 验证：同源码对 BFCL 子集做 off/on 配对，不能只看机制覆盖率。
6. **漏检治理（18 例，最大误差来源）**
   - 16/18 为 Judge high-confidence clean 且 Recall 无候选。先离线做错误分类（动作遗漏、结果捏造、观察误读、多轮上下文丢失等），再决定改 Judge 合同还是扩 Recall 扫描模式。
   - 不要在未分类前直接调阈值，避免把 FP 从 6 例推高。
7. **Recall Upgrade 精度审查（4 个阳性中 3 个 FP）**
   - 样本太小，不能据此定罪；检查 Upgrade 的因果证据门槛是否过松，可要求“事实错误 + 因果连接”双条件或 Defense 证成后再赋权。
8. **定位偏晚（13 例定位错误中 11 例偏晚）**
   - 检查 tracer 的“最早步骤”约束是否在投票合并时被后续步骤覆盖；这是可定位、可单测的契约问题。

### P2：成本与吞吐

9. **reasoning tokens 占输出 94.5%**
   - AutoDL 配置未关闭 thinking，`deepseek.json` 有 `extra_body.thinking.disabled`。先在小样本做配对 A/B，准确率保持再考虑降本，不要直接全量切换。
10. **并发渐进**
    - 本次 24 并发 23.3 分钟跑完 100 例，无资源异常；容量测试建议 64，但每请求一个隔离子进程，本机可用内存约 2.5GB。建议先 32 一轮观察峰值内存与错误率。
11. **3 例未决的合同政策**
    - 票型为 1 substantiated + 2 inconclusive、0/3、0/3。是否引入“证成票优先”是研究政策问题，不是 bug；先分析 Defense 理由再决定，不要为提高覆盖率放宽权限。

## 下一轮全量测试的前置条件

1. P0 修复完成，detector 全套离线测试通过，新增回归覆盖输出额度与 JSON 模式断言。
2. `scripts/preflight_real_run.py`（或等价检测器预检）无 BLOCKER；新增检查项生效。
3. 冻结配置、样本清单、源码身份与配置哈希写入 manifest。
4. 用冻结配置跑通 3 例跨家族真实冒烟，全部有结果且无 fatal stop。
5. 明确全量分母、并发、预算、补测规则与验收口径；不读取金标选择补测。
6. 全量启动命令与回滚方式记录在案；凭据只从 `.env`/进程环境读取。

## 限制

- 单次随机样本，未分层，家族占比与总体不同；73% 是本批次读数，不是总体准确率的无偏估计。95% 区间约为 ±9 个百分点。
- 3 例非权威来自 defense `inconclusive`，两轮补测后仍未恢复；不能当作负例，也不能当作正确正例计入权威口径。
- 运行使用新模型与新样本，不能与历史 GLM-5.3 的 82% 或本轮之前的读数直接相减。
- 没有同源码 off/on 配对消融，本轮只描述运行读数，不归因于单个机制。
- BFCL 的 19 例预测机制因任务轮次无法绑定而未运行；BFCL 的低准确率不能全部归因于检测模型本身，也不能用其余家族的表现替代。
- 19 例预测因输出额度记账未能执行第二个风险目标；这是机制覆盖缺口，不是检测精度缺陷。
- `summary.json` 的 `measurement.elapsed_seconds` 是逐例耗时之和，不是批次墙钟；本报告已另算墙钟（主批次 23.3 分钟）。该字段命名容易误读，建议后续增加显式 `wall_clock_seconds`。
- 成本只统计供应商返回的 usage，不含本地 CPU；未使用账单金额。输出 token 中 94.5% 为 reasoning tokens，是成本的主要来源。
