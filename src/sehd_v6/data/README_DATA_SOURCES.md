# SEHD v6 Data and Artifact Notes

This directory contains research artifacts copied from several historical
experiments. File presence is not evidence that an artifact is current,
reproducible, formally evaluated, or eligible to be called a champion.

## Registered manifests and caches

- `stratified_evolution_set.json`: historical evolution case manifest.
- `validation_set_ids.json`: historical validation case manifest.
- `data_partition_v5.json`: historical partition artifact.
- `baseline_predictions_by_id.json`: small cached baseline subset keyed by ID.

The partition CLI requires an explicit Inner/Outer role on every input row; one
Outer marker does not make all unmarked rows Inner. A newly generated partition
must carry input hashes, D/P0 role counts, explicit Outer-role evidence, and a
pairwise mutual-exclusion/coverage audit. It emits `outer_set_ids.json` only
when every Outer row contains a real loadable case ID whose forward-derived
case reference matches. The manifest binds IDs, per-case roles and the
partition payload by SHA-256. Missing IDs fail closed; they are never recovered
from case references.

## Historical genomes and results

- `champion_genome_v13.json`, `champion_genome_v51.json`,
  `champion_genome_v52.json`
- `evolution_result_v51.json`, `evolution_result_v52.json`
- `eval_metrics_v52_full.json`, `eval_results_v51.json`

These are historical reference artifacts. Some use older genome hash, rule, or
metric contracts and may be rejected by strict lineage replay. Their filenames
do not confer current champion status. In particular, an abstention-only
transition, a non-significant paired correctness change, or an inner-set result
without a confidence interval supporting positive gain and Outer evaluation
is not eligible for a detector-improvement or champion claim.

Current evolution runs may also write:

- `htc_tes_coverage.json`: typed telemetry summary, not detector accuracy.
- `htc_tes_experience_memory.json`: typed observations, trajectory feature
  descriptions, strategies, directions and source-replay outcomes.

## Prediction JSONL artifacts

Prediction files such as `skill_predictions_analysis.jsonl`,
`skill_predictions_r2_validation.jsonl`, `predictions_cesv_v41_r1.jsonl`,
`predictions_cesv_v42_r2.jsonl`, and `predictions_paca_tcl_v3_5_r1.jsonl` are
research outputs. Metrics should use occurrence-first `incident_state`, with
legacy `state` only as a compatibility fallback. Negative `unresolved` rows
must remain unresolved in the canonical three-state confusion matrix; only the
explicitly labelled binary-compatibility view may collapse them to negative.

## External source locations

Large source data and run trees are not duplicated here. Historical local
locations include:

- `tmp_external/AgentHallu/AgentHallu/`: AgentHallu case JSON files.
- `tmp/bccse_analysis_assets/d_baseline_v3_5/round1/predictions.jsonl`:
  historical D baseline cache.
- `tmp/all_runs/` and `tmp/bccse_analysis_assets/bccse_v5/eval/`: historical
  run/evaluation trees.
- `验证文件/agent-hallucination-pilot/`: historical configs, artifacts,
  results, runs, outputs and selection manifests.

Paths are descriptive provenance hints, not proof that the files are present
on another machine. Formal run artifacts hash every available critical input
(code, config, data/case sequence, genome, provider configuration and caches).
If a critical source is unavailable, reproducibility is explicitly reported as
`not_reproducible`; no hash or provenance value is fabricated.

## Current authorization and Outer boundary

The repository's PACA-TCL v3.5 configuration is registered for
`P0_diagnostic_only` and records `D_execution_authorized=false`. It can support
authorized P0 diagnostics, but it cannot authorize validation/test
or Outer effectiveness execution. A formal Outer run additionally requires
`outer_execution_authorized=true` and exactly one valid `outer_set_ids.json`.
It reads a genome without mutation or experience/champion writeback and records
role-isolated final-evaluation semantics. Code does not establish secrecy or
one-shot custody: champion eligibility also requires
`outer_custody_verified=true`, recording an external governance attestation
that must be checked through artifact and authorization audit.

Historical artifacts with zero Outer exposure, reference-only Outer rows, or
no partition/role-bound real-ID manifest remain invalid for Outer or champion
claims. They must not be rewritten or re-labelled as compliant evidence.
