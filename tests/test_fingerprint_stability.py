"""T2.6 drift-tolerant attribution: claim-level fingerprint stability.

Design D3 rule 1: edge identity is compared by claim-level content
fingerprints, never positional certificate ids. A pure provider
re-enumeration (same claims, different order and re-minted positional ids)
must read as zero movement; a genuine claim removal must read as a withhold
independent of enumeration order.
"""

from __future__ import annotations

from sehd_rise.detection.court.identity import (
    certificate_carrier_movement,
    certificate_claim_fingerprint,
    surviving_carrier_fingerprints,
)


def _certificate(certificate_id: str, breach_type: str, origin: str) -> dict:
    return {
        "certificate_id": certificate_id,
        "origin_event_id": origin,
        "breach_type": breach_type,
        "claim_kind": "unsupported_claim",
        "norm_source": "tool_result",
        "impact_kind": "incorrect_result",
    }


def _result(surviving_ids: list[str], rows: list[dict]) -> dict:
    return {
        "combined_prosecution": {"candidates": rows},
        "proof_adjudication": {
            "surviving_error_certificate_ids": surviving_ids,
        },
    }


def test_fingerprint_is_stable_across_re_enumerations() -> None:
    a = _certificate("error_001", "omission", "event_1")
    b = _certificate("error_002", "contradiction", "event_2")
    assert certificate_claim_fingerprint(a) == certificate_claim_fingerprint(
        {"certificate_id": "renamed_999", **{
            k: v for k, v in a.items() if k != "certificate_id"
        }}
    )
    assert certificate_claim_fingerprint(a) != certificate_claim_fingerprint(b)


def test_pure_re_enumeration_is_zero_movement() -> None:
    before = _result(
        ["error_001", "error_002"],
        [
            _certificate("error_001", "omission", "event_1"),
            _certificate("error_002", "contradiction", "event_2"),
        ],
    )
    after = _result(
        ["error_002", "error_001"],
        [
            _certificate("error_002", "contradiction", "event_2"),
            _certificate("error_001", "omission", "event_1"),
        ],
    )
    movement = certificate_carrier_movement(before, after)
    assert movement is not None
    assert movement["resolved"] is True
    assert movement["withheld"] == frozenset()
    assert movement["emitted"] == frozenset()


def test_genuine_removal_is_order_independent_withhold() -> None:
    common = [
        _certificate("error_001", "omission", "event_1"),
        _certificate("error_002", "contradiction", "event_2"),
    ]
    before = _result(["error_001", "error_002"], common)
    after = _result(
        ["error_002"],
        [_certificate("error_002", "contradiction", "event_2")],
    )
    movement = certificate_carrier_movement(before, after)
    assert movement is not None
    assert movement["resolved"] is True
    assert len(movement["withheld"]) == 1
    withheld = next(iter(movement["withheld"]))
    assert withheld == certificate_claim_fingerprint(
        _certificate("error_001", "omission", "event_1")
    )
    assert movement["emitted"] == frozenset()


def test_unresolved_identity_fails_closed() -> None:
    # A surviving id with no matching row contributes an opaque positional
    # token, which must mark the movement unresolved so a strict consumer
    # never reads a rename as claim movement.
    before = _result(
        ["error_001"],
        [_certificate("error_001", "omission", "event_1")],
    )
    after = _result(["error_ghost"], [])
    movement = certificate_carrier_movement(before, after)
    assert movement is not None
    assert movement["resolved"] is False
    assert surviving_carrier_fingerprints(after) == frozenset({"id:error_ghost"})
