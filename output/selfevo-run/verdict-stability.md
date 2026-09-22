# A verdict is not stable across two identical runs

Found while checking whether the envelope fix "improved accuracy". It did not
have to, and the check is the reason to trust the run that follows.

## The observation

Attempts 2 and 3 evaluated the same 18 cases with the same detector config, the
same empty registry, and the same data. Only `evolution/*.py` differed (the
audit-freeze fix), which touches no detection code. Re-running the same case
gave the same verdict 17 times out of 18:

| case | attempt 2 | attempt 3 | gold | |
|---|---|---|---|---|
| Camel/048.json | True | False | False | **differs** (attempt 3 right) |
| *(17 others)* | | | | identical |

So the detector is *mostly* stable but not deterministic. That is expected:
`temperature` is 0.0, but the provider is a reasoning model whose deliberation
is sampled, and the probe measured one identical prompt returning 36 and 13,437
reasoning tokens on different calls.

## Where it bites: BFCL/038

Between attempt 3 and attempt 4 the same case moved from `verified_error` to
`verified_clean`. It flips into the *correct* answer (gold = clean), so the
tempting story is "the wider envelope let the detector think properly". The
cached provider records show that story is false:

| | attempt 3 | attempt 4 |
|---|---|---|
| judge ballots | 2/3 positive | **0/3 positive** |
| judge total completion tokens | 12,401 | 10,083 |
| judge verdict | `is_hallucination: true`, step 3, **high** confidence | `is_hallucination: false`, **medium** confidence |
| decisive path | `positive_source: judge` → defense `substantiated` | `positive_source: recall_upgrade` → defense `refuted` |

The judge changed its mind. Attempt 3's judge named step 3 and called it
high-confidence; attempt 4's judge saw no error at all, and then the recall
scanner raised a candidate that the defense refuted. Nothing about the
timeout made the judge more careful — the same three ballots simply landed
differently. The longer wall clock (3732s vs 1013s) is a *consequence* of that
deliberation being longer, not a cause of the answer.

This means **the flip is provider noise, and it is not evidence for the
envelope fix.** Reporting it as an accuracy improvement would be exactly the
kind of unearned claim this repo's invariants exist to prevent.

## What the envelope fix *is* entitled to claim

A separate, cleanly attributable result — the pair that broke attempt 3:

| case | attempt 3 (old envelope) | attempt 4 (new envelope) |
|---|---|---|
| Octotools/008.json | 3600.0s, cut at the case deadline, `degraded_judge_only`, prediction **None**, token budget burned | 3594.1s, `authoritative`, `verified_error`, prediction **True** = gold |

The old run's own terminal basis names the failure (`case_deadline_exceeded`,
six defense attempts, one judge vote lost to
`TimeoutError: isolated transport exceeded request deadline`). The new run
reached a decision on the same case. That is a claim about *whether a
measurement can complete*, which is what an envelope change affects — and the
retry counters say the same thing independently: attempts that returned no
ballot fell from 21.1% of all attempts (judge 44 of 203, defense 17 of 86) to
3.4% (judge 14 of 482, defense 9 of 216), measured over each run's full cache.
They did not fall to zero — the ceiling reduced the churn, it did not remove
the failure mode.

## Why this matters for the coming verdict

If a single case's verdict can move between runs, a one-case marginal gain is
not a measurement. The framework already anticipates this, and the mechanisms
are worth naming because they are what make a positive result credible:

1. **Paired replay.** `LiveDetectorBackend` captures the empty-registry result
   once and replays its settled stages for the candidate arm, so the two arms
   do not pay this noise twice. `M` compares arms on the same frozen roll.
2. **M requires `m_min_marginal_cases >= 1` with `m_new_errors_allowed = 0`** —
   a gain that costs a new error is refused, which is the exact shape noise
   produces.
3. **HARMLESSNESS** requires zero new FN, FP and *unresolved* on the protected
   set, so a flipped protection case blocks admission rather than being
   averaged away.
4. **`baseline_roll` pinning** exists so a run can declare and re-verify which
   control roll it replayed, rather than accepting whatever roll arrived.

The honest reading to carry forward: **a marginal-gain reading of 1 case on
this corpus is within run-to-run noise unless the paired replay held the
control fixed and the gain is not accompanied by a new error.** Any future
claim of self-evolution should be checked against that, including mine.
