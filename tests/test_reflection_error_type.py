"""Reflection error_type wiring: gold polarity drives the direction contract.

The L1 reflection prompt and the polarity admission pool consume the
classifier's closed error_type vocabulary (``false_positive`` /
``false_negative_clean`` / ``unresolved_should_*``). Before the fix, the
runner's ``_ReflectionSignalView.error_type`` fell back to the causal
``reason_code``, which is NOT in that vocabulary — so the direction action
contract never rendered and the polarity filter never fired for gold-locked
FP/FN cases. These tests lock the token mapping back onto the gold reading.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.causal.signals import project_detection
from sehd_rise.loop.runner import _ReflectionSignalView


def _result(state: str, *, surviving: list[str] | None = None) -> dict:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "proof_adjudication": {
            "state": state,
            "decision_authority": "authoritative",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": list(surviving or []),
            "responsible_event_id": "event_001",
        },
    }


def _view(signal, reason_code: str = "") -> _ReflectionSignalView:
    return _ReflectionSignalView(signal, "evidence_obligation", reason_code)


def test_false_positive_error_type() -> None:
    """gold=false + verified_error -> ``false_positive`` (error_to_clean)."""
    signal = project_detection(
        {**_result("verified_error", surviving=["e1"]), "gold_label": False},
        use_gold=True,
    )
    assert _view(signal, "gold_false_positive").error_type == "false_positive"


def test_false_negative_clean_error_type() -> None:
    """gold=true + verified_clean -> ``false_negative_clean`` (clean_to_error)."""
    signal = project_detection(
        {**_result("verified_clean"), "gold_label": True},
        use_gold=True,
    )
    assert _view(signal, "gold_false_negative_clean").error_type == "false_negative_clean"


def test_unresolved_error_types_follow_gold_polarity() -> None:
    gold_error = project_detection(
        {**_result("unresolved"), "gold_label": True},
        use_gold=True,
    )
    gold_clean = project_detection(
        {**_result("unresolved"), "gold_label": False},
        use_gold=True,
    )
    assert _view(gold_error).error_type == "unresolved_should_error"
    assert _view(gold_clean).error_type == "unresolved_should_clean"


def test_self_supervised_error_type_stays_empty() -> None:
    """Without a gold label the reason_code must never masquerade as a
    polarity token (the direction contract stays open for process repair)."""
    signal = project_detection(_result("verified_error", surviving=["e1"]))
    assert _view(signal, "provider_artifact_missing").error_type == ""


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
