# The localization objective was measured, and the tracer ignores directives

Attempt 5 cycle 1 is the first cycle in the framework's history that measured
the localization objective instead of refusing the candidate on a schema
mismatch. Its verdict is negative and decidable. This note records exactly what
was measured, because the distinction between "the gate is broken", "the
mechanism cannot work here" and "the producer cannot express it" decides what to
do next.

## What changed, and the proof it changed

`IV-1-001` — drafted by the **LLM proposer**, not the fallback — declared:

```json
{"expected_behavior": "…the tracer names the final delivery/synthesis step…",
 "target_kind": "localization",
 "target_polarity": "boundary"}
```

and passed STRUCTURAL. Before the fix, `target_kind` was absent from every
schema, `candidate_target_kind` returned `"detection"`, and the same candidate
would have been refused with *"localization failure family requires target_kind
localization or joint, got 'detection'"* — exactly the reason 4 of attempt 4's 9
candidates died without ever being scored.

## The four gate layers, on real data

| layer | verdict | reading |
|---|---|---|
| STRUCTURAL | **passed** | jaccard against active set 0.0181 |
| HARMLESSNESS | **passed** | detection delta all zero: `new_fn=0 new_fp=0 new_errors=0 unresolved_added=0` |
| localization BEHAVIOR | **failed** | `localizable_n=11, joint_improved=0, joint_broken=0` |
| localization M | not run | BEHAVIOR failed first |

The HARMLESSNESS result is worth stating plainly, because it is the structural
advantage the localization route was chosen for: **a tracer directive cannot
break the protection set.** It leaves every conviction alone, so the detection
delta is identically zero and the gate that rejected all seven recall candidates
in attempt 4 passes trivially. The harm a localization candidate can do is
breaking a localization, and that is what `joint_broken` measures.

## Why it failed: the directive fired and was ignored

This is the load-bearing check. A `joint_delta = 0` could mean three very
different things, so each was ruled out:

| hypothesis | test | result |
|---|---|---|
| the candidate never fired | `fired_rate` over its 20 rows | **0.5** — `text_injected` recorded on 10 cases, with `hook=tracer.system` |
| the tracer was frozen-replayed, so it *could not* respond | `freeze_tracer = "tracer.system" not in hooks` | **False** — the hook is in `hooks`, so the tracer stayed live; only `judge_result` was frozen |
| the tracer ran but under a cached prompt | compare the two arms' tracer provider calls | **genuinely fresh**: different `completion_tokens` (4,585 baseline vs 2,339 candidate) and different raw formatting for the same case, both concluding step 4 |

So the directive text was appended to a live tracer prompt, the tracer was
re-invoked, and it named the same step. For `Octotools/022` it returned step 4
in both arms while gold is 3, with a quote of the delivered `Answer: 1982`.

**The finding is about the mechanism, not the instrument.** A prompt-level
directive does not override the tracer's evidence-based reading of which step
delivered the error. That is a decidable statement about the `tracer_directive`
route, and it is the outcome pre-registered in
[localization-route.md](localization-route.md) as the most likely one.

## The family is directionally learnable, so this is not a labelling lottery

If mislocations went both ways, no single directive could fix them and the null
would be uninformative. Measured over every convicted-and-annotated case in
attempt 5's control arm:

| direction | count |
|---|---|
| detector names a **later** step than gold | **15** |
| exact hit | 6 |
| detector names an **earlier** step than gold | **1** |

15 of 16 mislocations are LATER, and all 4 members of the patrol family
(`Octotools/009` +1, `OpenDeepSearch/079` +2, `Octotools/031` +1,
`Magentic_One/061` +42) are later. So "name the earliest step that introduces
the error" is the correct direction for this family, and it is also what the
deterministic fallback directive already says. The candidate that was gated took
the *opposite* direction — its claim asks for the final delivery step, which
would push later still. The tracer ignored it either way, so the direction of
the claim was not what decided the verdict; but it does mean the LLM proposer
read the family backwards, which the next cycle's rejection feedback now
corrects with a concrete `joint_delta`.

## Cycle 2 falsified the "wrong direction" explanation

Cycle 1's candidate asked for the *final delivery* step, which is backwards, so
"the proposer read the family backwards" was a live explanation for the inert
result. Cycle 2 removed it: the proposer received cycle 1's rejection and
drafted **both** directions.

| candidate | direction it asked for |
|---|---|
| `IV-2-001` | the **final synthesis event** ("the last answer-tool interaction, or the last prose event") |
| `IV-2-002` | the **originating mid-trajectory event** ("names that originating event as the responsible step rather than the delivery") |

Both passed STRUCTURAL and HARMLESSNESS, and both were rejected identically:

```
localization claim but no responsible step was corrected
(joint_improved=0, joint_broken=0)
```

**A directive in either direction moves nothing.** That closes the question: the
tracer's named step is insensitive to a `tracer.system` text boundary. It is not
that the model was told the wrong thing; it is that this action space has no
lever over the step tracer's conclusion.

### The trap this avoided, and the proof it is real

"Insensitive" would be a wrong conclusion if `responsible_step` were fixed
before the tracer ran — then no tracer change could ever surface, and the null
would be an artifact. Two checks rule that out:

1. **`responsible_step` does track `tracer.step`.** Across all three arms the two
   agree wherever the tracer produced a step, and `adjudication.py` leaves
   `responsible_step` `None` where the tracer did not — so the recorded step is
   the tracer's own output, not a value pinned upstream.
2. **The directive demonstrably reached the tracer and changed its work.** For
   `Camel/029` the same case produced the same step 7 under three different
   prompts, with materially different deliberation:

   | arm | tracer step | completion tokens | its own reason |
   |---|---|---|---|
   | control | 7 | 1,450 | "Step 7 is the first assistant-authored assertion of 1939 as the answer" |
   | IV-2-001 (final-delivery directive) | 7 | 2,054 | "Event_007 is the final synthesis event where the delivered answer first…" |
   | IV-2-002 (originating-event directive) | 7 | **29,690** | "event_007 is the first assistant step to assert 1939 as the publicatio…" |

   The candidate-arm tracers are visibly reasoning under the injected text —
   29,690 tokens against the control's 1,450 — and still land on step 7. That is
   insensitivity, not a masked change.

## What this does and does not establish

**Established.** The localization objective is measurable end-to-end on real
data; HARMLESSNESS is not the obstacle for it (all three candidates passed with
an all-zero detection delta); the tracer route fires on real cases; the tracer
is insensitive to a directive in *both* directions; and the mislocation
direction is consistent enough that a *responsive* tracer could be steered.

**Not established.** Whether any intervention could move the step. The step
tracer is an LLM call whose conclusion follows from the delivered result and its
own prescribed backwards walk, and a directive is the only lever
`classify_family_actionability` offers for a `FAM-loc-` family — it returns
`["tracer_directive"]` and nothing else, because the conviction is already
correct and no guard or evidence row can change which step is named.

That is the honest framing of the remaining gap: this is a **capability
limitation of the action space on this corpus**, not a bug. The route exists, is
legal, fires, and has no measurable effect. The framework's contribution is that
it refused to admit three inert candidates — which is correct, and is the
reason a future positive result would mean something.

## Why the tracer is right to ignore it

`TRACER_SYSTEM` already instructs exactly what the directives asked for:

> Your only job is to find the EARLIEST step that introduced the error. …
> Walk backwards from the delivered result. At each earlier step ask: is the
> wrong content already present here? Stop at the first step where it is.

The tracer is following a more specific law than any proposer can usefully
restate, so a directive that paraphrases it adds nothing and a directive that
contradicts it is overruled by the stage's own contract. That is the mechanism,
stated plainly.
