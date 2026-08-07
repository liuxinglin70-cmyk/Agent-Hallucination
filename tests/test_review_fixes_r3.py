"""Regression tests for the third-round deep review fixes (R30-R32).

Each test pins one concrete defect fixed in the review round:

- R30: a raised contrast replay increments an audited error counter, so the
  deliberately fail-open neighbour gate cannot silently stop guarding;
- R31: ``canary_preserved`` reports distinct non-None sentinels when the
  state cannot be resolved, so an audit consumer recomputing stability as
  ``before_state == after_state`` cannot read a failure as preserved;
- R32: an out-of-range proof count degrades to 0 instead of raising
  OverflowError out of the fitness scorer.
"""

from __future__ import annotations

import inspect

from src.sehd_v6.core.bccse import evolution_runner
from src.sehd_v6.core.bccse import fitness


# ============================================================
# R30: contrast replay failures are observable
# ============================================================

def test_r30_budget_usage_registers_contrast_replay_errors() -> None:
    """The counter must exist in the budget template, not just on demand.

    A key created only inside the ``except`` branch would keep the audit
    schema unstable between a clean run and a degraded one.
    """
    source = inspect.getsource(evolution_runner.run_online_evolution)
    assert '"contrast_replay_errors": 0,' in source


def test_r30_contrast_replay_exception_increments_counter() -> None:
    source = inspect.getsource(evolution_runner.run_online_evolution)
    marker = 'budget_usage["contrast_replay_errors"] += 1'
    assert marker in source
    # The increment belongs to the contrast replay handler: it must sit
    # between the replay call and the fail-open audit row, otherwise the
    # neighbour gate's unreliability is still unobservable.
    handler_start = source.index('except Exception as exc:\n                        inverted = False')
    handler_end = source.index('"runtime_error"', handler_start)
    assert handler_start < source.index(marker) < handler_end


def test_r30_contrast_gate_stays_fail_open() -> None:
    """The guard is an overfitting check, not a verdict authority.

    Pinning this keeps a later reviewer from "hardening" the neighbour gate
    into a fail-closed veto that a transient provider outage could use to
    reject every otherwise-admissible edit.
    """
    source = inspect.getsource(evolution_runner.run_online_evolution)
    handler = source[
        source.index('except Exception as exc:\n                        inverted = False'):
    ]
    handler = handler[: handler.index('fitness_audit["contrast_replay"]')]
    assert "inverted = False" in handler
    assert "inverted = True" not in handler


# ============================================================
# R31: unresolved canary states use distinct sentinels
# ============================================================

def test_r31_canary_state_error_avoids_none_equality() -> None:
    source = inspect.getsource(evolution_runner.run_online_evolution)
    start = source.index("def canary_preserved(")
    handler = source[start : source.index("preserved = (", start)]
    assert '"before_state": None' not in handler
    assert '"after_state": None' not in handler
    before = handler.index('"before_state": "')
    after = handler.index('"after_state": "')
    before_value = handler[before:].split('"')[3]
    after_value = handler[after:].split('"')[3]
    # Distinct values: equal sentinels would reintroduce the false-preserved
    # reading that None == None produced.
    assert before_value != after_value
    assert before_value and after_value


# ============================================================
# R32: proof counts never raise out of the scorer
# ============================================================

def test_r32_oversized_proof_count_degrades_to_zero() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": float("inf")}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 0


def test_r32_nan_proof_count_degrades_to_zero() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": float("nan")}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 0


def test_r32_absent_key_still_returns_fallback() -> None:
    """The fallback path must survive the widened except clause."""
    row = {"proof_adjudication": {"other_key": 3}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 7


def test_r32_valid_proof_count_is_unchanged() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": 4}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 4
