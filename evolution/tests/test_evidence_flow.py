"""External evidence as a harness input: flow, caching, replay integrity."""

from __future__ import annotations

import json

from detector.evidence import excerpt_sha256
from evolution.backends import (
    CachingBackend,
    LiveDetectorBackend,
    build_evidence_provider,
    evidence_namespace,
)

CASE = "Suite/Case.json"


def _snapshot(value="116", excerpt="The vessel measured 116 tons."):
    return {
        "schema_version": "detector.evidence.v1",
        "query": {"text": "tonnage", "derivation": "task",
                  "task_excerpt": "How many tons?"},
        "sources": [{
            "evidence_id": "E1",
            "url": "https://example.org/x",
            "title": "Record",
            "publisher": "example.org",
            "retrieved_at": "2026-09-18T10:00:00+00:00",
            "excerpt": excerpt,
            "content_sha256": excerpt_sha256(excerpt),
            "verifies": {"entity": "Example", "attribute": "ton",
                         "value": value},
            "applicable_context": "the case's vessel",
        }],
    }


class _RecordingDetector:
    def __init__(self, authority="authoritative"):
        self.calls = []
        self.authority = authority

    def detect(self, record, *, case_id, interventions=None,
               frozen_judge=None, frozen_stages=None, evidence=None):
        self.calls.append({
            "case": case_id,
            "evidence": evidence,
            "frozen_stages": frozen_stages,
        })
        digest = None
        if evidence is not None:
            from detector.evidence import snapshot_digest

            digest = snapshot_digest(evidence)
        return {
            "case_id": case_id,
            "state": "verified_clean",
            "decision_authority": self.authority,
            "proof": {"judge": {"is_hallucination": False}, "deployment": {}},
            "stage_status": {"judge": {"state": "ok", "attempts": 1}},
            "provenance": {
                "packet_hash": "hash",
                **({"evidence_snapshot_digest": digest} if digest else {}),
            },
        }


def _live(detector, provider):
    backend = LiveDetectorBackend.__new__(LiveDetectorBackend)
    backend.detector = detector
    backend._base_results = {}
    backend._evidence_provider = provider
    backend._evidence_cache = {}
    backend.evidence_provider = provider
    return backend


def test_case_evidence_reaches_the_detector():
    detector = _RecordingDetector()
    provider = lambda case_id: _snapshot() if case_id == CASE else None
    backend = _live(detector, provider)
    backend.run_case({"question": "q", "history": [{"step": 1}]}, case_id=CASE)
    assert detector.calls[-1]["evidence"] is not None
    assert detector.calls[-1]["evidence"]["sources"][0]["evidence_id"] == "E1"


def test_no_provider_means_no_evidence_argument_changes():
    detector = _RecordingDetector()
    backend = _live(detector, None)
    backend.run_case({"question": "q", "history": [{"step": 1}]}, case_id=CASE)
    assert detector.calls[-1]["evidence"] is None


def test_a_baseline_recorded_under_other_evidence_is_not_reused():
    """A control recorded without evidence cannot serve an evidence run."""
    detector = _RecordingDetector()
    backend = _live(detector, lambda case_id: _snapshot())
    backend._base_results[CASE] = {
        "state": "verified_clean", "decision_authority": "authoritative",
        "provenance": {"packet_hash": "hash"},  # recorded with no evidence
        "proof": {}, "stage_status": {},
    }
    result = backend.run_case({"question": "q", "history": [{"step": 1}]},
                              case_id=CASE)
    # The stale baseline was dropped and a fresh measurement taken under the
    # active evidence.
    assert len(detector.calls) == 1
    assert detector.calls[0]["evidence"] is not None
    assert result["provenance"]["evidence_snapshot_digest"]


def test_a_baseline_recorded_under_the_same_evidence_is_reused():
    detector = _RecordingDetector()
    snapshot = _snapshot()
    from detector.evidence import snapshot_digest

    digest = snapshot_digest(snapshot)
    backend = _live(detector, lambda case_id: snapshot)
    backend._base_results[CASE] = {
        "state": "verified_clean", "decision_authority": "authoritative",
        "provenance": {"packet_hash": "hash",
                       "evidence_snapshot_digest": digest},
        "proof": {}, "stage_status": {},
    }
    backend.run_case({"question": "q", "history": [{"step": 1}]}, case_id=CASE)
    assert detector.calls == []  # no re-measurement


def test_frozen_envelope_records_the_evidence_digest():
    baseline = {
        "proof": {
            "judge": {"is_hallucination": False},
            "recall_candidates": [],
            "causal_upgrade_rows": [],
        },
        "stage_status": {
            "judge": {"state": "ok"}, "recall_scanner": {"state": "ok"},
            "causal_upgrade": {"state": "ok"},
        },
        "providers": {},
        "provenance": {"packet_hash": "ph", "evidence_snapshot_digest": "d1"},
    }
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    assert frozen["evidence_snapshot_digest"] == "d1"


def test_cache_tag_separates_evidence_variants(tmp_path):
    detector = _RecordingDetector()
    live = _live(detector, None)
    live._evidence_provider = lambda case_id: _snapshot(value="116")
    live._evidence_cache = {}
    cache = CachingBackend(
        live, tmp_path, namespace="ns", extra_key=live.evidence_tag,
    )
    record = {"question": "q", "history": [{"step": 1}]}
    cache.run_case(record, case_id=CASE)
    first = detector.calls[-1]["evidence"]["sources"][0]["verifies"]["value"]

    # Change the evidence: the cached entry must not be reused.
    live._evidence_provider = lambda case_id: _snapshot(
        value="76", excerpt="Another record lists 76 tons.",
    )
    live._evidence_cache = {}
    cache.run_case(record, case_id=CASE)
    second = detector.calls[-1]["evidence"]["sources"][0]["verifies"]["value"]
    assert (first, second) == ("116", "76")
    files = list(cache.cache_dir.rglob("*.json"))
    assert len(files) == 2


def test_provider_from_config_pins_and_corpus(tmp_path, monkeypatch):
    snapshot = _snapshot()
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps(snapshot), encoding="utf-8")
    config = {
        "working_dir": str(tmp_path),
        "evidence": {"cases": {CASE: str(pin)}},
    }
    provider = build_evidence_provider(config)
    loaded = provider(CASE)
    assert loaded["sources"][0]["evidence_id"] == "E1"
    # A case with no pin and no corpus yields no snapshot, not an error.
    assert provider("Other/Case.json") is None
    namespace = evidence_namespace(config, provider)
    assert namespace["enabled"] is True
    assert namespace["cases"] == [CASE]


def test_evidence_absent_from_config_reports_inactive():
    provider = build_evidence_provider({})
    assert provider is None
    assert evidence_namespace({}, None) == {"enabled": False}


def test_corpus_provider_reads_projected_dataset_question_and_invalidates_content(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = corpus / "source.txt"
    document.write_text("The schooner Example carried 116 tons.", encoding="utf-8")
    (corpus / "corpus.json").write_text(json.dumps({
        "schema_version": "detector.corpus.v1", "documents": [{
            "doc_id": "d", "path": "source.txt", "url": "https://example.org/source",
            "title": "Example", "publisher": "Example", "retrieved_at": "2026-09-18T00:00:00+00:00",
        }],
    }), encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "case.json").write_text(json.dumps({
        "question": [["How many tons did the schooner Example carry?"]],
        "history": [{"step": 1, "content": "Some answer"}],
        "true_answer": "SECRET GOLD", "is_hallucination": True,
    }), encoding="utf-8")
    config = {"dataset_root": str(dataset), "evidence": {"corpus": str(corpus)}}
    provider = build_evidence_provider(config)
    snapshot = provider("case.json")
    assert snapshot["sources"][0]["verifies"]["value"] == "116 ton"
    assert "SECRET GOLD" not in json.dumps(snapshot)
    before = evidence_namespace(config, provider)
    document.write_text("The schooner Example carried 200 tons.", encoding="utf-8")
    assert evidence_namespace(config, provider) != before
    assert build_evidence_provider(config)("case.json")["sources"][0]["verifies"]["value"] == "200 ton"


def test_evidence_namespace_tracks_pin_contents(tmp_path):
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps(_snapshot(value="116")), encoding="utf-8")
    config = {"evidence": {"cases": {CASE: str(pin)}}}
    provider = build_evidence_provider(config)
    before = evidence_namespace(config, provider)
    pin.write_text(json.dumps(_snapshot(value="200")), encoding="utf-8")
    assert evidence_namespace(config, provider) != before


def test_provider_returns_none_when_the_corpus_has_no_bearing(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "documents").mkdir(parents=True)
    (corpus / "documents" / "d.txt").write_text(
        "Nothing here concerns the case at hand.\n", encoding="utf-8",
    )
    (corpus / "corpus.json").write_text(json.dumps({
        "schema_version": "detector.corpus.v1",
        "documents": [{
            "doc_id": "d", "path": "documents/d.txt",
            "url": "https://a.example/x", "title": "Doc", "publisher": "a.example",
        }],
    }), encoding="utf-8")
    config = {"evidence": {"corpus": str(corpus)}}
    provider = build_evidence_provider(config)
    assert provider("Other/Case.json") is None


def test_evaluate_arm_carries_evidence_per_case(tmp_path):
    """The arm path the engine uses must attach each case's own snapshot."""
    from evolution.splits import load_record

    dataset = tmp_path / "dataset" / "Suite"
    dataset.mkdir(parents=True)
    (dataset / "Case.json").write_text(json.dumps({
        "question": "How many tons did the Example carry?",
        "history": [{"step": 1, "role": "assistant", "content": "116 tons."}],
        "is_hallucination": False,
    }), encoding="utf-8")
    (dataset / "Other.json").write_text(json.dumps({
        "question": "An unrelated question about birds.",
        "history": [{"step": 1, "role": "assistant", "content": "Sparrows."}],
        "is_hallucination": False,
    }), encoding="utf-8")

    detector = _RecordingDetector()
    provider = lambda case_id: _snapshot() if case_id.endswith("Case.json") else None
    backend = _live(detector, provider)

    rows = backend.evaluate(
        ["Suite/Case.json", "Suite/Other.json"],
        dataset_root=tmp_path / "dataset",
    )
    assert len(rows) == 2
    calls = {call["case"]: call["evidence"] for call in detector.calls}
    assert calls["Suite/Case.json"] is not None
    assert calls["Suite/Other.json"] is None
    # Records reaching the detector stay label-free.
    record = load_record(tmp_path / "dataset", "Suite/Case.json")
    assert set(record) == {"question", "history"}
