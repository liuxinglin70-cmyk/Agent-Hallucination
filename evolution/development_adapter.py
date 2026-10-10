"""Independent proposal and revision snapshots for the development loop."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from evolution.proposer import ProposalBatch


def feedback_delta(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    keys = (
        "marginal_cases", "new_fn", "new_fp", "new_errors", "unresolved_added",
        "health_regressed", "correctness_improved", "correctness_worsened",
    )
    return {key: int(value.get(key) or 0) for key in keys}


def make_development_drafter(
    *, proposer: Any, cycle: int, family: Mapping[str, Any], mechanism_id: str, identifier: str,
) -> Callable[..., ProposalBatch]:
    family_snapshot = deepcopy(dict(family))

    def renamed(batch: ProposalBatch) -> ProposalBatch:
        result = deepcopy(batch)
        for candidate in result.candidates:
            # Every developed mechanism needs a distinct id before its version is frozen.
            candidate["intervention_id"] = identifier
            candidate.pop("content_hash", None)
        return result

    def draft(mechanism, _plan, parent, diagnosis, _cycle):
        one = deepcopy({**family_snapshot, "mechanism": mechanism})
        if parent is None or not diagnosis:
            return renamed(proposer.propose([one], cycle=int(cycle), max_proposals=1))
        previous = deepcopy(getattr(proposer, "rejection_feedback", []))
        l1 = diagnosis.get("l1") or {}
        targets = [str(case) for case in mechanism.get("member_cases") or ()]
        neighbors = [str(case) for case in mechanism.get("normal_neighbors") or ()]
        aliases = {
            **{case: f"target_{index + 1}" for index, case in enumerate(targets)},
            **{case: f"normal_{index + 1}" for index, case in enumerate(neighbors)},
        }

        def case_refs(key):
            return [aliases.get(str(case), "unlisted_case") for case in l1.get(key) or ()]

        case_evidence = []
        for row in (l1.get("delta") or {}).get("rows") or ():
            case = str(row.get("case") or "")
            if case not in aliases:
                continue
            case_evidence.append({
                "member": aliases[case], "role": "target" if case in targets else "normal",
                "baseline_prediction": row.get("baseline_prediction") if type(row.get("baseline_prediction")) is bool else None,
                "active_prediction": row.get("active_prediction") if type(row.get("active_prediction")) is bool else None,
                "improved": case in (l1.get("target_improved") or ()),
                "regressed": case in (l1.get("target_regressed") or ()) or case in (l1.get("neighbor_regressed") or ()),
                "fired": case in (l1.get("fired_cases") or ()),
            })
        feedback = deepcopy(previous)
        feedback.append({
            "gate": "DEVELOPMENT", "type": parent.get("type"), "hook": parent.get("hook"),
            "candidate": deepcopy(dict(parent)),
            "parent_program": deepcopy({key: parent.get(key) for key in ("type", "hook", "trigger", "body", "claim")}),
            "development_diagnosis": {
                "version_id": diagnosis.get("version_id"),
                **{key: case_refs(key) for key in ("target_improved", "target_unchanged", "target_regressed", "neighbor_regressed", "fired_cases")},
                "reason_codes": deepcopy(list(l1.get("reason_codes") or ())), "case_evidence": case_evidence,
            },
            "delta": feedback_delta(l1.get("delta")),
            "reason": "; ".join(str(item) for item in diagnosis.get("reasons") or ())
            or f"{mechanism_id} version {diagnosis.get('version_id')} failed development",
            "not_evaluable": bool(l1.get("unmeasurable")),
            "evaluation_state": "unmeasurable" if l1.get("unmeasurable") else "measured" if l1 else "not_run",
        })
        setter = getattr(proposer, "set_rejection_feedback", None)
        try:
            if callable(setter):
                setter(feedback)
            return renamed(proposer.propose([one], cycle=int(cycle), max_proposals=1))
        finally:
            if callable(setter):
                setter(previous)

    return draft
