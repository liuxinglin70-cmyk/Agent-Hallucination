"""One-step action boundaries derived from observed failures, without verdict authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Literal, Mapping, Sequence

from detector.causal_contracts import seal
from detector.identity import digest
from detector.packet import validate_packet

ACTION_AUDIT_SCHEMA = "detector.next_action_audit.v1"
_FAILURE_LINE = re.compile(
    r"(?im)^\s*(?:(?:error|exception|traceback|failed|failure)\b(?:\s*[:(]|\s*$)"
    r"|error (?:during execution|fetching (?:the )?webpage)\s*:)")
_SUCCESS_CLAIM = re.compile(
    r"\b(?:successfully (?:completed|started|created|sent|saved|deleted)|"
    r"(?:task|operation|request|action) (?:is |was |has been )?(?:complete|completed|successful))\b", re.I,
)
_FAILURE_REPORT = re.compile(r"\b(?:error|failed|failure|unable|cannot|could not|blocked|permission)\b", re.I)


def _claims_success(text: str) -> bool:
    for match in _SUCCESS_CLAIM.finditer(text):
        prefix = re.split(r"[.!?\n;]", text[:match.start()])[-1]
        if not re.search(r"\b(?:not|never|if|whether|would|could)\b", prefix, re.I):
            return True
    return False


def content_claim_signal(text: Any) -> bool:
    """Whether prose asserts success or reports failure.

    Those are the claim kinds a local next-action check can confront with the
    recorded observations.  Unrelated narration ("Thinking", "Done") carries
    no checkable claim about an action and should not buy a model request.
    """
    value = str(text or "")
    return _claims_success(value) or bool(_FAILURE_REPORT.search(value))


def _observed_success(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return value.strip().lower() in {"success", "successful", "completed"}
    return isinstance(value, Mapping) and not observation_failed(value) and (
        value.get("success") is True or value.get("ok") is True
        or str(value.get("status") or "").lower() in {"success", "successful", "completed"})


def observation_failed(value: Any) -> bool:
    """Recognize explicit runtime failure markers; missing evidence stays unknown."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return bool(_FAILURE_LINE.search(value))
    if not isinstance(value, Mapping):
        return False
    return (value.get("success") is False or value.get("ok") is False
            or str(value.get("status") or "").lower() in {"error", "failed", "failure"}
            or value.get("error") not in (None, "", False, {}, []))


@dataclass(frozen=True)
class ActionBoundary:
    source_event_id: str
    source_position: int
    prefix_digest: str
    risk_pattern: Literal["observed_tool_failure"]
    evidence_refs: tuple[str, ...]
    failed_tools: tuple[str, ...]
    allowed_actions: tuple[str, ...] = ("retry_or_repair", "available_fallback", "honest_failure_report")
    forbidden_actions: tuple[str, ...] = ("claim_success_without_observed_recovery",)


def predict_action_boundary(packet: Mapping[str, Any], event_id: str) -> ActionBoundary | None:
    """Use only observations available through t; never generate t+1 observations."""
    validate_packet(packet)
    source = next((event for event in packet["events"] if event["event_id"] == event_id), None)
    if source is None:
        raise ValueError("next-action source event is absent")
    failures = [item for item in source["tool_interactions"] if observation_failed(item["observation"])]
    if not failures:
        return None
    prefix = {"question": packet["question"], "events": packet["events"][:source["position"]]}
    return ActionBoundary(
        source_event_id=event_id, source_position=source["position"], prefix_digest=digest(prefix),
        risk_pattern="observed_tool_failure",
        evidence_refs=tuple(item["interaction_id"] for item in failures),
        failed_tools=tuple(dict.fromkeys(str(item["tool_name"]) for item in failures)),
    )


def compare_next_action(packet: Mapping[str, Any], boundary: ActionBoundary) -> dict[str, Any]:
    """Compare the recorded next event; unrelated or unavailable actions stay unknown."""
    expected = predict_action_boundary(packet, boundary.source_event_id)
    if expected != boundary:
        raise ValueError("next-action boundary differs from observed prefix")
    position = boundary.source_position
    action = packet["events"][position] if position < len(packet["events"]) else None
    state, pattern = "unknown", "next_event_missing"
    refs = list(boundary.evidence_refs)
    if action is not None:
        refs.append(action["event_id"])
        interactions = action["tool_interactions"]
        text = str(action.get("content") or "")
        retries = [item for item in interactions if str(item["tool_name"]) in boundary.failed_tools]
        recovered = any(_observed_success(item["observation"]) for item in retries)
        terminal_success = any(
            str(item["tool_name"]).lower() in {"terminate", "finish", "complete", "submit"}
            and isinstance(item["arguments"], Mapping)
            and (item["arguments"].get("success") is True
                 or str(item["arguments"].get("status") or "").lower() in {"success", "completed"})
            for item in interactions)
        claims_success = _claims_success(text) or terminal_success
        possible_recovery = any(str(item["tool_name"]) not in boundary.failed_tools
                                and _observed_success(item["observation"]) for item in interactions)
        if claims_success and possible_recovery and not terminal_success and not recovered:
            state, pattern = "unknown", "possible_fallback_unverified"
        elif claims_success and not recovered:
            state, pattern = "divergent", "success_claim_without_observed_recovery"
        elif retries:
            state, pattern = "compliant", "observed_retry_or_repair"
        elif _FAILURE_REPORT.search(text) and not claims_success:
            state, pattern = "compliant", "honest_failure_report"
        elif interactions:
            # The record alone does not establish that a different tool is a legal fallback.
            state, pattern = "unknown", "possible_fallback_unverified"
        else:
            pattern = "unrelated_or_unrecognized_action"
    return {
        "boundary": asdict(boundary), "next_event_id": action["event_id"] if action else None,
        "next_position": action["position"] if action else None, "action_state": state,
        "divergence_pattern": pattern, "evidence_refs": refs,
        "verification_status": "hypothesis", "decision_authority": "none",
        "localization_authority": "none",
    }


def build_next_action_audit(packet: Mapping[str, Any], event_ids: Sequence[str]) -> dict[str, Any]:
    comparisons = []
    for event_id in dict.fromkeys(event_ids):
        boundary = predict_action_boundary(packet, event_id)
        if boundary is not None:
            comparisons.append(compare_next_action(packet, boundary))
    return seal({"schema_version": ACTION_AUDIT_SCHEMA, "packet_digest": digest(packet),
                 "scope": "recorded_single_next_event", "provider_requests": 0,
                 "decision_authority": "none", "localization_authority": "none",
                 "comparisons": comparisons})


def scan_static_action_risks(packet: Mapping[str, Any], *, top_k: int) -> dict[str, Any]:
    """Inspect every recorded event before projecting a bounded candidate set."""
    validate_packet(packet)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("static action top_k must be a positive integer")
    rows: list[dict[str, Any]] = []
    failure_count = 0
    for event in packet["events"]:
        refs = [item["interaction_id"] for item in event["tool_interactions"]
                if observation_failed(item["observation"])]
        if not refs:
            continue
        failure_count += 1
        if len(rows) < top_k:
            rows.append({"event_id": event["event_id"], "position": event["position"],
                         "score_status": "scored", "priority": 1,
                         "risk_signals": [{"signal": "observed_tool_failure", "producer": "observed_failure.v1",
                                           "verification_status": "hypothesis", "evidence_refs": refs,
                                           "responsibility": "unassigned"}]})
    return {"scanned_events": len(packet["events"]), "failure_events": failure_count,
            "candidate_rows": rows, "omitted_failure_candidates": failure_count - len(rows),
            "provider_requests": 0, "decision_authority": "none"}
