# SEHD v6 — Hallucination Detector and Self-Evolution Framework

SEHD v6 implements a court-style hallucination detector (PACA-TCL v3.5) and a
self-evolution framework (BCCSE v5 with HTC-TES v3 type conditioning).
Self-evolution operates on a two-layer evolvable surface: an L1 provider-rule
layer of eight controlled natural-language slot-rule modules, and an L2
adjudication-policy genome of closed-vocabulary policy items evaluated
deterministically by the proof court.  The detector and the evolution loop
are standalone modules; experiment protocols, data partitioning and
evaluation gates live outside the framework code.

For framework design and flow documentation see
[docs/design/TECHNICAL_DESIGN.md](docs/design/TECHNICAL_DESIGN.md) and
[docs/technical/DETECTION_EVOLUTION_FLOW.md](docs/technical/DETECTION_EVOLUTION_FLOW.md).

## Core Objective and Design Discipline (必读 / Mandatory)

Every agent working in this repository — on framework code, experiments, or
documentation — must read this section first and comply with it. It is the
single source of truth for why this project exists and how decisions are
made; downstream documents only reference it, never restate it.

### Core objective (核心目标)

Produce **effective self-evolving detection skills** that measurably improve
agent-hallucination detection quality. The detector base (PACA-TCL v3.5) is
frozen; all improvement must come from the two evolvable surfaces (L1
provider rules, L2 adjudication policies) and must be demonstrated by
independent holdout measurement, not by framework-internal signals alone.
Every framework change is justified by its contribution to this objective;
correctness, auditability and call-efficiency work are enablers, never ends
in themselves.

### Design philosophy (设计理念)

1. **Systemic over patch-style.** Fix root causes with structural, bottom-up
   redesign; never accumulate special-case `if`s, hand-tuned knobs, or
   scenario mappings (see the First-Time Principle: single source of truth,
   no residue, local/deployed parity).
2. **Evidence-driven gates, no hand-tuned thresholds.** Acceptance decisions
   rest on observable replay effects, closed-vocabulary contracts and gold
   polarity — never on a manually adjusted numeric threshold or CRS weight
   (weights are frozen at 0.35/0.15/0.20/0.20/0.10).
3. **Fail-closed discipline.** Missing authority, unparseable output, or
   vocabulary violations always reject; uncertainty never passes as a
   verdict or an accepted edit.
4. **Evolvable surfaces are closed under correction.** Every layer that can
   produce a defective artifact must also be able to repair or remove it
   (L1: add/modify/remove rules; L2: add_item/modify_item/remove_item).
5. **Gold isolation.** Gold labels select polarity and fitness credit only;
   they never enter skill content, policy content or reflection prompts.
6. **Provider discipline.** Only DeepSeek-V4-Flash / DeepSeek-V4-Pro routes
   are allowed; credential values never enter code or artifacts.

### Advisor requirements (导师要求, 2026-08 discussion)

1. **Never hard-tune thresholds.** A single hand-adjusted threshold is always
   wrong in the end; prefer experiential judgment expressed as evolved
   skills (the agent decides from accumulated experience, not from a
   hand-picked number).
2. **Alternatives to a single threshold are encouraged.** Multiple
   thresholds / multiple outputs with best-of-N selection (test-time
   scaling) is a legitimate direction; broaden the option space instead of
   tuning one value.
3. **Regression-style validation (e.g. canary regression) is an optimizable
   mechanism**, adopted selectively where it earns its cost; it is a guard,
   not a correctness authority.
4. **Skill effects must be validated on an independent validation set.**
   Progress reports must show concrete intermediate experiment outputs, not
   only framework descriptions.

Experiment protocols under `experiments/protocols/` must state how they
comply with this section; reviews check compliance before results are
accepted.

## Layout

```text
SEHD v6
├── src/sehd_v6/          # ① framework code: detector + self-evolution
│   ├── detector/         #   standalone single-trajectory detector API
│   ├── evolution/        #   standalone self-evolution framework API
│   ├── core/             #   engine implementations
│   │   ├── paca_tcl/     #   PACA-TCL v3.5 court-style detector engine
│   │   ├── bccse/        #   BCCSE v5 self-evolution core
│   │   └── htc_tes/      #   HTC-TES v3 type conditioning
│   ├── configs/          #   detector config template
│   ├── provider_routes.py
│   ├── env_loader.py
│   └── utils/            #   OCSE packet utilities
├── docs/                 # ② documentation: design, technical, papers
│   ├── design/
│   ├── technical/
│   ├── papers/           #   paper index (raw paper files local-only)
│   ├── notes/            #   local-only notes
│   └── visualization/    #   local-only diagrams
├── datasets/             # ③ raw datasets (local-only, git-ignored)
└── experiments/          # ④ experiment workspace
    └── protocols/        #   README + used-case ledger (tracked);
                          #   all run artifacts local-only, git-ignored
```

Experiment protocols (partitioning, manifests, execution authorization,
development probes, evaluation gates) are deliberately not part of the
framework code.  They belong under `experiments/protocols/` and may be
re-added as a layer on top of the standalone detector and evolution modules.

## Documentation Map

```text
docs/
├── SEHD_V6_ACADEMIC_DOCUMENT.md      # 学术科研技术文档（本框架的完整学术阐述）
├── SEHD_V6_FRAMEWORK_VISUALIZATION.md # 框架可视化图谱（10 张 Mermaid 架构图）
├── design/                    # framework design
│   ├── TECHNICAL_DESIGN.md    # detector + evolution architecture
│   └── SELF_EVOLUTION_FRAMEWORK_REVIEW.md  # evolution loop implementation contract
├── technical/                 # technical documentation
│   ├── DETECTION_EVOLUTION_FLOW.md   # detection-to-evolution data flow
│   └── SEHD_V6_FRAMEWORK_VISUAL.md   # framework visual overview
├── papers/                    # reference paper index (README tracked)
│   └── raw/                   # paper files (local-only, git-ignored)
├── notes/                     # personal notes (local-only)
└── visualization/             # diagrams and visualizations (local-only)
```

Module-level documentation lives beside the code:

- `src/sehd_v6/detector/README.md` — standalone detector API
- `src/sehd_v6/evolution/README.md` — standalone self-evolution API
- `datasets/README.md` — raw dataset layout and record format
- `experiments/README.md` — experiment workspace guide
- `experiments/protocols/README.md` — protocol records and used-case ledger

## Development Guidelines

The detector (`src/sehd_v6/detector`) and the self-evolution framework
(`src/sehd_v6/evolution`) are the two stable framework modules.  Subsequent
experiments may iterate and optimize the detector and evolution code, but must
follow these rules:

1. **No experiment protocols or boundary splitting inside the framework.**
   Partitions, manifests, execution authorization, development probes,
   evaluation gates and dataset splits must not be added to
   `src/sehd_v6/detector`, `src/sehd_v6/evolution` or `src/sehd_v6/core`.
2. **Experiments live in `experiments/`.**  Protocol records and the
   used-case ledger are tracked under `experiments/protocols/` (see its
   README); run scripts, dataset builds and run orchestration are local-only
   artifacts under `experiments/`, and consume the framework modules through
   their public APIs:
   - `detect_record()` / `load_detector_config()` from `src/sehd_v6.detector`
   - `run_evolution()` / `load_records()` from `src/sehd_v6.evolution`
3. **Keep the framework callable standalone.**  The framework must remain
   runnable with a raw trajectory as the only input; any experiment layer must
   be an optional consumer, never a required dependency.
4. **Shared improvements belong in the framework; experiment-specific
   behavior belongs in the experiment.**  If an experiment needs a new
   framework capability, add it as a generic API in the framework and
   implement the experiment-specific policy in `experiments/protocols/`.

## Caches and Logs

Experiment runs keep their caches and logs under `experiments/` so the
framework never depends on a fixed system path.  Conventional locations:

- `experiments/cache/provider/` — global provider cache.  Point the framework
  at it with `PACA_GLOBAL_CACHE_ROOT=experiments/cache/provider`.
- `experiments/logs/` — run logs.
- `experiments/results/<run>/` — per-run artifacts; the framework writes its
  `shared_cache/` and route caches inside each run's output directory.

These directories are git-ignored, created on demand by runs, and safe to
delete between runs.

## Core Methodology

### 1. PACA-TCL v3.5 — Registered Execution Protocol

The baseline protocol registers a court-style pipeline. Config,
case roles and provider routes are frozen for an individual audited run. BCCSE
can deliberately mutate injected strategy rules, so the resulting detector
behavior must not be described as globally frozen.

- **H0 Base pass and construct census**: Builds the OCSE packet, runs the layered base analysis and enumerates candidate constructs. These are hypotheses for later adjudication, not verdicts.
- **H1 Prosecution**: Constructs a causal prosecution case identifying hallucination candidates from the agent trajectory. Produces error certificates with evidence chains.
- **H2 Consensus Gate**: Adversarial defense vs prosecution. Defense attempts to exonerate candidates via counter-evidence. Gate adjudicates based on certificate validity.
- **H3 Dual-Phase Localization**: When error is confirmed, localizes the responsible step via causal backtracking and earliest-origin tiebreak.

**Five-dimensional CRS diagnostic projection** (configured weights):
- Factuality (0.35): Does the claim have verified tool/output support?
- Counterfactual (0.15): Would the error exist under intervention?
- Recovery (0.20): Did the agent self-correct downstream?
- Process (0.20): Was the reasoning process sound regardless of outcome?
- Scope (0.10): Is the error localized or systemic?

**Three-state output**: `verified_error` / `verified_clean` / `unresolved`

### 2. BCCSE v5 — Self-Evolution Framework

Self-evolution operates on a two-layer evolvable surface. Both layers share
the same gate discipline: no detection threshold or CRS weight is ever
hand-tuned, gold labels never enter rule content or reflection prompts, and
provider routes are restricted to DeepSeek-V4-Flash / DeepSeek-V4-Pro.

#### L1 provider-rule layer

Eight skill modules own the provider-emitted surfaces:

| Module | Stage | Role |
|--------|-------|------|
| `candidate_trigger` | Census | What constitutes a hallucination candidate |
| `evidence_obligation` | Verification | What evidence must support each candidate |
| `candidate_to_verifier_route` | Verification | How candidates are routed to verification |
| `atomic_verifier_instruction` | Verification | How to verify individual claims atomically |
| `clean_defense_boundary` | Verification | When to defend a candidate as clean |
| `causal_link_test` | Causal Court | How to test causal chains for localization |
| `earliest_origin_tiebreak` | Causal Court | How to resolve multi-origin localization ties |
| `abstention_rule` | Causal Court | When to output `unresolved` |

L1 rules are stored as typed slots (`condition` / `action` /
`evidence_anchor`, English by contract) instead of free text
(`rule_contract.py`). The `action` slot is a **closed opcode** of the
selected module (`module_charter.py::MODULE_ACTION_OPCODES`), optionally
`"<opcode>: <target>"` naming the exact output field it acts on; free-text
actions are rejected as `action_opcode_unowned`. Prompt injection is a
deterministic render: `render_rule()` expands the opcode into its English
template and emits exactly `When <condition>, <action>.`, so the structural
gate validates the slot contract whenever slots are present; the free-text
path only serves `remove_rule` and historical checkpoint replay. The
`evidence_anchor` slot is failure-signature bookkeeping for evolution triage
and is never rendered into runtime prompts.

Each module carries a charter (`module_charter.py`): its influenceable
surfaces, forbidden surfaces, allowed verdicts, and in-scope vs out-of-scope
failure examples (all in English). Only the allowed target modules'
charters and opcode lists are injected into the reflection prompt, so
proposals are constrained at generation time without relitigating the full
architecture. Each evolved `SkillEdit` also stores a label-free
`feature_description` recording the reusable trajectory pattern that
motivated the edit; it is metadata and never enters the final proof-court
verdict directly.

#### L2 adjudication-policy layer

The L2 genome (`policy_genome.py`) stores closed-vocabulary policy items:
one precondition predicate (a pure function over the frozen
`AdjudicationFacts` snapshot) paired with one enumerated action —
`downgrade_coverage`, `invalidate_coverage_claim`, `emit_telemetry_only` —
whose parameters draw from closed vocabularies. The single source of truth
for every predicate, action and parameter token is the versioned baseline
registry `vocabulary_registry.L2_REGISTRY` (re-exported through
`v6_contracts.py`), and
`L2_ACTIONS` contains exactly the actions the runtime can execute: the
engine-level actions (`reclassify_candidate_state`, `require_route_replay`)
are reserved in `ENGINE_LEVEL_ACTIONS` but excluded until a provider
re-execution engine implements them, so a proposal for one fails at parse
time instead of reaching a runtime rejection. The LLM only proposes items;
runtime semantics are owned by the deterministic evaluator
(`policy_evaluator.py`), which the proof court executes mechanically through
its policy hook:
`adjudicate_proof_court(policy_items=..., boundary_path_state=...)`. The
hook is inert by default — without policy items the detector output is
byte-identical to the baseline — and the detection engine re-adjudicates
with the same evidence bundle at the court's convergence point. The L2
baseline is the empty policy set, so every behavior change must be earned.

The L2 genome is a first-class capability alongside the L1 skill genome:
a fresh run can inherit both via `initial_genome` + `initial_policy_genome`
(`run_evolution` / `run_online_evolution`), checkpoints restore both, the
run persists `policy_genome.json`, and the standalone detector CLI accepts
`--policy-genome` (or `detect_record(policy_genome=...)`) so an evolved
two-layer capability can be deployed and validated independently.

#### Deterministic attribution

Attribution is a deterministic failure classifier (`failure_classifier.py`)
that answers exactly one question: at which layer, on which artifact, did
the failure happen — no LLM, no heuristics. The classifier input is the
complete provider-artifact integrity projection of one detection
(construct-stage candidate counts, court audit failed-checks, quarantine
tallies, appeal provider status, and the single gold-informed FN+omission
signal). It runs on every detection: an L2 attribution opens the L2 loop
independently of the self-supervised trigger, and an L1 attribution carries
an evidence-based owner that outranks the process-heuristic secondary
module. An L1 owner hypothesis must pass the gold polarity gate (a false
negative can only be owned by modules that tighten error detection, a false
positive only by modules that relax or bound the error claim); hypotheses
crossing the gold side are dropped. Non-L1 failures (adjudication-policy
layer, detector-layer anomalies) have no L1 owner: they are recorded in
`non_l1_failure_attributions` as audit telemetry and feed the L2 policy
loop.

#### L1 evolution loop (per case)

1. **Detect**: Run PACA-TCL v3.5 with the current genomes injected
2. **Attribute**: Deterministic failure classification on the full
   provider-artifact projection; L1 owners pass the gold polarity gate;
   non-L1 failures go to telemetry and the L2 loop
3. **Reflect**: LLM reflection receives evaluator-field-stripped trajectory
   evidence, attribution, module charters and closed action opcodes,
   executable module-output contracts, bounded negative experience, and
   (when available) a supporting case + stable counter-case contrast block
   that bounds the condition to the separating discriminator
4. **Gate**: Deterministic pre-validation (ownership/opcode/slot/contract
   shape) before the critic, then the structural constraint gate validates
   edit safety; a structurally invalid pick can fall through to the next
   ranked legal candidate
5. **Fitness**: Source replay plus rolling stable-case canaries; every
   replay records the injected rule ids (`rule_trace`) and a deterministic
   no-effect class (`output_changed_then_quarantined` /
   `provider_output_changed_but_court_invariant` /
   `no_observable_change_any_layer`) so a no-effect is no longer guessed;
   a budgeted probe can steer/rollback development, while only an
authorized sealed final holdout can support a generalization claim
6. **Repair**: A repairable structural or source-no-effect candidate may use
   one bounded same-source retry with its actual observable replay deltas;
   when a source exhausts its Flash attempts with a categorical no-edit
   outcome, an optional `pro_mutation_route` gets one deeper diagnosis
   attempt
7. **Commit/Rollback**: One-edit shadow commit; verdict or process-level canary regression rejects the edit

#### L2 closed loop

The L2 surface is closed under correction: proposals use three operations —
`add_item` (new item), `modify_item` (replace the item at `item_index`),
`remove_item` (delete the item at `item_index`) — so a wrong or dead policy
can be repaired or eliminated instead of accumulating.

1. **Propose**: One reflection call proposes a single closed-vocabulary
   `PolicyEdit` (the vocabulary table and the `[index]`-addressed current
   policy set in the prompt are generated from `v6_contracts.py` / the live
   genome at call time, never copied)
2. **Gate**: The structural gate enforces the closed vocabulary, genome
   capacity, deduplication, index bounds, and baseline/fitness protection
3. **Replay**: Source replay (counted separately as `policy_replays`) must
   show an observable adjudication movement: add/modify require the policy
   to fire and move the terminal state or effective coverage; remove
   requires the targeted item to have actually fired on the source
   detection and its removal to move an observable output
4. **Gold polarity gate**: When the terminal state moves, `task_state_score`
   must strictly improve (no consecutive-improvement threshold); state
   movement without gold is rejected
5. **Accept/Rollback**: Rejected candidates are only ever replayed as
   copies, so the working genome rolls back inherently. A gold-verified
   terminal-moving accept (add/modify) earns `strong` protection against
   later modify/remove; coverage-only accepts stay correctable (good
   policies lock in, bad policies are eliminable). Gate decisions are
   `accept_policy` / `reject_policy_no_effect` / `reject_policy_fitness` /
   `skip_no_policy`; the policy genome persists in the checkpoint (single
   persistence point, vocabulary + hash validated on resume), a failed
   development probe rolls the policy surface back together with the L1
   champion, and every decision is audited in
   `evolution_audit["l2_policy_audit"]`

### 3. HTC-TES v3 — Hallucination-Typed Conditioning

**Problem addressed**: BCCSE v5's monolithic evolution treats all hallucination types identically, causing:
- Evolution direction conflicts (error↔clean simultaneously)
- No type-specific detection experience accumulation
- Fitness signal diluted across heterogeneous types

**Solution**: Three-layer conditioning:

| Layer | Mechanism | Implementation |
|-------|-----------|----------------|
| Macro | Taxonomy-conditioned routing | `type_classifier.py` classifies 8 types; `type_conditioned_evolution.py` routes to target modules |
| Meso | Process-level fitness | `type_aware_fitness.py` evaluates per-type directional progress |
| Micro | Typed experience memory | `TypeExperienceMemory` persists trajectory features, feature descriptions and success/failure patterns |

**8 Hallucination Types**: FACTUAL, INSTRUCTIONAL, TOOL, REASONING, OMISSION, ADOPTION, CONTEXTUAL, UNKNOWN

**Multi-signal classification** (in `type_classifier.py`):
1. Error type mapping (`ErrorType` → `HallucinationType`)
2. Target module mapping (module → likely type)
3. Candidate nature analysis (claim/action/omission patterns)
4. CRS score pattern analysis (which dimensions are anomalous)
5. State transition analysis (unresolved + CRS aggregate)

**Type→Module Routing** (primary targets):
- FACTUAL → evidence_obligation, atomic_verifier
- INSTRUCTIONAL → clean_defense, abstention
- TOOL → c_to_v_route, atomic_verifier
- REASONING → causal_link, origin_tiebreak
- OMISSION → candidate_trigger, c_to_v_route
- ADOPTION → evidence_obligation, clean_defense
- CONTEXTUAL → abstention, clean_defense

**Advisory routing**: the type route is advisory, not a hard safety
constraint — the causal attributor owns root-cause priority and the type
classifier only filters implausible modules from the already bounded
attribution route (empty intersection falls back to the attribution
route). HTC-TES functions as an experience-organisation label, a
retrieval/diversity tag, and an experiment telemetry dimension; it never
decides the owner.

**Type→Editing Strategy** mapping:
- FACTUAL → strict_verification
- INSTRUCTIONAL → boundary_enforcement
- TOOL → tool_validation
- REASONING → causal_chain_strengthening
- OMISSION → trigger_sensitivity
- ADOPTION → adoption_scrutiny
- CONTEXTUAL → context_preservation

**Typed direction telemetry**: The implementation can record declared
error-oriented, clean-oriented, process-repair and abstention transitions.
This routing vocabulary is not evidence that both detector directions have
been successfully optimized.

**Typed telemetry**: Directional decisiveness × type-experience coverage
- Without gold, decisive outcomes are not called correct; the metric records whether a typed process edit reached its declared direction
- Type-experience coverage is reported separately from detector accuracy
- A dry-run rule application is not counted as detection improvement unless source replay changes the authoritative detector outcome

**Artifact-first experience memory**: experience deduplication keys on the
deterministic attribution signature (`layer` + `artifact` + `reason_code` +
`operation`) plus feature/direction/success, so the same intervention on
the same failure signature is one experience; repeated observations merge
into an outcome distribution (`outcome_counts`) instead of overwriting the
first snapshot. Reflection summaries aggregate artifact and reason-code
counts alongside the type label.

## Usage

### Standalone Detector

The detector can run independently from BCCSE evolution and evaluation. The
input JSON must contain a non-empty `history` and must not contain dataset gold
fields such as `is_hallucination`, `hallucination_step`, or `gold_label`.

```bash
# Required credential. Route-specific and purpose-specific variables override
# the provider defaults; credential values are never stored in config files.
set DEEPSEEK_API_KEY=<secret>

python -m src.sehd_v6.detector \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --record trajectory.json \
  --case-id example \
  --output-dir detector_output
```

The prediction is written to `detector_output/prediction.json`. Provider
settings use one precedence rule throughout the detector:

- `SEHD_ROUTE_<ROUTE>_*`, for example `SEHD_ROUTE_DEFENSE_MODEL`
- `SEHD_DETECTOR_*` or `SEHD_JUDGE_*`, selected by route responsibility
- `SEHD_PROVIDER_*`
- values in the JSON config
- official DeepSeek endpoint/model defaults

Supported suffixes are `ENDPOINT`, `MODEL`, `API_KEY_ENV`, `TEMPERATURE`,
`MAX_TOKENS`, `TRANSPORT_RETRIES`, `TIMEOUT_SECONDS`, and
`TOTAL_TIMEOUT_SECONDS`. The process environment overrides repository `.env`.

The stable application-facing import is now `src.sehd_v6.detector`. The
detector itself does not load partition/manifest files and does not enforce
experiment authorization. Those policies belong to experiment orchestration.
The evolution loop consumes an equivalent label-free projection inline
(`signal_adapter.resolve_authoritative_state()`); the `project_for_evolution()`
adapter remains available for external consumers that want the same projection
as an explicit object.

For self-evolution, the same detector boundary is used directly:

```python
from pathlib import Path
from src.sehd_v6.detector import (
    detect_record,
    project_for_evolution,
)

prediction = detect_record(
    record=trajectory_without_gold,
    config=detector_config,
    case_id="case-001",
    output_dir=Path("detector_output"),
)
evolution_input = project_for_evolution(prediction).to_dict()
```

The single-case detector does not require a partition, manifest, P0 role,
execution authorization flag, or precomputed census cache. A cache root is an
optional optimization; when omitted, the detector uses provider census
fallback. Genome injection is explicit through `detect_record(..., genome=...)`
and is not inferred from global evaluation state.

### Standalone Self-Evolution

The evolution framework is equally independent from experiment protocols. It
accepts raw trajectories (one case or a batch), runs the detector loop, and
writes the evolved champion genome plus typed experience memory.

```bash
python -m src.sehd_v6.evolution \
  --records records.jsonl \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --output-dir evolution_output
```

`records.jsonl` is one record per line; each line is either
`{"case_id": "...", "record": {...}}` or a record with an embedded
`case_id`. A JSON object mapping `case_id -> record` also works. The loop for
every case is:

`detect -> attribute (self-supervised) -> HTC-TES type routing ->
structured observation -> independent reflection candidates -> candidate
deduplication -> critic selection -> structural gate -> source replay + canary
fitness -> commit/rollback -> experience memory`

The CLI exposes bounded reflection test-time scaling:

```bash
python -m src.sehd_v6.evolution \
  --records records.jsonl \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --output-dir evolution_output \
  --reflection-candidates 3 \
  --max-mutation-calls 100
```

`--reflection-candidates` controls independent mutation samples per triggered
case (1..5). The critic only prioritizes which candidate reaches replay; it does
not certify correctness. Generation, attribution, critic and conflict-gate
calls share the hard `--max-mutation-calls` budget and fail closed when it is
exhausted. The evolution audit records candidate count, owner validation,
critic decision and cache metadata.

Artifacts written to `evolution_output`:

- `evolution_result.json` — audit trail
- `champion_genome.json` — evolved skill genome
- `htc_tes_experience_memory.json` — typed experience memory

The loop can continue from its own outputs:

```bash
python -m src.sehd_v6.evolution \
  --records records.jsonl \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --output-dir evolution_output_2 \
  --initial-genome evolution_output/champion_genome.json \
  --experience-memory evolution_output/htc_tes_experience_memory.json
```

The standalone evolution module does not require partitions, manifests,
development probes, pre-registered canaries, execution authorization, or a
census cache. Those are experiment protocols and live under `experiments/`,
not in the framework loop.

Provider routes are loaded from the repository-root `.env` (or the process
environment; process values win). No credential value is written to code or
artifacts. The default mutation route is official DeepSeek V4-Flash:

```text
DEEPSEEK_API_KEY=<secret>
# Optional purpose-specific overrides:
SEHD_MUTATION_ENDPOINT=https://api.deepseek.com
SEHD_MUTATION_MODEL=deepseek-v4-flash
SEHD_MUTATION_API_KEY_ENV=DEEPSEEK_API_KEY
SEHD_DETECTOR_ENDPOINT=...
SEHD_DETECTOR_MODEL=...
SEHD_DETECTOR_API_KEY_ENV=...
SEHD_JUDGE_ENDPOINT=...
# Route-level selectors such as SEHD_ROUTE_DEFENSE_MODEL take precedence over
# purpose-level selectors. A non-official proxy route never silently receives
# DEEPSEEK_API_KEY.
```

Raw datasets belong in `datasets/` (see `datasets/README.md`). Experiment
progress, results and logs belong in `experiments/` (local-only,
git-ignored).

## Public API

The stable application-facing imports are `sehd_v6.detector` and
`sehd_v6.evolution`:

```python
from sehd_v6.detector import detect_record, project_for_evolution
from sehd_v6.evolution import run_evolution, SkillGenome, create_baseline_genome
```

Lower-level imports remain available through `sehd_v6.core`:

```python
from sehd_v6.core import (
    # Detection (PACA-TCL v3.5)
    adjudicate_case, evaluate,
    # Evolution (BCCSE v5)
    run_online_evolution, SkillGenome, create_baseline_genome,
    SkillEdit, SkillOp, apply_edit, validate_edit,
    ErrorType, AttributionSignal, attribute_failure,
    StructuralGate, create_structural_gate,
    # Type conditioning (HTC-TES v3)
    HallucinationType, HallucinationTypeClassifier,
    TypeClassificationResult, TypeConditionedEvolution,
    TypeAwareFitness, classify_hallucination_type,
)
```

Each sub-package also exposes its own `__init__.py` for granular imports:
- `sehd_v6.detector` — standalone detector API
- `sehd_v6.evolution` — standalone self-evolution API
- `sehd_v6.core.bccse` — self-evolution framework core
- `sehd_v6.core.paca_tcl` — detector engine
- `sehd_v6.core.htc_tes` — type conditioning layer

## Key Design Decisions

1. **Controlled natural-language slot rules** (not a typed DSL, not free text): the LLM generates rule slots directly — no compilation step — and the runtime injection is a deterministic render (`When <condition>, <action>.`) that the structural gate can validate as a slot contract.
2. **Closed-vocabulary L2 policies, closed under correction**: adjudication-policy items pair a closed precondition predicate with an enumerated action; runtime semantics are fixed by the deterministic proof-court evaluator, and the evolution loop supports add/modify/remove so defective policies are repairable instead of permanent.
3. **Deterministic attribution**: a heuristic-free failure classifier answers "which layer, which artifact"; L1 owners pass the gold polarity gate, non-L1 failures feed telemetry and the L2 policy loop.
4. **Self-supervised triggers**: Evolution can proceed without gold labels from unresolved, CRS contradiction, evidence-incomplete, and unavailable-signal conditions. It does not prescribe clean/error polarity without gold.
5. **Incremental batch commit**: Each edit validated independently; failed edits don't discard successful ones.
6. **Shared LLM cache**: Same genome+case replay reuses cache, eliminating CRS nondeterminism.
7. **Layered edit checks**: Structural validation and stable-case canary replay cover different failure modes; neither establishes generalization.
8. **Interface normalization**: HTC-TES modules accept both `AttributionSignal` dataclass and `Mapping[str, Any]` dict via a `_normalize_signal` helper, decoupling the type-conditioning layer from BCCSE's dataclass internals while maintaining a uniform access pattern.
9. **Typed transition bookkeeping**: Direction and type are recorded separately from gold correctness and claim eligibility.

## Module Dependencies

```
evolution/runner.py (run_evolution)
  └── evolution_runner.py (run_online_evolution)
        ├── genome.py (SkillGenome, SkillEdit, apply_edit)
        ├── step_attributor.py (attribute_failure, ErrorType, AttributionSignal)
        ├── reflection_engine.py (build_causal_reflection_prompt)
        ├── fitness.py (assess_source_fitness)
        ├── safety_gate.py (StructuralGate)
        ├── types.py (ExperienceRecord) ← breaks circular dep
        ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
        └── htc_tes/ (HallucinationTypeClassifier, TypeConditionedEvolution, TypeAwareFitness)
                └── type_classifier.py::_normalize_signal() ← accepts AttributionSignal | Mapping

detector/__init__.py (detect_record)
  └── proof_engine.py (adjudicate_case)
        ├── layered_engine.py (base layer)
        ├── proof_contract.py (proof-court contract)
        └── basic_runner.py (provider transport)

signal_adapter.py (resolve_evolution_state, metric_snapshot, experience_record_from_row)
  ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
  └── types.py (ExperienceRecord)
```

## Interface Contract

HTC-TES modules accept attribution signals via a normalized interface:

| Function | Accepts | Normalization |
|----------|---------|---------------|
| `HallucinationTypeClassifier.classify()` | `AttributionSignal \| Mapping \| None` | `_normalize_signal()` → dict |
| `TypeConditionedEvolution.create_edit_proposal()` | `AttributionSignal \| Mapping` | `_normalize_signal()` → dict |
| `build_type_conditioned_reflection_prompt()` | `AttributionSignal \| Mapping` | `_normalize_signal()` → dict |

This decouples HTC-TES from BCCSE's dataclass shape while allowing the runner
path (which passes `AttributionSignal` objects) to work without manual
`.to_dict()` conversion at every call site.

## Current Evidence Boundaries

- **PACA-TCL v3.5**: standalone research detector implementation; its
  strategy can be modified by genome injection.
- **BCCSE v5**: standalone research evolution machinery. Historical artifacts
  do not by themselves establish out-of-sample improvement.
- **HTC-TES v3**: integrated typed telemetry.
  - Type telemetry and type→module routing are recorded in evolution results
  - Interface normalization: `_normalize_signal` resolves AttributionSignal/dict mismatch

Experiment protocols (partitions, probes, final-holdout roles, authorization
flags) are intentionally removed from the framework code. They can be re-added
as a layer under `experiments/` on top of the standalone detector and
evolution modules.

## License

Private research repository.
