"""Unit tests for the multi-threshold decision profile pure functions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "analyze_decision_profile",
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "analyze_decision_profile.py",
)
assert _SPEC is not None and _SPEC.loader is not None
profile_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(profile_module)

verdict_at_operating_point = profile_module.verdict_at_operating_point
flip_band_case_ids = profile_module.flip_band_case_ids


def test_high_crs_with_complete_prosecution_is_error_above_tau() -> None:
    verdict = verdict_at_operating_point(
        0.5,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        tau=0.45,
    )
    assert verdict == "verified_error"


def test_same_case_flips_to_clean_below_tau() -> None:
    """The flip-band core: defense complete, aggregate between two taus."""
    assert verdict_at_operating_point(
        0.35,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="complete",
        tau=0.45,
    ) == "verified_clean"
    assert verdict_at_operating_point(
        0.35,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        tau=0.28,
    ) == "verified_error"


def test_high_crs_without_prosecution_stays_unresolved() -> None:
    """A prosecution-side score alone must never be read as an error proof."""
    verdict = verdict_at_operating_point(
        0.6,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="incomplete",
        tau=0.45,
    )
    assert verdict == "unresolved"


def test_missing_aggregate_is_fail_closed_unresolved() -> None:
    assert (
        verdict_at_operating_point(
            None,
            prosecution_coverage_state="complete",
            defense_coverage_state="complete",
            tau=0.45,
        )
        == "unresolved"
    )


def test_defense_complete_but_low_aggregate_is_clean() -> None:
    verdict = verdict_at_operating_point(
        0.1,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="complete",
        tau=0.28,
    )
    assert verdict == "verified_clean"


def test_quantitative_coverage_is_compared_against_tau() -> None:
    # Quantitative prosecution strong enough for the low operating point...
    assert (
        verdict_at_operating_point(
            0.5,
            prosecution_coverage=0.6,
            defense_coverage=0.1,
            tau=0.45,
        )
        == "verified_error"
    )
    # ...but not for the higher one.
    assert (
        verdict_at_operating_point(
            0.5,
            prosecution_coverage=0.5,
            defense_coverage=0.1,
            tau=0.55,
        )
        == "unresolved"
    )


def test_flip_band_detects_unstable_cases_only() -> None:
    stable_error = {
        "case_id": "BFCL/001",
        "verdicts_by_tau": {"0.28": "verified_error", "0.45": "verified_error"},
    }
    flipping = {
        "case_id": "BFCL/002",
        "verdicts_by_tau": {"0.28": "verified_error", "0.45": "verified_clean"},
    }
    unresolved_band = {
        "case_id": "BFCL/003",
        "verdicts_by_tau": {"0.28": "verified_clean", "0.45": "unresolved"},
    }
    flips = flip_band_case_ids([stable_error, flipping, unresolved_band])
    assert flips == ["BFCL/002", "BFCL/003"]
