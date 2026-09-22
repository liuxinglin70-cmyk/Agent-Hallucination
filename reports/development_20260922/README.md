# P0.3：候选开发循环（2026-09-22）

对应[优化方案](../../agent_hallucination_self_evolution_optimization_plan.md) §23.3 P0.3 与 §6、§7.1–7.4。把部署侧从"一轮一个新候选"改成"同一父候选的多版本开发"，并让开发成本显著低于正式晋升。

复现：`python reports/development_20260922/develop_cycle.py`

## 1. 数据与口径

| 项 | 值 |
|---|---|
| 工作区 | 合成 20 例（mock 后端），无 provider 调用 |
| 证据等级 | **diagnostic-only**：展示循环机制与拒绝行为，不是检测能力读数 |
| mock oracle | 目标可达性由 mock override 提供，因此本报告不构成候选质量结论 |

## 2. 改动

### 2.1 版本图共用（Task 5）

`evolution/candidate_versions.py`（新增）从 `research.py` 抽取版本管理：`vNNN` 目录、`candidate.json`、`parent.json` + `diagnosis.json`、`identity.json`、`results/`。**磁盘格式逐字未变**——`reports/rsi_pilot_20260919/` 的归档仍可读，`test_research.py` 22 项通过。

开发状态（`draft`/`failed_l0`/`failed_l1`/`ready_for_freeze`/`frozen`/`discarded`）写在旁车文件 `development.json`，不动既有记录字段：研究格式是其他报告已在解析的契约，新增键会让它们意外。

### 2.2 Hook 依赖图与重放边界（Task 7 最小版）

`evolution/backends.py` 新增 `HOOK_DEPENDENCY_GRAPH`（10 个 hook → 可达阶段）与 `partial_replay_plan()`。

**顺序修正的依据**：`gates.requires_fresh_evaluation` 只把 `judge.system` 与 `require_full_view_defense` 视为 fresh，其余 hook 走冻结 judge 回放。不先声明"改了哪个 hook 要重跑到哪一层"，L1 只能退化为整臂重跑，开发成本等于晋升成本，L0/L1 分层就没有意义。实测边界：

| 候选 hook | 需重跑阶段 | fresh |
|---|---|---|
| `adjudicate.guard` | adjudication | 否 |
| `tracer.system` | step_tracer, adjudication | 否 |
| `recall.trigger` | recall_scanner, causal_upgrade, step_tracer, defense, adjudication | 否 |
| `judge.system` | 全部 | 是 |

### 2.3 开发循环（Task 6）

`evolution/development.py`（新增）：`CandidateDeveloper.develop()` 跑 Draft → L0 → L1 → 失败诊断 → 修订 → 预算耗尽或成功 → Freeze。

- **L0（零成本）**：schema 可执行、无 case id 泄漏、structured identity 不冲突、**候选确实能到达失败阶段**、确定性谓词不在正常近邻上误触发。
- **L1（定向重放）**：目标案例确有改善 ∧ 近邻未退化 ∧ runtime 健康未退化 ∧ **候选确实触发过**。
- **freeze()**：唯一出口，只接受 `ready_for_freeze`。

## 3. 读数（两种对照）

| | mock 不修目标 | mock 修目标 |
|---|---|---|
| L0 | 通过 | 通过 |
| L1 | **失败**（无目标改善） | 通过 |
| 版本状态 | `failed_l1` | `ready_for_freeze` |
| freeze | **拒绝** | 成功 |
| 产出 | 无 | `v001`，mechanism `M-17aad6d46f7a`，route `recall_trigger` |

开发预算独立记账（`provider_calls` / `l1_cases`），与 promotion 预算分离：开发可以反复试，晋升必须冻结，共用一个计数器会让长开发循环吃掉正式协议需要的额度。

## 4. 本轮修掉的三个真实缺陷

### 4.1 机制阶段名与运行时词汇不一致（影响 P0.1/P0.2 全部读数）

`mechanisms.py` 把失败阶段写作 `recall`、`tracer`，而运行时 `stage_status` 的键是 `recall_scanner`、`step_tracer`。后果不是显示问题：**L0 的可达性检查按运行时词汇查表，于是每一个召回类机制都被判定为"没有任何 hook 能到达"**，开发循环无法启动。

修法：新增 `FAILURE_STAGES` 常量并从运行时词汇取值；`_OPERATION_BY_FAILURE`、`_FAILURE_BY_FAMILY_PREFIX` 与成员级推断全部改用 `recall_scanner`/`step_tracer`。此前 P0.2 报告里 `route_recall` 机制被判可修，靠的是提示路径而不是阶段名比对，所以缺陷没有在那轮暴露。

### 4.2 preview 报告丢弃案例列表

`cmd_develop` 从 `preview_cycle` 取机制与案例，但 preview 的 family 视图只保留 `failure_family`/`count`，`failure_cases` 从未出现；而 `mechanism_prompt_view` 按设计剥离 case id（那是给 proposer 提示用的）。开发循环因此拿到空案例集，`make_intervention` 以"provenance.failure_cases needs >=2 cases"失败。

修法：preview 的 family 视图显式带上 `failure_cases`/`sources`/`target_kind`/`coarse_failure_family` 与 `mechanism_detail`，并注明"提示视图剥离标识符、预览是运行记录"的理由。

### 4.3 L1 用错了判据（我自己的设计错误）

我最初给 L1 加了"目标阶段状态必须变化"，但那不是计划 §7.4 的要求——计划第 4 条是"候选的目标行为发生可观测变化"。阶段状态在 mock 后端下根本不上报，这条会拒绝一切候选。

修法：改为按计划检查**候选确实触发过**（从 deployment 记录读，排除 `recall_trigger_not_matched`/`predicate_evaluated` 这类记账事件）。这条同时堵住一个更隐蔽的问题：hook 从未运行时，"目标没改善"会被误读为机制不可修，而实际是候选根本没进运行时。

## 5. 边界

- 本报告不含检测能力数字；mock oracle 是刻意构造的，只用于让两个对照都可演示。
- 开发循环**不写 registry、不判准入**：它产出候选与开发证据，正式门仍是唯一权威。
- L1 的"近邻未退化"判据在测试中覆盖（§15.4 第一条负例集成测试：目标改善但近邻退化 → 不得 freeze），本干跑未构造该类对照。

## 6. 与下一步的关系

P0.4（机制对齐的正式晋升清单）现在可以消费开发循环的产出：`freeze()` 返回的候选带 `mechanism_id`、`route`、`lineage` 与 `development_evidence`，正式协议的 `development_cases` 校验可直接用这套数据保证开发/晋升隔离。
