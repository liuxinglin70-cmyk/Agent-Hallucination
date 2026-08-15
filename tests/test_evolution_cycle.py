"""Phase 2 self-evolution: proposal validation, gate semantics, ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sehd_rise2.evolution import cycle as cycle_mod
from sehd_rise2.evolution.proposal import (
    build_worksheet,
    validate_proposals,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _result(*, state, supported, upheld, claim="claim"):
    return {
        "state": state,
        "proof_adjudication": {
            "census": [
                {"candidate_id": f"c{i + 1}", "claim": claim}
                for i in range(max(len(supported), len(upheld)))
            ],
            "certificates": [
                {"candidate_id": cid, "verdict": "supported",
                 "rationale": "r"}
                for cid in supported
            ],
            "defense_decisions": {
                cid: {"verdict": verdict, "rationale": "r"}
                for cid, verdict in upheld.items()
            },
        },
    }


def _tree():
    return {
        "A/001.json": {
            "gold": False,
            "result": _result(
                state="verified_error", supported=["c1"],
                upheld={"c1": "upheld"},
            ),
        },
        "A/002.json": {
            "gold": False,
            "result": _result(
                state="verified_error", supported=["c1"],
                upheld={"c1": "upheld"},
            ),
        },
        "A/003.json": {
            "gold": True,
            "result": _result(
                state="verified_error", supported=["c1"],
                upheld={"c1": "upheld"},
            ),
        },
        "A/004.json": {
            "gold": True,
            "result": _result(
                state="verified_clean", supported=[], upheld={},
            ),
        },
    }


def _bindings(tree, certificate_class):
    return {
        (case_id, "c1"): {
            "certificate_class": certificate_class, "basis": "b",
        }
        for case_id in tree
        if (tree[case_id]["result"]["proof_adjudication"]
            .get("certificates"))
    }


GENOME_ITEMS = [
    {
        "policy_id": "L2-P2",
        "version": "v1",
        "target_certificate_class": "presentational_addition",
        "precondition": "p",
        "exclusions": [],
        "action": "withhold_conviction",
        "risk_boundary": {"statement": "s", "do_not_apply": ["x"]},
        "provenance": {},
    },
]


def _proposal(**overrides):
    proposal = {
        "kind": "new",
        "policy_id": "L2-P9",
        "title": "t",
        "target_certificate_class": "content_contradiction",
        "trigger": {
            "definition": "the certificate asserts a self-assessment",
            "must_not_match": [],
        },
        "action": "withhold_conviction",
        "hypothesis": {
            "statement": "m",
            "case_evidence": [
                {"case_id": "A/001.json", "observation": "o",
                 "policy_fit": "f"},
                {"case_id": "A/002.json", "observation": "o",
                 "policy_fit": "f"},
            ],
            "source_gap_names": ["strictness_miscalibration"],
        },
        "expected_effect": {
            "fp_cases": ["A/001.json", "A/002.json"],
            "fn_risk": "r",
            "tp_must_survive": [
                {"case_id": "A/003.json", "why": "w"},
            ],
        },
        "risk_boundary": {
            "statement": "s", "do_not_apply": ["self-assessments"],
        },
    }
    proposal.update(overrides)
    return proposal


def _worksheet():
    tree = _tree()
    return build_worksheet(
        run_tree=tree,
        bindings=_bindings(tree, "content_contradiction"),
        genome_items=GENOME_ITEMS,
        source="test",
    )


class TestWorksheet:
    def test_polarity_split(self):
        worksheet = _worksheet()
        assert [
            row["case_id"] for row in worksheet["wrongful_convictions"]
        ] == ["A/001.json", "A/002.json"]
        assert [
            row["case_id"] for row in worksheet["tp_sentinels"]
        ] == ["A/003.json"]
        assert [
            row["case_id"] for row in worksheet["recorded_misses"]
        ] == ["A/004.json"]

    def test_certificate_entries_carry_class(self):
        worksheet = _worksheet()
        entries = worksheet["wrongful_convictions"][0][
            "surviving_certificates"
        ]
        assert entries[0]["certificate_class"] == (
            "content_contradiction"
        )


class TestValidateProposals:
    def test_valid_birth_policy_passes(self):
        rejections = validate_proposals(
            [_proposal()], worksheet=_worksheet(),
            genome_items=GENOME_ITEMS,
        )
        assert rejections == []

    def test_single_case_support_rejected(self):
        proposal = _proposal()
        proposal["hypothesis"]["case_evidence"] = [
            proposal["hypothesis"]["case_evidence"][0],
        ]
        proposal["expected_effect"]["fp_cases"] = ["A/001.json"]
        rejections = validate_proposals(
            [proposal], worksheet=_worksheet(), genome_items=GENOME_ITEMS,
        )
        assert rejections and "needs 2" in rejections[0]["reason"]

    def test_case_id_in_trigger_rejected(self):
        proposal = _proposal()
        proposal["trigger"]["definition"] = (
            "fires on A/001.json exactly"
        )
        rejections = validate_proposals(
            [proposal], worksheet=_worksheet(), genome_items=GENOME_ITEMS,
        )
        assert any(
            "case id" in record["reason"] for record in rejections
        )

    def test_policy_free_class_rejected(self):
        proposal = _proposal(
            target_certificate_class="delivery_form",
        )
        rejections = validate_proposals(
            [proposal], worksheet=_worksheet(), genome_items=GENOME_ITEMS,
        )
        assert any(
            "policy-free" in record["reason"] for record in rejections
        )

    def test_unarmed_fp_case_rejected(self):
        worksheet = _worksheet()
        for row in worksheet["wrongful_convictions"]:
            for entry in row["surviving_certificates"]:
                entry["certificate_class"] = "expression_variance"
        rejections = validate_proposals(
            [_proposal()], worksheet=worksheet, genome_items=GENOME_ITEMS,
        )
        assert any(
            "without a surviving" in record["reason"]
            for record in rejections
        )

    def test_amend_requires_known_id(self):
        rejections = validate_proposals(
            [_proposal(kind="amend")], worksheet=_worksheet(),
            genome_items=GENOME_ITEMS,
        )
        assert any(
            "unknown policy" in record["reason"] for record in rejections
        )

    def test_retire_requires_evidence(self):
        proposal = _proposal(
            kind="retire", policy_id="L2-P2",
        )
        proposal["hypothesis"]["case_evidence"] = []
        rejections = validate_proposals(
            [proposal], worksheet=_worksheet(), genome_items=GENOME_ITEMS,
        )
        assert any(
            "without evidence" in record["reason"] for record in rejections
        )


class TestGate:
    def test_metrics_baseline_vs_withhold(self):
        tree = _tree()
        bindings = _bindings(tree, "content_contradiction")
        baseline = cycle_mod.gate_metrics(
            tree, cycle_mod.adjudicate(tree, bindings, []),
        )
        assert (baseline["fp"], baseline["tp"], baseline["fn"]) == (
            2, 1, 1,
        )
        withholding = [
            {**item, "target_certificate_class": "content_contradiction"}
            for item in GENOME_ITEMS
        ]
        gated = cycle_mod.gate_metrics(
            tree, cycle_mod.adjudicate(tree, bindings, withholding),
        )
        assert gated["fp"] == 0
        assert gated["tp_lost"] == ["A/003.json"]

    def test_corroboration_keeps_conviction(self):
        tree = _tree()
        bindings = _bindings(tree, "content_contradiction")
        bindings[("A/003.json", "c2")] = {
            "certificate_class": "content_contradiction",
            "basis": "b",
        }
        tree["A/003.json"]["result"]["proof_adjudication"]["census"].append(
            {"candidate_id": "c2", "claim": "claim"},
        )
        tree["A/003.json"]["result"]["proof_adjudication"][
            "certificates"
        ].append(
            {"candidate_id": "c2", "verdict": "supported",
             "rationale": "r"},
        )
        tree["A/003.json"]["result"]["proof_adjudication"][
            "defense_decisions"
        ]["c2"] = {"verdict": "upheld", "rationale": "r"}
        item = {
            **GENOME_ITEMS[0],
            "target_certificate_class": "expression_variance",
            "action": "require_corroborating_content_error",
        }
        audit = cycle_mod.adjudicate(tree, bindings, [item])
        assert audit["A/003.json"]["state"] == "verified_error"


class TestApplyProposals:
    def test_birth_amend_retire(self):
        amended = cycle_mod.apply_proposals(
            GENOME_ITEMS,
            [
                _proposal(),
                {
                    "kind": "amend", "policy_id": "L2-P2",
                    "target_certificate_class": "presentational_addition",
                    "trigger": {"definition": "tighter",
                                "must_not_match": []},
                    "action": "require_corroborating_content_error",
                    "hypothesis": {"case_evidence": []},
                },
            ],
            cycle=3, worksheet_path="w.json",
        )
        by_id = {item["policy_id"]: item for item in amended}
        assert by_id["L2-P2"]["version"] == "v2"
        assert by_id["L2-P2"]["action"] == (
            "require_corroborating_content_error"
        )
        assert by_id["L2-P9"]["version"] == "v1"
        retired = cycle_mod.apply_proposals(
            amended,
            [{"kind": "retire", "policy_id": "L2-P9",
              "hypothesis": {"case_evidence": []}}],
            cycle=4, worksheet_path="w.json",
        )
        assert [item["policy_id"] for item in retired] == ["L2-P2"]


class TestMergeGuards:
    def test_tree_overlap_rejected(self):
        with pytest.raises(ValueError, match="overlap"):
            cycle_mod.merge_trees(
                [REPO_ROOT / "runs/random100_20260815_142446"] * 2,
            )


def _write_run(root, case_id, *, gold, state, supported, upheld):
    case_dir = root / "cases" / case_id.replace("/", "__")
    case_dir.mkdir(parents=True)
    result = {
        "case_id": case_id,
        **_result(state=state, supported=supported, upheld=upheld),
    }
    (case_dir / "result.json").write_text(
        json.dumps(result), encoding="utf-8",
    )
    return case_id


def _write_gold(root, entries):
    (root / "eval_gold.json").write_text(
        json.dumps({
            "cases": [
                {"case": case_id, "gold": gold}
                for case_id, gold in entries
            ],
        }), encoding="utf-8",
    )


def _write_bindings(path, rows):
    path.write_text(
        json.dumps({
            "schema_version": "sehd2.certificate_bindings.runtime.v1",
            "bindings": [
                {
                    "case_id": case_id, "candidate_id": candidate_id,
                    "certificate_class": certificate_class,
                    "basis": "b", "binding_status": "classified",
                }
                for (case_id, candidate_id), certificate_class in (
                    rows.items()
                )
            ],
        }), encoding="utf-8",
    )


SYNTH_GENOME = {
    "schema_version": "sehd2.policy_genome.v1",
    "items": [
        {
            "policy_id": "L2-X", "version": "v1",
            "target_certificate_class": "expression_variance",
            "precondition": "p", "exclusions": [],
            "action": "withhold_conviction",
            "risk_boundary": {"statement": "s", "do_not_apply": ["x"]},
            "provenance": {},
        },
    ],
    "content_hash": "seed",
}


def _seed_cycle_env(tmp_path, monkeypatch):
    prior_run = tmp_path / "prior_run"
    _write_run(
        prior_run, "P/001.json", gold=True, state="verified_error",
        supported=["c1"], upheld={"c1": "upheld"},
    )
    _write_gold(prior_run, [("P/001.json", True)])
    prior_bindings = tmp_path / "prior_bindings.json"
    _write_bindings(
        prior_bindings, {("P/001.json", "c1"): "expression_variance"},
    )
    genome_file = tmp_path / "genome_seed.json"
    genome_file.write_text(json.dumps(SYNTH_GENOME), encoding="utf-8")
    monkeypatch.setattr(cycle_mod, "PHASE1_RUN", prior_run)
    monkeypatch.setattr(cycle_mod, "PHASE1_BINDINGS", prior_bindings)
    monkeypatch.setattr(cycle_mod, "PHASE1_GENOME", genome_file)
    library_dir = tmp_path / "library"
    cycle_mod.init_library(library_dir)
    return library_dir


def _seed_batch(tmp_path, *, batch_name="batch", content_only_tp=False):
    batch = tmp_path / batch_name
    entries = []
    gold_rows = []
    entries.append(_write_run(
        batch, "A/001.json", gold=False, state="verified_error",
        supported=["c1"], upheld={"c1": "upheld"},
    ))
    gold_rows.append(("A/001.json", False))
    entries.append(_write_run(
        batch, "A/002.json", gold=False, state="verified_error",
        supported=["c1"], upheld={"c1": "upheld"},
    ))
    gold_rows.append(("A/002.json", False))
    entries.append(_write_run(
        batch, "A/003.json", gold=True, state="verified_error",
        supported=["c1", "c2"], upheld={"c1": "upheld", "c2": "upheld"},
    ))
    gold_rows.append(("A/003.json", True))
    if content_only_tp:
        entries.append(_write_run(
            batch, "A/004.json", gold=True, state="verified_error",
            supported=["c1"], upheld={"c1": "upheld"},
        ))
        gold_rows.append(("A/004.json", True))
    _write_gold(batch, gold_rows)
    binding_rows = {
        ("A/001.json", "c1"): "content_contradiction",
        ("A/002.json", "c1"): "content_contradiction",
        ("A/003.json", "c1"): "content_contradiction",
        ("A/003.json", "c2"): "requirement_compliance",
    }
    if content_only_tp:
        binding_rows[("A/004.json", "c1")] = "content_contradiction"
    batch_bindings = tmp_path / f"{batch_name}_bindings.json"
    _write_bindings(batch_bindings, binding_rows)
    return batch, batch_bindings


class TestRunCycle:
    def test_promote_birth_and_retire(self, tmp_path, monkeypatch):
        library_dir = _seed_cycle_env(tmp_path, monkeypatch)
        batch, batch_bindings = _seed_batch(tmp_path)
        proposals = [
            _proposal(),
            {
                "kind": "retire", "policy_id": "L2-X",
                "title": "t",
                "hypothesis": {
                    "case_evidence": [
                        {"case_id": "P/001.json", "observation": "o",
                         "policy_fit": "f"},
                    ],
                },
            },
        ]
        monkeypatch.setattr(
            cycle_mod, "generate_proposals",
            lambda **kwargs: (proposals, {
                "model": "test", "raw_proposals": 2, "accepted": 2,
                "rejections": [],
            }),
        )
        result = cycle_mod.run_cycle(
            library_dir=library_dir, batch_run=batch,
            batch_bindings=batch_bindings,
            prior_runs=[], prior_bindings=[],
        )
        assert result["outcome"] == "promoted genomes/genome_v2.json"
        assert result["survivors"] == ["L2-P9", "L2-X"]
        assert result["gate"]["candidate"]["tp_lost"] == []
        assert result["gate"]["candidate"]["fp"] == 0
        library = cycle_mod.load_library(library_dir)
        assert library["policies"]["L2-P9"]["status"] == "active"
        assert library["policies"]["L2-X"]["status"] == "retired"
        genome = json.loads(
            (library_dir / "genomes/genome_v2.json").read_text(
                encoding="utf-8",
            ),
        )
        assert [item["policy_id"] for item in genome["items"]] == ["L2-P9"]

    def test_no_change_when_no_dominance(self, tmp_path, monkeypatch):
        library_dir = _seed_cycle_env(tmp_path, monkeypatch)
        batch, batch_bindings = _seed_batch(tmp_path, batch_name="b2")
        idle = _proposal(
            policy_id="L2-P8",
            target_certificate_class="requirement_compliance",
        )
        monkeypatch.setattr(
            cycle_mod, "generate_proposals",
            lambda **kwargs: ([idle], {
                "model": "test", "raw_proposals": 1, "accepted": 1,
                "rejections": [],
            }),
        )
        result = cycle_mod.run_cycle(
            library_dir=library_dir, batch_run=batch,
            batch_bindings=batch_bindings,
            prior_runs=[], prior_bindings=[],
        )
        assert result["outcome"] == "no_change"
        assert result["gate"]["current"]["fp"] > 0
        library = cycle_mod.load_library(library_dir)
        assert library["genomes"][-1]["file"] == "genomes/genome_v1.json"

    def test_tp_loss_culprit_dropped(self, tmp_path, monkeypatch):
        library_dir = _seed_cycle_env(tmp_path, monkeypatch)
        batch, batch_bindings = _seed_batch(
            tmp_path, batch_name="b3", content_only_tp=True,
        )
        reckless = _proposal(policy_id="L2-P7")
        monkeypatch.setattr(
            cycle_mod, "generate_proposals",
            lambda **kwargs: ([reckless], {
                "model": "test", "raw_proposals": 1, "accepted": 1,
                "rejections": [],
            }),
        )
        result = cycle_mod.run_cycle(
            library_dir=library_dir, batch_run=batch,
            batch_bindings=batch_bindings,
            prior_runs=[], prior_bindings=[],
        )
        assert result["outcome"] == "no_change"
        assert any(
            record["policy_id"] == "L2-P7"
            and "true-positive loss" in record["reason"]
            for record in result["dropped"]
        )
