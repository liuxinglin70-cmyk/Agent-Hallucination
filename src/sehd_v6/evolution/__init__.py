"""Independent self-evolution module.

The module owns the detector -> evolution -> experience closed loop and does
not require partitions, manifests, development probes or experiment
authorization.  Experiment orchestration may consume its result later, but is
not needed to run the framework.
"""

from ..core.bccse.evolution_runner import (
    EvolutionStep,
    OnlineEvolutionResult,
    run_online_evolution,
)
from ..core.bccse.genome import (
    SkillEdit,
    SkillGenome,
    SkillOp,
    create_baseline_genome,
)
from .runner import (
    DEFAULT_REFLECTION_CANDIDATES,
    default_mutation_route,
    load_records,
    run_evolution,
)
from .state_manifest import (
    load_state_manifest,
    write_state_manifest,
)

__all__ = [
    "run_evolution",
    "run_online_evolution",
    "load_records",
    "default_mutation_route",
    "DEFAULT_REFLECTION_CANDIDATES",
    "load_state_manifest",
    "write_state_manifest",
    "OnlineEvolutionResult",
    "EvolutionStep",
    "SkillGenome",
    "SkillEdit",
    "SkillOp",
    "create_baseline_genome",
]
