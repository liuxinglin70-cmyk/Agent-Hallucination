# Real Eval v1 Protocol

This protocol replaces the historical A/B/C sequence for the next clean
experiment. There is no separate C stage.

## Boundary

`tools/build_real_eval_v1.py` freezes a new manifest under
`datasets/eval_splits/real_eval_v1/` (or a new versioned output directory):

- B development: 50 cases for bare-base patrol, proposal, and V1/V2/V3/M.
- B effect holdout: 20 cases never used by proposal or gate selection.
- Full holdout: the remaining 623 cases, read once only after B passes.
- Question groups are never split between B development, B effect holdout, and
  full holdout. Each raw case records a SHA-256 digest.

## B Gate

The B command runs at most two cycles and at most two proposer calls per cycle.
The committed registry is evaluated fresh against the disjoint 20-case effect
holdout. B passes only when all conditions hold:

- active registry is non-empty;
- fresh active minus fresh empty baseline gains at least one correct case;
- no new error and no added unresolved case;
- ledger verification succeeds.

If B fails, the full holdout must not be read. A timeout or incomplete cycle is
also a failure, not a reason to retry with more calls.

## Commands

```powershell
python tools/build_real_eval_v1.py
python -m pytest detector_v2/tests evolution_v3/tests tests -q
python -m evolution_v3 init --config evolution_v3/configs/framework_real_eval_v1_b.json --reset
python -m evolution_v3 experiment --cycles 2 --backend detector_v2 --mini --config evolution_v3/configs/framework_real_eval_v1_b.json
python -m evolution_v3 verify-ledger --config evolution_v3/configs/framework_real_eval_v1_b.json
```

Only after the B report says `mini_effect.passed=true` and the registry is
non-empty may the single full verification be started:

```powershell
python -m evolution_v3 final --config evolution_v3/configs/framework_real_eval_v1_full.json
```

`run_final()` uses fresh evaluation for both empty and active arms. It does not
use frozen-stage replay or cached baseline results.

## Current Attempt

The first clean B manifest (`real_eval_v1`, seed `20260820`) completed two
cycles but admitted no intervention; its disjoint effect holdout was `-1`
correct case, two new errors, and therefore failed.

The second clean B manifest (`real_eval_v2`, seed `20260821`) exposed and fixed
a replay bug: failed baseline provider stages were being frozen as if they were
valid results, producing artificial unresolved regressions. After the fix,
the run produced a partial first cycle but exceeded the bounded runtime before
the gate completed. It is recorded as failed/incomplete and did not authorize
the full holdout.

The third attempt re-ran the refactored code on `real_eval_v2` (seed
`20260821`). The replay fix held: both cycles completed to `state=completed`
within budget and the ledger verified clean (10 records). Each cycle proposed
two candidates from the same stable failure family
`FAM-fn-judge_false_no_recall_candidates` — an `evidence_check` on `judge.post`
and a `decision_guard` on `adjudicate.guard`. Every candidate was rejected at
V2 with a genuine sentinel harmlessness violation: the `decision_guard` flipped
clean cases to error and dropped ten cases to unresolved
(`new_fn=4, new_fp=2, new_unresolved=10`, `health_regressed=10`), so no
intervention was admitted and the active registry stayed empty.
`mini_effect.passed=false` (its reported `marginal_cases=1` is noise between two
fresh empty arms, `unresolved_added=1`). B did not pass and the full holdout was
not read.
