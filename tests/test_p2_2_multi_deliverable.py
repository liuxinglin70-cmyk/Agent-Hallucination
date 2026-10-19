"""P2-2修复专项测试：多交付修正的材料性条件统一"""

import pytest
from detector.semantics import UPGRADE_SYSTEM, JUDGE_SYSTEM, DEFENSE_SYSTEM


class TestP2Fix2MultiDeliverableMateriality:
    """测试P2-2修复：确保升级、判决、辩护三阶段对多交付修正的材料性条件一致。

    新规则：
    - UPGRADE_SYSTEM: P1-2 CAUSAL TEST REFINEMENT (第361-374行)
    - JUDGE_SYSTEM: P2-2 MULTI-DELIVERABLE REFINEMENT (第239-242行)
    - DEFENSE_SYSTEM: P2-2 MULTI-DELIVERABLE REFINEMENT (第508-514行)

    旧规则需替换/细化：
    - UPGRADE: 第357行"would every deliverable become correct"
    - JUDGE: 第236行"would make every deliverable correct"
    - DEFENSE: 第504行"would the affected deliverable(s) become correct"
    """

    def test_upgrade_has_multi_deliverable_refinement(self):
        """Upgrade提示词应包含P1-2 CAUSAL TEST REFINEMENT规则。"""
        # 查找P1-2标记和关键内容
        assert "P1-2 CAUSAL TEST REFINEMENT" in UPGRADE_SYSTEM, \
            "Upgrade应包含P1-2 CAUSAL TEST REFINEMENT标记"

        assert "independent deliverable" in UPGRADE_SYSTEM.lower(), \
            "Upgrade应明确讨论独立交付"

        assert "correcting this error alone" in UPGRADE_SYSTEM.lower() or \
               "does not require" in UPGRADE_SYSTEM.lower(), \
            "Upgrade应说明不要求修正单个错误就修正所有交付"

    def test_judge_has_multi_deliverable_refinement(self):
        """Judge提示词应包含P2-2 MULTI-DELIVERABLE REFINEMENT规则。"""
        assert "P2-2 MULTI-DELIVERABLE REFINEMENT" in JUDGE_SYSTEM, \
            "Judge应包含P2-2 MULTI-DELIVERABLE REFINEMENT标记"

        assert "independent deliverable" in JUDGE_SYSTEM.lower(), \
            "Judge应明确讨论独立交付"

    def test_defense_has_multi_deliverable_refinement(self):
        """Defense提示词应包含P2-2 MULTI-DELIVERABLE REFINEMENT规则。"""
        assert "P2-2 MULTI-DELIVERABLE REFINEMENT" in DEFENSE_SYSTEM, \
            "Defense应包含P2-2 MULTI-DELIVERABLE REFINEMENT标记"

        assert "independent deliverable" in DEFENSE_SYSTEM.lower(), \
            "Defense应明确讨论独立交付"

    def test_all_three_stages_reference_refinement(self):
        """确保所有三个阶段都包含多交付细化规则。"""
        # Upgrade有P1-2，Judge和Defense有P2-2
        has_upgrade = "CAUSAL TEST REFINEMENT" in UPGRADE_SYSTEM
        has_judge = "MULTI-DELIVERABLE REFINEMENT" in JUDGE_SYSTEM
        has_defense = "MULTI-DELIVERABLE REFINEMENT" in DEFENSE_SYSTEM

        assert has_upgrade and has_judge and has_defense, \
            f"所有阶段应包含细化规则：Upgrade={has_upgrade}, Judge={has_judge}, Defense={has_defense}"

    def test_refinement_rules_are_consistent(self):
        """三个阶段的细化规则应该语义一致（都讨论独立交付和材料性）。"""
        # 所有三个阶段都应讨论独立交付
        stages = {
            "UPGRADE": UPGRADE_SYSTEM,
            "JUDGE": JUDGE_SYSTEM,
            "DEFENSE": DEFENSE_SYSTEM
        }

        for stage_name, system_text in stages.items():
            assert "deliverable" in system_text.lower(), \
                f"{stage_name}应讨论deliverable"

            # 应该提及独立性或分别处理
            assert ("independent" in system_text.lower() or
                    "separate" in system_text.lower() or
                    "per deliverable" in system_text.lower()), \
                f"{stage_name}应讨论独立交付或分别处理"
