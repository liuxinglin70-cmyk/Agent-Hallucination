# SEHD v6 Technical Design

## Purpose

SEHD v6 is a research prototype that treats PACA-TCL v3.5 verdicts as the
runtime authority and adds a type-conditioned evolution layer around them.
Configuration, role manifests and provider routes are frozen for a registered
execution, while the injected strategy genome is intentionally mutable. The
design follows a two-step research protocol:

The normative project charter — core objective, design
philosophy and advisor requirements that every agent must comply with — is
the "Core Objective and Design Discipline" section of the repository
[README](../../README.md); this design document only implements it.

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

The type route is advisory, not a hard safety constraint: the causal
attributor owns root-cause priority, and type classification may only
filter implausible modules from the already bounded attribution route.

```text
editable_modules = filter(type_route(type), attribution_route(signal))
```

If the intersection is empty, the bounded attribution route is used as-is
instead of discarding the case: a low-confidence type mismatch must not
silently lose an otherwise auditable edit candidate. HTC-TES therefore
functions as an experience-organisation label, a retrieval/diversity tag,
and an experiment telemetry dimension — it never decides the owner.

The self-supervised direction for unresolved cases is
`unresolved_to_decisive`, not `unresolved_to_error`. Without gold there is no
authority to choose clean or error polarity. Decisive cases use
`process_repair` when they still provide an internal trigger.

## Skill Structure

L1 rules are stored as typed English slots instead of free text. Each rule is
a `RuleSpec` with three slots:

- `condition`: one trajectory-observable situation;
- `action`: one module-local action within the owning module's charter;
- `evidence_anchor`: failure-signature bookkeeping for evolution triage. The
  anchor is never rendered into runtime prompts, so it cannot leak case
  identity into detection.

Prompt injection is a deterministic render: exactly one sentence of the form
`When <condition>, <action>.`, a pure function of the slots, which is what
identity and deduplication rely on. Slot capacity is bounded (300 characters
per condition/action slot, 240 per anchor); a condition may not encode
several situations and an action may not prescribe several steps.

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

The structural gate validates `add_rule`/`modify_rule` through the slot
contract whenever typed slots are present; the slot contract is the single
shape check because the rendered text is a construction guarantee, not a
re-guess. The free-text validation path serves only `remove_rule` callers
and historical checkpoint replay. Runtime rules remain constrained by the
gate:

- observable `if/when -> one action` form;
- no dataset labels, case-specific identifiers or internal diagnostic state;
- no forbidden direct verdict outside the module permission;
- no unsafe duplicate or cross-module reference;
- no baseline mutation.

Each of the eight modules additionally declares a charter (`module_charter`):
its pipeline stage, the provider-output surfaces it may influence, the
surfaces it must never touch, its allowed verdicts (sourced from the genome
gate, the single owner of verdict permission), and in-scope versus
out-of-scope failure examples. Charters are rendered into the reflection
prompt so proposals are constrained at generation time instead of only
rejected at the validation gate.

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

## Deterministic Failure Attribution

Attribution answers exactly one question: at which layer, on which artifact,
did the failure happen? Routing an unownable failure to some module anyway
ends in no-edit steps or no-effect replays, so the failure classifier is a
pure deterministic function of the addressable failure shape of one detection
record — no LLM, no heuristics. Every predicate test goes through the
versioned baseline registry (`vocabulary_registry.L2_REGISTRY`, re-exported as
`L2_PREDICATES` in `v6_contracts`), so the attribution vocabulary has
a single source of truth.

The classification rules run in priority order:

1. A provider artifact is missing or malformed. This is the most upstream
   failure — the adjudication layer cannot observe facts correctly if its
   inputs are broken, so artifact integrity is checked before any
   adjudication-layer pattern. Layer L1; the owning module is resolved from
   the artifact kind (candidate -> `candidate_trigger`, error certificate ->
   `evidence_obligation`, defense certificate -> `clean_defense_boundary`);
   a registered `module_hint` takes precedence over the default owner.
2. The consensus gate blocked a non-empty candidate set. Layer L2, artifact
   `consensus_gate_policy`.
3. One side claims coverage `complete` while holding zero valid certificates
   on that side (hollow complete). Layer L2, artifact
   `coverage_validity_policy`.
4. The boundary path returned `verified_clean` but the terminal state is
   `unresolved`. Layer L2, artifact `boundary_consumption_policy`.
5. Fall-through: an unresolved terminal carries no actionable shape
   (`no_actionable`); a terminal verdict that contradicts the court's own
   certificate bookkeeping is `detector_layer_out_of_scope`.

An L1 owner hypothesis additionally passes the gold polarity gate: a false
negative (gold error, verdict clean) may only be owned by modules that can
tighten error detection (`_FN_POLARITY_MODULES`), a false positive only by
modules that can relax or bound the error claim (`_FP_POLARITY_MODULES`); an
owner on the wrong polarity side is dropped. Unresolved terminals carry no
polarity and are accepted as-is.

Non-L1 attributions are never force-routed into an L1 module; they are
recorded in `non_l1_failure_attributions` as audit telemetry. L2
attributions route the case into the policy evolution loop instead.

Defect characterization follows the same surface map: boundary/terminal
conflicts and hollow-complete coverage are adjudication-policy problems and
belong to L2. The consensus gate's blocking semantics are genuinely in
question — the gate is an anti-false-positive mechanism that can also read as
over-strict — so the gate itself is not hand-adjusted; `consensus_gate_policy`
is its designated first evolution candidate.

## Two-Layer Evolvable Surface

The evolvable surface has exactly two layers, and they evolve on separate
schedules without cross-layer coupling.

**L1 — provider-rule genome.** Eight registered provider-rule modules. Each
rule is stored as typed English slots (`condition` / `action` /
`evidence_anchor`) and rendered deterministically as
`When <condition>, <action>.` for injection into provider prompts; see the
Skill Structure section for the slot contract and the module charters. The
structural gate validates add/modify edits through the slot contract; the
free-text path serves only `remove_rule` and historical checkpoint replay.

**L2 — adjudication-policy genome.** Closed-vocabulary policy items of the
form (precondition predicate, enumerated action with closed-vocabulary
parameters) over the adjudication facts of one detection. Every predicate and
action name is owned by the versioned registry
(`vocabulary_registry.L2_REGISTRY`), re-exported as `L2_PREDICATES` /
`L2_ACTIONS` in `v6_contracts`; the reflection prompt's vocabulary table is
generated from that registry at call time, and every parsed proposal is
re-validated against the same closed sets. The vocabulary is online-evolvable
(P3B): a constrained registration planner can register new whitelist-DSL
predicates or court-local action instances as `pending` (shadow-only), and the
registry is persisted with the policy genome and bound into its
`content_hash`. `validate_policy_edit` enforces vocabulary membership,
genome capacity (12 items), semantic deduplication, immutable baseline items
and weak/strong fitness protection; `apply_policy_edit` returns a new genome
with parent-hash lineage. The baseline is the empty policy set, so evolved
items must earn every behaviour change and L2 evolution cannot silently
inherit adjudication semantics.

**Why policy artifacts are executed by a deterministic evaluator.** An L2
policy item is mechanically coupled to court behaviour: once an item's
precondition fires, its effect on coverage state and the terminal edge is
exactly what the evaluator implements. If the proposing model also
interpreted the item at runtime, the proof court's verdict would depend on an
unrepeatable LLM reading of its own artifact — the artifact-to-behaviour
coupling would run through a nondeterministic channel. The deterministic
evaluator therefore owns all runtime semantics; the LLM only proposes, and a
proposal outside the closed vocabulary is discarded without effect.

## L2 Policy Evolution Closed Loop

A triggered case whose attribution lands on the L2 surface enters the policy
loop instead of the L1 mutation path. The surface is closed under
correction: proposals use `add_item` / `modify_item` / `remove_item`, so a
wrong or dead policy is repairable or eliminable instead of permanent.

1. **Proposal.** One reflection call (`purpose=policy_reflection`) receives
   the attribution triple, the addressable adjudication facts, the current
   policy set rendered with `[index]` edit addresses, and the
   rejected-proposal history. It returns one closed-vocabulary edit (a new
   item, a replacement for the item at `item_index`, or the deletion of the
   item at `item_index`) or an explicit no-proposal response. No gold label
   enters the prompt.
2. **Validation.** The response is parsed into a `PolicyEdit` (identity and
   source-case bookkeeping are assigned by the evolution loop, not the
   model; legacy responses without an `operation` field stay `add_item`)
   and validated against the closed vocabulary, capacity, deduplication,
   index bounds and protection rules.
3. **Candidate application.** `apply_policy_edit` produces a candidate policy
   genome in immutable-lineage style; the working genome is not touched yet.
4. **Source replay.** The triggering case is re-detected with
   `_run_detection_with_genome(policy_genome=candidate)` — counted
   separately as `policy_replays` — which forwards the items to
   `adjudicate_case(policy_items=...)`. The engine re-runs the proof court
   at its convergence point with the identical evidence bundle that produced
   the current verdict, so the policy re-adjudicates exactly the facts the
   court committed to.
5. **Effect gate.** Add/modify acceptance requires a non-empty
   `policy_evaluation.fired` list plus either a terminal-state change or an
   effective-coverage change; remove additionally requires that the
   targeted item actually fired on the source detection and that its
   removal moves an observable output. An edit that does not move the
   terminal edge is rejected as no-effect.
6. **Gold polarity gate.** When the terminal state moves, the move must be
   scorable against the gold label and `task_state_score` must strictly
   improve; otherwise the candidate is rejected.
7. **Accept or roll back.** Acceptance swaps in the candidate genome;
   rejection rolls back inherently because every replay runs on a copy of
   the policy genome. A gold-verified terminal-moving accept (add/modify)
   marks the item `strong` in `item_meta.fitness_status`, locking it
   against later modify/remove; coverage-only accepts stay correctable.

Gate decisions are recorded as `accept_policy`, `reject_policy_no_effect`,
`reject_policy_fitness` or `skip_no_policy` (no mutation route, failed
reflection call, unparseable response or invalid edit). The checkpoint
persists `policy_genome` as the single persistence point (resume re-validates
the closed vocabulary and the content hash fail-closed), keeps the probe
rollback anchor in `stable_policy_genome_payload` — a failed development
probe rolls the policy surface back together with the L1 champion — and
every proposal decision — attribution, facts, proposal, `policy_evaluation`,
replay state and policy genome hash — is written into
`evolution_audit["l2_policy_audit"]`.

## Proof Court Policy Hook

`adjudicate_proof_court(policy_items=..., boundary_path_state=...)` is the
single entry point for L2 semantics, and both parameters default to inert:
an empty policy set leaves the default adjudication path untouched.

- Policies act on the terminal edge only. The occurrence ledger records what
  the evidence established before policy application and is intentionally
  not recomputed; that split is the design semantics, not a missing
  synchronization.
- Coverage downgrades are monotone: a policy can only lower a coverage rank
  (`invalidate_coverage_claim` resolves the side to `unresolved`); it can
  never upgrade one.
- Unknown predicates, unknown actions and invalid parameters fail closed
  into the evaluator's `skipped` list and are never applied.
- `boundary_path_state` is an engine-level fact injected by the caller,
  because the court itself cannot address the boundary path.

## Supporting Evolution Mechanisms

- Contrastive replay acceptance: an otherwise-accepted edit is additionally
  replayed on one nearest stable same-type neighbour; polarity flips and
  gold-informed task-score regressions count as inversions and reject the
  edit.
- Procedural abstention ladder: an accepted `abstention_rule`
  clean-to-unresolved demotion re-queues the source exactly once for the
  `evidence_obligation` rung; each rung passes the same structural gate,
  source replay and canary checks and is credited individually.
- Structuralized rejection feedback: retry prompts carry the exact structural
  failure category as a hard constraint, and repeated rejection on the same
  module and failure category engages the module cooldown.
- Localization telemetry: a `verified_error` localized by the temporal
  fallback onto the last audited step records a `terminal_step_bias` audit
  signal; diagnostic only, never a verdict input.
- Base-layer failure surfacing: base-layer quarantine outcomes are projected
  into `authority_failure_reasons` so a `runtime_valid=false` run always
  carries a machine-readable cause; observability only, never a verdict
  input.

## Evolution Invariants

- Frozen base: evolution mutates only the L1 rule genome and the L2 policy
  genome; CRS weights, court decision rules and stage structure are
  untouched, and no threshold or CRS weight is hand-tuned.
- Gold isolation: gold selects polarity and fitness credit only; it never
  enters skill content, policy content or reflection prompts.
- Provider discipline: mutation, detection and judge calls use the
  registered DeepSeek V4-Flash / V4-Pro routes only.
- Case exclusion: every case id recorded in
  `experiments/protocols/used_case_ledger.txt` is excluded from new
  evolution runs.
- No self-promotion: every accepted edit still requires source replay plus
  non-regression evidence; directional evidence is not promotable, and any
  proposal failing vocabulary, structural or fitness checks is rejected and
  recorded as negative experience.
- Closed under correction: every evolvable surface can repair and remove its
  own artifacts (L1 add/modify/remove rules; L2 add_item/modify_item/
  remove_item), while gold-verified wins stay protected against erosion.
- Evidence-driven gates: acceptance rests on observable replay effects,
  closed-vocabulary contracts and gold polarity; no numeric threshold is
  ever hand-tuned into a gate.
