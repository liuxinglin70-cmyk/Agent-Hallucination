from __future__ import annotations

import json

import pytest

from evolution.ledger import Ledger
from evolution.metrics import binary_metrics, paired_delta
from evolution.registry import ActiveRegistry, LibraryLimitError
from evolution.splits import FinalHoldout, _final_cases, bool_gold

from evolution.tests.test_contracts_gates import _candidate


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


def test_paired_delta_counts_unresolved_to_wrong_as_new_error():
    baseline = [{"case": "a", "gold": True, "prediction": None}]
    active = [{"case": "a", "gold": True, "prediction": False}]
    delta = paired_delta(baseline, active)
    assert delta["flips"] == {"U->F": 1}
    assert delta["new_fn"] == 1
    assert delta["new_errors"] == 1


def test_paired_delta_rejects_duplicate_or_missing_cases():
    baseline = [{"case": "a", "gold": True, "prediction": False}]
    with pytest.raises(ValueError, match="duplicate active"):
        paired_delta(
            baseline,
            [
                {"case": "a", "gold": True, "prediction": True},
                {"case": "a", "gold": True, "prediction": True},
            ],
        )
    with pytest.raises(ValueError, match="missing active"):
        paired_delta(
            baseline + [{"case": "b", "gold": False, "prediction": False}],
            [{"case": "a", "gold": True, "prediction": True}],
        )


def test_final_holdout_is_read_once():
    holdout = FinalHoldout([
        {"case_id": "A/001.json", "gold": True},
        {"case_id": "A/002.json", "gold": False},
    ])
    assert holdout.read("final")[0]["case_id"] == "A/001.json"
    with pytest.raises(RuntimeError):
        holdout.read("again")


def test_final_cases_accepts_legacy_case_records():
    rows = _final_cases({
        "final_verification": {
            "cases": [
                {"case_id": "A/001.json", "framework": "A", "gold": True},
                {"case_id": "A/002.json", "framework": "A", "gold": False},
            ],
        },
    })
    assert rows == [
        {"case_id": "A/001.json", "gold": True},
        {"case_id": "A/002.json", "gold": False},
    ]


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


def test_final_holdout_consumes_before_returning_labels(tmp_path):
    marker = tmp_path / "marker.json"
    holdout = FinalHoldout(
        [{"case_id": "A/001.json", "gold": True}], marker_path=marker,
    )
    assert holdout.read("final")
    assert marker.exists() is True
    assert holdout.reads == 1
    # Without a marker path, the in-process read-once state still applies.
    with pytest.raises(RuntimeError):
        holdout.read("again")


@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), ("true", True), ("FALSE", False), (None, None),
])
def test_bool_gold_accepts_only_explicit_labels(value, expected):
    assert bool_gold(value) is expected


def test_bool_gold_rejects_unknown_string():
    with pytest.raises(ValueError, match="invalid is_hallucination"):
        bool_gold("unknown")


def test_net_contribution_is_idempotent_per_cycle(tmp_path):
    """A retried cycle overwrites its own record instead of appending."""
    registry = ActiveRegistry(tmp_path / "registry")
    registry.record_net_contribution(3, cycle=1)
    registry.record_net_contribution(-2, cycle=2)
    registry.record_net_contribution(0, cycle=2)  # cycle 2 rerun
    history = registry.state["net_contribution_history"]
    assert history == [
        {"cycle": 1, "net_correct_cases": 3},
        {"cycle": 2, "net_correct_cases": 0},
    ]


def test_registry_rollback_restores_governance_state(tmp_path):
    registry = ActiveRegistry(tmp_path / "registry")
    first = _candidate("IV-1-001")
    registry.write([first], cycle=1, reason="first")
    registry.record_marginal("IV-1-001", 2, cycle=1, fired_rate=1.0)
    second = _candidate("IV-2-001")
    registry.write(
        [first, second], cycle=2, reason="second",
        library_cfg={"max_text_boundary_per_hook": 2},
    )
    registry.record_marginal("IV-1-001", -3, cycle=2, fired_rate=1.0)
    registry.record_marginal("IV-2-001", -3, cycle=2, fired_rate=1.0)
    registry.record_net_contribution(-2, cycle=2)

    registry.rollback_to_snapshot(1)
    assert [item["intervention_id"] for item in registry.entries] == ["IV-1-001"]
    state = registry.state
    assert state["cycle"] == 1
    assert state["net_contribution_history"] == []
    assert state["interventions"]["IV-1-001"]["marginal_history"] == [{
        "cycle": 1, "marginal_cases": 2, "fired_rate": 1.0,
    }]
    assert state["interventions"]["IV-2-001"]["rolled_back_out"] is True
