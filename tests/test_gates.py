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
        "schema_version": "sehd2.gate_calibration.v1",
        "test_retest_flip_rate": None,
        "convention_g2": {
            "max_paired_sentinel_loss": 0,
            "max_paired_clean_conviction": 0,
            "max_reproduced_discovery_harm": 0,
            "min_paired_net_gain": 1,
        },
        "policy_cycle": {
            "max_added_fn_vs_baseline": 1, "max_tp_lost": 0,
        },
    }


def test_tracked_calibration_file_loads() -> None:
    path = REPO_ROOT / DEFAULT_CALIBRATION_PATH
    if not path.exists():
        pytest.skip("tracked calibration not present")
    calibration = load_calibration(path)
    assert calibration["test_retest_flip_rate"] is None
    assert calibration["convention_g2"]["min_paired_net_gain"] >= 1


def test_missing_calibration_refuses_to_run(tmp_path) -> None:
    with pytest.raises(ValueError, match="missing"):
        load_calibration(tmp_path / "absent.json")


def test_schema_and_field_checks() -> None:
    validate_calibration(_base())
    with pytest.raises(ValueError, match="schema"):
        validate_calibration({**_base(), "schema_version": "x"})
    with pytest.raises(ValueError, match="test_retest_flip_rate"):
        validate_calibration({**_base(), "test_retest_flip_rate": 1.5})
    with pytest.raises(ValueError, match="lacks a"):
        validate_calibration({
            key: value for key, value in _base().items()
            if key != "policy_cycle"
        })
    block = dict(_base()["policy_cycle"], max_tp_lost=-1)
    with pytest.raises(ValueError, match="max_tp_lost"):
        validate_calibration({**_base(), "policy_cycle": block})


def test_round_trip_through_json(tmp_path) -> None:
    payload = {**_base(), "test_retest_flip_rate": 0.12}
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_calibration(path)["test_retest_flip_rate"] == 0.12
