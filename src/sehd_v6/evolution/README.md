# Standalone Self-Evolution

The evolution boundary is:

```text
raw trajectories -> detector -> self-evolution -> experience memory + champion genome
```

Public API:

```python
from pathlib import Path
from src.sehd_v6.evolution import run_evolution

result = run_evolution(
    records={
        "case-001": {"history": [{"role": "assistant", "content": "..."}]},
        "case-002": {"history": [{"role": "assistant", "content": "..."}]},
    },
    detector_config="src/sehd_v6/configs/paca_tcl_detector_v3_5.json",
    output_dir=Path("evolution_output"),
)
```

Or from a file:

```python
result = run_evolution(
    records=Path("records.jsonl"),   # JSON mapping or JSONL
    detector_config=Path("detector_config.json"),
    output_dir=Path("evolution_output"),
)
```

Run it directly:

```bash
python -m src.sehd_v6.evolution \
  --records records.jsonl \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --output-dir evolution_output
```

## Records input

`--records` accepts:

- a JSON object mapping `case_id -> record`
- a JSON object containing a `history` key (single record)
- a JSONL file; each line is either `{"case_id": "...", "record": {...}}` or
  a record with an embedded `case_id`

Each record is a raw trajectory with a non-empty `history`. Evaluator-only
fields such as `gold_label`, `is_hallucination` and `hallucination_step` may be
present for the optional gold-enhanced mode; they are stripped before every
detector call.

## The closed loop

For each case, in order:

1. **Detect**: run PACA-TCL v3.5 with the current skill genome injected
   (`src/sehd_v6/detector` boundary; census cache optional).
2. **Attribute**: self-supervised causal process reflection (no gold needed).
3. **Classify**: HTC-TES type conditioning routes the failure to target
   modules.
4. **Reflect/Mutate**: LLM or template reflection proposes a typed skill edit.
5. **Gate**: structural constraint gate validates edit safety.
6. **Fitness**: source replay plus rolling stable-case canaries; a regression
   rejects the edit.
7. **Commit/Rollback**: one-edit shadow commit with canary non-regression.
8. **Experience**: HTC-TES experience memory is updated.

Artifacts written to `output_dir`:

- `evolution_result.json` — full audit trail
- `champion_genome.json` — the evolved skill genome
- `htc_tes_experience_memory.json` — typed experience memory
- `htc_tes_coverage.json` — type coverage summary

## Continuing the loop

The next run can resume from the previous artifacts:

```python
result = run_evolution(
    records=records,
    detector_config=detector_config,
    output_dir=Path("evolution_output_2"),
    initial_genome=Path("evolution_output/champion_genome.json"),
    experience_memory_path=Path(
        "evolution_output/htc_tes_experience_memory.json"
    ),
)
```

## What is not required

The standalone evolution module does not read or require:

- data partitions or manifests
- development probes (`probe_ids`, probe schedules)
- pre-registered canary case pools
- execution authorization flags or experiment phases
- a precomputed census cache (`census_root` is optional)

These are experiment protocols and belong to experiment orchestration, not to
the framework loop.

## Provider configuration

Detector routes use the detector precedence documented in
`src/sehd_v6/detector/README.md`. The mutation/reflection route defaults to
`SEHD_MUTATION_*` (official DeepSeek when unset) and can be overridden with
`--mutation-route route.json`.
