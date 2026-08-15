# Phase 1-B Offline Policy Replay Report

- cases: 100
- schema: sehd2.policy_replay.v1

## Certificate class exposure (surviving certificates)

| class | gold-error cases | gold-clean cases |
|---|---|---|
| content_contradiction | 142 | 0 |
| requirement_compliance | 3 | 5 |
| delivery_form | 2 | 1 |
| presentational_addition | 0 | 3 |
| expression_variance | 0 | 2 |
| premise_conflict | 0 | 2 |
| absence_based | 0 | 2 |
| unclassified | 0 | 0 |

## Main configurations (baseline / singles / full set)

| configuration | TP | FP | FN | TN | accuracy | TP lost | FP fixed | changes | must-survive violations |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 50 | 12 | 12 | 26 | 76.0% | 0 | 0 | 0 | 0 |
| L2-P1 | 50 | 9 | 12 | 29 | 79.0% | 0 | 3 | 3 | 0 |
| L2-P2 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P3 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P4 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P5 | 50 | 11 | 12 | 27 | 77.0% | 0 | 1 | 1 | 0 |
| full_set | 50 | 1 | 12 | 37 | 87.0% | 0 | 11 | 11 | 0 |

### Single-policy detail

| policy | TP lost | FP fixed | firing cases |
|---|---|---|---|
| L2-P1 | 0 | 3 | 4 |
| L2-P2 | 0 | 2 | 2 |
| L2-P3 | 0 | 2 | 2 |
| L2-P4 | 0 | 2 | 2 |
| L2-P5 | 0 | 1 | 2 |

## Interpretation anchors

- best subset by accuracy: L2-P1+L2-P2+L2-P3+L2-P4 (85.0%, TP lost 0)
- best subset with zero TP loss: L2-P1+L2-P2+L2-P3+L2-P4 (85.0%, FP 3)
- full set: 87.0%
- promotion gate (review): accuracy >= baseline, FP clearly reduced, FN increase <= 1, no must-survive violations

