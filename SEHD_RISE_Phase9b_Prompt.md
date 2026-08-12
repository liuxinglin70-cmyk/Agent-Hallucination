# 交接任务卡：SEHD-RISE Phase 9b — loop runner 重写合流（C6/C2/C8/C9/C10 串联）

> 用法：本卡由用户在新会话手动粘贴启动。本卡 + 仓库 = 全部所需上下文；
> 9a 的结论不在对话里，而在 9a 提交（`refactor(sehd_rise): phase 9a …`）与
> `SEHD_RISE_Refactor_Execution_Plan.md`「Phase 9a 实施记录」小节里。
> 本卡是 `SEHD_RISE_Phase9_Prompt.md` 的阶段拆分（用户拍板 9a/9b），
> 与文档冲突时以文档为准并回写文档。

## 0. 前置校验（30 秒；任一失败即停，不实施）
- [ ] `git status --short` 恰好只含未跟踪的 `SEHD_RISE_Phase9b_Prompt.md`（9a 已提交，工作区干净；若 9a 未提交则先回 9a 卡）
- [ ] `python -c "import sehd_rise.loop.{machine,result,records,state,persistence,ledger,probes,shadow}"` 通过（8 模块）
- [ ] `python tools/parity/test_loop_machine.py` 全过（10/10）
- [ ] `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise/loop` → 0 命中
- [ ] `python tools/parity/check_layers.py` 绿（75 文件/13 层；loop 不 import 任何上层）
- [ ] 既有 7 harness 仍全绿

## 1. 已完成（9a，已提交）
- `loop/machine.py`：§7 状态机（12 状态 / 33 迁移 / 17 outcome / 4 层）、`RepairStateMachine`、`OutcomeFunnel`（C2 unaddressable 独立计数）。
- `loop/result.py`：`EvolutionStep`（`gate_decision` 承载 Outcome 值或中间标记）/ `OnlineEvolutionResult` / `ProvisionalEdit` / `_build_evolution_result`（签名收 `funnel: OutcomeFunnel`，计数唯一权威见 §2.4）。
- `loop/records.py`：`attribute_failure_for_result` / `validate_evolution_params`。
- `loop/state.py`：`RunState`（字段 Any 宽松，`meta_skill_state` 字段名待 9b 对账见 §2.9b）+ `l2_promotion_evidence`（`gold_polarity == "ACCEPTED"`）。
- `loop/ledger.py`：`GateEvent`（字段 `gate_decision`）/ `GateEventLedger` / `aggregate_gate_experience` / `_project_execution_trace`（`INFRA_FAILURE`）/ `_project_delta_class` / `_project_gate_event` / `_build_gate_event_ledger`（单一投影权威）/ `_record_gate_event` / `_record_attempt`（`CausalFailureEpisode`）。
- `loop/persistence.py`：`load_evolution_checkpoint` / `_restore_state_from_checkpoint` / `_last_audited_step` / `_restore_checkpoint_state` / `_PersistenceContext` / `_persist_progress` / `_save_evolution_results`（schema 仅 v5，禁迁移层；落盘用 `platform.jsonio`）。
- `loop/probes.py`：`PendingProbeCommit` / `_probe_case_delta` / `_probe_certification` / `_probe_certification_label` / `_run_development_probe_impl`（`StrategyState`/`create_baseline_strategy`；`rollback_probe`→`CANARY_REGRESSION`）。
- `loop/shadow.py`：`_shadow_commit_eligible` / `_flush_shadow_batch_impl`（`accept_batch`→`ACCEPTED`、`rollback_batch`→`SOURCE_NOT_IMPROVED`）+ `outcome_class_for`/`_structured_outcome`/`_evict_stable_canaries` 及其闭词汇常量（唯一 sehd_rise 真源）。
- 9a 验证证据见计划文档「Phase 9a 实施记录」§4。

## 2. 关键裁决（9a 已定 + 9b 接线表）
1. **9a/9b 拆分（用户拍板）**：9a 已完成 7 平移模块 + machine.py + 状态机单测 + 提交；9b = `loop/runner.py` 重写合流 + e2e 小样本 + 检查点恢复等价 + 完整验证 + Phase 10 卡。
2. **C8 漏斗词汇** = `machine.Outcome` 闭集；`skip_*` 旧词在 loop 不存在。
3. **`EvolutionStep.gate_decision`** 承载 Outcome 值或中间标记（`provisional` 等）；`ACCEPTED` 视为接受。
4. **`_build_evolution_result` 计数映射**（唯一权威）：`edits_accepted=funnel.accepted()`；`edits_rejected=SOURCE_NOT_IMPROVED+CANARY_REGRESSION+TRANSFER_REGRESSION`；`stable_cases=NON_EVOLVABLE`；`runtime_invalid_cases=INFRA_FAILURE`。
5. **attribution 管线**：gold join → `project_detection(use_gold=)` → `build_causal_cut(signal)`；`repairability.analyze(episode)` 分流（Phase 4 单一权威）。
6. **`_initialize_state` 归属 9b**：其主体（evolution_runner.py:6859-7128+）是纯 `self.*` 绑定，属 runner 上下文 → 随 9b `runner.py` 迁移。
7. **`_project_execution_trace`** 已在 ledger.py（9a 迁入）。
8. **GateEvent 字段 `gate_decision`**（9a 已重命名，`legacy_decision` 废止）。

### 2.9b. 9b 接线表（9a 拋留的对账项，runner 接线时裁决）
- **[W1] gate_decision Outcome 词汇 ↔ `promote()` `LEGACY_DECISIONS` 对账**：9a 将 shadow/probes 的 `step.gate_decision` 写为 Outcome 值（`ACCEPTED`/`SOURCE_NOT_IMPROVED`/`CANARY_REGRESSION`），但 `evaluation.promotion.promote()` 仍以 `LEGACY_DECISIONS`（`accept_batch`/`accept_policy`/`reject_*`/`skip_*`/`provisional`）校验，Outcome 值不在其内 → `_project_gate_event` 捕获 `ValueError` 后 `decision=None`、grade 误降 `skip`。9b 须裁决：扩展 `promote()` 识别 Outcome 值（`ACCEPTED`→accept 类），或在 runner 写 `step.gate_decision` 前映射 Outcome→legacy 决策词。单一真源原则下优先扩 `promote()` 词汇。
- **[W2] persistence/probes 拋留 legacy 词汇的 Outcome 重接**：`_restore_checkpoint_state` 读 `gate_decision == "halted"` / `== "provisional"` / `.startswith("retry_")`、写 `"rollback_resume"`；`_flush_shadow_batch_impl`/`_run_development_probe_impl` 的 `record_attempt(decision="accept_batch"/"rollback_batch")` 为 attempt 级遥测。9b 裁决这些读写点是否换 Outcome 词（读侧 = 旧检查点格式兼容；写侧 = 与 [W1] 一致）。`halted`/`provisional`/`retry_`/`skipped` 非 C8 grep 词（无 `skip_`），9a 已通过；9b 接线时定夺。
- **[W3] `RunState.meta_skill_state` 字段名 ↔ `StrategyState` 对账**：9a 保留字段名 `meta_skill_state`（§3「字段保持」）但赋 `StrategyState` 实例；`_initialize_state` 迁入 runner 时裁决是否重命名字段为 `strategy_state`（9b runner 是字段唯一写处，重命名安全）。
- **[W4] `causal_memory.meta_review.get("meta_state")` 键对账**：`_run_development_probe_impl` 读 `causal_memory.meta_review.get("meta_state")`；9b 裁决 `ExperienceMemory` 持久化 `StrategyState` 的实际键名。
- **[W5] `EvolutionExperienceMemory` → `ExperienceMemory`**：9a 已用 `Any` 类型；9b 接线时核实 `ExperienceMemory` 的 `to_dict`/`from_dict`/`meta_review`/`upsert_attempt` API 与调用点一致。

## 3. 未完成清单（按序执行，逐行勾销后提交）
- [ ] `loop/runner.py`（重写合流，单一编排路径）：`EvolutionLoop.run`（evolution_runner.py:7404-9851，约 2,400 行）+ `_run_l2_policy_case_impl`（6059-6783，约 700 行）+ `run_online_evolution`（10013-10165）→ 重写为 `execute_repair_plan` 主路径。**C6 合流**：L1/L2 两套编排合并为一条（案例队列 → 检测 → `causal.repairability` 分流 → RepairPlan → planning → execution → evaluation → ledger → experience → meta）。消费 9a 落地的 `RunState`/`OutcomeFunnel`/`RepairStateMachine`/`_build_evolution_result`/`_flush_shadow_batch_impl`/`_run_development_probe_impl`/`_persist_progress`/`_restore_checkpoint_state`/`_save_evolution_results`/`_build_gate_event_ledger`/`_record_attempt`。
- [ ] **C2**：`UNADDRESSABLE` 独立计数（`OutcomeFunnel.unaddressable()`），不进 no-edit / 收敛统计。
- [ ] **C9**：收敛窗口只消费 complete repair attempts（plan 合法→提案合法→已执行→已测效应→无可接受改善）；budget exhausted / infra failure / provider failure 不再是收敛证据。
- [ ] **C10**：被接受编辑具备完整信用链（dispatch→edge moved→blocker cleared→terminal improved→canary/transfer preserved）逐环证据落账，任何中间层不替代下一层证据。
- [ ] **[W1-W5] 接线**：按 §2.9b 裁决并落地（单一真源优先扩 `promote()` 词汇）。
- [ ] 防丢失勾销：runner 不在 src 留副本；`_initialize_state` 主体随 runner 落地（§2.6）；无别名/死代码残留。

## 4. 验证（全绿才可提交）
1. 状态机单测 `python tools/parity/test_loop_machine.py` 全过（9a 已建，runner 不改 machine）。
2. **e2e 小样本**：固定夹具案例流，含三场景各一：unaddressable / provider 故障 / 预算耗尽。断言：C2 unaddressable 不进 no-edit；C9 非学习事件不进收敛计数；C10 被接受编辑完整信用链逐环证据；C8 结果 JSON 按四层漏斗统计（`outcome_funnel.to_dict()`）。
3. **检查点恢复等价**：同一运行在中断点恢复后与连续运行的队列位置 / 预算 / 台账一致。
4. 全模块冒烟：`python -c "import sehd_rise.loop.runner, …"`（含 runner）。
5. `python tools/parity/check_layers.py` 绿（76 文件/13 层；runner 不 import 上层）。
6. 既有 7 harness 仍全绿（下层未回归）。
7. S1 维持：`grep -rn "from sehd_rise.loop" sehd_rise/{experience,skills,causal,detection,platform,planning,typing,execution,evaluation,meta}` → 无命中。
8. `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise/loop` → 0 命中。
9. C8 词汇核对：`grep -rn "skip_" sehd_rise/loop` → 无命中；`grep -rn "legacy\|backward" sehd_rise/loop` → 无命中。
10. R2：e2e 小样本若需 provider 调用，用暖缓存回放（`PACA_GLOBAL_CACHE_ROOT`）；prompt 编辑使 mutation cache 失效。纯离线契约校验仍优先。

## 5. 提交 + 回写（顺序执行）
1. `git commit`，格式：
   `refactor(sehd_rise): phase 9b — loop runner rewrite (L1/L2 convergence on RepairPlan, C2/C9/C10 credit chain, outcome funnel wiring)`
   正文：迁移源（EvolutionLoop.run/_run_l2_policy_case_impl/run_online_evolution 行号）、§2.9b 接线裁决（[W1-W5] 逐项落地方案）、验证证据（e2e 三场景 / 检查点恢复等价 / 冒烟 / parity / 7 harness / S1 / pristine / C8 词汇）。
2. 回写计划文档「Phase 9b 实施记录」小节（模式照抄 Phase 2-8 / 9a 条目）：落地裁决、验证摘要、[W1-W5] 接线结论、降级说明。回写内容并入本阶段提交。
3. 更新 zcode 记忆条目 `sehd-rise-refactor-plan` 状态行（9b 已提交 + 哈希；下一=Phase 10）。

## 6. 交接协议（用户手动递推，不用定时任务）
1. 提交 9b 后生成 `SEHD_RISE_Phase10_Prompt.md`（仓库根目录，随 9b 提交入库）：Phase 10 = api/cli 门面 + 端到端 parity 验收（见计划文档 §Phase 10）。内容 = 门面迁移映射 + 防丢失 + 纪律 + 验证（全包 parity / e2e gold holdout / 结构 / S1 / pristine）+ 提交 + 递推（Phase 10 为终态，无下一卡或递推至全包验收报告）+ 最终回复格式。
2. 最终回复末节：报告 `SEHD_RISE_Phase10_Prompt.md` 已生成入库，全文置于反引号代码块供用户复制。
3. 故障停止：前置校验失败 / 验证未全绿 → 不提交，报告证据，由用户介入。

## 7. 最终回复格式（严格）
1. 完成声明：各验证项证据摘要 + 命令原文。
2. 提交哈希 + 勾销后的防丢失清单 + [W1-W5] 接线结论。
3. 裁决/降级记录（§2 各条 + §2.9b 接线 + R2）。
4. `SEHD_RISE_Phase10_Prompt.md` 已生成并入库。
5. `SEHD_RISE_Phase10_Prompt.md` 全文（反引号代码块，供用户复制）。
