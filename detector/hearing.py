"""Input projections for independent Judge and Recall hearings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


_TARGET_DEFAULTS = {
    "source": "", "candidate_id": None, "step": None, "reason": "",
    "quote": "", "referent": "", "evidence_refs": [], "evidence_citations": [],
    "delivered_result": "", "correct_result": "", "contracts": [], "confidence": "",
}


def hearing_target(positive: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy({key: positive.get(key, default) for key, default in _TARGET_DEFAULTS.items()})


def judge_target(parsed: Mapping[str, Any]) -> dict[str, Any]:
    root = parsed.get("root_cause") or {}
    return hearing_target({
        "source": "judge", "step": parsed.get("hallucination_step"),
        "reason": parsed.get("hallucination_reason") or "",
        "delivered_result": parsed.get("delivered_result") or "",
        "correct_result": parsed.get("correct_result") or "",
        "evidence_refs": parsed.get("evidence_refs") or [],
        "contracts": parsed.get("contracts") or [], "confidence": parsed.get("confidence") or "",
        "quote": root.get("quote") or "", "referent": root.get("referent") or "",
    })


def recall_target(candidate: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    # Task obligations may cross this boundary; Judge answer/verdict anchors may not.
    return hearing_target({
        "source": "recall_upgrade", "candidate_id": candidate.get("candidate_id"),
        "step": candidate.get("corrected_step"), "reason": candidate.get("claim") or "",
        "evidence_refs": candidate.get("evidence_refs") or [],
        "evidence_citations": candidate.get("evidence_citations") or [],
        "quote": candidate.get("quote") or "", "contracts": list(contracts),
    })
