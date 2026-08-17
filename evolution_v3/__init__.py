"""SEHD-RISE3 self-evolution framework for detector_v2 (V3).

The framework implements the admission protocol from
``SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md``:

- bare-base patrol and mechanical failure families (worksheet);
- three intervention types with the closed hook/action vocabularies;
- V1 structural, V2 sentinel harmlessness, V3 claim/behavior consistency,
  and M marginal-gain gates (verifier-as-gatekeeper);
- append-only hash-chained ledger, pre-commit registry, library limits,
  defect-injection audit, and meta-evaluation.
"""

from evolution_v3.engine import EvolutionEngine, run_experiment

__version__ = "0.1.0"

__all__ = ["EvolutionEngine", "run_experiment", "__version__"]
