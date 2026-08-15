# Phase 1-D Frozen Evaluation Split (v1)

- created: 2026-08-15T17:44:55.069568+00:00
- seed: 20260816
- evolution pool: 100 cases (run random100_20260815_142446)
- contamination boundary: 109 cases across all recorded runs
- fresh universe: 584 cases
- frozen evaluation set: 100 cases (50 gold-error / 50 gold-clean)

## Frozen set by framework

| framework | seats |
|---|---|
| BFCL | 24 |
| Camel | 13 |
| Magentic_One | 14 |
| Octotools | 7 |
| OpenDeepSearch | 16 |
| OpenManus | 14 |
| SmolAgents | 12 |

## Canary set (deployment-form regression gate)

| case | gate | declared by |
|---|---|---|
| BFCL/025.json | must_stay_convicted | L2-P3:do_not_apply; L2-P3:tp_must_survive
| BFCL/027.json | must_stay_convicted | L2-P1:do_not_apply; L2-P1:fn_risk
| BFCL/067.json | must_stay_convicted | L2-P1:do_not_apply; L2-P1:fn_risk
| BFCL/071.json | must_stay_convicted | L2-P1:do_not_apply; L2-P1:fn_risk
| BFCL/104.json | must_stay_convicted | L2-P2:do_not_apply; L2-P2:fn_risk; L2-P5:do_not_apply
| BFCL/159.json | must_stay_convicted | L2-P3:do_not_apply; L2-P3:fn_risk
| Camel/039.json | must_stay_convicted | L2-P1:tp_must_survive; L2-P4:do_not_apply; L2-P4:tp_must_survive; L2-P5:do_not_apply; L2-P5:tp_must_survive
| Camel/053.json | must_stay_convicted | L2-P4:do_not_apply; L2-P4:fn_risk
| Camel/059.json | must_stay_convicted | L2-P4:do_not_apply
| Camel/060.json | must_stay_convicted | L2-P4:do_not_apply
| OpenDeepSearch/007.json | must_stay_convicted | L2-P2:do_not_apply; L2-P2:tp_must_survive; L2-P3:do_not_apply
| OpenManus/048.json | monitor_only | L2-P1:do_not_apply; L2-P1:fn_risk; NS-1:non_separable
| OpenManus/060.json | must_stay_convicted | L2-P2:do_not_apply; L2-P2:fn_risk; L2-P5:do_not_apply
| OpenManus/067.json | must_stay_convicted | L2-P1:do_not_apply; L2-P1:fn_risk; NS-1:non_separable
| OpenManus/072.json | must_stay_convicted | L2-P1:do_not_apply; NS-1:non_separable

## Deployment form

- detector routes: census=deepseek-v4-flash, verification/defense=deepseek-v4-pro (config fingerprint 76d6fd60b069420c),
- binder: deepseek-v4-flash with the calibrated discriminative class definitions and structured abstention,
- policy genome: L2-P2, L2-P3, L2-P4, L2-P5.
