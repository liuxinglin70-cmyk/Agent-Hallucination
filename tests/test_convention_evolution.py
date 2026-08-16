"""Convention evolution: triage, G1 dual-boundary gate, genome identity."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sehd_rise2.evolution.convention_evolution import (  # noqa: E402
    attribute_miss,
    build_convention_genome,
    gate_conventions,
)


def _result(state: str, *, candidates: int = 0, abstained: int = 0,
            supported: int = 0, overturned: int = 0, degraded: bool = False,
            evidence_truncated: bool = False) -> dict:
    return {
        "state": state,
        "proof_adjudication": {
            "census": [{"claim": f"claim {i}"} for i in range(candidates)],
            "miss_triage_facts": {
                "census_candidates": candidates,
                "abstained_count": abstained,
                "supported_count": supported,
                "overturned_count": overturned,
                "census_view_degraded": degraded,
                "evidence_view_truncated": evidence_truncated,
            },
        },
    }


def _miss_tree(*case_ids: str) -> dict:
    return {
        case_id: {
            "result": _result("verified_clean", candidates=0),
            "gold": True,
        }
        for case_id in case_ids
    }


def _fp_tree(*case_ids: str) -> dict:
    tree = {}
    for case_id in case_ids:
        tree[case_id] = {
            "result": {
                "state": "verified_error",
                "proof_adjudication": {
                    "census": [],
                    "certificates": [],
                    "defense_decisions": {"c1": {
                        "verdict": "upheld", "rationale": "r",
                    }},
                },
            },
            "gold": False,
        }
    return tree


def test_triage_priority_infrastructure_first() -> None:
    assert attribute_miss(_result(
        "verified_clean", degraded=True, candidates=3,
    )) == "view_degraded"
    assert attribute_miss(_result(
        "verified_clean", candidates=0,
    )) == "census_miss"
    assert attribute_miss(_result(
        "verified_clean", candidates=2, abstained=2,
    )) == "evidence_insufficient"
    assert attribute_miss(_result(
        "verified_clean", candidates=2, supported=2, overturned=2,
    )) == "defense_overturn"
    assert attribute_miss(_result(
        "verified_clean", candidates=2, supported=2,
    )) == "verification_reject"


def test_gate_requires_dual_boundary_and_support() -> None:
    tree = {**_miss_tree("BFCL/001.json", "BFCL/002.json")}
    good = {
        "title": "status overclaim",
        "trigger_features": ["structured_status_observations"],
        "directive": (
            "Describing a boolean status observation as a stronger "
            "confirmation it never returned is a deviation."
        ),
        "exclusion": (
            "the observation's own words already carry the stronger "
            "confirmation."
        ),
        "failure_direction": "miss",
        "case_evidence": [
            {"case_id": "BFCL/001.json"},
            {"case_id": "BFCL/002.json"},
        ],
    }
    accepted, rejections = gate_conventions(
        [good], run_tree=tree,
    )
    assert rejections == []
    assert accepted[0]["convention_id"] == "EC-01"

    one_sided = dict(good, exclusion="")
    _, rejections = gate_conventions(
        [one_sided], run_tree=tree,
    )
    assert rejections[0]["reason"] == (
        "exclusion boundary missing or over length"
    )

    single_case = dict(
        good,
        case_evidence=[{"case_id": "BFCL/001.json"}],
    )
    _, rejections = gate_conventions(
        [single_case], run_tree=tree,
    )
    assert rejections[0]["reason"] == (
        "mechanism supported by 1 case(s), needs 2"
    )

    bad_trigger = dict(good, trigger_features=["long_web_pages"])
    _, rejections = gate_conventions(
        [bad_trigger], run_tree=tree,
    )
    assert rejections[0]["reason"] == (
        "trigger_features empty or outside the vocabulary"
    )

    bad_direction = dict(good, failure_direction="sideways")
    _, rejections = gate_conventions(
        [bad_direction], run_tree=tree,
    )
    assert rejections[0]["reason"] == (
        "failure_direction 'sideways' invalid"
    )

    outside = dict(
        good,
        case_evidence=[
            {"case_id": "BFCL/001.json"},
            {"case_id": "Camel/999.json"},
        ],
    )
    _, rejections = gate_conventions(
        [outside], run_tree=tree,
    )
    assert "outside the worksheet" in rejections[0]["reason"]


def test_convention_genome_hash_is_content_addressed() -> None:
    entry = {
        "convention_id": "EC-01",
        "title": "t",
        "trigger_features": ["multi_tool_sequence"],
        "directive": "D.",
        "exclusion": "E.",
        "failure_direction": "miss",
        "case_evidence": [],
    }
    a = build_convention_genome([entry])
    b = build_convention_genome(a["conventions"])
    assert a["content_hash"] == b["content_hash"]
    b["conventions"][0]["directive"] = "Changed."
    assert build_convention_genome(
        b["conventions"],
    )["content_hash"] != a["content_hash"]
