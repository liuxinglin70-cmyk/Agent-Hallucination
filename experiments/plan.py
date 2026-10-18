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
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from detector.identity import digest
from detector.runner import safe_case_id

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_SCHEMA = "experiments.plan.v1"
MEASUREMENT_SCHEMA = "detector.measurement_manifest.v1"


def case_list_digest(cases: list[str]) -> str:
    return hashlib.sha256("\n".join(cases).encode("utf-8")).hexdigest()


def measured_prefix(order: Sequence[str], measured: Sequence[str]) -> list[str]:
    found = set(measured)
    for index, case in enumerate(order):
        if case not in found:
            return list(order[:index])
    return list(order)


def _case_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(case, str) for case in value):
        raise ValueError(f"{name} must be a list of case identifiers")
    paths = [safe_case_id(case).casefold() for case in value]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{name} contains duplicate or colliding case identifiers")
    return list(value)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@dataclass(frozen=True)
class MeasurementSet:
    """The frozen sample: an ordered list of cases and the rule that produced it."""

    cases: list[str]
    digest: str
    policy: str
    excluded_mini_split: list[str]
    excluded_unlabeled: list[str]
    gold_positive: int | None
    gold_negative: int | None
    source: Path
    parent_manifest_digest: str = ""
    manifest_digest: str = ""

    @property
    def excluded(self) -> list[str]:
        return [*self.excluded_mini_split, *self.excluded_unlabeled]

    @property
    def size(self) -> int:
        return len(self.cases)

    def prefix(self, count: int) -> "MeasurementSet":
        if type(count) is not int or count < 0:
            raise ValueError("prefix count must be a nonnegative integer")
        return self.select(self.cases[:count])

    def select(self, cases: Sequence[str]) -> "MeasurementSet":
        selected = _case_list(list(cases), "selected cases")
        positions = {case: index for index, case in enumerate(self.cases)}
        if any(case not in positions for case in selected):
            raise ValueError("selected cases are outside the measurement manifest")
        if [positions[case] for case in selected] != sorted(positions[case] for case in selected):
            raise ValueError("selected cases must preserve the frozen measurement order")
        complete = len(selected) == len(self.cases)
        return MeasurementSet(
            cases=list(selected), digest=case_list_digest(selected),
            policy=self.policy, excluded_mini_split=list(self.excluded_mini_split),
            excluded_unlabeled=list(self.excluded_unlabeled),
            gold_positive=self.gold_positive if complete else None if selected else 0,
            gold_negative=self.gold_negative if complete else None if selected else 0,
            source=self.source, parent_manifest_digest=self.parent_manifest_digest,
            manifest_digest=self.manifest_digest,
        )

    def to_manifest(self, *, gold: Mapping[str, bool] | None = None) -> dict[str, Any]:
        positive, negative = self.gold_positive, self.gold_negative
        if gold is not None:
            if set(gold) != set(self.cases) or any(type(value) is not bool for value in gold.values()):
                raise ValueError("exported gold must cover exactly the selected cases with boolean labels")
            positive, negative = sum(gold.values()), len(gold) - sum(gold.values())
        payload = {"schema_version": MEASUREMENT_SCHEMA, "policy": self.policy,
                   "cases": list(self.cases), "cases_digest": self.digest,
                   "gold_positive": positive, "gold_negative": negative,
                   "excluded_mini_split": list(self.excluded_mini_split),
                   "excluded_unlabeled": list(self.excluded_unlabeled),
                   "parent_manifest_digest": self.parent_manifest_digest}
        _measurement(payload, self.source)
        return payload

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

    def arm(self, name: str) -> dict[str, Any]:
        spec = self.arms.get(name)
        if not isinstance(spec, Mapping):
            raise ValueError(f"arm {name!r} is not declared by the experiment plan")
        if not isinstance(spec.get("registry"), str) or not spec["registry"].strip():
            raise ValueError(f"arm {name!r} requires a registry selector")
        if not isinstance(spec.get("role"), str) or not spec["role"].strip():
            raise ValueError(f"arm {name!r} requires a role")
        return deepcopy(dict(spec))

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
                "parent_manifest_digest": self.measurement.parent_manifest_digest,
                "manifest_digest": self.measurement.manifest_digest,
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
        "rule": "decisive_fixed > decisive_broken on stable cases and one-sided p < significance_level",
        "significance_level": 0.05,
        # False positives are read as a paired movement, not as two counts. On a
        # batch with 38 clean cases the standard error of the raw difference is
        # over two cases, so "fp must not exceed baseline" rejects at about the
        # rate of a coin flip and hides a 3-for-1 regression behind a tie. The
        # gate fails only when clean-case additions significantly exceed
        # removals; both counts stay in the reading.
        "false_positive_rule": (
            "clean cases that turn positive (fp_added) must not significantly "
            "exceed clean cases that stop being positive (fp_removed), one-sided "
            "p < significance_level; a tie or a net removal always passes"
        ),
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
    path = Path(path) if path is not None else Path(DEFAULT_MEASUREMENT)
    if not path.is_file():
        raise FileNotFoundError(f"measurement manifest not found: {path}; pass --measurement <manifest.json>")
    payload = _read_json(path)
    return _measurement(payload, path)


def _measurement(payload: Any, path: Path) -> MeasurementSet:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != MEASUREMENT_SCHEMA:
        raise ValueError("measurement manifest schema is unsupported")
    cases = _case_list(payload.get("cases"), "measurement cases")
    if not cases:
        raise ValueError(f"{path} declares no cases")
    if payload.get("cases_digest") != case_list_digest(cases):
        raise ValueError("measurement case digest differs from its ordered case list")
    excluded = _case_list(payload.get("excluded_mini_split", []), "excluded mini split")
    unlabeled = _case_list(payload.get("excluded_unlabeled", []), "excluded unlabeled")
    all_paths = [safe_case_id(case).casefold() for case in [*cases, *excluded, *unlabeled]]
    if len(all_paths) != len(set(all_paths)):
        raise ValueError("measurement and exclusion sets overlap or collide")
    for key in ("gold_positive", "gold_negative"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            raise ValueError(f"measurement {key} must be a nonnegative integer")
    if payload["gold_positive"] + payload["gold_negative"] != len(cases):
        raise ValueError("measurement gold counts differ from case count")
    counts = payload.get("counts")
    if counts is not None:
        expected = {"corpus": len(all_paths), "measurable": len(cases),
                    "excluded_mini_split": len(excluded), "excluded_unlabeled": len(unlabeled)}
        if not isinstance(counts, Mapping) or any(type(counts.get(key)) is not int or counts[key] != value
                                                  for key, value in expected.items()):
            raise ValueError("measurement manifest counts differ from its declared sets")
    manifest_digest = digest(payload)
    parent_digest = payload.get("parent_manifest_digest", manifest_digest)
    if (not isinstance(parent_digest, str) or len(parent_digest) != 64
            or any(character not in "0123456789abcdef" for character in parent_digest)):
        raise ValueError("measurement parent_manifest_digest must be a SHA-256 digest")
    return MeasurementSet(
        cases=cases,
        digest=case_list_digest(cases),
        policy=str(payload.get("policy") or ""),
        excluded_mini_split=excluded, excluded_unlabeled=unlabeled,
        gold_positive=payload["gold_positive"], gold_negative=payload["gold_negative"],
        source=path, parent_manifest_digest=parent_digest, manifest_digest=manifest_digest,
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
    plan_file = Path(plan_path) if plan_path is not None else DEFAULT_PLAN
    if plan_path is not None and not plan_file.is_file():
        raise FileNotFoundError(f"explicit experiment plan not found: {plan_file}")
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
    if "effect" in criteria:
        level = criteria["effect"].get("significance_level")
        if (type(level) not in (int, float) or not math.isfinite(level)
                or not 0 < level < 1):
            raise ValueError("effect significance_level must be finite and between zero and one")
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
