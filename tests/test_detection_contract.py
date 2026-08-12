"""Proof-court terminal/occurrence state consistency unit tests.

Guards the invariant that ``occurrence_state`` never reports ``verified_clean``
off a case the court left ``unresolved``: a candidate the defense could only
abstain on (``insufficient_evidence``) with no causal error certificate is an
open question, not a decisive no-incident result.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.detection.court.contract import adjudicate_proof_court
from sehd_rise.detection.injection import (
    PacaStage,
    make_skill_aware_provider,
    route_binding_map,
)
from sehd_rise.skills.genome import create_baseline_genome
from sehd_rise.skills.vocabulary.contracts import PolicyItem


def _packet() -> dict:
    return {
        "schema_version": "agenthallu.ocse_packet.v1",
        "question": "q",
        "events": [{"event_id": "event_001", "position": 1, "tool_interactions": []}],
    }


def _candidate() -> list[dict]:
    return [{"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"}]


def test_unresolved_candidate_stays_unresolved_in_occurrence() -> None:
    """Coverage-complete but non-terminal candidate is unresolved at both
    the verdict and the occurrence level — never promoted to clean."""
    result = adjudicate_proof_court(
        required_candidates=_candidate(),
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={
            "coverage_state": "complete",
            "candidates": [{
                "certificate_id": "d1",
                "target_candidate_id": "c1",
                "target_origin_event_id": "event_001",
                "defense_type": "insufficient_evidence",
                "evidence_refs": ["event_001"],
                "rationale": "no clean boundary provable",
            }],
        },
        packet=_packet(),
    )
    assert result["state"] == "unresolved"
    assert result["occurrence_state"] == "unresolved"
    assert result["incident_occurred"] is None


def test_clean_candidate_is_clean_in_occurrence() -> None:
    """A dominating defense resolves the candidate clean at both levels."""
    result = adjudicate_proof_court(
        required_candidates=_candidate(),
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={
            "coverage_state": "complete",
            "candidates": [{
                "certificate_id": "d1",
                "target_candidate_id": "c1",
                "target_origin_event_id": "event_001",
                "defense_type": "explicitly_authorized",
                "evidence_refs": ["event_001"],
                "rationale": "action was explicitly authorized",
            }],
        },
        packet=_packet(),
    )
    assert result["state"] == "verified_clean"
    assert result["occurrence_state"] == "verified_clean"
    assert result["incident_occurred"] is False


def test_policy_evaluation_terminal_state_is_court_authority() -> None:
    """The court owns the post-policy terminal state; the evaluation record
    projects the same value instead of a simplified recomputation."""
    result = adjudicate_proof_court(
        required_candidates=_candidate(),
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={
            "coverage_state": "complete",
            "candidates": [{
                "certificate_id": "d1",
                "target_candidate_id": "c1",
                "target_origin_event_id": "event_001",
                "defense_type": "explicitly_authorized",
                "evidence_refs": ["event_001"],
                "rationale": "action was explicitly authorized",
            }],
        },
        packet=_packet(),
        policy_items=[
            PolicyItem(
                item_id="p1",
                precondition="prosecution_coverage_complete_and_zero_certificates",
                action="downgrade_coverage",
                params={"side": "prosecution", "to_state": "incomplete"},
            )
        ],
    )
    evaluation = result["policy_evaluation"]
    assert result["state"] == "unresolved"
    assert evaluation["terminal_state"] == "unresolved"
    assert result["pre_policy_state"] == "verified_clean"


def _route_config() -> dict:
    routes = (
        "candidate_fallback_route", "construct_census_route",
        "qualification_route", "propagation_route", "boundary_route",
        "focused_prosecution_route", "defense_route",
        "appeal_prosecutor_route", "appeal_defender_route",
    )
    return {
        name: {"_provider_route_name": name, "model": "deepseek-v4-flash"}
        for name in routes
    }


def test_route_binding_is_name_keyed_not_identity() -> None:
    """A copied route dict still resolves its binding (O1): the map keys on
    ``_provider_route_name``, so object identity no longer matters."""
    config = _route_config()
    bindings = route_binding_map(config)
    # Keys are route names, not memory addresses.
    assert set(bindings) == set(_route_config())
    # A shallow copy of a route (what any caller may do before dispatch) is
    # still addressable by its stamped name.
    copied = dict(config["focused_prosecution_route"])
    assert bindings[copied["_provider_route_name"]][0] == "focused_prosecution_route"


def test_skill_provider_accepts_copied_route() -> None:
    """The wrapper resolves a copied route dict instead of raising on it."""
    from sehd_rise.detection.injection import compile_genome_for_paca

    config = _route_config()
    compiled = compile_genome_for_paca(create_baseline_genome())
    wrapped = make_skill_aware_provider(
        lambda *, route, system, user: ({"candidates": []}, {"usage": {}}),
        compiled=compiled, config=config,
    )
    copied = dict(config["focused_prosecution_route"])
    value, meta = wrapped(route=copied, system="s", user="u")
    assert isinstance(value, dict)
    assert meta["bccse_route"] == "focused_prosecution_route"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
