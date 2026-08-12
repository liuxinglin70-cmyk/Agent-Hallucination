"""Regression: ProvisionalEdit.source_signal must be a DetectionSignal.

A provisional edit is flushed through the shadow batch, whose
``_expected_effect_audit`` -> ``observable_process_delta`` reads
``signal.prosecution_coverage`` / ``signal.defense_coverage`` as
``CoverageReading`` objects (``.score`` / ``.state``). The runner once
stored the reflection VIEW (``_ReflectionSignalView``) there instead, whose
``prosecution_coverage`` property is a plain float; the first gold-assisted
``strong_improvement`` then crashed the batch commit with
``AttributeError: 'float' object has no attribute 'score'``.

This test locks the type contract on both sides of the boundary so the two
shapes can never be swapped again.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.causal.signals import CoverageReading, project_detection
from sehd_rise.loop.runner import _ReflectionSignalView


def _result(state: str) -> dict:
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
            "surviving_error_certificate_ids": [],
            "responsible_event_id": "event_001",
        },
    }


def test_detection_signal_coverage_is_coverage_reading() -> None:
    """ProvisionalEdit.source_signal (a DetectionSignal) exposes the
    CoverageReading shape observable_process_delta requires."""
    signal = project_detection(_result("verified_clean"))
    assert isinstance(signal.prosecution_coverage, CoverageReading)
    assert isinstance(signal.defense_coverage, CoverageReading)
    assert signal.prosecution_coverage.score is not None


def test_reflection_view_coverage_is_float_and_must_not_cross() -> None:
    """The reflection VIEW flattens coverage to a float — the exact shape
    that crashes the shadow batch, proving the two must never be swapped."""
    signal = project_detection(
        {**_result("verified_error"), "gold_label": False},
        use_gold=True,
    )
    view = _ReflectionSignalView(signal, "evidence_obligation", "gold_false_positive")
    assert isinstance(view.prosecution_coverage, float)
    # The view is a prompt-facing projection, not a DetectionSignal: it has
    # no CoverageReading to feed observable_process_delta.
    assert not hasattr(view.prosecution_coverage, "score")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
