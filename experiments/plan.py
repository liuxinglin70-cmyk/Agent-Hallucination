"""The pre-registered plan: which cases, in what order, judged how.

Reading this module should answer, without running anything: what sample does
this experiment measure, what arms does it compare, and what would count as an
answer for each question. Everything here is fixed before the first provider
call, because a plan chosen after seeing results is not a plan.

The one idea worth stating plainly is the *prefix* rule. A full-corpus pass
costs far more than a research budget (the measured mean was 21.7 minutes per
case, so 653 cases is a week of wall clock per arm), so the run will be stopped
somewhere inside the pre-registered order. Rotating that order across sources
is what makes every prefix approximately stratified: the sample size becomes a
budget decision instead of a design decision, and no prefix is more biased than
another. Choosing cases *after* seeing partial results would not have that
property, which is why the order is frozen and the size is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_SCHEMA = "experiments.plan.v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@dataclass(frozen=True)
class MeasurementSet:
    """The frozen sample: an ordered list of cases and the rule that produced it."""

    cases: list[str]
    digest: str
    policy: str
    excluded: list[str]
    gold_positive: int
    gold_negative: int
    source: Path

    @property
    def size(self) -> int:
        return len(self.cases)

    def prefix(self, count: int) -> "MeasurementSet":
        return MeasurementSet(
            cases=self.cases[: max(0, int(count))], digest=self.digest,
            policy=self.policy, excluded=self.excluded,
            gold_positive=self.gold_positive, gold_negative=self.gold_negative,
            source=self.source,
        )

    def stratification(self, count: int | None = None) -> dict[str, Any]:
        """Source and polarity coverage of a prefix, for the report.

        Reported rather than asserted: this is how a reader checks that a
        stopped run measured a spread of sources instead of one block.
        """
        cases = self.cases[: count if count is not None else len(self.cases)]
        by_source: dict[str, int] = {}
        for case in cases:
            source = str(case).split("/")[0]
            by_source[source] = by_source.get(source, 0) + 1
        return {
            "cases": len(cases),
            "sources": len(by_source),
            "by_source": dict(sorted(by_source.items())),
        }


@dataclass(frozen=True)
class ExperimentPlan:
    """A measurement set plus the stages and criteria that read it."""

    schema_version: str
    measurement: MeasurementSet
    mini_split: list[str]
    stages: list[str]
    criteria: dict[str, Any]
    arms: dict[str, Any]
    source: Path | None = None
    notes: list[str] = field(default_factory=list)

    def stage(self, name: str) -> dict[str, Any]:
        if name not in self.stages:
            raise KeyError(
                f"stage {name!r} is not part of this plan; stages are "
                f"{self.stages}"
            )
        return dict(self.criteria.get(name) or {})

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "measurement": {
                "cases": self.measurement.size,
                "digest": self.measurement.digest,
                "policy": self.measurement.policy,
                "gold_positive": self.measurement.gold_positive,
                "gold_negative": self.measurement.gold_negative,
                "excluded": len(self.measurement.excluded),
                "stratification": self.measurement.stratification(),
            },
            "stages": list(self.stages),
            "arms": dict(self.arms),
            "criteria": dict(self.criteria),
        }


DEFAULT_MEASUREMENT = REPO_ROOT / "datasets/protocols/measurement_v1.json"
DEFAULT_PLAN = REPO_ROOT / "datasets/protocols/experiment_plan_v1.json"

#: The criteria shipped when no plan file exists. Kept beside the loader rather
#: than in a document so a run and its stated criteria cannot drift apart.
DEFAULT_CRITERIA: dict[str, Any] = {
    "baseline": {
        "reads": ["tp", "fp", "fn", "tn", "decisive_accuracy",
                  "step_localization"],
        "note": (
            "a baseline is a reading, not a pass/fail; the only requirement is "
            "that every case is accounted for as measured or not measured"
        ),
    },
    "effect": {
        "rule": "decisive_fixed > decisive_broken on stable cases",
        "minimum_detectable": {
            "fixed": 5, "broken": 0, "one_sided_p": 0.0312,
            "note": (
                "four decisive corrections with no regression gives p=0.0625, "
                "which does not clear the framework's own 0.05 terminal bar"
            ),
        },
        "unstable_cases_excluded": True,
    },
    "no_admission": {
        "rule": (
            "0 admissions must state which of the five questions it answers, "
            "or 'not measured' -- never 'the mechanism is unrepairable'"
        ),
        "questions": [
            "mechanism_unrepairable",
            "action_class_insufficient",
            "candidate_design_failed",
            "no_gain_opportunity",
            "harmlessness_failed",
        ],
    },
}

DEFAULT_ARMS: dict[str, Any] = {
    "g0": {"registry": "empty", "role": "baseline"},
    "g0_roll2": {"registry": "empty", "role": "noise_floor"},
    "g1": {"registry": "active", "role": "deployed"},
}


def load_measurement(path: str | Path | None = DEFAULT_MEASUREMENT) -> MeasurementSet:
    path = Path(path) if path else Path(DEFAULT_MEASUREMENT)
    payload = _read_json(path)
    cases = [str(case) for case in payload.get("cases") or ()]
    if not cases:
        raise ValueError(f"{path} declares no cases")
    return MeasurementSet(
        cases=cases,
        digest=str(payload.get("cases_digest") or payload.get("digest") or ""),
        policy=str(payload.get("policy") or ""),
        excluded=[str(case) for case in payload.get("excluded_mini_split") or ()],
        gold_positive=int(payload.get("gold_positive") or 0),
        gold_negative=int(payload.get("gold_negative") or 0),
        source=path,
    )


def load_plan(
    *,
    plan_path: str | Path | None = None,
    measurement_path: str | Path | None = DEFAULT_MEASUREMENT,
) -> ExperimentPlan:
    """Load the plan, filling stage criteria from the shipped defaults.

    A plan file may override the criteria; it may not silently drop one, so a
    stage named in ``stages`` without a criterion is an error rather than a
    stage that quietly measures nothing in particular.
    """
    measurement = load_measurement(measurement_path)
    raw: dict[str, Any] = {}
    plan_file = Path(plan_path) if plan_path else DEFAULT_PLAN
    if plan_file.is_file():
        raw = _read_json(plan_file)
        if not isinstance(raw, Mapping):
            raise ValueError(f"{plan_file} must contain an object")
    stages = [str(name) for name in raw.get("stages") or DEFAULT_CRITERIA]
    criteria = {
        name: {**DEFAULT_CRITERIA.get(name, {}), **(raw.get("criteria") or {}).get(name, {})}
        for name in stages
    }
    unknown = [
        name for name in stages
        if not criteria[name] and name not in DEFAULT_CRITERIA
    ]
    if unknown:
        raise ValueError(
            f"plan declares stage(s) with no criterion: {unknown}; add them to "
            "the plan file or use one of "
            f"{sorted(DEFAULT_CRITERIA)}"
        )
    return ExperimentPlan(
        schema_version=str(raw.get("schema_version") or PLAN_SCHEMA),
        measurement=measurement,
        mini_split=[str(case) for case in raw.get("mini_split") or measurement.excluded],
        stages=stages,
        criteria=criteria,
        arms={**DEFAULT_ARMS, **(raw.get("arms") or {})},
        source=plan_file if plan_file.is_file() else None,
        notes=[str(note) for note in raw.get("notes") or ()],
    )
