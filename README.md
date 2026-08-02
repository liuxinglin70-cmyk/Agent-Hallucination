# SEHD v6 — Hallucination Detector and Self-Evolution Framework

SEHD v6 implements a court-style hallucination detector (PACA-TCL v3.5) and a
self-evolution framework (BCCSE v5 with HTC-TES v3 type conditioning).  The
detector and the evolution loop are standalone modules; experiment protocols,
data partitioning and evaluation gates live outside the framework code.

For framework design and flow documentation see
[docs/design/TECHNICAL_DESIGN.md](docs/design/TECHNICAL_DESIGN.md) and
[docs/technical/DETECTION_EVOLUTION_FLOW.md](docs/technical/DETECTION_EVOLUTION_FLOW.md).

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
    ├── plans/            #   experiment plans (tracked)
    ├── protocols/        #   experiment protocol + benchmark workspace
    ├── progress/         #   local-only
    ├── results/          #   local-only
    └── logs/             #   local-only
```

Experiment protocols (partitioning, manifests, execution authorization,
development probes, evaluation gates) are deliberately not part of the
framework code.  They belong under `experiments/protocols/` and may be
re-added as a layer on top of the standalone detector and evolution modules.

## Documentation Map

```text
docs/
├── design/                    # framework design
│   └── TECHNICAL_DESIGN.md    # detector + evolution architecture
├── technical/                 # technical documentation
│   ├── DETECTION_EVOLUTION_FLOW.md   # detection-to-evolution data flow
│   └── EXPERIMENT_FRAMEWORK_GUIDE.md # framework guide (historical)
├── papers/                    # reference paper index (README tracked)
│   └── raw/                   # paper files (local-only, git-ignored)
├── notes/                     # personal notes (local-only)
├── visualization/             # diagrams and visualizations (local-only)
└── superpowers/               # historical planning records
```

Module-level documentation lives beside the code:

- `src/sehd_v6/detector/README.md` — standalone detector API
- `src/sehd_v6/evolution/README.md` — standalone self-evolution API
- `datasets/README.md` — raw dataset layout and record format
- `experiments/README.md` — experiment workspace guide
- `experiments/protocols/README.md` — protocol/benchmark workspace guide

## Development Guidelines

The detector (`src/sehd_v6/detector`) and the self-evolution framework
(`src/sehd_v6/evolution`) are the two stable framework modules.  Subsequent
experiments may iterate and optimize the detector and evolution code, but must
follow these rules:

1. **No experiment protocols or boundary splitting inside the framework.**
   Partitions, manifests, execution authorization, development probes,
   evaluation gates and dataset splits must not be added to
   `src/sehd_v6/detector`, `src/sehd_v6/evolution` or `src/sehd_v6/core`.
2. **Experiments live in `experiments/`.**  Protocol definitions, benchmark
   scripts and run orchestration are written under
   `experiments/protocols/` (see its README) and consume the framework
   modules through their public APIs:
   - `detect_record()` / `load_detector_config()` from `src/sehd_v6.detector`
   - `run_evolution()` / `load_records()` from `src/sehd_v6.evolution`
3. **Keep the framework callable standalone.**  The framework must remain
   runnable with a raw trajectory as the only input; any experiment layer must
   be an optional consumer, never a required dependency.
4. **Shared improvements belong in the framework; experiment-specific
   behavior belongs in the experiment.**  If an experiment needs a new
   framework capability, add it as a generic API in the framework and
   implement the experiment-specific policy in `experiments/protocols/`.

Large local workspaces that stay in place and are git-ignored: none —
obsolete `tmp*` directories, `_server_latest/` and the legacy validation data
were removed.  Ten empty `tmp/pytest-*` shell directories created by a past
pytest run have broken ACLs and cannot be removed from a non-admin shell;
delete them from an elevated terminal or after a reboot:

```powershell
Remove-Item -Recurse -Force "$PWD\tmp"
```

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

The 8-module natural-language rule genome:

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

Each evolved `SkillEdit` stores a label-free `feature_description` alongside
the runtime rule. The description records the reusable trajectory pattern
(candidate kind/axis, tool or omission pattern, proof coverage and CRS
provenance) that motivated the edit; it is metadata and never enters the final
proof-court verdict directly.

**Evolution loop** (per case):
1. **Detect**: Run PACA-TCL v3.5 with current genome injected
2. **Attribute**: Self-supervised trigger check (no gold required)
3. **Reflect**: LLM reflection receives evaluator-field-stripped trajectory
   evidence, attribution, executable module-output contracts, and bounded
   negative experience
4. **Gate**: Structural constraint gate validates edit safety
5. **Fitness**: Source replay plus rolling stable-case canaries; a budgeted probe can steer/rollback development, while only an authorized sealed final holdout can support a generalization claim
6. **Repair**: A repairable structural or source-no-effect candidate may use
   one bounded same-source retry with its actual observable replay deltas
7. **Commit/Rollback**: One-edit shadow commit; verdict or process-level canary regression rejects the edit

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
experiment authorization. Those policies belong to experiment orchestration;
the self-evolution framework reads the optional `project_for_evolution()`
projection from the detector result.

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
reflect/mutate -> structural gate -> source replay + canary fitness ->
commit/rollback -> experience memory`

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
plans, progress, results and logs belong in `experiments/`.

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

1. **Natural language rules** (not typed DSL): LLM generates rules directly; no compilation step needed.
2. **Self-supervised triggers**: Evolution can proceed without gold labels from unresolved, CRS contradiction, evidence-incomplete, and unavailable-signal conditions. It does not prescribe clean/error polarity without gold.
3. **Incremental batch commit**: Each edit validated independently; failed edits don't discard successful ones.
4. **Shared LLM cache**: Same genome+case replay reuses cache, eliminating CRS nondeterminism.
5. **Layered edit checks**: Structural validation and stable-case canary replay cover different failure modes; neither establishes generalization.
6. **Interface normalization**: HTC-TES modules accept both `AttributionSignal` dataclass and `Mapping[str, Any]` dict via a `_normalize_signal` helper, decoupling the type-conditioning layer from BCCSE's dataclass internals while maintaining a uniform access pattern.
7. **Typed transition bookkeeping**: Direction and type are recorded separately from gold correctness and claim eligibility.

## Module Dependencies

```
evolution/runner.py (run_evolution)
  └── evolution_runner.py (run_online_evolution)
        ├── genome.py (SkillGenome, SkillEdit, apply_edit)
        ├── step_attributor.py (attribute_failure, ErrorType, AttributionSignal)
        ├── reflection_engine.py (build_causal_reflection_prompt)
        ├── fitness.py (assess_source_fitness)
        ├── safety_gate.py (StructuralGate)
        ├── types.py (EvaluationOutcome, ExperienceRecord) ← breaks circular dep
        ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
        ├── mutator.py (MutationProvider, AtomicMutation)
        └── htc_tes/ (HallucinationTypeClassifier, TypeConditionedEvolution, TypeAwareFitness)
                └── type_classifier.py::_normalize_signal() ← accepts AttributionSignal | Mapping

detector/__init__.py (detect_record)
  └── proof_engine.py (adjudicate_case)
        ├── layered_engine.py (base layer)
        ├── proof_contract.py (proof-court contract)
        └── basic_runner.py (provider transport)

proof_evaluator.py
  ├── proof_engine.py (adjudicate_case, evaluate)
  ├── types.py (EvaluationOutcome) ← breaks circular dep
  ├── step_attributor.py (ErrorType)
  ├── genome.py (SkillGenome)
  ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
  └── skill_integration.py (integrated_v35_provider)
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
