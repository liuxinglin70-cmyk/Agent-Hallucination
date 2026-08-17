from __future__ import annotations

import json

import pytest

from evolution_v3.ledger import Ledger
from evolution_v3.metrics import binary_metrics, paired_delta
from evolution_v3.registry import ActiveRegistry, LibraryLimitError
from evolution_v3.splits import FinalHoldout

from evolution_v3.tests.test_contracts_gates import _candidate


def test_ledger_is_append_only_and_hash_chained(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("cycle_started", {"cycle": 1})
    ledger.append("admitted", {"cycle": 1, "intervention_id": "IV-1-001"})
    verification = ledger.verify()
    assert verification["valid"] is True
    assert verification["records"] == 2

    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"cycle": 1', '"cycle": 2')
    (tmp_path / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ledger.verify()["valid"] is False


def test_registry_limits_and_snapshots(tmp_path):
    registry = ActiveRegistry(tmp_path)
    candidate = _candidate("IV-1-001")
    registry.write([candidate], cycle=1, reason="test")
    assert len(registry.entries) == 1
    with pytest.raises(LibraryLimitError):
        registry.write([candidate] * 4, cycle=2, reason="too wide")

    # Same hook cannot carry two text_boundary interventions.
    second = _candidate("IV-2-001")
    registry.write(
        [candidate],
        cycle=2,
        reason="rewrite singleton",
        library_cfg={"max_text_boundary_per_hook": 1},
    )
    with pytest.raises(LibraryLimitError):
        registry.write(
            [candidate, second],
            cycle=3,
            reason="two boundaries on one hook",
            library_cfg={"max_text_boundary_per_hook": 1},
        )


def test_paired_delta_counts_only_paired_flips():
    baseline = [
        {"case": "a", "gold": True, "prediction": False, "state": "verified_clean"},
        {"case": "b", "gold": False, "prediction": True, "state": "verified_error"},
        {"case": "c", "gold": True, "prediction": True, "state": "verified_error"},
    ]
    active = [
        {"case": "a", "gold": True, "prediction": True, "state": "verified_error"},
        {"case": "b", "gold": False, "prediction": False, "state": "verified_clean"},
        {"case": "c", "gold": True, "prediction": True, "state": "verified_error"},
    ]
    delta = paired_delta(baseline, active)
    assert delta["marginal_cases"] == 2
    assert delta["new_errors"] == 0
    assert delta["flips"] == {"F->T": 1, "T->F": 1, "T->T": 1}
    assert binary_metrics(active)["tp"] == 2


def test_final_holdout_is_read_once():
    holdout = FinalHoldout([
        {"case_id": "A/001.json", "gold": True},
        {"case_id": "A/002.json", "gold": False},
    ])
    assert holdout.read("final")[0]["case_id"] == "A/001.json"
    with pytest.raises(RuntimeError):
        holdout.read("again")


def test_final_holdout_marker_survives_new_process(tmp_path):
    marker = tmp_path / "marker.json"
    holdout = FinalHoldout(
        [{"case_id": "A/001.json", "gold": True}],
        marker_path=marker,
    )
    holdout.read("final")
    fresh_holdout = FinalHoldout(
        [{"case_id": "A/001.json", "gold": True}],
        marker_path=marker,
    )
    with pytest.raises(RuntimeError):
        fresh_holdout.read("final")
