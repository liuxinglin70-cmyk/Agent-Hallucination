"""The big-run protocol's wall-clock envelope must fit its own case envelope.

Why this exists. The measurement envelope and the cycle envelope are two
nested budgets, and only their *ratio* is meaningful. On 2026-09-21 both were
raised for measured reasons (a legitimate deliberation needs more than 300s per
attempt, so the per-case cap went to 10800s), and raising the inner budget
without the outer one would have silently converted a working cycle into a
fail-closed one: every row past the cycle deadline becomes ``unresolved``,
which the report reads as *unmeasured* rather than as a mechanism failure.

That is the specific confusion this file prevents. A cycle runs roughly ten
serial case evaluations (sentinel, gain, audit, patrol, gate arms, reruns), so
a cycle ceiling below a small multiple of the case ceiling cannot finish one.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Serial case evaluations a cycle performs before it can reach a verdict:
# baseline sentinel, baseline gain, one or two audit arms, patrol, the bare
# reference, up to two candidate arms, and the rerun bootstrap.
SERIAL_CASE_EVALUATIONS_PER_CYCLE = 10


def _config(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))


def test_detector_and_evolution_envelopes_are_consistent():
    detector = _config("detector/configs/autodl.json")
    evolution = _config("evolution/configs/validation_big_run.json")
    case_cap = float(detector["case_timeout_seconds"])
    cycle_cap = float(evolution["budget"]["cycle_timeout_seconds"])
    assert cycle_cap >= case_cap * 2, (
        "a cycle ceiling below twice the case ceiling cannot survive one "
        "unlucky case plus its paired arm",
        cycle_cap,
        case_cap,
    )
    # Ten serial case evaluations is the shape of a cycle; a ceiling far below
    # that does not bound the cycle, it truncates it.
    assert cycle_cap >= case_cap * 3


def test_per_attempt_budget_leaves_room_for_a_measured_deliberation():
    detector = _config("detector/configs/autodl.json")
    provider = detector["provider"]
    # Measured: reasoning on one identical prompt ranged 36..13,437 tokens at
    # 20-46 tok/s, so a legitimate call can need ~672s. A per-attempt cap below
    # that kills working calls and retries them from scratch.
    assert float(provider["timeout_seconds"]) >= 672
    assert float(provider["total_timeout_seconds"]) >= 2 * 672
    assert float(provider["total_timeout_seconds"]) > float(provider["timeout_seconds"])


def test_case_envelope_is_not_below_one_call_plus_its_retry():
    # The case cap must at least cover the per-call ceiling times the stages a
    # case can run before it can be cut; below that it is guaranteed to cut.
    detector = _config("detector/configs/autodl.json")
    per_call = float(detector["provider"]["total_timeout_seconds"])
    assert float(detector["case_timeout_seconds"]) >= per_call * 3
