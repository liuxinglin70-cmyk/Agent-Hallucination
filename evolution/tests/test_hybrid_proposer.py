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


def _llm_config():
    config = _config()
    config["budget"] = {"llm_calls": 2}
    return config


def _capture_prompt(monkeypatch):
    """Stub the provider and capture the drafting prompt it receives."""
    captured = {}

    def fake_provider_call(*, route, system, user, purpose):
        captured["system"] = system
        captured["user"] = user
        return {"interventions": []}, {"purpose": purpose, "model": "stub"}

    monkeypatch.setattr("evolution.proposer.provider_call", fake_provider_call)
    return captured


def test_retrieved_memory_is_supplied_to_the_drafting_prompt(monkeypatch):
    """Admitted procedures and open gaps must reach the prompt, not just a store."""
    captured = _capture_prompt(monkeypatch)
    proposer = HybridProposer(_llm_config())
    proposer.set_memory_context({
        "procedures": [{
            "failure_family": "FAM-fn-judge_false_no_recall_candidates-test",
            "treatment_route": "recall_trigger",
            "intervention_id": "IV-1-001",
        }],
        "open_capability_gaps": [{
            "failure_family": "FAM-fn-unreachable",
            "reason": "no treatment route reaches the responsible stage",
            "occurrences": 3,
        }],
        "recurring_capability_gaps": [],
    })
    proposer.propose([_fn_family()], cycle=9, max_proposals=2)
    user = captured["user"]
    assert "IV-1-001" in user
    assert "recall_trigger" in user
    assert "no treatment route reaches the responsible stage" in user
    assert "Retrieved prior experience" in user


def test_empty_memory_context_does_not_block_drafting(monkeypatch):
    """With no experience yet, the prompt still carries an empty block."""
    captured = _capture_prompt(monkeypatch)
    proposer = HybridProposer(_llm_config())
    proposer.set_memory_context(None)
    proposer.propose([_fn_family()], cycle=10, max_proposals=2)
    assert "Retrieved prior experience" in captured["user"]


def test_offline_fallback_ignores_memory_context():
    """A proposer without the setter must keep working unchanged."""
    proposer = HybridProposer(_config())
    assert not hasattr(proposer.llm, "memory_context") or (
        proposer.llm.memory_context == {}
    )
    batch = proposer.propose([_fn_family()], cycle=11, max_proposals=2)
    assert batch.candidates
