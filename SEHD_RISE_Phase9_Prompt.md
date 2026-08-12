# 阶段任务卡：SEHD-RISE Phase 9 — loop 统一编排层（Unified Loop Orchestration）

> 用法：本卡由用户在新会话手动粘贴启动（手动递推，不用定时任务）。本卡 + 仓库 = 全部所需上下文；
> 上一会话的结论不在对话里，而在 git 提交与计划文档实施记录中（§3.4 R5）。本卡是
> SEHD_RISE_Refactor_Execution_Plan.md §Phase 9 的渲染，与文档冲突时以文档为准并回写文档。

## 0. 前置校验（30 秒；任一失败即停，不实施、不生成下一阶段任务卡）
- [ ] `git log --oneline -11` 确认 Phase 0–8 已提交：5b88634、50fb9d7、0f144a9、f9b0883、f34495d、
      93b3334、fa04f51、c04fd95，且 Phase 8 提交为
      `refactor(sehd_rise): phase 8 — meta skill as cross-case strategy learner`
      （HEAD 为其后随卡提交或工作区干净）
- [ ] `git status` 干净（无未提交的 Phase 9 之前产物）
- [ ] `sehd_rise/loop/` 只有空白 `__init__.py`（Phase 0 骨架，本阶段从零生长）
- [ ] 计划文档含 Phase 2/3/4/5/6/7/8 实施记录小节（证明接续链正常、记录模式可循）
- [ ] 源文件行数与计划一致：`evolution_runner.py`=10165、`bccse/gate_feedback.py`=338
      （另从 evolution_runner.py 剥离 result/persistence/records/ledger/shadow/probes/state/runner 函数族）
- [ ] 工作目录 = 仓库根目录；`python tools/parity/check_layers.py` 可运行且当前通过
      （Phase 8 终态：67 文件 / 13 层）
- [ ] 既有 7 个 harness 仍全绿：roundtrip_skills / compare_causal / compare_detection
      / compare_experience_typing / compare_planning / compare_effect / compare_meta（基线未回归）

## 1. 迁移映射（源文件只读，目标 sehd_rise/）

| 源（src/sehd_v6/core/bccse/，只读） | 目标 | 方式 |
|---|---|---|
| `evolution_runner.py` 的 `EvolutionStep`/`OnlineEvolutionResult`/`ProvisionalEdit`/`_build_evolution_result` | `loop/result.py` | 平移 |
| `evolution_runner.py` 的 `_PersistenceContext`/`_persist_progress`/`load_evolution_checkpoint`/`_restore_state_from_checkpoint`/`_restore_checkpoint_state`/`_save_evolution_results`/`_last_audited_step` | `loop/persistence.py` | 平移 |
| `evolution_runner.py` 的 `_attribution_for_result`/`_validate_evolution_params` | `loop/records.py` | 平移 |
| `bccse/gate_feedback.py`（338 行）+ `evolution_runner.py` 的 `_project_gate_event`/`_project_delta_class`/`_build_gate_event_ledger`/`_record_gate_event`/`_record_attempt` | `loop/ledger.py` | 合并 |
| `evolution_runner.py` 的 `_shadow_commit_eligible`/`_flush_shadow_batch_impl`（约 600 行） | `loop/shadow.py` | 平移 + 接口重接 |
| `evolution_runner.py` 的 `PendingProbeCommit`/`_probe_case_delta`/`_probe_certification`/`_probe_certification_label`/`_run_development_probe_impl` | `loop/probes.py` | 平移 |
| `evolution_runner.py` 的 `_RunState`/`_initialize_state`/`_l2_promotion_evidence` | `loop/state.py` | 平移 |
| **新增** | `loop/machine.py` | **新建** |
| `EvolutionLoop.run`（约 2,400 行）+ `_run_l2_policy_case_impl`（约 700 行）+ `run_online_evolution` | `loop/runner.py` | **重写（合流）** |

- loop/records.py：案例记录准备与参数校验。`_detector_record` 已迁 `detection/detector.py`、
  `_evaluator_gold` 已迁 `platform/gold.py`、`_authority_failure_details` 已迁 `execution/replay.py`
  （见 Phase 7 实施记录归属迁移裁决），本模块只收编 `_attribution_for_result`/`_validate_evolution_params`。
- loop/ledger.py：GateEvent 台账与全部投影合并于此（维持单一投影权威）；`FUZZY_FEEDBACK_CODES`
  与 `GateExperience` 已在 `meta/review.py`（Phase 8）落地为单一真源，loop import 同集，
  GateEvent→GateExperience 投影函数（旧 `aggregate_gate_experience`）归此处。
- loop/shadow.py：shadow 批量提交；消费 RepairPlan outcome。
- loop/machine.py：纲领 §7 状态机 `DETECTED → CAUSAL_FAILURE_ANALYZED → {NON_EVOLVABLE |
  FRAMEWORK_DEFECT | REPAIRABLE} → REPAIR_PLANNED → HYPOTHESIS_SEARCH → PROPOSAL_VALIDATED →
  EXECUTED → TARGET_EDGE_CHANGED → BLOCKER_CLEARED → FITNESS_EVALUATED → {REJECTED | ACCEPTED}
  → EXPERIENCE_LEARNED → META_UPDATED`；每次迁移携带 §2.9 四层 outcome 终态分类（C8）：
  Framework 层 `NON_EVOLVABLE/UNADDRESSABLE/ROUTING_FAILURE/SCHEMA_FAILURE/INFRA_FAILURE/BUDGET_EXHAUSTED`，
  Search 层 `NO_HYPOTHESIS/INVALID_PROPOSAL/DUPLICATE_PROPOSAL`，
  Execution 层 `ACTION_NOT_DISPATCHED/PROVIDER_NOT_COMPLIED/TARGET_EDGE_UNCHANGED/BLOCKER_NOT_CLEARED`，
  Fitness 层 `SOURCE_NOT_IMPROVED/CANARY_REGRESSION/TRANSFER_REGRESSION/ACCEPTED`。
- loop/runner.py：C6——L1/L2 两套编排合并为一条 `execute_repair_plan` 主路径：案例队列 →
  检测 → `causal.repairability` 分流 → RepairPlan → planning → execution → evaluation → ledger
  → experience → meta。C2——`UNADDRESSABLE_FRAMEWORK_DEFECT` 独立计数，不进入 no-edit/收敛统计。
  C9——收敛窗口只消费 complete repair attempts（plan 合法→提案合法→已执行→已测效应→无可接受改善）；
  budget exhausted / infra failure / provider failure 等不再是收敛证据。

**skip 词汇废止对照（C8 落地核对表）：** `skip_stable`→`NON_EVOLVABLE`；`skip_unaddressable_failure`→`UNADDRESSABLE`；
`skip_runtime_invalid`→`INFRA_FAILURE`；`skip_mutation_budget_exhausted`→`BUDGET_EXHAUSTED`；
`skip_invalid_mutation`→`INVALID_PROPOSAL`；`skip_no_policy` 按原因细分（无路由→`INFRA_FAILURE`，
无候选→`NO_HYPOTHESIS`，提案非法→`INVALID_PROPOSAL`，运行时前置不满足→`ROUTING_FAILURE`）；
`skip_no_edit` 按阶段细分为 Search/Execution/Fitness 层对应终态。旧词汇在新包中不存在。

## 2. 防丢失清单（逐行勾销后方可提交）
- [ ] L1/L2 两套编排合并为一条 `execute_repair_plan` 主路径（C6 编排消费 Canonical RepairPlan 协议）
- [ ] 状态机 §7 全部合法迁移路径可达、非法迁移拒绝、每个 outcome 终态有独立计数（C8）
- [ ] skip 词汇在新包不存在（C8 废止核对表逐项收敛）
- [ ] C2：`UNADDRESSABLE_FRAMEWORK_DEFECT` 独立计数，不进入 no-edit/收敛统计
- [ ] C9：收敛窗口只消费 complete repair attempts；budget/infra/provider failure 不再是收敛证据
- [ ] C10：被接受编辑完整信用链逐环证据（dispatch→edge moved→blocker cleared→terminal improved→canary preserved）
- [ ] GateEvent 台账与投影单一权威（`loop/ledger.py`）；`FUZZY_FEEDBACK_CODES` 与 `GateExperience` 单一真源为 `meta/review.py`（Phase 8 已落），loop import 同集
- [ ] 从 `evolution_runner.py` 剥离的函数族不得在旧 god object 留残渣副本（pristine：不留残渣）
- [ ] 删除项不得以任何别名/死代码残留于新包

## 3. 纪律约束（不可违反）
- 不修改 src/sehd_v6/ 任何文件；不分派 subagent；全程主会话直接执行。
- 包内一律绝对导入：loop 层
  `from sehd_rise.platform... / from sehd_rise.skills... / from sehd_rise.detection...
  / from sehd_rise.causal... / from sehd_rise.experience... / from sehd_rise.typing...
  / from sehd_rise.planning... / from sehd_rise.execution... / from sehd_rise.evaluation...
  / from sehd_rise.meta...`。
- 分层依赖（check_layers.py 为准）：loop → platform, skills, detection, causal, experience,
  typing, planning, execution, evaluation, meta；下方各层永不 import loop。
- 本阶段含**语义变更 C2（统计）/C6（编排）/C8（漏斗）/C9（收敛）/C10（串联）**：
  凡故意偏离旧判定处必须能引用纲领条款（§2.2 C2 / §2.7 C6 / §2.9 C8 / §2.10 C9 / §2.12 C10）。
- 移植即重命名：无 _old/_new、无版本号后缀、无兼容别名（pristine 上线初稿）。
- 注释只写 why；pristine-scan 信号词命中逐个收敛，保留误报必须写收敛记录（模式见 Phase 2/3/4/5/6/7/8）。

## 4. 验证（全绿才可提交）
1. 状态机单测级校验：全部合法迁移路径可达、非法迁移拒绝、每个 outcome 终态有独立计数。
2. 端到端小样本：固定夹具案例流（含人工构造的 unaddressable / provider 故障 / 预算耗尽场景各一），
   断言：C2 unaddressable 不进 no-edit；C9 非学习事件不进收敛计数；C10 被接受编辑具备完整信用链
   （dispatch→edge moved→blocker cleared→terminal improved→canary preserved）逐环证据；
   C8 结果 JSON 按四层漏斗统计。
3. 检查点恢复等价：同一运行在中断点恢复后与连续运行的队列位置/预算/台账一致。
4. 结构：`python tools/parity/check_layers.py`；全包 import 冒烟 `python -c "import sehd_rise"`
   （不只本阶段新增模块，§3.4 DoD）。
5. S1 维持证据：`grep -rn "from sehd_rise.loop" sehd_rise/experience sehd_rise/skills sehd_rise/causal
   sehd_rise/detection sehd_rise/platform sehd_rise/planning sehd_rise/typing sehd_rise/execution
   sehd_rise/evaluation sehd_rise/meta` → 无命中。
6. 既有 7 个 harness 仍全绿（roundtrip_skills / compare_causal / compare_detection
   / compare_experience_typing / compare_planning / compare_effect / compare_meta），证明下层未回归。
7. 暖缓存回放不可用（§3.3 R2）→ 降级为离线契约校验（schema/序列化/确定性判定比对），
   提交信息与实施记录双重显式记录降级，不允许静默跳过。

## 5. 提交 + 回写（顺序执行）
1. git commit，格式：
   `refactor(sehd_rise): phase 9 — unified loop orchestration on RepairPlan, state machine and failure funnel`
   正文含：迁移/重写源文件清单、旧逻辑去向勾销表、验证证据（状态机单测 / 端到端小样本 /
   检查点恢复等价 / S1 维持 / 降级说明）、C2/C6/C8/C9/C10 偏差（引用 §2.2/§2.7/§2.9/§2.10/§2.12）、
   与旧包行为偏差。
2. 提交后回写计划文档「Phase 9 实施记录」小节（模式严格照抄 Phase 2/3/4/5/6/7/8 已有条目）：
   落地裁决、验证证据摘要、降级说明、纯净扫描收敛记录。回写内容并入本阶段提交
   （沿用 f34495d / Phase 5/6/7/8 做法：文档随阶段提交入库）。
3. 更新 zcode 记忆条目 sehd-rise-refactor-plan 的「下一阶段」状态行（一行索引，状态本体在仓库）。

## 6. 交接协议（用户手动递推，不用定时任务）
本卡由用户在新会话手动粘贴启动（Phase 9）。完成第 5 节后按下列步骤为 Phase 10 生成下一阶段任务卡，
由用户手动粘贴递推；Phase 10 完成后停止。

### 6.1 生成下一阶段任务卡
以 `SEHD_RISE_Refactor_Execution_Plan.md` §Phase(N+1) 为唯一内容源，套用本卡第 0–7 节
结构生成 `SEHD_RISE_Phase(N+1)_Prompt.md`（仓库根目录，随本阶段提交入库）。变更点：
- 第 0 节 commit 清单追加本阶段哈希
- 第 1/2/3/4 节按文档对应小节填充
- 第 5 节提交信息用文档原文
- 第 6 节递推到 N+2；Phase 10 改为「不生成下一阶段任务卡，第 7 节输出最终验收汇总」
- 第 7 节第 4 项改为「下一阶段任务卡文件名 + 全文已置于代码块供用户复制」

### 6.2 交付下一阶段启动 prompt（用户手动粘贴）
任务卡文件生成并提交后，**不调用 CronCreate**（用户拍板：手动递推，不用定时任务）。在最终回复（第 7 节）中：
1. 报告 `SEHD_RISE_Phase{N+1}_Prompt.md` 已生成并随本阶段提交入库；
2. 将该任务卡全文置于一个代码块内供用户复制（外层用 4 反引号包裹，使内层 3 反引号不破坏可复制性）；
3. 用户在新会话粘贴该全文启动 Phase {N+1}。

### 6.3 故障停止（不递推坏状态）
- 第 0 节前置校验失败 → 停止，不提交，报告故障项，由用户介入。
- 第 4 节验证未全绿 → 不提交，报告失败项与证据。
- 任务卡文件未生成或未提交 → 报告错误（下一会话读不到文件）。

### 6.4 终止条件
Phase 10 完成后不生成下一阶段任务卡；第 7 节输出最终验收汇总（各阶段哈希、C1–C10 语义变更落地清单、
parity 报告、纯净扫描终验），重构结束。

## 7. 最终回复格式（严格）
1. 完成声明：各验证项证据摘要 + 证据输出文件/命令原文。
2. 提交哈希 + 勾销后的防丢失清单。
3. C2/C6/C8/C9/C10 偏差记录（引用 §2.2/§2.7/§2.9/§2.10/§2.12）+ 降级记录（R2，若有）。
4. 下一阶段任务卡交付确认：`SEHD_RISE_Phase10_Prompt.md` 已生成并入库。
5. `SEHD_RISE_Phase10_Prompt.md` 全文（置于代码块内，供用户复制；下一会话由用户手动粘贴启动）。
