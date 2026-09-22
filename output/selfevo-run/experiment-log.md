# Self-evolution framework validation — experiment log

Consolidated record of the validation effort on 2026-09-21/22. Five runs of the
same protocol, three framework defects found and fixed, two 3-cycle runs
completed to a gate verdict. **No new experiment is planned.**

Protocol under test: `evolution/configs/validation_big_run.json`, split
`output/selfevo-run/split-big.json` (pool 44 = 29 gold-positive / 15
gold-negative across all 7 sources; patrol pool 24; sentinel 6+6; gain 8;
validation 12, disjoint from the pool). Detector config
`detector/configs/autodl.json`.

**Final result: the framework validated; the self-evolution effect did not.**

---

## 1. The five runs

| # | work root | wall clock | outcome | what it established |
|---|---|---|---|---|
| 1 | `attempt1-sizing/` | 46 min, stopped at 10 rows | stopped by operator | 4h cycle cap too small for measured latency; a non-completed cycle aborts the whole run |
| 2 | `attempt2-frozen/` | 120 min | **frozen after cycle 1** | the defect-injection audit refused to certify because one case hit its deadline → framework defect #1 |
| 3 | `attempt3-deadline-cuts/` | 89 min, stopped at 18 rows | stopped by operator | the measurement envelope was cutting cases → framework defect #2 |
| 4 | `artifacts/selfevo-bigrun4` | 12.6 h | **3/3 cycles completed, 0 admissions** | the loop runs end to end; 4 of 8 candidates died on a schema mismatch → framework defect #3 |
| 5 | `artifacts/selfevo-bigrun5` | 5.0 h | **3/3 cycles completed, 0 admissions** | the objective is now measurable; the tracer route is inert |

Attempts 1–3 were diagnostic. Attempt 3's per-case rows are the evidence base
for the timeout analysis; its archive is corrected (see §5.1).

## 2. Framework defects found and fixed

All three were defects of the instrument, not of the candidate, and none
involved touching a gate threshold, a case set, or a metric definition.

### 2.1 The defect-injection audit froze a healthy run

The audit treated *any* unmeasurable row anywhere in the batch as disqualifying.
One sentinel case hitting its case deadline therefore froze a three-cycle run
(`frozen_after_defect_injection_audit`).

**Fix** (`evolution/engine.py`): the audit re-derives the defect rejection with
the unmeasurable rows excluded; insufficient-coverage detection is kept as a
separate, first condition so the two are never conflated. 6 tests in
`evolution/tests/test_audit_inconclusive_scope.py`.

**Verified**: attempt 2's guard returned `'unresolved row for case
Octotools/008.json'` (freeze); the same input after the fix returns `''`
(certify). Attempts 4 and 5 passed the audit in every cycle.

### 2.2 The measurement envelope was cutting working cases

Full analysis: [timeout-root-cause.md](timeout-root-cause.md). This is the
answer to "the case-timeout variance should not be this large".

**Cause, measured with controlled probes at zero concurrency.** Reasoning length
is stochastic and heavy-tailed: one identical prompt returned **36** and
**13,437** reasoning tokens on different calls (median 2,113) at a steady 20–46
tok/s. Implied wall clock per call: median ~106s, p90 ~411s, max ~672s — against
a **300s per-attempt cap**. A large share of legitimate calls were killed
mid-deliberation and retried from scratch, paying for both attempts. Measured
retry amplification in attempt 3's cache:

| stage | attempts that returned no ballot | share of attempts |
|---|---|---|
| judge | 44 of 203 | 21.7% |
| defense | 17 of 86 | 19.8% |
| causal_upgrade | 12 of 27 | 44.4% |

Note on the metric: a "no-output attempt" is one that consumed its budget
without delivering a ballot, so it covers a killed call, a transport error and a
gateway error alike. The reduction is consistent with the timeout being the
dominant cause, but not every one of them was provably a timeout — the recorded
warnings do not break down cleanly enough to claim that.

Whether a case paid a wasted retry was essentially a coin flip, which is exactly
the observed 34x spread in seconds-per-call. One row (`Octotools/008`) was cut
at the 3600s case deadline with its whole token budget spent, returning
`degraded_judge_only` and burning ~31k completion tokens for nothing.

**Fix** — four runtime-envelope values, no gate values:

| value | before | after |
|---|---|---|
| `provider.timeout_seconds` | 300 | 900 |
| `provider.total_timeout_seconds` | 900 | 2400 |
| `case_timeout_seconds` | 3600 | 10800 |
| `budget.cycle_timeout_seconds` | 18000 | 43200 |

**No token restriction was added**, per the request. `max_tokens` was
deliberately *removed* from the judge route after I briefly added it: observed
completions reach 14,090 tokens with a heavy tail, so a ceiling there would
introduce a truncation mode the route did not have, and a truncated reply
becomes a provider error. A test now pins it out.

**Verified three ways.** (a) `Octotools/008`, the case that broke attempt 3,
reached an authoritative correct verdict in attempt 4. (b) No-output attempts
fell from **21.1% of all attempts to 3.4%** (judge 21.7% → 2.9%, defense 19.8%
→ 4.2%, causal_upgrade 44.4% → 8.4%), measured over the full final cache of each
run. (c) Attempt 5 had
**20 distinct cases above 900s** (10 above 1800s, 1 above 3600s — `BFCL/070` at
4075s) and **all of them reached an authoritative verdict**, with zero cuts and
zero unresolved rows across 240 evaluations. Under the old 300s per-attempt cap
a large share of those calls could not have completed in one attempt.

### 2.3 The localization objective was unmeasurable by construction

The most consequential defect. `STRUCTURAL` requires a family's mechanism and
the claim's objective to agree: a `FAM-loc-` family must declare
`target_kind` in `{localization, joint}`. But:

- **`target_kind` appeared zero times in `evolution/proposer.py`.** No schema
  taught it, so `candidate_target_kind` defaulted every LLM-drafted claim to
  `"detection"`, and every candidate drawn from a localization family was
  refused at STRUCTURAL.
- **The family block mislabelled localization families as `"FP"`.** The line was
  `"FN" if startswith("FAM-fn") else "FP"`, and the prompt then instructed
  "target_polarity must match the family kind: recall for FN, precision for FP",
  so the model was told to answer a localization family with precision polarity.
- **The deterministic fallback had no localization branch at all**, so when the
  LLM proposer truncated (a real, observed failure) the entire objective fell
  through silently.
- **The cycle scored every candidate with a detection delta.** A tracer
  directive deliberately leaves the binary verdict alone, so even a correct
  localization claim would have been reported as "no marginal gain".

**Fix**: every claim schema now carries
`"target_kind": "detection|localization|joint"`; the family block states
`kind` and `required_target_kind`; the instructions give the mapping and the
route; the fallback gained a localization branch; and `engine._run_unit` now
dispatches BEHAVIOR and M by `target_kind`, using the localization gates and
`paired_localization_delta` for a localization claim. The gate was **not**
loosened — only the producer was repaired, and the gate still refuses a
detection claim on a localization family.

**Verified**: see §4.

### 2.4 Also fixed: token accounting undercounted by the ballot multiplicity

A voting stage returns its **last ballot's** provider meta, so `usage` recorded
one ballot of three while judge and defense each carried 3 ballots plus up to 2
topups. The reported burn was a floor, not the burn — the number a run's cost
must be sized from. Fixed by `accumulate_usage()` in `detector/provider.py`,
which writes a new `usage_total` beside the existing field and sums numeric
leaves by path rather than by a field whitelist. 6 tests in
`detector/tests/test_usage_accumulation.py`.

### 2.5 Recorded but not fixed

[known-defects.md](known-defects.md) documents a fifth: the per-case progress log
is never written during a journaled cycle, because `EVOLUTION_PROGRESS_LOG` is
opened inside `evaluate`'s `workers > 1` branch while `CycleJournal.evaluate`
forces `workers = 1`. Observability only — the writer's own docstring says it is
a log and never an input, and nothing downstream reads it. The dashboard's
liveness panel uses OS process inspection instead.

Also recorded there, fixed in the attempt-5 window: a truncated proposer reply
carried no `rejected` key, so it never entered the repair loop and the
configured `max_repair_calls` was dead code for the one failure it exists to
handle. Now routed via `rejected: "provider_failure"` (with `missing_credential`
excluded, since re-asking cannot mint a credential).

## 3. Attempt 4 — the loop runs, and refuses

`artifacts/selfevo-bigrun4`, 3/3 cycles `completed`, ledger valid (38 records).

| cycle | patrol acc | tp/fp/fn/tn | unresolved | families (det/loc) | admitted |
|---|---|---|---|---|---|
| 1 | 0.70 | 10/1/5/4 | 0 | 1 / 2 | none |
| 2 | 0.80 | 11/1/3/5 | 0 | 1 / 2 | none |
| 3 | 0.70 | 10/1/5/4 | 0 | 1 / 2 | none |

Eight candidates, all rejected. What matters is *where*:

| candidate | hook | stopped at |
|---|---|---|
| `IV-1-evo_recall_has_tool_calls` | recall.trigger | HARMLESSNESS `new_fp=2` |
| `IV-1-evo_recall_question_has_numbers` | recall.trigger | HARMLESSNESS `new_fp=1` |
| `IV-2-001` | recall.trigger | HARMLESSNESS `new_fp=2` |
| `IV-2-002` | tracer.system | **STRUCTURAL** — schema mismatch (§2.3) |
| `IV-2-003` | tracer.system | **STRUCTURAL** — schema mismatch |
| `IV-3-001` | tracer.system | **STRUCTURAL** — schema mismatch |
| `IV-3-002` | tracer.system | **STRUCTURAL** — schema mismatch |
| `IV-3-003` | scanner.system | HARMLESSNESS `new_fp=1` |

**Four of eight died on a schema mismatch**, so the localization objective was
never scored in this run. Two recall candidates showed `+1 marginal on the gain
set with zero new errors` while costing 1–2 protected cases — the exact trade
`m_new_errors_allowed = 0` plus `harmlessness_new_fp_allowed = 0` exists to
refuse, and it refused it.

The audit verified itself on two injected defects, each caught by the gate that
*should* catch it:

| defect | flaw | caught by | reason |
|---|---|---|---|
| `IV-DEFECT-001` | case-id leak in the directive | STRUCTURAL | `case id leak in directive/body/claim: BFCL/001.json` |
| `IV-DEFECT-002` | semantically empty noise | BEHAVIOR | `target polarity mismatch: boundary claim corrected no decisive case` |

The second is the sharper result: it passed STRUCTURAL and HARMLESSNESS and was
stopped only at BEHAVIOR.

## 4. Attempt 5 — the objective is measured, and the route is inert

`artifacts/selfevo-bigrun5`, 3/3 cycles `completed`, ledger valid (35 records).
Run on the fixed code (`source_digest` `34dd12051e73d9d6` →
`98485e220211b1c2`), same split (sha256 `b986902c75a16b9d`, unchanged), same
gates.

| cycle | patrol acc | tp/fp/fn/tn | unresolved | families (det/loc) | admitted |
|---|---|---|---|---|---|
| 1 | 0.85 | 13/1/2/4 | 0 | 0 / 1 | none |
| 2 | 0.85 | 12/1/2/5 | 0 | 0 / 2 | none |
| 3 | 0.85 | 13/1/2/4 | 0 | 0 / 1 | none |

Five candidates, all `tracer.system`, all LLM-drafted, all declaring
`target_kind: localization`:

| cycle | candidate | STRUCTURAL | HARMLESSNESS | localization BEHAVIOR |
|---|---|---|---|---|
| 1 | `IV-1-001` | pass | **pass** | **fail** `joint_improved=0, joint_broken=0` |
| 2 | `IV-2-001` | pass | **pass** | **fail** `joint_improved=0, joint_broken=0` |
| 2 | `IV-2-002` | pass | **pass** | **fail** `joint_improved=0, joint_broken=0` |
| 3 | `IV-3-001` | pass | **pass** | **fail** `joint_improved=0, joint_broken=0` |
| 3 | `IV-3-002` | pass | **pass** | **fail** `joint_improved=0, joint_broken=0` |

**Zero died at STRUCTURAL**, against four in attempt 4. The objective moved from
*unmeasurable* to *measured*, with `localizable_n = 11` on the first gate that
could score it.

Full analysis: [localization-measured.md](localization-measured.md). The
load-bearing checks, because `joint_delta = 0` has several possible causes:

| hypothesis | test | result |
|---|---|---|
| the candidate never fired | `fired_rate` over its 20 rows | **0.5** — `text_injected` recorded, `hook=tracer.system` |
| the tracer was frozen, so it could not respond | `freeze_tracer = "tracer.system" not in hooks` | **False** — the hook keeps the tracer live; only `judge_result` was frozen |
| the prompt never changed | tracer deliberation on one case, across arms | 1,450 → 2,054 → **29,690** completion tokens, visibly different reasoning text, all concluding step 7 |
| `responsible_step` is pinned upstream so a tracer change could not show | compare `tracer.step` vs `responsible_step` | they **agree** wherever the tracer ran; `adjudication.py` leaves it `None` when it did not |

And cycle 2 falsified the "wrong direction" explanation: the proposer received
cycle 1's rejection and drafted **both** directions — one asking for the final
synthesis event, one asking for the originating mid-trajectory event — and
**both** moved nothing.

Reading the tracer's own contract explains it. `TRACER_SYSTEM` already commands
the procedure the directives paraphrase:

> Your only job is to find the EARLIEST step that introduced the error. … Walk
> backwards from the delivered result. At each earlier step ask: is the wrong
> content already present here? Stop at the first step where it is.

A text boundary on that hook therefore adds nothing the stage does not already
do, and one that contradicts it is overruled by the stage's own contract.

That is a **capability limitation of the action space on this corpus**, not a
framework defect. `classify_family_actionability` offers a `FAM-loc-` family
exactly one route (`tracer_directive`), because the conviction is already
correct and no guard or evidence row can change which step is named.

### 4.1 The mislocation direction is consistent, so the null is informative

If mislocations went both ways, no directive could fix them and "no gain" would
mean nothing. Measured over every convicted-and-annotated case in attempt 5's
control arm:

| direction | count |
|---|---|
| detector names a **later** step than gold | **15** |
| exact hit | 6 |
| detector names an **earlier** step than gold | **1** |

15 of 16 are LATER, and all four patrol-family members are later (+1, +2, +1,
+42). So "name the earliest step" is the correct direction for this family — and
it is what the tracer already does, which is precisely why the directive is
redundant.

### 4.2 Annotation caveat on what a localization gain would mean

The two improvable gain-set cases are **adjacent-step editorial disagreements,
not detector defects**, and the annotation's own reason text shows it:

- `Octotools/022` — gold 3 blames the *choice of method*; the detector names 4,
  where the false answer is delivered.
- `OpenManus/074` — gold 1 is the human's `ask_human` reply that supplied the
  wrong state; the detector names 2, the agent adopting it. For a question about
  *agent* hallucination the detector's reading is arguably more correct.

So a tracer directive that moved these by one step would be fitting an editorial
convention, not repairing a defect. Stated in advance in
[localization-route.md](localization-route.md), and it did not arise because no
step moved.

## 5. Cross-run findings

### 5.1 A verdict is not stable across two identical runs

[verdict-stability.md](verdict-stability.md). Attempts 2 and 3 evaluated the
same 18 cases under the same config: 17 verdicts matched, **one differed**
(`Camel/048`). Between attempts 3 and 4, `BFCL/038` moved from
`verified_error` to `verified_clean` — and the cached provider records show the
**judge itself changed its mind** (2/3 positive, high confidence → 0/3 positive,
medium confidence). That flip is provider noise and is **not** evidence for the
envelope fix; reporting it as an accuracy gain would have been unearned.

Consequence, and the reason a gate verdict is worth more than a hand-computed
delta: a one-case marginal gain on this corpus is inside run-to-run noise unless
the paired replay held the control fixed and no new error accompanies it.

### 5.2 Power of the patrol wave

[patrol-power.md](patrol-power.md), written before the decisive run. The patrol
wave reaches all 24 patrol-pool cases (18 gold-positive) because the engine's
bootstrap already covers whatever the initial 20-case draw leaves. With the
measured same-signature rate, ~93% power to see a 2-member family. No pool
enlargement was made or needed.

### 5.3 Attempt 3's archive was corrected

The first archive listed both `Octotools/008` and `Camel/048` as cut, inferred
from `elapsed >= 3599`. Reading `Camel/048`'s row shows an authoritative
`verified_clean` verdict with every stage `ok`. Only `Octotools/008` was cut;
`Camel/048` finished 139ms past the deadline, which is possible because the
deadline is checked *before* each call. `archive.json`, `run_manifest.json` and
the timeout analysis were corrected to record one cut and one near-miss.

### 5.4 The retry figures were corrected down, not up

The first version of §2.2 reported retry churn as "judge 44 extra attempts over
25 cases → 0; defense 17 over 9 → 0". Both halves of that were wrong, and the
error is worth recording because it ran in the flattering direction:

- **The baseline was a mid-run snapshot.** 44/17/20 came from a 6-case sample
  taken while attempt 3 was still running, and I later quoted it as though it
  were the final measurement.
- **The endpoint was never zero.** I compared that sample against another
  mid-run sample from attempt 4, where the counts happened to be low. The final
  attempt-4 cache still contains 14 judge and 9 defense attempts that returned
  no ballot.
- **The denominator was missing entirely.** "44 extra attempts" without a total
  reads as a large fraction of the work; the honest figure is 21.7% of judge
  attempts.

Corrected, from each run's full final cache:

| stage | attempt 3 | attempt 4 |
|---|---|---|
| judge | 44 of 203 = 21.7% | 14 of 482 = **2.9%** |
| defense | 17 of 86 = 19.8% | 9 of 216 = **4.2%** |
| causal_upgrade | 12 of 27 = 44.4% | 8 of 95 = **8.4%** |
| overall | **21.1%** | **3.4%** |

The conclusion survives — a roughly six-fold reduction in attempts that produced
no ballot — but it is smaller and messier than the first draft implied, and it is
corroboration rather than proof. What actually establishes the cause is the
controlled probe (one identical prompt returning 36 and 13,437 reasoning tokens
against a 300s cap at 20–46 tok/s) plus the deadline-cut row; neither depends on
this table.

Also noted while correcting it: a "no-output attempt" is not definitionally a
timeout. It covers killed calls, transport errors and gateway error pages, and
the recorded warnings do not break down cleanly enough to attribute every one.
`timeout-root-cause.md`, `results.md` and `verdict-stability.md` carry the same
corrected framing.

## 6. Final result

**The framework validated. The self-evolution effect did not.**

*Validated, with direct evidence:*

1. **Two independent 3-cycle runs completed** (attempts 4 and 5), every cycle
   reaching family diagnosis → proposal → a formal gate verdict. Ledger hash
   chains verify (38 and 35 records).
2. **The audit certified itself** in every cycle of both runs, on two injected
   defects each caught by the gate that should catch it — having been the defect
   that killed attempt 2.
3. **HARMLESSNESS refused 7 candidates** for breaking the protection set,
   including two that showed a gain-set win with zero new errors. The
   anti-compensation invariant held on real data.
4. **STRUCTURAL refused 4 candidates** in attempt 4 and **0** in attempt 5,
   which is the measured effect of fixing §2.3.
5. **The instrument was never the limit**: `instrument_limited: false` in every
   cycle, `blocked_families: []`, and **zero unresolved rows across 120 patrol
   evaluations** in attempt 5.
6. **The proposer responded to its own rejection record**: in attempt 4 it moved
   from the deterministic fallback to LLM drafting and cut sentinel harm from 2
   protected cases to 1; in attempt 5 cycle 2 it read cycle 1's rejection and
   attempted the opposite direction.
7. **The empty registry was never bypassed.** `active: []` in both final
   reports, `registry_state.interventions: {}`, and no version record advanced.

*Not demonstrated:* no intervention was admitted, so there is no measured
self-improvement.

### The out-of-sample read

`mini_effect` on the 12-case validation set, disjoint from the pool and never
seen by any gate:

```
cases: 12   paired_replay: true
marginal_cases: 0   new_errors: 0   unresolved_added: 0
baseline_accuracy: 0.583   active_accuracy: 0.583   flips: {T->T: 3, F->F: 9}
```

Both arms are identical because the registry is empty, so this is **not an
effect claim** — it is a detector reading on held-out data: 58.3% all-case
accuracy, zero unresolved. That is the honest baseline a future admitted
candidate would have to beat. The reported `passed: false` compares the empty
registry against itself and carries no meaning about any mechanism; it is
included because it is in the artifact.

### One-line reading

> This effort shows the loop correctly refusing to call thirteen weak candidates
> improvements — which is the property that makes an eventual positive result
> meaningful, and is not itself that positive result.

## 7. Cost

Attempt 5, 240 case evaluations, provider wall-clock:

| metric | value |
|---|---|
| per-case seconds | min 0 (cache) · median 0 (cache) · p90 638 · max 4,075 |
| total provider wall-clock | 14.2 h across 240 evaluations |
| cycle wall clock | 191 + 72 + 35 min, plus the 12-case validation |
| completion tokens (cached rows) | 3,086,952, of which **94% reasoning** |
| cases at the old 3600s cap | 1, and it completed authoritatively rather than being cut |
| unresolved rows | **0** |

The 94% reasoning share is the same property that caused §2.2, and the reason
the per-attempt ceiling had to exceed the model's deliberation length.

## 8. Where a positive result would come from next

Recorded for a future round; **not being started here.**

1. **Widen the gain set's recall opportunity.** `gain_opportunity.recall = 1`
   means the entire recall objective rested on a single case, against a
   protection cost of 1–2. A larger gain set with more judge-negative true
   positives would let a partially selective trigger show a marginal gain
   instead of a 1-for-1 trade.
2. **Give the localization objective a second route, or accept it as measured
   inert.** The tracer ignores text boundaries in both directions, so the only
   lever this action space exposes for a `FAM-loc-` family is the one that does
   not work. Either the action space needs a route that can move a named step,
   or this objective should be reported as a capability limit rather than
   pursued further on this corpus.
3. **Resolve the annotation ambiguity before treating step labels as ground
   truth.** §4.2 shows the two "improvable" cases are adjacent-step editorial
   disagreements, and the 2026-09-21 viability scan measured that most observed
   mislocations are annotation ambiguity. Step-level credit on this corpus needs
   an annotation audit first.

## 9. Artifact index

| path | contents |
|---|---|
| `results.md` | the full results narrative and verdicts |
| `timeout-root-cause.md` | §2.2, the case-timeout analysis |
| `localization-measured.md` | §4, why the tracer route is inert |
| `localization-route.md` | pre-registered expectation for attempt 5 |
| `verdict-stability.md` | §5.1, run-to-run verdict instability |
| `known-defects.md` | all five defects/limits, with fixes and rationale |
| `patrol-power.md` | §5.2, power of the patrol wave |
| `run_manifest.json` | per-attempt provenance and config changes |
| `log-numbers.json` | machine-readable numbers quoted in this log |
| `final-analysis.json` | read-only re-derivation of attempt 4's readings |
| `config-as-launched-attempt{2,3,4,5}.json` | the exact config each run used |
| `split-big.json` | the frozen split (unchanged across all runs) |
| `attempt1-sizing/`, `attempt2-frozen/`, `attempt3-deadline-cuts/` | archived diagnostics |
| `artifacts/selfevo-bigrun4/`, `artifacts/selfevo-bigrun5/` | raw run artifacts (ledgers, checkpoints, cycle reports, caches) |
