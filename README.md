# SEHD v6 — Evidence-routed Skill Evolution for Agent Hallucination Detection

## Architecture

```
SEHD v6
├── src/sehd_v6/
│   ├── core/
│   │   ├── paca_tcl/      # PACA-TCL v3.5: Court-style causal attribution detector
│   │   │   ├── proof_contract.py
│   │   │   ├── proof_engine.py
│   │   │   └── proof_evaluator.py
│   │   ├── bccse/          # BCCSE v5: Self-evolution framework
│   │   │   ├── genome.py
│   │   │   ├── evolution_runner.py
│   │   │   ├── reflection_engine.py
│   │   │   ├── fitness.py
│   │   │   ├── safety_gate.py
│   │   │   ├── step_attributor.py
│   │   │   ├── signal_adapter.py
│   │   │   └── curriculum.py
│   │   └── htc_tes/        # HTC-TES v3: Hallucination-Typed Conditioning
│   │       ├── type_classifier.py
│   │       ├── type_conditioned_evolution.py
│   │       └── type_aware_fitness.py
│   ├── data/
│   │   ├── evolution_sets/
│   │   ├── validation_sets/
│   │   ├── records/
│   │   ├── outputs/
│   │   └── baselines/
│   ├── configs/
│   └── utils/
├── 过程文档/               # Design documents (Chinese)
└── archive/                # Legacy code and data
```

## Key Concepts

- **PACA-TCL v3.5**: Court-style causal attribution detector with 3-stage pipeline (H1 Prosecution → H2 Consensus Gate → H3 Dual-phase Localization), 5-dimensional CRS scoring (factuality 0.35 / counterfactual 0.15 / recovery 0.20 / process 0.20 / scope 0.10), 3-state output (verified_error / verified_clean / unresolved).

- **BCCSE v5**: 8-module natural language rule genome (candidate_trigger, evidence_obligation, c_to_v_route, atomic_verifier, clean_defense, causal_link, origin_tiebreak, abstention). Self-evolution loop: detect → attribute → LLM causal reflection → 3-layer gate → apply edit → genome update.

- **HTC-TES v3**: Hallucination-Typed Conditioning for Taxonomy-conditioned Evolution of Skills. 3-dimensional conditioning (input type × output direction × process stage). Hallucination type classifier, type-conditioned evolution strand mechanism, type-aware process-level fitness, structured rule spec, enhanced reflection engine, typed experience library, Decisive Accuracy × Coverage Rate metrics.

## Usage

```bash
# Partition data
python -m src.sehd_v6.main partition

# Run evolution
python -m src.sehd_v6.main evolve --size 50 --use-gold

# Evaluate
python -m src.sehd_v6.main evaluate --set validation --genome <path>
```

## Methodological Innovation

1. **Process Reward Model (PRM)**: Score reasoning steps, not just final results.
2. **Causal Attribution & Counterfactual Reasoning**: Identify root causes through intervention and counterfactual analysis.
3. **Hierarchical Fitness**: Strong acceptance (complete closure) + Weak acceptance (local predicate improvement).
4. **Type-Conditioned Evolution**: Learn different detection strategies for different hallucination types.

## Status

- **v5**: Production — BCCSE online evolution with 50-case experiments completed.
- **v6 (HTC-TES)**: Active development — taxonomy-conditioned evolution with structured fitness.

## License

Private research repository.
