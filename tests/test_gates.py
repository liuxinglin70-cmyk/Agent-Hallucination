"""Gate calibration: single source, fail-closed loading, closed schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sehd_rise2.evolution.gates import (
    DEFAULT_CALIBRATION_PATH,
    load_calibration,
    validate_calibration,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _base() -> dict:
    return {
        "schema_version": "sehd2.gate_calibration.v2",
        "composition": {"max_interaction_loss": 0},
        "capability_proposal": {"predicate_runners": ["deterministic"]},
    }


def test_tracked_calibration_file_loads() -> None:
    path = REPO_ROOT / DEFAULT_CALIBRATION_PATH
    if not path.exists():
        pytest.skip("tracked calibration not present")
    calibration = load_calibration(path)
    assert calibration["composition"]["max_interaction_loss"] >= 0
    assert calibration["capability_proposal"]["predicate_runners"]


def test_missing_calibration_refuses_to_run(tmp_path) -> None:
    with pytest.raises(ValueError, match="missing"):
        load_calibration(tmp_path / "absent.json")


def test_schema_and_field_checks() -> None:
    validate_calibration(_base())
    with pytest.raises(ValueError, match="schema"):
        validate_calibration({**_base(), "schema_version": "x"})
    with pytest.raises(ValueError, match="lacks a"):
        validate_calibration({
            key: value for key, value in _base().items()
            if key != "composition"
        })
    block = dict(_base()["composition"], max_interaction_loss=-1)
    with pytest.raises(ValueError, match="max_interaction_loss"):
        validate_calibration({**_base(), "composition": block})


def test_composition_and_runner_sections() -> None:
    missing = {
        key: value for key, value in _base().items()
        if key != "composition"
    }
    with pytest.raises(ValueError, match="lacks a composition"):
        validate_calibration(missing)
    with pytest.raises(ValueError, match="non-empty list"):
        validate_calibration({
            **_base(), "capability_proposal": {"predicate_runners": []},
        })
    with pytest.raises(ValueError, match="outside the closed vocabulary"):
        validate_calibration({
            **_base(),
            "capability_proposal": {"predicate_runners": ["freeform"]},
        })
    validate_calibration({
        **_base(),
        "capability_proposal": {
            "predicate_runners": ["deterministic", "llm_structured"],
        },
    })


def test_round_trip_through_json(tmp_path) -> None:
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert load_calibration(path)["schema_version"] == (
        "sehd2.gate_calibration.v2"
    )
