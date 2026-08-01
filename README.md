# SEHD v6 — Research Prototype for Evidence-routed Skill Evolution

SEHD v6 is a research prototype. It implements a registered execution
protocol, a mutable strategy/genome layer, and typed telemetry with provenance;
it is not a production detector or a demonstrated detector improvement.

For a reader-oriented, end-to-end explanation of the data roles, PACA-TCL
court stages, BCCSE case evolution, evaluation boundaries and current evidence
limits, see [EXPERIMENT_FRAMEWORK_GUIDE.md](EXPERIMENT_FRAMEWORK_GUIDE.md).

## Architecture

```
SEHD v6
├── src/sehd_v6/
│   ├── core/
│   │   ├── paca_tcl/      # PACA-TCL v3.5: Court-style causal attribution detector
│   │   │   ├── contract_v1.py          # Typed causal-ledger contract
│   │   │   ├── proof_contract_v3.py    # Proof-court contract v3
│   │   │   ├── proof_contract_v3_4.py  # Proof-court contract v3.4 (extended defenses)
│   │   │   ├── layered_contract.py     # Genuinely layered contract v2
│   │   │   ├── proof_contract.py       # Unified proof contract facade
│   │   │   ├── proof_engine.py         # v3.5 adjudication engine
│   │   │   ├── proof_engine_v3_4.py    # v3.4 engine (legacy compat)
│   │   │   ├── layered_engine.py       # Layered adjudication engine
│   │   │   ├── proof_evaluator.py      # BCCSE evaluator adapter + LLM provider
│   │   │   ├── skill_integration.py    # Genome→v3.5 provider injection
│   │   │   └── basic_runner.py         # I/O helpers
│   │   ├── bccse/        # BCCSE v5: Self-evolution framework
│   │   │   ├── genome.py              # 8-module NL rule genome + v4 mutation types
│   │   │   ├── evolution_runner.py    # Per-case online evolution main loop
│   │   │   ├── reflection_engine.py   # LLM causal process reflection
│   │   │   ├── fitness.py             # Hierarchical fitness assessment
│   │   │   ├── safety_gate.py         # Structural constraint gate
│   │   │   ├── step_attributor.py     # Self-supervised + gold-enhanced attribution
│   │   │   ├── signal_adapter.py      # CRS/coverage signal extraction
│   │   │   ├── types.py               # Shared types (EvaluationOutcome, ExperienceRecord, ...)
│   │   │   ├── mutator.py             # Reflective typed-skill mutator
│   │   │   ├── promotion_gates.py     # G1 checks, MetricSnapshot, ReplayEvidence
│   │   │   ├── critic.py              # Independent semantic/leakage/construct critics
│   │   │   ├── cesv_contract.py       # CESV auxiliary graph contract
│   │   │   └── curriculum.py          # Evolution curriculum (case ordering)
│   │   └── htc_tes/      # HTC-TES v3: Hallucination-Typed Conditioning
│   │       ├── type_classifier.py             # 8-type hallucination classifier
│   │       ├── type_conditioned_evolution.py  # Type→module routing + editing strategy
│   │       └── type_aware_fitness.py          # DACR metric + typed experience memory
│   ├── data/             # Experiment data (partitions, champions, results)
│   ├── configs/          # JSON schemas and config templates
│   ├── utils/            # OCSE packet utilities, preflight checks
│   ├── main.py           # CLI entry: partition / evolve / evaluate
│   ├── dry_run_5.py      # 5-case dry-run wiring exercise (mock or configured LLM)
│   └── bccse_v5_data_partition.py  # Stratified data partitioning
└── .gitignore
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

```bash
# 5-case dry-run wiring exercise (mock detections; optionally uses configured mutation LLM)
cd src
python -m sehd_v6.dry_run_5

# Provider routes are loaded from the repository-root `.env` (or the process
# environment; process values win). No credential value is written to code or
# artifacts. The default mutation route is official DeepSeek V4-Flash:
# DEEPSEEK_API_KEY=<secret>
# Optional purpose-specific overrides:
# SEHD_MUTATION_ENDPOINT=https://api.deepseek.com
# SEHD_MUTATION_MODEL=deepseek-v4-flash
# SEHD_MUTATION_API_KEY_ENV=DEEPSEEK_API_KEY
# SEHD_DETECTOR_ENDPOINT=...
# SEHD_DETECTOR_MODEL=...
# SEHD_DETECTOR_API_KEY_ENV=...
# SEHD_JUDGE_ENDPOINT=...
# Route-level selectors such as SEHD_ROUTE_DEFENSE_MODEL take precedence over
# purpose-level selectors. Legacy config-file route values remain compatible;
# a non-official proxy route never silently receives DEEPSEEK_API_KEY.

# Partition into a reusable development pool, a small fixed gold probe inside
# that pool, and a sealed final holdout. Legacy explicit Outer rows are kept.
python -m src.sehd_v6.main partition --probe-size 10 --holdout-size 20 --seed 42

# Run diagnostic evolution on a stage-authorized manifest. A manifest containing
# D cases requires D_execution_authorized=true even though this is not a formal
# effectiveness evaluation.
python -m src.sehd_v6.main evolve --size 50 --use-gold \
  --probe-interval 10 --probe-budget 5

# Continue from an audited champion and prior experience memory
python -m src.sehd_v6.main evolve --size 50 \
  --initial-genome <champion_genome.json> \
  --experience-memory <htc_tes_experience_memory.json>

# Budgeted probe check: directional/rollback evidence, never a generalization claim.
python -m src.sehd_v6.main evaluate --set probe --genome <path>

# Read-only final-holdout evaluation. `outer` is a compatibility alias. Requires
# a hash-bound final_holdout_ids.json/outer_set_ids.json plus execution_authorized=true,
# D_execution_authorized=true and outer_execution_authorized=true.
python -m src.sehd_v6.main evaluate --set final_holdout --genome <path>
```

Most data can remain in the development pool because source replay, experience
memory and repeated evolution are development activities; making each old
validation/test slice permanently forbidden would unnecessarily starve that
loop. The fixed gold probe is a small, budgeted subset of the development pool:
it can steer or veto a change, but repeated access means it is not final
generalization evidence. The disjoint final holdout answers that separate
question only after development ends.

Final-holdout IDs are never recovered from `case_ref` values or dataset scans. The
final path reads the supplied genome without mutation and never writes back
experience or champion state. Code guarantees role isolation and hash binding,
not secrecy, operator blindness or one-shot custody. Champion eligibility
therefore also requires `outer_custody_verified=true`, an external governance
attestation that the role stayed outside development and was opened under the
final-evaluation policy. The CLI has no durable global use registry.

## Public API

Import from `sehd_v6.core` for a unified entry point:

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
- `sehd_v6.core.bccse` — self-evolution framework
- `sehd_v6.core.paca_tcl` — registered detector protocol implementation
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
main.py
  └── evolution_runner.py
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

- **PACA-TCL v3.5**: research detector implementation with a frozen per-run
  execution protocol; its strategy can be modified by genome injection.
- **BCCSE v5**: research evolution machinery. Historical 50-case artifacts do
  not by themselves establish out-of-sample or Outer-set improvement.
- **HTC-TES v3**: integrated typed telemetry; a 5-case dry-run exercised the
  configured path but was not a validation study.
  - Type telemetry emitted FACTUAL, OMISSION and REASONING labels in the recorded exercise
  - Type→module routing: evidence_obligation, candidate_trigger, causal_link_test
  - A configured DeepSeek dry-run completed 5/5 executions; 4 typed cases produced experience records
  - The dry-run correctly reported 0% effective coverage because no authoritative detector improvement was established
  - Interface normalization: `_normalize_signal` resolves AttributionSignal/dict mismatch

Formal artifacts report occurrence-first three-state confusion, selective
accuracy/risk, paired transitions and exact McNemar statistics. A detector
improvement or champion label is ineligible when changes are abstention-only,
paired correctness gain is not significant, the deterministic paired
net-correctness-gain interval is unavailable/non-positive, the result is not
from the sealed final-holdout/Outer role, or critical provenance is incomplete.
Probe, validation and test results remain development observations; validation
and test are optional legacy aliases and need not both exist. The currently
registered `P0_diagnostic_only` configuration has
`D_execution_authorized=false`, so it cannot authorize probe or final-holdout
effectiveness execution.

## License

Private research repository.
