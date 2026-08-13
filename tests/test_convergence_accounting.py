"""B3 convergence-accounting acceptance: the no-edit streak counts distinct
source cases, never rejected edits, and streak restores undo the case
identity along with the count."""

from __future__ import annotations

from sehd_rise.evaluation.gate import (
    GateCertificate,
    GateConfig,
    StructuralGate,
)
from sehd_rise.skills.genome import SkillGenome


def _gate(*, window: int = 20) -> StructuralGate:
    return StructuralGate(config=GateConfig(convergence_window=window))


def _reject(gate: StructuralGate, case_id: str, case_index: int) -> GateCertificate:
    accepted, cert, reasons = gate.evaluate(
        case_id=case_id,
        case_index=case_index,
        target_module="atomic_verifier_instruction",
        operation="invalid_operation",
        content="x" * 20,
        preconditions=(),
        current_genome=SkillGenome(genome_id="test", modules={}),
    )
    assert accepted is False
    assert reasons
    return cert


def _accept_cert(case_id: str, *, prior_streak: int, prior_case_id: str) -> GateCertificate:
    return GateCertificate(
        timestamp=0.0,
        case_id=case_id,
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        content_preview="x" * 20,
        decision="accept",
        reject_reasons=[],
        edit_index=1,
        case_index=0,
        prior_consecutive_no_edit=prior_streak,
        prior_converged=False,
        prior_no_edit_case_id=prior_case_id,
    )


def test_rejections_on_one_case_advance_the_streak_once() -> None:
    gate = _gate(window=3)
    for attempt in range(20):
        _reject(gate, "case_A", attempt)
    summary = gate.summary()
    assert summary["consecutive_no_edit"] == 1
    assert summary["convergence_signal"] is False
    _reject(gate, "case_B", 0)
    assert gate.summary()["consecutive_no_edit"] == 2


def test_window_fills_on_distinct_cases_only() -> None:
    gate = _gate(window=3)
    for attempt in range(20):
        _reject(gate, "case_A", attempt)
    for attempt in range(20):
        _reject(gate, "case_B", attempt)
    assert gate.converged is False
    _reject(gate, "case_C", 0)
    assert gate.converged is True
    assert gate.summary()["consecutive_no_edit"] == 3


def test_revoke_then_record_counts_the_terminal_rejection() -> None:
    """The runner order (revoke first, record after) must leave the case
    counted exactly once instead of being erased by the streak restore."""
    gate = _gate()
    gate._consecutive_no_edit = 5
    gate._last_no_edit_case_id = "case_X"
    cert = _accept_cert("case_A", prior_streak=5, prior_case_id="case_X")
    gate.revoke_structural_accept(cert, ["source_worse"])
    gate.record_no_edit("case_A")
    assert gate.summary()["consecutive_no_edit"] == 6
    gate.record_no_edit("case_A")
    assert gate.summary()["consecutive_no_edit"] == 6
    assert gate.summary()["last_no_edit_case_id"] == "case_A"


def test_defer_undoes_attempt_record_and_terminal_counts_once() -> None:
    gate = _gate()
    cert = _reject(gate, "case_A", 0)
    assert gate.summary()["consecutive_no_edit"] == 1
    gate.defer_structural_reject(cert)
    assert gate.summary()["consecutive_no_edit"] == 0
    gate.record_no_edit("case_A")
    assert gate.summary()["consecutive_no_edit"] == 1
    gate.record_no_edit("case_A")
    assert gate.summary()["consecutive_no_edit"] == 1


def test_summary_roundtrip_preserves_recorded_case_identity() -> None:
    gate = _gate()
    gate.record_no_edit("case_A")
    restored = _gate()
    restored.restore_summary(gate.summary())
    restored.record_no_edit("case_A")
    assert restored.summary()["consecutive_no_edit"] == 1
    restored.record_no_edit("case_B")
    assert restored.summary()["consecutive_no_edit"] == 2


def test_convergence_snapshot_roundtrip_carries_case_identity() -> None:
    gate = _gate()
    gate.record_no_edit("case_A")
    snapshot = gate.snapshot_convergence()
    gate.record_no_edit("case_B")
    assert gate.summary()["consecutive_no_edit"] == 2
    gate.restore_convergence(snapshot)
    gate.record_no_edit("case_A")
    assert gate.summary()["consecutive_no_edit"] == 1
