"""Regression coverage for the H_t harness manifest.

These tests pin the properties the design depends on: the manifest is derived
from the code that runs, it detects its own tampering, an unchanged generation
diffs empty, and a change is attributed to a component rather than to "the
system".
"""

from __future__ import annotations

import copy

import pytest

from evolution.backends import backend_runtime_identity, MockDetectorBackend
from evolution.config import load_config
from evolution.harness import (
    COMPONENT_KEYS,
    HARNESS_SCHEMA,
    build_manifest,
    component_payload,
    harness_diff,
    load_manifest,
    verify_manifest,
    verify_workflow,
    write_manifest,
)


def _config():
    return load_config()


def _manifest(config=None, *, cycle=1, parent=None, memory_root=None):
    return build_manifest(
        runtime_identity=backend_runtime_identity(MockDetectorBackend()),
        config=config or _config(),
        registry=None,
        memory_root=memory_root,
        cycle=cycle,
        parent_digest=parent,
    )


def test_manifest_uses_full_finite_canonical_digests():
    manifest = _manifest()
    assert len(manifest["digest"]) == 64
    assert all(len(component["digest"]) == 64 for component in manifest["components"].values())
    config = _config()
    config["budget"]["invalid"] = float("nan")
    with pytest.raises(ValueError):
        _manifest(config)


def test_old_harness_schema_cannot_describe_a_new_generation():
    manifest = _manifest()
    manifest["schema_version"] = "evolution.harness.v1"
    with pytest.raises(ValueError, match="schema"):
        verify_manifest(manifest)


def test_manifest_has_all_five_components_and_verifies():
    manifest = _manifest()
    assert manifest["schema_version"] == HARNESS_SCHEMA
    assert set(manifest["components"]) == set(COMPONENT_KEYS)
    assert verify_manifest(manifest)["digest"] == manifest["digest"]


def test_workflow_component_is_derived_from_real_entrypoints():
    """Building G must fail if the detector is reorganised, not go stale."""
    resolved = verify_workflow()
    assert resolved["judge"] == "detector.judge.run_judge"
    assert resolved["adjudicate"] == "detector.adjudication.adjudicate"
    component = component_payload(_manifest(), "G")
    assert component["stages"][0] == "validate"
    assert component["entrypoints"] == resolved
    # The graph is not rewritable today; the manifest must say so rather than
    # leaving the reader to assume it is.
    assert component["execution_graph_rewrite"] is False


def test_context_component_records_live_budgets():
    component = component_payload(_manifest(), "C")
    assert component["budgets"]["_JUDGE_VIEW_CHARS"] == 400_000
    assert set(component["renderers"]) == {
        "render_full_view", "render_index_tail_view",
        "render_judge_view", "render_defense_view",
    }


def test_tools_component_tracks_the_registry():
    from evolution.contracts import make_intervention, make_registry

    entry = make_intervention(
        intervention_id="IV-1-001",
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "evidence_refs_resolve",
            "runner": "deterministic",
            "output_verdicts": ["resolved", "unresolved", "not_applicable"],
        },
        expected_behavior="record the active capability set",
        target_polarity="boundary",
        cycle=1,
        failure_family="FAM-test",
        failure_cases=["A/001.json", "A/002.json"],
    )
    registry = make_registry([entry])
    manifest = build_manifest(
        config=_config(), registry=registry, memory_root=None, cycle=1,
        runtime_identity=backend_runtime_identity(MockDetectorBackend()),
    )
    component = component_payload(manifest, "T")
    assert component["intervention_ids"] == ["IV-1-001"]
    assert manifest["registry_hash"] == registry["content_hash"]


def test_policy_component_reflects_configuration_changes():
    config = _config()
    first = _manifest(config, cycle=1)
    changed = copy.deepcopy(config)
    changed["budget"] = {**config["budget"], "patrol_cases": 7}
    second = _manifest(changed, cycle=2, parent=first["digest"])
    diff = harness_diff(first, second)
    # Only the policy component moved; attributing this to anything else would
    # make a later outcome uninterpretable.
    assert diff["changed"] == ["R"]
    assert "budget" in diff["components"]["R"]["changed_keys"]
    assert diff["components"]["G"]["changed"] is False


def test_identical_generation_diffs_to_nothing():
    config = _config()
    first = _manifest(config, cycle=1)
    second = _manifest(config, cycle=2, parent=first["digest"])
    diff = harness_diff(first, second)
    assert diff["changed"] == []
    assert diff["first_manifest"] is False


def test_first_manifest_diff_marks_every_component_new():
    diff = harness_diff(None, _manifest(cycle=1))
    assert diff["first_manifest"] is True
    assert diff["changed"] == list(COMPONENT_KEYS)
    assert diff["previous_digest"] is None


def test_component_tampering_is_detected():
    manifest = _manifest()
    tampered = copy.deepcopy(manifest)
    tampered["components"]["R"]["payload"]["patrol_registry"] = "bare_base"
    with pytest.raises(ValueError, match="component R digest mismatch"):
        verify_manifest(tampered)


def test_manifest_level_tampering_is_detected():
    manifest = _manifest()
    tampered = copy.deepcopy(manifest)
    tampered["components"]["G"]["digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_manifest(tampered)


def test_unsupported_schema_is_rejected():
    with pytest.raises(ValueError, match="unsupported harness manifest schema"):
        verify_manifest({"schema_version": "evolution.harness.v99"})


def test_manifest_round_trips_through_disk_with_history(tmp_path):
    manifest = _manifest(cycle=1)
    path = tmp_path / "harness.json"
    history = tmp_path / "harness_history.jsonl"
    write_manifest(path, manifest=manifest, history_path=history)
    loaded = load_manifest(path)
    assert loaded["digest"] == manifest["digest"]
    from evolution.contracts import read_jsonl

    records = read_jsonl(history)
    assert len(records) == 1
    assert records[0]["digest"] == manifest["digest"]
    assert set(records[0]["component_digests"]) == set(COMPONENT_KEYS)


def test_write_manifest_refuses_a_tampered_manifest(tmp_path):
    manifest = copy.deepcopy(_manifest())
    manifest["components"]["C"]["payload"]["budgets"]["_JUDGE_VIEW_CHARS"] = 1
    with pytest.raises(ValueError, match="digest mismatch"):
        write_manifest(tmp_path / "harness.json", manifest=manifest)


def test_unknown_component_is_rejected():
    with pytest.raises(ValueError, match="unknown harness component"):
        component_payload(_manifest(), "Z")
