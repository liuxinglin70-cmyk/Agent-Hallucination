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

## Clean B Commands

A repaired implementation must never continue from the invalid attempt's work
root. Remove the whole B work root so cache, registry, ledger, reports and audit
verdicts are reset together. The detector cache namespace is currently
`v10-mechanism-bound-cache`; seeing a v9 namespace means the run is invalid.

```powershell
Remove-Item -Recurse -Force evolution_v3/work/real_eval_v2_b
python tools/build_real_eval_v1.py --check --seed 20260821 --output-dir datasets/eval_splits/real_eval_v2
python -m pytest detector_v2/tests evolution_v3/tests tests -q
python -m evolution_v3 init --config evolution_v3/configs/framework_real_eval_v2_b.json --reset
$env:EVO3_PROGRESS = "1"
python -m evolution_v3 experiment --cycles 2 --backend detector_v2 --mini --config evolution_v3/configs/framework_real_eval_v2_b.json
python -m evolution_v3 verify-ledger --config evolution_v3/configs/framework_real_eval_v2_b.json
```

Only after the B report says `mini_effect.passed=true` and the registry is
non-empty may the single full verification be started:

```powershell
python -m evolution_v3 final --config evolution_v3/configs/framework_real_eval_v2_full.json
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
`20260821`). Both cycles completed and the ledger verified clean (10 records),
but a post-run architecture audit found that the V2 result was not a valid
proposal-quality measurement. Frozen replay wrote short envelope names
(`tracer`, `recall`, `upgrade`) back through canonical detector status names
(`step_tracer`, `recall_scanner`, `causal_upgrade`) incorrectly. Successful
frozen results therefore carried empty status objects, failed authority
validation, and became artificial unresolved rows. The reported
`new_fn=4, new_unresolved=10` is contaminated by that execution defect.

The same audit also found independent proposal-mechanism defects: an outcome
path label was treated as a causal failure family, the proposer received only
aggregate counts, a diagnostic-only anchor mismatch could authorize a terminal
conviction, and rejected mechanisms were not fed into the next cycle. The two
observed new false positives remain evidence that the proposed guard was
unsafe, but the run cannot rank the proposer or estimate its net effect. It is
recorded as failed/invalid, the full holdout was not read, and no threshold was
changed in response.

The framework was subsequently repaired at the architecture boundary: replay
uses canonical stage/status mappings; mechanical convictions require a
conviction-authorized predicate and runtime evidence from the same failure
family; families are refined by stage/contract and per-member deterministic
predicate profiles; the proposer receives de-identified member summaries; and
gate rejections persist in the ledger and block unchanged proposals across
cycles and process restarts. These changes require a new clean B run and must
not reuse the contaminated V2 cache.

The fourth attempt re-ran clean (whole work root deleted, v10 cache namespace)
and completed both cycles with the ledger valid, but the mechanism signature
still over-split: contract kinds were part of the family key, so five
patrol FN cases with different contract profiles degenerated into five
singleton families and the proposer never ran. The family key was then
restricted to the execution path (judge polarity, recall path, stage states,
defense/positive source) while contract kinds stayed in per-member summaries;
`refine_failure_families` now keeps a heterogeneous family instead of dropping
it when no predicate subgroup reaches the stability threshold. A clean B must
be re-run after that change.
