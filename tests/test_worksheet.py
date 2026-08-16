"""Worksheet primitives: attribution, reproduction, evidence packs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sehd_rise2.evolution.worksheet import (  # noqa: E402
    attribute_miss,
    evidence_pack,
)


def _result(**facts) -> dict:
    return {"proof_adjudication": {"miss_triage_facts": facts}}


def test_miss_attribution_priority():
    assert attribute_miss(
        _result(census_view_degraded=True, census_candidates=0),
    ) == "view_degraded"
    assert attribute_miss(
        _result(census_view_degraded=False, census_candidates=0),
    ) == "census_miss"
    assert attribute_miss(
        _result(census_candidates=2, abstained_count=1),
    ) == "evidence_insufficient"
    assert attribute_miss(
        _result(census_candidates=2, overturned_count=1),
    ) == "defense_overturn"
    assert attribute_miss(_result(census_candidates=2)) == (
        "verification_reject"
    )


def _entry(gold: bool, case_id: str = "fam/1.json") -> dict:
    proof: dict = {
        "census": [
            {"candidate_id": "c1", "claim": "claim text one"},
        ],
    }
    if not gold:
        proof["defense_decisions"] = {
            "c1": {"verdict": "upheld", "rationale": "convict rationale"},
        }
    return {
        "result": {
            "case_id": case_id,
            "state": "verified_clean" if gold else "verified_error",
            "stage_audit": {"packet_features": ["numeric_observations"]},
            "proof_adjudication": proof,
        },
        "gold": gold,
    }


def test_evidence_pack_miss_carries_gold_error(tmp_path):
    case = tmp_path / "fam" / "1.json"
    case.parent.mkdir()
    case.write_text(
        '{"is_hallucination": true, "hallucination_step": "3",'
        ' "hallucination_reason": "fabricated sensor value"}',
        encoding="utf-8",
    )
    pack = evidence_pack(_entry(True), tmp_path)
    assert pack["gold_error"].startswith("(step 3)")
    assert "fabricated sensor value" in pack["gold_error"]
    assert pack["census_claims"] == ["claim text one"]
    assert pack["packet_features"] == ["numeric_observations"]
    assert "upheld_certificates" not in pack


def test_evidence_pack_conviction_carries_certificates(tmp_path):
    pack = evidence_pack(_entry(False), tmp_path)
    assert pack["upheld_certificates"] == [{
        "claim": "claim text one",
        "defense_rationale": "convict rationale",
    }]
    assert "gold_error" not in pack


def test_evidence_pack_never_carries_labels(tmp_path):
    case = tmp_path / "fam" / "1.json"
    case.parent.mkdir()
    case.write_text(
        '{"is_hallucination": true, "hallucination_reason": "r"}',
        encoding="utf-8",
    )
    pack = evidence_pack(_entry(True), tmp_path)
    assert "is_hallucination" not in pack
    assert "gold" not in pack
