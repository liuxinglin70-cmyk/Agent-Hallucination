"""Rebuilt capability framework: contracts, gates, runtime traces."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from sehd_rise2.detector import court as court_module  # noqa: E402
from sehd_rise2.detector.artifacts import (  # noqa: E402
    ARTIFACT_KINDS,
    validate_artifact,
    validate_registry,
)
from sehd_rise2.detector.packet import build_packet  # noqa: E402
from sehd_rise2.evolution.capability_evolution import (  # noqa: E402
    directive_redundancy,
    gate_proposals,
    validate_proposal,
)
from sehd_rise2.evolution.artifact_validation import _correct_state  # noqa: E402
from sehd_rise2.evolution.router import (  # noqa: E402
    _fact_route,
    build_route_rows,
    route_failures,
)


def _diagnosis(case_id: str, failure_class: str) -> dict:
    return {
        "schema_version": "sehd2.failure_route.v1",
        "case_id": case_id,
        "gold": True,
        "recorded_state": "verified_clean",
        "reroll_states": ["verified_clean", "verified_clean"],
        "attribution": "census_miss",
        "failure_class": failure_class,
        "allowed_channels": sorted({
            "missing_capability": ["candidate_generator", "evidence_predicate"],
            "execution_failure": ["stage_fix"],
        }[failure_class]),
        "reason": "r",
    }


def _component_proposal(
    *, kind: str = "evidence_predicate", failure_class: str = "missing_capability",
    directive: str = "Always require the exact required entity set.",
) -> dict:
    artifact_id = f"CAP-{kind[:2].upper()}1"
    runner = (
        "deterministic" if kind == "evidence_predicate" else "llm_structured"
    )
    proposal = {
        "artifact_id": artifact_id,
        "kind": kind,
        "title": "component",
        "failure_class": failure_class,
        "gap": {
            "category": "verification" if kind == "evidence_predicate" else "observation",
            "name": "exactness_boundary" if kind == "evidence_predicate" else "missing_required_action",
            "gap_type": "boundary" if kind == "evidence_predicate" else "recall",
            "failure_signature": "required referents were not checked",
            "affected_stage": "verification" if kind == "evidence_predicate" else "census",
            "expected_capability": "check required referents",
        },
        "trigger": {"packet_features": ["task_specifies_values"]},
        "capability": {
            "name": "required_entity_set_preserved",
            "failure_mode": "false positive when the final answer legitimately paraphrases a required referent",
            "input": {"candidate_claim": "text", "task_question": "text"},
            "output": {"verdict": {"enum": ["equivalent", "not_equivalent", "insufficient"]}},
            "guarantees": ["entity_set_preserved"],
        },
        "implementation": {
            "runner": runner,
            "system": "return candidates" if kind != "evidence_predicate" else "",
        },
        "capability_delta": {
            "capability_id": artifact_id,
            "runner": runner,
            "before_capability": "cannot check", "after_capability": "checks",
        },
        "counterfactual_tests": [
            {
                "id": "CF-A", "expect": "equivalent",
                "input": {
                    "candidate_claim": "same",
                    "task_question": "'Alpha'",
                    "packet": {
                        "question": "'Alpha'",
                        "events": [{
                            "event_id": "event_001",
                            "position": 1,
                            "role": "assistant",
                            "content": "Alpha",
                            "tool_interactions": [],
                        }],
                    },
                },
            },
            {
                "id": "CF-B", "expect": "not_equivalent",
                "input": {
                    "candidate_claim": "other",
                    "task_question": "'Alpha'",
                    "packet": {
                        "question": "'Alpha'",
                        "events": [{
                            "event_id": "event_001",
                            "position": 1,
                            "role": "assistant",
                            "content": "other",
                            "tool_interactions": [],
                        }],
                    },
                },
            },
        ],
        "case_evidence": [
            {"case_id": "A/1.json", "observation": "o"},
            {"case_id": "A/2.json", "observation": "o"},
        ],
    }
    if kind == "prompt_directive":
        proposal["intervention"] = {"directive": directive, "exclusion": "never when meaning changes"}
    return proposal


class TestRouter:
    @staticmethod
    def _dataset(tmp_path: Path, case_id: str, gold: bool) -> Path:
        path = tmp_path / case_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "is_hallucination": gold,
            "hallucination_reason": "fabricated value",
        }), encoding="utf-8")
        return tmp_path

    @staticmethod
    def _tree_entry(gold: bool, state: str, case_id: str = "fam/1.json"):
        return {"gold": gold, "result": {
            "case_id": case_id,
            "state": state,
            "stage_audit": {"packet_features": ["numeric_observations"]},
            "proof_adjudication": {"census": [
                {"candidate_id": "c1", "claim": "claim"},
            ]},
        }}

    def test_mechanical_unstable_and_infrastructure(self, tmp_path):
        root = self._dataset(tmp_path, "fam/1.json", True)
        rows = build_route_rows(
            run_tree={"fam/1.json": self._tree_entry(
                True, "verified_clean",
            )},
            rerolled={"fam/1.json": ["verified_error"]},
            attribution_by_case={"fam/1.json": "census_miss"},
            dataset_root=root,
            unstable_cases=["fam/1.json"],
        )
        routed, meta = route_failures(rows)
        assert routed[0]["failure_class"] == "unstable"
        assert meta["mechanical"] == 1 and meta.get("routed", 0) == 0

        root = self._dataset(tmp_path, "fam/2.json", True)
        rows = build_route_rows(
            run_tree={"fam/2.json": self._tree_entry(
                True, "unresolved", "fam/2.json",
            )},
            rerolled={"fam/2.json": ["unresolved"]},
            attribution_by_case={"fam/2.json": "view_degraded"},
            dataset_root=root,
        )
        routed, _ = route_failures(rows)
        assert routed[0]["failure_class"] == "infrastructure"

    def test_swing_facts_reach_the_evidence_pack(self, tmp_path):
        root = self._dataset(tmp_path, "fam/1.json", True)
        rows = build_route_rows(
            run_tree={"fam/1.json": self._tree_entry(
                True, "verified_clean",
            )},
            rerolled={"fam/1.json": ["verified_error"]},
            attribution_by_case={"fam/1.json": "census_miss"},
            dataset_root=root,
            unstable_cases=["fam/1.json"],
        )
        swing = rows[0]["evidence"]["swing"]
        assert swing["recorded_state"] == "verified_clean"
        assert swing["reroll_states"] == ["verified_error"]
        assert "three-roll majority" in swing["note"]


class TestArtifactContracts:
    def test_component_kinds_have_executors(self):
        assert ARTIFACT_KINDS == {
            "prompt_directive": "stage_prompts",
            "adjudication_policy": "adjudication",
            "evidence_predicate": "verification_predicates",
            "candidate_generator": "census_extension",
        }

    def test_evidence_predicate_validates(self):
        from sehd_rise2.evolution.capability_evolution import build_artifact
        proposal = _component_proposal(kind="evidence_predicate")
        artifact = build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )
        validate_artifact(artifact)
        registry = {
            "schema_version": "sehd2.evolution_artifact.v1",
            "artifacts": [artifact],
        }
        from sehd_rise2.detector.artifacts import registry_hash
        registry["content_hash"] = registry_hash(registry)
        assert validate_registry(registry)[0]["kind"] == "evidence_predicate"

    def test_evidence_predicate_rejects_open_inputs(self):
        from sehd_rise2.evolution.capability_evolution import build_artifact
        proposal = _component_proposal(kind="evidence_predicate")
        proposal["capability"]["input"] = {"gold_label": "text"}
        artifact = build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )
        with pytest.raises(ValueError, match="closed predicate vocabulary"):
            validate_artifact(artifact)

    def test_candidate_generator_validates(self):
        from sehd_rise2.evolution.capability_evolution import build_artifact
        proposal = _component_proposal(kind="candidate_generator")
        proposal["capability"] = {
            "name": "missing_required_action_probe",
            "failure_mode": "false candidate on a complete trajectory",
            "input": {"packet_view": "text"},
            "output": {"candidates": {
                "candidate_id": "text", "claim": "text",
                "locus_event_id": "text", "evidence_refs": ["text"],
            }},
            "guarantees": ["closed_candidate_contract"],
        }
        proposal["counterfactual_tests"] = [
            {
                "id": "CF-A", "input": {"packet": {"events": []}},
                "expect": {"contains_claim": "missing required action"},
            },
            {
                "id": "CF-B", "input": {"packet": {"events": []}},
                "expect": {"candidate_count": 0},
            },
        ]
        artifact = build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )
        validate_artifact(artifact)

    def test_candidate_generator_rejects_recall_wrong_stage(self):
        from sehd_rise2.evolution.capability_evolution import build_artifact
        proposal = _component_proposal(kind="candidate_generator")
        proposal["gap"]["affected_stage"] = "verification"
        artifact = build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )
        with pytest.raises(ValueError, match="must affect census"):
            validate_artifact(artifact)


class TestProposalGate:
    def _diagnoses(self):
        return {
            "A/1.json": _diagnosis("A/1.json", "missing_capability"),
            "A/2.json": _diagnosis("A/2.json", "missing_capability"),
        }

    def test_missing_capability_accepts_executable_component(self):
        accepted, rejections = gate_proposals(
            [_component_proposal(kind="evidence_predicate")],
            diagnoses=self._diagnoses(),
        )
        assert rejections == []
        assert accepted[0]["kind"] == "evidence_predicate"

    def test_missing_capability_rejects_prompt_patch(self):
        proposal = _component_proposal(kind="prompt_directive")
        accepted, rejections = gate_proposals(
            [proposal], diagnoses=self._diagnoses(),
        )
        assert accepted == []
        assert any("not allowed" in reason["reason"] for reason in rejections)

    def test_execution_failure_rejects_redundant_prompt(self):
        diagnoses = {
            "A/1.json": _diagnosis("A/1.json", "execution_failure"),
            "A/2.json": _diagnosis("A/2.json", "execution_failure"),
        }
        proposal = _component_proposal(
            kind="prompt_directive", failure_class="execution_failure",
            directive=(
                "In prose reports, formatting variations and equivalent "
                "rephrasing that preserve the referent are not deviations."
            ),
        )
        accepted, rejections = gate_proposals(
            [proposal], diagnoses=diagnoses,
            baseline_rule_texts=[
                "In prose reports, formatting variations and equivalent "
                "rephrasing that preserve the referent are not deviations.",
            ],
        )
        assert accepted == []
        assert any("repeats" in reason["reason"] for reason in rejections)

    def test_redundancy_overlap_is_textual(self):
        assert directive_redundancy(
            "formatting variations preserve the referent",
            ["formatting variations that preserve the referent"],
        ) > 0.5

    def test_predicate_runner_restricted_by_round(self):
        proposal = _component_proposal(kind="evidence_predicate")
        proposal["implementation"] = {
            "runner": "llm_structured", "system": "verdict json",
        }
        proposal["capability_delta"]["runner"] = "llm_structured"
        accepted, rejections = gate_proposals(
            [proposal], diagnoses=self._diagnoses(),
            allowed_predicate_runners=["deterministic"],
        )
        assert accepted == []
        assert any("round-allowed" in r["reason"] for r in rejections)

    def test_predicate_runner_default_allows_both(self):
        accepted, rejections = gate_proposals(
            [_component_proposal(kind="evidence_predicate")],
            diagnoses=self._diagnoses(),
        )
        assert rejections == []
        assert accepted[0]["implementation"]["runner"] == "deterministic"


class TestFlipPolarity:
    def test_correct_state_polarity(self):
        assert _correct_state("verified_error", True)
        assert not _correct_state("verified_clean", True)
        assert _correct_state("verified_clean", False)
        assert _correct_state("evidence_insufficient", False)
        assert not _correct_state("verified_error", False)


class TestCourtCapabilityExecution:
    def _record(self):
        return {
            "question": "Return 'Alpha' and 'Beta' in any wording.",
            "history": [
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "Alpha and Beta are returned."},
            ],
        }

    def _predicate_artifact(self) -> dict:
        proposal = _component_proposal(kind="evidence_predicate")
        proposal["artifact_id"] = "CAP-ENTITY"
        proposal["trigger"] = {"packet_features": ["task_specifies_values"]}
        from sehd_rise2.evolution.capability_evolution import build_artifact
        return build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )

    def test_evidence_predicate_trace_and_prompt_evidence(self, monkeypatch):
        artifact = self._predicate_artifact()
        seen: dict = {}

        def fake_provider(*, route, system, user, purpose, **_kwargs):
            if purpose == "census":
                seen["census"] = True
                return {"candidates": [{
                    "candidate_id": "c1",
                    "claim": "the agent changed the required entities",
                    "locus_event_id": "event_002",
                    "evidence_refs": ["event_002"],
                }]}, {"attempts": 1}
            if purpose == "verification":
                seen["verification_system"] = system
                seen["verification_user"] = user
                return {"certificates": [{
                    "candidate_id": "c1", "verdict": "supported",
                    "rationale": "r",
                }]}, {"attempts": 1}
            if purpose == "defense":
                seen["defense_user"] = user
                return {"decisions": [{
                    "candidate_id": "c1", "verdict": "upheld",
                    "rationale": "r",
                }]}, {"attempts": 1}
            raise AssertionError(f"unexpected purpose {purpose!r}")

        monkeypatch.setattr(court_module, "provider_call", fake_provider)
        packet = build_packet(self._record())
        proof, _providers, _audit = court_module.run_court(
            packet,
            {"census_route": {}, "verification_route": {}, "defense_route": {}},
            artifacts=[artifact],
        )
        assert proof["state"] == "verified_error"
        assert proof["capability_trace"][0]["capability_id"] == "CAP-ENTITY"
        assert proof["capability_trace"][0]["status"] == "produced"
        assert "CAPABILITY EVIDENCE" in seen["verification_user"]
        assert "CAPABILITY EVIDENCE" in seen["defense_user"]

    def test_candidate_generator_extends_census(self, monkeypatch):
        proposal = _component_proposal(kind="candidate_generator")
        proposal["artifact_id"] = "GEN-MISSING-ACTION"
        from sehd_rise2.evolution.capability_evolution import build_artifact
        artifact = build_artifact(
            proposal, case_evidence=["A/1.json", "A/2.json"], active_by_id={},
        )
        artifact["capability"] = {
            "name": "missing_required_action_probe",
            "failure_mode": "false candidate on a complete trajectory",
            "input": {"packet_view": "text"},
            "output": {"candidates": {
                "candidate_id": "text", "claim": "text",
                "locus_event_id": "text", "evidence_refs": ["text"],
            }},
            "guarantees": ["closed_candidate_contract"],
        }
        artifact["counterfactual_tests"] = [
            {"id": "CF-A", "input": {"packet": {"events": []}},
             "expect": {"contains_claim": "missing"}},
            {"id": "CF-B", "input": {"packet": {"events": []}},
             "expect": {"candidate_count": 0}},
        ]
        artifact["implementation"] = {
            "runner": "llm_structured",
            "system": "return candidates",
        }
        artifact["capability_delta"]["capability_id"] = "GEN-MISSING-ACTION"
        validate_artifact(artifact)

        def fake_provider(*, route, system, user, purpose, **_kwargs):
            if purpose == "census":
                return {"candidates": []}, {"attempts": 1}
            if purpose.startswith("candidate_generator:GEN"):
                return {"candidates": [{
                    "claim": "the agent omitted the required lockDoors call",
                    "locus_event_id": "event_002",
                    "evidence_refs": ["event_001"],
                }]}, {"attempts": 1}
            if purpose == "verification":
                return {"certificates": [{
                    "candidate_id": "g1", "verdict": "supported",
                    "rationale": "r",
                }]}, {"attempts": 1}
            if purpose == "defense":
                return {"decisions": [{
                    "candidate_id": "g1", "verdict": "upheld",
                    "rationale": "r",
                }]}, {"attempts": 1}
            raise AssertionError(f"unexpected purpose {purpose!r}")

        monkeypatch.setattr(court_module, "provider_call", fake_provider)
        artifact["trigger"] = {"packet_features": ["task_specifies_values"]}
        packet = build_packet({
            "question": "perform the required 'lockDoors' action",
            "history": [
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "done"},
            ],
        })
        proof, _providers, _audit = court_module.run_court(
            packet,
            {"census_route": {}, "verification_route": {}, "defense_route": {}},
            artifacts=[artifact],
        )
        assert proof["state"] == "verified_error"
        assert proof["census"][0]["candidate_id"] == "g1"
        assert proof["candidate_generator_trace"][0]["status"] == "produced"
        assert "GEN-MISSING-ACTION" in proof["active_artifacts"]



def _two_diagnoses() -> list[dict]:
    return [
        _diagnosis("A/1.json", "missing_capability"),
        _diagnosis("A/2.json", "missing_capability"),
    ]


class TestRevisionLoop:
    """Bounded drafting: rejection feedback in, capped attempts out."""

    @staticmethod
    def _scripted_proposer(script):
        import sehd_rise2.evolution.capability_evolution as ce

        calls: list[dict] = []

        def fake_call(user_payload):
            calls.append(dict(user_payload))
            reply = script[len(calls) - 1]
            return reply, {"attempt": len(calls)}

        return ce, fake_call, calls

    def test_revision_stops_when_clean(self, monkeypatch):
        ce, fake_call, calls = self._scripted_proposer([
            [_component_proposal()],
        ])
        monkeypatch.setattr(ce, "_call_proposer", fake_call)
        accepted, meta = ce.generate_proposals(
            diagnoses=_two_diagnoses(),
        )
        assert len(calls) == 1
        assert len(accepted) == 1
        assert meta["revision_attempts"] == 1
        assert meta["revision"][-1]["g1_accepted"] == 1

    def test_rejection_feedback_revises_then_stops(self, monkeypatch):
        ce, fake_call, calls = self._scripted_proposer([
            [{"artifact_id": "", "kind": "unknown"}],
            [_component_proposal()],
        ])
        monkeypatch.setattr(ce, "_call_proposer", fake_call)
        accepted, meta = ce.generate_proposals(
            diagnoses=_two_diagnoses(),
        )
        assert len(calls) == 2
        assert "revision_feedback" in calls[1]
        assert calls[1]["revision_feedback"]["g1_rejections"]
        assert len(accepted) == 1

    def test_revision_budget_is_bounded(self, monkeypatch):
        ce, fake_call, calls = self._scripted_proposer([
            [{"artifact_id": "", "kind": "unknown"}],
            [{"artifact_id": "", "kind": "unknown"}],
            [{"artifact_id": "", "kind": "unknown"}],
        ])
        monkeypatch.setattr(ce, "_call_proposer", fake_call)
        accepted, meta = ce.generate_proposals(
            diagnoses=_two_diagnoses(),
            revision_rounds=2,
        )
        assert len(calls) == 3
        assert accepted == []
        assert meta["revision_attempts"] == 3

    def test_revision_budget_exhausted_returns_last(self, monkeypatch):
        ce, fake_call, calls = self._scripted_proposer([
            [{"artifact_id": "", "kind": "unknown"}],
            [{"artifact_id": "", "kind": "unknown"}],
            [{"artifact_id": "", "kind": "unknown"}],
        ])
        monkeypatch.setattr(ce, "_call_proposer", fake_call)
        accepted, meta = ce.generate_proposals(
            diagnoses=_two_diagnoses(),
            revision_rounds=2,
        )
        assert accepted == []
        assert meta["revision_attempts"] == 3
        assert len(meta["revision"]) == 3

class TestRouterInitialization:
    def test_memory_file_exists_and_valid(self):
        from sehd_rise2.evolution.router import MEMORY_SCHEMA, load_memory
        memory = load_memory()
        assert memory["schema_version"] == MEMORY_SCHEMA
        assert memory["per_class"] == {}

    def test_fact_route_cold_start_heuristic(self):
        assert _fact_route({
            "component_routing": {
                "G1": {"kind": "candidate_generator",
                       "routed": True, "produced": False},
            },
        }) == "not_triggered"
        assert _fact_route({
            "component_routing": {
                "P1": {"kind": "evidence_predicate",
                       "routed": True, "produced": True},
            },
        }) == "decision_conflict"
        assert _fact_route({"component_routing": {}}) == (
            "missing_capability"
        )

    def test_provider_failure_degrades_to_fact_route(self, monkeypatch):
        import sehd_rise2.evolution.router as router_module
        from sehd_rise2.detector.provider import ProviderError

        def boom(*_args, **_kwargs):
            raise ProviderError(
                "failure_router", "provider down",
                error_type="provider_error",
            )

        monkeypatch.setattr(router_module, "provider_call", boom)
        rows = [{
            "schema_version": "sehd2.failure_route.v1",
            "case_id": "A/1.json", "gold": True,
            "recorded_state": "verified_clean",
            "attribution": "census_miss", "failure_class": "",
            "component_routing": {}, "census_candidates": 0,
            "evidence": {},
        }]
        routed, meta = router_module.route_failures(rows)
        assert routed[0]["failure_class"] == "missing_capability"
        assert meta["degraded_to_fact_route"] is True
