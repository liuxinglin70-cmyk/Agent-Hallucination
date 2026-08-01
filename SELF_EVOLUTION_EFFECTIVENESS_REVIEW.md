# Self-evolution effectiveness review

## Conclusion

The repeated rejection pattern was not caused by a single overly strict
threshold. The structural gate is mostly enforcing valid safety invariants.
The dominant failure was an incomplete evidence and learning loop:

1. Label-free source replay can only produce `directional_evidence`.
2. Such evidence requires an independent stable canary before it may enter the
   reversible working genome.
3. The CLI had a stable-canary selector, but never passed selected canaries to
   `run_online_evolution`.
4. An all-triggered evolution sample therefore had no possible acceptance path.
5. Rejected edits then lost their actual rejection stage and reason before the
   next reflection, so later proposals could not learn whether the failure was
   structural, ineffective, regressive, or a runtime error.

Lowering semantic-deduplication, rule-shape, leakage, or canary regression
thresholds would mask this control-flow defect and admit unsafe or redundant
rules. Those thresholds are therefore unchanged.

## Changes

- Reserve stable development cases outside the mutation-source set and pass
  them to the evolution runner as independent canaries.
- Revalidate every reserved canary under the initial genome. A label-free run
  fails before mutation calls if none remains stable, instead of spending the
  budget on candidates that cannot be admitted.
- Keep mutation sources, canaries, probes, and final holdout roles disjoint.
- Preserve rejection stage, normalized reason categories, replay budget, and
  observed effect in negative experience memory.
- Feed safe reason categories back into reflection. Case identifiers and echoed
  rule text are stripped before prompt construction.
- Require the mutator to state a bounded counterfactual `expected_effect` when
  it can. Source replay records whether the effect was observed, absent, or
  contradicted; this remains telemetry and never self-certifies promotion.
- Emit `rejection_summary` grouped by stage, reason, and gate decision, and
  separate detector-call from mutation-call accounting.

## Acceptance semantics after the change

- Structural legality is necessary but not sufficient.
- Gold-assisted source improvement may become a development candidate.
- Label-free directional improvement may only become an unprotected,
  reversible provisional rule when independent canaries are preserved.
- No-effect edits remain rejected, but now teach the next reflection that the
  proposed mechanism had no observable effect.
- Canary regression and runtime-invalid results remain fail-closed.
- No development result becomes a final champion without the existing probe,
  holdout, provenance, and statistical claim gates.

## Verification

The project test suite passes with 72 tests, including regression coverage for
independent canary admission, fail-fast behavior for unstable canaries,
counterfactual telemetry sanitization, and actionable rejection feedback.
