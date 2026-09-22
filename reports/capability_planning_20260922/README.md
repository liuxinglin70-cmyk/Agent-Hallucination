# P0.2：能力规划与 route 反证记忆（2026-09-22）

对应[优化方案](../../agent_hallucination_self_evolution_optimization_plan.md) §23.3 P0.2 与 §5。**不改检测器行为**，回答两个问题：当前系统是否真的具备修复某机制的能力；某个 route 触发过但没修好时，是否被记住。

复现：`python reports/capability_planning_20260922/plan_capabilities.py`

## 1. 数据与口径

| 项 | 值 |
|---|---|
| 行来源 | `reports/error_scope_20260918/attempts/*`（归档，只读） |
| 机制来源 | P0.1 重建的 2 个 confirmed 机制 |
| 依赖外部服务 | 无（纯离线） |
| 证据等级 | **diagnostic-only**：验证能力判定与反证记忆的逻辑与接线，不构成检测能力结论 |

## 2. 核心改动：三个问题分开

`worksheet.classify_family_actionability` 只回答"有哪些 route"（Generatable）。P0.2 新增 `evolution/capability_planning.py` 回答第三个问题（Repair-capable），判定分三层，三层都过才算可修：

| 层 | 含义 | 否证时的读数 |
|---|---|---|
| **fit** | route 提供的能力类型是修复实际需要的 | 观察救不回"召回从未运行" |
| **reach** | 观察类路线能读到它要比较的字段（含 hook 上下文可见性） | `representation_missing` |
| **express** | 观察类路线能表达该关系（确定性算子只能做字面包含） | `semantic_judgment_missing` |

**fit 这一层是本轮最重要的发现。** 首轮干跑把"召回从未运行"（`recall/not_reached`）判为可修，理由是 `evolved_predicate` 能读到 `question_text` 与 `tool_names`——但观察类干预只在 `judge.post` 处报告，**不能让召回扫描器开始运行**。修复需要控制流能力，而当时枚举里唯一的控制流路线是 `recall_trigger`。这是"存在 route"与"具备能力"被混为一谈的又一实例，且是自动干跑抓出来的，不是人工审查。

## 3. 读数

### 3.1 能力规划

| mechanism | 修复操作 | 判定 | 充分 route | 不充分 route（及原因） |
|---|---|---|---|---|
| `M-edb55577e87b` | `route_recall` | actionable | `recall_trigger` | `mechanical_conviction`（guard 不适配）、`recall_directive`/`upgrade_directive`（prompt 不适配） |
| `M-5a89ab46e81f` | `withhold_conviction` | actionable | `withhold_guard` | — |

两个归档机制都有真实可行的修复路线，因此本轮 `gaps` 为 0。这不是缺陷：缺口分类法在**没有缺口时输出空**才是正确行为，否则就是在制造问题。分类法生效的证据在 §3.2 与测试中。

### 3.2 route 反证记忆

对 `M-edb55577e87b` 注入 Attempt-5 形态的两轮观测（hook 触发 1.0、21 次行为事件、目标结果 0 变化）：

```
before: classification=actionable     sufficient=['recall_trigger']
after : classification=research_only  sufficient=[]
        required_capability=already_supported_but_insufficient
        recall_trigger 的 reasons: "route previously fired and changed nothing
          on this mechanism (repeat_observation, 2 observation(s))"
```

即：一个已被实测证明修不好该机制的路线，不再被当作可行方案推荐。

### 3.3 端到端（真实 cycle，mock 后端）

`test_cycle_records_route_insufficiency_end_to_end` 走完整 cycle：候选在 gain 集有 4 个真实机会、哨兵覆盖满足、干预触发（`fired_rate=1.0`、`text_injected` 12 次）、目标结果未变、未被判为不可测量。引擎写入 1 条 `evolution.route_failure.v1` 记录，字段含 `hook_fired`/`output_changed`/`target_outcome_changed`/`gain_opportunities`。

## 4. 纪律：什么不算反证

反证记录会被下一轮当作"此路不通"消费，所以写入条件必须严。以下情形一律**不记**，各有测试：

| 情形 | 为何不算 | 测试 |
|---|---|---|
| hook 从未触发 | 什么都没发生，谈不上下结论 | `test_cycle_does_not_record_insufficiency_for_a_never_fired_candidate` |
| 目标结果改善 | 路线有效 | 参数化用例 |
| 候选造成伤害 | 是回归，不是能力不足 | 参数化用例 |
| runtime 健康退化 | 属基础设施 | 参数化用例 |
| gain 无可修机会 | 没给过路线机会 | `test_insufficiency_requires_a_measurable_opportunity_and_a_substantive_rejection` |
| 拒绝本身是 `not_evaluable` | 未测量 ≠ 已证伪 | 同上 |
| 只有一轮观测 | 一轮不构成证伪 | `test_one_observation_is_not_yet_a_falsification` |

**证伪门槛**：两轮独立观测，或一次带修订证据的观测（后者是方案 §5.2 的原始条件，随 P0.3 版本图落地后启用）。续跑重放不会重复计数——否则一个 cycle 就能自我满足两轮门槛（有测试）。

## 5. 落点

| 文件 | 内容 |
|---|---|
| `evolution/capability_planning.py`（新增） | 三层判定、能力面报告、gap 记录构造、`plan_capabilities` |
| `evolution/route_failures.py`（新增） | schema `evolution.route_failure.v1`、`route_of`/`unit_route`、`insufficiency_evidence`、store 与聚合状态 |
| `evolution/mechanisms.py` | `repair_requirements()` 公开访问器；`required_inputs` 改为运行时真实字段名 |
| `evolution/gates.py` | `behavior_gate` 四个分支携带 `behavior_events`/`behavior_event_kind`（加法式，判据不变） |
| `evolution/gaps.py` | reason 分类法扩展；身份同时剥离 `-P`/`-M` 后缀 |
| `evolution/engine.py` | cycle 读反证 → 规划 → 报告；cycle 尾写反证记录与 planner gaps |

## 6. 边界

- 两个机制"可修"只说明**存在适配的能力类型**，不说明候选一定能修好——那由 P0.3 开发循环与正式门判定。
- 本报告不含准确率、召回率或定位能力数字；`gaps` 为空是真实读数，不是分类法未生效。
- 反证记忆是**诊断与规划输入**，不参与任何门判定，不会因它改变准入。

## 7. 与下一步的关系

P0.3（候选开发循环）现在有了两样输入：每个机制的**能力需求**（本报告 §3.1）与**已证伪路线清单**（§3.2）。开发循环在草拟前应先读这两项，避免把预算花在已知不通的路线组合上。
