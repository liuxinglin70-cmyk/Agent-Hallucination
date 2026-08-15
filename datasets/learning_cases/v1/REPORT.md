# Phase 0 Learning Case Dataset — Capability Gap Report

- generated: 2026-08-15T11:09:58.832383+00:00
- total learning cases: 100
- taxonomy frozen: True
- human stage overrides: 18

## Failure type counts

| failure_type | count |
|---|---|
| CORRECT | 51 |
| FN | 12 |
| FP | 12 |
| LOCALIZATION | 25 |

## Failed stage by failure type

- CORRECT / none: 51
- FN / census: 6
- FN / defense: 2
- FN / verification: 4
- FP / defense: 1
- FP / verification: 11
- LOCALIZATION / adjudication: 25

## Capability gap distribution (annotated failures)

- attribution/locus_vs_manifestation: 22
- policy/strictness_miscalibration: 8
- observation/unsupported_claim_grounding: 4
- attribution/tie_ambiguity: 3
- policy/expression_tolerance: 3
- observation/missing_required_action: 2
- policy/adversarial_defense_overturn: 2
- verification/excuse_based_rejection: 1
- verification/source_credibility_conflict: 1
- verification/unverified_derivation: 1
- verification/exactness_boundary: 1
- policy/evidence_weighting: 1
