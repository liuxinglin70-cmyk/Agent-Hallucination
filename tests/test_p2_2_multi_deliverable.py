"""Per-error materiality must be shared across the three decision stages."""

from detector.semantics import CAUSALITY_LAW, UPGRADE_SYSTEM, JUDGE_SYSTEM, DEFENSE_SYSTEM


class TestP2Fix2MultiDeliverableMateriality:
    """One material error need not explain every independent delivery error."""

    def test_upgrade_has_multi_deliverable_refinement(self):
        assert CAUSALITY_LAW in UPGRADE_SYSTEM
        assert "actual effect on a required" in UPGRADE_SYSTEM
        assert "would every deliverable become correct" not in UPGRADE_SYSTEM

    def test_judge_has_multi_deliverable_refinement(self):
        assert CAUSALITY_LAW in JUDGE_SYSTEM
        assert "earliest one affecting any required deliverable" in JUDGE_SYSTEM
        assert "would make every deliverable correct" not in JUDGE_SYSTEM

    def test_defense_has_multi_deliverable_refinement(self):
        assert CAUSALITY_LAW in DEFENSE_SYSTEM
        assert "actual effect on a" in DEFENSE_SYSTEM
        assert "dependent copies" in DEFENSE_SYSTEM

    def test_all_three_stages_reference_refinement(self):
        for prompt in (UPGRADE_SYSTEM, JUDGE_SYSTEM, DEFENSE_SYSTEM):
            assert prompt.count(CAUSALITY_LAW) == 1
            assert "P1-2 CAUSAL TEST REFINEMENT" not in prompt
            assert "P2-2 MULTI-DELIVERABLE REFINEMENT" not in prompt

    def test_refinement_rules_are_consistent(self):
        stages = {
            "UPGRADE": UPGRADE_SYSTEM,
            "JUDGE": JUDGE_SYSTEM,
            "DEFENSE": DEFENSE_SYSTEM
        }

        for stage_name, system_text in stages.items():
            normalized = " ".join(system_text.split())
            assert "affects at least one required deliverable" in normalized, stage_name
            assert "not whether one edit repairs all independent errors" in normalized, stage_name
