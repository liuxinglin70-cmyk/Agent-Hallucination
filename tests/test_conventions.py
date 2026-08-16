"""Convention overlay, three-verdict court and census-probe execution.

Locks the detector-side rework:

- the closed convention vocabulary routes on mechanical packet features
  (never dataset identity), and the routed block reaches all three stage
  prompts;
- verification is three-valued: an insufficient_evidence certificate
  abstains from conviction but is recorded for FN triage;
- census check obligations (the evolution channel-A genome) inject only
  when every trigger feature holds — the unguided default stays clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from sehd_rise2.detector import court as court_module  # noqa: E402
from sehd_rise2.detector.conventions import (  # noqa: E402
    packet_features,
    render_convention_block,
    render_evolved_block,
    route_conventions,
)
from sehd_rise2.detector.packet import build_packet  # noqa: E402


def _record(history: list[dict], question: str = "task") -> dict:
    return {"question": question, "history": history}


# --------------------------------------------------------------------------
# mechanical feature decisions
# --------------------------------------------------------------------------


def test_features_numeric_restatement_requires_later_position() -> None:
    observed_then_restated = build_packet(_record([
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log", "arguments": {"value": 36}}],
            "tool_responses": ["{'result': 2.0}"],
        },
        {"role": "assistant", "content": "The result is 2.0000."},
    ], question="compute log to 4 decimals"))
    assert "numeric_report_restatement" in packet_features(observed_then_restated)

    # Same numeric in a strictly EARLIER argument is not a restatement.
    argument_then_observed = build_packet(_record([
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log", "arguments": {"value": 2.0}}],
            "tool_responses": ["{'result': 2.0}"],
        },
        {"role": "assistant", "content": "Done."},
    ], question="compute log of 2.0"))
    assert "numeric_report_restatement" not in packet_features(argument_then_observed)


def test_features_task_specifies_values_needs_literal() -> None:
    with_literal = build_packet(_record(
        [{"role": "assistant", "content": "ok"}],
        question="book seat with code 'RMS' for flight 2.0",
    ))
    assert "task_specifies_values" in packet_features(with_literal)
    without = build_packet(_record(
        [{"role": "assistant", "content": "ok"}],
        question="find the best restaurant nearby",
    ))
    assert "task_specifies_values" not in packet_features(without)


def test_conventions_route_on_required_features() -> None:
    # A later TOOL ARGUMENT restating an observed value routes the
    # argument-fidelity convention (exact), not the prose tolerance.
    argument_restate = _record([
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log", "arguments": {"value": 36}}],
            "tool_responses": ["{'result': 2.0}"],
        },
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "echo",
                            "arguments": {"content": "2.0000"}}],
            "tool_responses": ["None"],
        },
    ], question="compute log to 4 decimals")
    packet = build_packet(argument_restate)
    features = packet_features(packet)
    routed = route_conventions(features)
    assert "tool_argument_exact_match" in routed
    assert "observation_restatement_fidelity" in routed
    assert "report_presentation_tolerance" not in routed
    assert "CONVENTION tool_argument_exact_match" in render_convention_block(routed)
    assert render_convention_block([]) == ""

    # A PROSE restatement routes the presentation tolerance instead.
    prose_restate = _record([
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log", "arguments": {"value": 36}}],
            "tool_responses": ["{'result': 2.0}"],
        },
        {"role": "assistant", "content": "The result is 2.0000."},
    ], question="compute log to 4 decimals")
    features_prose = packet_features(build_packet(prose_restate))
    assert "report_presentation_tolerance" in route_conventions(features_prose)
    assert "observation_restatement_fidelity" not in route_conventions(
        features_prose,
    )


# --------------------------------------------------------------------------
# three-verdict court: abstention is recorded, terminal rule unchanged
# --------------------------------------------------------------------------


def _three_verdict_provider(verdicts: dict[str, str]):
    def _call(*, route, system, user, purpose, **_kwargs):
        if purpose == "census":
            return {"candidates": [{
                "candidate_id": "c1",
                "claim": "the agent did X instead of Y",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
            }]}, {"attempts": 1}
        if purpose == "verification":
            return {"certificates": [
                {"candidate_id": "c1", "verdict": verdicts["c1"],
                 "rationale": "missing observation"},
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    return _call


def test_insufficient_evidence_abstains_and_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = build_packet(_record([
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "here is the answer"},
    ]))
    monkeypatch.setattr(
        court_module, "provider_call",
        _three_verdict_provider({"c1": "insufficient_evidence"}),
    )
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, _audit = court_module.run_court(packet, config)
    assert proof["state"] == "verified_clean"
    assert proof["runtime_valid"] is True
    assert len(proof["abstained_certificates"]) == 1
    assert proof["abstained_certificates"][0]["candidate_id"] == "c1"
    assert proof["miss_triage_facts"]["abstained_count"] == 1
    # No supported certificate: defense is skipped entirely.
    assert "decisions" not in str(proof.get("defense_decisions")) or (
        proof["defense_decisions"] == {}
    )


def test_supported_still_convicts_after_rejection_flips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = build_packet(_record([
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "here is the answer"},
    ]))

    def _call(*, route, system, user, purpose, **_kwargs):
        if purpose == "census":
            return {"candidates": [
                {"candidate_id": "c1", "claim": "wrong A",
                 "locus_event_id": "event_002", "evidence_refs": ["event_002"]},
                {"candidate_id": "c2", "claim": "wrong B",
                 "locus_event_id": "event_002", "evidence_refs": ["event_002"]},
            ]}, {"attempts": 1}
        if purpose == "verification":
            return {"certificates": [
                {"candidate_id": "c1", "verdict": "supported",
                 "rationale": "r"},
                {"candidate_id": "c2", "verdict": "rejected",
                 "rationale": "r"},
                {"candidate_id": "c3", "verdict": "insufficient_evidence",
                 "rationale": "r"},
            ][:2]}, {"attempts": 1}
        if purpose == "defense":
            return {"decisions": [
                {"candidate_id": "c1", "verdict": "upheld", "rationale": "r"},
            ]}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    monkeypatch.setattr(court_module, "provider_call", _call)
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    proof, _providers, _audit = court_module.run_court(packet, config)
    assert proof["state"] == "verified_error"
    assert proof["miss_triage_facts"]["supported_count"] == 1


# --------------------------------------------------------------------------
# probe execution surface: route-gated injection
# --------------------------------------------------------------------------


EVOLVED = {
    "convention_id": "EC-01",
    "trigger_features": [
        "numeric_argument_restatement", "numeric_observations",
    ],
    "directive": (
        "Treat every later tool-argument restatement of an observed "
        "numeric value that changes the literal form as a deviation."
    ),
    "exclusion": (
        "the argument value the task itself fixed differs from the "
        "observation because the task demanded the original form."
    ),
}


def _capture_provider(seen: dict):
    def _call(*, route, system, user, purpose, **_kwargs):
        if purpose == "census":
            seen["census_system"] = system
            return {"candidates": []}, {"attempts": 1}
        raise AssertionError(f"unexpected purpose {purpose!r}")

    return _call


def test_convention_injects_only_when_trigger_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matched = build_packet(_record([
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "log", "arguments": {"value": 36}}],
            "tool_responses": ["{'result': 2.0}"],
        },
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"name": "echo",
                            "arguments": {"content": "2.0000"}}],
            "tool_responses": ["None"],
        },
    ], question="compute log to 4 decimals"))
    unmatched = build_packet(_record([
        {"role": "assistant", "content": "no tools here"},
    ], question="write a poem"))
    config = {
        "census_route": {}, "verification_route": {}, "defense_route": {},
    }
    seen: dict = {}
    monkeypatch.setattr(
        court_module, "provider_call", _capture_provider(seen),
    )
    proof, _p, audit = court_module.run_court(
        matched, config, evolved_conventions=[EVOLVED],
    )
    assert "Treat every later tool-argument restatement" in seen[
        "census_system"
    ]
    assert "NOT WHEN" in seen["census_system"]
    assert proof["active_evolved_conventions"] == ["EC-01"]
    assert audit["packet_features"] is not None

    court_module.run_court(unmatched, config, evolved_conventions=[EVOLVED])
    assert "Treat every later tool-argument restatement" not in seen[
        "census_system"
    ]


def test_render_evolved_block_requires_dual_boundary() -> None:
    # trigger not fully matched: no injection
    assert render_evolved_block(
        [EVOLVED], {"numeric_argument_restatement"},
    ) == ""
    # both boundaries present and trigger matched: injection carries
    # the exclusion side too
    block = render_evolved_block(
        [EVOLVED],
        {"numeric_argument_restatement", "numeric_observations"},
    )
    assert "Treat every later tool-argument restatement" in block
    assert "NOT WHEN" in block
    # a one-sided entry (no exclusion) never routes
    one_sided = {**EVOLVED, "exclusion": ""}
    assert render_evolved_block(
        [one_sided],
        {"numeric_argument_restatement", "numeric_observations"},
    ) == ""
