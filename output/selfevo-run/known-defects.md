# Known defects deliberately not fixed mid-run

Editing any file under `detector/` or `evolution/` changes `source_digest()`,
which is hashed into the cycle contract, the audit fingerprint and the provider
cache namespace. On 2026-09-21 that mechanism was already observed: fixing the
audit bug changed the digest from `d23c003d6a90791b` to `014fdb6135ef7318` and
invalidated attempt 2's entire cache. Editing mid-run would do the same to the
run in flight — the honest cost of keeping derived caches tied to the code that
produced them.

So the following are recorded, not fixed while the run is live.

## 1. The per-case progress log is never written during a journaled cycle

`EVOLUTION_PROGRESS_LOG` is opened inside `DetectionBackend.evaluate`'s
parallel branch:

```python
if int(workers) > 1 and len(cases) > 1:
    ...
    progress_log = os.environ.get("EVOLUTION_PROGRESS_LOG")
```

But `CycleJournal.evaluate` drives every case through that path one at a time
with a forced single worker:

```python
result = evaluate([case], **{**kwargs, "workers": 1})
```

`workers == 1` and `len(cases) == 1`, so the branch never runs and
`artifacts/selfevo-bigrun4/progress.jsonl` is never created. That is why
attempts 3 and 4 have no such file despite the env var being set.

**Impact: none on any measurement.** The writer's own docstring already says
this: *"It is a log, never an input: nothing downstream reads this file, so a
truncated or missing line changes no measurement."* Nothing in the engine, the
gates or the report reads it.

**Impact on my dashboard: reduced, not broken.** `progress_dashboard.py` reads
three sources and the missing one is the least important:

| source | status |
|---|---|
| checkpoint arm rows | present — this is what the per-case table and confusion counts use |
| ledger | present |
| cycle reports | present once a cycle finishes |
| progress log | **absent** (this defect) |

Liveness therefore comes from `psutil` process inspection plus the checkpoint
rows, which is exactly what the "是否在正常运行" panel needed and why the panel
still works. A fix would move the log write outside the parallel branch (it is
per-case, not per-batch), and should be done between runs.

## 2. The deterministic fallback's recall_trigger hardcodes its trigger feature

Found by reading the first two real proposals this framework ever produced
(cycle 1, `proposed` ledger event). `HybridProposer._recall_trigger_candidate`
takes the discriminating packet feature as a parameter and uses it correctly in
the predicate program, but hardcodes the *trigger* filter:

```python
return {
    ...
    "trigger": {
        "packet_features": ["has_tool_calls"],   # <-- same for every candidate
        "facts": ["judge_positive=false"],
    },
    "body": {
        "predicate": name,
        "predicate_definition": program,          # <-- uses `feature` correctly
        ...
    },
}
```

so a candidate generated for any other feature is internally inconsistent:

| `feature` argument | predicate rule value | trigger packet_features | consistent |
|---|---|---|---|
| `has_tool_calls` | `has_tool_calls` | `["has_tool_calls"]` | yes |
| `question_has_numbers` | `question_has_numbers` | `["has_tool_calls"]` | **no** |
| `some_other_feature` | `some_other_feature` | `["has_tool_calls"]` | **no** |

The real cycle-1 candidates show exactly this: `IV-1-evo_recall_question_has_numbers`
carries predicate rule `packet_features any_equals "question_has_numbers"` beside
trigger `packet_features: ["has_tool_calls"]`.

**Impact.** The trigger decides which packets the intervention is evaluated on,
so this silently narrows every candidate except the first: it fires only where
`has_tool_calls` holds *and* the named feature holds. For its own family that is
harmless, because the feature was drawn from the members' *shared* packet
features — so every member satisfies both. The harm is on the gain set and any
case outside the family, where the candidate fails to fire on cases its own name
and claim say it should, which can turn a real repair into a no-op.

**Why STRUCTURAL does not catch it.** Its recall-trigger checks require the
predicate to emit a trigger verdict and to be unanimously violated across the
family (`evolution/gates.py`); it does not compare `trigger.packet_features`
against the predicate's rule value, so the inconsistency passes.

**Not fixed here.** Editing `evolution/proposer.py` changes `source_digest()`,
which is hashed into the running cycle's contract and would invalidate it. The
fix is one line — pass `"packet_features": [feature]` — plus a STRUCTURAL check
that the trigger feature appears in the predicate's rules, and it should land
between runs.

## 3. A truncated proposer reply never reaches the repair loop

Cycle 1's only substantive proposal path was the **deterministic fallback**: the
LLM proposer returned `truncated_output` and produced no candidates, so
`HybridProposer` supplied typed-DSL templates instead. Those templates are what
HARMLESSNESS then rejected (`new_fp=2` and `new_fp=1` on the sentinel).

That matters because a repair call exists for exactly this class of failure and
never fired. The trigger is:

```python
mechanical_rejections = [
    note for note in notes
    if note.get("rejected") in {
        "unknown_failure_family", "invalid_contract", "echo_of_active",
        "duplicate_in_batch", "echo_of_rejected", "missing_evidence_partner",
        "missing_terminal_partner", "echo_of_rejected_combination",
    }
]
if (not batch.candidates and mechanical_rejections and repairs < self.max_repair_calls ...):
```

but the truncation branch returns a note with a different shape:

```python
return ProposalBatch(notes=[{
    "generator": "llm", "provider_called": True, "provider_ok": False,
    "error_type": exc.error_type, "attempts": exc.attempts,
}])            # <- no "rejected" key at all
```

so `mechanical_rejections` is empty, the condition is false, and the repair
call is skipped. `max_repair_calls: 1` is configured in the run config and was
never spent.

**Impact.** A provider-side truncation — the failure the 4096 → 32768 ceiling
raise already fixed once, and which is inherently stochastic — silently
downgrades the cycle from the LLM proposer to the weaker deterministic
templates. That is a proposer-quality regression masquerading as a proposal.
It does not touch a gate: the templates are still gated honestly, which is why
cycle 1's rejections are a legitimate result and not a defect artifact.

**Fix (for between runs).** Give the truncation note a `rejected` value the
repair loop recognises (for example `"truncated_output"` added to the trigger
set, or route provider failures through the same `rejected` vocabulary), so the
one configured repair call is actually available to a truncated reply.

## 4. The proposer labels a localization family with a detection polarity

Cycle 2 rejected two of three candidates at STRUCTURAL, and the reason is
worth separating into "the gate worked" and "the proposer mislabelled".

The two candidates were drafted from a **localization** family
(`FAM-loc-mislocated-…`) and both declared
`claim.target_polarity: "precision"`, which resolves to `target_kind:
detection`. The newly added STRUCTURAL rule refused exactly that:

```
localization failure family requires target_kind localization or joint,
got 'detection'
```

So the gate did its job — this is the check from N4.1 firing on a real
candidate for the first time, and it prevented a `FAM-loc-` family from being
treated as a detection family. `classify_family_actionability` had already
offered only `["tracer_directive"]` for that family, and the proposer picked the
right hook (`tracer.system`) while attaching the wrong claim.

**Proposer-quality issue, recorded not fixed.** A `tracer_directive` changes
which step is named and leaves the conviction alone, so a precision claim on it
is incoherent; the family's `target_kind` should drive the claim's polarity, and
the draft prompt should state the mapping. Editing `evolution/proposer.py`
mid-run would change `source_digest()` and invalidate the cycle, so this lands
between runs.

## 5. The proposer cannot express a localization claim: `target_kind` is never taught

This is the most consequential defect found, because it is a **hard blocker on
one of the three objectives**, not a quality issue.

`STRUCTURAL` requires a family's mechanism and the claim's objective to agree:

```python
if family.startswith("FAM-loc-") and target_kind not in {"localization", "joint"}:
    return _policy_fail("localization failure family requires target_kind "
                        "localization or joint, got {target_kind!r}")
```

and `target_kind` is read from the structured claim, defaulting to detection:

```python
def candidate_target_kind(candidate):
    return str((candidate.get("claim") or {}).get("target_kind") or "detection")
```

**But `target_kind` appears exactly zero times in `evolution/proposer.py`.** The
draft prompt teaches the model a schema in which every claim carries
`"target_polarity": "recall|precision|boundary"` and nothing else. The model
therefore cannot set `target_kind`, so `candidate_target_kind` returns
`"detection"` for every LLM-drafted candidate — and every candidate drawn from a
`FAM-loc-` family is rejected at STRUCTURAL. Observed on the real run:

| cycle | candidate | family kind | `target_kind` |
|---|---|---|---|
| 2 | IV-2-002, IV-2-003 | `FAM-loc-` | absent → `detection` → **STRUCTURAL fail** |
| 3 | IV-3-001, IV-3-002 | `FAM-loc-` | absent → `detection` → **STRUCTURAL fail** |

The model is clearly *trying*: cycle 3's `expected_behavior` reads "Localization:
the tracer localizes each delivered claim that conflicts with a tool observation
to the specific event id …, **jointly with detection**". That intent is written
in free prose that no gate reads, because the schema never gave it a field.

**Consequence for the experiment.** The localization objective has 2–4 member
families in every cycle and a legal action route (`tracer_directive`), and it is
still unable to produce a single admissible candidate — not because the mechanism
is wrong, but because the producer and the gate disagree about the schema. Every
localization verdict this run produced was `not_evaluable` at the STRUCTURAL
layer rather than a measurement of the mechanism.

**Fix (for between runs).** Add `target_polarity`'s sibling to the draft schema
and to the drafting instructions — `"target_kind": "detection|localization|joint"`
— with the mapping stated (`FAM-fn-`/`FAM-fp-` → `detection`; `FAM-loc-` →
`localization` or `joint`), and have the deterministic fallback set it too.
The gate should stay exactly as strict: the point is to let a correct claim be
expressed, not to accept an incoherent one.

## 6. `localization_opportunity` needs gold step annotations the loop does not carry for patrol

Not a defect, a limit worth naming: the localization objective's opportunity
count is derived from the evaluator-side `gold_step` map, which the engine
builds for the whole pool. That is the sanctioned development-side use of gold
(plan §3.2) and never reaches the detector — but it does mean a localization
family can only form on cases whose step annotation exists and is unambiguous.
The 2026-09-21 viability scan measured that most observed mislocations are
annotation ambiguity rather than a repairable defect, so this objective's
reachable rate is genuinely low. That is a property of the corpus, and the
honest thing to report rather than to work around.
