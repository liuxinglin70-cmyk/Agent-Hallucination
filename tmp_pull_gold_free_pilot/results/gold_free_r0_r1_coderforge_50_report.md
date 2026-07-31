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

- R0 dev: `{'detections': 10, 'json_schema_valid': 10, 'evidence_ref_valid': 10, 'ordinary_failure_boundary_present': 10, 'outcome_as_gold_violations': 1, 'abstentions_or_uncertain': 2, 'examples': {'outcome_as_gold': [{'card_id': '07da1baa6c70d459331450b3', 'outcome_ids': []}]}}`
- R1 test: `{'detections': 9, 'json_schema_valid': 9, 'evidence_ref_valid': 9, 'outcome_as_gold_violations': 2, 'ordinary_failure_boundary_present': 9, 'abstentions_or_uncertain': 2, 'examples': {'outcome_as_gold': [{'card_id': '00dfaab630914e59ad4c0095', 'outcome_ids': []}, {'card_id': '0dd88e7e882e0b285d8979aa', 'outcome_ids': []}]}}`

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
