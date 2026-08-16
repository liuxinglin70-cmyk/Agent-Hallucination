"""Evolution artifact contract: fail-closed loading, shared conviction
semantics, runtime/offline parity."""

from __future__ import annotations

import json

import pytest

from sehd_rise2.detector.artifacts import (
    ARTIFACT_KINDS,
    apply_policy_actions,
    load_artifact_registry,
    policy_actions_of,
    prompt_directive_entries,
    registry_hash,
    strongest_actions,
    validate_artifact,
    validate_registry,
)
from sehd_rise2.detector.court import adjudicate, _StageAudit
from sehd_rise2.evolution.replay import replay_case


def _policy_artifact(**overrides) -> dict:
    artifact = {
        "artifact_id": "L2-X",
        "version": "v1",
        "kind": "adjudication_policy",
        "execution_point": "adjudication",
        "gap": {
            "category": "policy", "name": "expression_tolerance",
            "gap_type": "precision",
            "failure_signature": "wording-level certificates convict",
            "affected_stage": "adjudication",
            "expected_capability": "cannot convict",
        },
        "trigger": {"certificate_class": "expression_variance"},
        "intervention": {"action": "withhold_conviction", "exclusions": []},
        "capability_delta": {
            "before_capability": "convicts like any other",
            "after_capability": "cannot convict",
        },
        "provenance": {},
    }
    artifact.update(overrides)
    return artifact


def _prompt_artifact(**overrides) -> dict:
    artifact = {
        "artifact_id": "EC-01",
        "version": "v1",
        "kind": "prompt_directive",
        "execution_point": "stage_prompts",
        "gap": {
            "category": "verification", "name": "excuse_based_rejection",
            "gap_type": "recall",
            "failure_signature": "out-of-task excuses dismiss real gaps",
            "affected_stage": "verification",
            "expected_capability": "reject the excuse reading",
        },
        "trigger": {"packet_features": ["multi_tool_sequence"]},
        "intervention": {"directive": "D.", "exclusion": "E."},
        "capability_delta": {
            "before_capability": "cannot", "after_capability": "can",
        },
        "provenance": {},
    }
    artifact.update(overrides)
    return artifact


class TestContract:
    def test_kind_without_executor_cannot_exist(self):
        assert set(ARTIFACT_KINDS) == {
            "prompt_directive", "adjudication_policy",
        }

    def test_rule_only_artifact_rejected(self):
        artifact = _policy_artifact(
            capability_delta={"before_capability": "", "after_capability": ""},
        )
        with pytest.raises(ValueError, match="capability_delta"):
            validate_artifact(artifact)

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError, match="no executor"):
            validate_artifact(_policy_artifact(kind="census_probe"))

    def test_execution_point_must_match_kind(self):
        with pytest.raises(ValueError, match="must execute at"):
            validate_artifact(
                _policy_artifact(execution_point="stage_prompts"),
            )

    def test_prompt_artifact_requires_dual_boundary(self):
        intervention = {"directive": "D.", "exclusion": ""}
        with pytest.raises(ValueError, match="intervention.exclusion"):
            validate_artifact(
                _prompt_artifact(intervention=intervention),
            )

    def test_policy_free_class_rejected_at_load(self):
        trigger = {"certificate_class": "delivery_form"}
        with pytest.raises(ValueError, match="policy-free"):
            validate_artifact(_policy_artifact(trigger=trigger))

    def test_gap_stage_and_type_vocabulary(self):
        gap = dict(_policy_artifact()["gap"], gap_type="vibes")
        with pytest.raises(ValueError, match="gap_type"):
            validate_artifact(_policy_artifact(gap=gap))
        gap = dict(_policy_artifact()["gap"], affected_stage="router")
        with pytest.raises(ValueError, match="affected_stage"):
            validate_artifact(_policy_artifact(gap=gap))

    def test_registry_hash_mismatch_rejected(self, tmp_path):
        registry = {
            "schema_version": "sehd2.evolution_artifact.v1",
            "artifacts": [_policy_artifact()],
        }
        registry["content_hash"] = registry_hash(registry)
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        loaded = load_artifact_registry(path)
        assert loaded["content_hash"] == registry["content_hash"]
        registry["artifacts"][0]["version"] = "v9"
        path.write_text(json.dumps(registry), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match content"):
            load_artifact_registry(path)

    def test_real_phase1_registry_loads(self):
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "datasets/policy_candidates/v1/policy_artifacts.json"
        )
        if not path.exists():
            pytest.skip("phase 1 registry not generated")
        registry = load_artifact_registry(path)
        assert len(registry["artifacts"]) == 5


class TestRouting:
    def test_prompt_directive_entries_shape(self):
        entries = prompt_directive_entries([_prompt_artifact()])
        assert entries == [{
            "convention_id": "EC-01",
            "trigger_features": ["multi_tool_sequence"],
            "directive": "D.",
            "exclusion": "E.",
        }]

    def test_policy_actions_of_merges_withhold_dominant(self):
        actions = policy_actions_of([
            _policy_artifact(),
            _policy_artifact(
                artifact_id="L2-Y",
                intervention={
                    "action": "require_corroborating_content_error",
                    "exclusions": [],
                },
            ),
        ])
        assert actions == {
            "expression_variance": "withhold_conviction",
        }

    def test_strongest_actions_withhold_dominates(self):
        assert strongest_actions([
            ("x", "require_corroborating_content_error"),
            ("x", "withhold_conviction"),
            ("y", "withhold_conviction"),
        ]) == {"x": "withhold_conviction", "y": "withhold_conviction"}


class TestSharedSemantics:
    def test_withhold_removes_conviction(self):
        convicting, fired, _skipped = apply_policy_actions(
            [("c1", "expression_variance")],
            {"expression_variance": "withhold_conviction"},
        )
        assert convicting == []
        assert fired[0]["effect"] == "withheld"

    def test_corroboration_with_content_keeps_conviction(self):
        convicting, _fired, skipped = apply_policy_actions(
            [
                ("c1", "requirement_compliance"),
                ("c2", "content_contradiction"),
            ],
            {"requirement_compliance":
             "require_corroborating_content_error"},
        )
        assert convicting == ["c1", "c2"]
        assert skipped[0]["effect"] == "corroborated"

    def test_corroboration_sole_basis_removed(self):
        convicting, fired, _skipped = apply_policy_actions(
            [("c1", "requirement_compliance")],
            {"requirement_compliance":
             "require_corroborating_content_error"},
        )
        assert convicting == []
        assert fired[0]["effect"] == "sole_basis_removed"

    def test_unbound_certificate_keeps_baseline(self):
        convicting, fired, _skipped = apply_policy_actions(
            [("c1", None), ("c2", "expression_variance")],
            {"expression_variance": "withhold_conviction"},
        )
        assert convicting == ["c1"]
        assert [record["candidate_id"] for record in fired] == ["c2"]

    def test_unbound_fellow_certificate_blocks_corroboration(self):
        convicting, _fired, skipped = apply_policy_actions(
            [("c1", "requirement_compliance"), ("c2", None)],
            {"requirement_compliance":
             "require_corroborating_content_error"},
        )
        assert convicting == ["c1", "c2"]
        assert skipped[0]["effect"] == "corroboration_unverifiable"


def _audit_with_defense() -> _StageAudit:
    audit = _StageAudit()
    audit.census_ok = True
    audit.verification_ok = True
    audit.defense_ok = True
    audit.defense_needed = True
    audit.census_candidate_count = 2
    audit.certificate_count = 2
    audit.defense_view_meta = {"batches": 1}
    audit.verification_view_meta = {"batches": 1}
    return audit


def _court_facts():
    census = [
        {"candidate_id": "c1", "claim": "a", "locus_event_id": "e1",
         "evidence_refs": ["e1"]},
        {"candidate_id": "c2", "claim": "b", "locus_event_id": "e1",
         "evidence_refs": ["e1"]},
    ]
    certificates = [
        {"candidate_id": "c1", "verdict": "supported", "rationale": "r"},
        {"candidate_id": "c2", "verdict": "supported", "rationale": "r"},
    ]
    decisions = {
        "c1": {"verdict": "upheld", "rationale": "r"},
        "c2": {"verdict": "upheld", "rationale": "r"},
    }
    packet = {"events": [
        {"event_id": "e1", "position": 1, "role": "user", "content": "q"},
    ]}
    return census, certificates, decisions, packet


class TestRuntimeOfflineParity:
    """The runtime court and the offline replay share one semantics.

    Parity is on the (certificate_class, action, effect) triples; the
    runtime records additionally carry per-certificate ids, which the
    class-level offline view does not have.
    """

    @staticmethod
    def _effects(block) -> list[tuple[str, str, str]]:
        return [
            (
                record["certificate_class"], record["action"],
                record["effect"],
            )
            for record in block
        ]

    def _runtime_state(self, classes, actions):
        census, certificates, decisions, packet = _court_facts()
        proof = adjudicate(
            packet=packet, census=census, certificates=certificates,
            decisions=decisions, audit=_audit_with_defense(),
            runtime_valid=True, policy_actions=actions,
            certificate_classes=classes,
        )
        return proof

    def test_withhold_parity(self):
        classes = {"c1": "expression_variance", "c2": "content"}
        actions = {"expression_variance": "withhold_conviction"}
        proof = self._runtime_state(classes, actions)
        offline = replay_case(["expression_variance", "content"], actions)
        assert proof["state"] == offline["state"] == "verified_error"
        assert self._effects(
            proof["policy_evaluation"]["fired"],
        ) == self._effects(offline["policy_evaluation"]["fired"])

    def test_full_withhold_parity(self):
        classes = {
            "c1": "expression_variance", "c2": "expression_variance",
        }
        actions = {"expression_variance": "withhold_conviction"}
        proof = self._runtime_state(classes, actions)
        offline = replay_case(
            ["expression_variance", "expression_variance"], actions,
        )
        assert proof["state"] == offline["state"] == "verified_clean"
        assert len(proof["policy_evaluation"]["fired"]) == 2

    def test_corroboration_parity(self):
        classes = {
            "c1": "requirement_compliance",
            "c2": "content_contradiction",
        }
        actions = {
            "requirement_compliance":
                "require_corroborating_content_error",
        }
        proof = self._runtime_state(classes, actions)
        offline = replay_case(
            ["requirement_compliance", "content_contradiction"], actions,
        )
        assert proof["state"] == offline["state"] == "verified_error"
        assert self._effects(
            proof["policy_evaluation"]["skipped"],
        ) == self._effects(offline["policy_evaluation"]["skipped"])
