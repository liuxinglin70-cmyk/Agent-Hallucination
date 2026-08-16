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
    worksheet = {"BFCL/001.json", "BFCL/002.json"}
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
        [good], worksheet_case_ids=worksheet,
    )
    assert rejections == []
    assert accepted[0]["convention_id"] == "EC-01"

    one_sided = dict(good, exclusion="")
    _, rejections = gate_conventions(
        [one_sided], worksheet_case_ids=worksheet,
    )
    assert rejections[0]["reason"] == (
        "exclusion boundary missing or over length"
    )

    single_case = dict(
        good,
        case_evidence=[{"case_id": "BFCL/001.json"}],
    )
    _, rejections = gate_conventions(
        [single_case], worksheet_case_ids=worksheet,
    )
    assert rejections[0]["reason"] == (
        "mechanism supported by 1 case(s), needs 2"
    )

    bad_trigger = dict(good, trigger_features=["long_web_pages"])
    _, rejections = gate_conventions(
        [bad_trigger], worksheet_case_ids=worksheet,
    )
    assert rejections[0]["reason"] == (
        "trigger_features empty or outside the vocabulary"
    )

    bad_direction = dict(good, failure_direction="sideways")
    _, rejections = gate_conventions(
        [bad_direction], worksheet_case_ids=worksheet,
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
        [outside], worksheet_case_ids=worksheet,
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


# --------------------------------------------------------------------------
# worksheet reproducibility filter + paired frozen-census G2
# --------------------------------------------------------------------------

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

import sehd_rise2.evolution.convention_evolution as ce  # noqa: E402


class _ScriptedDetector:
    """detect() answers from a per-case script keyed by (genome, frozen).

    Script entries: "unguided" -> state; "paired" -> guided frozen-census
    state; "discovery" -> guided full-detection state. The census of an
    unguided roll is one candidate c1.
    """

    def __init__(self, scripts: dict[str, dict[str, str]]) -> None:
        self.scripts = scripts

    def detect(self, record, *, case_id="case", evolved_conventions=None,
               frozen_census=None, **_kwargs):
        if evolved_conventions is None:
            state = self.scripts[case_id]["unguided"]
            census = [] if state == "verified_clean" else [
                {"candidate_id": "c1", "claim": "c",
                 "locus_event_id": "event_002",
                 "evidence_refs": ["event_002"]},
            ]
            return _shaped(state, census)
        if frozen_census is not None:
            state = self.scripts[case_id]["paired"]
            return _shaped(state, list(frozen_census))
        state = self.scripts[case_id].get("discovery", "verified_clean")
        return _shaped(state, [])


def _shaped(state: str, census: list) -> dict:
    return {
        "state": state,
        "proof_adjudication": {"census": census},
        "authority_failure_reasons": [],
    }


def _tree_entry(state: str, gold: bool, features: list[str]) -> dict:
    return {
        "result": {
            "state": state,
            "proof_adjudication": {
                "census": [], "certificates": [],
                "defense_decisions": {},
                "miss_triage_facts": {
                    "census_candidates": 0, "abstained_count": 0,
                    "supported_count": 0, "overturned_count": 0,
                    "census_view_degraded": False,
                    "evidence_view_truncated": False,
                },
            },
            "stage_audit": {"packet_features": features},
        },
        "gold": gold,
    }


def _install(monkeypatch: pytest.MonkeyPatch, detector) -> None:
    monkeypatch.setattr(ce, "Detector", lambda _config: detector)
    monkeypatch.setattr(ce, "load_config", lambda **_kw: {})


def _make_dataset(tmp_path, *case_ids) -> object:
    root = tmp_path / "ds"
    for case_id in case_ids:
        path = root / case_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    return root


def test_confirm_failures_keeps_only_reproduced(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    run_tree = {
        "A/1.json": _tree_entry("verified_clean", True, []),
        "A/2.json": _tree_entry("verified_clean", True, []),
        "A/3.json": _tree_entry("verified_error", False, []),
        "A/4.json": _tree_entry("verified_error", False, []),
    }
    detector = _ScriptedDetector({
        "A/1.json": {"unguided": "verified_clean"},   # miss reproduced
        "A/2.json": {"unguided": "verified_error"},   # self-recovered
        "A/3.json": {"unguided": "verified_error"},   # FP reproduced
        "A/4.json": {"unguided": "verified_clean"},   # FP gone
    })
    _install(monkeypatch, detector)
    misses, convictions, stats = ce.confirm_failures(
        run_tree=run_tree,
        dataset_root=_make_dataset(
            tmp_path, "A/1.json", "A/2.json", "A/3.json", "A/4.json",
        ),
    )
    assert [row["case_id"] for row in misses] == ["A/1.json"]
    assert [row["case_id"] for row in convictions] == ["A/3.json"]
    assert stats["misses_reproduced"] == 1
    assert stats["convictions_reproduced"] == 1


def _validation_convention() -> dict:
    return {
        "convention_id": "EC-01",
        "title": "t",
        "trigger_features": ["multi_tool_sequence"],
        "directive": "D.",
        "exclusion": "E.",
        "failure_direction": "miss",
        "case_evidence": [
            {"case_id": "A/1.json"}, {"case_id": "A/2.json"},
        ],
    }


def test_g2_paired_replay_repairs_only_paired_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    feature = ["multi_tool_sequence"]
    run_tree = {
        # miss target whose unguided re-roll stays clean: pairable
        "A/1.json": _tree_entry("verified_clean", True, feature),
        # miss target that self-recovers on the roll: never a repair
        "A/2.json": _tree_entry("verified_clean", True, feature),
        # sentinel: convicted, must stay convicted in both arms
        "A/5.json": _tree_entry("verified_error", True, feature),
        # trigger-matching clean case: must stay clean everywhere
        "A/6.json": _tree_entry("verified_clean", False, feature),
    }
    scripts = {
        "A/1.json": {"unguided": "verified_clean",
                     "paired": "verified_error",
                     "discovery": "verified_clean"},
        "A/2.json": {"unguided": "verified_error",
                     "paired": "verified_error"},
        "A/5.json": {"unguided": "verified_error",
                     "paired": "verified_error"},
        "A/6.json": {"unguided": "verified_clean",
                     "paired": "verified_clean",
                     "discovery": "verified_clean"},
    }
    _install(monkeypatch, _ScriptedDetector(scripts))
    report = ce.validate_conventions(
        run_tree=run_tree, conventions=[_validation_convention()],
        dataset_root=_make_dataset(
            tmp_path, "A/1.json", "A/2.json", "A/5.json", "A/6.json",
        ),
        out_dir=tmp_path,
    )
    metrics = report["per_convention"]["EC-01"]["metrics"]
    assert metrics["miss_repaired_paired"] == ["A/1.json"]
    assert metrics["self_recovered_targets"] == ["A/2.json"]
    assert metrics["sentinel_lost_paired"] == []
    assert metrics["clean_convicted_paired"] == []
    assert metrics["discovery_harm_reproduced"] == []
    assert report["deployable"] == ["EC-01"]


def test_g2_rejects_discovery_harm_even_with_paired_gain(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    feature = ["multi_tool_sequence"]
    run_tree = {
        "A/1.json": _tree_entry("verified_clean", True, feature),
        "A/2.json": _tree_entry("verified_clean", True, feature),
        "A/5.json": _tree_entry("verified_error", True, feature),
        "A/6.json": _tree_entry("verified_clean", False, feature),
    }
    scripts = {
        "A/1.json": {"unguided": "verified_clean",
                     "paired": "verified_error"},
        "A/2.json": {"unguided": "verified_clean",
                     "paired": "verified_clean"},
        "A/5.json": {"unguided": "verified_error",
                     "paired": "verified_error"},
        # clean case stays clean in the paired arm but the convention's
        # census injection convicts it in BOTH discovery re-rolls
        "A/6.json": {"unguided": "verified_clean",
                     "paired": "verified_clean",
                     "discovery": "verified_error"},
    }
    _install(monkeypatch, _ScriptedDetector(scripts))
    report = ce.validate_conventions(
        run_tree=run_tree, conventions=[_validation_convention()],
        dataset_root=_make_dataset(
            tmp_path, "A/1.json", "A/2.json", "A/5.json", "A/6.json",
        ),
        out_dir=tmp_path,
    )
    block = report["per_convention"]["EC-01"]
    assert block["metrics"]["discovery_harm_reproduced"] == ["A/6.json"]
    assert report["deployable"] == []
    assert block["gate"]["zero_reproduced_discovery_harm"] is False
