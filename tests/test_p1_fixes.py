"""P1和P2修复的专项测试。"""

import json
import pytest
from detector.action_forecast import _constraints
from detector.defense import _parse_defense
from detector.adjudication import adjudicate
from detector.packet import build_packet


class TestP1Fix1TypeValidation:
    """P1-1: 新增字段类型验证，防止AttributeError。"""

    def test_source_type_null_raises_value_error(self):
        """source_type为null时应抛出ValueError而非AttributeError。"""
        prefix = {
            "question": "Report the sum of 2+2.",
            "events": [],
        }
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Must calculate sum",
                    "allowed_actions": ["Calculate"],
                    "forbidden_actions": ["Skip calculation"],
                    "evidence_ref": "question",
                    "quote": "sum of 2+2",
                    "source_type": None,  # 不合法类型
                }
            ]
        }
        with pytest.raises(ValueError, match="constraint source_type must be a string"):
            _constraints(parsed, prefix, view)

    def test_source_type_list_raises_value_error(self):
        """source_type为数组时应抛出ValueError。"""
        prefix = {
            "question": "Report the sum.",
            "events": [],
        }
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Must report",
                    "allowed_actions": ["Report"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Report",
                    "source_type": [],  # 不合法类型
                }
            ]
        }
        with pytest.raises(ValueError, match="constraint source_type must be a string"):
            _constraints(parsed, prefix, view)

    def test_source_type_int_raises_value_error(self):
        """source_type为整数时应抛出ValueError。"""
        prefix = {
            "question": "Calculate.",
            "events": [],
        }
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Calculate",
                    "allowed_actions": ["Calculate"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Calculate",
                    "source_type": 1,  # 不合法类型
                }
            ]
        }
        with pytest.raises(ValueError, match="constraint source_type must be a string"):
            _constraints(parsed, prefix, view)

    def test_applies_to_null_raises_value_error(self):
        """applies_to为null时应抛出ValueError。"""
        prefix = {"question": "Do it.", "events": []}
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Do it",
                    "allowed_actions": ["Do"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Do it",
                    "applies_to": None,
                }
            ]
        }
        with pytest.raises(ValueError, match="constraint applies_to must be a string"):
            _constraints(parsed, prefix, view)

    def test_time_constraint_list_raises_value_error(self):
        """time_constraint为数组时应抛出ValueError。"""
        prefix = {"question": "Complete task.", "events": []}
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Complete",
                    "allowed_actions": ["Complete"],
                    "forbidden_actions": ["Abandon"],
                    "evidence_ref": "question",
                    "quote": "Complete task",
                    "time_constraint": [],
                }
            ]
        }
        with pytest.raises(ValueError, match="constraint time_constraint must be a string"):
            _constraints(parsed, prefix, view)

    def test_valid_new_fields_accepted(self):
        """合法的新字段应正常通过。"""
        prefix = {"question": "Report the result.", "events": []}
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Report result",
                    "allowed_actions": ["Report"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Report the result",
                    "source_type": "user_request",
                    "applies_to": "current_action",
                    "time_constraint": "next_step_must",
                }
            ]
        }
        rows = _constraints(parsed, prefix, view)
        assert len(rows) == 1
        assert rows[0]["source_type"] == "user_request"
        assert rows[0]["applies_to"] == "current_action"
        assert rows[0]["time_constraint"] == "next_step_must"

    def test_omitted_new_fields_accepted(self):
        """省略新字段时应向后兼容。"""
        prefix = {"question": "Do task.", "events": []}
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Do task",
                    "allowed_actions": ["Do"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Do task",
                }
            ]
        }
        rows = _constraints(parsed, prefix, view)
        assert len(rows) == 1
        assert "source_type" not in rows[0]
        assert "applies_to" not in rows[0]
        assert "time_constraint" not in rows[0]


class TestP1Fix2ReasonFiltering:
    """P1-2: 移除关键词黑名单，信任有证据的refuted判决。"""

    def test_refuted_with_missing_keyword_and_evidence_accepted(self):
        """包含missing但有证据引用的refuted应被接受。"""
        parsed = {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": ["event_002"],
            "evidence_citations": [],
            "reason": "The alleged missing result is explicitly present: event_002 gives 4.",
        }
        result, error = _parse_defense(parsed)
        assert error == ""
        assert result["verdict"] == "refuted"
        assert result["evidence_refs"] == ["event_002"]

    def test_refuted_with_conflict_keyword_and_evidence_accepted(self):
        """包含conflict但有证据引用的refuted应被接受。"""
        parsed = {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": ["event_003"],
            "evidence_citations": [],
            "reason": "The alleged conflict does not exist: event_003 shows consistent results.",
        }
        result, error = _parse_defense(parsed)
        assert error == ""
        assert result["verdict"] == "refuted"

    def test_refuted_with_uncertain_keyword_and_evidence_accepted(self):
        """包含uncertain但有证据引用的refuted应被接受。"""
        parsed = {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": ["event_001"],
            "evidence_citations": [],
            "reason": "The uncertain claim in the allegation is proven false by event_001.",
        }
        result, error = _parse_defense(parsed)
        assert error == ""
        assert result["verdict"] == "refuted"

    def test_refuted_without_evidence_still_rejected(self):
        """没有证据引用的refuted仍应被拒绝（P1-1原有规则）。"""
        parsed = {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": [],
            "evidence_citations": [],
            "reason": "This looks wrong.",
        }
        result, error = _parse_defense(parsed)
        assert result is None
        assert "refuted requires at least one evidence reference" in error


class TestP1Fix3SourceBinding:
    """P1-3: 验证source_type与实际引用来源绑定。"""

    def test_user_request_must_reference_question(self):
        """source_type='user_request'必须引用question。"""
        prefix = {
            "question": "Report the sum.",
            "events": [
                {"event_id": "event_001", "role": "Planner", "content": "Plan to search."}
            ],
        }
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Search first",
                    "allowed_actions": ["Search"],
                    "forbidden_actions": ["Report without search"],
                    "evidence_ref": "event_001",  # Planner事件
                    "quote": "Plan to search",
                    "source_type": "user_request",  # 自报为用户请求
                }
            ]
        }
        with pytest.raises(ValueError, match="source_type='user_request' must cite question"):
            _constraints(parsed, prefix, view)

    def test_user_request_from_question_accepted(self):
        """source_type='user_request'引用question时应通过。"""
        prefix = {"question": "Report the sum.", "events": []}
        view = prefix.copy()
        parsed = {
            "constraints": [
                {
                    "requirement": "Report sum",
                    "allowed_actions": ["Report"],
                    "forbidden_actions": ["Skip"],
                    "evidence_ref": "question",
                    "quote": "Report the sum",
                    "source_type": "user_request",
                }
            ]
        }
        rows = _constraints(parsed, prefix, view)
        assert len(rows) == 1
        assert rows[0]["source_type"] == "user_request"
        assert rows[0]["evidence_ref"] == "question"
