"""Public detection facade — the stable application-facing boundary.

Re-exports the detection layer's single-record, batch, and replica-aggregation
outlets.  The loop orchestration layer consumes detection through its own
internal imports; this module is the public surface for standalone callers
and the CLI.  Evolution-detection bridge types that existed in the frozen
package (``EvolutionDetection`` / ``project_for_evolution``) are absent
here: the canonical RepairPlan protocol (C6) replaces them, so the detection
facade no longer carries an evolution projection outlet.
"""

from sehd_rise.detection.batch import (
    collect_replica_predictions,
    label_free_record,
    load_batch_records,
    run_batch,
    run_replicated_batch,
)
from sehd_rise.detection.detector import (
    detect_record,
    load_detector_config,
    load_policy_genome,
    load_skill_genome,
    resolve_detector_config,
    validate_record,
)
from sehd_rise.detection.replicas import (
    POLICIES,
    POLICY_CONSENSUS,
    POLICY_MAJORITY,
    aggregate_replicas,
    aggregate_states,
)

__all__ = [
    "detect_record",
    "load_detector_config",
    "load_skill_genome",
    "load_policy_genome",
    "resolve_detector_config",
    "validate_record",
    "label_free_record",
    "load_batch_records",
    "run_batch",
    "run_replicated_batch",
    "collect_replica_predictions",
    "aggregate_replicas",
    "aggregate_states",
    "POLICIES",
    "POLICY_CONSENSUS",
    "POLICY_MAJORITY",
]
