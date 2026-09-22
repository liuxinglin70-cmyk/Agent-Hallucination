# Why the localization route can be admitted, and the recall route could not

Written before attempt 5, so that whatever it produces is read against a stated
expectation rather than a story fitted afterwards.

## The asymmetry, from the gates themselves

The protection set is checked by `harmlessness_gate`, which counts **detection**
transitions: `new_fn`, `new_fp`, `new_errors`, `unresolved_added`.

| candidate kind | what it changes | effect on the protection set |
|---|---|---|
| `recall_trigger` (the FN route) | forces the recall net on judge-negative cases → can **turn a conviction on** | a protected clean case becomes an FP, i.e. `new_fp ≥ 1` → HARMLESSNESS fails |
| `tracer.system` (the localization route) | changes **which step is named**; the conviction is untouched | `new_fn = new_fp = 0` by construction → HARMLESSNESS passes |

Verified offline: a tracer directive that moved the named step on every
sentinel case, changing no verdict, passed HARMLESSNESS with an all-zero
detection delta. That is not a loophole — it is the gate measuring the right
thing. The harm a localization candidate can do is breaking a localization the
control already had right, and that is guarded separately by
`localization_behavior_gate`'s `joint_broken`, plus `meets_localization`'s
`localization_joint_broken_allowed: 0`.

This explains the shape of attempt 4 without appeal to bad luck: the FN family
had `gain_opportunity.recall = 1` while every recall candidate cost 1–2
protected cases. The trade could never clear a zero-tolerance invariant. The
localization objective has `improvable = 2` on the same gain set and a
protection cost of zero by construction.

## What attempt 5 can and cannot show

`localization_opportunity` on the gain set, measured from attempt 4's control
rows:

```
improvable: 2   preserved: 2   unannotated_positive: 0
```

| case | control step | gold step | reading |
|---|---|---|---|
| OpenManus/074 | 2 | 1 | improvable |
| Octotools/022 | 4 | 3 | improvable |
| OpenManus/003 | 1 | 1 | hit — must be preserved |
| SmolAgents/039 | 2 | 2 | hit — must be preserved |

**A caveat I have to state, because it bounds what a "gain" would mean.** The
two improvable cases are not detector defects. Both are adjacent-step editorial
disagreements, and the annotation's own reason text shows it:

- **Octotools/022** — gold 3 blames the *choice of method* ("treated the
  problem as a generic lower-bound estimation"), the detector names 4, where
  the false answer is delivered. Both are defensible readings of "responsible".
- **OpenManus/074** — gold 1 is the human's `ask_human` reply that supplied the
  wrong state; the detector names 2, the agent adopting it. The detector's
  reading is arguably *more* correct for a question about agent hallucination,
  and the gold reason itself says "the agent hallucinated by wrongly replacing
  the original normalized quantum state with a new, unnormalized one" while
  locating that at the step where the human introduced it.

So a tracer directive that moves these by one step would be **fitting an
editorial convention, not repairing a defect**. If attempt 5 admits such a
candidate, the marginal gain is real by the metric's definition and I will
report it as measured — but I will also report that the underlying annotation is
ambiguous, because a reader would otherwise take it for a detector fix. The
alternative reading, that the injection of the wrong value is the responsible
step even when a human supplied it, is at least as defensible; the honest
position is that this benchmark's step labels do not settle it.

**And here is why I still expect `joint_broken` to decide attempt 5.** The
gate's stated intent is to name "the earliest step that introduced the error".
On both improvable cases the detector already names a *later* step than gold, so
a directive that pushes earlier could convert them — while on the two hits it
could push the step past the annotated one and break them. The candidate has to
move four cases in the right direction with none broken, on a family whose
members were themselves classified `needs_review` as annotation ambiguity. That
is a narrow target, and a `joint_delta = 1` with `joint_broken = 0` is the
plausible best case, not a guaranteed one.

## The honest prediction

Attempt 5 converts the localization objective from **unmeasurable** (every
candidate refused at STRUCTURAL on a schema mismatch, so the objective was never
scored) to **measured**. Whether the measurement is positive depends on the
tracer responding to a directive in the intended direction on a majority of
annotated positives.

If it admits: the mechanism is shown to produce, gate and deploy a change, and
I will report the marginal gain together with the annotation caveat above.

If it rejects with `joint_broken ≥ 1`: the gate refused a candidate that broke a
correct localization, which is a correct refusal and a decidable result.

If it rejects with `joint_delta = 0`: the tracer did not move the named step in
the directed way, which is a finding about the tracer's determinism against a
prompt-level directive — also decidable, and the most likely outcome given that
the same tracer produced identical steps across attempts 2–4.

In none of those cases will a threshold move.
