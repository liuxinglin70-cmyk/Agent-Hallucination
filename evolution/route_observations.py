"""Measured route provenance projected before gate rows discard raw results."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from detector.identity import digest
from detector.measurement import measurement_sources
from detector.result import prediction_state
from detector.replay import HOOK_POLICIES
from evolution.contracts import make_registry

ROUTE_MEASUREMENT_SCHEMA = "evolution.route_measurement.v1"



def _has_digest(value: Any, schema: str) -> bool:
    if not isinstance(value, Mapping) or value.get("schema_version") != schema:
        return False
    try:
        return value.get("digest") == digest({key: item for key, item in value.items() if key != "digest"})
    except (TypeError, ValueError):
        return False


def _hex_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _input_identity(value: Any) -> bool:
    if not _has_digest(value, "detector.execution_identity.v2"):
        return False
    hashes = {"source", "config", "record", "packet", "evidence", "registry", "replay", "authority"}
    return (
        set(value) == hashes | {"schema_version", "digest", "artifacts"}
        and all(_hex_digest(value[key]) for key in hashes)
        and isinstance(value["artifacts"], Mapping)
        and all(_hex_digest(item) for item in value["artifacts"].values())
    )


def build_route_measurement(
    *, candidate: Mapping[str, Any], partners: Sequence[Mapping[str, Any]],
    cases: Sequence[str], control_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]], runtime: Mapping[str, Any],
    control_registry: Mapping[str, Any], candidate_registry: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not cases or len(cases) > 10000 or len(set(cases)) != len(cases):
        return None
    if not _has_digest(runtime, "evolution.backend_runtime_identity.v1"):
        return None
    try:
        expected_registry = make_registry([*control_registry.get("interventions", ()), *partners, candidate])
    except (TypeError, ValueError):
        return None
    if expected_registry != candidate_registry:
        return None
    arms = {}
    registries = {"control": digest(control_registry), "candidate": digest(candidate_registry)}
    for name, rows in (("control", control_rows), ("candidate", candidate_rows)):
        if len(rows) != len(cases) or {row.get("case") for row in rows} != set(cases):
            return None
        projected = []
        for row in sorted(rows, key=lambda item: item["case"]):
            result = row.get("result")
            if not isinstance(result, Mapping) or result.get("case_id") != row["case"]:
                return None
            if result.get("decision_authority") != "authoritative" or type(row.get("prediction")) is not bool:
                return None
            if prediction_state(result) != row["prediction"]:
                return None
            provenance = result.get("provenance") or {}
            inputs = provenance.get("execution_identity")
            if not _input_identity(inputs):
                return None
            if inputs.get("source") != runtime.get("detector_source"):
                return None
            if inputs.get("config") != runtime.get("effective_config"):
                return None
            if inputs.get("registry") != registries[name]:
                return None
            execution = result.get("execution")
            if not isinstance(execution, Mapping) or not (sources := measurement_sources(execution)):
                return None
            frozen = provenance.get("frozen_stages") or []
            if not isinstance(frozen, list) or not all(isinstance(item, str) for item in frozen):
                return None
            if name == "candidate" and any(
                (policy := HOOK_POLICIES.get(str(entry.get("hook") or ""))) is not None
                and policy.frozen_key in frozen
                for entry in [candidate, *partners]
            ):
                return None
            projected.append({
                "case": row["case"], "inputs": deepcopy(inputs),
                "measurement_sources": list(sources), "frozen_stages": sorted(frozen),
                "result_digest": digest(result),
            })
        arms[name] = projected
    payload = {
        "schema_version": ROUTE_MEASUREMENT_SCHEMA,
        "candidate_digest": digest(candidate),
        "partner_digests": sorted(digest(entry) for entry in partners),
        "runtime": deepcopy(runtime), "arms": arms, "registries": registries,
    }
    measurement = {**payload, "digest": digest(payload)}
    return measurement if route_measurement_sources(measurement) else None


def route_measurement_sources(value: Any) -> tuple[str, ...]:
    """Validate stored provenance and return candidate invocation leaves."""
    if not _has_digest(value, ROUTE_MEASUREMENT_SCHEMA):
        return ()
    if set(value) != {"schema_version", "candidate_digest", "partner_digests", "runtime", "arms", "registries", "digest"}:
        return ()
    runtime = value["runtime"]
    if not _has_digest(runtime, "evolution.backend_runtime_identity.v1"):
        return ()
    arms = value["arms"]
    if not isinstance(arms, Mapping) or set(arms) != {"control", "candidate"}:
        return ()
    registries = value["registries"]
    if not isinstance(registries, Mapping) or set(registries) != set(arms):
        return ()
    if not isinstance(value["partner_digests"], list):
        return ()
    hashes = [value["candidate_digest"], *value["partner_digests"], *registries.values()]
    if not all(_hex_digest(item) for item in hashes):
        return ()
    sources_by_arm = {}
    for name, rows in arms.items():
        if not isinstance(rows, list) or not rows or len(rows) > 10000:
            return ()
        cases = []
        sources = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"case", "inputs", "measurement_sources", "frozen_stages", "result_digest"}:
                return ()
            if not isinstance(row["case"], str) or not row["case"]:
                return ()
            cases.append(row["case"])
            inputs = row["inputs"]
            if not _input_identity(inputs) or inputs.get("source") != runtime.get("detector_source"):
                return ()
            if inputs.get("config") != runtime.get("effective_config"):
                return ()
            if inputs.get("registry") != registries[name]:
                return ()
            if not _hex_digest(row["result_digest"]) or not isinstance(row["frozen_stages"], list) or not all(
                isinstance(item, str) for item in row["frozen_stages"]
            ):
                return ()
            leaves = row["measurement_sources"]
            if not isinstance(leaves, list) or not leaves or len(leaves) > 100:
                return ()
            for leaf in leaves:
                if not measurement_sources({"schema_version": "detector.execution.v1", "measurement_id": leaf}):
                    return ()
            sources.extend(leaves)
        if len(set(cases)) != len(cases) or len(set(sources)) != len(sources):
            return ()
        sources_by_arm[name] = (cases, sources)
    control, candidate = sources_by_arm["control"], sources_by_arm["candidate"]
    if control[0] != candidate[0] or set(control[1]) & set(candidate[1]):
        return ()
    for before, after in zip(arms["control"], arms["candidate"]):
        if any(before["inputs"].get(key) != after["inputs"].get(key)
               for key in ("source", "config", "record", "packet", "evidence", "artifacts")):
            return ()
    return tuple(candidate[1])


def measurement_for_candidate(value: Any, candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    if not route_measurement_sources(value):
        return None
    if digest(candidate) not in [value["candidate_digest"], *value["partner_digests"]]:
        return None
    return deepcopy(value)
