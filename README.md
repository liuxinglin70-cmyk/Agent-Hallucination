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

**Evolution loop** (per case):
1. **Detect**: Run PACA-TCL v3.5 with current genome injected
2. **Attribute**: Self-supervised trigger check (no gold required)
3. **Reflect**: LLM causal process reflection generates edit proposal
4. **Gate**: Structural constraint gate validates edit safety
5. **Fitness**: Source replay + batch canary audit
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
| Meso | Process-level fitness | `type_aware_fitness.py` evaluates per-type with DACR metric |
| Micro | Typed experience memory | `TypeExperienceMemory` persists per-type success/failure patterns |

**8 Hallucination Types**: FACTUAL, INSTRUCTIONAL, TOOL, REASONING, OMISSION, ADOPTION, CONTEXTUAL, UNKNOWN

**Type→Module Routing** (primary targets):
- FACTUAL → evidence_obligation, atomic_verifier
- INSTRUCTIONAL → clean_defense, abstention
- TOOL → c_to_v_route, atomic_verifier
- REASONING → causal_link, origin_tiebreak
- OMISSION → candidate_trigger, c_to_v_route
- ADOPTION → evidence_obligation, clean_defense
- CONTEXTUAL → abstention, clean_defense

**DACR Metric**: Decisive Accuracy × Coverage Rate
- Decisive Accuracy = correct decisive judgments / total decisive judgments
- Coverage Rate = hallucination types with successful detection experience / total types

## Usage

```bash
# Partition data
python -m src.sehd_v6.main partition

# Run evolution (with HTC-TES v3 type conditioning)
python -m src.sehd_v6.main evolve --size 50 --use-gold

# Evaluate champion genome
python -m src.sehd_v6.main evaluate --set validation --genome <path>
```

## Key Design Decisions

1. **Natural language rules** (not typed DSL): LLM generates rules directly; no compilation step needed.
2. **Self-supervised triggers**: Evolution can proceed without gold labels (unresolved, CRS contradiction, evidence incomplete, boundary hesitation).
3. **Incremental batch commit**: Each edit validated independently; failed edits don't discard successful ones.
4. **Shared LLM cache**: Same genome+case replay reuses cache, eliminating CRS nondeterminism.
5. **Structural prevention > post-hoc verification**: Gate prevents unsafe edits structurally, not by expensive canary re-evaluation.

## Module Dependencies

```
main.py
  └── evolution_runner.py
        ├── genome.py (SkillGenome, SkillEdit, apply_edit)
        ├── step_attributor.py (attribute_failure, ErrorType)
        ├── reflection_engine.py (build_causal_reflection_prompt)
        ├── fitness.py (assess_source_fitness)
        ├── safety_gate.py (StructuralGate)
        ├── types.py (EvaluationOutcome, ExperienceRecord) ← breaks circular dep
        ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
        ├── mutator.py (MutationProvider, AtomicMutation)
        └── htc_tes/ (HallucinationTypeClassifier, TypeConditionedEvolution, TypeAwareFitness)

proof_evaluator.py
  ├── proof_engine.py (adjudicate_case, evaluate)
  ├── types.py (EvaluationOutcome) ← breaks circular dep
  ├── step_attributor.py (ErrorType)
  ├── genome.py (SkillGenome)
  ├── promotion_gates.py (MetricSnapshot, ReplayEvidence)
  └── skill_integration.py (integrated_v35_provider)
```

## Status

- **PACA-TCL v3.5**: Frozen production base
- **BCCSE v5**: Production — 50-case evolution experiments completed
- **HTC-TES v3**: Integrated into evolution loop, pending dry-run validation

## License

Private research repository.
