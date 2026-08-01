# SEHD v6 — Evidence-routed Skill Evolution for Agent Hallucination Detection

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
│   ├── dry_run_5.py      # 5-case dry-run validation (mock or configured LLM)
│   └── bccse_v5_data_partition.py  # Stratified data partitioning
└── .gitignore
```

## Core Methodology

### 1. PACA-TCL v3.5 — Court-Style Causal Attribution Detector (Frozen Base)

The frozen detection base operates as a three-stage court-style pipeline:

- **H1 Prosecution**: Constructs a causal prosecution case identifying hallucination candidates from the agent trajectory. Produces error certificates with evidence chains.
- **H2 Consensus Gate**: Adversarial defense vs prosecution. Defense attempts to exonerate candidates via counter-evidence. Gate adjudicates based on certificate validity.
- **H3 Dual-Phase Localization**: When error is confirmed, localizes the responsible step via causal backtracking and earliest-origin tiebreak.

**Five-dimensional CRS Scoring** (frozen weights):
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
3. **Reflect**: LLM causal process reflection generates edit proposal
4. **Gate**: Structural constraint gate validates edit safety
5. **Fitness**: Source replay only; cross-case effectiveness is measured on the registered validation set
6. **Commit/Rollback**: Incremental shadow batch commit

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

**Bidirectional evolution**: Each type maintains both error-detection and clean-detection strands, resolving direction conflicts via type-specific guidance.

**Typed telemetry**: Directional decisiveness × type-experience coverage
- Without gold, decisive outcomes are not called correct; the metric records whether a typed process edit reached its declared direction
- Type-experience coverage is reported separately from detector accuracy
- A dry-run rule application is not counted as detection improvement unless source replay changes the authoritative detector outcome

## Usage

```bash
# 5-case dry-run validation (mock detections; optionally uses configured mutation LLM)
cd src
python -m sehd_v6.dry_run_5

# Optional mutation API environment (the key is never stored in the repository)
# PowerShell example:
# $env:SEHD_MUTATION_API_KEY = "<secret>"
# $env:SEHD_MUTATION_ENDPOINT = "https://api.deepseek.com"
# $env:SEHD_MUTATION_MODEL = "deepseek-v4-flash"
# $env:SEHD_MUTATION_API_KEY_ENV = "SEHD_MUTATION_API_KEY"

# Partition data
python -m src.sehd_v6.main partition

# Run evolution (with HTC-TES v3 type conditioning)
python -m src.sehd_v6.main evolve --size 50 --use-gold

# Evaluate champion genome
python -m src.sehd_v6.main evaluate --set validation --genome <path>
```

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
- `sehd_v6.core.paca_tcl` — frozen detector base
- `sehd_v6.core.htc_tes` — type conditioning layer

## Key Design Decisions

1. **Natural language rules** (not typed DSL): LLM generates rules directly; no compilation step needed.
2. **Self-supervised triggers**: Evolution can proceed without gold labels from unresolved, CRS contradiction, evidence-incomplete, and unavailable-signal conditions. It does not prescribe clean/error polarity without gold.
3. **Incremental batch commit**: Each edit validated independently; failed edits don't discard successful ones.
4. **Shared LLM cache**: Same genome+case replay reuses cache, eliminating CRS nondeterminism.
5. **Structural prevention > post-hoc verification**: Gate prevents unsafe edits structurally, not by expensive canary re-evaluation.
6. **Interface normalization**: HTC-TES modules accept both `AttributionSignal` dataclass and `Mapping[str, Any]` dict via a `_normalize_signal` helper, decoupling the type-conditioning layer from BCCSE's dataclass internals while maintaining a uniform access pattern.
7. **Bidirectional type-conditioned evolution**: Each hallucination type maintains both error-detection and clean-detection strands, preventing evolution direction conflicts.

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

This decouples HTC-TES from BCCSE's dataclass shape while allowing the production
code path (which passes `AttributionSignal` objects) to work without manual
`.to_dict()` conversion at every call site.

## Status

- **PACA-TCL v3.5**: Frozen production base
- **BCCSE v5**: Production — 50-case evolution experiments completed
- **HTC-TES v3**: Integrated into evolution loop; 5-case dry-run validation passed (5/5)
  - Type classification: FACTUAL, OMISSION, REASONING correctly identified
  - Type→module routing: evidence_obligation, candidate_trigger, causal_link_test
  - A configured DeepSeek dry-run completed 5/5 cases; 4 typed cases produced experience records
  - The dry-run correctly reported 0% effective coverage because no authoritative detector improvement was established
  - Interface normalization: `_normalize_signal` resolves AttributionSignal/dict mismatch

## License

Private research repository.
