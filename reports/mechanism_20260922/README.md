# E0：失败机制重建干跑（2026-09-22）

对应[优化方案](../../agent_hallucination_self_evolution_optimization_plan.md) §16 E0 与 §23.3 P0.1 判据。**不改检测器行为**，只用归档的已评估行重建机制，检查"同路径不同机制能否分开、同机制跨来源能否合并"。

复现：`python reports/mechanism_20260922/rebuild_mechanisms.py`

## 1. 数据与口径

| 项 | 值 |
|---|---|
| 行来源 | `reports/error_scope_20260918/attempts/*/run/checkpoints/cycle*/arms/*`（归档，只读） |
| 去重后案例 | 30（按 `case` 取首次出现） |
| 形成粗家族的失败案例 | 6 |
| 依赖外部服务 | 无（纯离线，无模型调用） |
| 证据等级 | **diagnostic-only**：验证机制分组是否可解释，不构成检测能力或定位能力结论 |

## 2. 读数

案例账目闭合（`accounting_closed: true`）：6 = 4 可提案 + 0 未覆盖 + 2 显式排除。

### 2.1 形成的机制

| mechanism_id | 状态 | 任务义务 | 观测偏差 | 检测器失败 | 修复操作 | 成员 |
|---|---|---|---|---|---|---|
| `M-5a89ab46e81f` | confirmed | `exact_value` | `none` | `adjudication/false_conviction` | `withhold_conviction` | `SmolAgents/019`、`BFCL/107` |
| `M-edb55577e87b` | confirmed | `required_action` | `omission` | `recall/not_reached` | `route_recall` | `BFCL/041`、`BFCL/062` |

### 2.2 判据对照

| 判据 | 结果 |
|---|---|
| 同路径不同机制能否分开 | **能**。两个机制同属 `FAM-fn-judge_false`/`FAM-fp-judge` 的相近路径，但义务（`required_action` vs `exact_value`）、偏差与修复操作不同，未合并 |
| 同机制跨来源能否合并 | **能**。`M-5a89ab46e81f` 的成员来自 `SmolAgents` 与 `BFCL`，`M-edb55577e87b` 来自同一来源；前者证明跨来源合并成立 |
| 是否依赖 case id / gold | **否**。机制 key 只由 objective、义务、偏差、失败阶段与修复操作构成；`evolution/tests/test_mechanisms.py` 有改名不变性与"gold 字段加入不改变 key"的测试 |
| 分组是否可解释 | 两个机制逐例可复核（§2.1 与 `mechanism_dryrun.json::mechanism_cases`），每组一个修复操作、两个同源案例 |
| 单例是否被当作机制 | **否**。少于 2 例的读数进 `unrepresented` 并标 `single_case_research_only`，不进入可提案集 |

## 3. 本轮修掉的三个真实缺陷

### 3.1 上游静默丢弃案例（已修）

粗家族 `FAM-fn-judge_false_no_recall_candidates-a4f141369d93`（n=4）经 `refine_failure_families` 按机械画像拆分后只剩 2 例，`BFCL/017` 与 `OpenDeepSearch/035` **静默消失**——报告看不出巡逻曾发现 4 个失败，机制分组因此被在一个人为缩小的集合上评估。

修法与仓库既有约定一致（同 `localization_annotation_choices`）：新增 `worksheet.excluded_failure_cases()`，把未存活分组逐例报出，进入 `worksheet.mechanisms.excluded`。排除规则本身不改——单例分组确实无法承载 STRUCTURAL 要求的两个 provenance 案例。

### 3.2 误报机制被按合同类型拆碎（已修）

首轮干跑中两个误报（`SmolAgents/019`、`BFCL/107`）被判为 `ambiguous` 而非同一机制：成员的 `contracts` 一个是 `exact_value`、一个是 `required_action`。

根因是签名设计错误：**误报没有任务级偏差**（交付物本身没错，错在检测器），其修复完全在检测器一侧，与任务要求什么无关。原实现把义务无条件放进 key，等于按合同类型把同一机制拆成多份。

修法：义务只在偏差非 `none` 时参与修复签名（`_repair_obligation`），分组签名与一致性判定共用同一条规则，避免两者再次分叉。

### 3.3 子组共用一个家族 id（已修）

`mechanism_subgroups` 最初让子组沿用粗家族 id。但 gate 与提案校验器都以 `failure_family` 为字典键建 `family_cases` 映射（`gates.py:271-276`、`engine.py:2681-2687`），两个子组共用 id 会互相覆盖，其中一个看起来"本轮未诊断"。

修法：子组 id 为 `<粗家族>-M<hash>`，另存 `coarse_failure_family` 保留原 id；`FAM-fn-`/`FAM-fp-`/`FAM-loc-` 前缀逐字保留，因此极性、目标类型与家族成员校验的行为不变（有测试）。

另有第四项：preview 报告用白名单构造 family 视图，机制对象一度没有进去——而 `python -m evolution preview` 正是方案指定用来核对机制的入口。已改为把 `mechanism_report()` 与 `mechanism_prompt_view()` 接入 preview，并补回归测试。

## 4. 边界（不得越读）

- 6 个失败案例来自 `error_scope_20260918` 的预检档案，**不是**独立泛化样本；本报告不提供准确率、召回率或定位能力数字。
- "两个机制 confirmed"只说明机制推断在历史失败上自洽，**不说明**任何候选可修复它们；可修复性属 P0.2 能力规划与 P0.3 开发循环的判据。
- `ambiguous` 状态在首轮出现、修复后消失，属实现缺陷而非数据结论；若后续真实运行再次出现 `ambiguous`，那是成员读数确实不一致的信号，应保留并逐例审查，不得调参掩盖。

## 5. 与下一步的关系

P0.1 的代码与判据已完成（`evolution/mechanisms.py`、`worksheet.mechanism_subgroups`、engine 接线、proposer 消费端）。下一步 P0.2 消费本文件的两个机制：`M-edb55577e87b` 的 `route_recall` 与 `M-5a89ab46e81f` 的 `withhold_conviction` 是能力规划要判定的两个候选修复路线。
