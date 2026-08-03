"""Independent hallucination detector package.

This is the stable application-facing boundary.  Experiment commands and
evolution orchestration may consume its result, but are not required to load
or execute it.
"""

from ..core.paca_tcl.detector import (
    detect_record,
    load_skill_genome,
    load_detector_config,
    resolve_detector_config,
    validate_record,
)
from ..core.paca_tcl.evolution_contract import EvolutionDetection, project_for_evolution
from .batch import (
    collect_replica_predictions,
    label_free_record,
    load_batch_records,
    run_batch,
    run_replicated_batch,
)
from .replica_aggregation import (
    POLICY_CONSENSUS,
    POLICY_MAJORITY,
    POLICIES,
    aggregate_replicas,
    aggregate_states,
)

__all__ = [
    "detect_record",
    "load_skill_genome",
    "load_detector_config",
    "resolve_detector_config",
    "validate_record",
    "EvolutionDetection",
    "project_for_evolution",
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
