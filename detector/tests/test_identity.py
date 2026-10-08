from __future__ import annotations

from copy import deepcopy

import pytest

from detector.identity import artifact_digest, build_execution_identity, digest, source_identity
from detector.packet import build_packet


def test_digest_is_canonical_and_rejects_non_json_identity_values():
    assert digest({"a": 1, "b": [2, 3]}) == digest({"b": [2, 3], "a": 1})
    assert digest([2, 3]) != digest([3, 2])
    for value in (float("nan"), float("inf"), {"object": object()}):
        with pytest.raises((TypeError, ValueError)):
            digest(value)


@pytest.mark.parametrize("field", ["source", "config", "record", "packet", "evidence", "registry", "replay"])
def test_execution_identity_binds_each_actual_input(sample_record, field):
    inputs = dict(source="source-a", config={"budget": 1}, record=sample_record,
                  packet=build_packet(sample_record), evidence={"sources": ["a"]},
                  registry={"hooks": ["judge.system"]}, replay={"judge_result": {"ok": True}})
    first = build_execution_identity(**inputs)
    changed = deepcopy(inputs)
    if field == "source":
        changed[field] = "source-b"
    elif field == "record":
        changed[field]["question"] = "A different question"
    elif field == "packet":
        changed[field]["events"][0]["content"] = "different observation"
    else:
        changed[field]["changed"] = True
    assert build_execution_identity(**changed)["digest"] != first["digest"]
    assert build_execution_identity(**inputs) == first


def test_declared_artifact_content_is_bound_and_missing_artifact_is_refused(tmp_path, sample_record):
    artifact = tmp_path / "scorer.bin"
    artifact.write_bytes(b"scorer-one")
    inputs = dict(source="s", config={"causal_audit": {"scorer_artifact": str(artifact)}},
                  record=sample_record, packet=build_packet(sample_record))
    first = build_execution_identity(**inputs)
    artifact.write_bytes(b"scorer-two")
    assert build_execution_identity(**inputs)["digest"] != first["digest"]
    artifact.unlink()
    with pytest.raises(ValueError, match="declared artifact"):
        build_execution_identity(**inputs)


def test_artifact_directory_identity_includes_names_and_contents(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "weights").write_bytes(b"same")
    before = artifact_digest(artifact)
    (artifact / "weights").rename(artifact / "tokenizer")
    assert artifact_digest(artifact) != before


def test_source_identity_excludes_tests_and_documents(tmp_path, monkeypatch):
    import detector.identity as identity

    package = tmp_path / "detector"
    package.mkdir()
    (package / "identity.py").write_text("def rule(): return 1", encoding="utf-8")
    monkeypatch.setattr(identity, "__file__", str(package / "identity.py"))
    first = source_identity()
    (package / "tests").mkdir()
    (package / "tests/test_rule.py").write_text("test change", encoding="utf-8")
    (package / "README.md").write_text("doc change", encoding="utf-8")
    assert source_identity() == first
    (package / "identity.py").write_text("def rule(): return 2", encoding="utf-8")
    assert source_identity() != first
