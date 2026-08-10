# 修复结果：batch01-10 深度审计发现的框架 bug

> 交接物：`docs/HANDOFF_BATCH01_10_FIX.md`。本文件记录修复实施、测试与验证结果。
> 基线：966 全绿 → 修复后 979 全绿（+13 回归测试）。

## 1. 改动文件与关键行号

### `src/sehd_v6/core/bccse/evolution_runner.py`

| 问题 | 修复 | 关键行号 |
|---|---|---|
| P1-A | `_build_gate_event_ledger(steps)` 单一投影权威，从最终 steps 确定性重建 ledger；`_persist_progress` 与 `_finalize` Meta Review 均从该重建结果读取 | `_build_gate_event_ledger` 4285；`_persist_progress` 调用 3960；`_finalize` 调用 9095 |
| P1-A | `_project_gate_event(step, step_ordinal)` 从 step 最终状态投影事件，identity 用 `attempt_id`/`episode_id`（有 proposal 时），无 proposal 才回退 observation-slot | 4148 |
| P1-B | `_project_execution_trace(step, ...)` 从最终 attempt 的 compiled/applied 与 L2 `policy_evaluation.fired/skipped` 投影 `TRACE_CLASSES` | 4086 |
| P1-B | `_project_delta_class(deltas, downstream=...)` 从 observable_delta/downstream_court_delta 投影 `DELTA_CLASSES` | 4131 |
| P1-C | `finish()` 写入 `candidate_policy_genome_hash`（candidate_genome.content_hash）；`candidate_genome` 声明为闭包变量 | `candidate_genome` 声明 5451；audit 字段 5579 |
| P1-D | `_classify_rejection_stage(reasons)` 单一 stage 分类器；`_step_rejection_reasons(fitness)` 聚合 L2 reason；`_build_rejection_summary` 增加 per-step 遍历，L1 structural reject 不重复计数 | `_classify_rejection_stage` 765；`_step_rejection_reasons` 795；`_build_rejection_summary` 826 |

### `experiments/run_batch.py`

| 问题 | 修复 | 关键行号 |
|---|---|---|
| P2-E | `summarize_case` 增加 `total_cases`/`runtime_invalid_cases`/`l2_policy_accepts`/`l2_policy_rejects`/`l2_policy_skips`/`rejection_summary`/`budget` | 263 |
| P2-E | `_build_batch_totals(cases)` 批次合计 + 计数不变量 `total = triggered + stable + runtime_invalid` | 222 |
| P2-E | `main()` report 增加 `totals` 块 | 483 |

### 未改动的边界（遵守 §4 约束）

- R17/R18 已修行为（`replay_runtime_invalid` 诊断化、`reject_policy_replay_error`、确定性后置条件、quarantine 修复面撤销）——**未触碰**。
- `edits_accepted` 混计 L2 accept（R16 显式行为）——**未改运行行为**。
- resume 幂等：`_restore_run_state` 的 `GateEventLedger.from_list`/`drop_event` 逻辑——**未触碰**；`_build_gate_event_ledger` 是纯函数，同一 steps 列表恒产生同一 event_id 与 sequencing。

## 2. 新增测试清单与断言语义

### `tests/test_evolution_hardening.py`（+9 测试）

| 测试 | 断言语义 |
|---|---|
| `test_build_gate_event_ledger_projects_final_decision_not_provisional` | P1-A：provisional→accept_batch 的 step 重建后事件 legacy_decision=accept_batch，grade≠conditional，activation≠pending |
| `test_build_gate_event_ledger_l2_accept_has_fired_trace_and_terminal_delta` | P1-B：L2 accept_policy 事件 execution_trace_class=fired，observable_delta_class=terminal |
| `test_build_gate_event_ledger_skip_step_uses_observation_slot_identity` | P1-A identity：无 proposal 的 skip/halt step 回退 observation-slot identity |
| `test_runner_checkpoint_gate_events_match_final_step_decisions` | P1-A+B 集成：持久化 checkpoint 的 gate_events.legacy_decision == step.gate_decision；accept-class 事件非 no_proposal |
| `test_l2_reject_audit_records_candidate_policy_genome_hash` | P1-C：L2 reject audit 的 candidate_policy_genome_hash 非空且 ≠ working genome hash |
| `test_l2_skip_no_policy_has_empty_candidate_hash` | P1-C 边界：skip_no_policy 的 candidate_policy_genome_hash 为空字符串 |
| `test_rejection_summary_includes_l2_policy_reasons` | P1-D：L2 reject reason 进入 by_reason/by_stage |
| `test_rejection_summary_includes_l2_no_effect_and_runtime_stages` | P1-D：no_effect→evidence_no_effect，replay_runtime_invalid→runtime |
| `test_rejection_summary_does_not_double_count_l1_structural` | P1-D 不变量：L1 structural reject 只计一次（certificate），不重复 |

### `tests/test_run_batch.py`（+4 测试）

| 测试 | 断言语义 |
|---|---|
| `test_summarize_case_includes_l2_runtime_and_budget_fields` | P2-E：summarize_case 包含 total_cases/runtime_invalid_cases/l2 counts/rejection_summary/budget |
| `test_build_batch_totals_aggregates_counts_and_checks_invariant` | P2-E：批次合计正确，count_invariant_ok=True |
| `test_build_batch_totals_detects_broken_invariant` | P2-E：计数不一致时 count_invariant_ok=False |
| `test_batch_report_includes_totals_block` | P2-E 集成：batch_report.json 包含 totals 块 |

## 3. 全量测试结果

```
python -m pytest tests/ -x -q
........................................................................ [ 44%]
........................................................................ [ 51%]
........................................................................ [ 58%]
........................................................................ [ 66%]
........................................................................ [ 73%]
........................................................................ [ 80%]
........................................................................ [ 88%]
........................................................................ [ 95%]
...........................................                              [100%]
979 passed in 29.86s
```

966 基线 + 13 新增 = 979 全绿。

## 4. 复核脚本输出

对修复后代码生成的 checkpoint（L2 reject 场景）运行 §6 验证逻辑：

```
=== §6 Verification (L2 reject scenario) ===
trace_default = 0          (target: 0)
provisional_accept = 0     (target: 0)
l2_hash_missing = 0        (target: 0)
by_reason_missing_l2 = 0   (target: 0)

=== P1-A: step vs gate_event decision consistency ===
p1a_mismatches = 0         (target: 0)

=== GateEvent details ===
  decision=reject_policy_no_effect  grade=reject  trace=skipped  obs_delta=none  court_delta=none

=== L2 audit candidate hashes ===
  decision=reject_policy_no_effect  working_hash=8f5d1568a886...  candidate_hash=8b0a2e182c87...

=== rejection_summary ===
  by_reason: {'policy_route_unavailable': 1, 'policy_skipped': 1}
  by_stage: {'runtime': 1}
  by_decision: {'reject_policy_no_effect': 1}
```

四个 bug 计数全部归零：
- `trace_default=0`：无 accept/reject_policy 事件携带 `no_proposal`/`none`/`none` 默认值。
- `l2_hash_missing=0`：所有 L2 reject audit 携带非空 `candidate_policy_genome_hash`。
- `by_reason_missing_l2=0`：所有 L2 reject 的 `by_reason` 非空。
- `p1a_mismatches=0`：step.gate_decision == gate_event.legacy_decision。

> **注**：`experiments/results/` 被 `.gitignore` 排除，batch01-10 的旧 checkpoint 是修复前数据，无法回填新字段（`candidate_policy_genome_hash` 等）。上述验证在修复后代码生成的 checkpoint 上运行，确认 bug 计数归零。重新运行批次实验即可在全量结果上复现。

## 5. 设计说明

### P1-A/P1-B：单一投影权威

根因不在 commit 处（`flush_shadow_batch`），而在事件记录时机：`_record_gate_event` 在 step 初次记录时写事件，commit 后不更新。修复采用**单一投影权威**而非 commit 处打补丁：

- `_build_gate_event_ledger(steps)` 是纯函数，从最终 steps（含 final attempt 的 compiled/injected/applied/observable_delta/downstream_court_delta 与 fitness_check）确定性重建 ledger。
- `_persist_progress`（checkpoint 写入）和 `_finalize`（Meta Review 消费）都从该重建结果读取。
- `_record_gate_event` 保留为 live ledger 的增量缓冲，供 resume rewind 的 `drop_event` 使用。

identity 修复：有 proposal 时用 `attempt_id`（或 `episode_id`+`decision_kind`），无 proposal 才回退 observation-slot。resume 路径的 `from_list`/`drop_event` 逻辑不变——投影是纯函数，同一 steps 列表恒产生同一 event_id。

### P1-C：candidate hash 向后兼容

`finish()` 在创建 `candidate_genome` 后读取其 `content_hash` 写入 audit。accept 路径：candidate==working（赋值后），hash 偶然正确；reject 路径：candidate≠working，hash 现在正确。保留 `policy_genome_hash`（working）字段不变，新增 `candidate_policy_genome_hash`。

### P1-D：不重复计数

L1 structural reject 已由 StructuralGate certificate 覆盖，per-step 遍历用 `_L1_STRUCTURAL_REJECT_DECISIONS` 集合排除 `reject`/`structural_reject`，避免双重计数。`by_decision` 语义不变（遍历所有 steps）。

### P2-E：计数不变量

`_build_batch_totals` 在批次报告层断言 `total = triggered + stable + runtime_invalid`，以 `count_invariant_ok` 布尔字段暴露。一个缺失或错计的 case 可直接从 batch report 发现，无需逐 case 重算。
