# Phase 1-D Fresh Evaluation Report

- frozen set: 100 cases (runs/p1d_frozen100_20260816_015857)
- canary set: 14 gold-error sentinels (runs/p1d_canary_20260816_014553)

## Overall improvement (baseline vs deployed chain)

| chain | TP | FP | FN | TN | accuracy | TP lost | FP fixed |
|---|---|---|---|---|---|---|---|
| baseline (frozen detector) | 35 | 21 | 15 | 29 | 64.0% | — | — |
| EvoGuard (binder + genome) | 35 | 21 | 15 | 29 | 64.0% | 0 | 1 |

## Policy contribution

| policy | class | triggers | correct | false activations | missed |
|---|---|---|---|---|---|
| L2-P1 | requirement_compliance | 3 | 2 | 1 | 1 |
| L2-P2 | presentational_addition | 1 | 0 | 0 | 0 |
| L2-P3 | expression_variance | 0 | 0 | 0 | 0 |
| L2-P4 | premise_conflict | 0 | 0 | 0 | 0 |
| L2-P5 | absence_based | 0 | 0 | 0 | 0 |

### Trigger detail

- L2-P1 fired on: Camel/062.json, Camel/080.json, Octotools/024.json
  - correct improvements: Camel/062.json, Camel/080.json
  - FALSE activations (TP lost): Octotools/024.json
- L2-P2 fired on: BFCL/019.json

## Canary regression gate

- verdict: **PASSED**
- BFCL/025.json: baseline verified_error -> deployed verified_error (survived)
- BFCL/027.json: baseline verified_error -> deployed verified_error (survived)
- BFCL/067.json: baseline verified_error -> deployed verified_error (survived)
- BFCL/071.json: baseline verified_clean -> deployed verified_clean (baseline_fn)
- BFCL/104.json: baseline verified_clean -> deployed verified_clean (baseline_fn)
- BFCL/159.json: baseline verified_clean -> deployed verified_clean (baseline_fn)
- Camel/039.json: baseline verified_error -> deployed verified_error (survived)
- Camel/053.json: baseline verified_clean -> deployed verified_clean (baseline_fn)
- Camel/059.json: baseline verified_error -> deployed verified_error (survived)
- Camel/060.json: baseline verified_error -> deployed verified_error (survived)
- OpenDeepSearch/007.json: baseline verified_error -> deployed verified_error (survived)
- OpenManus/060.json: baseline verified_error -> deployed verified_error (survived)
- OpenManus/067.json: baseline verified_error -> deployed verified_error (survived)
- OpenManus/072.json: baseline verified_error -> deployed verified_error (survived)

## Evolution trajectory

| stage | accuracy |
|---|---|
| evolution pool baseline | 76.0% |
| oracle policy replay | 83.0% |
| runtime chain (calibrated) | 78.0% |
| fresh baseline | 64.0% |
| fresh deployed chain | 64.0% |

## Promotion gate

- PASS canary_zero_known_regression
- PASS frozen_zero_tp_loss
- FAIL frozen_net_fp_improvement
- PASS frozen_accuracy_not_below_baseline

**Verdict: genome NOT promoted** on fresh-set evidence.
