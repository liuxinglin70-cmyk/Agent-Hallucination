"""One place that says what an experiment is, runs it, and reports what happened.

The repository grew five scripts that each answered part of this question --
``run_baseline_693``, ``freeze_measurement_manifest``, ``freeze_measured_prefix``,
``paired_endpoint``, ``experiment_status`` -- and every one of them re-derived
"which cases", "which arm", "what counts as measured" from its own conventions.
That is how the 2026-09-23 run ended up with a baseline of 204 authoritative
rows, 134 invalid ones, no deployed arm at all, and a report whose headline
numbers had to be assembled by hand from four files.

This package states the model once:

* :mod:`experiments.plan` -- the pre-registered plan: the measurement set, its
  order, the stages, and the criteria fixed before any reading exists.
* :mod:`experiments.report` -- one report contract, where every case is
  accounted for as exactly one of measured / not measured, and the cost and
  concurrency readings come from the run itself.
* :mod:`experiments.runner` -- executes a stage under the runtime guarantees
  (bounded submission, one writer, fatal stop, per-attempt records).

The old scripts keep working; they are thin wrappers over the same primitives
so an archived command still reproduces its run.
"""

from __future__ import annotations

from experiments.plan import (
    ExperimentPlan,
    MeasurementSet,
    load_plan,
)
from experiments.report import StageReport, summarize_stage
from experiments.runner import (
    StageOutcome,
    plan_status,
    run_baseline_stage,
    run_measurement_stage,
)

__all__ = [
    "ExperimentPlan",
    "MeasurementSet",
    "StageOutcome",
    "StageReport",
    "load_plan",
    "plan_status",
    "run_baseline_stage",
    "run_measurement_stage",
    "summarize_stage",
]
