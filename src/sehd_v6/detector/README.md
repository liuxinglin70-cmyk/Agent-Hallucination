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

Load an audited evolved genome explicitly:

```bash
python -m src.sehd_v6.detector \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --records trajectories.jsonl \
  --genome evolution_output/champion_genome.json \
  --output-dir detector_output/evolved
```

Genome loading validates the persisted content hash and exact registered module
set. Batch completion and resume artifacts are bound to the genome hash, so a
prediction produced by the baseline or a different champion is never reused.
Predictions and summaries record the active genome provenance. Loading remains
explicit: an evolution output is a candidate artifact, not an automatic
production promotion.

## Replicated Batch (Test-Time-Scaling Orchestration)

`run_replicated_batch` runs the frozen detector `k` times per case and
aggregates the three-state verdicts; it is the library-level entry point that
the experiment CLI (`experiments/run_decision_replicas.py`) is a thin shell
over. Each replica uses the same explicitly supplied genome, and its batch
resume identity includes that genome hash.

```python
from src.sehd_v6.detector import run_replicated_batch

report = run_replicated_batch(
    records=records,
    config=resolved_detector_config,
    output_root=Path("experiments/results/decision_replicas"),
    replicas=3,                # 1..MAX_REPLICAS (5)
    replica_policy="majority", # "majority" | "consensus"
    max_parallel=4,
    resume=True,
)
```

Contract:

- Replicas run **serially**; each replica gets an isolated provider cache via
  `PACA_GLOBAL_CACHE_ROOT` and the previous environment value is restored in
  a `finally` (or removed if it was unset).
- Aggregation is fail-closed: disagreement/ties, fewer-than-`k` replicas
  (`incomplete_replicas`), or any `runtime_valid != True` replica keep the
  case `unresolved`. Policies come from
  `src.sehd_v6.detector.replica_aggregation.POLICIES`.
- Temperature guard: when `replicas > 1` and every top-level `*_route`
  temperature is `0`/`0.0`/unset, the run degrades to a single replica and
  records `replica_degradation = {"reason": "temperature_zero", "requested": k}`
  instead of burning `k` identical calls. Temperatures are never modified
  implicitly.
- The report (`agenthallu.decision_replicas.v1`) is written atomically to
  `<output_root>/aggregated_report.json` and carries
  `config_hash / genome_hash / provenance / k / replicas_effective /
  replica_degradation / deterministic_route_warning / policy / case_count /
  unanimous_cases / cases`; per-replica artifacts live in
  `<output_root>/replica_<i>/`.

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

Within online evolution, each accepted edit is committed before the next source
case starts (`fitness_batch_size` is fixed at 1). The next case therefore runs
with the newly committed genome. The persisted champion is not loaded
implicitly by the standalone detector; pass `--genome` explicitly so promotion
is auditable and cannot silently change production behavior.

Label-free source replay and LLM agreement are process evidence, not correctness
authority. They may be retained as provisional telemetry and negative learning
feedback, but they do not certify a protected champion. Canary and contrast
replays are regression checks. Correctness-oriented promotion requires an
independent evaluation authority, such as a gold development set or a separate
evaluation agent whose inputs and artifacts are isolated from mutation.

The reflection engine also receives a structured runtime observation in
addition to the prose trace. It records observed executable fields and module
ownership so the generator must name one missing or malformed provider output
before proposing a rule. This is the first stage of the planned collaborative
reflection design, not a correctness vote.

Evolution reflection supports bounded test-time scaling through
`--reflection-candidates` (default `3` for the CLI). Multiple candidate rules are generated
by independent mutation provider calls, duplicate candidates are removed, and a
critic selects one replay candidate without rewriting it. The critic uses the
`reflection_critic` provider purpose and is a search-prioritization role, not an
independent correctness authority; source replay and separately authorized
evaluation remain responsible for evidence. The library API defaults to one
candidate for backward-compatible cost; the CLI opts into three.

The library also enforces `--max-mutation-calls` (CLI default `100`) across
generation, critic, attribution, and conflict-gate calls. Exhausting this budget
fails closed and is recorded in the evolution audit.

## Online Evolution Semantics

The online runner is sequential, not a static batch optimizer:

```text
case N detects with current genome
  -> candidate edit is applied to a shadow genome
  -> source replay evaluates the candidate
  -> batch commit updates champion/current genome
  -> case N+1 detects with that updated genome
```

The source and next-case genome hashes are recorded in the evolution steps.
Only committed edits are inherited; rejected edits are rolled back. A resumed
run restores the last committed champion and never resurrects an uncommitted
shadow edit.
