"""Run identity: the detector fingerprint covers the verdict surface.

Two runs with identical configs but different prompt code are NOT the
same detector — the rework-era runs shared a config fingerprint across
incompatible prompt surfaces, so cross-run comparisons could not tell a
code change from provider churn.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sehd_rise2.detector.runs as runs_module  # noqa: E402


def test_fingerprint_stable_for_same_surface() -> None:
    assert (
        runs_module.detector_fingerprint({"model": "m"})
        == runs_module.detector_fingerprint({"model": "m"})
    )


def test_config_change_changes_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runs_module, "_VERDICT_SURFACE_MODULES", ("court.py",),
    )
    assert (
        runs_module.detector_fingerprint({"model": "a"})
        != runs_module.detector_fingerprint({"model": "b"})
    )


def test_prompt_surface_change_changes_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fake = tmp_path / "court.py"
    fake.write_text("PROMPT_A", encoding="utf-8")
    monkeypatch.setattr(
        runs_module, "_VERDICT_SURFACE_MODULES", (str(fake),),
    )
    before = runs_module.detector_fingerprint({"model": "m"})
    fake.write_text("PROMPT_B", encoding="utf-8")
    after = runs_module.detector_fingerprint({"model": "m"})
    assert before != after
