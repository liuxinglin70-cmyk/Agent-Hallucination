"""Execution evidence for one edit on its source replay (plan B).

The L1 Skill layer executes deterministically as post-court transforms, so
the provider no longer self-reports ``rule_effects`` / ``applied_rule_ids``.
The compiled/fired state of a rule is read from the executor's
``l1_execution`` record on the replay result instead — the same deterministic
execution surface the effect layer consumes.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise.skills.genome import SkillEdit
from sehd_rise.skills.binding import (
    BINDING_AMBIGUOUS,
    BINDING_DOMAIN_MISMATCH,
    BINDING_NOT_FOUND,
)
from sehd_rise.detection.continuation import (
    CONSTRUCTED_CANDIDATE_DUPLICATE,
    CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE,
    CONSTRUCTED_CANDIDATE_NOT_QUALIFIED,
    CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
    CONSTRUCTION_ANCHOR_MISMATCH,
    CONSTRUCTION_NO_CANDIDATE,
)


def _injected_applied_flags(
    edit: SkillEdit,
    candidate_result: Mapping[str, Any] | None,
) -> tuple[bool, bool]:
    """Execution evidence for one edit on its source replay.

    ``injected`` = the rule compiled into the deterministic execution set;
    ``applied`` = its transform fired on the court result. Both are read from
    the executor's ``l1_execution`` record, never from provider self-report.
    """
    candidate_result = candidate_result or {}
    execution = candidate_result.get("l1_execution")
    if not isinstance(execution, Mapping):
        return False, False
    target_module = str(edit.target_module or "")
    target_operation = str((edit.action_spec or {}).get("operation") or "")
    if not target_operation:
        return False, False

    def _matches(row: Any) -> bool:
        return (
            isinstance(row, Mapping)
            and str(row.get("module_id") or "") == target_module
            and str(row.get("operation") or "") == target_operation
        )

    fired = any(_matches(row) for row in execution.get("fired") or ())
    skipped = any(_matches(row) for row in execution.get("skipped") or ())
    # compiled = the rule reached the executor (fired or skipped); applied =
    # its transform actually fired.
    return (fired or skipped), fired


def binding_failure_for_edit(
    edit: SkillEdit,
    candidate_result: Mapping[str, Any] | None,
) -> tuple[str, Mapping[str, Any]]:
    """Return the edit's binding failure before fitness attribution."""
    execution = (candidate_result or {}).get("l1_execution")
    if not isinstance(execution, Mapping):
        return "", {}
    target_module = str(edit.target_module or "")
    target_operation = str((edit.action_spec or {}).get("operation") or "")
    statuses = {
        BINDING_NOT_FOUND, BINDING_AMBIGUOUS, BINDING_DOMAIN_MISMATCH,
    }
    for row in execution.get("skipped") or ():
        if not isinstance(row, Mapping):
            continue
        if (
            str(row.get("module_id") or "") == target_module
            and str(row.get("operation") or "") == target_operation
            and str(row.get("binding_status") or "") in statuses
        ):
            return str(row.get("binding_status")), dict(row)
    return "", {}


def construction_failure_for_edit(
    edit: SkillEdit,
    candidate_result: Mapping[str, Any] | None,
) -> tuple[str, Mapping[str, Any]]:
    """Return a layered constructive failure before generic source fitness."""
    execution = (candidate_result or {}).get("l1_execution")
    if not isinstance(execution, Mapping):
        return "", {}
    target_module = str(edit.target_module or "")
    target_operation = str((edit.action_spec or {}).get("operation") or "")
    statuses = {
        CONSTRUCTION_NO_CANDIDATE, CONSTRUCTION_ANCHOR_MISMATCH,
        CONSTRUCTED_CANDIDATE_DUPLICATE,
        CONSTRUCTED_CANDIDATE_NOT_QUALIFIED,
        CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
        CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE,
    }
    for bucket in ("skipped", "fired"):
        for row in execution.get(bucket) or ():
            if not isinstance(row, Mapping):
                continue
            status = str(row.get("construction_status") or "")
            if (
                str(row.get("module_id") or "") == target_module
                and str(row.get("operation") or "") == target_operation
                and status in statuses
            ):
                return status, dict(row)
    return "", {}


__all__ = [
    "_injected_applied_flags", "binding_failure_for_edit",
    "construction_failure_for_edit",
]
