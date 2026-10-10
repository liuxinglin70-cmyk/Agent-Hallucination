"""Route observations derived from measured objective outcomes.

A route is insufficient only after its hook fires and changes the promised
behavior, while a measurable outcome records neither benefit nor harm.
Only independent observations in the applicable runtime admit falsification.
"""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from typing import Any, Mapping, Sequence

from evolution.contracts import append_jsonl, read_jsonl
from evolution.route_observations import route_measurement_sources

ROUTE_FAILURE_SCHEMA = "evolution.route_failure.v1"
ROUTE_FAILURE_NAME = "route_failures.jsonl"

# Two independent observations on the same (mechanism, route) are admitted as a
# falsification when no revision evidence is available. See the module docstring.
REPEAT_OBSERVATION_THRESHOLD = 2

# Treatment routes, in the vocabulary ``worksheet.TREATMENT_ROUTES`` already
# uses. The trailing entries are reachable hooks the worksheet does not route
# to yet; they are named here so a record never has to invent a label.
TREATMENT_ROUTES = (
    "recall_directive",
    "upgrade_directive",
    "restore_guard",
    "withhold_guard",
    "mechanical_conviction",
    "evolved_predicate",
    "tracer_directive",
    "recall_trigger",
    "judge_directive",
    "defense_directive",
    "defense_requirement",
)

_HOOK_ROUTES = {
    "scanner.system": "recall_directive",
    "recall.trigger": "recall_trigger",
    "judge.system": "judge_directive",
    "tracer.system": "tracer_directive",
    "upgrade.system": "upgrade_directive",
    "defense.system": "defense_directive",
    "defense.pre": "defense_directive",
    "upgrade.pre": "upgrade_directive",
}

_GUARD_ROUTES = {
    "withhold_conviction": "withhold_guard",
    "restore_judge_conviction": "restore_guard",
    "convict_mechanical_violation": "mechanical_conviction",
    "require_full_view_defense": "defense_requirement",
}

# Which guard action a route implies. A conviction route is served by either a
# built-in predicate or an inline typed-DSL program, and those are different
# capabilities: only the second one creates a new observation.
_PROGRAM_RUNNER = "typed_dsl"


def route_of(candidate: Mapping[str, Any]) -> str | None:
    """The treatment route one intervention implements, or None.

    A candidate's shape decides the route; the family's route list only says
    which routes were *available*. Recording the available list instead would
    attribute a failure to routes the candidate never used.
    """
    entry_type = str(candidate.get("type") or "")
    hook = str(candidate.get("hook") or "")
    body = candidate.get("body") or {}
    if entry_type == "decision_guard":
        action = str(body.get("action") or "")
        return _GUARD_ROUTES.get(action)
    if entry_type == "text_boundary":
        return _HOOK_ROUTES.get(hook)
    if entry_type == "recall_trigger":
        return "recall_trigger" if hook == "recall.trigger" else None
    if entry_type == "evidence_check":
        if hook != "judge.post":
            return None
        # An evidence check alone only reports; it is the guard partner that
        # acts on it, so its route is named by the program it carries.
        return (
            "evolved_predicate"
            if str(body.get("runner") or "") == _PROGRAM_RUNNER
            else "mechanical_conviction"
        )
    return None


def unit_route(members: Sequence[Mapping[str, Any]]) -> str | None:
    """The route of one admission unit.

    A conviction is served by a pair: an ``evidence_check`` observes and a
    ``decision_guard`` acts on the observation. The pair is one route, and
    which one depends on where the observation came from -- a built-in
    predicate rides the closed mechanical route, an inline typed-DSL program is
    capability creation. That difference is the whole reason the two routes are
    listed separately, so it must survive the pairing.

    Members that disagree about which *acting* route was used yield None:
    attributing the outcome to one of two routes would be a guess.
    """
    routes = [
        route for route in (route_of(member) for member in members)
        if route is not None
    ]
    acting = [route for route in routes if route != "evolved_predicate"]
    if "evolved_predicate" in routes and (
        not acting or set(acting) == {"mechanical_conviction"}
    ):
        # The same guard, a newly created observation: capability creation.
        return "evolved_predicate"
    if len(set(acting)) != 1:
        return None
    return acting[0]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def insufficiency_evidence(
    *,
    candidate: Mapping[str, Any],
    behavior: Mapping[str, Any] | None,
    outcome: Mapping[str, Any] | None,
    opportunity: int,
    substantive_rejection: bool,
) -> dict[str, Any] | None:
    """One unit's route-insufficiency reading, or None.

    Returns a record body when every condition holds: the hook fired, the
    intervention moved the behaviour it promised, the outcome was measured on a
    set where it could have been rewarded, and it did not move. Anything else --
    an outage, an unmeasured claim, a harmful candidate, a candidate that never
    fired -- is not evidence about the route's repair capability and returns
    None.
    """
    route = route_of(candidate)
    if route is None or not substantive_rejection or opportunity <= 0:
        return None
    if not isinstance(behavior, Mapping) or behavior.get("passed"):
        return None
    fired_rate = float(behavior.get("fired_rate") or 0.0)
    events = _int(behavior.get("behavior_events"))
    if fired_rate <= 0.0 or events <= 0:
        return None
    if not isinstance(outcome, Mapping):
        return None
    polarity = str((candidate.get("claim") or {}).get("target_polarity") or "")
    if polarity not in {"recall", "precision", "boundary"}:
        return None
    if (
        outcome.get("measurable") is not True or outcome.get("status") != "unchanged"
        or outcome.get("target_polarity") != polarity
        or outcome.get("target_kind") != str((candidate.get("claim") or {}).get("target_kind") or "detection")
    ):
        return None
    return {
        "route": route,
        "status": "insufficient_for_mechanism",
        "evidence": {
            "hook_fired": round(fired_rate, 6),
            "output_changed": events,
            "behavior_event": str(behavior.get("behavior_event_kind") or ""),
            "target_outcome_changed": 0,
            "target_polarity": polarity,
            "target_kind": outcome["target_kind"],
            "gain_opportunities": int(opportunity),
            "delta_basis": outcome["delta_basis"],
        },
    }


def _measurement_stamp(value: Any) -> str | None:
    stamp = value.get("digest") if isinstance(value, Mapping) else None
    return stamp if isinstance(stamp, str) else None


def route_failure_record(
    *,
    cycle: int,
    mechanism_id: str,
    family_id: str,
    observation: Mapping[str, Any],
    revisions_observed: int | None = None,
) -> dict[str, Any]:
    """Build one stored record from an observation."""
    return {
        "schema_version": ROUTE_FAILURE_SCHEMA,
        "kind": "observation",
        "mechanism_id": str(mechanism_id or ""),
        "family_id": str(family_id or ""),
        "route": str(observation.get("route") or ""),
        "status": str(observation.get("status") or ""),
        "evidence": deepcopy(observation.get("evidence") or {}),
        "measurement": deepcopy(observation.get("measurement")),
        "revisions_observed": (
            None if revisions_observed is None else int(revisions_observed)
        ),
        "cycle": int(cycle),
    }


def load_route_failures(path: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(path)
    for number, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"route-failure record {number} is not an object")
        schema = record.get("schema_version")
        if schema != ROUTE_FAILURE_SCHEMA:
            raise ValueError(
                f"route-failure record {number} has unsupported schema {schema!r}"
            )
    return [dict(record) for record in records]


def record_route_failures(
    path: str | Path,
    *,
    cycle: int,
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Append distinct measurements; replaying an identical event is idempotent."""
    seen = {(
        int(record.get("cycle") or -1), str(record.get("mechanism_id") or ""),
        str(record.get("route") or ""), _measurement_stamp(record.get("measurement")),
    ): record for record in load_route_failures(path) if record.get("kind") != "resolution"}
    appended: list[dict[str, Any]] = []
    for observation in observations:
        mechanism_id = str(observation.get("mechanism_id") or "")
        route = str((observation.get("observation") or observation).get(
            "route") or "")
        if not mechanism_id or not route:
            continue
        record = route_failure_record(
            cycle=cycle, mechanism_id=mechanism_id,
            family_id=str(observation.get("family_id") or ""),
            observation=observation.get("observation") or observation,
            revisions_observed=observation.get("revisions_observed"),
        )
        key = (int(cycle), mechanism_id, route, _measurement_stamp(record.get("measurement")))
        if key in seen:
            if seen[key] != record:
                raise ValueError("conflicting route observation for the same measurement")
            continue
        append_jsonl(path, record)
        seen[key] = record
        appended.append(record)
    return appended


def route_failure_state(
    records: Sequence[Mapping[str, Any]],
    *, runtime_identity: Mapping[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Reduce append order into the current observation epoch for each route."""
    state: dict[tuple[str, str], dict[str, Any]] = {}
    seen: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    used_sources: dict[tuple[str, str], set[str]] = {}
    for record in records:
        key = (str(record.get("mechanism_id") or ""), str(record.get("route") or ""))
        if not all(key):
            continue
        kind = str(record.get("kind") or "observation")
        if kind != "resolution" and record.get("status") != "insufficient_for_mechanism":
            continue
        cycle = int(record.get("cycle") or 0)
        measurement = record.get("measurement")
        stamp = _measurement_stamp(measurement)
        event_key = (*key, kind, cycle, stamp)
        if event_key in seen:
            if dict(seen[event_key]) != dict(record):
                raise ValueError(f"conflicting route event: {event_key}")
            continue
        seen[event_key] = record
        if kind == "resolution":
            state.pop(key, None)
            continue
        entry = state.setdefault(key, {
            "mechanism_id": key[0], "route": key[1], "observations": 0,
            "diagnostic_observations": 0, "insufficient": False, "basis": "",
            "cycles": [], "evidence": {},
        })
        sources = route_measurement_sources(measurement)
        used = used_sources.setdefault(key, set())
        if not sources or runtime_identity is None or measurement["runtime"] != runtime_identity or used.intersection(sources):
            entry["diagnostic_observations"] += 1
            continue
        used.update(sources)
        entry["observations"] += 1
        entry["cycles"] = [*entry["cycles"], cycle]
        entry["evidence"] = deepcopy(record.get("evidence") or {})
        if entry["observations"] >= REPEAT_OBSERVATION_THRESHOLD:
            entry["insufficient"] = True
            entry["basis"] = "repeat_observation"
    return state


def falsified_routes(
    records: Sequence[Mapping[str, Any]],
    mechanism_id: str,
    *, runtime_identity: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Routes admitted as insufficient for one mechanism."""
    state = route_failure_state(records, runtime_identity=runtime_identity)
    return {
        route: entry for (mid, route), entry in state.items()
        if mid == str(mechanism_id or "") and entry.get("insufficient")
    }


def route_failure_summary(records: Sequence[Mapping[str, Any]], *, runtime_identity: Mapping[str, Any] | None) -> dict[str, Any]:
    """A report-shaped view: what has been tried, what is admitted."""
    state = route_failure_state(records, runtime_identity=runtime_identity)
    admitted = {key: entry for key, entry in state.items() if entry["insufficient"]}
    return {
        "records": len([
            record for record in records if record.get("kind") != "resolution"
        ]),
        "pairs": len(state),
        "insufficient": [
            {
                "mechanism_id": entry["mechanism_id"],
                "route": entry["route"],
                "observations": entry["observations"],
                "diagnostic_observations": entry["diagnostic_observations"],
                "basis": entry["basis"],
                "evidence": entry["evidence"],
            }
            for _key, entry in sorted(admitted.items())
        ],
        "observed_only": [
            {
                "mechanism_id": entry["mechanism_id"],
                "route": entry["route"],
                "observations": entry["observations"],
                "diagnostic_observations": entry["diagnostic_observations"],
            }
            for _key, entry in sorted(state.items())
            if not entry["insufficient"]
        ],
    }


def resolve_route_failure(
    path: str | Path,
    *,
    mechanism_id: str,
    route: str,
    cycle: int,
    note: str,
) -> dict[str, Any]:
    """Clear a falsification: the route now repairs this mechanism.

    Recorded as history rather than by deleting the observations, so a route
    that was once falsified stays explainable after it is repaired.
    """
    record = {
        "schema_version": ROUTE_FAILURE_SCHEMA,
        "kind": "resolution",
        "mechanism_id": str(mechanism_id or ""),
        "route": str(route or ""),
        "cycle": int(cycle),
        "note": str(note or ""),
    }
    for existing in load_route_failures(path):
        if all(existing.get(key) == record[key] for key in ("kind", "mechanism_id", "route", "cycle")):
            if existing != record:
                raise ValueError("conflicting resolution for the same cycle and route")
            return existing
    append_jsonl(path, record)
    return record


__all__ = [
    "REPEAT_OBSERVATION_THRESHOLD",
    "ROUTE_FAILURE_NAME",
    "ROUTE_FAILURE_SCHEMA",
    "TREATMENT_ROUTES",
    "falsified_routes",
    "insufficiency_evidence",
    "load_route_failures",
    "record_route_failures",
    "resolve_route_failure",
    "route_failure_record",
    "route_failure_state",
    "route_failure_summary",
    "route_of",
    "unit_route",
]
