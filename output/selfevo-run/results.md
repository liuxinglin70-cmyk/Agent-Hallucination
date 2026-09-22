# Self-evolution framework validation — results

> **This document is the attempt-by-attempt narrative and was written
> incrementally. The consolidated record — all five runs, all defects, the final
> verdict, and the artifact index — is [experiment-log.md](experiment-log.md).**
> Read that first; read this for the reasoning behind each step.

Two 3-cycle runs reached a gate verdict:

- `artifacts/selfevo-bigrun4` — attempt 4, 3/3 cycles completed, 0 admissions.
- `artifacts/selfevo-bigrun5` — attempt 5, on the fixed code, 3/3 cycles
  completed, 0 admissions.

Command: `python -m evolution experiment --cycles 3 --mini --config
evolution/configs/validation_big_run.json`
Split: `output/selfevo-run/split-big.json` — pool 44 (29 gold-positive / 15
gold-negative, all 7 sources), validation 12 (6/6), disjoint. Byte-identical
across every run (sha256 `b986902c75a16b9d`).

**Final status: complete. The framework validated; the self-evolution effect did
not.** No admission in either run, and no gate was relaxed, no batch swapped,
and no failure dropped to change that.

---

## 1. The question this run answers

Does the loop produce, gate and (if it passes) deploy a real change on a wider
development split — with the verdict coming from the gates rather than from
anyone's judgement?

The `run_manifest.json` fixed the acceptance criteria before launch:

*Engineering:* three cycles complete or stop for a recorded reason; the ledger
hash chain verifies; no gate relaxed and no threshold edited.

*Effect:* at least one candidate reaches a gate verdict on a real batch; if one
is admitted, the version record is written and the next cycle loads it; if
nothing is admitted, the readings still distinguish "instrument could not
measure" from "mechanism failed".

*Explicitly not success:* any admission obtained by widening a gate, swapping a
batch after seeing output, or dropping failures from the report.

## 2. Attempts 1-3: three framework defects, found and fixed

Nothing below changed a gate, a split, a case set, or a metric definition.

| # | Outcome | Framework defect it exposed | Fix |
|---|---|---|---|
| 1 | stopped at 10 rows | the 4h cycle cap was smaller than the measured per-case latency, and a non-completed cycle aborts the whole experiment | cycle cap raised after measuring latency (16 → 20 workers) |
| 2 | froze after cycle 1 | the defect-injection audit swept the whole batch for unmeasurable rows and treated the presence of any one as disqualifying, so a single case at its deadline froze a three-cycle run | the audit re-derives the defect rejection with the unmeasurable rows excluded; insufficient-coverage detection kept as a separate condition |
| 3 | stopped at 18 rows | the per-case measurement envelope was itself cutting cases — see §3 | envelope raised; token accounting fixed |

Attempts 1, 2 and 3 are archived under `output/selfevo-run/` with their measured
evidence.

## 3. The case-timeout finding

Full analysis: [timeout-root-cause.md](timeout-root-cause.md).

**Cause, measured.** Reasoning length is stochastic and heavy-tailed: one
identical prompt returned 36 and 13,437 reasoning tokens on different calls
(median 2,113) at 20–46 tok/s, so a legitimate call can need ~672s. The
per-attempt timeout was **300s**, so a large share of working calls were killed
mid-deliberation and retried from scratch — paying for both attempts. Judge
produced no ballot on 21.7% of its attempts, defense on 19.8%, the causal
upgrade on 44.4%. This is what produced the 34x spread in seconds-per-call:
whether a case paid a wasted retry was essentially a coin flip.

**Fix.** Three runtime-envelope values and one cycle ceiling:
`provider.timeout_seconds` 300 → 900, `total_timeout_seconds` 900 → 2400,
`case_timeout_seconds` 3600 → 10800, `budget.cycle_timeout_seconds` 18000 →
43200.

**Result, verified two ways.**

Paired on the case that broke attempt 3:

| case | attempt 3 (old envelope) | attempt 4 (new envelope) |
|---|---|---|
| Octotools/008 | 3600.0s, cut at the deadline, `degraded_judge_only`, prediction **None**, budget burned | 3594.1s, `authoritative`, `verified_error`, prediction **True** = gold |

And the no-output attempt share, independently — measured over each run's
**full final cache**, not a mid-run sample:

| stage | attempt 3 (300s/attempt) | attempt 4 (900s/2400s) |
|---|---|---|
| judge | 44 of 203 = 21.7% | 14 of 482 = **2.9%** |
| defense | 17 of 86 = 19.8% | 9 of 216 = **4.2%** |
| causal_upgrade | 12 of 27 = 44.4% | 8 of 95 = **8.4%** |
| **overall** | **21.1%** | **3.4%** |

They fell by roughly six-fold and did not reach zero: the ceiling removed most
of the churn, not the failure mode. A "no-output attempt" also covers transport
and gateway errors, not only timeouts, so this is corroboration of the probe
evidence rather than a timeout count on its own.

Attempt 4 has since measured cases at 1150s, 1698s, 1886s and 3733s — all
authoritative, none cut, and every one of them beyond the old ceilings.

**Rejected on evidence:** disabling thinking is 40x faster and **changes the
answer** — on a known error case the reasoning path says error while
`thinking: disabled`, `enable_thinking: false` and `reasoning_effort: none` all
say clean. That is a worse detector, not a cheaper one. Also rejected: adding a
`max_tokens` ceiling to the judge route (a test now pins it *out*, because a
truncated reply becomes a provider error).

## 4. A defect found on the way: tokens were undercounted

A voting stage returns its **last ballot's** provider meta, so `usage` recorded
one ballot of three while judge and defense each carried 3 ballots plus up to 2
topups. The reported burn was a floor. Fixed by `accumulate_usage()` in
`detector/provider.py`, which writes a new `usage_total` beside the existing
field and sums numeric leaves by path rather than by a field whitelist.

## 5. What the split can support (power)

Full note: [patrol-power.md](patrol-power.md).

The patrol wave reaches **all 24 patrol-pool cases, 18 of them gold-positive**,
because the engine's bootstrap already covers whatever the initial 20-case draw
leaves. With the measured same-signature rate among positives (2 of 9), the
wave has **~93% power** to see a 2-member family. No pool enlargement was made
or needed; the split manifest is byte-identical to attempt 1's.

Verified separately (`family_signature_scan.py`) that a family **does** form on
the measured errors: two gold-positive cases from two different sources share
the signature `FAM-fn-judge_false_no_recall_candidates`, and
`classify_family_actionability` gives it four routes (`recall_trigger`,
`recall_directive`, `upgrade_directive`, `evolved_predicate`). The gain set
contains `BFCL/101`, a member of that same family, so the M gate has a case it
can actually reward.

## 6. A caveat that constrains any future claim

Full note: [verdict-stability.md](verdict-stability.md).

Attempts 2 and 3 evaluated the same 18 cases under the same config; 17 verdicts
matched and **one differed** (Camel/048). Between attempts 3 and 4, BFCL/038
moved from `verified_error` to `verified_clean` — and the cached provider
records show the **judge itself changed its mind** (2/3 positive with high
confidence → 0/3 positive with medium confidence). That flip is provider noise
and is **not** evidence for the envelope fix; reporting it as an accuracy gain
would have been unearned.

Consequence: a one-case marginal gain on this corpus is inside run-to-run noise
unless the paired replay held the control fixed and no new error accompanies it.
The framework already has the mechanisms for that (frozen-stage replay,
`m_new_errors_allowed = 0`, zero new unresolved on the protected set,
`baseline_roll` pinning), which is why a gate verdict here is worth more than
a delta computed by hand.

## 7. Cycle 1 verdict — the loop reached a real gate decision

**State: `completed`** (333.5 min). This is the first time in the framework's
history that a cycle completed with a diagnosed family and a gated proposal, so
every stage below was exercised on a real batch.

### The instrument was not the limit

| reading | value |
|---|---|
| `instrument_limited` | **false** |
| `gain_opportunity` | `{recall: 1, precision: 0}` |
| `blocked_families` | `[]` |
| `unmeasurable_polarities` | `["precision"]` |
| sentinel preflight | evaluable, `protected_tp=5`, `protected_tn=5` (floor 2/2) |
| patrol unresolved | **0 of 20** |
| defect-injection audit | **passed** on all defects |

The `unmeasurable_polarities: ["precision"]` entry is a measured property of the
evaluation set: the control arm makes no false positive on the gain set, so a
precision claim has nothing to win there. That is reported, not worked around.

### Patrol: a stable family exists

20 cases, `tp=10 fp=1 fn=5 tn=4`, accuracy 0.70, **1 detection family and 2
localization families**:

| family | count | cases |
|---|---|---|
| `FAM-fn-judge_false_no_recall_candidates-…` | 3 | BFCL/070, BFCL/159, Magentic_One/021 |
| `FAM-loc-mislocated-…Pe2d79672304f` | 3 | OpenDeepSearch/016, Octotools/009, Octotools/031 |
| `FAM-loc-mislocated-…Pdc59b76d89dc` | 2 | OpenDeepSearch/079, Magentic_One/061 |

The detection family's 5 members span four sources (`BFCL`, `Magentic_One`,
`OpenManus`, `SmolAgents`) — a cross-source mechanism, not one benchmark's
quirk. The bootstrap did not need to fire (`family_found: true` on the initial
20-case sweep), which is exactly what the pre-registered power note predicted.

### Proposals

Two candidates, both `recall_trigger` on the FN family:
`IV-1-evo_recall_has_tool_calls` and `IV-1-evo_recall_question_has_numbers`.

They came from the **deterministic fallback**, not the LLM proposer: the LLM
call returned `truncated_output` and produced no candidates. See
[known-defects.md](known-defects.md) §3 — a truncation does not reach the
configured repair call, which is a real proposer-quality gap and is recorded
rather than patched mid-run.

### Gate verdicts: both rejected, on the merits

| candidate | structural | harmlessness | M |
|---|---|---|---|
| `IV-1-evo_recall_has_tool_calls` | passed (jaccard 0.0181) | **FAILED** `new_fp=2` | not run |
| `IV-1-evo_recall_question_has_numbers` | passed (jaccard 0.0181) | **FAILED** `new_fp=1` | not run |

Terminal classification: both **`rejected`**, `not_evaluable: false`.

This is the correct outcome and it is worth being precise about why. The
fallback templates fire on a *packet feature* (`has_tool_calls`,
`question_has_numbers`) rather than on the mechanism. `has_tool_calls` is true
for almost everything the detector sees, so the candidate forced the recall net
on the sentinel and turned two clean cases into convictions — the harmlessness
set caught precisely the over-broad trigger. `question_has_numbers` is narrower
and cost one. The gates refused an over-broad intervention that would have
looked like a repair on the gain set:

| candidate | gain-set effect (my offline recomputation) | sentinel cost |
|---|---|---|
| `…has_tool_calls` | +1 marginal, 0 new errors | **−2 (2 new FP)** |
| `…question_has_numbers` | +1 marginal, 0 new errors | **−1 (1 new FP)** |

That is exactly the trade the invariant `m_new_errors_allowed = 0` plus
HARMLESSNESS `new_fp_allowed = 0` exists to refuse, and it refused it. **A
candidate that gains on the benefit set while breaking the protection set was
not admitted**, which is a direct, positive demonstration that the gate stack
works — the framework's own "no weighted compensation" rule (§5.2) enforced on
a real candidate.

### What cycle 1 did **not** produce

No admission. `active_after_cycle: []`, `marginal_selection.selected: []`,
`combination.reason: "no qualified unit"`. **The self-evolution effect is not
yet demonstrated** — a mechanism that both repairs a family and survives the
protection set has not been found.

## 8. Cycles 2 and 3: the loop keeps working, and keeps refusing

All three cycles reached `completed` with a real diagnosis and a real gate
verdict. The ledger verifies (38 records, hash chain intact).

| cycle | patrol acc | tp/fp/fn/tn | unresolved | families (det/loc) | admitted |
|---|---|---|---|---|---|
| 1 | 0.70 | 10/1/5/4 | 0 | 1 / 2 | **none** |
| 2 | 0.80 | 11/1/3/5 | 0 | 1 / 2 | **none** |
| 3 | 0.70 | 10/1/5/4 | 0 | 1 / 2 | **none** |

Every candidate, every cycle, with the layer that stopped it:

| cycle | candidate | stopped at | reason |
|---|---|---|---|
| 1 | `IV-1-evo_recall_has_tool_calls` | HARMLESSNESS | `new_fp=2` |
| 1 | `IV-1-evo_recall_question_has_numbers` | HARMLESSNESS | `new_fp=1` |
| 2 | `IV-2-001` | HARMLESSNESS | `new_fp=2` |
| 2 | `IV-2-002` | STRUCTURAL | `FAM-loc-` needs `target_kind` localization/joint |
| 2 | `IV-2-003` | STRUCTURAL | same |
| 3 | `IV-3-001` | STRUCTURAL | same |
| 3 | `IV-3-002` | STRUCTURAL | same |
| 3 | `IV-3-003` | HARMLESSNESS | `new_fp=1` |

`instrument_limited: false` in all three cycles. The instrument could measure;
the candidates did not qualify.

### The proposer did learn

This is the clearest evidence that the loop is a closed feedback system rather
than a repetitive sampler. Cycle 1's candidates were the deterministic
fallback's pattern-match on a packet feature (`has_tool_calls` — true of almost
everything). Cycle 2's LLM proposer succeeded (26,013 completion tokens) and
drafted narrower triggers, and cycle 3's candidate `IV-3-003` **scored zero
harm to the entire gain set and used a hand-written predicate rather than a
feature flag**. The harm it did cause was a single case:

| | cycle 1 best | cycle 3 best |
|---|---|---|
| sentinel harm | 2 protected cases broken | **1** |
| trigger basis | `has_tool_calls` packet feature | mechanism-level recall predicate |
| source | deterministic fallback | **LLM proposer** |

And the reason is visible in the run's own ledger: the `rejected` events carry
`feedback` that the next cycle's prompt receives, and cycle 3's
`expected_behavior` text explicitly answers the earlier refusal
("…jointly with detection…", "…while honest error-reporting negatives remain
excluded"). The loop acted on its own rejection record.

### Why the one surviving FN family never admits — the real tension

The single stable detection family is
`FAM-fn-judge_false_no_recall_candidates`, present in **all three cycles** with
members from 3–4 different sources. Its mechanism signature is unambiguous:

```
judge_positive = false, recall_path = "not_needed", upgrade_positive = false
```

The judge says "clean", so the recall net is never invoked and nothing
downstream can convict. Repairing it requires *forcing recall on cases the
judge cleared* — which is exactly the intervention that harms a protection set
containing **10 baseline-correct cases (5 positive, 5 negative)**.

Every candidate found this trade and lost it:

| candidate | gain-set effect | sentinel cost |
|---|---|---|
| `IV-1-…has_tool_calls` | +1 marginal | −2 protected |
| `IV-2-001` | 0 marginal | −2 protected |
| `IV-3-003` | 0 marginal | −1 protected |

This is a **genuine mechanism-level finding**, not a framework failure. The
gain opportunity (`recall: 1`) is a single case; the protection cost of the
only available route is 1–2 cases. Under the repo's invariant that a
new error is never netted against a gain (`m_new_errors_allowed = 0`, and
`harmlessness_new_fp_allowed = 0`), no candidate of this shape can be admitted —
and the framework is *right* to refuse it. Admitting `IV-3-003` would have
traded one recovered false negative for one new false positive at equal weight,
which is not self-improvement.

Note precisely what this does and does not say. It says the loop cannot find a
recall intervention selective enough to fix this family without touching the
protected clean cases, on this 44-case pool with this protection set. It does
not say the detector is broken, and it does not say the mechanism is impossible
at a larger scale — a more discriminating trigger might exist that this proposer
did not find in three cycles.

## 9. Attempt 5: the fixes worked, and the objective was measured

Attempt 4's negative result was not final, because **four of its eight**
candidates had been refused on a schema mismatch rather than on merit. The inter-run window
was the time to fix that, so it was fixed (see
[known-defects.md](known-defects.md) §3 and §5, now closed):

- **`target_kind` is taught.** Every claim schema in the draft prompt now
  carries `"target_kind": "detection|localization|joint"`, the family block
  states each family's `kind` and `required_target_kind`, and the instructions
  give the mapping. The family block had also been mislabelling every non-FN
  family as `"FP"`, which told the model to answer a localization family with
  precision polarity.
- **The fallback can express localization.** A `FAM-loc-` family now routes to
  a deterministic `tracer.system` directive instead of producing nothing.
- **The cycle scores by objective.** M and BEHAVIOR dispatch on `target_kind`,
  so a localization claim is measured by the localization gates. Previously the
  cycle computed a *detection* delta for every candidate, which would report
  "no marginal gain" for a tracer directive that did exactly what it promised.
- **A truncated proposer reply reaches the repair call**, via a
  `rejected: "provider_failure"` note (`missing_credential` excluded, since
  re-asking cannot mint one).

528 tests pass and all seven verifier checks are green.

### The proof the fix worked

Attempt 5 cycle 1's single candidate was **LLM-drafted** and declared:

```json
{"target_kind": "localization", "target_polarity": "boundary",
 "expected_behavior": "…the tracer names the final delivery/synthesis step…"}
```

It **passed STRUCTURAL**. Under attempt 4's code the same candidate would have
died with *"localization failure family requires target_kind localization or
joint, got 'detection'"*. This is the first localization candidate in the
framework's history to reach an objective-specific gate.

### The verdict, and the three-way distinction behind it

| layer | verdict | reading |
|---|---|---|
| STRUCTURAL | **passed** | jaccard 0.0181 |
| HARMLESSNESS | **passed** | detection delta all zero |
| localization BEHAVIOR | **failed** | `localizable_n=11, joint_improved=0, joint_broken=0` |
| localization M | not run | BEHAVIOR failed first |

`localizable_n = 11` is the number that matters: **the objective was measured**,
where before it was refused. And HARMLESSNESS passed with an identically zero
detection delta — the structural advantage the localization route was chosen
for, since a tracer directive leaves every conviction alone.

`joint_delta = 0` could mean three very different things, so each was ruled out:

| hypothesis | test | result |
|---|---|---|
| the candidate never fired | `fired_rate` on its 20 rows | **0.5** — `text_injected` on 10 cases, `hook=tracer.system` |
| the tracer was frozen, so it could not respond | `freeze_tracer = "tracer.system" not in hooks` | **False** — the hook kept the tracer live |
| the tracer ran but on a cached prompt | compare the arms' tracer calls | **genuinely fresh** (4,585 vs 2,339 completion tokens; different raw output, same step 4) |

So the directive was appended to a live tracer prompt, the tracer re-ran, and it
named the same step. **The finding is about the mechanism, not the instrument.**

Reading the tracer's own system prompt explains it: `TRACER_SYSTEM` already says
*"find the EARLIEST step that introduced the error"* and prescribes walking
backwards until the wrong content first appears. The candidate's claim asked for
the *final delivery* step — the opposite direction. The tracer followed its own
law, which is the correct behaviour, and the candidate was inert.

### The family is directionally learnable, so the null is informative

If mislocations went both ways, no directive could fix them and "no gain" would
mean nothing. Measured over every convicted-and-annotated case in attempt 5's
control arm:

| direction | count |
|---|---|
| detector names a **later** step than gold | **15** |
| exact hit | 6 |
| detector names an **earlier** step than gold | **1** |

15 of 16 are LATER, and all four patrol-family members are later (+1, +2, +1,
+42). "Name the earliest step" is therefore the right direction for this family
— it is also what the deterministic fallback already says. The LLM proposer read
the family backwards, and cycle 1's rejection feedback now hands cycle 2 that
correction concretely.

### Attempt 5 cycles 1 and 2: the route fires and is inert

| cycle | candidates | STRUCTURAL | HARMLESSNESS | localization BEHAVIOR | outcome |
|---|---|---|---|---|---|
| 1 | 1 (`tracer.system`) | pass | **pass** (detection delta all zero) | **fail** `joint_improved=0, joint_broken=0` | rejected |
| 2 | 2 (`tracer.system`) | pass | **pass** (detection delta all zero) | **fail** `joint_improved=0, joint_broken=0` | rejected |

Cycle 2 is what makes this decidable rather than a single-sample null: the
proposer received cycle 1's rejection and drafted **both** directions — one
asking for the final synthesis event, one asking for the originating
mid-trajectory event — and **both** moved nothing. Full analysis in
[localization-measured.md](localization-measured.md).

Four hypotheses were eliminated before accepting that:

| hypothesis | test | result |
|---|---|---|
| the candidate never fired | `fired_rate` on its rows | **0.5** — `text_injected` recorded, `hook=tracer.system` |
| the tracer was frozen, so it could not respond | `freeze_tracer = "tracer.system" not in hooks` | **False** — the hook keeps the tracer live |
| `responsible_step` is pinned upstream, so a tracer change could not show | compare `tracer.step` against `responsible_step` across arms | they **agree** wherever the tracer ran, and `adjudication.py` leaves it `None` when it did not — the recorded step *is* the tracer's output |
| the injected prompt never changed the tracer's work | compare tracer deliberation across arms on one case | control 1,450 completion tokens → 2,054 → **29,690**, with visibly different reasoning text, all concluding step 7 |

So the directive reached a live tracer, changed how it reasoned, and did not
change what it answered. The `tracer_directive` route is legal, fires, and has
no measurable effect on this corpus — a **capability limitation of the action
space**, not a framework defect.

This is not a satisfying result and it is not a manufactured one. It has a clear
mechanism: `TRACER_SYSTEM` already commands the procedure the directives
paraphrase ("find the EARLIEST step", "walk backwards … stop at the first step
where it is"), so a text boundary on that hook adds nothing the stage does not
already do, and one that contradicts it is overruled by the stage's own
contract.

### Where attempt 5 stands

Cycles 2 and 3 repeated the result below, so after three cycles attempt 5
closed with still no admission. The remaining objective is not reachable with
the routes this action space exposes:
`classify_family_actionability` offers a `FAM-loc-` family exactly one route
(`tracer_directive`), because the conviction is already correct and no guard or
evidence row can change which step is named.

## 10. Verdict (attempt 4)
*Attempt 4's verdict. Attempt 5's is in §9 above and in
[experiment-log.md](experiment-log.md) §6.*

**The framework validated. The self-evolution effect did not.**

*Validated* — with direct evidence, not proxy signals:

1. **Three consecutive cycles completed** on the widened split, each reaching
   family diagnosis, proposal, and a formal gate verdict. `state: completed`
   three times; ledger hash chain intact (38 records).
2. **The defect-injection audit passed** in every cycle, having been the defect
   that killed attempt 2. Both injected defects were rejected by the gate that
   *should* reject them, and neither was `inconclusive`:

   | defect | injected flaw | caught by | reason |
   |---|---|---|---|
   | `IV-DEFECT-001` | case-id leak in the directive | STRUCTURAL | `case id leak in directive/body/claim: BFCL/001.json` |
   | `IV-DEFECT-002` | semantically empty noise | BEHAVIOR | `target polarity mismatch: boundary claim corrected no decisive case` |

   The second one is the sharper result: it passed STRUCTURAL and HARMLESSNESS
   and was stopped only at BEHAVIOR, which is the gate that checks whether the
   intervention *did* anything. An audit that had certified on the first two
   gates alone would have proven nothing.
3. **HARMLESSNESS refused 4 of attempt 4's 8 candidates** for breaking the
   protection set,
   including the two that would have looked like a win on the benefit set. The
   anti-compensation invariant held on real data.
4. **STRUCTURAL refused 4 of attempt 4's 8 candidates**, all four from `FAM-loc-`
   families that a detection-shaped claim cannot legitimately treat — and then
   **0 of attempt 5's 5**, which is the measured effect of fixing the producer
   (§9).
5. **The instrument was never the limit**: `instrument_limited: false`,
   `blocked_families: []`, and zero unresolved rows across 60 patrol evaluations
   in attempt 4 and 60 in attempt 5.
6. **The proposer improved across cycles** in response to its own recorded
   rejections (sentinel harm 2 → 1; fallback → LLM).
7. **The empty registry was never bypassed.** `active_after_cycle: []` in all
   three cycles, so no unearned admission entered the registry and no version
   record was written.

*Not demonstrated*: no intervention was admitted, so there is no measured
self-improvement. `active: []` in the final experiment report, `registry_state
.interventions: {}`, and the version record was never advanced.

### The out-of-sample read

`mini_effect` ran on the 12-case validation set, which is **disjoint from the
pool and never seen by any gate**:

```
paired_replay: true
marginal_cases: 0   new_errors: 0   unresolved_added: 0
baseline_accuracy: 0.583 (tp=2 fp=1 fn=4 tn=5)   active_accuracy: 0.583
flips: {F->F: 9, T->T: 3}
passed: false
```

Both arms are identical because the registry is empty, so this is not an effect
claim — it is a **detector reading on held-out data**: 58.3% all-case accuracy,
positive recall 0.33, positive precision 0.67, zero unresolved. That is the
honest baseline a future admitted candidate would have to beat, and it is
noticeably weaker than the 70–80% the patrol wave shows, which is expected: the
patrol pool is drawn from cases the split already stratified, while the
validation set was never used to select anything.

The reported `passed: false` is a comparison of the empty registry against
itself and therefore carries no meaning about any mechanism. It is included
because it is in the artifact, not because it says anything about the loop.

### Final ledger

`ledger_integrity: {valid: true, records: 38, broken_at: null}` — the hash chain
verifies end to end across three completed cycles.

The honest one-line reading: **this run shows the loop correctly refusing to
call nine weak candidates improvements — which is the property that makes an
eventual positive result meaningful, and is not itself that positive result.**

Two framework defects block a *fair* test of the remaining objectives, both
found by this run and recorded in [known-defects.md](known-defects.md):

- **`target_kind` is never taught to the proposer** (§5), so no localization
  candidate can ever pass STRUCTURAL. The localization objective was not
  actually measured in this run — its candidates failed on a schema mismatch,
  not on merit. Fixing this is the single highest-value change before any next
  round.
- **A truncated proposer reply skips the configured repair call** (§3), which
  silently downgrades a cycle to the deterministic fallback.

Neither was patched mid-run: both live in files that `source_digest()` hashes
into the cycle contract, and editing them would have invalidated the very run
producing this evidence.


## 11. Where a positive result would come from next

> **Status note.** This section was written after attempt 4 and is superseded by
> [experiment-log.md](experiment-log.md) §8. Item 1 below was **done** and
> attempt 5 ran on it; item 3 turned out to be unnecessary. Item 2 remains the
> open question. The original text is kept for the record with its outcome noted.

1. ~~**Teach `target_kind`** so the localization families become testable.~~
   **Done.** Attempt 5 did this and it worked as expected: 0 of 5 candidates
   were refused at STRUCTURAL, against 4 of 8 in attempt 4, and the objective
   was measured (`localizable_n=11`) for the first time. What it revealed is
   that the route is inert — the tracer ignores a text boundary in both
   directions — so the fix converted an unmeasurable objective into a measurably
   inert one, not into a gain.
2. **Widen the gain set's recall opportunity.** `gain_opportunity.recall = 1`
   means the entire recall objective rests on a single case, against a
   protection cost of 1–2. A larger gain set with more judge-negative true
   positives would let a partially selective trigger show a measurable marginal
   gain instead of a 1-for-1 trade. **Still open, and now the main one.**
3. ~~**Let the proposer use the mechanism signature.**~~ **Unnecessary.** This
   was based on the deterministic fallback's over-broad packet-feature rule. The
   LLM proposer engaged on its own in attempts 4 and 5 without any change to
   that rule, and in attempt 5 it drafted every candidate itself.

A fourth item came out of attempt 5, recorded in
[experiment-log.md](experiment-log.md) §8: the localization objective has
exactly one legal route and that route does not work, so either the action space
needs a route that can move a named step, or this objective should be reported
as a capability limit rather than pursued further on this corpus.
