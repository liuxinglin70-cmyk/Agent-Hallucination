import hashlib
import json

import pytest

from experiments.plan import load_measurement


def manifest(**changes):
    cases = ["A/001.json", "B/002.json"]
    return {"schema_version": "detector.measurement_manifest.v1", "cases": cases,
            "cases_digest": hashlib.sha256("\n".join(cases).encode()).hexdigest(),
            "gold_positive": 1, "gold_negative": 1, "excluded_mini_split": ["A/003.json"],
            "excluded_unlabeled": [], **changes}


def write(tmp_path, payload):
    path = tmp_path / "measurement.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("changes", [
    {"schema_version": "unknown"}, {"cases_digest": "forged"},
    {"cases": ["A/001.json", "A/001.json"]},
    {"cases": ["A/001.json", "A__001.json"]},
    {"cases": ["A/001.json", "a/001.json"]},
    {"excluded_mini_split": ["A/001.json"]},
    {"excluded_unlabeled": ["B/002.json"]},
    {"gold_positive": True}, {"gold_negative": -1}, {"gold_negative": 0},
    {"cases": ["../outside.json"]},
])
def test_invalid_manifest_is_rejected_before_execution(tmp_path, changes):
    if "cases" in changes and "cases_digest" not in changes:
        changes = {**changes, "cases_digest": hashlib.sha256("\n".join(changes["cases"]).encode()).hexdigest()}
    with pytest.raises(ValueError):
        load_measurement(write(tmp_path, manifest(**changes)))


def test_prefix_has_its_own_identity_and_unknown_subset_gold(tmp_path):
    full = load_measurement(write(tmp_path, manifest()))
    subset = full.prefix(1)
    assert subset.digest != full.digest
    assert subset.digest == hashlib.sha256(b"A/001.json").hexdigest()
    assert subset.parent_manifest_digest == full.parent_manifest_digest
    assert subset.gold_positive is None and subset.gold_negative is None
    assert full.prefix(2).gold_positive == 1
    assert full.prefix(0).gold_positive == full.prefix(0).gold_negative == 0


def test_prefix_does_not_share_mutable_case_or_exclusion_lists(tmp_path):
    full = load_measurement(write(tmp_path, manifest()))
    subset = full.prefix(1)
    subset.excluded.clear()
    subset.cases.clear()
    assert full.excluded == ["A/003.json"]
    assert len(full.cases) == 2
