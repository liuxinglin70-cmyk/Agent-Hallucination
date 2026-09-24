"""One report contract for every stage of every experiment.

The rule this module enforces is simple to state and was violated by every
report the repository produced before it: **every case in the requested list is
accounted for exactly once**, as either measured or not measured, and the two
are never summed into a single "coverage" number that reads like a result.

The 2026-09-23 baseline is the worked example. Its summary said
``completed: 693`` while 134 of those rows were provider failures and 15 were
degraded; the accuracy that a reader would take from it was computed over the
204 rows that were actually measurements, and nothing in the file said so. A
report whose headline count and whose scoring denominator disagree is how a
truncated run gets quoted as a corpus result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from detector.measurement import measurement_status

REPORT_SCHEMA = "experiments.stage_report.v1"

#: The one status that counts as a reading. It is the name
#: ``detector.measurement.measurement_status`` gives an authoritative terminal
#: decision -- the same predicate ``detector.evaluation`` scores, so the report
#: and the scorer cannot disagree about which rows are measurements.
MEASURED = "authoritative"
NOT_MEASURED = "not_measured"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageReport:
    """What one stage actually did, with the two denominators kept apart."""

    stage: str
    arm: str
    requested: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    stop: dict[str, Any] = field(default_factory=dict)
    wall_seconds: float | None = None
    notes: list[str] = field(default_factory=list)

    # -- accounting -------------------------------------------------------

    def classification(self) -> dict[str, str]:
        """Each requested case mapped to exactly one measurement status."""
        seen: dict[str, str] = {}
        for row in self.rows:
            seen[str(row.get("case"))] = measurement_status(row.get("result") or {})
        for case in self.requested:
            seen.setdefault(str(case), "not_started")
        return seen

    def buckets(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {}
        for case, status in self.classification().items():
            buckets.setdefault(status, []).append(case)
        return {key: sorted(value) for key, value in buckets.items()}

    @property
    def measured_cases(self) -> list[str]:
        """Cases with a verdict the detector is willing to stand behind.

        Degraded rows are decisions under a reduced view rather than outages,
        so they are *reported* separately and *excluded* from scoring -- the
        distinction ``detector.evaluation`` already draws, restated here so a
        reader sees it in the report without opening the evaluator.
        """
        classification = self.classification()
        return sorted(
            case for case, status in classification.items()
            if status == MEASURED
        )

    @property
    def admitted_cases(self) -> list[str]:
        """Cases with an authoritative terminal decision -- the scoring set."""
        return sorted(
            str(row["case"]) for row in self.rows
            if str((row.get("result") or {}).get("decision_authority") or "")
            == "authoritative"
            and (row.get("result") or {}).get("state") in
            {"verified_error", "verified_clean"}
        )

    # -- readings ---------------------------------------------------------

    def readings(self, evaluation: Mapping[str, Any] | None = None) -> dict[str, Any]:
        metrics = dict((evaluation or {}).get("metrics") or {})
        return {
            "requested_cases": len(self.requested),
            "measured_cases": len(self.admitted_cases),
            "by_status": {key: len(value) for key, value in self.buckets().items()},
            "tp": metrics.get("tp"), "fp": metrics.get("fp"),
            "fn": metrics.get("fn"), "tn": metrics.get("tn"),
            "decisive_accuracy": metrics.get("decisive_accuracy"),
            "all_case_accuracy": metrics.get("all_case_accuracy"),
            "step_localization": metrics.get("step_localization"),
        }

    def denominator_audit(self) -> dict[str, int]:
        """Decompose requested cases into strict mutually exclusive categories."""
        by_status = {key: len(value) for key, value in self.buckets().items()}
        attempted = len(self.rows)
        authoritative = len(self.admitted_cases)
        semantic_unresolved = by_status.get("semantic_unresolved", 0)
        healthy = authoritative + semantic_unresolved
        degraded = by_status.get("degraded", 0)
        infra = (
            by_status.get("infrastructure_failure", 0)
            + by_status.get("fatal_provider", 0)
        )
        not_started = len(self.requested) - attempted
        return {
            "requested_n": len(self.requested),
            "attempted_n": attempted,
            "healthy_n": healthy,
            "authoritative_n": authoritative,
            "semantic_unresolved_n": semantic_unresolved,
            "degraded_n": degraded,
            "infrastructure_failure_n": infra,
            "not_started_n": max(0, not_started),
        }

    def cost(self) -> dict[str, Any]:
        """Wall time, provider attempts and tokens, from the rows themselves."""
        elapsed = 0.0
        attempts = 0
        usage: dict[str, Any] = {}
        missing_usage = 0
        per_case: list[dict[str, Any]] = []
        for row in self.rows:
            seconds = float(row.get("elapsed_seconds") or 0.0)
            elapsed += seconds
            result = row.get("result") or {}
            execution = result.get("execution") or {}
            if isinstance(execution, Mapping):
                attempts += int(execution.get("provider_attempts") or 0)
                missing_usage += int(execution.get("usage_missing_attempts") or 0)
                usage = _fold(usage, execution.get("usage"))
            per_case.append({"case": row.get("case"), "elapsed_seconds": round(seconds, 1)})
        per_case.sort(key=lambda item: -item["elapsed_seconds"])
        return {
            "case_seconds": round(elapsed, 1),
            "case_hours": round(elapsed / 3600, 3),
            "provider_attempts": attempts,
            "usage": usage or None,
            "usage_missing_attempts": missing_usage,
            "slowest_cases": per_case[:5],
            "wall_seconds": self.wall_seconds,
            "mean_case_seconds": (
                round(elapsed / len(self.rows), 1) if self.rows else None
            ),
        }

    def to_dict(self, evaluation: Mapping[str, Any] | None = None) -> dict[str, Any]:
        buckets = self.buckets()
        return {
            "schema_version": REPORT_SCHEMA,
            "stage": self.stage,
            "arm": self.arm,
            "generated_at": _utc(),
            "coverage": {
                "requested": len(self.requested),
                "measured": len(self.admitted_cases),
                "not_measured": len(self.requested) - len(self.admitted_cases),
                "by_status": {key: len(value) for key, value in buckets.items()},
                "not_measured_cases": sorted(
                    case for case, status in self.classification().items()
                    if status != MEASURED
                ),
                "note": (
                    "measured counts authoritative terminal decisions only; "
                    "every other requested case is not_measured and is not "
                    "part of any accuracy"
                ),
            },
            "readings": self.readings(evaluation),
            "denominators": self.denominator_audit(),
            "cost": self.cost(),
            "stop": dict(self.stop),
            "notes": list(self.notes),
        }


def _fold(total: dict[str, Any], usage: Any) -> dict[str, Any]:
    if not isinstance(usage, Mapping):
        return total
    for key, value in usage.items():
        if isinstance(value, Mapping):
            bucket = total.setdefault(key, {})
            if isinstance(bucket, dict):
                _fold(bucket, value)
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
    return total


def summarize_stage(
    *,
    stage: str,
    arm: str,
    requested: Sequence[str],
    batch_summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    evaluation: Mapping[str, Any] | None = None,
    wall_seconds: float | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the report from a batch summary and its per-case rows.

    ``requested`` is the frozen list, so a case that never produced a row is
    still accounted for. ``batch_summary["measurement"]["not_started_cases"]``
    and the row set describe the same cases from two sides; the report states
    both rather than trusting either to be complete.
    """
    report = StageReport(
        stage=stage, arm=arm, requested=[str(case) for case in requested],
        rows=[dict(row) for row in rows],
        stop=dict((batch_summary or {}).get("stop") or {}),
        wall_seconds=wall_seconds,
        notes=list(notes),
    )
    payload = report.to_dict(evaluation)
    batch_measurement = dict((batch_summary or {}).get("measurement") or {})
    payload["batch"] = {
        "attempt_id": batch_measurement.get("attempt_id"),
        "attempts": batch_measurement.get("attempts"),
        "not_started_cases": list(batch_measurement.get("not_started_cases") or ()),
        "peak_provider_inflight": batch_measurement.get("peak_provider_inflight"),
        "deadline_seconds": batch_measurement.get("deadline_seconds"),
    }
    if batch_measurement.get("not_started_cases"):
        payload["notes"].append(
            f"{len(batch_measurement['not_started_cases'])} case(s) never "
            "started; the sample is a prefix of the requested list, not the "
            "whole list"
        )
    return payload
