"""Convention evolution: triage, G1 dual-boundary gate, genome identity."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sehd_rise2.evolution.convention_evolution import (  # noqa: E402
    attribute_miss,
    build_convention_registry,
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
    attributions = {
        "BFCL/001.json": "census_miss", "BFCL/002.json": "census_miss",
    }
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
        "gap_name": "hidden_contradiction",
        "failure_signature": "boolean statuses restated as confirmations",
        "expected_capability": (
            "detect status observations restated as stronger claims"
        ),
        "capability_delta": {
            "before_capability": "cannot detect the overclaim",
            "after_capability": "detects the overclaim",
        },
        "case_evidence": [
            {"case_id": "BFCL/001.json"},
            {"case_id": "BFCL/002.json"},
        ],
    }
    accepted, rejections = gate_conventions(
        [good], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections == []
    assert accepted[0]["convention_id"] == "EC-01"
    assert accepted[0]["case_attribution"] == "census_miss"

    one_sided = dict(good, exclusion="")
    _, rejections = gate_conventions(
        [one_sided], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections[0]["reason"] == (
        "exclusion boundary missing or over length"
    )

    rule_only = dict(
        good, capability_delta={"before_capability": "", "after_capability": ""},
    )
    _, rejections = gate_conventions(
        [rule_only], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections[0]["reason"] == "capability_delta needs both sides"

    unknown_gap = dict(good, gap_name="vibe_mismatch")
    _, rejections = gate_conventions(
        [unknown_gap], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert "outside the deployable taxonomy" in rejections[0]["reason"]

    # Diagnostic-only gap categories never spawn detector capabilities,
    # so their names cannot pass G1 even though the taxonomy knows them.
    diagnostic_gap = dict(good, gap_name="locus_vs_manifestation")
    _, rejections = gate_conventions(
        [diagnostic_gap], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert "outside the deployable taxonomy" in rejections[0]["reason"]

    single_case = dict(
        good,
        case_evidence=[{"case_id": "BFCL/001.json"}],
    )
    _, rejections = gate_conventions(
        [single_case], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections[0]["reason"] == (
        "mechanism supported by 1 case(s), needs 2"
    )

    bad_trigger = dict(good, trigger_features=["long_web_pages"])
    _, rejections = gate_conventions(
        [bad_trigger], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections[0]["reason"] == (
        "trigger_features empty or outside the vocabulary"
    )

    bad_direction = dict(good, failure_direction="sideways")
    _, rejections = gate_conventions(
        [bad_direction], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert rejections[0]["reason"] == (
        "failure_direction 'sideways' invalid"
    )

    view_degraded = dict(good, case_evidence=[
        {"case_id": "BFCL/001.json"}, {"case_id": "BFCL/002.json"},
    ])
    _, rejections = gate_conventions(
        [view_degraded], worksheet_case_ids=worksheet,
        case_attributions={
            "BFCL/001.json": "view_degraded",
            "BFCL/002.json": "view_degraded",
        },
    )
    assert "infrastructural" in rejections[0]["reason"]

    outside = dict(
        good,
        case_evidence=[
            {"case_id": "BFCL/001.json"},
            {"case_id": "Camel/999.json"},
        ],
    )
    _, rejections = gate_conventions(
        [outside], worksheet_case_ids=worksheet,
        case_attributions=attributions,
    )
    assert "outside the worksheet" in rejections[0]["reason"]


def _full_convention() -> dict:
    return {
        "convention_id": "EC-01",
        "title": "t",
        "trigger_features": ["multi_tool_sequence"],
        "directive": "D.",
        "exclusion": "E.",
        "failure_direction": "miss",
        "gap_name": "hidden_contradiction",
        "failure_signature": "s",
        "expected_capability": "c",
        "capability_delta": {
            "before_capability": "b", "after_capability": "a",
        },
        "case_attribution": "census_miss",
        "case_evidence": [],
    }


def test_convention_registry_hash_is_content_addressed() -> None:
    a = build_convention_registry([_full_convention()])
    assert a["schema_version"] == "sehd2.evolution_artifact.v1"
    artifact = a["artifacts"][0]
    assert artifact["kind"] == "prompt_directive"
    assert artifact["execution_point"] == "stage_prompts"
    assert artifact["gap"]["category"] == "observation"
    assert artifact["gap"]["gap_type"] == "recall"
    assert artifact["gap"]["affected_stage"] == "census"
    b = build_convention_registry([_full_convention()])
    assert a["content_hash"] == b["content_hash"]
    changed = _full_convention()
    changed["directive"] = "Changed."
    assert build_convention_registry(
        [changed],
    )["content_hash"] != a["content_hash"]


def test_convention_library_merges_namespaced_and_idempotentently(
    tmp_path,
) -> None:
    from sehd_rise2.detector.artifacts import validate_registry
    from sehd_rise2.evolution.convention_evolution import (
        merge_round_registry,
    )

    registry = build_convention_registry([_full_convention()])
    library = tmp_path / "registry.json"

    merged = merge_round_registry(library, registry, 2)
    assert [a["artifact_id"] for a in merged["artifacts"]] == ["R2-EC-01"]
    # Re-running the same round replaces its block instead of duplicating.
    merged_again = merge_round_registry(library, registry, 2)
    assert len(merged_again["artifacts"]) == 1
    # Later rounds accumulate alongside earlier ones.
    third = merge_round_registry(library, registry, 3)
    assert [a["artifact_id"] for a in third["artifacts"]] == [
        "R2-EC-01", "R3-EC-01",
    ]
    validate_registry(third)


# --------------------------------------------------------------------------
# worksheet reproducibility filter + paired frozen-census G2
# --------------------------------------------------------------------------

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

import sehd_rise2.evolution.convention_evolution as ce  # noqa: E402


_CALIBRATION = {
    "schema_version": "sehd2.gate_calibration.v1",
    "test_retest_flip_rate": None,
    "convention_g2": {
        "max_paired_sentinel_loss": 0,
        "max_paired_clean_conviction": 0,
        "max_reproduced_discovery_harm": 0,
        "min_paired_net_gain": 1,
    },
    "policy_cycle": {
        "max_added_fn_vs_baseline": 1, "max_tp_lost": 0,
    },
}


class _ScriptedDetector:
    """detect() answers from a per-case script keyed by (registry, frozen).

    Script entries: "unguided" -> state; "paired" -> guided frozen-census
    state; "discovery" -> guided full-detection state. The census of an
    unguided roll is one candidate c1. Guided arms report the artifact
    as engaged (``active_artifacts``) unless the detector was built with
    ``active=False`` — the G2a engagement-evidence source.
    """

    def __init__(
        self, scripts: dict[str, dict[str, str]], *, active: bool = True,
    ) -> None:
        self.scripts = scripts
        self.active = active

    def detect(self, record, *, case_id="case", artifacts=None,
               frozen_census=None, **_kwargs):
        if artifacts is None:
            state = self.scripts[case_id]["unguided"]
            census = [] if state == "verified_clean" else [
                {"candidate_id": "c1", "claim": "c",
                 "locus_event_id": "event_002",
                 "evidence_refs": ["event_002"]},
            ]
            return _shaped(state, census)
        if frozen_census is not None:
            state = self.scripts[case_id]["paired"]
            return _shaped(state, list(frozen_census), active=self.active)
        state = self.scripts[case_id].get("discovery", "verified_clean")
        return _shaped(state, [], active=self.active)


def _shaped(state: str, census: list, *, active: bool = True) -> dict:
    proof: dict = {"census": census}
    if active:
        proof["active_artifacts"] = ["EC-01"]
    return {
        "state": state,
        "proof_adjudication": proof,
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
        "gap_name": "hidden_contradiction",
        "failure_signature": "s",
        "expected_capability": "c",
        "capability_delta": {
            "before_capability": "b", "after_capability": "a",
        },
        "case_attribution": "verification_reject",
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
        out_dir=tmp_path, calibration=_CALIBRATION,
    )
    metrics = report["per_convention"]["EC-01"]["metrics"]
    assert metrics["miss_repaired_paired"] == ["A/1.json"]
    assert metrics["self_recovered_targets"] == ["A/2.json"]
    assert metrics["engaged_target_cases"] == ["A/1.json", "A/2.json"]
    assert metrics["sentinel_lost_paired"] == []
    assert metrics["clean_convicted_paired"] == []
    assert metrics["discovery_harm_reproduced"] == []
    assert report["per_convention"]["EC-01"]["gate"][
        "engaged_on_targets"
    ] is True
    assert report["deployable"] == ["EC-01"]


def test_g2a_rejects_unengaged_artifact_despite_repair(
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
        "A/6.json": {"unguided": "verified_clean",
                     "paired": "verified_clean"},
    }
    # the guided arm never reports the artifact as active: the paired
    # repair exists, but no engagement evidence does — an unengaged
    # artifact cannot deploy
    _install(monkeypatch, _ScriptedDetector(scripts, active=False))
    report = ce.validate_conventions(
        run_tree=run_tree, conventions=[_validation_convention()],
        dataset_root=_make_dataset(
            tmp_path, "A/1.json", "A/2.json", "A/5.json", "A/6.json",
        ),
        out_dir=tmp_path, calibration=_CALIBRATION,
    )
    block = report["per_convention"]["EC-01"]
    assert block["metrics"]["miss_repaired_paired"] == ["A/1.json"]
    assert block["metrics"]["engaged_target_cases"] == []
    assert block["gate"]["engaged_on_targets"] is False
    assert block["gate"]["paired_improvement"] is True
    assert report["deployable"] == []


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
        out_dir=tmp_path, calibration=_CALIBRATION,
    )
    block = report["per_convention"]["EC-01"]
    assert block["metrics"]["discovery_harm_reproduced"] == ["A/6.json"]
    assert report["deployable"] == []
    assert block["gate"]["discovery_harm_within_tolerance"] is False
