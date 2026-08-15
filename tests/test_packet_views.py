"""Packet view tests: two-phase evidence access + truncation-aware fail-closed.

Locks the sehd_rise2 packet rendering contract:

- ``render_full_view``: bounded full-detail render whose raised budget keeps
  the chronological TAIL visible for moderate trajectories (the old 24k cap
  dropped the tail — the most hallucination-relevant part).
- ``render_index_view`` (census): every event is nameable via the index;
  the tail is additionally rendered at full detail.
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
    EVIDENCE_EVENT_CHARS,
    INDEX_TAIL_EVENTS,
    _MAX_EVENT_CONTENT,
    build_packet,
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
# full view: the raised budget keeps the tail visible
# --------------------------------------------------------------------------


def test_full_view_keeps_tail_for_moderate_trajectory() -> None:
    packet = build_packet(_record(20, 3000))
    text, meta = render_full_view(packet)
    # 20 x ~3k chars fits under the 96k budget: the final event must be
    # visible. Under the old 24k cap the tail was cut off.
    assert "EVENT event_020" in text
    assert len(meta["rendered_event_ids"]) == 20
    assert meta["packet_truncated"] is False


def test_full_view_truncates_only_when_budget_exhausted() -> None:
    packet = build_packet(_record(50, 3000))
    _text, meta = render_full_view(packet)
    assert meta["packet_truncated"] is True
    assert len(meta["rendered_event_ids"]) < 50


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
        "event_004", "event_005", "event_006",
    ]
    assert "event_001" not in text
    assert "event_020" not in text
    assert len(meta["omitted_event_ids"]) == 17


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
        "event_002", "event_003", "event_004",
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


def test_court_fails_closed_on_truncated_verification_view(
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
    # The verification stage saw a truncated evidence view: the run must
    # fail closed instead of convicting on partial evidence.
    assert audit["evidence_coverage"]["verification"] == "reduced"
    assert proof["state"] == "unresolved"
    assert proof["runtime_valid"] is False
    codes = [r["code"] for r in proof["authority_failure_reasons"]]
    assert "evidence_view_incomplete" in codes


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
            ids = _re.findall(r"- (c\d+): verdict=supported", user)
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


# --------------------------------------------------------------------------
# census second pass: empty first census re-scans long middle content
# --------------------------------------------------------------------------


def test_census_second_pass_recovers_middle_hallucination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [
        {"role": "assistant" if i % 2 == 0 else "user",
         "content": f"message {i} " + ("y" * 1000)}
        for i in range(1, 31)
    ]
    record = {"question": "second pass", "history": history}
    packet = build_packet(record)
    state: dict[str, int] = {"census": 0}

    def _call(*, route, system, user, purpose, **_kwargs) -> tuple[dict, dict]:
        if purpose == "census":
            state["census"] += 1
            return {"candidates": []}, {"attempts": 1}
        if purpose == "census_pass2":
            return {"candidates": [{
                "candidate_id": "c1", "claim": "middle error",
                "locus_event_id": "event_010", "evidence_refs": ["event_010"],
            }]}, {"attempts": 1}
        if purpose == "verification":
            return {"certificates": [
                {"candidate_id": "c1", "verdict": "supported", "rationale": "r"},
            ]}, {"attempts": 1}
        if purpose == "defense":
            return {"decisions": [
                {"candidate_id": "c1", "verdict": "upheld", "rationale": "r"},
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    monkeypatch.setattr(court_module, "provider_call", _call)
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, providers, _audit = court_module.run_court(packet, config)
    assert state["census"] == 1
    assert "census_pass2" in providers
    assert proof["state"] == "verified_error"


# --------------------------------------------------------------------------
# anti-noise contract scoping: formatting tolerance must not swallow
# exact-match tool-argument deviations (BFCL/025 class)
# --------------------------------------------------------------------------


def test_formatting_tolerance_is_scoped_to_prose_not_tool_arguments() -> None:
    """'2.0000' vs '2.0' is a deviation when it is a tool ARGUMENT the task
    requires exactly, but not in prose reports. All three stage contracts
    must carry the scoped rule."""
    assert "exact-match interfaces" in court_module.DEFENSE_SYSTEM
    assert "exact-match semantics" in court_module.VERIFICATION_SYSTEM
    assert "even " + "only in formatting" in court_module.VERIFICATION_SYSTEM
    assert "even " + "only in formatting" in court_module.DEFENSE_SYSTEM
    assert "matched EXACTLY" in court_module.CENSUS_SYSTEM
    # The census must also compare later restatements against tool
    # observations exactly (BFCL/025: observation '2.0' echoed as '2.0000').
    assert "RESTATES a value" in court_module.CENSUS_SYSTEM
    # The old blanket exemption is gone: the tolerance must be scoped to
    # prose reports, never apply to tool arguments.
    assert "are not deviations in PROSE" in court_module.VERIFICATION_SYSTEM
    assert "in a REPORTED number" in court_module.DEFENSE_SYSTEM
    # A tool observation restated in a later argument/report is
    # authoritative (BFCL/025: observation '2.0' echoed as '2.0000').
    assert "observation is the AUTHORITATIVE" in court_module.VERIFICATION_SYSTEM
    assert "observation is the authoritative value" in court_module.DEFENSE_SYSTEM


def test_observation_restatement_check_adds_deterministic_candidate() -> None:
    """BFCL/025 class: observation '2.0' restated as '2.0000' must be
    proposed mechanically even when the LLM census returns empty."""
    history = [
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "logarithm",
                            "arguments": {"value": 36, "base": 6, "precision": 4}}],
            "tool_responses": ["{'result': mpf('2.0')}"],
        },
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "echo",
                            "arguments": {"content": "2.0000",
                                          "file_name": "result.txt"}}],
            "tool_responses": ["None"],
        },
        {
            "role": "assistant", "content": "The result is: **2.0000**",
        },
    ]
    packet = build_packet({"question": "log to 4 decimals", "history": history})
    added = court_module._observation_restatement_check(packet, [])
    assert len(added) == 1
    candidate = added[0]
    assert candidate["locus_event_id"] == "event_002_tool_01"
    assert candidate["evidence_refs"] == ["event_001_tool_01"]
    assert "'2.0'" in candidate["claim"] and "'2.0000'" in candidate["claim"]


def test_observation_restatement_check_skips_exact_restatements() -> None:
    """Identical string forms are not a deviation: no candidate added."""
    history = [
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "wc",
                            "arguments": {"file_name": "f", "mode": "c"}}],
            "tool_responses": ['{"count": 36, "type": "characters"}'],
        },
        {
            "role": "assistant", "content": "The count is 36 characters.",
        },
    ]
    packet = build_packet({"question": "count", "history": history})
    assert court_module._observation_restatement_check(packet, []) == []


def test_observation_restatement_check_ignores_prose_and_noise() -> None:
    """Prose restatements, leading-zero variants and same-interaction
    pairs are not covered by the exact-match convention."""
    # prose restatement: '2.0' -> content '2.0000' (no tool argument)
    history = [
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log",
                            "arguments": {"value": 36}}],
            "tool_responses": ["{'result': mpf('2.0')}"],
        },
        {"role": "assistant", "content": "The result is 2.0000."},
    ]
    packet = build_packet({"question": "log", "history": history})
    assert court_module._observation_restatement_check(packet, []) == []
    # leading-zero variant without decimal points: '01' -> '1'
    history2 = [
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "list", "arguments": {}}],
            "tool_responses": ["{'id': '01'}"],
        },
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "use", "arguments": {"id": 1}}],
            "tool_responses": ["None"],
        },
    ]
    packet2 = build_packet({"question": "use", "history": history2})
    assert court_module._observation_restatement_check(packet2, []) == []
    # same-interaction pair: observation and argument in one tool call
    history3 = [
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "echo",
                            "arguments": {"content": "5.5"}}],
            "tool_responses": ["{'echoed': '5.50'}"],
        },
    ]
    packet3 = build_packet({"question": "echo", "history": history3})
    assert court_module._observation_restatement_check(packet3, []) == []


def test_census_second_pass_contract_failure_keeps_clean_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [
        {"role": "assistant" if i % 2 == 0 else "user",
         "content": f"message {i} " + ("y" * 1000)}
        for i in range(1, 31)
    ]
    record = {"question": "second pass", "history": history}
    packet = build_packet(record)

    def _call(*, route, system, user, purpose, **_kwargs) -> tuple[dict, dict]:
        if purpose == "census":
            return {"candidates": []}, {"attempts": 1}
        if purpose == "census_pass2":
            # Contract violation in the advisory pass: the first pass's
            # well-formed clean verdict must stand.
            return {"candidates": "not-a-list"}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    monkeypatch.setattr(court_module, "provider_call", _call)
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, providers, _audit = court_module.run_court(packet, config)
    assert "census_pass2" in providers
    assert proof["runtime_valid"] is True
    assert proof["state"] == "verified_clean"
