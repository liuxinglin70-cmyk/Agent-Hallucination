# SEHD v6 Technical Design

## Purpose

SEHD v6 is a research prototype that treats PACA-TCL v3.5 verdicts as the
runtime authority and adds a type-conditioned evolution layer around them.
Configuration, role manifests and provider routes are frozen for a registered
execution, while the injected strategy genome is intentionally mutable. The
design follows a two-step research protocol:

The reader-oriented companion guide
[EXPERIMENT_FRAMEWORK_GUIDE.md](EXPERIMENT_FRAMEWORK_GUIDE.md) connects this
design to the concrete CLI, result fields, dataset roles and claim boundaries.

1. Extract reusable trajectory features and classify the failure before editing.
2. Evolve only the skill route supported by both the failure attribution and
   the classified type, then validate the edit against the authoritative
   detector output.

The classification layer is not allowed to overwrite the proof court's final
state. It controls experience organization, mutation routing and fitness
interpretation.

## Development, Probe and Final-Holdout Protocol

The v3 partition has three semantic roles:

1. `development_pool`: every non-final case. These cases may be reused for
   evolution, source replay and experience memory across iterations.
2. `gold_probe`: a fixed small subset of `development_pool`, disjoint from the
   mutation-source list. It is opened only on the registered
   `probe_interval`/`probe_budget` schedule as a direction or rollback gate.
   Repeated access makes it development evidence, not generalization evidence.
3. `final_holdout` (legacy alias: `outer`): disjoint, sealed and read-only. It
   is opened at experiment end, or under equivalent external custody, and is
   the only role that can enter the detector-improvement claim gate.

`probe_size`, `holdout_size` and `seed` make the split reproducible. New
manifests are `development_pool_ids.json`, `probe_set_ids.json` and
`final_holdout_ids.json`; old `stratified_evolution_set.json`,
`validation_set_ids.json`, `test_set_ids.json` and `outer_set_ids.json` remain
read-compatible. Validation/test are optional compatibility views, not
mandatory permanently isolated data silos. The run contract records
`development_case_count`, `probe_case_count`, `final_holdout_count`,
`probe_interval`, `probe_budget` and `holdout_sealed`.

## Detection Authority

The final detection path is:

```text
trajectory record
  -> H0: OCSE packet + layered base pass + construct census
  -> H1: qualification / propagation + focused prosecution and defense
  -> H2: calibrated appeal + informed defense + consensus gate
  -> H3: counterfactual localization
  -> verified_error | verified_clean | unresolved
```

`H0` is a descriptive name for the existing pre-H1 base phase, not an
additional configured court stage. The occurrence ledger records what each
candidate did across the stages. The final `occurrence_state` is projected
from that ledger only while `decision_authority` is valid; invalid authority
forces `unresolved`. A formally scored case must also have `runtime_valid=true`.

The proof court is the source of truth for the three-state verdict. CRS is a
diagnostic projection used by BCCSE attribution and must not be treated as an
independent verdict. The following invariants are required:

- A certificate must use addressable event or interaction references.
- An invalid certificate is quarantined and cannot support a clean or appeal
  decision.
- `supported_or_correct` is mechanism-limited and requires related evidence.
- Appeal positives require admissible independent initial support.
- Invalid counterfactual rankings fall back to deterministic localization.
- Unresolved is a first-class outcome. Canonical three-state and selective
  metrics never call a negative unresolved case clean. A labelled legacy
  binary-compatibility view still collapses unresolved to the negative class;
  its TN therefore includes negative unresolved and must not be cited as
  decisive-clean accuracy.

The v2 layered path normalizes both `origin_event_id` and the runtime
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

The classifier combines runtime candidate structure, normalized CRS
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

Cross-run evolution explicitly loads both artifacts: `--initial-genome`
restores the audited rule lineage and `--experience-memory` restores typed
success and failure observations. Loading only memory does not silently mutate
the registered baseline genome.

## Fitness and Promotion

The promotion hierarchy is:

1. Structural validation rejects unsafe edits.
2. Source replay compares before and after on the triggering case.
3. A rolling set of previously stable evolution cases must preserve its
   decisive state, runtime validity and non-triggered process status before an
   edit can be committed.
4. Gold mode may commit explicit `strong_improvement` or `local_improvement`
   as a development candidate after source/canary checks; those labels are
   still not generalization evidence. In no-gold mode, detector-local score
   gains and `directional_evidence` may enter a reversible
   `provisional_label_free` working candidate only after canary
   non-regression. Such rules keep `fitness_status=none`, are not protected as
   strong/weak, and cannot create an accuracy or champion claim. A periodic
   gold probe can veto/rollback but cannot promote a provisional candidate.
5. `no_effect` is not silently committed.
6. Cross-set effectiveness is evaluated only when the frozen config explicitly
   authorizes the D/effectiveness phase and the registered set.

Candidate search is bounded separately from promotion. The default source
budget is two mutation attempts (maximum three). A repairable structural
failure or source-level no-effect result restores the source's pre-attempt
convergence checkpoint and may retry from the unchanged parent genome. Replay
emits categorical observable deltas (state/trigger, coverage, candidate and
certificate counts, quarantine, causal fields and responsible-step presence)
into negative experience. Attempts never share fitness credit.

Evidence-obligation local fitness rewards the weaker side of the prosecution
and defense coverage, rather than rewarding a large coverage gap. This avoids
turning one-sided adjudication into a false improvement signal.

Canary re-evaluation uses previously stable development cases and is a veto
against catastrophic forgetting. A separately scheduled gold probe may steer
or roll back development, but does not establish generalization. Only the
sealed final holdout is the final cross-set check; stable canaries establish
local non-regression. The current
`P0_diagnostic_only` config has `D_execution_authorized=false` and therefore
cannot enter that effectiveness path.

Champion eligibility is narrower than development checking. Probe and legacy
validation/test runs are never champion-eligible. The only eligible role is
`--set final_holdout` (or legacy `--set outer`),
which additionally requires `outer_execution_authorized=true` and one explicit
`final_holdout_ids.json` (or legacy `outer_set_ids.json`) bound to the loaded
partition and per-case final role assignments. IDs must be real loadable
`source/id` values; reverse lookup
from case references and dataset scanning are forbidden.

Role isolation and hash binding do not provide secrecy or one-shot custody.
An Outer run may execute without a custody attestation for diagnostic auditing,
but `outer_custody_verified=true` is required for champion eligibility. This
flag represents an external governance assertion that Outer did not participate
in development and was opened under the final-evaluation policy; code cannot
establish that history on its own.

Formal evolution uses one edit per shadow batch. Combined multi-edit replay is
not attributed back to individual rules because that would create invalid
fitness credit and experience records.

## Dry-Run Wiring Exercise

`src/sehd_v6/dry_run_5.py` supports two modes:

- no mutation environment: deterministic local mutation plumbing;
- configured mutation API: one proposal per case, structural validation,
  one repair request when needed, then deterministic safe fallback if the API
  proposal remains invalid.

All active provider routes use the dependency-free resolver in
`src/sehd_v6/provider_routes.py`. It loads the repository `.env` without
overriding process variables, then applies route-level (`SEHD_ROUTE_*_*`),
purpose-level (`SEHD_MUTATION_*`, `SEHD_DETECTOR_*`, `SEHD_JUDGE_*`,
`SEHD_EMBEDDING_*`) and global `SEHD_PROVIDER_*` overrides before falling back
to a legacy config route or the official DeepSeek V4-Flash route. Only the
name of the key environment variable is retained; the credential is read at
the HTTP boundary and is never written to source, prompts, result files or
logs. Explicit non-official proxy routes fail closed when their selected key
is absent and never silently substitute `DEEPSEEK_API_KEY`.

One recorded configured DeepSeek exercise used `deepseek-v4-flash` and
completed 5/5 executions. Four cases created typed experience entries
(`factual`, `contextual`, `omission`, `reasoning`). This is plumbing evidence,
not validation evidence. Effective coverage remained 0% because the exercise
did not establish detector improvement.

## Metrics, Claims and Provenance

Evaluation resolves state in this order: top-level `occurrence_state`, proof
or occurrence-ledger fields, structured `incident_state.occurrence_state`, and
only then legacy `incident_state`/`state`. Artifacts contain:

- three-state confusion and a separately labelled binary-compatibility view;
- decision coverage, selective accuracy/risk, and class-conditional unresolved
  rates;
- the paired baseline→skill transition table and a dependency-free exact
  McNemar test;
- a 95% Wilson interval on the discordant-pair skill-win probability,
  transformed to paired net correctness gain over all paired cases. The
  artifact records both denominators and the conditioning assumption; fewer
  than 20 paired cases is marked `insufficient_sample` and cannot promote;
- hashes/provenance for available code, config, partition, case manifest and
  sequence, case records, genome, provider configuration and caches.

Missing critical provenance is recorded as `not_reproducible`; no placeholder
hash is generated. Detector-improvement and champion labels are ineligible for
abstention-only migration, absence of significant paired correctness gain,
missing/non-positive confidence intervals, or missing sealed final-holdout
evaluation. Probe metrics can be used only for online direction/rollback.

Final-holdout/Outer evaluation is read-only with respect to the genome, mutation system,
experience memory and champion state. Its artifact records
`role_isolated_final_holdout_read_only`. The implementation has no durable
cross-process use registry and cannot prove secrecy, operator blindness or
single prior use; custody and one-shot enforcement require external
artifact/authorization audit.

## Current Evidence Boundaries

- The repository contains external and archived test trees; unrestricted
  `pytest` collection is not a valid SEHD-only test command.
- Historical champion files created under incompatible hash and rule contracts
  are rejected by strict lineage loading rather than silently migrated.
- Type classification is a control and observability layer for evolution; it
  does not replace proof-court evidence validation.
- Historical dry-runs and inner-set artifacts do not establish generalization,
  production readiness, or an Outer-set detector improvement.
- Formal evaluation fails closed unless stage authorization and complete role
  isolation are established before provider calls.
- Existing artifacts with `outer_exposure=false`, zero Outer cases or no
  hash-bound real-ID Outer manifest remain Inner evidence and are not upgraded
  by this protocol.
