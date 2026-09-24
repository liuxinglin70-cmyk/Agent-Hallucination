"""Execute an experiment stage and report it under one contract.

A stage is a list of cases, an arm, and a run tree. This module owns the
mechanics that every stage needs and that the five ad-hoc scripts each
reimplemented: resolve the registry for the arm, run the batch under the
runtime guarantees, score what was measured, and emit a report where the
requested list and the measured list are both visible.

Re-running a stage is the normal case, not the exception -- the measured mean
was 21.7 minutes per case, so a corpus pass is stopped and resumed many times.
Two properties follow from that and are enforced here rather than left to the
caller:

* a resumed stage keeps the *same* case order, because the frozen order is the
  pre-registration and a re-sorted list is a different sample;
* a resumed stage re-measures only cases that are genuinely unmeasured, so
  "resume" cannot silently convert an outage row into a decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from detector.evaluation import evaluate_run
from detector.interventions import load_intervention_registry
from detector.result import prediction_state
from detector.runner import run_batch, safe_case_id
from detector.runtime import RunControl, exclusive_writer

from experiments.plan import ExperimentPlan
from experiments.report import summarize_stage

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class StageOutcome:
    """What a stage produced: the frozen list, the rows, and the report."""

    stage: str
    arm: str
    requested: list[str]
    batch: dict[str, Any]
    rows: list[dict[str, Any]]
    report: dict[str, Any]
    run_id: str

    @property
    def measured(self) -> list[str]:
        return [
            str(row["case"]) for row in self.rows
            if prediction_state(row.get("result") or {}) is not None
        ]

    def measured_prefix(self) -> list[str]:
        """The longest leading run of the requested list that was measured.

        This is the sample a paired comparison may use. A gap anywhere stops
        the prefix, because "the first N in the frozen order" is the only
        subset of a pre-registered order that nobody chose after the fact.
        """
        measured = set(self.measured)
        prefix: list[str] = []
        for case in self.requested:
            if case not in measured:
                break
            prefix.append(case)
        return prefix


def _resolve_registry(arm: str, registry: Mapping[str, Any] | None,
                      path: str | Path | None) -> Mapping[str, Any] | None:
    if registry is not None:
        return registry
    if arm == "empty":
        return None
    if path is None:
        raise ValueError(
            f"arm {arm!r} needs an intervention registry: pass --interventions"
        )
    return load_intervention_registry(path, require_executable_bounds=True)


def _load_rows(runs_root: Path, run_id: str, cases: Sequence[str]) -> list[dict[str, Any]]:
    """Read the run tree back into the row shape the report consumes."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        case_dir = runs_root / run_id / "cases" / safe_case_id(case)
        result_path = case_dir / "result.json"
        if not result_path.is_file():
            continue
        stamp = case_dir / "elapsed_seconds.json"
        elapsed = None
        if stamp.is_file():
            try:
                elapsed = json.loads(stamp.read_text(encoding="utf-8-sig")).get(
                    "elapsed_seconds"
                )
            except ValueError:
                elapsed = None
        rows.append({
            "case": case,
            "result": json.loads(result_path.read_text(encoding="utf-8-sig")),
            "elapsed_seconds": elapsed,
        })
    return rows


def run_measurement_stage(
    *,
    plan: ExperimentPlan,
    stage: str,
    arm: str,
    cases: Sequence[str] | None = None,
    runs_root: str | Path,
    run_id: str,
    dataset_root: str | Path,
    registry: Mapping[str, Any] | None = None,
    interventions_path: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
    workers: int = 4,
    deadline_seconds: float | None = None,
    retry_unresolved: bool = False,
    report_path: str | Path | None = None,
    notes: Sequence[str] = (),
) -> StageOutcome:
    """Run one arm over the measurement set (or a prefix of it) and report it."""
    requested = [str(case) for case in (cases if cases is not None else plan.measurement.cases)]
    resolved = _resolve_registry(arm, registry, interventions_path)
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with exclusive_writer(runs_root / run_id):
        batch = run_batch(
            dataset_root=dataset_root, cases=requested, run_id=run_id,
            runs_root=runs_root, config=config, interventions=resolved,
            workers=workers, retry_unresolved=retry_unresolved,
            deadline_seconds=deadline_seconds,
            control=RunControl(max_provider_inflight=max(1, int(workers))),
        )
    wall = time.monotonic() - started
    rows = _load_rows(runs_root, run_id, requested)
    measured = [
        str(row["case"]) for row in rows
        if prediction_state(row.get("result") or {}) is not None
    ]
    evaluation = None
    if measured:
        evaluation = evaluate_run(
            runs_root=runs_root, run_id=run_id,
            dataset_root=dataset_root, cases=measured,
        )
    report = summarize_stage(
        stage=stage, arm=arm, requested=requested, batch_summary=batch,
        rows=rows, evaluation=evaluation, wall_seconds=round(wall, 1),
        notes=notes,
    )
    report["measurement_prefix"] = {
        "size": len(_prefix_of(requested, measured)),
        "cases": _prefix_of(requested, measured),
        "rule": (
            "the longest leading run of the frozen order that was measured; a "
            "gap stops it, because a subset of a pre-registered order chosen "
            "after the fact is not pre-registered"
        ),
    }
    if report_path is not None:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["report_path"] = str(out)
    return StageOutcome(
        stage=stage, arm=arm, requested=requested, batch=batch, rows=rows,
        report=report, run_id=run_id,
    )


def _prefix_of(requested: Sequence[str], measured: Sequence[str]) -> list[str]:
    seen = set(measured)
    prefix: list[str] = []
    for case in requested:
        if case not in seen:
            break
        prefix.append(case)
    return prefix


def run_baseline_stage(**kwargs: Any) -> StageOutcome:
    """The baseline arm: no interventions, over the frozen measurement order."""
    return run_measurement_stage(**{**kwargs, "arm": "empty"})


def _shared_registry(plan: ExperimentPlan, arms: Sequence[str]) -> bool:
    """Whether every named arm deploys the same registry.

    Two rolls of one configuration are the instrument's own jitter. Reporting
    them as a paired effect would call noise a result, which is exactly how a
    "2 fixed, 1 broken" reading between two baseline rolls would have been
    quoted -- and the terminal criterion only reaches p<0.05 at five net
    corrections, so nothing at this scale is an effect either way.
    """
    registries = {
        str((plan.arms.get(arm) or {}).get("registry") or "empty")
        for arm in arms
    }
    return len(registries) <= 1


def paired_sample(
    plan: ExperimentPlan, runs_root: str | Path, arms: Sequence[str],
) -> dict[str, Any]:
    """The cases every named arm measured, and what conditioning on them costs.

    Two samples are defensible and they answer different questions:

    * the **contiguous prefix** of the frozen order is the strict
      pre-registration -- "the first N cases", chosen by nothing but the order;
    * the **common measured set** is everything the arms can actually be paired
      on, which is what a paired delta requires.

    The second is larger and is *not* result-dependent: which cases survived
    measurement is decided by the provider and the clock, not by the verdict.
    It is, however, difficulty-dependent -- the slow cases are exactly the ones
    an outage removes -- so a comparison on it is biased toward the easier part
    of the corpus, and the report has to say that out loud rather than letting
    a reader assume the sample is representative.
    """
    runs_root = Path(runs_root)
    measured: dict[str, list[str]] = {}
    for arm in arms:
        rows = _load_rows(runs_root, arm, plan.measurement.cases)
        measured[arm] = [
            str(row["case"]) for row in rows
            if prediction_state(row.get("result") or {}) is not None
        ]
    if not measured:
        return {"arms": {}, "common": [], "size": 0}
    common = set(plan.measurement.cases)
    for arm in arms:
        common &= set(measured.get(arm, ()))
    ordered = [case for case in plan.measurement.cases if case in common]
    return {
        "arms": {arm: len(cases) for arm, cases in measured.items()},
        "size": len(ordered),
        "common": ordered,
        "note": (
            "the common measured set is chosen by which cases survived "
            "measurement, not by their verdicts; because slow cases are the "
            "ones an outage removes, it is biased toward the easier part of "
            "the corpus and must not be read as a representative sample"
        ),
    }


def plan_status(plan: ExperimentPlan, runs_root: str | Path) -> dict[str, Any]:
    """Which arms exist and how far each one got, without running anything."""
    runs_root = Path(runs_root)
    arms: dict[str, Any] = {}
    for name, spec in plan.arms.items():
        run_dir = runs_root / name
        if not run_dir.is_dir():
            arms[name] = {"exists": False, "role": spec.get("role")}
            continue
        rows = _load_rows(runs_root, name, plan.measurement.cases)
        measured = [
            str(row["case"]) for row in rows
            if prediction_state(row.get("result") or {}) is not None
        ]
        arms[name] = {
            "exists": True,
            "role": spec.get("role"),
            "rows": len(rows),
            "measured": len(measured),
            "measured_prefix": len(_prefix_of(plan.measurement.cases, measured)),
        }
    shared = None
    if arms:
        prefixes = [
            value.get("measured_prefix", 0) for value in arms.values()
            if value.get("exists")
        ]
        shared = min(prefixes) if prefixes else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement_cases": plan.measurement.size,
        "stages": list(plan.stages),
        "arms": arms,
        "shared_prefix": shared,
        "note": (
            "an arm's measured_prefix is how many cases it can contribute to a "
            "paired comparison; shared_prefix is the sample every existing arm "
            "can be paired on"
        ),
    }
