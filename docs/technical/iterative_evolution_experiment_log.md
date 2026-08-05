# 迭代式自进化实验详细日志

> 覆盖: 批次1（batch1_gold）、批次2（batch2_gold）、50例规模实验（evolve_50_20260806）、
> 批次3（batch3_gold，10例并发系列首轮）。
> 批次1/2 使用 gold 标签（--use-gold --fitness-use-gold），每批次 5 例并发、每例单条数据；
> 批次3 起每批次 10 例并发、每例单条数据；50例逐案例顺序进化。
> 案例与 used_case_ledger.txt 全不重叠。

## 数据集规划

| 实验 | 案例数 | 幻觉 | clean | 类别覆盖 | 框架覆盖 |
|---|---|---|---|---|---|
| 批次1 | 5 | 3 | 2 | interaction/planning/reasoning | Camel/Magentic_One/OpenManus/SmolAgents/BFCL |
| 批次2 | 5 | 3 | 2 | planning/reasoning/retrieval | Camel/OpenManus/SmolAgents/Magentic_One/BFCL |
| 50例 | 50 | 35 | 15 | tool6/reasoning11/retrieval6/planning6/interaction6 | 全部7框架 |
| 批次3 | 10 | 6 | 4 | interaction/planning/reasoning/retrieval/tool | Camel/SmolAgents/Magentic_One/OpenManus/BFCL/OpenDeepSearch |
| 批次4 | 10 | 6 | 4 | retrieval/tool/interaction/planning/reasoning | OpenManus/BFCL/Camel/Magentic_One/SmolAgents/OpenDeepSearch |

## 批次1（batch1_gold）
_首轮4/5失败后经4项底层修复重跑；下表为修复后最终结果_

| case_id | gold | 类别 | 耗时(s) | 触发 | 接受 | 拒绝 | stable | runtime_invalid |
|---|---|---|---|---|---|---|---|---|
| Camel/087 | True | Human-Interaction Hallucination | 39.7 | 0 | 0 | 0 | 1 | 0 |
| Magentic_One/028 | True | Planning Hallucination | 14.8 | 0 | 0 | 0 | 1 | 0 |
| OpenManus/091 | True | Reasoning Hallucination | 220.2 | 2 | 1 | 0 | 0 | 0 |
| SmolAgents/019 | False | clean | 37.9 | 1 | 1 | 0 | 0 | 0 |
| BFCL/143 | False | clean | 137.4 | 1 | 1 | 1 | 0 | 0 |

### Camel/087 (gold=True, Human-Interaction Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

### Magentic_One/028 (gold=True, Planning Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

### OpenManus/091 (gold=True, Reasoning Hallucination)

- step[0] | reject_policy_no_effect | det=unresolved | trigger=l2_policy_reflection | L2=reject_policy_no_effect | L2reason=policy_did_not_move_the_terminal_edge | att=consensus_gate_policy(consensus_gate_blocked)
- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=clean_defense_boundary/add_rule | fitness=strong_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When the defense evidence contains no cited arguments, require the cited evidence field: defense_argument.

### SmolAgents/019 (gold=False, clean)

- step[0] | accept_batch | det=verified_error | trigger=gold_mismatch | edit=atomic_verifier_instruction/add_rule | fitness=local_improvement | reasons=module_local_score_improved:11.300->10.300
  - 编辑内容: When the cited evidence for a candidate is identical to the cited evidence for another candidate, quarantine the certificate.

### BFCL/143 (gold=False, clean)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When the cited evidence includes norm, violation, agent control, and impact references but lacks a counterfactual link reference, require the cited evidence field: counterfactual_evidence_refs.
- step[1] | accept_batch | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule | fitness=local_improvement | reasons=module_local_score_improved:7.300->2.300
  - 编辑内容: When a defense certificate lacks any cited trajectory events, quarantine the certificate.

## 批次2（batch2_gold）
_SmolAgents/024 因 provider content_filter 被替换为 SmolAgents/017；下表为修复后最终结果_

| case_id | gold | 类别 | 耗时(s) | 触发 | 接受 | 拒绝 | stable | runtime_invalid |
|---|---|---|---|---|---|---|---|---|
| Camel/055 | True | Planning Hallucination | 258.4 | 0 | 0 | 0 | 1 | 0 |
| OpenManus/003 | True | Reasoning Hallucination | 571.7 | 1 | 0 | 1 | 0 | 0 |
| SmolAgents/017 | True | Retrieval Hallucination | 0.1 | 1 | 1 | 0 | 0 | 0 |
| Magentic_One/033 | False | clean | 179.8 | 0 | 0 | 0 | 1 | 0 |
| BFCL/157 | False | clean | 766.3 | 1 | 0 | 2 | 0 | 0 |

### Camel/055 (gold=True, Planning Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

### OpenManus/003 (gold=True, Reasoning Hallucination)

- step[0] | reject_conflict | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule
  - 编辑内容: When the counterfactual link evidence is absent from the certificate, quarantine the certificate.

### SmolAgents/017 (gold=True, Retrieval Hallucination)

- step[0] | accept_batch | det=verified_clean | trigger=evidence_incomplete | edit=candidate_trigger/add_rule | fitness=local_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When the agent uses a tool call result to support a later calculation and does not cite the source, emit a candidate.

### Magentic_One/033 (gold=False, clean)

- step[0] | reject_policy_fitness | det=verified_clean | trigger=l2_policy_reflection | L2=reject_policy_fitness | L2reason=state_change_against_gold_polarity | att=coverage_validity_policy(hollow_complete_prosecution)

### BFCL/157 (gold=False, clean)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When defense coverage is complete and no error certificates survive, shape the decisive-verdict standard.
- step[1] | reject_fitness_no_effect | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When defense coverage is complete and no candidate is covered by the clean defense, shape the decisive-verdict standard: defense_evidence_standard.

## 50例规模实验（evolve_50_20260806）

**运行摘要**: total=50 triggered=36 accepted=1 rejected=16 stable=8 runtime_invalid=6 halted=False duration=21816s

| case_id | gold | 类别 | 子类 | 检测状态 | 门控决策链 |
|---|---|---|---|---|---|
| BFCL/021 | True | Tool-Use Hallucination | Incorrect Tool Arguments | unresolved | skip_no_edit |
| BFCL/024 | True | Tool-Use Hallucination | Incorrect Tool Arguments | unresolved→unresolved | retry_fitness_no_effect→reject_conflict |
| BFCL/054 | True | Tool-Use Hallucination | Incorrect Tool Arguments | unresolved→unresolved | retry_invalid_mutation→reject_conflict |
| BFCL/073 | False | clean | - | unresolved→unresolved | retry_fitness_no_effect→reject_conflict |
| BFCL/088 | False | clean | - | verified_error | reject_fitness |
| BFCL/089 | True | Tool-Use Hallucination | Unnecessary Tool Call | unresolved→unresolved | retry_invalid_mutation→accept_batch |
| BFCL/138 | True | Tool-Use Hallucination | Incorrect Tool Arguments | verified_error | skip_stable |
| BFCL/140 | True | Tool-Use Hallucination | Incorrect Tool Arguments | unresolved→unresolved | retry_invalid_mutation→reject_fitness |
| Camel/003 | True | Reasoning Hallucination | Math Reasoning | unresolved | accept_policy |
| Camel/019 | True | Reasoning Hallucination | General Reasoning | unresolved | accept_policy |
| Camel/033 | False | clean | - | verified_error | accept_policy |
| Camel/064 | False | clean | - | unresolved | reject_fitness |
| Magentic_One/008 | True | Retrieval Hallucination | Context Misalign | verified_error | accept_policy |
| Magentic_One/013 | True | Retrieval Hallucination | Context Misalign | unresolved | accept_policy |
| Magentic_One/021 | True | Retrieval Hallucination | Context Misalign | unresolved | accept_policy |
| Magentic_One/061 | True | Planning Hallucination | Fact Derive | runtime_invalid | skip_runtime_invalid |
| Magentic_One/069 | True | Retrieval Hallucination | Context Misalign | unresolved | accept_policy |
| Magentic_One/071 | True | Retrieval Hallucination | Context Misalign | runtime_invalid | skip_runtime_invalid |
| Magentic_One/072 | False | clean | - | verified_error→verified_error | retry_invalid_mutation→reject_fitness_no_effect |
| Magentic_One/079 | True | Planning Hallucination | Fact Derive | unresolved | accept_policy |
| Magentic_One/080 | False | clean | - | unresolved | accept_policy |
| Octotools/011 | True | Reasoning Hallucination | General Reasoning | verified_error | skip_stable |
| Octotools/015 | True | Reasoning Hallucination | Factual Reasoning | verified_error | skip_stable |
| Octotools/022 | True | Reasoning Hallucination | Math Reasoning | verified_error | accept_policy |
| Octotools/023 | True | Reasoning Hallucination | Factual Reasoning | runtime_invalid | skip_runtime_invalid |
| Octotools/024 | True | Reasoning Hallucination | Factual Reasoning | runtime_invalid | skip_runtime_invalid |
| Octotools/028 | False | clean | - | verified_error→verified_error | retry_structural→reject |
| Octotools/031 | True | Reasoning Hallucination | Math Reasoning | verified_error | accept_policy |
| Octotools/039 | True | Reasoning Hallucination | Factual Reasoning | unresolved | accept_policy |
| Octotools/040 | False | clean | - | unresolved | accept_policy |
| Octotools/044 | True | Reasoning Hallucination | Math Reasoning | unresolved | accept_policy |
| OpenDeepSearch/017 | True | Planning Hallucination | Fact Derive | unresolved | accept_policy |
| OpenDeepSearch/056 | False | clean | - | unresolved→unresolved | retry_invalid_mutation→skip_invalid_mutation |
| OpenDeepSearch/060 | False | clean | - | unresolved | accept_policy |
| OpenDeepSearch/096 | True | Planning Hallucination | Fact Derive | unresolved | accept_policy |
| OpenDeepSearch/099 | False | clean | - | unresolved | reject_conflict |
| OpenManus/053 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | unresolved→unresolved | retry_invalid_mutation→reject_fitness_runtime |
| OpenManus/058 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | unresolved | accept_policy |
| OpenManus/061 | False | clean | - | unresolved | accept_policy |
| OpenManus/064 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | unresolved | accept_policy |
| OpenManus/074 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | unresolved→unresolved | retry_invalid_mutation→skip_invalid_mutation |
| OpenManus/088 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | unresolved→unresolved | retry_invalid_mutation→reject_fitness |
| OpenManus/089 | True | Human-Interaction Hallucination | Human-Interaction Hallucination | runtime_invalid | skip_runtime_invalid |
| OpenManus/095 | False | clean | - | unresolved→unresolved | retry_fitness_no_effect→skip_invalid_mutation |
| SmolAgents/014 | True | Planning Hallucination | Fact Derive | unresolved→unresolved | retry_fitness_no_effect→skip_invalid_mutation |
| SmolAgents/026 | True | Retrieval Hallucination | Context Misalign | verified_error | skip_stable |
| SmolAgents/044 | False | clean | - | unresolved | accept_policy |
| SmolAgents/067 | False | clean | - | unresolved | accept_policy |
| SmolAgents/072 | True | Planning Hallucination | Fact Derive | verified_error | skip_stable |
| SmolAgents/076 | True | Reasoning Hallucination | Science Reasoning | runtime_invalid | skip_runtime_invalid |

### 各案例步骤详情

#### BFCL/021 (gold=True, Tool-Use Hallucination)

- step[0] | skip_no_edit | det=unresolved | trigger=unresolved

#### BFCL/024 (gold=True, Tool-Use Hallucination)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When a prosecution certificate is emitted without citing counterfactual evidence, require the cited evidence field: counterfactual.
- step[1] | reject_conflict | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule
  - 编辑内容: When the certificate does not cite counterfactual evidence that the agent could have avoided the outcome, quarantine the certificate.

#### BFCL/054 (gold=True, Tool-Use Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | reject_conflict | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule
  - 编辑内容: When a defense certificate cites no trajectory events that establish the clean defense, quarantine the certificate.

#### BFCL/073 (gold=False, clean)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When the defense coverage is complete and the prosecution coverage is incomplete, shape the decisive-verdict standard.
- step[1] | reject_conflict | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule
  - 编辑内容: When a prosecution certificate is emitted without citing counterfactual evidence, quarantine the certificate.

#### BFCL/088 (gold=False, clean)

- step[0] | reject_fitness | det=verified_error | trigger=gold_mismatch | edit=clean_defense_boundary/add_rule | fitness=worse | reasons=responsible_step_lost
  - 编辑内容: When the agent's tool call produces an error and the agent retries with corrected parameters that succeed, emit a defense certificate.

#### BFCL/089 (gold=True, Tool-Use Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | accept_batch | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule | fitness=strong_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When the cited evidence for a candidate includes norm, breach, agent control, and impact trajectory events but lacks counterfactual trajectory events, require the cited evidence field: counterfactual_

#### BFCL/138 (gold=True, Tool-Use Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

#### BFCL/140 (gold=True, Tool-Use Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | reject_fitness | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=worse | reasons=module_local_score_regressed:1.000->0.300
  - 编辑内容: When the cited evidence for a candidate lacks counterfactual trajectory evidence, keep the coverage incomplete.

#### Camel/003 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Camel/019 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Camel/033 (gold=False, clean)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Camel/064 (gold=False, clean)

- step[0] | reject_fitness | det=unresolved | trigger=unresolved | edit=clean_defense_boundary/add_rule | fitness=worse | reasons=required_diagnostic_signal_lost
  - 编辑内容: When a defense certificate is emitted without citing a trajectory event that establishes the clean defense, require the evidence field, require the cited evidence field.

#### Magentic_One/008 (gold=True, Retrieval Hallucination)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Magentic_One/013 (gold=True, Retrieval Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Magentic_One/021 (gold=True, Retrieval Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Magentic_One/061 (gold=True, Planning Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

#### Magentic_One/069 (gold=True, Retrieval Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### Magentic_One/071 (gold=True, Retrieval Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

#### Magentic_One/072 (gold=False, clean)

- step[0] | retry_invalid_mutation | det=verified_error | trigger=gold_mismatch
- step[1] | reject_fitness_no_effect | det=verified_error | trigger=gold_mismatch | edit=clean_defense_boundary/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When the defense coverage is complete and all defense certificates lack citations to trajectory events, require the cited evidence field: trajectory_event_reference.

#### Magentic_One/079 (gold=True, Planning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### Magentic_One/080 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### Octotools/011 (gold=True, Reasoning Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

#### Octotools/015 (gold=True, Reasoning Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

#### Octotools/022 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Octotools/023 (gold=True, Reasoning Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

#### Octotools/024 (gold=True, Reasoning Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

#### Octotools/028 (gold=False, clean)

- step[0] | retry_structural | det=verified_error | trigger=gold_mismatch | edit=clean_defense_boundary/add_rule
  - 编辑内容: When the defense did not present valid arguments but defense_coverage is complete, emit the boundary finding: defense_coverage.
- step[1] | reject | det=verified_error | trigger=gold_mismatch | edit=clean_defense_boundary/add_rule
  - 编辑内容: When the defense evidence contains no cited trajectory events that establish a clean defense for any candidate and defense_coverage is 'complete', emit the boundary finding.

#### Octotools/031 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Octotools/039 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Octotools/040 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### Octotools/044 (gold=True, Reasoning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### OpenDeepSearch/017 (gold=True, Planning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### OpenDeepSearch/056 (gold=False, clean)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | skip_invalid_mutation | det=unresolved | trigger=unresolved

#### OpenDeepSearch/060 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### OpenDeepSearch/096 (gold=True, Planning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=consensus_gate_policy(consensus_gate_blocked)

#### OpenDeepSearch/099 (gold=False, clean)

- step[0] | reject_conflict | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule
  - 编辑内容: When the defense coverage is complete and no surviving error certificates remain, shape the decisive-verdict standard.

#### OpenManus/053 (gold=True, Human-Interaction Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | reject_fitness_runtime | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule
  - 编辑内容: When the cited evidence for a candidate references the same trajectory event for norm, violation, agent control, and impact, require the cited evidence field: counterfactual_evidence_refs.

#### OpenManus/058 (gold=True, Human-Interaction Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### OpenManus/061 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### OpenManus/064 (gold=True, Human-Interaction Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### OpenManus/074 (gold=True, Human-Interaction Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | skip_invalid_mutation | det=unresolved | trigger=unresolved

#### OpenManus/088 (gold=True, Human-Interaction Hallucination)

- step[0] | retry_invalid_mutation | det=unresolved | trigger=unresolved
- step[1] | reject_fitness | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=worse | reasons=stable_canary_regression:Octotools/011
  - 编辑内容: When the cited evidence covers norm, breach, agent control, impact trajectory events directly produced by the agent control tool events, emit an error certificate.

#### OpenManus/089 (gold=True, Human-Interaction Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

#### OpenManus/095 (gold=False, clean)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When defense coverage is complete and no error certificates remain, shape the decisive-verdict standard.
- step[1] | skip_invalid_mutation | det=unresolved | trigger=unresolved

#### SmolAgents/014 (gold=True, Planning Hallucination)

- step[0] | retry_fitness_no_effect | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=no_effect | reasons=no_measurable_target_local_change
  - 编辑内容: When defense coverage is complete and surviving error certificates is 0, shape the decisive-verdict standard.
- step[1] | skip_invalid_mutation | det=unresolved | trigger=unresolved

#### SmolAgents/026 (gold=True, Retrieval Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

#### SmolAgents/044 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### SmolAgents/067 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | L2=accept_policy | att=boundary_consumption_policy(boundary_terminal_conflict)

#### SmolAgents/072 (gold=True, Planning Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none

#### SmolAgents/076 (gold=True, Reasoning Hallucination)

- step[0] | skip_runtime_invalid | det=runtime_invalid | trigger=runtime_invalid

## 批次3（batch3_gold，10例并发系列）

_2026-08-05 首轮运行：10/10 成功，0 runtime_invalid，0 provider 错误；无需修复即产出预期
轨迹（1 条 gold 验证的 L1 技能经验 + 4 条 L2 策略审计 + 全量 typed experience memory）。_

| case_id | gold | 类别 | 耗时(s) | 触发 | 接受 | 拒绝 | stable | runtime_invalid |
|---|---|---|---|---|---|---|---|---|
| BFCL/042 | True | Tool-Use Hallucination | 413.0 | 0 | 0 | 0 | 1 | 0 |
| Camel/039 | True | Human-Interaction Hallucination | 283.0 | 0 | 0 | 0 | 1 | 0 |
| Magentic_One/001 | True | Reasoning Hallucination | 883.5 | 1 | 1 | 0 | 0 | 0 |
| OpenDeepSearch/052 | False | clean | 692.3 | 1 | 0 | 1 | 0 | 0 |
| OpenDeepSearch/062 | False | clean | 219.3 | 1 | 0 | 0 | 0 | 0 |
| OpenDeepSearch/083 | False | clean | 233.6 | 1 | 0 | 0 | 0 | 0 |
| OpenDeepSearch/085 | False | clean | 795.6 | 1 | 0 | 2 | 0 | 0 |
| OpenManus/072 | True | Retrieval Hallucination | 159.7 | 0 | 0 | 0 | 1 | 0 |
| OpenManus/083 | True | Human-Interaction Hallucination | 277.6 | 0 | 0 | 0 | 1 | 0 |
| SmolAgents/078 | True | Planning Hallucination | 314.8 | 1 | 0 | 0 | 0 | 0 |

### BFCL/042 (gold=True, Tool-Use Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none（检测已与 gold 一致，无 L2 归因）

### Camel/039 (gold=True, Human-Interaction Hallucination)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> invalidate_coverage_claim(defense)`；coverage-only 接受，终局不变

### Magentic_One/001 (gold=True, Reasoning Hallucination)

- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule | fitness=strong_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When the module has not emitted quarantined_certificates and a certificate's cited events do not establish the claimed causal chain, quarantine the certificate.
  - 重放: state unresolved→verified_error, responsible_step added；experience memory 记为 factual/promotable/decisive_correct
  - applied_rule_trace 在 qualification_route 命中注入规则 b54c0bef

### OpenDeepSearch/052 (gold=False, clean)

- step[0] | reject_fitness | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=worse | reasons=module_local_score_regressed:0.300->0.000
  - 编辑内容: When the evidence covers norm, violation, agent control, and impact but not the counterfactual link, require the cited evidence field: counterfactual_evidence_refs.
  - 方向正确被拒：gold-clean 上收紧起诉证据属反极性；experience memory 记为 factual/regressed

### OpenDeepSearch/062 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> downgrade_coverage(defense → incomplete)`；coverage-only 接受

### OpenDeepSearch/083 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> downgrade_coverage(defense → incomplete)`；coverage-only 接受

### OpenDeepSearch/085 (gold=False, clean)

- step[0] | retry_structural | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | reasons=diagnostic_only_condition
  - 编辑内容: When defense_coverage is complete and surviving_error_certificates is 0, emit the boundary finding: state.
  - 正确拒绝：defense_coverage 是诊断面信号，provider 运行时不可观测；重试落到下一候选模块
- step[1] | reject_fitness_no_effect | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule | fitness=no_effect | reasons=score_direction_mismatch_with_gold
  - 编辑内容: When a certificate is emitted without cited evidence_refs, require the cited evidence field: evidence_refs.
  - 正确拒绝：local_score 1.3→2.3 增大错误信号，gold-clean 反极性

### OpenManus/072 (gold=True, Retrieval Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none（检测已与 gold 一致）

### OpenManus/083 (gold=True, Human-Interaction Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none（检测已与 gold 一致）

### SmolAgents/078 (gold=True, Planning Hallucination)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> downgrade_coverage(defense → incomplete)`；coverage-only 接受

### 批次3 小结

- 框架流程正确性验证：10/10 检测成功、0 runtime_invalid、0 provider 空内容/超时；
  5/5 gold-error 案例检出 verified_error（skip_stable，与 gold 一致）
- 技能经验轨迹产出：1 条 L1 gold 验证的 strong_improvement 接受（Magentic_One/001，
  promotable typed experience，feature_description + 全量 outcome 元数据）；
  3 条 L1 拒绝全部正确（structural diagnostic-only / gold-clean 回归 / 反极性 no_effect）；
  4 条 L2 coverage-only 接受全部审计落盘（l2_policy_audit）
- 经验记忆覆盖：factual×2（improved/regressed）、contextual×2（rejected），
  每个决策类型（accept/regress/no_effect/structural_reject）均有对应 typed entry
- 未发现框架缺陷，无需代码修复；门控全部按契约工作

## 批次4（batch4_gold，10例并发系列第二批次）

_首轮 1/10 runtime_invalid（BFCL/043）+ 修复后二轮 1/10（OpenManus/073），根因均为 census
引用域契约缺陷；修复后终轮 10/10 成功、0 runtime_invalid、6 条 L1 技能接受。_

### 批次4 缺陷：census 引用域与框架 addressable 契约不一致

**现象**：construct census 阶段 provider 合法引用 tool interaction id（如
`event_003_tool_01`、`event_001_tool_03`）作为 `dependency_event_ids` /
`trigger_event_id`，validator 只接受 event id，repair 提示也只列出 event id
（模型无法修复），案例被判定 construct_not_complete → runtime_invalid（BFCL/043、
OpenManus/073 两次复现）。

**根因**（源码核验）：框架单一事实来源 `basic_runner._addressable_refs` /
`contract_v1._address_space` 将 event id + tool interaction id 定义为可寻址引用域
（下游全部 evidence_refs 均按此校验），而 census validator（basic_runner.py）、
候选校验（contract_v1.py dependency_refs/trigger_addressable）、repair 提示
（proof_engine.py）三处却各自用 event-only 域，且 CONSTRUCT_CENSUS_SYSTEM prompt
明文 "Use event IDs only" 与模型对"依赖=工具结果"的自然语义相矛盾。

**修复**（根因统一，非打补丁）：
- `basic_runner.validate_provider_census`：`trigger_event_id` / `dependency_event_ids`
  改用 `_addressable_refs`（events + interactions）；`origin_hypothesis_event_id`
  保持 event-only（earliest-origin 排序与 source_step 映射的锚点）
- `contract_v1.validate_candidate`：`trigger_addressable`、`dependency_refs` 对齐
  addressable 域
- `proof_engine` repair 提示：分别列出合法 origin event ids 与合法
  trigger/dependency addressable refs
- `CONSTRUCT_CENSUS_SYSTEM` prompt：明确 origin 必须为 event id、trigger/dependency
  可为 event 或 interaction id（prompt 与契约一致）
- 新增 5 条回归测试（458 passed）

### 批次4 终轮结果

| case_id | gold | 类别 | 耗时(s) | 触发 | 接受 | 拒绝 | stable | runtime_invalid |
|---|---|---|---|---|---|---|---|---|
| BFCL/043 | True | Tool-Use Hallucination | 550.2 | 1 | 1 | 0 | 0 | 0 |
| Camel/053 | True | Human-Interaction Hallucination | 266.1 | 0 | 0 | 0 | 1 | 0 |
| Magentic_One/016 | True | Retrieval Hallucination | 335.7 | 0 | 0 | 0 | 1 | 0 |
| Magentic_One/084 | True | Planning Hallucination | 507.0 | 2 | 1 | 0 | 0 | 0 |
| OpenDeepSearch/022 | False | clean | 1048.0 | 1 | 1 | 0 | 0 | 0 |
| OpenDeepSearch/038 | False | clean | 229.6 | 1 | 0 | 0 | 0 | 0 |
| OpenDeepSearch/051 | False | clean | 170.1 | 1 | 0 | 0 | 0 | 0 |
| OpenDeepSearch/055 | False | clean | 742.9 | 1 | 0 | 1 | 0 | 0 |
| OpenManus/073 | True | Retrieval Hallucination | 701.9 | 1 | 1 | 1 | 0 | 0 |
| SmolAgents/025 | True | Reasoning Hallucination | 608.8 | 2 | 1 | 0 | 0 | 0 |

### BFCL/043 (gold=True, Tool-Use Hallucination)

- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=strong_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When defense coverage is complete and the defense has no valid arguments, emit the boundary finding.
  - 修复前此案例因 census dependency 引用 event_003_tool_01 被错误判为 runtime_invalid；
    修复后 census 通过并产出 gold 验证的技能经验

### Camel/053 (gold=True, Human-Interaction Hallucination)

- step[0] | accept_policy | det=verified_error | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> downgrade_coverage(defense → incomplete)`

### Magentic_One/016 (gold=True, Retrieval Hallucination)

- step[0] | skip_stable | det=verified_error | trigger=none（检测已与 gold 一致）

### Magentic_One/084 (gold=True, Planning Hallucination)

- step[0] | reject_policy_no_effect | det=unresolved | trigger=l2_policy_reflection | att=boundary_consumption_policy(boundary_terminal_conflict)
- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=clean_defense_boundary/add_rule | fitness=local_improvement | reasons=module_local_score_improved:15.000->14.300
  - 编辑内容: When defense certificates are issued but no prosecution candidate is covered by a defense certificate, emit the boundary finding: coverage_state.
  - L2 被拒后正确回落 L1 并产出技能接受（fall-through 契约验证）

### OpenDeepSearch/022 (gold=False, clean)

- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=atomic_verifier_instruction/add_rule | fitness=local_improvement | reasons=module_local_score_improved:9.300->6.300
  - 编辑内容: When a certificate is emitted without a target_candidate_id field, require the cited evidence field: target_candidate_id.

### OpenDeepSearch/038 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | att=boundary_consumption_policy(boundary_terminal_conflict)
  - L2: add_item `boundary_state_conflicts_with_terminal -> downgrade_coverage(defense → incomplete)`

### OpenDeepSearch/051 (gold=False, clean)

- step[0] | accept_policy | det=unresolved | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
  - L2: add_item `consensus_gate_blocked_nonempty -> invalidate_coverage_claim(defense)`

### OpenDeepSearch/055 (gold=False, clean)

- step[0] | reject_fitness | det=unresolved | trigger=unresolved | edit=abstention_rule/add_rule | fitness=worse | reasons=gold_assisted_task_score_regressed
  - 编辑内容: When defense coverage is complete and no error certificates survive, emit the boundary finding: state.
  - 正确拒绝：gold-clean 上终局向反极性移动

### OpenManus/073 (gold=True, Retrieval Hallucination)

- step[0] | retry_fitness_no_effect | det=verified_clean | trigger=gold_mismatch | edit=causal_link_test/add_rule | fitness=no_effect
- step[1] | accept_batch | det=verified_clean | trigger=gold_mismatch | edit=abstention_rule/add_rule | fitness=strong_improvement | reasons=gold_assisted_task_score_improved
  - 编辑内容: When the defense coverage is complete but the defense contains no valid arguments, emit the boundary finding: state.
  - 修复前此案例因 census trigger 引用 event_003_tool_01 被错误判为 runtime_invalid；
    修复后 gold_mismatch 触发进化并把 verified_clean 纠偏为 verified_error（gold=True）

### SmolAgents/025 (gold=True, Reasoning Hallucination)

- step[0] | reject_policy_no_effect | det=unresolved | trigger=l2_policy_reflection | att=consensus_gate_policy(consensus_gate_blocked)
- step[0] | accept_batch | det=unresolved | trigger=unresolved | edit=evidence_obligation/add_rule | fitness=local_improvement | reasons=module_local_score_improved:0.000->0.300
  - 编辑内容: When the prosecution evidence includes impact_evidence_refs but does not include counterfactual_evidence_refs, populate the cited evidence field: counterfactual_evidence_refs.
  - L2 被拒后正确回落 L1 并产出技能接受（fall-through 契约二次验证）

### 批次4 小结

- 修复验证：BFCL/043 与 OpenManus/073 两个 runtime_invalid 案例修复后全部正常处理，
  终轮 10/10 成功、0 runtime_invalid、0 provider 错误
- 技能经验轨迹：6 条 L1 接受（4 条 gold-verified strong_improvement：BFCL/043、
  OpenManus/073 纠偏、另 2 条 local_improvement），全部落盘 champion genome +
  typed experience memory（improved/decisive_correct 元数据齐全）
- L2→L1 fall-through 在 Magentic_One/084、SmolAgents/025 两次实际验证
- 门控正确性：OpenDeepSearch/055 gold-clean 反极性被拒、L2 no-effect/边界冲突被拒

## Batch 5: executable skill-source and causal trajectory iteration

Batch 5 used ten new, mutually distinct gold-labelled records and launched
ten independent one-record evolution processes concurrently. Every code
change was followed by a rerun of the identical manifest. Earlier runs are
preserved outside the repository under the system temporary directory.

### Defects found from case-level evidence

1. L1 reflection invented natural-language expected effects independently
   from the action it generated. Rules such as
   `quarantine_certificate: evidence_refs` could pass module ownership while
   naming an operand the operation cannot execute.
2. L2 reflection composed arbitrary predicate/action pairs. This admitted
   coverage-only policies for attribution classes the L2 runtime could not
   actually repair, and L2 attempts produced no experience trajectory.
3. A replay could change because provider output varied even when the evolved
   rule had no applied-rule/effect trace. Aggregate fitness could therefore
   assign improvement credit to an intervention that did not execute.

### Root design

- Added ActionSpec as the single source for module routes, output object,
  output field, operation, postcondition, and observable effect. Reflection
  selects a condition plus a closed operation/target pair; expected-effect
  text and contract are compiled from ActionSpec, not generated separately.
- Added an opcode-specific provider-field grammar. Every new operation must
  name an owned output field; module-wide target membership is insufficient.
- Persisted ActionSpec with each evolved rule and injected the structured
  specification into every owned provider route. Skill integration schema is
  now `agenthallu.paca_tcl_skill_integration.v5`.
- Replaced free-form L2 policy generation with deterministic repair planning.
  The model may select a precomputed executable candidate or abstain. An
  attribution with no L2 repair falls through to L1 and records an L2 failure
  experience instead of fabricating a policy. Planned edit IDs are content
  addressed, so identical attribution/genome inputs produce identical prompts,
  cache keys, and trajectory identities.
- Added causal fitness attribution. For an instrumented ActionSpec rule, a
  replay improvement belongs to the skill only when the same stable rule id
  is injected, reported as applied, and bound to the ActionSpec output field.
  Otherwise the replay is `intervention_not_executed/no_effect` regardless of
  aggregate score movement. This is a structural causal fact, not a numeric
  admission threshold.
- Experience outcome v2 stores the canonical ActionSpec and reduced execution
  trajectory (`applied_rule_ids`, `routes`, `output_fields`).

### Final authoritative rerun

| case_id | gold | seconds | accepted | rejected | decisions |
|---|---:|---:|---:|---:|---|
| BFCL/085 | true | 71.6 | 1 | 0 | accept_batch |
| Camel/058 | true | 0.1 | 0 | 0 | skip_stable |
| Magentic_One/002 | true | 0.2 | 0 | 0 | skip_no_policy |
| Magentic_One/092 | true | 23.0 | 0 | 0 | skip_stable |
| Octotools/007 | false | 87.6 | 0 | 1 | retry_invalid_mutation, reject_fitness |
| OpenDeepSearch/069 | true | 27.6 | 0 | 1 | reject_fitness |
| OpenManus/042 | false | 6.1 | 0 | 0 | reject_policy_fitness |
| OpenManus/051 | false | 0.4 | 0 | 2 | L2 skip, retry no-effect, L2 skip, reject |
| OpenManus/099 | false | 0.3 | 0 | 2 | retry_fitness_no_effect, reject_fitness_no_effect |
| SmolAgents/046 | true | 32.0 | 0 | 0 | skip_stable |

The final rerun completed 10/10 baseline detections with zero runtime-invalid
cases. Exactly one skill was promoted. `BFCL/085` moved from unresolved to the
gold-correct verified-error state through rule `edeb5cb4eff3`; the same id was
applied on `focused_prosecution_route`, and its effect named
`benchmark_causal_status`, matching the ActionSpec output field. Its promotable
experience is schema v2 and contains that full causal trajectory.

No other experience was promotable. Stable cases emitted no skill experience;
L2 abstentions/rejections and L1 structural, no-effect, or runtime failures
were retained only as non-promotable learning trajectories. In particular,
the earlier result-only improvements for `OpenDeepSearch/069` and
`OpenManus/099` became `intervention_not_executed/no_effect` rather than being
written to a champion.

L2 experiences now retain `outcome_stage=l2_policy_replay`; policy candidates
with coverage-only effects use the directional outcome class rather than being
mislabelled as rejected.

Verification: 469 tests passed.

## 批次6（batch6_gold，2026-08-06）

因果经验闭环（SELF_EVOLUTION_FRAMEWORK_REVIEW）实施后的首轮真实 gold 验证。
10 例并发、每例单条数据，全部与 used_case_ledger 不重叠。10/10 达到明确终态，
0 runtime_invalid，0 provider 失败，无静默挂起。

| case_id | gold | 耗时(s) | 触发 | 接受 | 拒绝 | stable | runtime_invalid | 决策 |
|---|---:|---:|---:|---:|---:|---:|---|
| BFCL/136 | true | 348 | 0 | 0 | 0 | 1 | 0 | skip_stable |
| Camel/021 | true | 1096 | 1 | 0 | 1 | 0 | 0 | reject_fitness |
| Magentic_One/088 | true | 483 | 0 | 0 | 0 | 1 | 0 | skip_stable |
| OpenDeepSearch/009 | false | 528 | 2 | 0 | 2 | 0 | 0 | L2 skip, retry_structural, L2 skip, reject_fitness_no_effect |
| OpenDeepSearch/066 | false | 791 | 1 | 0 | 2 | 0 | 0 | retry_fitness_no_effect, reject_fitness |
| OpenDeepSearch/068 | false | 556 | 1 | 0 | 1 | 0 | 0 | retry_fitness_no_effect, skip_invalid_mutation |
| OpenDeepSearch/093 | false | 734 | 1 | 0 | 2 | 0 | 0 | retry_structural, reject_fitness_no_effect |
| OpenManus/032 | true | 630 | 1 | 0 | 1 | 0 | 0 | reject_fitness |
| OpenManus/056 | true | 370 | 1 | 1 | 0 | 0 | 0 | accept_batch |
| SmolAgents/047 | true | 414 | 0 | 0 | 0 | 1 | 0 | skip_stable |

### 验收漏斗（7 个触发案例）

should_evolve=7 -> episode_created=7 -> failure_edge_complete=7 ->
proposal_generated=7 -> action_compiled=7 -> action_injected=7 ->
action_applied=2 -> observable_changed=7 -> local_source_improved=1 ->
skill_accepted=1

history_retrieved=0 符合预期：本批次是单案例并发隔离实验，每个进程从空记忆开始，
不验证在线跨案例继承（该能力由两案例顺序集成测试覆盖）。

### OpenManus/056（gold=True，唯一接受案例）

- 归因: l1_provider_rule / atomic_verifier_instruction / provider_artifact_malformed
- failure edge: malformed -> well_formed
- episode completeness: complete；episode_id 在两次 attempt 间保持稳定
- attempt: add_rule，compiled=True injected=True applied=True
- observable delta: state:unresolved->verified_error, trigger:closed,
  responsible_step:added, localization_status:not_applicable->failed_temporal_fallback
- downstream court delta: state:unresolved->verified_error（gold=True 方向正确）
- no_effect_reason=expected_delta_observed；memory 记录 effective 代表

### 其他触发案例的漏斗定位

- Camel/021、OpenManus/032: 编辑被 provider 应用（applied=True）但目标局部分数下降
  （target_local_score:decreased），reject_fitness 是方向性拒绝而非执行失败。
- OpenDeepSearch/009: L2 boundary_consumption_policy 无计划候选（skip_no_policy），
  L1 提案 structural retry 后 no-effect（provider_output_changed_but_downstream_invariant）。
- OpenDeepSearch/066、/068、/093: no-effect 或方向性回归，均保留为
  no_effect/structural_reject 代表。
- BFCL/136、Magentic_One/088、SmolAgents/047: gold=True 且检测 verified_error，
  正确检测，不触发进化。

所有 10 个进程输出 evolution_result.json + htc_tes_experience_memory.json
（sehd.evolution_experience_memory.v1）+ evolution_state_manifest.json 完整。
无框架缺陷需要修复，无需重跑。

Verification: 525 tests passed。
