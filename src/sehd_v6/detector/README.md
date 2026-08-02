# Standalone Detector

The detector boundary is:

```text
label-free trajectory -> detector -> label-free detection result
                                      -> optional evolution projection
```

Public API:

```python
from src.sehd_v6.detector import detect_record, project_for_evolution

prediction = detect_record(
    record={"history": [{"role": "assistant", "content": "answer"}]},
    config=resolved_detector_config,
    case_id="case-001",
)
evolution_input = project_for_evolution(prediction).to_dict()
```

Required raw input shape:

```json
{
  "question": "optional task text",
  "history": [
    {
      "step": 1,
      "role": "assistant",
      "content": "agent message",
      "tool_calls": [],
      "tool_responses": []
    }
  ]
}
```

`history` must be a non-empty list. The packet adapter assigns stable event and
tool-interaction IDs. `tool_calls` and `tool_responses` are optional; when
present, their order defines the interaction pairing used by evidence
certificates.

The detector does not read or require partition files, manifests, experiment
plans, execution authorization flags, gold labels, or BCCSE state. A census
cache is optional. If no `census_root` is supplied, candidate census is
requested through the configured provider route.

Run it directly:

```bash
python -m src.sehd_v6.detector \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --record trajectory.json \
  --case-id case-001 \
  --output-dir detector_output
```

Run independent trajectories concurrently with hash-bound resume:

```bash
python -m src.sehd_v6.detector \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --records trajectories.jsonl \
  --max-parallel 10 \
  --output-dir detector_output/batch
```

`--records` accepts JSON, JSONL, a directory of trajectory JSON files, or a
JSON manifest containing `{"cases":[{"case_id":"...","record_path":"..."}]}`.
Every case has an isolated directory with an atomically written
`prediction.json` and hash-bound `completion.json`. A subsequent invocation
resumes only cases whose input, resolved detector config and prediction hashes
still match. Failed or interrupted cases are retried; `--no-resume` forces all
cases to run again. Dataset labels are stripped before `detect_record()` and
are never included in provider prompts or detector output.

## Provider Configuration

`load_detector_config()` resolves route values without experiment policy. The
precedence is:

1. `SEHD_ROUTE_<ROUTE>_*`
2. `SEHD_DETECTOR_*` or `SEHD_JUDGE_*`
3. `SEHD_PROVIDER_*`
4. JSON route values
5. official provider defaults

Supported suffixes:

`ENDPOINT`, `MODEL`, `API_KEY_ENV`, `TEMPERATURE`, `MAX_TOKENS`,
`TRANSPORT_RETRIES`, `TIMEOUT_SECONDS`, `TOTAL_TIMEOUT_SECONDS`.

The selected credential is read only when a provider call is made or when
`load_detector_config(..., require_credentials=True)` is used. Process
environment values override repository `.env` values.

The API transport is OpenAI-compatible and sends requests to
`<ENDPOINT>/chat/completions` with bearer authentication, JSON response mode,
configured temperature/token limits, bounded retries, and a total timeout.
Provider responses must decode to JSON objects; detector ledger routes must
also return a `candidates` list.

## Skill Loading

The baseline detector has no BCCSE dependency. An evolved `SkillGenome` can be
passed explicitly with `detect_record(..., genome=genome)`. Skill integration
is loaded lazily only for that call and injects route-specific rule deltas into
provider system prompts. Different genomes are serialized inside one process
because the legacy proof engine binds provider functions at module scope;
parallel evaluation of different genomes should use separate processes.
