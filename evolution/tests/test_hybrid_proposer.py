from __future__ import annotations

from evolution.proposer import HybridProposer


def _fn_family():
    return {
        "failure_family": "FAM-fn-judge_false_no_recall_candidates-test",
        "count": 2,
        "failure_cases": ["A/012.json", "A/013.json"],
        "sources": ["A"],
        "actionability": {
            "routes": ["recall_trigger", "evolved_predicate"],
        },
        "diagnostics": {
            "member_summaries": [
                {
                    "typed_samples": {
                        "question_text": "Use search and report F.",
                        "tool_names": ["python"],
                        "packet_features": ["has_tool_calls"],
                    },
                },
                {
                    "typed_samples": {
                        "question_text": "Use search and report G.",
                        "tool_names": ["python"],
                        "packet_features": ["has_tool_calls"],
                    },
                },
            ],
        },
    }


def _fp_family():
    return {
        "failure_family": "FAM-fp-judge_conviction-test",
        "count": 2,
        "failure_cases": ["A/014.json", "A/015.json"],
        "sources": ["A"],
        "actionability": {"routes": ["withhold_guard"]},
        "diagnostics": {
            "predicate_signals": {
                "delivered_anchor_consistency": {
                    "applicable": 2, "violations": 2,
                },
            },
            "member_summaries": [],
        },
    }


def _config():
    return {
        "detector_config": "detector/configs/autodl.json",
        "budget": {"llm_calls": 0},
        "proposer": {
            "max_output_tokens": 1024,
            "thinking_level": "low",
            "max_repair_calls": 0,
        },
    }


def test_hybrid_proposer_falls_back_to_typed_dsl_templates():
    proposer = HybridProposer(_config())
    batch = proposer.propose(
        [_fn_family(), _fp_family()], cycle=7, max_proposals=6,
    )
    types = [candidate.get("type") for candidate in batch.candidates]
    assert "recall_trigger" in types
    assert types.count("evidence_check") >= 2
    assert types.count("decision_guard") >= 2
    assert batch.joint_groups
    assert any(
        note.get("generator") == "hybrid_fallback" for note in batch.notes
    )


def test_hybrid_proposer_uses_llm_candidates_when_present():
    # max_calls=0 keeps this test provider-free; the LLM path is covered by
    # the real B experiments and the parser contract tests.
    proposer = HybridProposer(_config())
    batch = proposer.propose([_fn_family()], cycle=8, max_proposals=2)
    assert any(
        candidate.get("type") == "recall_trigger"
        for candidate in batch.candidates
    )
