"""Deployment-plan loading: disk registry -> compiled plan -> executors.

Covers the full load path a round actually uses: a persisted registry
file is read, compiled once into a DeployPlan, and every artifact kind
reaches its execution surface — prompt directives render into stage
prompts, evidence predicates run over census candidates, candidate
generators extend the census, adjudication policies apply at the
deterministic court — with the compile cache keyed on content hash.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from sehd_rise2.detector.api import Detector  # noqa: E402
from sehd_rise2.detector.artifacts import registry_hash  # noqa: E402
from sehd_rise2.detector import artifacts as artifacts_module  # noqa: E402
from sehd_rise2.detector import court as court_module  # noqa: E402
from sehd_rise2.detector.deploy import (  # noqa: E402
    DeployPlan,
    compile_deploy_plan,
)

SCHEMA = "sehd2.evolution_artifact.v1"

PROMPT_ARTIFACT = {
    "artifact_id": "EC-01", "version": "v1",
    "kind": "prompt_directive", "execution_point": "stage_prompts",
    "gap": {
        "category": "verification", "name": "evidence_locus_mismatch",
        "gap_type": "precision", "failure_signature": "s",
        "affected_stage": "verification", "expected_capability": "c",
    },
    "trigger": {"packet_features": ["task_specifies_values"]},
    "intervention": {
        "directive": "Treat every later tool-argument restatement as "
                     "suspect until verified against the call site.",
        "exclusion": "NOT WHEN the restatement matches the original "
                     "argument exactly.",
    },
    "capability_delta": {"before_capability": "b", "after_capability": "a"},
}

POLICY_ARTIFACT = {
    "artifact_id": "L2-X", "version": "v1",
    "kind": "adjudication_policy", "execution_point": "adjudication",
    "gap": {
        "category": "policy", "name": "defense_boundary_drift",
        "gap_type": "precision", "failure_signature": "s",
        "affected_stage": "defense", "expected_capability": "c",
    },
    "trigger": {"certificate_class": "expression_variance"},
    "intervention": {"action": "withhold_conviction"},
    "capability_delta": {"before_capability": "b", "after_capability": "a"},
}


def _predicate(artifact_id: str) -> dict:
    return {
        "artifact_id": artifact_id, "version": "v1",
        "kind": "evidence_predicate",
        "execution_point": "verification_predicates",
        "gap": {
            "category": "verification", "name": "exactness_boundary",
            "gap_type": "boundary", "failure_signature": "s",
            "affected_stage": "verification",
            "expected_capability": "check required referents",
        },
        "trigger": {"packet_features": ["task_specifies_values"]},
        "capability": {
            "name": "required_entity_set_preserved",
            "failure_mode": "false positive on legitimate paraphrase",
            "input": {
                "candidate_claim": "text", "task_question": "text",
            },
            "output": {"verdict": {"enum": [
                "equivalent", "not_equivalent", "insufficient",
            ]}},
            "guarantees": ["entity_set_preserved"],
        },
        "implementation": {"runner": "deterministic"},
        "capability_delta": {
            "capability_id": artifact_id, "runner": "deterministic",
            "before_capability": "b", "after_capability": "a",
        },
    }


def _generator(artifact_id: str) -> dict:
    return {
        "artifact_id": artifact_id, "version": "v1",
        "kind": "candidate_generator", "execution_point": "census_extension",
        "gap": {
            "category": "observation", "name": "missing_required_action",
            "gap_type": "recall", "failure_signature": "s",
            "affected_stage": "census",
            "expected_capability": "find missed candidates",
        },
        "trigger": {"packet_features": ["task_specifies_values"]},
        "capability": {
            "name": "missing_action_generator",
            "failure_mode": "missed required actions",
            "input": {"packet_view": "text"},
            "output": {"candidates": {
                "candidate_id": "text", "claim": "text",
                "locus_event_id": "text", "evidence_refs": ["text"],
            }},
            "guarantees": ["closed_candidate_contract"],
        },
        "implementation": {
            "runner": "llm_structured", "system": "find missing actions",
        },
        "capability_delta": {
            "capability_id": artifact_id, "runner": "llm_structured",
            "before_capability": "b", "after_capability": "a",
        },
    }


def _registry(*artifacts: dict) -> dict:
    payload = {"schema_version": SCHEMA, "artifacts": [
        copy.deepcopy(a) for a in artifacts
    ]}
    payload["content_hash"] = registry_hash(payload)
    return payload


def _record():
    return {
        "question": "perform the required 'lockDoors' action with code 1234",
        "history": [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "done, doors locked"},
        ],
    }


class TestCompile:
    def test_plan_buckets_all_four_kinds(self):
        plan = compile_deploy_plan(_registry(
            PROMPT_ARTIFACT, POLICY_ARTIFACT,
            _predicate("CAP-EV1"), _generator("CAP-GE1"),
        ))
        assert isinstance(plan, DeployPlan)
        assert [e["convention_id"] for e in plan.prompts] == ["EC-01"]
        assert [a["artifact_id"] for a in plan.predicates] == ["CAP-EV1"]
        assert [a["artifact_id"] for a in plan.generators] == ["CAP-GE1"]
        assert plan.policy_actions == {
            "expression_variance": "withhold_conviction",
        }
        assert plan.policy_ids == ["L2-X"]

    def test_none_registry_compiles_empty_plan(self):
        plan = compile_deploy_plan(None)
        assert plan is None or plan.prompts == []

    def test_invalid_registry_fails_closed(self):
        broken = _registry(PROMPT_ARTIFACT)
        broken["artifacts"][0]["kind"] = "not_a_kind"
        broken["content_hash"] = registry_hash(broken)
        with pytest.raises(ValueError):
            compile_deploy_plan(broken)

    def test_hash_keyed_cache_returns_same_object(self):
        registry = _registry(PROMPT_ARTIFACT)
        first = compile_deploy_plan(registry)
        second = compile_deploy_plan(registry)
        assert first is second

    def test_changed_hash_recompiles(self):
        first = compile_deploy_plan(_registry(PROMPT_ARTIFACT))
        other = compile_deploy_plan(_registry(dict(PROMPT_ARTIFACT,
                                                   artifact_id="EC-02")))
        assert first is not other
        assert [e["convention_id"] for e in other.prompts] == ["EC-02"]

    def test_trigger_routing_by_feature_set(self):
        plan = compile_deploy_plan(_registry(
            PROMPT_ARTIFACT, _predicate("CAP-EV1"), _generator("CAP-GE1"),
        ))
        matched = {"task_specifies_values"}
        unmatched = {"retrieval_tools"}
        assert len(plan.routed_prompts(matched)) == 1
        assert plan.routed_prompts(unmatched) == []
        assert len(plan.routed_predicates(matched)) == 1
        assert plan.routed_predicates(unmatched) == []
        assert len(plan.routed_generators(matched)) == 1
        assert plan.routed_generators(unmatched) == []


class TestEndToEndLoad:
    def test_disk_registry_reaches_all_execution_surfaces(
        self, monkeypatch, tmp_path,
    ):
        """The round's real load path: file -> detect -> all four planes."""
        registry = _registry(
            PROMPT_ARTIFACT, POLICY_ARTIFACT,
            _predicate("CAP-EV1"), _generator("CAP-GE1"),
        )
        disk = tmp_path / "registry.json"
        disk.write_text(json.dumps(registry), encoding="utf-8")
        loaded = json.loads(disk.read_text(encoding="utf-8"))

        seen: dict = {}

        def fake_provider(*, route, system, user, purpose, **_kwargs):
            route_name = str(route.get("_route_name") or purpose)
            if route_name == "census":
                seen["census_system"] = system
                return {"candidates": [{
                    "candidate_id": "c1", "claim": "doors locked",
                    "locus_event_id": "event_002",
                    "evidence_refs": ["event_002"],
                }]}, {"attempts": 1}
            if route_name == "verification":
                seen["verification_system"] = system
                seen["verification_user"] = user
                return {"certificates": [{
                    "candidate_id": "c1", "verdict": "supported",
                    "rationale": "r",
                }]}, {"attempts": 1}
            if route_name == "defense":
                seen["defense_system"] = system
                return {"decisions": [{
                    "candidate_id": "c1", "verdict": "upheld",
                    "rationale": "r",
                }]}, {"attempts": 1}
            if route_name.startswith("candidate_generator:"):
                seen["generator_system"] = system
                return {"candidates": [{
                    "claim": "missing lockDoors verification",
                    "locus_event_id": "event_002",
                    "evidence_refs": ["event_002"],
                }]}, {"attempts": 1}
            if route_name == "binder":
                seen["binder"] = True
                return {"bindings": [{
                    "candidate_id": "c1",
                    "class": "expression_variance",
                    "confidence": "high",
                    "evidence_basis": "prose tolerance boundary",
                }]}, {"attempts": 1}
            raise AssertionError(f"unexpected route {route_name!r}")

        monkeypatch.setattr(court_module, "provider_call", fake_provider)
        monkeypatch.setattr(
            artifacts_module, "provider_call", fake_provider,
        )
        detector = Detector({
            "census_route": {}, "verification_route": {},
            "defense_route": {},
        })
        result = detector.detect(
            _record(), case_id="case/1.json", artifacts=loaded,
        )
        proof = result["proof_adjudication"]
        # prompt plane: directive text reached the census prompt
        assert "Treat every later tool-argument restatement" in seen[
            "census_system"
        ]
        # generator plane: extension stage executed and its id is active
        assert "generator_system" in seen
        assert "CAP-GE1" in proof["active_artifacts"]
        # predicate plane: deterministic runner over every candidate
        produced = [
            record for record in proof["capability_trace"]
            if record["capability_id"] == "CAP-EV1"
        ]
        assert produced, "predicate never ran over the merged census"
        # policy plane: binder ran, expression_variance withheld
        assert seen.get("binder")
        fired = proof.get("policy_evaluation", {}).get("fired") or []
        assert any(
            record.get("action") == "withhold_conviction"
            for record in fired
        )
        assert result["provenance"]["artifacts_hash"] == (
            loaded["content_hash"]
        )
