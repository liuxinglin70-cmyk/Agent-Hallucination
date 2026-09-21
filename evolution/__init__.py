"""Controlled self-evolution through validated detector interventions.

Patrol -> failure families -> proposals -> structural/harmlessness/behavior/
marginal gates -> registry admission, attribution, retirement and rollback.
See docs/technical-solution.md for the runtime contracts.
"""

from evolution.engine import EvolutionEngine, run_experiment

__version__ = "0.1.0"

__all__ = ["EvolutionEngine", "run_experiment", "__version__"]
