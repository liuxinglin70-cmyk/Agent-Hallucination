from __future__ import annotations

import pytest

from detector_v2.artifacts import (
    ARTIFACT_REGISTRY_SCHEMA,
    compile_deploy_plan,
    registry_hash,
    validate_registry,
)


def _prompt_artifact(artifact_id="a1", stages=None, features=None):
    return {
        "artifact_id": artifact_id,
        "kind": "prompt_directive",
        "trigger": {
            "packet_features": features or ["has_tool_calls"],
            "stages": stages or ["judge"],
        },
        "intervention": {
            "directive": "check the final result",
            "exclusion": "never when formatting only",
        },
    }


def _registry(artifacts):
    payload = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": artifacts,
    }
    payload["content_hash"] = registry_hash(payload)
    return payload


def test_valid_registry_and_routing():
    payload = _registry([_prompt_artifact()])
    artifacts = validate_registry(payload)
    plan = compile_deploy_plan(payload)
    block = plan.prompt_block({"has_tool_calls"}, stage="judge")
    assert "check the final result" in block
    assert "NOT WHEN" in block
    assert plan.prompt_block({"question_has_numbers"}, stage="judge") == ""
    assert plan.prompt_block({"has_tool_calls"}, stage="defense") == ""
    assert len(artifacts) == 1


def test_unknown_kind_rejected():
    payload = _registry([{"artifact_id": "x", "kind": "unknown"}])
    with pytest.raises(ValueError):
        validate_registry(payload)


def test_bad_feature_rejected():
    payload = _registry([_prompt_artifact(features=["not_a_feature"])])
    with pytest.raises(ValueError):
        validate_registry(payload)


def test_hash_mismatch_rejected():
    payload = _registry([_prompt_artifact()])
    payload["content_hash"] = "0" * 64
    with pytest.raises(ValueError):
        validate_registry(payload)
