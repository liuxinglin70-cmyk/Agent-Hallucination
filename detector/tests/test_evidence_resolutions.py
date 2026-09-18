"""Recorded conflict resolutions: one settled input for every stage."""

from __future__ import annotations

import pytest

from detector.evidence import (
    EVIDENCE_SCHEMA,
    EvidenceError,
    build_snapshot,
    citation_error,
    conflict_report,
    excerpt_sha256,
    render_evidence_block,
    validate_snapshot,
)


def _source(evidence_id, value, excerpt, publisher, url):
    return {
        "evidence_id": evidence_id,
        "url": url,
        "title": f"Entry {evidence_id}",
        "publisher": publisher,
        "retrieved_at": "2026-09-18T10:00:00+00:00",
        "excerpt": excerpt,
        "content_sha256": excerpt_sha256(excerpt),
        "verifies": {"entity": "Lisette", "attribute": "tonnage", "value": value},
        "applicable_context": f"context of {evidence_id}",
    }


def _conflicting_snapshot(resolutions=None):
    payload = {
        "schema_version": EVIDENCE_SCHEMA,
        "query": {"text": "Lisette tonnage", "derivation": "task",
                  "task_excerpt": "How many tons was the Lisette?"},
        "sources": [
            _source("E1", "116 tons", "The schooner Lisette measured 116 tons.",
                    "gutenberg.org", "https://gutenberg.org/x"),
            _source("E2", "76 tons", "A caption calls her a 76 ton schooner.",
                    "heritage-images.com", "https://heritage-images.com/y"),
        ],
    }
    if resolutions is not None:
        payload["resolutions"] = resolutions
    return validate_snapshot(payload)


def test_conflict_without_a_record_is_unresolved():
    snapshot = _conflicting_snapshot()
    conflicts = conflict_report(snapshot["sources"])
    assert len(conflicts) == 1 and conflicts[0]["resolved"] is False
    error = citation_error(
        snapshot, ["E1"], where="upgrade", require_settled=True,
    )
    assert error is not None and "unresolved conflicts" in error


def test_recorded_resolution_settles_the_conflict_for_the_named_source():
    snapshot = _conflicting_snapshot(resolutions=[{
        "entity": "Lisette", "attribute": "tonnage", "resolved_by": "E1",
        "reason": "E1 matches the task's decoy-service context; E2 is a "
                  "photo caption with no register citation",
    }])
    conflicts = conflict_report(
        snapshot["sources"], snapshot["resolutions"],
    )
    assert conflicts[0]["resolved"] is True
    assert conflicts[0]["resolved_by"] == "E1"
    # Citing the resolved source is allowed...
    assert citation_error(
        snapshot, ["E1"], where="upgrade", require_settled=True,
    ) is None
    # ...citing only the superseded one is not.
    error = citation_error(
        snapshot, ["E2"], where="upgrade", require_settled=True,
    )
    assert error is not None and "resolves it in favour of E1" in error


def test_resolution_must_name_a_snapshot_source():
    with pytest.raises(EvidenceError, match="is not a snapshot source"):
        _conflicting_snapshot(resolutions=[{
            "entity": "Lisette", "attribute": "tonnage", "resolved_by": "E9",
            "reason": "invented",
        }])


def test_resolution_entries_are_closed_and_unique():
    with pytest.raises(EvidenceError, match="unknown fields"):
        _conflicting_snapshot(resolutions=[{
            "entity": "Lisette", "attribute": "tonnage", "resolved_by": "E1",
            "reason": "r", "extra": "no",
        }])
    with pytest.raises(EvidenceError, match="repeats"):
        _conflicting_snapshot(resolutions=[
            {"entity": "Lisette", "attribute": "tonnage", "resolved_by": "E1",
             "reason": "r"},
            {"entity": "lisette", "attribute": "TONNAGE", "resolved_by": "E2",
             "reason": "r"},
        ])


def test_build_snapshot_carries_resolutions_into_the_render():
    built = build_snapshot(
        query="Lisette tonnage", derivation="task", task_excerpt="How many tons?",
        sources=[
            {k: v for k, v in _source(
                "E1", "116 tons", "The schooner Lisette measured 116 tons.",
                "gutenberg.org", "https://gutenberg.org/x",
            ).items() if k != "content_sha256"},
            {k: v for k, v in _source(
                "E2", "76 tons", "A caption calls her a 76 ton schooner.",
                "heritage-images.com", "https://heritage-images.com/y",
            ).items() if k != "content_sha256"},
        ],
        resolutions=[{
            "entity": "Lisette", "attribute": "tonnage", "resolved_by": "E1",
            "reason": "E1 describes the decoy-service vessel the task names",
        }],
    )
    text = render_evidence_block(built)
    assert "RECORDED CONFLICT RESOLUTIONS" in text
    assert "the applicable source is E1" in text
    assert "UNRESOLVED CONFLICTS" not in text
