"""Stable public facade of the frozen detector package.

Application-facing boundary for standalone callers, the CLI and — through
``evolution_api`` — the future self-evolution framework. Deployment of
evolved capability is explicit (``genome`` / ``policy_genome`` arguments);
no framework-global state exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.pipeline.batch import (
    collect_replica_predictions,
    label_free_record,
    load_batch_records,
    run_batch,
    run_replicated_batch,
)
from sehd_rise2.detector.pipeline.core import (
    DETECTOR_SCHEMA_VERSION,
    FORBIDDEN_INPUT_FIELDS,
    REQUIRED_ROUTES,
    adjudicate_case,
    detect_record,
    load_detector_config,
    load_policy_genome,
    load_skill_genome,
    resolve_detector_config,
    validate_record,
)
from sehd_rise2.detector.pipeline.replicas import (
    POLICIES,
    POLICY_CONSENSUS,
    POLICY_MAJORITY,
    aggregate_replicas,
    aggregate_states,
)


def default_config_path() -> Path:
    """Path of the packaged detector config (deterministic, cwd-independent)."""
    return Path(__file__).resolve().parent / "configs" / "detector_v3_5.json"


def detect(
    record: Mapping[str, Any],
    *,
    case_id: str = "case",
    config: Mapping[str, Any] | None = None,
    genome: Any | None = None,
    genome_source: str | None = None,
    policy_genome: Any | None = None,
    adjudicator: Any | None = None,
) -> dict[str, Any]:
    """Run one detection; ``config=None`` loads the packaged detector config."""
    if config is None:
        config = load_detector_config(default_config_path())
    return detect_record(
        record=dict(record), config=config, case_id=case_id,
        genome=genome, genome_source=genome_source,
        policy_genome=policy_genome, adjudicator=adjudicator,
    )


__all__ = [
    "DETECTOR_SCHEMA_VERSION",
    "FORBIDDEN_INPUT_FIELDS",
    "POLICIES",
    "POLICY_CONSENSUS",
    "POLICY_MAJORITY",
    "REQUIRED_ROUTES",
    "adjudicate_case",
    "aggregate_replicas",
    "aggregate_states",
    "collect_replica_predictions",
    "default_config_path",
    "detect",
    "detect_record",
    "label_free_record",
    "load_batch_records",
    "load_detector_config",
    "load_policy_genome",
    "load_skill_genome",
    "resolve_detector_config",
    "run_batch",
    "run_replicated_batch",
    "validate_record",
]
