"""P2-1修复专项测试：追踪记录应比较原始触发ID与最终定罪ID"""

import pytest
from detector.adjudication import adjudicate
from detector.packet import build_packet


class TestP2Fix1TriggerIdentity:
    """测试P2-1修复：convicted_same_candidate应比较原始触发ID和最终定罪ID，
    而不是比较最终ID是否在upgraded_candidates中。"""

    def test_different_candidates(self):
        """当原始触发ID与最终定罪ID不同时，应识别为不同候选。"""
        packet = build_packet({
            "question": "计算2+2",
            "history": [
                {"event_id": "event_001", "type": "message", "role": "assistant", "content": "2+2=5"}
            ]
        })

        judge_result = {"ok": True, "parsed": {"is_hallucination": True, "hallucination_step": 1}}
        judge_status = {"state": "ok"}

        recall_result = {
            "ok": True,
            "candidates": [],
            "action_candidates": [
                {"candidate_id": "c_original", "claim": "Missing action"}
            ]
        }
        recall_status = {"state": "ok"}

        upgrade_result = {
            "ok": True,
            "parsed": {"causal_repairs": []},
            "upgraded_candidates": [
                {"candidate_id": "c_new", "claim": "New violation"}
            ],
        }
        upgrade_status = {"state": "ok"}

        # 构建hearing：source=recall_upgrade，candidate_id=c_new（最终定罪ID）
        hearings = [
            {
                "source": "recall_upgrade",
                "candidate_id": "c_new",
                "step": 1,  # P2-1: 必需字段，alleged_step
                "defense_result": {
                    "parsed": {
                        "verdict": "substantiated",
                        "confirmed_step": 1,
                        "evidence_refs": ["event_001"],
                        "evidence_citations": [],
                        "conflict_note": "",
                        "reason": "Confirmed"
                    }
                },
                "defense_status": {"state": "ok"},
                "tracer_result": {
                    "parsed": {
                        "step": 1,
                        "quote": "5",
                        "referent": "4",
                        "evidence_refs": [],
                        "reason": "Wrong"
                    }
                },
                "tracer_status": {"state": "ok"},
            }
        ]

        recall_trigger_metadata = {
            "triggered_by_action_candidates": True,
            "action_candidates_count": 1,
            "original_action_candidate_ids": ["c_original"],  # 原始触发ID
        }

        proof = adjudicate(
            packet=packet,
            judge_result=judge_result,
            judge_status=judge_status,
            recall_result=recall_result,
            recall_status=recall_status,
            upgrade_result=upgrade_result,
            upgrade_status=upgrade_status,
            tracer_result=None,
            tracer_status=None,
            defense_result=None,
            defense_status=None,
            guard_actions=[],
            mechanical_evidence=None,
            hearings=hearings,
            hearing_facts=[],
            predicate_conviction_verdicts={},
            recall_trigger_metadata=recall_trigger_metadata,
        )

        assert proof["recall_trigger_record"] is not None
        trigger_vs_conviction = proof["recall_trigger_record"]["trigger_vs_conviction"]

        # 原始触发ID=c_original，最终定罪ID=c_new → 不同
        assert trigger_vs_conviction["convicted_same_candidate"] is False
        assert trigger_vs_conviction["conviction_explanation"] == "扩大搜索后发现的新指控"

    def test_same_candidate(self):
        """当原始触发ID与最终定罪ID相同时，应识别为同一候选。"""
        packet = build_packet({
            "question": "计算2+2",
            "history": [
                {"event_id": "event_001", "type": "message", "role": "assistant", "content": "2+2=5"}
            ]
        })

        judge_result = {"ok": True, "parsed": {"is_hallucination": True, "hallucination_step": 1}}
        judge_status = {"state": "ok"}

        recall_result = {
            "ok": True,
            "candidates": [],
            "action_candidates": [
                {"candidate_id": "c_same", "claim": "Missing action"}
            ]
        }
        recall_status = {"state": "ok"}

        upgrade_result = {
            "ok": True,
            "parsed": {"causal_repairs": []},
            "upgraded_candidates": [
                {"candidate_id": "c_same", "claim": "Missing action"}
            ],
        }
        upgrade_status = {"state": "ok"}

        # hearing的candidate_id与原始触发ID相同
        hearings = [
            {
                "source": "recall_upgrade",
                "candidate_id": "c_same",
                "step": 1,  # P2-1: 必需字段
                "defense_result": {
                    "parsed": {
                        "verdict": "substantiated",
                        "confirmed_step": 1,
                        "evidence_refs": ["event_001"],
                        "evidence_citations": [],
                        "conflict_note": "",
                        "reason": "Confirmed"
                    }
                },
                "defense_status": {"state": "ok"},
                "tracer_result": {
                    "parsed": {
                        "step": 1,
                        "quote": "5",
                        "referent": "4",
                        "evidence_refs": [],
                        "reason": "Wrong"
                    }
                },
                "tracer_status": {"state": "ok"},
            }
        ]

        recall_trigger_metadata = {
            "triggered_by_action_candidates": True,
            "action_candidates_count": 1,
            "original_action_candidate_ids": ["c_same"],
        }

        proof = adjudicate(
            packet=packet,
            judge_result=judge_result,
            judge_status=judge_status,
            recall_result=recall_result,
            recall_status=recall_status,
            upgrade_result=upgrade_result,
            upgrade_status=upgrade_status,
            tracer_result=None,
            tracer_status=None,
            defense_result=None,
            defense_status=None,
            guard_actions=[],
            mechanical_evidence=None,
            hearings=hearings,
            hearing_facts=[],
            predicate_conviction_verdicts={},
            recall_trigger_metadata=recall_trigger_metadata,
        )

        assert proof["recall_trigger_record"] is not None
        trigger_vs_conviction = proof["recall_trigger_record"]["trigger_vs_conviction"]

        # 原始触发ID=c_same，最终定罪ID=c_same → 相同
        assert trigger_vs_conviction["convicted_same_candidate"] is True
        assert trigger_vs_conviction["conviction_explanation"] == "原触发候选通过验证并被定罪"
