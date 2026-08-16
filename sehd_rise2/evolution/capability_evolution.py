"""Rebuilt evolution operator: failure class -> executable component.

The Round-2 convention extractor generated prompt directives for every
failure family regardless of whether the failure was a missing
capability or an execution slip. This module replaces that operator in
the round pipeline:

- ``missing_capability`` produces an executable component
  (``evidence_predicate`` or ``candidate_generator``) with input/output
  contracts and counterfactual tests;
- ``not_triggered`` amends the trigger of an existing component;
- ``execution_failure`` may only produce a ``prompt_directive`` stage
  patch — it is forbidden from claiming a new capability;
- ``decision_conflict`` produces an ``adjudication_policy`` amend/retire.

The proposer is NOT one-shot: a bounded revision loop feeds G1 rejection
reasons and local counterfactual test results back to the drafting call,
so drafting quality improves inside the round instead of dying at the
gate. The mechanical gate itself is unchanged and stricter than the
Round-2 G1 — a prompt patch that merely repeats an existing stage rule
is rejected as redundant, which is the exact Round-2 EC-05 failure mode.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.artifacts import (
    ARTIFACT_KINDS,
    CERTIFICATE_CLASSES,
    POLICY_ACTIONS,
    POLICY_FREE_CLASSES,
    registry_hash,
    validate_artifact,
    validate_registry,
)
from sehd_rise2.detector.capabilities import PREDICATE_RUNNERS
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.provider import ProviderError, provider_call
# Capability component kinds that add a new execution path. A
# prompt_directive is NOT in this set: it only changes the text of an
# existing LLM stage, not the state space of the detector.
EXECUTABLE_COMPONENT_KINDS = frozenset({
    "evidence_predicate", "candidate_generator",
})
from sehd_rise2.evolution.gap_taxonomy import GAP_CATEGORIES

CASE_ID_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*/\d+\.json\b")
MIN_SUPPORT_CASES = 2
TEXT_MAX_CHARS = 400
REDUNDANCY_OVERLAP = 0.72

GAP_CATEGORY_BY_NAME = {
    name: category
    for category, names in GAP_CATEGORIES.items()
    for name in names
}

# The deployment representation allowed per routed failure class.
# Unstable rows carry missing_capability (widest channel): a
# stabilisation attempt is simply a component that must survive the
# three-roll flip gate.
KIND_BY_FAILURE_CLASS: dict[str, frozenset[str]] = {
    "missing_capability": frozenset({
        "evidence_predicate", "candidate_generator",
    }),
    "not_triggered": frozenset({
        "prompt_directive", "adjudication_policy",
        "evidence_predicate", "candidate_generator",
    }),
    "execution_failure": frozenset({"prompt_directive"}),
    "decision_conflict": frozenset({"adjudication_policy"}),
    "infrastructure": frozenset(),
}

PROPOSAL_SYSTEM = (
    "You are the capability proposer of a self-evolving hallucination "
    "detector. You receive mechanically classified failures, never raw "
    "labels alone. Each failure carries exactly one class, one allowed "
    "channel and an evidence block — the gold error description with the "
    "detector's own scan for misses, the convicting certificates for "
    "wrongful convictions. Design every component against that "
    "evidence, never against case ids:\n"
    "- missing_capability: propose a NEW executable component. "
    "evidence_predicate = a structured check whose verdict enters "
    "verification/defense as evidence; candidate_generator = a separate "
    "census extension stage that finds missed candidates.\n"
    "- not_triggered: amend the trigger of an EXISTING component; never "
    "invent a new one.\n"
    "- execution_failure: the rule already exists but a stage did not "
    "follow it. Propose only a prompt_directive stage patch, and only "
    "if you can name the stage prompt that lacks the enforcement.\n"
    "- decision_conflict: propose an adjudication_policy amend/retire.\n"
    "Hard rules: every proposal is mechanism-level, supported by >=2 "
    "cases of the same class and mechanism; never quote a case id inside "
    "directive/exclusion/signature/capability text; executable "
    "components MUST carry capability.input, capability.output, "
    "implementation and counterfactual_tests (at least one positive "
    "preservation case and one negative mutation/omission case). When "
    "round_constraints.evidence_predicate_runners lists runners, every "
    "evidence_predicate must use one of them. When the payload carries "
    "revision_feedback, return the complete corrected proposal set: "
    "address every listed rejection and failed test; proposals that "
    "already passed may be resubmitted unchanged. Reply "
    "with ONLY JSON: {\"proposals\": [ ... ]}. No commentary."
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


def _text_case_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(CASE_ID_IN_TEXT.findall(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            found.update(_text_case_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_text_case_ids(item))
    return found


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9']+", text.lower())
        if len(token) > 2
    }


def directive_redundancy(text: str, baseline_texts: Sequence[str]) -> float:
    """Jaccard overlap against the stage rules already deployed.

    EC-05 scored against ``court.VERIFICATION_SYSTEM``'s existing
    prose-tolerance rule and passed anyway; this gate closes that hole.
    """
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    scores = []
    for baseline in baseline_texts:
        other = _tokens(str(baseline or ""))
        if not other:
            continue
        scores.append(len(tokens & other) / len(tokens | other))
    return max(scores, default=0.0)


def _expected_verdicts(proposal: Mapping[str, Any]) -> set[str]:
    capability = proposal.get("capability")
    capability = capability if isinstance(capability, Mapping) else {}
    output = capability.get("output")
    output = output if isinstance(output, Mapping) else {}
    verdict = output.get("verdict")
    verdict = verdict if isinstance(verdict, Mapping) else {}
    return {str(item) for item in verdict.get("enum") or []}


def validate_proposal(
    proposal: Mapping[str, Any],
    *,
    diagnoses: Mapping[str, Mapping[str, Any]],
    active_artifacts: Sequence[Mapping[str, Any]] = (),
    baseline_rule_texts: Sequence[str] = (),
    allowed_predicate_runners: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Mechanical gate; returns rejection records (empty = accepted)."""
    rejections: list[dict[str, str]] = []
    artifact_id = str(proposal.get("artifact_id") or "")

    def reject(reason: str) -> None:
        rejections.append({"artifact_id": artifact_id, "reason": reason})

    if not artifact_id:
        reject("artifact_id missing")
        return rejections
    evidence = proposal.get("case_evidence") or []
    evidence_ids = {
        str(entry.get("case_id")) for entry in evidence
        if isinstance(entry, Mapping) and entry.get("case_id")
    }
    if not evidence_ids <= set(diagnoses):
        reject("case evidence outside the diagnosed failures")
    if len(evidence_ids) < MIN_SUPPORT_CASES:
        reject(
            f"mechanism supported by {len(evidence_ids)} case(s), "
            f"needs {MIN_SUPPORT_CASES}"
        )
    classes = {
        str(diagnoses[case_id]["failure_class"]) for case_id in evidence_ids
    }
    if len(classes) != 1:
        reject(f"proposal mixes failure classes {sorted(classes)}")
        return rejections
    failure_class = next(iter(classes))
    kind = str(proposal.get("kind") or "")
    if kind not in ARTIFACT_KINDS:
        reject(f"kind {kind!r} has no detector executor")
        return rejections
    if kind not in KIND_BY_FAILURE_CLASS[failure_class]:
        reject(
            f"kind {kind!r} is not allowed for failure class "
            f"{failure_class!r}"
        )
        return rejections

    title = str(proposal.get("title") or "")
    gap = proposal.get("gap")
    gap = gap if isinstance(gap, Mapping) else {}
    gap_name = str(gap.get("name") or "")
    gap_category = str(gap.get("category") or "")
    expected_category = GAP_CATEGORY_BY_NAME.get(gap_name)
    if expected_category is None:
        reject(f"gap name {gap_name!r} outside the frozen taxonomy")
        return rejections
    if gap_category and gap_category != expected_category:
        reject(
            f"gap {gap_name!r} belongs to {expected_category!r}, "
            f"not {gap_category!r}"
        )
        return rejections

    amends = str(proposal.get("amends") or "")
    if failure_class == "not_triggered":
        if not amends:
            reject("not_triggered proposal must name amends artifact")
        else:
            existing = {
                str(artifact.get("artifact_id")) for artifact in active_artifacts
            }
            if amends not in existing:
                reject(f"amends unknown artifact {amends!r}")
    elif amends and amends not in {
        str(artifact.get("artifact_id")) for artifact in active_artifacts
    }:
        reject(f"amends unknown artifact {amends!r}")

    if kind == "prompt_directive":
        if failure_class != "execution_failure":
            reject("prompt_directive may only patch execution_failure")
            return rejections
        intervention = proposal.get("intervention") or {}
        directive = str(intervention.get("directive") or "")
        exclusion = str(intervention.get("exclusion") or "")
        if not directive or not exclusion:
            reject("execution patch needs directive and exclusion")
        overlap = directive_redundancy(
            directive + " " + exclusion, baseline_rule_texts,
        )
        if overlap >= REDUNDANCY_OVERLAP:
            reject(
                f"execution patch repeats an existing stage rule "
                f"(overlap {overlap:.2f})"
            )
        if _text_case_ids(directive + exclusion):
            reject("prompt patch contains a case id")
    elif kind == "adjudication_policy":
        if failure_class != "decision_conflict":
            reject("adjudication_policy may only resolve decision_conflict")
            return rejections
        trigger = proposal.get("trigger") or {}
        certificate_class = str(trigger.get("certificate_class") or "")
        if certificate_class not in CERTIFICATE_CLASSES:
            reject("certificate class outside the closed vocabulary")
        if certificate_class in POLICY_FREE_CLASSES:
            reject("certificate class is policy-free by design")
        intervention = proposal.get("intervention") or {}
        if str(intervention.get("action") or "") not in POLICY_ACTIONS:
            reject("policy action outside the closed vocabulary")
        precondition = intervention.get("precondition")
        if not isinstance(precondition, Mapping) or not precondition:
            reject(
                "new decision_conflict policies must declare a capability "
                "precondition (capability_id + expect verdicts)"
            )
    else:
        if failure_class != "missing_capability":
            reject("executable components may only fill missing_capability")
            return rejections
        if kind not in EXECUTABLE_COMPONENT_KINDS:
            reject(f"kind {kind!r} is not an executable component")
        if kind == "candidate_generator":
            if gap_category != "observation" or str(
                gap.get("gap_type") or ""
            ) != "recall" or str(gap.get("affected_stage") or "") != "census":
                reject(
                    "candidate_generator needs observation/recall/census"
                )
        if kind == "evidence_predicate":
            if gap_category != "verification":
                reject("evidence_predicate needs a verification gap")
            if str(gap.get("affected_stage") or "") != "verification":
                reject("evidence_predicate must affect verification")
            runners = set(allowed_predicate_runners or PREDICATE_RUNNERS)
            runner = str(
                (proposal.get("implementation") or {}).get("runner") or ""
            )
            if runner not in runners:
                reject(
                    f"evidence_predicate runner {runner!r} outside the "
                    f"round-allowed {sorted(runners)}"
                )
        capability = proposal.get("capability") or {}
        implementation = proposal.get("implementation") or {}
        tests = proposal.get("counterfactual_tests") or []
        if not isinstance(capability, Mapping) or not capability.get("name"):
            reject("component capability block missing")
        if not str(capability.get("failure_mode") or ""):
            reject("component capability.failure_mode missing")
        if not isinstance(implementation, Mapping) or not implementation.get(
            "runner"
        ):
            reject("component implementation runner missing")
        if not isinstance(tests, list) or len(tests) < 2:
            reject("component needs at least two counterfactual tests")
        elif kind == "evidence_predicate":
            verdicts = _expected_verdicts(proposal)
            expectations = {
                str(test.get("expect")) for test in tests
                if isinstance(test, Mapping)
            }
            if expectations and not expectations <= verdicts:
                reject("counterfactual expectations outside output verdicts")
    for field in ("failure_signature", "expected_capability"):
        text = str(gap.get(field) or "")
        if not text or len(text) > TEXT_MAX_CHARS:
            reject(f"gap.{field} missing or over length")
        if _text_case_ids(text):
            reject(f"gap.{field} contains a case id")
    if not title:
        reject("title missing")
    return rejections


def build_artifact(
    proposal: Mapping[str, Any],
    *,
    case_evidence: Sequence[str],
    active_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Turn an accepted proposal into a deployable artifact payload."""
    amends = str(proposal.get("amends") or "")
    base = active_by_id.get(amends, {})
    version = "v1"
    if base:
        current = str(base.get("version") or "v1")
        version = f"v{int(current[1:]) + 1}" if current[1:].isdigit() else "v1"
    artifact: dict[str, Any] = {
        "artifact_id": str(proposal["artifact_id"]),
        "version": version,
        "kind": str(proposal["kind"]),
        "execution_point": ARTIFACT_KINDS[str(proposal["kind"])],
        "gap": dict(proposal.get("gap") or {}),
        "trigger": dict(proposal.get("trigger") or {}),
        "capability_delta": dict(proposal.get("capability_delta") or {}),
        "provenance": {
            "failure_class": str(proposal.get("failure_class") or ""),
            "case_evidence": sorted(case_evidence),
            "amends": amends or None,
        },
    }
    intervention = proposal.get("intervention")
    if isinstance(intervention, Mapping):
        artifact["intervention"] = dict(intervention)
    capability = proposal.get("capability")
    if isinstance(capability, Mapping):
        artifact["capability"] = dict(capability)
    implementation = proposal.get("implementation")
    if isinstance(implementation, Mapping):
        artifact["implementation"] = dict(implementation)
    tests = proposal.get("counterfactual_tests")
    if isinstance(tests, list):
        artifact["counterfactual_tests"] = [dict(test) for test in tests]
    if base and "risk_boundary" in base:
        artifact["risk_boundary"] = dict(base["risk_boundary"])
    return artifact


def gate_proposals(
    proposals: Sequence[Mapping[str, Any]],
    *,
    diagnoses: Mapping[str, Mapping[str, Any]],
    active_artifacts: Sequence[Mapping[str, Any]] = (),
    baseline_rule_texts: Sequence[str] = (),
    allowed_predicate_runners: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """G1: validate and build deployable artifacts (fail-closed)."""
    active_by_id = {
        str(artifact.get("artifact_id")): dict(artifact)
        for artifact in active_artifacts
    }
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for proposal in proposals:
        if not isinstance(proposal, Mapping):
            rejections.append({
                "artifact_id": "<invalid>", "reason": "not an object",
            })
            continue
        artifact_id = str(proposal.get("artifact_id") or "")
        if artifact_id in seen_ids:
            rejections.append({
                "artifact_id": artifact_id, "reason": "duplicate id",
            })
            continue
        seen_ids.add(artifact_id)
        reasons = validate_proposal(
            proposal, diagnoses=diagnoses,
            active_artifacts=active_artifacts,
            baseline_rule_texts=baseline_rule_texts,
            allowed_predicate_runners=allowed_predicate_runners,
        )
        if reasons:
            rejections.extend(reasons)
            continue
        evidence = proposal.get("case_evidence") or []
        evidence_ids = sorted({
            str(entry.get("case_id")) for entry in evidence
            if isinstance(entry, Mapping) and entry.get("case_id")
        })
        normalized = dict(proposal)
        normalized.setdefault(
            "failure_class",
            next(iter({
                str(diagnoses[case_id]["failure_class"])
                for case_id in evidence_ids
            })),
        )
        artifact = build_artifact(
            normalized, case_evidence=evidence_ids,
            active_by_id=active_by_id,
        )
        try:
            validate_artifact(artifact)
        except ValueError as exc:
            rejections.append({
                "artifact_id": artifact_id,
                "reason": f"artifact contract: {exc}",
            })
            continue
        accepted.append(artifact)
    return accepted, rejections


def _call_proposer(
    user_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One drafting call; returns (raw proposals, provider meta)."""
    config = load_config()
    route = dict(config["verification_route"])
    route["model"] = os.environ.get(
        "SEHD_MUTATION_MODEL", "deepseek-v4-pro",
    )
    route["_route_name"] = "capability_proposal"
    parsed, meta = provider_call(
        route=route, system=PROPOSAL_SYSTEM,
        user=json.dumps(user_payload, ensure_ascii=False, indent=1),
        purpose="capability_proposal",
    )
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("proposals"), list,
    ):
        raise ProviderError(
            "capability_proposal", "answer is not a proposals object",
            error_type="contract",
        )
    return [
        dict(item) for item in parsed["proposals"]
        if isinstance(item, Mapping)
    ], meta


def generate_proposals(
    *,
    diagnoses: Sequence[Mapping[str, Any]],
    registry_path: Path | None = None,
    baseline_rule_texts: Sequence[str] = (),
    active_artifacts: Sequence[Mapping[str, Any]] | None = None,
    allowed_predicate_runners: Sequence[str] | None = None,
    revision_rounds: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Draft, gate and revise: classified failures -> deployable artifacts.

    The loop is bounded to ``1 + revision_rounds`` drafting calls. Each
    round re-gates the full set; G1 rejection reasons feed the next
    draft. The flip gate stays downstream and out of the loop — a
    component that gates clean but flips nothing dies there, not in
    drafting.
    """
    deployed: list[dict[str, Any]] = []
    if active_artifacts is not None:
        deployed = [dict(artifact) for artifact in active_artifacts]
    elif registry_path is not None and Path(registry_path).exists():
        deployed = validate_registry(_read_json(Path(registry_path)))
    runners = sorted(allowed_predicate_runners or PREDICATE_RUNNERS)
    diagnoses_map = {
        str(row["case_id"]): dict(row) for row in diagnoses
    }
    base_payload = {
        "diagnoses": list(diagnoses),
        "active_artifacts": deployed,
        "baseline_rule_texts": list(baseline_rule_texts),
        "round_constraints": {"evidence_predicate_runners": runners},
    }

    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    revision: list[dict[str, Any]] = []
    provider_meta: dict[str, Any] = {}
    attempt = 0
    while True:
        attempt += 1
        payload = dict(base_payload)
        if attempt > 1:
            payload["revision_feedback"] = {
                "g1_rejections": rejections,
            }
        proposals, provider_meta = _call_proposer(payload)
        accepted, rejections = gate_proposals(
            proposals,
            diagnoses=diagnoses_map,
            active_artifacts=deployed,
            baseline_rule_texts=baseline_rule_texts,
            allowed_predicate_runners=allowed_predicate_runners,
        )
        revision.append({
            "attempt": attempt,
            "raw_proposals": len(proposals),
            "g1_accepted": len(accepted),
            "g1_rejections": rejections,
        })
        if attempt > revision_rounds or not rejections:
            break

    provider_meta.update({
        "raw_proposals": revision[-1]["raw_proposals"],
        "accepted": len(accepted),
        "rejections": rejections,
        "revision": revision,
        "revision_attempts": attempt,
    })
    return accepted, provider_meta


def build_registry(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    registry = {
        "schema_version": "sehd2.evolution_artifact.v1",
        "artifacts": [dict(artifact) for artifact in artifacts],
    }
    registry["content_hash"] = registry_hash(registry)
    return registry


__all__ = [
    "REDUNDANCY_OVERLAP",
    "build_artifact",
    "build_registry",
    "directive_redundancy",
    "gate_proposals",
    "generate_proposals",
    "validate_proposal",
]



