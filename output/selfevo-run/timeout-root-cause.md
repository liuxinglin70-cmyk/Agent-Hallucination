# Case-timeout variance: root cause and fix

Requested action: "the case time-limit problem needs real analysis — find what
causes it and solve it; in theory there should not be this much variance, and
tokens should not be heavily restricted."

This document answers that with measurement. The short version: **the variance
was not the network and not the trajectory size. It was the per-attempt
timeout cutting working calls in half and paying for them twice.** One of the
18 measured cases was killed at the 3600s case deadline with its whole token
budget spent and nothing to show for it, and a second finished 0.14 seconds
past that same deadline — the envelope was one case away from cutting two.

## 1. What was actually observed

Across attempt 3 (18 landed cases, 88 provider calls), seconds-per-call ranged
from 11.8s to 400.0s — a 34x spread on one model, one endpoint, one config.

| case | elapsed | calls | s/call | last-ballot tokens | state |
|---|---|---|---|---|---|
| SmolAgents/069.json | 38.4 | 3 | 12.8 | 1,580 | verified_clean |
| OpenDeepSearch/045.json | 93.2 | 3 | 31.1 | 1,529 | verified_clean |
| BFCL/027.json | 316.1 | 7 | 45.2 | 5,109 | verified_error |
| OpenManus/006.json | 904.0 | 8 | 113.0 | 11,255 | verified_error |
| BFCL/038.json | 1013.0 | 8 | 126.6 | 20,230 | verified_error |
| Camel/048.json | 3600.1 | 9 | 400.0 | 22,701 | verified_clean (authoritative) |
| Octotools/008.json | 3600.0 | 4 | — | — | **degraded_judge_only (CUT)** |

Exactly one row was cut, and its terminal basis names the cause directly:
`case_deadline_exceeded`, six defense attempts, one judge vote lost to
`TimeoutError: isolated transport exceeded request deadline`. Camel/048
completed with an authoritative verdict at 3600.139s — 139ms past the deadline,
because the deadline is only checked *before* each call, so a call that starts
in time may finish after it. That row was not cut, but it was inside the
margin by a tenth of a second.

## 2. The cause

Two probes, both single calls at **zero concurrency**, on the real route.

### 2.1 The model's deliberation length is stochastic and heavy-tailed

`timeout_probe.py` sent the *same* prompt twice:

| call | completion tokens | reasoning tokens | elapsed | attempts |
|---|---|---|---|---|
| 1 | 802 | 507 | 23.4s | 1 |
| 2 | 11,365 | 10,902 | **546.5s** | **2** |

Same words in, 14x the tokens out. Across the run's cache the reasoning share
of completion tokens is 90–98% for every stage, and the full distribution is:

| stage | n | min | median | p90 | max |
|---|---|---|---|---|---|
| judge | 35 | 70 | 2,019 | 8,458 | 13,437 |
| defense | 23 | 33 | 3,479 | 8,217 | 10,496 |
| step_tracer | 23 | 25 | 1,286 | 4,544 | 5,608 |
| recall_scanner | 4 | 91 | 4,776 | 11,372 | 11,372 |
| causal_upgrade | 4 | 1,918 | 5,883 | 9,462 | 9,462 |

Throughput is a steady 20–46 tok/s. So the implied wall clock for one call is
**median ~106s, p90 ~411s, max ~672s** — and the old per-attempt timeout was
**300s**. Between a fifth and a third of legitimate calls could not finish in
one attempt.

### 2.2 What happens to a call that exceeds the cap

It is killed mid-deliberation and retried from scratch, and **the first
attempt's tokens are gone**. Attempt 2's cache shows the failure directly:
six warnings of `provider returned non-JSON body: b'<!DOCTYPE html>...'` and
`502 Bad Gateway` — the gateway itself failing under long, retried calls.

Across the cache, attempts that consumed their budget without returning a
ballot — counted from `stage_status.attempts` (the sum over ballots against
`vote.cast`), which is the only field that carries the total:

| stage | configured ballots | attempts with no ballot | share of attempts |
|---|---|---|---|
| judge | 3 (+2 topup) | **44 of 203** | 21.7% |
| defense | 3 (+2 topup) | **17 of 86** | 19.8% |
| causal_upgrade | 1 | **12 of 27** | 44.4% |
| **all stages** | | **73 of 346** | **21.1%** |
| all stages, after the ceiling raise (attempt 4) | | 31 of 901 | **3.4%** |

Read these as corroboration, not as a timeout count: one attempt can return no
ballot because it was killed, because the transport failed, or because the
gateway returned an error page. What the table establishes is that a large share
of calls were not producing ballots under the 300s ceiling, and that raising it
cut that share by roughly six-fold — consistent with the probe evidence above,
which isolates the cause directly.

`Camel/048` shows the amplification plainly: its `stage_status` reports judge
`attempts: 5`, recall `attempts: 1`, upgrade `attempts: 3` — nine calls for a
stage budget of five.

### 2.3 Why this produced the *variance* the user noticed

Retry escalation is not a fixed cost; it is a coin flip per call:

- A call that finishes inside 300s costs its own duration (12–90s typical).
- A call that crosses 300s costs 300s **plus** a fresh attempt of up to 900s.

With a heavy-tailed token distribution, whether a given case pays that second
attempt is essentially random — which is exactly the 34x spread. The variance
was a property of the *envelope*, not of the task.

## 3. Why the case deadline made it worse rather than bounding it

`case_timeout_seconds` is a hard cap on the whole case, and the check runs
before each call: `total_timeout = min(total_timeout, remaining_case)`. So
deadline pressure does not cleanly abort — it *shrinks later calls' budgets*.
The judge votes consume the budget, then the remaining call ceiling collapses,
and calls that would have succeeded are cut for lack of remaining time. That is
the same "case deadline starves the later stages" shape already documented in
`tests/test_repository.py` from the 2026-09-18 SmolAgents/019 measurement.

## 4. Why it matters beyond slowness

A cut is not a neutral "slow". It has three consequences:

1. **The tokens are spent and thrown away.** Octotools/008 burned its entire
   budget and returned `degraded_judge_only` — non-authoritative, so the row
   cannot be scored, and it is the only case in the sample that happened to.
2. **It can land on one arm only.** The two arms run at different times, so a
   cut can hit the candidate arm and not the control. `HARMLESSNESS` has
   `new_unresolved_allowed = 0`, so an envelope artifact then reads as *a
   regression the candidate caused*.
3. **It ends the experiment.** A cycle that does not reach `completed` aborts
   `run_experiment` (`if result.get("state") != "completed": break`). Attempt 1
   died this way, and attempt 2 died because one cut sentinel row made the
   defect-injection audit inconclusive.

So this was not a performance problem. It was a **measurement-validity**
problem: it can manufacture a failure verdict that the mechanism did not earn.

## 5. What was rejected

Disabling thinking is the obvious way to make calls fast. The probe tested five
thinking controls on one prompt, and all were accepted by the gateway:

| variant | elapsed | completion tokens | reasoning | verdict (case is a real error) |
|---|---|---|---|---|
| baseline (thinking on) | 78.3s | 2,655 | 2,604 | **error** |
| `thinking: disabled` | 2.1s | 79 | 0 | clean |
| `enable_thinking: false` | 6.9s | 55 | 0 | clean |
| `reasoning_effort: none` | 14.4s | 43 | 0 | clean |
| `reasoning_effort: low` | 9.9s | 90 | 16 | **error** |

The fast path is 40x quicker and **also changes the answer** — it clears a case
the reasoning path convicts. Three of the four speedups invert the verdict.
That is not a cheaper detector; it is a different, worse one, and adopting it to
buy speed would be the same error as relaxing a gate. **Not adopted.** The
config's own `test_shipped_routes_use_supported_thinking_levels` already
records that GLM-5.3-flash always reasons and must not be told otherwise.

Also rejected: adding `max_tokens` to the judge route. I added it, then
reverted it. Observed single-call completions reach 14,090 tokens with a
heavy-tailed distribution, so a ceiling would introduce a truncation mode the
route does not have today, and a truncated reply is a provider error that
resolves the case to unresolved. A test now pins `max_tokens` **out** of the
judge route with that reasoning.

## 6. The fix

Three runtime-envelope values, in `detector/configs/autodl.json`:

| value | before | after | why |
|---|---|---|---|
| `provider.timeout_seconds` | 300 | **900** | above the measured 672s worst case, so a deliberate call finishes in one attempt |
| `provider.total_timeout_seconds` | 900 | **2400** | room for a genuine first-attempt failure plus a retry, without retry-churn |
| `case_timeout_seconds` | 3600 | **10800** | ~10 serial stages at the new ceilings; must fit the corpus, not truncate it |

And one in `evolution/configs/validation_big_run.json`:

| value | before | after | why |
|---|---|---|---|
| `budget.cycle_timeout_seconds` | 18000 (5h) | **43200 (12h)** | a case may now take up to 3h and a cycle is ~10 serial case evaluations; a 5h cap would fail-close the tail of every cycle to `unresolved`, which reports as *unmeasured* rather than as a mechanism failure |

**No token restriction was introduced**, per the request. Token ceilings were
raised where they existed and deliberately not imposed where they did not.

### These are not gate changes

Every value above is a **runtime envelope**. None of them is an admission
threshold. This run's `gates` block is bit-identical to the one attempt 1 used:

```
structural_jaccard_threshold 0.72   harmlessness_new_fn_allowed 0
harmlessness_new_fp_allowed 0       harmlessness_new_unresolved_allowed 0
harmlessness_min_protected_tp 2     harmlessness_min_protected_tn 2
behavior_require_fired true         m_min_marginal_cases 1
m_new_errors_allowed 0              localization_min_joint_cases 1
localization_joint_broken_allowed 0
```

The split manifest (`output/selfevo-run/split-big.json`) is unchanged. No case
set was edited, no threshold moved, and nothing was swapped after seeing output.
The distinction matters: raising a timeout changes *whether a measurement can
complete*; raising a threshold changes *what verdict a completed measurement
earns*. Only the first was done.

## 7. A second defect found on the way: tokens were undercounted

A voting stage returns its **last ballot's** provider meta as the stage's
sample (`sample_meta = dict(meta or {})` on each ballot). So `usage` recorded
one ballot of three, while judge and defense each carried 3 ballots plus up to
2 topups. The "120,331 completion tokens" figure for attempt 3's 18 rows was a
floor, not the burn — which is precisely the number a run's cost has to be
sized from.

Fixed in `detector/provider.py`: `accumulate_usage()` folds every ballot's
usage into a new `usage_total` key **beside** the existing `usage`, which is
left intact for current readers. It sums numeric leaves by path rather than
carrying a field whitelist, so a gateway that starts reporting a new counter is
totalled without a code change, and it skips booleans (`bool` is an `int` in
Python, and summing a cache-hit flag would be nonsense). Six tests in
`detector/tests/test_usage_accumulation.py`.

## 8. Guards

The envelope cannot silently regress:

- `tests/test_repository.py::test_shipped_case_budget_fits_a_measured_long_trajectory`
  now pins `case_timeout_seconds >= 10800`, `timeout_seconds >= 900`,
  `total_timeout_seconds >= 2400`, and pins `max_tokens` **out** of the judge
  route — with the measurement written into the comment.
- `tests/test_run_envelope_consistency.py` (new) pins the *ratio*: a cycle
  ceiling must exceed three case ceilings, a per-attempt ceiling must clear the
  measured 672s worst case, and a case ceiling must cover at least three calls.
  This is the specific confusion that would otherwise convert a working cycle
  into a fail-closed one.

## 9. Honest limits

- The prompt-sized probe (170 prompt tokens) isolates deliberation cost, not
  prompt size. A 19k-token judge prompt adds prefill time, which this document
  does not separate out. The judge's own measured calls (median 2,019
  completion tokens, up to 14,090) already clear the old cap, so the conclusion
  does not depend on that split.
- The 672s worst case comes from observed tokens at observed throughput. The
  true tail is longer: it is inferred from retried calls whose full length was
  never seen, because the cap destroyed the evidence. 900s is a floor for a
  fix, not a proven bound.
- A node whose calls all run long can still exceed 10800s in a cycle. That is
  why the cycle ceiling was raised with it, and why a cut row is classified
  `unresolved` — *unmeasured*, never a candidate-caused failure.
