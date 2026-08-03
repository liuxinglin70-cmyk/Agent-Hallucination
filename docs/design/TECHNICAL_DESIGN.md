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

The standalone self-evolution module (`python -m src.sehd_v6.evolution`)
supports the same two wiring modes the retired `dry_run_5.py` script used:

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

## v5.8 Restructuring Proposal: Contrastive Attribution and Abstention Ladder (Pending Approval)

Implementation status: mechanism 1 (implemented as a deterministic,
provider-free dual-channel owner arbitration; the LLM cross-examination
variant remains a possible later extension), mechanism 4 (structuralized
rejection feedback, including the same-category retry cooldown), mechanism 5
(base-layer failure surfacing) and mechanism 4-localization telemetry
(`terminal_step_bias`, audit-only) are implemented and covered by regression
tests. Mechanisms 2 (contrastive replay acceptance) and 3 (procedural
abstention ladder) stay pending approval.

This section records a design proposal produced from the 2026-08-03 five-case
dry-run (`trial_5case_official_r1`) and a survey of current self-evolving
agent work. It changes only the BCCSE evolution layer; the frozen PACA-TCL
v3.5 base, CRS weights and proof-court decision rules are untouched.

### Dry-run evidence motivating the restructuring

Five gold-positive cases were evolved with `--use-gold --fitness-use-gold`
against the official DeepSeek-V4-Flash endpoint:

- 3/5 authoritative verdicts were `verified_clean` (false negatives) and 1/5
  `unresolved`; effective detection recall on the subset was 0/5.
- All 4 triggered cases routed to a narrow module set (`abstention_rule`,
  `causal_link_test`). For two FN cases the reflection LLM explicitly
  diagnosed the true root cause as candidate-generation sensitivity but
  returned `no_edit`, because the routed module set contained no module able
  to express that fix. Result: 0 accepted edits, 3 rejected, 2 no-edit.
- One forensic re-run of a runtime-invalid case later produced an
  authoritative `verified_error`, showing the base detector can detect these
  cases; the miss is a sensitivity/routing problem, not a capability ceiling.
- One base-layer contract quarantine (propagation `impact_surfaces`) produced
  `runtime_valid=false` with empty `authority_failure_reasons`: an
  observability gap, not a verdict logic error.
- Local proxy instability (`IncompleteRead`, SSL EOF) caused two transport
  failures; this is environmental and outside the framework.

### Related-work positioning

Skill-library optimizers such as SkillOpt (arXiv:2605.23904), SkillCAT
(arXiv:2606.13317) and certificate-gated self-evolution such as SEA
(arXiv:2607.00871) were already incorporated into earlier BCCSE versions
(bounded edits, structural gate, source replay, canary, rejected buffer).
The remaining gap is not gating but attribution and abstention, so this
proposal draws on three fresher lines:

- AgentDebugX / DeepDebug (arXiv:2607.18754): multi-round root-cause
  diagnosis for agent trajectories. Core insight: the step where a failure
  surfaces is rarely the step where it was planted. Method: two independent
  investigations produce candidate root steps, and a cross-examination
  converts the needle-in-a-haystack search into an evidenced two-candidate
  choice. The analogues here are the observed localization bias
  (detector-localized step 12 versus gold step 1) and the static
  error-type-to-module map that misroutes FN cases.
- Prover-Verifier Deliberation, PVD (arXiv:2605.25133): selective prediction
  where the confidence signal is procedural, not a thresholded scalar: a
  prover defends and a verifier attacks, and the Accept/Challenge/Reject
  dialogue outcome drives deferral. PACA-TCL already implements an
  adversarial court; what is missing is treating the court's procedural
  evidence (coverage states, quarantine counts, appeal balance) as a
  first-class abstention signal instead of collapsing it into a verdict.
- UniCR (arXiv:2509.01455) and the selective-prediction literature (Chow
  reject option, risk-coverage curves, AURC): heterogeneous evidence fusion
  into a calibrated correctness probability plus risk-controlled refusal.
  This supplies the audit lens for abstention policy: coverage-conditioned
  risk curves rather than full-coverage accuracy.

None of these evolves a *detector* whose runtime is an adversarial,
evidence-gated adjudication system, and none addresses the specific failure
mode observed here: an internally coherent verdict that misses the label.
The proposal below targets exactly that gap while preserving every
architectural invariant.

### Mechanism 1: Dual-pass cross-examination attribution (attribution v2, deterministic core implemented)

Adapted from AgentDebugX. Implemented form (v5.8 batch 2): a deterministic,
provider-free owner arbitration adds ONE second owner hypothesis from
label-free runtime evidence instead of calling a second LLM investigator:

```text
h_process  = omission-typed FN with a clean verdict
             -> candidate_trigger (process channel)
h_counter  = quarantined error certificates on an FN verdict
             -> evidence_obligation (verdict-vs-evidence gap channel)
             FP verdict with complete defense -> clean_defense_boundary
owner route = attribution route + arbitration hypothesis
subject to: polarity(owner) == polarity(gold_mismatch)
            attributor's owner keeps root-cause priority
            structural gate and fitness unchanged
```

The arbitration hypothesis widens the bounded route; it never replaces or
reorders the attributor's owner and never crosses the gold polarity side.
The LLM cross-examination variant (two independent investigations followed
by an evidenced two-candidate choice) remains a possible later extension.
The same batch adds localization telemetry: a verified_error localized by
the temporal fallback records a `terminal_step_bias` audit signal (the
detector-localized step disagreeing with earlier sufficient-evidence
origins), routing visibility for `causal_link_test`/`earliest_origin_tiebreak`
without changing any verdict. This unblocks modules such as
`candidate_trigger` and `evidence_obligation` for FN cases whose coverage
states prove the court, not the evidence budget, is the broken stage.

### Mechanism 2: Contrastive replay acceptance

An edit candidate is replayed not only on the triggering case but also on one
nearest stable neighbour of the same classified type. Acceptance additionally
requires a target-aligned process delta on the source and no contrast
inversion on the neighbour. This reduces single-trajectory overfitting
without weakening canary non-regression.

### Mechanism 3: Procedural abstention ladder for false-negative recovery

Grounded in PVD and UniCR: the abstention signal is procedural evidence, not
a thresholded scalar, and abstention policy is expressed as evolved rules
rather than tuned constants. FN cases whose prosecution evidence is thin
become recoverable through two sequential, individually gated edits instead
of one impossible edit:

1. an `abstention_rule` edit demotes confident-but-thin-evidence clean
   verdicts to `unresolved`. The rule conditions only on label-free
   procedural evidence (coverage states, certificate balance, quarantine
   counts), which is exactly the threshold-behaviour-as-skill requirement;
2. the resulting `unresolved_should_error` trigger routes
   `evidence_obligation` evolution on the same source.

Each rung passes the same structural gate, source replay and canary checks;
no rung may be merged or credited together. The selective risk-coverage view
(AURC-style audit of error versus abstention rate) is recorded as telemetry
for the abstention module; it steers evolution direction but never enters
the proof-court verdict and never promotes a candidate on its own.

### Mechanism 4: Structuralized rejection feedback (implemented)

Retry prompts must carry the exact structural failure category as a hard
constraint (for example `condition_overload` implies "at most 2
conditions"), and a repeated-rejection cycle on the same module and failure
category engages the module cooldown. This converts the existing rejection
buffer from context into contract.

### Mechanism 5: Base-layer failure surfacing (implemented)

Base-layer quarantine outcomes (for example propagation `impact_surfaces`
quarantine counts) must be projected into `authority_failure_reasons` so a
`runtime_valid=false` run always carries a machine-readable cause. This is
observability only and cannot change any verdict.

### Invariant compliance

- Frozen base: mechanisms 1-5 mutate only the genome, routing, retry contract
  and audit projection; CRS weights, court rules and stage structure are
  unchanged.
- Gold isolation: gold continues to select polarity and fitness credit only;
  it never enters skill content or reflection prompts.
- No self-promotion: every mechanism still requires source replay plus canary
  non-regression; directional evidence remains non-promotable.
- Fail-closed: any proposal failing polarity, structural or fitness checks is
  rejected and recorded as negative experience.

### Validation plan

1. Minimal reproduction tests for mechanisms 1, 3 and 5 before any code
   change (mutant-style tests in `tests/`).
2. Re-run the same five-case dry-run; success criterion: at least one
   accepted edit with a target-aligned process delta and unchanged canary
   states, with counters consistent.
3. Only after a green dry-run, extend to the registered 50-case fast set at
   the approved concurrency.

