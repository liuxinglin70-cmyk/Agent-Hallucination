# 小规模验证实验（2026-09-23）

**证据等级：工程闭环通过；不构成检测能力或泛化结论。** 全程使用 mock backend 与离线
proposer，**未调用任何真实服务**（本轮的触网事故与修复见
[archive_wiring_20260923/README.md](../archive_wiring_20260923/README.md)）。mock oracle 正是
让目标可达的原因，因此下列读数只说明框架机制是否真的接通。

## 目的

本轮的三个新能力（P2.3 变形准入 gate、P2.2 Level B 沙箱、§28.6 存档接线）**都可以在单元
测试里通过而仍然到不了运行路径**——前三个工作包各修过一次这类缺陷。本实验逐个走一遍
真实运行路径并记录读数。

## 复现

```bash
python reports/validation_20260923/run_validation.py     # 约 3 秒，零 provider 调用
```

机器可读结果：[validation_report.json](validation_report.json)；运行日志：
[validation_output.txt](validation_output.txt)。重跑两次读数一致。

## 读数

### 1. 离线 cycle：四门通过并写入 registry

单纯跑 `cycle --backend mock` 找不到失败族（不种错），什么都证明不了。因此按
`evolution smoke` 的方式**种下**失败：两个 gold-positive patrol 案例在裸底座上判错，脚本候选
修好两个 gold-positive gain 案例。这展示的是门链与 registry 写入，不是检测质量。

| 项 | 读数 |
|---|---|
| 四门 | `structural=True`、`harmlessness=True`、`behavior=True`、`m=True` |
| 准入 | `IV-VALID-001` |
| cycle 后 registry | `['IV-VALID-001']`（真实写入） |
| gain 集配对效果 | `paired_n=8`，`base=6` → `active=8` correct，`marginal_cases=2` |
| 新增错误 | `new_fn=0`、`new_fp=0`、`new_errors=0`、`unresolved_added=0`、`health_regressed=0` |
| 缺陷注入审计 | `passed=True` |
| 账本哈希链 | `valid=True` |

### 2. 离线多轮 experiment：第 2 轮被**正确地**阻断

两轮都种下同样的失败（否则 CLI 默认路径找不到族，读数会像"通过"却什么都没测）。

| 轮 | state | 准入 | 轮后 registry | marginal | new_errors |
|---|---|---|---|---|---|
| 1 | `completed` | `IV-VALID-001` | `['IV-VALID-001']` | 2 | 0 |
| 2 | `blocked_evaluation_preflight` | — | `['IV-VALID-001']` | — | — |

第 2 轮的阻断理由是：

```json
{"gain_opportunity": {"recall": 0, "precision": 0},
 "reason": "no diagnosed failure direction has repair opportunities in gain",
 "blocked_families": [{"status": "not_evaluated",
                       "reason": "no repair opportunities in gain"}]}
```

**这是正确行为，不是缺陷**，且正是 §15.4 要求的那一条：正式增益集无机制机会时判
`not_evaluated`，**不能判 failure**。语义上也对——mock 的脚本修复在第 1 轮已经吃掉了增益集里
全部可修机会，第 2 轮确实没有可赚的收益。仪表报出了真相，而不是制造一次"通过"。

**这条读数同时说明本实验能测什么**：它证明了多轮流程、账本与状态机在"无机会"时的正确
行为；它**没有**证明跨代继承（§17 的 `G0 < G1 < G2`）。要测继承，需要的是**多轮各自都有
可修机会**的设置，那属于真实运行（见 §30），不是本 mock 实验。

### 3. 开发循环 + 存档接线（§28.6）

| 项 | 读数 |
|---|---|
| 机制 | `M-17aad6d46f7a`，修复操作 `route_recall` |
| 能力（组件登记键） | `control_flow` |
| 版本状态 | `ready_for_freeze` → `frozen`（`v001`） |
| 归档候选 | 1 条，`status='frozen'` |
| 归档组件 | 1 个，`evo_delivery_probe` |
| 报告权威性 | `metric_role='diagnostic-only'` |

即：开发循环 → L1 通过 → 冻结 → **归档写入运行路径**，端到端接通。

### 4. runner 准入（P2.3）

| runner | 准入 | 类数 | 失败 |
|---|---|---|---|
| `deterministic` | 是 | 5 | — |
| `typed_dsl` | 是 | 5 | — |
| `sandboxed_component` | 是 | 5 | — |
| `typed_semantic` | 是 | 6 | — |

**负对照（必须被拒，否则 gate 是空的）**：

| 对照 | 被拒 | 失败类 |
|---|---|---|
| 关键词匹配器 | 是 | `neighbor_surface_token` |
| 逐字相等的语义判定器 | 是 | `semantic_equivalence`（等 6 类） |
| 机制 key 读 case id | 是 | `case_id_rename`（等 3 类） |

### 5. 沙箱（P2.2）

| 项 | 读数 |
|---|---|
| `admission_blockers` | `[]`（平台可约束子进程） |
| 平台机制 | `windows_job_object` |
| 逃逸路径 | **27/27 被拒** |
| runner 终局权威 | `evidence_only` |
| 组件执行 | 从 registry 自动构建执行器 → 产出 `runner="sandboxed_component"` 的 evidence 行 |
| 篡改 digest | **被拒**（`refusing to attribute results to an artifact that did not produce them`） |
| 失控组件（死循环） | 返回 **`abstained`**，不是定罪 |

最后两条是本能力的要点：digest 校验在真实路径上生效；资源上限触发时组件**只能用词汇表里
"我说不准"这个词**，不能用它产生定罪。

### 6. capability-smoke：新 typed-DSL 断言的增益路径

`capability-smoke`：`exit_code=0`，`state=completed`，准入 `['IV-1-001','IV-1-002']`，
`audit_passed=True`。即：**新增观测词汇 → 命中只有它能抓的案例 → 通过门 → 被准入**这条
能力扩展路径是通的（同样以种错为代价）。

### 7. 能力面自述

`open_ended_self_evolution=False`（P3 未开工，前置未满足）；`runner_count=4`；
`runner_admission_all=True`；`sandboxed_code_generation=True` 并附带范围说明
（纯函数、固定 schema、导入白名单、无文件系统/网络、OS 级 CPU/时间/内存上限、
evidence-only）。

## 本实验**没有**测的东西

- **没有测检测质量**：所有"修好"都来自 mock oracle，不是模型能力。
- **没有测跨代继承**（§17 的 `G0 < G1 < G2`）：需要真实多轮运行。
- **没有测真实 holdout**：`datasets/protocols/final_holdout_v1.json` 不存在（按红线不由
  我创建）。
- **没有测真实服务**：本实验零 provider 调用；真实自进化实验见
  [real_evolution_20260923/](../real_evolution_20260923/README.md)。
- **没有测 POSIX 沙箱分支**：本机为 win32。
- **没有验证 `cycle_end` 归档策略**：该策略值已声明并通过校验，但 `_run_cycle` 未接线
  （见 §28.6 报告"未做"）。

## 结论

三个新能力的**运行路径可达性**成立：gate 在两条准入路径上真实拦截、沙箱组件在
evolution 后端与 Detector 两条路径上真实执行且 digest 校验生效、存档在开发循环结束时
真实写入并被下一轮检索到。**没有**任何读数支持"检测能力提升"或"自进化成功"的结论。
