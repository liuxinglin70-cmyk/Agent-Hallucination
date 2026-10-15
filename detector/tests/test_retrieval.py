"""Mechanical, label-free retrieval: extraction rules and fail-safe behaviour."""

from __future__ import annotations

import json

import pytest

from detector.retrieval import (
    CORPUS_SCHEMA,
    CorpusError,
    best_passage,
    build_case_snapshot,
    case_entity,
    derive_query,
    extract_verifies,
    load_corpus,
    score_documents,
    tokenize,
)
from detector.evidence import evidence_ids, validate_snapshot


BOOK = """\
CHAPTER VII

Even a private yacht was taken up for this work in June. This was the 116-ton
topsail schooner Lisette, which had formerly belonged to the Duke of
Sutherland. She had been built as far back as 1873 with a standing bowsprit.

In April 1917 the auxiliary schooner Sidney began service as a decoy.
"""

CATALOGUE = """\
The 76 ton schooner 'Lisette' under sail. 'Lisette' was built at Cowes in 1873.
"""

LISTING = """\
release: Jet Set Radio Original Sound Tracks
date: 2000-12-20
media[0]/tracks[11]/position: 12
media[0]/tracks[11]/recording/title: Funky Radio
media[0]/tracks[11]/length: 207866
media[0]/tracks[12]/position: 13
media[0]/tracks[12]/recording/title: Moody's Shuffle
"""


def _corpus(tmp_path, documents):
    root = tmp_path / "corpus"
    (root / "documents").mkdir(parents=True)
    manifest = {"schema_version": CORPUS_SCHEMA, "documents": []}
    for index, spec in enumerate(documents):
        name = f"doc{index}.txt"
        (root / "documents" / name).write_text(spec["text"], encoding="utf-8")
        manifest["documents"].append({
            "doc_id": spec["doc_id"],
            "path": f"documents/{name}",
            "url": spec["url"],
            "title": spec["title"],
            "publisher": spec["publisher"],
            "retrieved_at": "2026-09-18T10:00:00+00:00",
            "source_kind": spec.get("source_kind", "text"),
            "derivation": spec.get("derivation", ""),
        })
    (root / "corpus.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
    )
    return root


def test_tokenizer_splits_hyphens_and_stems_plurals():
    assert tokenize("116-ton schooner; tons!") == ["116", "ton", "schooner", "ton"]


def test_query_derivation_drops_question_words():
    terms = derive_query("How many tons was the topsail schooner Lisette?")
    assert "ton" in terms and "schooner" in terms and "lisette" in terms
    assert "how" not in terms and "many" not in terms and "the" not in terms


def test_case_entity_prefers_the_quoted_name():
    assert case_entity('How many tons was the "Lisette", built in 1873?') == "Lisette"
    assert case_entity("What's the name of the 'Kashmir' album?") == "Kashmir"
    assert case_entity("What’s the name of the ‘Kashmir’ album?") == "Kashmir"
    assert case_entity("Who was the first cardiologist in Kashmir?") in (
        "Kashmir", "Cardiologist Kashmir",
    )


def test_extract_verifies_refuses_an_unstated_attribute():
    verifies, reason = extract_verifies(
        "What is the name of track 12 on the 'Kashmir' album?",
        "Kashmir: Northern Lights", "Kashmir",
    )
    assert verifies is None
    assert "asked property" in reason


def test_best_passage_prefers_the_paragraph_stating_the_asked_number():
    text = LISTING
    passage, _ = best_passage(
        text, derive_query("track 12 on the release?"),
        "What is the name of track 12 on the release?",
    )
    assert "Funky Radio" in passage
    assert "Moody" not in passage


def test_extract_verifies_reads_a_number_with_its_unit():
    verifies, reason = extract_verifies(
        "How many tons was the Lisette?", BOOK, "Q-Ships",
    )
    assert reason == ""
    assert verifies["entity"] == "Lisette"
    assert verifies["attribute"] == "ton"
    assert verifies["value"].startswith("116")


def test_extract_verifies_reads_a_name_from_a_listing():
    # The question must name its subject: a question with no identifiable
    # subject abstains rather than guessing what to look up.
    verifies, reason = extract_verifies(
        "What is the name of track 12 on the Jet Set Radio soundtrack?",
        "media[0]/tracks[11]/position: 12\n"
        "media[0]/tracks[11]/recording/title: Funky Radio\n",
        "Jet Set Radio Original Sound Tracks",
    )
    assert reason == ""
    assert verifies["attribute"] == "track"
    assert verifies["value"] == "Funky Radio"


def test_extract_verifies_refuses_a_passage_about_another_subject():
    verifies, reason = extract_verifies(
        "How many tons was the Lisette?",
        "The album was recorded in 2000 and reissued in 2011.",
        "Unrelated album notes",
    )
    assert verifies is None
    assert "does not mention the case entity" in reason


def test_extract_verifies_refuses_a_question_with_no_named_subject():
    verifies, reason = extract_verifies(
        "How many tons did the vessel displace?", BOOK, "Q-Ships",
    )
    assert verifies is None
    assert "names no subject" in reason


def test_conflicting_sources_are_resolved_only_by_task_descriptors(tmp_path):
    root = _corpus(tmp_path, [
        {
            "doc_id": "book", "url": "https://a.example/x",
            "title": "Q-Ships", "publisher": "a.example", "text": BOOK,
        },
        {
            "doc_id": "catalogue", "url": "https://b.example/y",
            "title": "Photo catalogue", "publisher": "b.example",
            "text": CATALOGUE,
        },
    ])
    snapshot, report = build_case_snapshot(
        load_corpus(root),
        'How many tons was the topsail schooner "Lisette," built in 1873, '
        "which the Duke of Sutherland had formerly owned before she began "
        "working as a decoy craft in 1917?",
    )
    assert snapshot is not None
    assert evidence_ids(snapshot) == {"E1", "E2"}
    resolutions = snapshot["resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["resolved_by"] == "E1"
    # The resolution is a statement about applicability, not a vote count.
    assert "descriptors" in resolutions[0]["reason"]


def test_a_tie_between_sources_stays_unresolved(tmp_path):
    tie = ("The USS Example measured 10 tons. She was built in 1900.\n")
    root = _corpus(tmp_path, [
        {"doc_id": "a", "url": "https://a.example/1", "title": "A",
         "publisher": "a.example", "text": tie},
        {"doc_id": "b", "url": "https://b.example/2", "title": "B",
         "publisher": "b.example",
         "text": tie.replace("10 tons", "20 tons")},
    ])
    snapshot, _ = build_case_snapshot(
        load_corpus(root), "How many tons was the USS Example?",
    )
    assert snapshot is not None
    assert "resolutions" not in snapshot


def test_retrieval_is_deterministic(tmp_path):
    root = _corpus(tmp_path, [
        {"doc_id": "book", "url": "https://a.example/x", "title": "Q-Ships",
         "publisher": "a.example", "text": BOOK},
    ])
    corpus = load_corpus(root)
    question = "How many tons was the topsail schooner Lisette?"
    first, _ = build_case_snapshot(corpus, question)
    second, _ = build_case_snapshot(corpus, question)
    assert first == second


def test_no_covering_document_returns_no_snapshot_and_a_reason(tmp_path):
    root = _corpus(tmp_path, [
        {"doc_id": "book", "url": "https://a.example/x", "title": "Q-Ships",
         "publisher": "a.example", "text": BOOK},
    ])
    snapshot, report = build_case_snapshot(
        load_corpus(root),
        "What is the boiling point of mercury in degrees Celsius?",
    )
    assert snapshot is None
    assert report["outcome"]
    assert "outcome" in report


def test_thousands_separators_are_read(tmp_path):
    passage = "The SS Example displaced 1,250 tons and served until 1917.\n"
    root = _corpus(tmp_path, [
        {"doc_id": "a", "url": "https://a.example/x", "title": "A",
         "publisher": "a.example", "text": passage},
    ])
    snapshot, _ = build_case_snapshot(
        load_corpus(root), "How many tons did the SS Example displace?",
    )
    assert snapshot is not None
    assert snapshot["sources"][0]["verifies"]["value"] == "1250 ton"


def test_corpus_manifest_rejects_paths_outside_the_directory(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "corpus.json").write_text(json.dumps({
        "schema_version": CORPUS_SCHEMA,
        "documents": [{
            "doc_id": "escape", "path": "../outside.txt",
            "url": "https://a.example/x", "title": "T", "publisher": "a.example",
        }],
    }), encoding="utf-8")
    with pytest.raises(CorpusError, match="stay inside the corpus|escapes"):
        load_corpus(root)


def test_scoring_returns_nothing_below_the_term_floor():
    documents = [{"doc_id": "d", "text": "an unrelated paragraph about birds"}]
    assert score_documents(["schooner", "lisette"], documents) == []


def test_generated_snapshot_satisfies_the_evidence_schema(tmp_path):
    root = _corpus(tmp_path, [
        {"doc_id": "book", "url": "https://a.example/x", "title": "Q-Ships",
         "publisher": "a.example", "text": BOOK},
    ])
    snapshot, _ = build_case_snapshot(
        load_corpus(root), "How many tons was the Lisette?",
    )
    assert snapshot is not None
    normalized = validate_snapshot(snapshot)
    assert normalized["query"]["derivation"] == "task"
    assert normalized["sources"][0]["excerpt"]
