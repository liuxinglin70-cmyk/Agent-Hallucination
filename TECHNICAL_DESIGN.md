# SEHD v6 Technical Design

## Purpose

SEHD v6 keeps PACA-TCL v3.5 as the authoritative detector and adds a
type-conditioned evolution layer around it. The design follows a two-step
research protocol:

1. Extract reusable trajectory features and classify the failure before editing.
2. Evolve only the skill route supported by both the failure attribution and
   the classified type, then validate the edit against the authoritative
   detector output.

The classification layer is not allowed to overwrite the proof court's final
state. It controls experience organization, mutation routing and fitness
interpretation.

## Detection Authority

The final detection path is:

```text
trajectory record
  -> OCSE packet
  -> candidate census
  -> qualification / propagation
  -> focused prosecution and defense
  -> proof court
  -> appeal consensus gate
  -> counterfactual localization
  -> verified_error | verified_clean | unresolved
```

The proof court is the source of truth for the three-state verdict. CRS is a
diagnostic projection used by BCCSE attribution and must not be treated as an
independent verdict. The following invariants are required:

- A certificate must use addressable event or interaction references.
- An invalid certificate is quarantined and cannot support a clean or appeal
  decision.
- `supported_or_correct` is mechanism-limited and requires related evidence.
- Appeal positives require admissible independent initial support.
- Invalid counterfactual rankings fall back to deterministic localization.
- Unresolved is reported separately and is fail-closed in official binary
  metrics: positive unresolved contributes FN and negative unresolved
  contributes TN.

The v2 layered path normalizes both `origin_event_id` and the production
`origin_hypothesis_event_id` before propagation validation. This prevents a
valid candidate from becoming unresolved solely because of an internal field
name mismatch.

## Trajectory Classification

`HallucinationTypeClassifier.extract_features()` creates the versioned,
label-free `TrajectoryFeatures` object:

```text
htc_tes.trajectory_features.v1
```

The object records:

- candidate count, candidate kinds and axes;
- module tags and claim/action/omission/adoption indicators;
- prosecution and defense coverage states;
- detector state and CRS provenance.

This is deliberately a feature description, not a copy of the dataset label.
Gold can be used in explicitly gold-enhanced experiments for evaluation and
direction checks, but it must not enter runtime detector prompts in
self-supervised mode.

The current taxonomy is:

```text
factual, instructional, tool, reasoning,
omission, adoption, contextual, unknown
```

The classifier combines production candidate structure, normalized CRS
signals, attribution and module evidence. Unknown or unavailable attribution
does not override informative trajectory features.

## Type-Conditioned Evolution

The type route is a hard safety constraint, not only prompt guidance:

```text
editable_modules = type_route(type) ∩ attribution_route(signal)
```

If the intersection is empty, the case is recorded as `skip_route_conflict`
and no mutation is generated. This prevents an LLM from using a generic type
prompt to edit an unrelated module.

The self-supervised direction for unresolved cases is
`unresolved_to_decisive`, not `unresolved_to_error`. Without gold there is no
authority to choose clean or error polarity. Decisive cases use
`process_repair` when they still provide an internal trigger.

## Skill Structure

Every evolved `SkillEdit` contains:

- `target_module`
- `operation`
- `new_content`
- `rationale`
- `source_case_id`
- `feature_description`

`feature_description` stores the reusable trajectory pattern that motivated
the rule, such as:

```text
candidate origin addressability and proof completeness
```

It is serialized into genome lineage and type experience memory. It is
metadata for generalization and auditability; it does not directly set the
proof-court verdict.

Runtime rules remain constrained by the structural gate:

- observable `if/when -> one action` form;
- no dataset labels, case-specific identifiers or internal diagnostic state;
- no forbidden direct verdict outside the module permission;
- no unsafe duplicate or cross-module reference;
- no baseline mutation.

## Experience Memory

`TypeConditionedExperience` stores:

- hallucination type and evolution direction;
- target module and strategy;
- detection improvement status;
- trajectory `feature_description`;
- detector state transition and classification confidence;
- source provenance.

The runner writes both summary and full memory artifacts:

```text
htc_tes_coverage.json
htc_tes_experience_memory.json
```

The experience memory is evidence for future reflection, not an authority to
claim detector accuracy. In particular, a generated rule or a decisive state
alone does not increase effective coverage. Coverage improves only after the
fitness layer records a successful source-level outcome.

## Fitness and Promotion

The promotion hierarchy is:

1. Structural validation rejects unsafe edits.
2. Source replay compares before and after on the triggering case.
3. Only explicit `strong_improvement` or `local_improvement` is promotable.
4. `no_effect` is not silently committed.
5. Cross-case effectiveness is evaluated on the registered validation set.

Evidence-obligation local fitness rewards the weaker side of the prosecution
and defense coverage, rather than rewarding a large coverage gap. This avoids
turning one-sided adjudication into a false improvement signal.

Canary batch re-evaluation is intentionally not part of the evolution flow.
This reduces repeated detection calls and avoids treating an audit-only,
non-veto signal as a safety guarantee. Validation-set evaluation remains the
authoritative cross-case check.

## Dry-Run Protocol

`src/sehd_v6/dry_run_5.py` supports two modes:

- no mutation environment: deterministic local mutation plumbing;
- configured mutation API: one proposal per case, structural validation,
  one repair request when needed, then deterministic safe fallback if the API
  proposal remains invalid.

The API key is read only from the environment variable named by
`SEHD_MUTATION_API_KEY_ENV`. It is never written to source, prompts, result
files or logs.

The latest configured DeepSeek run used `deepseek-v4-flash` and completed 5/5
cases. Four cases created typed experience entries (`factual`, `contextual`,
`omission`, `reasoning`). Effective coverage remained 0% because the dry-run
did not establish authoritative detector improvement; this is the intended
anti-self-certification behavior.

## Known Evaluation Boundaries

- The repository contains external and archived test trees; unrestricted
  `pytest` collection is not a valid SEHD-only test command.
- Historical champion files created under incompatible hash and rule contracts
  are rejected by strict lineage loading rather than silently migrated.
- Type classification is a control and observability layer for evolution; it
  does not replace proof-court evidence validation.
