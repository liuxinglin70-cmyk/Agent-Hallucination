# 判错对象口径决定与固定预检复跑

本轮把 2026-09-18 预检阻断的根因处理到底：先做「判错对象」的口径决定，再修一个底座真实缺陷，然后以新冻结协议复跑同一预检。**门限、划分、种子、标签一律未动**；每次源码或配置变动都单独冻结协议、单独复跑，因此每个结论都能追溯到一次明确改动的运行。

## 结论摘要

口径决定见 [docs/error-scope.md](../../docs/error-scope.md)。它把「检测器被要求定罪什么」写成版本化决定（ES-1…ES-6），并附与数据集标签的已知分歧目录。定罪的唯一改动是其中的 ES-2：观测互相冲突时不得用世界知识挑边定罪。

真实复跑暴露了两个与口径无关的运行缺陷，各自在被修复前阻断流程。四次尝试的终态如下（每次都以同一 S=6/G=4、同一种子、同一门限运行）：

| 尝试 | 相对上次的改动 | 预检 | 终态 |
|---|---|---|---|
| attempt1 | ES-2 规则（`semantics.py`） | **2 TP + 1 TN（仍不足）** | `blocked_evaluation_preflight` |
| attempt2 | + 召回扫描器预算 8192→32768 | **2 TP + 2 TN 通过** | `frozen_after_defect_injection_audit`（缓存长路径事故） |
| attempt3 | + 缓存文件名越界修复 | **2 TP + 2 TN 通过** | `frozen_after_defect_injection_audit`（案例时限不足） |
| attempt4 | + 运行时包络按实测上调 | **2 TP + 2 TN 通过** | `blocked_evaluation_preflight`（增益集无修复机会） |

三次终止都是**设计要求的 fail-closed 或协议停止**：缺陷注入审计不能把一个基础设施失败认证成「门正确拒绝了缺陷」，而预检的第二道检查在没有任何方向可计分时不允许生成候选。它们都不是判定阈值问题——修的都是运行包络与接线，停止的是「没有可用仪器」。

attempt4 是本轮唯一一次走完全部前置检查的运行：哨兵预检通过、缺陷注入审计通过（两个注入缺陷均被实质拒绝，且不再有「无结论」），并在巡检阶段诊断出一个稳定失败族。随后它在预检的第二道检查上按协议停止：**增益集中没有任何可修复案例**（`gain_opportunity = {recall: 0, precision: 0}`），因此该失败族无法被计分，生成候选没有意义。

## attempt1：ES-2 生效，但预检仍被阻断

`OpenDeepSearch/077` 的判官输出直接引用了新规则并转为阴性：

> `"correct_result": "Undecided on this record: the trajectory's own tool observations conflict on the same point (e…"`

即：**ES-2 消除了一个此前由错误世界知识造成的定罪**。但该案例随后因召回扫描器输出被截断而失败：

- 扫描器在 8192 token 上限处被切断（`finish_reason=length`，completion 恰好用满上限）；
- 截断在 provider 层是致命错误，不走契约修复路径，因此整个召回网读作不可用；
- 判官阴性的 clean 判定因此降级为 `degraded_judge_only`，不计入保护 TN。

预检仍为 2 TP + 1 TN，达不到 2 TP + 2 TN，按协议停止。

## attempt2：预检通过（2 TP + 2 TN），审计因缓存事故冻结

把 `recall_route.max_tokens` 提到 32768（与 proposer 因同类截断得到的处置一致）后：

- `OpenDeepSearch/077` 成为 authoritative clean，**保护 TN 从 1 升到 2，预检首次通过**；
- 运行继续进入缺陷注入审计，然后冻结：`OpenDeepSearch/080` 在增益集中的一次缓存写入抛 `WinError 3`，案例 fail-closed 成 `unresolved`。

根因是**证据标签把缓存文件名推过 Windows 路径上限**：该缓存名由案例 id、记录哈希与证据标签组成，带快照标签的案例路径达 268 字符；同目录其余 9 个案例使用 `noevidence` 标签、恰好 259 字符而侥幸通过。已在本地复现（`os.replace` 在 268 字符目标上失败、在 259 上成功）。

## attempt3：缓存修复生效，审计因案例时限冻结

缓存文件名超过预算时退化为输入的摘要后，`OpenDeepSearch/080` 恢复为 authoritative TP（`080` 在 gain 集中从未决变为判对）。预检继续通过（2 TP + 2 TN），审计再次冻结，这次是 `SmolAgents/019`：

- 该案例的判官投票撞上 480 秒单次调用上限两次（`total timeout exceeded`），累计超过 900 秒案例时限；
- 之后 tracer 与 defense 直接放弃（`case_deadline_exceeded`），该行成为非 authoritative；
- 审计不能把无法计分的臂认证为「门正确拒绝」，因此冻结。

这是**包络问题，不是判定问题**：[diagnostics/019_timing.json](diagnostics/019_timing.json) 用充裕预算实测该案例端到端需要 **1480 秒**，期间判官 3 票、tracer 1 次、defense 3 次全部正常完成，终态 authoritative。900 秒的案例时限在这条轨迹上必然把它压成未决。

## attempt4：运行时包络按实测上调

只改运行包络，不改任何进入准入的数值：

| 参数 | 原值 | 新值 | 依据 |
|---|---:|---:|---|
| `case_timeout_seconds` | 900 | 3600 | 实测单案例需 1480 s |
| `provider.timeout_seconds` | 240 | 300 | 单票均值约 210 s，并发的降速需余量 |
| `provider.total_timeout_seconds` | 480 | 900 | 同上，避免重试把自己耗进截止 |
| `budget.cycle_timeout_seconds`（仅本轮配置） | 7200 | 14400 | 该案例每轮要顺序评估三次臂 |

这些是 fail-closed 的界限，只能把一个无法测量的行变成可测量的行，不能改变任何门限、划分、种子或标签。`cycle_timeout_seconds` 的偏离写在本轮协议里、由验收脚本显式记录，不是静默放宽。

## attempt4：走到运行时与统计功效的边界

| 检查 | attempt4 结果 |
|---|---|
| 哨兵预检 | 通过（2 TP + 2 TN + 1 FN（`BFCL/041`，已知 ES-3）+ 1 例判阳） |
| 缺陷注入审计 | 通过：`IV-DEFECT-001` 结构门拒绝、`IV-DEFECT-002` 走完无害/行为/边际被拒绝，两者都不再是「无结论」 |
| 巡检（20 例） | 11 TP、4 TN、1 FP、3 FN、1 未决（`Camel/048`，防御未决，属判定语义而非基建） |
| 诊断失败族 | 1 个：`FAM-fn-judge_false_no_recall_candidates`（召回方向，3 例：`BFCL/017`、`BFCL/062`、`OpenDeepSearch/035`） |
| 增益集 | 4 例全部判对：2 TP（080、005）+ 2 TN（050、002）→ 两个方向的机会都是 0 |
| 终态 | `blocked_evaluation_preflight`：「no diagnosed failure direction has repair opportunities in gain」 |

这次停止是**已记录的结构性限制**，不是新缺陷：增益集只有 4 例且当前全部判对，因此它既不能测召回修复（没有 FN）也不能测精确率修复（没有 FP）。这对应 9-17 诊断的 R3（收益集与失败族脱节）与 [进展文档](../../docs/progress.md) 第 7/8 项的统计功效问题。本轮的两处修复把它从「被接线缺陷掩盖」变成「作为唯一约束显式出现」。

值得记录的一点：9-17 时该增益集还含一个精确率机会（`Magentic_One/050` 的 FP）。现在该案例的判官 `basis` 是 `observation`（依轨迹自身观测接受答案 90），属于更早一轮的观测优先级改动，**不是**本轮 ES-2 的冲突规则所致；本轮不改变它。

## 验证

每次尝试都冻结 90–91 个源文件与 8 个语料文件的 SHA-256；`reports/error_scope_20260918/freeze_protocol.py` 逐次生成协议，并把每次尝试的检测器配置另存为字节精确副本（`attempts/<name>/frozen/detector-config-autodl.json`），因此后续改动不会悄悄改写早先尝试实际运行的配置。attempt1 的冻结副本与优化轮基线协议的哈希逐字节一致，可反查。

验收脚本 `audit_run.py` 只读归档、不调用模型：它独立重算保护集覆盖、核对账本哈希链、确认门限/划分未变、并检查每个偏离都在协议中声明过。

| 尝试 | 验收 |
|---|---|
| attempt1 | 通过（重算 2 TP + 1 TN，与记录一致） |
| attempt2 | 通过（重算 2 TP + 2 TN，与「已通过预检」一致） |
| attempt3 | 通过（同上） |
| attempt4 | 通过（重算 2 TP + 2 TN；唯一偏离 `cycle_timeout_seconds` 已在协议声明） |

离线侧：`python -m pytest -q` 392 passed（新增 7 项：观测冲突规则 4 项、召回预算 1 项、缓存长路径 1 项、运行时包络 1 项）；`scripts/verify.py` 全部检查通过；原始 695 个文件哈希一致。新增的长路径与预算测试都在修复前的实现上失败，因此不是平凡通过。

## 边界

- 覆盖只有 1/10 的语料证据现状没有改变；本轮的两次预检通过不是准确率结论。
- `SmolAgents/019` 即使跑完也是**判阳**，而数据集标签是 clean——这是 [口径决定](../../docs/error-scope.md) 里记录的 ES-4 已知分歧，本轮不改判、不改标签。因此它在保护 TN 上不计入，预检的两例 TN 是 `Magentic_One/036` 与 `OpenDeepSearch/077`。
- `BFCL/041` 按 ES-3 记为已知 FN，未改判。
- 本报告不宣称自进化收益。attempt4 走完哨兵预检与缺陷审计后，因增益集无修复机会而停止，因此**本轮仍未产生候选，也没有任何准入结果**。
- 要让诊断出的召回族可被计分，需要一份预先冻结、按机制组织（含已知 FN/FP）的开发评估协议，或持久化的替代评估集。两者都改变测量仪器，按项目既有纪律属于单独决定，必须满足与主增益集同等的 gold、协议与暴露保证；在看过本轮失败族之后再挑案例，等同于事后选择，不在此列。
