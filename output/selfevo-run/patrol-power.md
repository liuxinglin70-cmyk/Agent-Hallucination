# Power of the frozen split's patrol wave

Written before the decisive run, from the partial attempt-3 rows. Its purpose is
to make a "no family" outcome interpretable: without it, a null result is
ambiguous between "the mechanism has no failure family to fix" and "the sample
was too small to see one".

## The quantity that matters

`stable_failure_families` keeps only groups of `min_cases` (2) or more that
share a *mechanism signature* -- the same execution path, not merely the same
outcome kind. So the wave needs **two errors of the same signature**, and the
relevant rate is the per-case rate of the *most common* signature, not the
overall error rate.

## Measured rate

Attempt 3 evaluated 18 cases (12 sentinel + 8 gain minus 2 not yet landed).
Four rows were not correct:

| case | gold | pred | family |
|---|---|---|---|
| BFCL/038.json | False | True | `FAM-fp-judge_conviction` |
| BFCL/101.json | True | False | `FAM-fn-judge_false_no_recall_candidates` |
| OpenDeepSearch/045.json | True | False | `FAM-fn-judge_false_no_recall_candidates` |
| Octotools/008.json | True | None | `FAM-unresolved` |

Two of the nine gold-positive cases share one signature, so the rate for that
signature among positives is `p = 2/9 = 0.222`.

This is a pool-level rate. The patrol wave draws from the same pool, and the
pool was built stratified at 29 gold-true / 15 gold-false.

## The sample the patrol wave actually reaches

`patrol_pool` is what remains after the sentinel (6 pos / 6 neg) and the gain
(5 pos / 3 neg) are drawn:

```
patrol_pool = (29 - 11) pos + (15 - 9) neg = 18 pos + 6 neg = 24 cases
```

The initial sweep takes `patrol_cases = 20`. If no family forms, the bootstrap
adds the rest (`stable_failure_rerun_cases = 20` but only 4 cases remain), so
the wave covers **all 24 patrol-pool cases, of which 18 are gold-positive**.
The bootstrap is already maximal; raising its budget alone would buy nothing.

## Power

`P(>= 2 members) = 1 - (1-p)^n - n*p*(1-p)^(n-1)` with `p = 0.222`, `n = 18`:

```
(1-p)^n        = 0.778^18 = 0.0114
n*p*(1-p)^(n-1)= 3.996 * 0.778^17 = 0.0579
P(>=2)         = 1 - 0.0114 - 0.0579 = 0.931
```

**~93% power.** The frozen split can detect the family it needs to detect, so
there is no case for enlarging the pool, and none was made. The split manifest
is unchanged from the one attempt 1 was launched with.

The estimate rests on `p` from nine positive cases, so the honest reading is
"the wave is well powered for a family at roughly a fifth of positive cases,
and less powered for a rarer signature". A rarer family would need a larger
pool, which is a reason to run more cycles, not to edit the split now.

## What this does not license

A family forming is step one of five. The candidate still has to be proposed,
pass STRUCTURAL, HARMLESSNESS, BEHAVIOR and M, and then show a marginal gain on
the validation set (`mini_effect.min_marginal_cases >= 1`). Nothing in this
document makes any of that more likely, and no threshold was touched to write
it.
