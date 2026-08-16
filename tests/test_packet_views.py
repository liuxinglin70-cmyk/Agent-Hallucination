"""Packet view tests: full-fidelity census + selective fail-closed evidence.

Locks the sehd_rise2 packet rendering contract:

- ``render_full_view``: full-detail render at the census budgets; the
  census view degrades to index+tail only beyond the budget, and the
  degradation is recorded, never silent.
- ``render_index_view`` (degraded fallback): every event is nameable via
  the index; the tail is additionally rendered at full detail.
- ``render_evidence_view`` (verification/defense): only cited events plus
  neighbours; a CITED event that cannot be rendered in full marks the view
  truncated, and the court fails closed (unresolved) instead of convicting
  on partial evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from sehd_rise2.detector import court as court_module  # noqa: E402
from sehd_rise2.detector.packet import (  # noqa: E402
    CENSUS_VIEW_CHARS,
    EVIDENCE_EVENT_CHARS,
    INDEX_TAIL_EVENTS,
    _MAX_EVENT_CONTENT,
    build_packet,
    render_census_view,
    render_evidence_view,
    render_full_view,
    render_index_view,
)


def _record(n_events: int = 20, content_chars: int = 3000) -> dict:
    history = []
    for i in range(1, n_events + 1):
        row = {
            "role": "assistant" if i % 2 == 0 else "user",
            "content": f"message {i} " + ("x" * content_chars),
        }
        history.append(row)
    return {"question": "long trajectory test", "history": history}


def _record_with_giant_observation() -> dict:
    record = _record(20)
    record["history"][4]["tool_calls"] = [
        {"name": "search", "arguments": {"q": "test"}},
    ]
    record["history"][4]["tool_responses"] = ["y" * 600_000]
    return record


# --------------------------------------------------------------------------
# full view: census budgets, tail visible for moderate trajectories
# --------------------------------------------------------------------------


def test_full_view_keeps_tail_for_moderate_trajectory() -> None:
    packet = build_packet(_record(20, 3000))
    text, meta = render_full_view(packet)
    assert "EVENT event_020" in text
    assert len(meta["rendered_event_ids"]) == 20
    assert meta["packet_truncated"] is False


def test_full_view_truncates_only_when_budget_exhausted() -> None:
    packet = build_packet(_record(200, 3000))
    _text, meta = render_full_view(packet)
    assert meta["packet_truncated"] is True
    assert len(meta["rendered_event_ids"]) < 200


def test_census_view_degrades_recorded_beyond_budget() -> None:
    packet = build_packet(_record(200, 3000))
    text, meta = render_census_view(packet)
    assert meta["degraded"] is True
    assert meta["full_render_chars"] > CENSUS_VIEW_CHARS
    # The degraded view is the index view: previews, not full content.
    assert "EVENT INDEX:" in text
    assert "preview=" in text


def test_census_view_full_fidelity_within_budget() -> None:
    packet = build_packet(_record(20, 3000))
    _text, meta = render_census_view(packet)
    assert meta["degraded"] is False
    assert len(meta["rendered_event_ids"]) == 20


# --------------------------------------------------------------------------
# index view (census): every event nameable, tail at full detail
# --------------------------------------------------------------------------


def test_index_view_lists_every_event() -> None:
    packet = build_packet(_record(40, 3000))
    text, meta = render_index_view(packet)
    for i in range(1, 41):
        assert f"event_{i:03d}" in text
    assert len(meta["rendered_event_ids"]) == INDEX_TAIL_EVENTS
    assert "event_040" in meta["rendered_event_ids"]


def test_index_view_previews_observation_lengths() -> None:
    packet = build_packet(_record_with_giant_observation())
    text, _meta = render_index_view(packet)
    assert "obs_chars=600000" in text


# --------------------------------------------------------------------------
# evidence view (verification/defense): selective, full-fidelity, fail-closed
# --------------------------------------------------------------------------


def test_evidence_view_renders_only_cited_plus_window() -> None:
    packet = build_packet(_record(20, 1000))
    text, meta = render_evidence_view(packet, ["event_005_tool_01"])
    assert meta["rendered_event_ids"] == [
        "event_003", "event_004", "event_005", "event_006", "event_007",
    ]
    # Omitted events never show content; their ids appear only inside the
    # omission notice that keeps "not shown" distinguishable from "absent".
    assert "message 1 " not in text
    assert "message 20 " not in text
    assert len(meta["omitted_event_ids"]) == 15
    assert "event_001" in text
    assert "EVENTS OMITTED FROM THIS VIEW" in text


def test_evidence_view_full_fidelity_for_cited_event() -> None:
    packet = build_packet(_record(20, 3000))
    text, meta = render_evidence_view(packet, ["event_010"])
    assert ("message 10 " + "x" * 3000) in text
    assert meta["packet_truncated"] is False
    assert meta["truncated_event_ids"] == []


def test_evidence_view_cited_field_truncation_marks_view_incomplete() -> None:
    packet = build_packet(_record_with_giant_observation())
    text, meta = render_evidence_view(packet, ["event_005_tool_01"])
    # The 600k observation cannot fit even the raised per-event budget:
    # the conviction basis is incomplete -> fail-closed marker.
    assert meta["packet_truncated"] is True
    assert meta["truncated_event_ids"] == ["event_005"]


def test_evidence_view_missing_refs_is_empty_but_not_truncated() -> None:
    packet = build_packet(_record(5, 100))
    _text, meta = render_evidence_view(packet, ["event_999"])
    assert meta["rendered_event_ids"] == []
    assert meta["packet_truncated"] is False


def test_evidence_view_resolves_interaction_refs_to_parent_event() -> None:
    packet = build_packet(_record(5, 100))
    # An interaction-level ref whose interaction does not exist still
    # resolves to the existing parent event (plus window).
    _text, meta = render_evidence_view(packet, ["event_003_tool_99"])
    assert meta["rendered_event_ids"] == [
        "event_001", "event_002", "event_003", "event_004", "event_005",
    ]
    assert meta["packet_truncated"] is False


def test_evidence_event_budget_is_large() -> None:
    # The per-event evidence budget must comfortably exceed the full-view
    # per-event cap: cited evidence gets the big window.
    assert EVIDENCE_EVENT_CHARS >= _MAX_EVENT_CONTENT * 1.5


# --------------------------------------------------------------------------
# court integration: truncated evidence view -> unresolved, fail-closed
# --------------------------------------------------------------------------


def _fake_provider(census_parsed: dict, certificate_parsed: dict):
    def _call(*, route, system, user, purpose, **_kwargs) -> tuple[dict, dict]:
        if purpose == "census":
            return census_parsed, {"attempts": 1}
        if purpose == "verification":
            return certificate_parsed, {"attempts": 1}
        if purpose == "defense":
            return {"decisions": [
                {"candidate_id": "c1", "verdict": "upheld", "rationale": "r"},
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    return _call


def test_court_abstains_on_truncated_verification_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record_with_giant_observation()
    packet = build_packet(record)
    census_parsed = {"candidates": [{
        "candidate_id": "c1",
        "claim": "the agent claimed X instead of Y",
        "locus_event_id": "event_005_tool_01",
        "evidence_refs": ["event_005_tool_01"],
    }]}
    certificate_parsed = {"certificates": [{
        "candidate_id": "c1", "verdict": "supported", "rationale": "r",
    }]}
    monkeypatch.setattr(
        court_module, "provider_call", _fake_provider(census_parsed, certificate_parsed),
    )
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, audit = court_module.run_court(packet, config)
    # The cited 600k observation exceeds any renderable view: the
    # candidate mechanically abstains — never a conviction on partial
    # evidence, never an unresolved case, degradation recorded.
    assert audit["evidence_coverage"]["verification"] == "degraded"
    assert proof["state"] == "verified_clean"
    assert proof["runtime_valid"] is True
    assert proof["abstained_certificates"][0]["candidate_id"] == "c1"
    assert proof["miss_triage_facts"]["evidence_view_truncated"] is True


def test_court_runs_clean_on_complete_views(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record(5, 500)
    packet = build_packet(record)
    census_parsed = {"candidates": [{
        "candidate_id": "c1",
        "claim": "the agent did X instead of Y",
        "locus_event_id": "event_003",
        "evidence_refs": ["event_002"],
    }]}
    certificate_parsed = {"certificates": [{
        "candidate_id": "c1", "verdict": "supported", "rationale": "r",
    }]}
    monkeypatch.setattr(
        court_module, "provider_call", _fake_provider(census_parsed, certificate_parsed),
    )
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, audit = court_module.run_court(packet, config)
    assert audit["evidence_coverage"]["verification"] == "complete"
    assert audit["evidence_coverage"]["defense"] == "complete"
    assert proof["runtime_valid"] is True
    assert proof["state"] == "verified_error"


# --------------------------------------------------------------------------
# responsible-step rule: earliest upheld LOCUS, evidence refs excluded
# --------------------------------------------------------------------------


def test_responsible_event_is_earliest_upheld_locus_not_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence refs are proof anchors that precede the deviation; the
    responsible event must be the earliest deviation locus, not the
    earliest proof (e.g. the user question at event_001)."""
    record = _record(10, 100)
    packet = build_packet(record)

    def _call(*, route, system, user, purpose, **_kwargs) -> tuple[dict, dict]:
        if purpose == "census":
            return {"candidates": [
                {
                    "candidate_id": "c1", "claim": "wrong claim A",
                    "locus_event_id": "event_004",
                    "evidence_refs": ["event_001"],
                },
                {
                    "candidate_id": "c2", "claim": "wrong claim B",
                    "locus_event_id": "event_006",
                    "evidence_refs": ["event_003", "event_006"],
                },
            ]}, {"attempts": 1}
        if purpose == "verification":
            return {"certificates": [
                {"candidate_id": "c1", "verdict": "supported", "rationale": "r"},
                {"candidate_id": "c2", "verdict": "supported", "rationale": "r"},
            ]}, {"attempts": 1}
        if purpose == "defense":
            return {"decisions": [
                {"candidate_id": "c1", "verdict": "upheld", "rationale": "r"},
                {"candidate_id": "c2", "verdict": "upheld", "rationale": "r"},
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    monkeypatch.setattr(court_module, "provider_call", _call)
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, _audit = court_module.run_court(packet, config)
    assert proof["state"] == "verified_error"
    # Old rule (min over locus ∪ refs) would pick event_001.
    assert proof["responsible_event_id"] == "event_004"


# --------------------------------------------------------------------------
# batched evidence views: oversized citation sets split instead of failing
# --------------------------------------------------------------------------


def _batched_provider():
    """Verification/defense respond only for the batch's own candidate ids."""
    import re as _re
    calls: dict[str, int] = {}

    def _call(*, route, system, user, purpose, **_kwargs) -> tuple[dict, dict]:
        calls[purpose] = calls.get(purpose, 0) + 1
        if purpose == "census":
            return {"candidates": [
                {
                    "candidate_id": f"c{i}",
                    "claim": f"claim {i}",
                    "locus_event_id": f"event_{i:03d}",
                    "evidence_refs": [f"event_{i:03d}"],
                }
                for i in range(1, 6)
            ]}, {"attempts": 1}
        if purpose == "verification":
            ids = _re.findall(r"- (c\d+):", user)
            return {"certificates": [
                {"candidate_id": cid, "verdict": "supported", "rationale": "r"}
                for cid in ids
            ]}, {"attempts": 1}
        if purpose == "defense":
            ids = _re.findall(r"- (c\d+): claim=", user)
            return {"decisions": [
                {"candidate_id": cid, "verdict": "upheld", "rationale": "r"}
                for cid in ids
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    _call.counts = calls  # type: ignore[attr-defined]
    return _call


def test_court_batches_oversized_verification_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 5 candidates citing events 1..5 (70k content each): the merged
    # windows (6 events x ~70k chars) exceed the evidence-view budget —
    # the court must split into batches instead of failing closed.
    history = [
        {
            "role": "assistant" if i % 2 == 0 else "user",
            "content": f"message {i} " + ("x" * 70_000),
        }
        for i in range(1, 9)
    ]
    record = {"question": "batched test", "history": history}
    packet = build_packet(record)
    fake = _batched_provider()
    monkeypatch.setattr(court_module, "provider_call", fake)
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, audit = court_module.run_court(packet, config)
    assert audit["verification_batches"] >= 2
    assert fake.counts["verification"] == audit["verification_batches"]
    assert audit["evidence_coverage"]["verification"] == "complete"
    assert proof["state"] == "verified_error"
    # Every candidate was covered across batches.
    assert proof["certificate_count"] == 5


