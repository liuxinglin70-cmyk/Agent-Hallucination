"""Independent hallucination detector package.

This is the stable application-facing boundary.  Experiment commands and
evolution orchestration may consume its result, but are not required to load
or execute it.
"""

from ..core.paca_tcl.detector import (
    detect_record,
    load_detector_config,
    resolve_detector_config,
    validate_record,
)
from ..core.paca_tcl.evolution_contract import EvolutionDetection, project_for_evolution

__all__ = [
    "detect_record",
    "load_detector_config",
    "resolve_detector_config",
    "validate_record",
    "EvolutionDetection",
    "project_for_evolution",
]
