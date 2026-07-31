# AutoDL Gold-Free R0->R1 CoderForge 50 Pilot

Generated at: 2026-06-21T22:36:55+08:00

## Decision

This run is a gold-free skill-evolution pilot. It reports evidence-quality feedback and does not report final hallucination F1.

## Setup

- Source: `coderforge_verified_eval` / `openhands_coderforge`
- Split counts: `{'train': 30, 'dev': 10, 'test': 10}`
- Skill model: `qwen3.6-plus`
- Detector model: `qwen3.6-plus`
- Feedback model: `DeepSeek-V4-Pro`
- API concurrency: `15`

## Hard Checks

- R0 dev: `{'detections': 10, 'json_schema_valid': 10, 'evidence_ref_valid': 10, 'ordinary_failure_boundary_present': 10, 'outcome_as_gold_violations': 1, 'abstentions_or_uncertain': 2, 'examples': {'outcome_as_gold': [{'card_id': '07da1baa6c70d459331450b3', 'outcome_ids': [], 'outcome_terms': ['\\breward\\s*(?:=|:|is|of|metric|metrics)\\b']}]}}`
- R1 test: `{'detections': 9, 'json_schema_valid': 9, 'evidence_ref_valid': 9, 'ordinary_failure_boundary_present': 9, 'abstentions_or_uncertain': 2, 'outcome_as_gold_violations': 1, 'examples': {'outcome_as_gold': [{'card_id': '0dd88e7e882e0b285d8979aa', 'outcome_ids': [], 'outcome_terms': ['\\breward\\s*(?:=|:|is|of|metric|metrics)\\b']}]}}`

## Gold-Free Quality Summary

- R0 dev distribution: `{'detections': 10, 'cards_with_detection': 10, 'missing_card_ids': [], 'detected_type_distribution': {'completion_progress_claim': 3, 'insufficient_evidence': 2, 'patch_effect_behavior': 1, 'requirement_intent': 1, 'supported_or_clean': 3}, 'support_status_distribution': {'contradicted': 3, 'supported': 5, 'uncertain': 2}, 'has_hallucination_distribution': {'False': 8, 'True': 2}}`
- R1 test distribution: `{'detections': 9, 'cards_with_detection': 9, 'missing_card_ids': ['11047caf0280813e4e94e815'], 'detected_type_distribution': {'completion_progress_claim': 2, 'insufficient_evidence': 2, 'requirement_intent': 1, 'supported_or_clean': 4}, 'support_status_distribution': {'contradicted': 2, 'supported': 5, 'uncertain': 2}, 'has_hallucination_distribution': {'False': 7, 'True': 2}}`
- R0->R1 revision summary: `{'repairs': ['Strengthened abstain_conditions for requirement_intent to explicitly require test coverage verification before asserting supported status.', 'Updated completion_progress_claim ordinary_failure_separation to explicitly forbid relying on reward=1.0 without unit-level verification.', 'Refined supported_or_clean gold_free_rule to strictly prohibit outcome-as-gold labeling and mandate independent unit verification.', 'Clarified insufficient_evidence boundary to trigger on truncated verification logs, preventing false positives/negatives.'], 'regression_risks': ['Over-abstention on cases where test coverage is implicit but not explicitly logged.', 'Strict evidence requirements may reduce detection rate for subtle hallucinations in highly truncated trajectories.'], 'feedback_used': ['Consider including explicit checks for test coverage when asserting supported status for requirement_intent.', 'Ensure that future cases with ambiguous problem statements are properly abstained.', 'The overall detection approach for truncated evidence is sound. No false positive or negative hallucination labeling was produced.', 'The completion_progress_claim skill effectively uses test output to verify agent claims. It correctly distinguishes between a failed buggy attempt (ordinary failure) and an explicit unsupported/contradicted success claim (hallucination).']}`

## Artifacts

- `manifest`: `data/gold_free_r0_r1_coderforge_50/pilot_manifest.json`
- `skill_r0`: `outputs/gold_free_r0_r1_coderforge_50/skill_r0.json`
- `r0_dev_detections`: `outputs/gold_free_r0_r1_coderforge_50/r0_dev_detections.jsonl`
- `r0_dev_feedback`: `outputs/gold_free_r0_r1_coderforge_50/r0_dev_feedback.jsonl`
- `skill_r1`: `outputs/gold_free_r0_r1_coderforge_50/skill_r1.json`
- `r1_test_detections`: `outputs/gold_free_r0_r1_coderforge_50/r1_test_detections.jsonl`
- `metrics`: `results/gold_free_r0_r1_coderforge_50_metrics.json`
- `report`: `results/gold_free_r0_r1_coderforge_50_report.md`

## Boundary

- Judge feedback is reverse audit of detector outputs, not ground-truth label generation.
- `reward`, `resolved`, `test_output`, and evaluator logs are evidence fields, not labels.
- Test split is used only after `skill_r1` is frozen.
