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



def test_explicit_missing_plan_does_not_use_defaults(tmp_path):
    from experiments.plan import load_plan
    measurement = write(tmp_path, manifest())
    with pytest.raises(FileNotFoundError, match="missing-plan"):
        load_plan(plan_path=tmp_path / "missing-plan.json", measurement_path=measurement)


def test_child_export_preserves_exclusion_roles_and_binds_its_own_content(tmp_path):
    payload = manifest(excluded_unlabeled=["B/004.json"])
    full = load_measurement(write(tmp_path, payload))
    child = full.prefix(1)
    exported = child.to_manifest(gold={child.cases[0]: False})
    output = tmp_path / "child.json"
    output.write_text(json.dumps(exported), encoding="utf-8")
    loaded = load_measurement(output)
    assert loaded.excluded_mini_split == ["A/003.json"]
    assert loaded.excluded_unlabeled == ["B/004.json"]
    assert loaded.parent_manifest_digest == full.manifest_digest
    assert loaded.gold_positive == 0 and loaded.gold_negative == 1
    exported["policy"] = "another sampling rule"
    output.write_text(json.dumps(exported), encoding="utf-8")
    changed = load_measurement(output)
    assert changed.manifest_digest != loaded.manifest_digest
    assert changed.parent_manifest_digest == loaded.parent_manifest_digest
    child.excluded_mini_split.clear()
    child.excluded_unlabeled.clear()
    assert full.excluded == ["A/003.json", "B/004.json"]


@pytest.mark.parametrize("gold", [None, {}, {"A/001.json": None}, {"A/001.json": 1}, {"A/001.json": False, "extra": True}])
def test_export_requires_known_exact_label_counts(tmp_path, gold):
    child = load_measurement(write(tmp_path, manifest())).prefix(1)
    with pytest.raises(ValueError):
        child.to_manifest(gold=gold)
