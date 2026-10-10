"""Frozen mechanism, candidate, data roles and evidence for promotion."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from detector.identity import digest
from evolution.mechanisms import MECHANISM_SCHEMA, mechanism_key
from evolution.metrics import mechanism_opportunity, mechanism_row_healthy

ALIGNED_PROTOCOL_SCHEMA = "evolution.promotion_protocol.v4"
ALIGNED_CANDIDATE_SCHEMA = "evolution.candidate.v2"
MEMBERSHIP_SCHEMA = "evolution.mechanism_membership.v1"
FORMAL_STAGES = ("structural", "harmlessness", "behavior", "m")

# Every terminal reason a promotion path may report, and the category it
# belongs to. The codes come from three producers: the aligned run's own
# decisions, the mechanism opportunity metric's vocabulary, and the legacy
# polarity path's terminal states. One table, so a new failure mode has to be
# given a category rather than silently landing in ``unclassified``.
REASON_CATEGORIES = {
    "promoted": "promoted",
    "no_mechanism_opportunity": "formal_gain_no_opportunity",
    "mechanism_unconfirmed": "mechanism_unconfirmed",
    "mechanism_invalid": "mechanism_unconfirmed",
    "mechanism_alignment_missing": "mechanism_alignment_missing",
    "mechanism_membership_invalid": "mechanism_alignment_missing",
    "protection_coverage_missing": "measurement_coverage_missing",
    "localization_protection_missing": "measurement_coverage_missing",
    "runtime_unresolved": "runtime_unresolved",
    "mechanism_measurement_unresolved": "runtime_unresolved",
    "protection_regression": "harmlessness_failed",
    "challenge_regression": "mechanism_external_regression",
    "localization_regression": "localization_protection_failed",
    "candidate_no_effect": "candidate_design_failed",
    "protocol_invalid": "protocol_invalid",
    "candidate_invalid": "candidate_invalid",
    "library_limit": "deployment_limit",
    "inconclusive": "inconclusive",
    "resolution_failed": "resolution_failed",
    "statistically_insufficient": "statistically_insufficient",
}

# Terminal states the legacy polarity path can reach, mapped to the reason code
# they carry. ``promotion_unmeasurable`` and ``promotion_blocked_preflight``
# are not candidate failures -- the first says the batch could not be measured,
# the second that the run never started -- so they must not be recorded as
# "candidate design failed".
LEGACY_STATE_REASONS = {
    "promoted": "promoted",
    "promotion_rejected": "candidate_no_effect",
    "promotion_unresolved": "runtime_unresolved",
    "promotion_unmeasurable": "runtime_unresolved",
    "promotion_blocked_preflight": "protocol_invalid",
}


def case_ids(values: Sequence[str], *, name: str) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list of canonical relative case ids")
    result = []
    for value in values:
        if (
            not isinstance(value, str) or not value.strip() or "\\" in value
            or ":" in value or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError(f"{name} contains a non-canonical case id")
        result.append(value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicate case ids")
    return sorted(result)


def mechanism_descriptor(mechanism: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(mechanism, Mapping):
        raise ValueError("a mechanism object is required")
    if mechanism.get("schema_version") != MECHANISM_SCHEMA:
        raise ValueError("unsupported mechanism schema")
    if mechanism.get("status") != "confirmed":
        raise ValueError("formal promotion requires a confirmed mechanism")
    if mechanism.get("objective") not in {"detection", "localization", "joint", "resolution"}:
        raise ValueError("invalid mechanism objective")
    for block, field in (
        ("task_obligation", "kind"), ("observed_deviation", "kind"),
        ("detector_failure", "stage"), ("detector_failure", "failure_mode"),
        ("repair_hypothesis", "operation"),
    ):
        if not isinstance(mechanism.get(block), Mapping) or not mechanism[block].get(field):
            raise ValueError(f"mechanism is missing {block}.{field}")
    if mechanism["repair_hypothesis"]["operation"] == "unavailable":
        raise ValueError("mechanism has no repair operation")
    if mechanism.get("mechanism_id") != mechanism_key(mechanism):
        raise ValueError("mechanism id does not match its repair signature")
    members = case_ids(mechanism.get("member_cases", []), name="mechanism members")
    if len(members) < 2:
        raise ValueError("formal promotion needs at least two mechanism discovery cases")
    return copy.deepcopy(dict(mechanism))


def development_cases(candidate: Mapping[str, Any]) -> list[str]:
    """Union of declared exposure, not just the final artifact's provenance."""
    seen: set[str] = set()
    for field in ("development_cases",):
        seen.update(case_ids(candidate.get(field, []), name=field))
    mechanism = candidate.get("mechanism") or {}
    for field in ("member_cases", "normal_neighbors"):
        seen.update(case_ids(mechanism.get(field, []), name=field))
    for artifact in candidate.get("artifacts") or ():
        seen.update(case_ids((artifact.get("provenance") or {}).get(
            "failure_cases", []), name="artifact development cases"))
    development = candidate.get("development_results") or {}
    for field in ("development_cases", "target_cases", "neighbor_cases"):
        seen.update(case_ids(development.get(field, []), name=field))
    for field in ("training", "neighbors"):
        value = development.get(field) or {}
        if isinstance(value, Mapping):
            seen.update(case_ids(list(value), name=field))
    return sorted(seen)


def validate_membership(
    membership: Mapping[str, Any], cases: Sequence[str], mechanism: Mapping[str, Any],
    roles: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(membership, Mapping) or set(membership) != set(cases):
        raise ValueError("membership must explicitly cover every promotion case")
    mechanism_id = mechanism["mechanism_id"]
    for case in cases:
        item = membership[case]
        if not isinstance(item, Mapping):
            raise ValueError("membership entries must be evidence-bearing objects")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(value, str) or not value.strip() for value in evidence
        ):
            raise ValueError("membership needs non-empty evaluator evidence")
        if item.get("status") not in {"confirmed", "external"}:
            raise ValueError("ambiguous membership is not a formal evaluation case")
        if not isinstance(item.get("mechanism_id"), str) or not item["mechanism_id"]:
            raise ValueError("membership must declare a mechanism id")
        if type(item.get("repairable")) is not bool:
            raise ValueError("membership repairable must be boolean")
        if roles[case] == "benefit":
            if item["mechanism_id"] != mechanism_id or item["status"] != "confirmed":
                raise ValueError("gain cases must be confirmed members of the target mechanism")
            if item["repairable"] is not True:
                raise ValueError("gain cases need an explicit repairable assessment")
        if roles[case] == "challenge" and (
            item["mechanism_id"] == mechanism_id or item["status"] != "external"
        ):
            raise ValueError("challenge cases must be explicitly mechanism-external")
        if "localization_viable" in item and type(item["localization_viable"]) is not bool:
            raise ValueError("localization viability must be boolean")
    return copy.deepcopy(dict(membership))


def data_digests(dataset_root: str | Path, cases: Sequence[str]) -> dict[str, str]:
    root = Path(dataset_root).resolve()
    result = {}
    for case in cases:
        path = (root / case).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"promotion case is missing or outside the dataset: {case}")
        result[case] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def frozen_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    from evolution.config import DEFAULTS

    return {
        "gates": {**DEFAULTS["gates"], **(config.get("gates") or {})},
        "library": {**DEFAULTS["library"], **(config.get("library") or {})},
    }


def build_alignment(
    protocol: Mapping[str, Any], *, candidate: Mapping[str, Any],
    mechanism: Mapping[str, Any], membership: Mapping[str, Any],
    dataset_root: str | Path, config: Mapping[str, Any],
    baseline_registry_hash: str,
) -> dict[str, Any]:
    mechanism = mechanism_descriptor(mechanism)
    if candidate.get("schema_version") != ALIGNED_CANDIDATE_SCHEMA:
        raise ValueError("mechanism-aligned promotion needs a v2 mechanism-bound candidate")
    if candidate.get("target_mechanism_id") != mechanism["mechanism_id"]:
        raise ValueError("candidate and protocol target different mechanisms")
    if digest(candidate.get("mechanism")) != digest(mechanism):
        raise ValueError("candidate and protocol mechanism evidence differ")
    if candidate.get("target_kind") != mechanism["objective"]:
        raise ValueError("candidate target_kind must match the mechanism objective")
    cases = case_ids(protocol["cases"], name="promotion cases")
    roles = protocol["roles"]
    if set(roles) != set(cases) or set(roles.values()) != {"benefit", "protection", "challenge"}:
        raise ValueError("mechanism-aligned protocol needs disjoint, nonempty gain, protection and challenge sets")
    membership = validate_membership(membership, cases, mechanism, roles)
    exposed = sorted(set(development_cases(candidate)) | set(protocol["development_cases"]))
    if set(exposed) & set(cases):
        raise ValueError("candidate-unused rule: formal cases overlap development or normal neighbors")
    if any(type(protocol["gold"].get(case)) is not bool for case in cases):
        raise ValueError("formal gold labels must be boolean")
    if not baseline_registry_hash:
        raise ValueError("mechanism-aligned protocol must freeze the active baseline registry identity")
    policy = frozen_policy(config)
    for key in ("harmlessness_new_fn_allowed", "harmlessness_new_fp_allowed",
                "harmlessness_new_unresolved_allowed", "m_new_errors_allowed",
                "localization_joint_broken_allowed"):
        if policy["gates"].get(key) != 0:
            raise ValueError(f"mechanism-aligned protocol does not allow relaxing {key}")
    for key in ("harmlessness_min_protected_tp", "harmlessness_min_protected_tn"):
        if type(policy["gates"].get(key)) is not int or policy["gates"][key] < 2:
            raise ValueError(f"mechanism-aligned protocol requires {key} >= 2")
    if policy["gates"].get("behavior_require_fired") is not True:
        raise ValueError("mechanism-aligned protocol requires actual hook firing")
    for key in ("m_min_marginal_cases", "localization_min_joint_cases"):
        if type(policy["gates"].get(key)) is not int or policy["gates"][key] < 1:
            raise ValueError(f"mechanism-aligned protocol requires {key} >= 1")
    if mechanism["objective"] in {"localization", "joint"}:
        for case in cases:
            if protocol["gold"][case]:
                step = protocol["gold_steps"].get(case)
                if type(step) is not int or step < 1:
                    raise ValueError("localization protocols require positive integer gold steps")
    if protocol.get("workers", 0) < 1:
        raise ValueError("workers must be positive")
    timeout = protocol.get("timeout_seconds")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout_seconds must be finite and positive")
    bound = copy.deepcopy(dict(protocol))
    bound.update({
        "schema_version": ALIGNED_PROTOCOL_SCHEMA,
        "candidate_content_hash": candidate["content_hash"],
        "mechanism_id": mechanism["mechanism_id"],
        "mechanism": mechanism,
        "mechanism_membership": membership,
        "membership_schema": MEMBERSHIP_SCHEMA,
        "mechanism_gain_cases": sorted(case for case in cases if roles[case] == "benefit"),
        "challenge_cases": sorted(case for case in cases if roles[case] == "challenge"),
        "development_cases": exposed,
        "baseline_registry_hash": baseline_registry_hash,
        "policy": policy,
        "case_digests": data_digests(dataset_root, cases),
        "evidence_scope": "candidate_unused_development_data",
        "mechanism_opportunity": {
            "evaluable": None, "reason_code": "baseline_not_measured",
        },
    })
    bound["protocol_hash"] = digest(bound)
    return bound



def verify_alignment(protocol: Mapping[str, Any]) -> None:
    raw = {key: value for key, value in protocol.items()
           if key not in {"protocol_hash", "loaded_from", "resolved_cases"}}
    if protocol.get("protocol_hash") != digest(raw):
        raise ValueError("frozen protocol content hash mismatch")
    schema = protocol.get("schema_version")
    if schema != ALIGNED_PROTOCOL_SCHEMA:
        raise ValueError(f"unsupported promotion protocol schema: {schema}")
    mechanism = mechanism_descriptor(protocol["mechanism"])
    cases = case_ids(protocol["cases"], name="promotion cases")
    roles = protocol["roles"]
    if set(roles) != set(cases) or set(roles.values()) != {"benefit", "protection", "challenge"}:
        raise ValueError("frozen roles do not define three complete disjoint sets")
    validate_membership(protocol["mechanism_membership"], cases, mechanism, roles)
    if protocol["mechanism_id"] != mechanism["mechanism_id"]:
        raise ValueError("protocol mechanism id mismatch")
    if sorted(protocol["mechanism_gain_cases"]) != sorted(case for case in cases if roles[case] == "benefit"):
        raise ValueError("mechanism gain cases disagree with frozen roles")
    if sorted(protocol["challenge_cases"]) != sorted(case for case in cases if roles[case] == "challenge"):
        raise ValueError("challenge cases disagree with frozen roles")
    if set(case_ids(protocol["development_cases"], name="development cases")) & set(cases):
        raise ValueError("candidate-unused rule: development overlaps the formal batch")
    # Re-derived, not trusted: a localization protocol is scored against the
    # annotated step, and this is the same precondition ``build_alignment``
    # enforces at freeze time. A consumer that only calls ``verify_alignment``
    # must not accept a protocol the freeze would have rejected -- without
    # this, an unannotated localization batch reads as "measurement
    # unresolved" at run time instead of being refused up front.
    if mechanism["objective"] in {"localization", "joint"}:
        for case in cases:
            if protocol["gold"][case]:
                step = protocol["gold_steps"].get(case)
                if type(step) is not int or step < 1:
                    raise ValueError(
                        "localization protocols require positive integer gold "
                        f"steps (case {case!r})"
                    )
    if not protocol.get("baseline_registry_hash"):
        raise ValueError("protocol must freeze the baseline registry identity")


def checked_rows(rows: Sequence[Mapping[str, Any]], protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    cases = [row.get("case") for row in rows]
    if len(cases) != len(set(cases)) or set(cases) != set(protocol["cases"]):
        raise ValueError("arm case coverage differs from the frozen batch")
    for row in rows:
        if not mechanism_row_healthy(row):
            raise ValueError(f"arm is unresolved or unhealthy on {row.get('case')}")
        if row.get("gold") is not protocol["gold"][row["case"]]:
            raise ValueError("arm labels differ from the frozen protocol")
    return [dict(row) for row in rows]


def opportunity(protocol: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gain = set(protocol["mechanism_gain_cases"])
    return mechanism_opportunity(
        [row for row in rows if row.get("case") in gain],
        protocol["mechanism"], protocol["mechanism_membership"],
        gold_steps=protocol["gold_steps"],
    )


def diagnosis(code: str, *, mechanism_id: str = "", reason: str = "") -> dict[str, Any]:
    """One terminal state as a machine-readable reason.

    Every code a promotion path can emit has to be in this table: an unmapped
    code becomes ``unclassified``, which reads as "no reason recorded" for a
    run that did record one. ``REASON_CATEGORIES`` is the single source, and
    ``test_promotion_alignment.py`` fails if the reason codes reachable from
    the aligned and legacy paths are not all covered.
    """
    category = REASON_CATEGORIES.get(code, "unclassified")
    return {
        "schema_version": "evolution.promotion_diagnosis.v1",
        "metric_role": "diagnostic-only",
        "mechanism_id": mechanism_id,
        "reason_code": code,
        "category": category,
        "reason": reason,
        # The two questions §16 E2 asks that a single batch cannot answer:
        # both stay explicitly unanswered (None) rather than being folded into
        # "candidate design failed", which would blame the candidate for a
        # mechanism or action class the harness cannot repair.
        "mechanism_unrepairable": None,
        "action_class_insufficient": None,
    }
