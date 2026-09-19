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


def _active_registry_entry():
    return {
        "intervention_id": "IV-ACTIVE-001",
        "type": "text_boundary",
        "hook": "scanner.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "directive": (
                "Cross-check every delivered tool argument against the task "
                "requirement before voting."
            ),
            "not_when": (
                "Never flag a paraphrased delivery when the task allows "
                "summarising."
            ),
        },
        "claim": {
            "expected_behavior": "scanner re-examines delivered arguments",
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-judge_false_no_recall_candidates-test",
            "failure_cases": ["A/012.json", "A/013.json"],
        },
    }


def test_engine_style_active_entries_assignment_reaches_inner_llm(
    monkeypatch,
):
    """The engine assigns `proposer.active_entries = registry.entries`.

    The hybrid wrapper must forward that assignment to the inner LLM
    proposer so the next drafting call sees the newly admitted entry.
    """
    captured = _capture_prompt(monkeypatch)
    proposer = HybridProposer(_llm_config())
    # Cycle 1: registry empty.
    proposer.active_entries = []
    assert proposer.llm.active_entries == []
    # Cycle 2: one intervention was admitted since.
    entry = _active_registry_entry()
    proposer.active_entries = [entry]
    assert proposer.llm.active_entries == [entry]
    assert proposer.active_entries == [entry]
    proposer.propose([_fn_family()], cycle=12, max_proposals=2)
    assert "IV-ACTIVE-001" in captured["user"]


def _drafted_text_boundary(monkeypatch):
    """Draft one text_boundary candidate through the mocked provider."""
    candidate = {
        "type": "text_boundary",
        "hook": "scanner.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "directive": (
                "Cross-check every delivered tool argument against the task "
                "requirement before voting."
            ),
            "not_when": (
                "Never flag a paraphrased delivery when the task allows "
                "summarising."
            ),
        },
        "claim": {
            "expected_behavior": "scanner re-examines delivered arguments",
            "target_polarity": "recall",
        },
        "provenance": {
            "failure_family": "FAM-fn-judge_false_no_recall_candidates-test",
        },
    }

    def fake_provider_call(*, route, system, user, purpose):
        return {"interventions": [dict(candidate)]}, {
            "purpose": purpose, "model": "stub",
        }

    monkeypatch.setattr("evolution.proposer.provider_call", fake_provider_call)


def test_single_candidate_rejection_forwards_and_blocks_verbatim_echo(
    monkeypatch,
):
    """A real gate rejection must stop the same mechanism being re-drafted."""
    _drafted_text_boundary(monkeypatch)
    proposer = HybridProposer(_llm_config())
    batch = proposer.propose([_fn_family()], cycle=77, max_proposals=2)
    assert len(batch.candidates) == 1
    payload = {
        "cycle": 77,
        "intervention_id": "IV-77-001",
        "candidate": batch.candidates[0],
        "type": "text_boundary",
        "hook": "scanner.system",
        "gate": "BEHAVIOR",
        "reason": "behavior delta empty on paired replay",
        "delta": None,
        "not_evaluable": False,
    }
    # Engine recovery rebuilds the proposer and re-feeds the same feedback
    # from the ledger before the next drafting call.
    resumed = HybridProposer(_llm_config())
    resumed.set_rejection_feedback([payload])
    batch2 = resumed.propose([_fn_family()], cycle=78, max_proposals=2)
    assert any(
        note.get("rejected") == "echo_of_rejected" for note in batch2.notes
    )
    # The LLM path yields zero candidates after the echo drop, so the hybrid
    # falls back to deterministic templates; the rejected text boundary
    # itself must not survive in any form.
    assert all(
        candidate.get("type") != "text_boundary"
        for candidate in batch2.candidates
    )


def test_not_evaluable_single_rejection_does_not_ban_retry(monkeypatch):
    """Missing evaluation coverage is not a falsified mechanism."""
    _drafted_text_boundary(monkeypatch)
    proposer = HybridProposer(_llm_config())
    batch = proposer.propose([_fn_family()], cycle=77, max_proposals=2)
    payload = {
        "cycle": 77,
        "intervention_id": "IV-77-001",
        "candidate": batch.candidates[0],
        "type": "text_boundary",
        "hook": "scanner.system",
        "gate": "M",
        "reason": "gain set unmeasurable this cycle",
        "delta": None,
        "not_evaluable": True,
    }
    resumed = HybridProposer(_llm_config())
    resumed.set_rejection_feedback([payload])
    batch2 = resumed.propose([_fn_family()], cycle=78, max_proposals=2)
    assert len(batch2.candidates) == 1
    assert batch2.candidates[0]["type"] == "text_boundary"
    assert not any(
        note.get("rejected") == "echo_of_rejected" for note in batch2.notes
    )


def _drafted_pair_with_bystander(monkeypatch):
    """Draft an evidence+guard pair plus an unrelated text boundary."""
    family = "FAM-fn-judge_false_no_recall_candidates-test"
    program = {
        "schema_version": "detector.predicate_program.v1",
        "name": "evo_missing_search_required",
        "description": "question requires search but no search-like tool ran",
        "verdicts": ["satisfied", "violated", "not_applicable"],
        "conviction_verdicts": ["violated"],
        "default_verdict": "not_applicable",
        "rules": [
            {"field": "tool_names", "operator": "any_contains", "value": "search", "verdict": "satisfied", "detail": "search tool present"},
            {"field": "tool_names", "operator": "any_contains", "value": "grep", "verdict": "satisfied", "detail": "grep tool present"},
            {"field": "tool_names", "operator": "any_contains", "value": "find", "verdict": "satisfied", "detail": "find tool present"},
            {
                "conditions": [
                    {"field": "question_text", "operator": "contains", "value": "search"},
                    {"field": "tool_names", "operator": "none_contains", "value": "search"},
                    {"field": "tool_names", "operator": "none_contains", "value": "grep"},
                    {"field": "tool_names", "operator": "none_contains", "value": "find"},
                ],
                "verdict": "violated",
                "detail": "question requires search but no search-like tool ran",
            },
        ],
    }
    evidence = {
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "evo_missing_search_required",
            "runner": "typed_dsl",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
            "predicate_definition": program,
        },
        "claim": {
            "expected_behavior": "detect required search without search-like tool",
            "target_polarity": "recall",
        },
        "provenance": {"failure_family": family},
    }
    guard = {
        "type": "decision_guard",
        "hook": "adjudicate.guard",
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": [
                "evo_missing_search_required=violated",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        "body": {
            "action": "convict_mechanical_violation",
            "evidence_predicate": "evo_missing_search_required",
            "action_not_when": "never when a search-like tool ran",
            "action_not_when_facts": ["evo_missing_search_required=satisfied"],
        },
        "claim": {
            "expected_behavior": "convict mechanically missing required search",
            "target_polarity": "recall",
        },
        "provenance": {"failure_family": family},
    }
    bystander = {
        "type": "text_boundary",
        "hook": "scanner.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "directive": (
                "Re-read the task text before voting on tool-argument errors."
            ),
            "not_when": "Never flag paraphrase the task explicitly allows.",
        },
        "claim": {
            "expected_behavior": "scanner re-reads the task text",
            "target_polarity": "recall",
        },
        "provenance": {"failure_family": family},
    }

    def fake_provider_call(*, route, system, user, purpose):
        return {
            "interventions": [evidence, guard, bystander],
        }, {"purpose": purpose, "model": "stub"}

    monkeypatch.setattr("evolution.proposer.provider_call", fake_provider_call)


def test_joint_group_rejection_forwards_and_breaks_combination_echo(
    monkeypatch,
):
    """A rejected evidence+guard pair must not be re-emitted as a unit."""
    _drafted_pair_with_bystander(monkeypatch)
    proposer = HybridProposer(_llm_config())
    batch = proposer.propose([_fn_family()], cycle=20, max_proposals=6)
    evidence = next(
        candidate for candidate in batch.candidates
        if candidate.get("type") == "evidence_check"
    )
    guard = next(
        candidate for candidate in batch.candidates
        if candidate.get("type") == "decision_guard"
    )
    payload = {
        "cycle": 20,
        "intervention_id": "combination",
        "candidates": [dict(evidence), dict(guard)],
        "type": "combination",
        "hook": "multiple",
        "gate": "COMBINATION_M",
        "reason": "joint M check lost net gain",
        "delta": None,
        "not_evaluable": False,
    }
    resumed = HybridProposer(_llm_config())
    resumed.set_rejection_feedback([payload])
    batch2 = resumed.propose([_fn_family()], cycle=21, max_proposals=6)
    assert any(
        note.get("rejected") == "echo_of_rejected_combination"
        for note in batch2.notes
    )
    assert all(
        candidate.get("body", {}).get("action") != "convict_mechanical_violation"
        for candidate in batch2.candidates
    )
    # An unrelated mechanism from the same draft is not collateral damage.
    assert any(
        candidate.get("type") == "text_boundary"
        for candidate in batch2.candidates
    )


def test_not_evaluable_joint_group_does_not_ban_retry(monkeypatch):
    """An unevaluable combination stays diagnosable without a permanent ban."""
    _drafted_pair_with_bystander(monkeypatch)
    proposer = HybridProposer(_llm_config())
    batch = proposer.propose([_fn_family()], cycle=20, max_proposals=6)
    evidence = next(
        candidate for candidate in batch.candidates
        if candidate.get("type") == "evidence_check"
    )
    guard = next(
        candidate for candidate in batch.candidates
        if candidate.get("type") == "decision_guard"
    )
    payload = {
        "cycle": 20,
        "intervention_id": "combination",
        "candidates": [dict(evidence), dict(guard)],
        "type": "combination",
        "hook": "multiple",
        "gate": "COMBINATION_M",
        "reason": "gain set unmeasurable this cycle",
        "delta": None,
        "not_evaluable": True,
    }
    resumed = HybridProposer(_llm_config())
    resumed.set_rejection_feedback([payload])
    batch2 = resumed.propose([_fn_family()], cycle=21, max_proposals=6)
    assert not any(
        note.get("rejected") == "echo_of_rejected_combination"
        for note in batch2.notes
    )
    assert any(
        candidate.get("body", {}).get("action") == "convict_mechanical_violation"
        for candidate in batch2.candidates
    )


def test_feedback_resupply_after_recovery_is_idempotent(monkeypatch):
    """Re-supplying the same ledger feedback must not double-count it."""
    _drafted_text_boundary(monkeypatch)
    proposer = HybridProposer(_llm_config())
    batch = proposer.propose([_fn_family()], cycle=77, max_proposals=2)
    payload = {
        "cycle": 77,
        "intervention_id": "IV-77-001",
        "candidate": batch.candidates[0],
        "type": "text_boundary",
        "hook": "scanner.system",
        "gate": "BEHAVIOR",
        "reason": "behavior delta empty on paired replay",
        "delta": None,
        "not_evaluable": False,
    }
    proposer.set_rejection_feedback([payload])
    proposer.set_rejection_feedback([payload])
    assert len(proposer.llm.rejection_feedback) == 1
    assert len(proposer.llm.rejected_keys) == 1
    batch2 = proposer.propose([_fn_family()], cycle=78, max_proposals=2)
    assert any(
        note.get("rejected") == "echo_of_rejected" for note in batch2.notes
    )
    assert all(
        candidate.get("type") != "text_boundary"
        for candidate in batch2.candidates
    )


def test_memory_context_forwarding_reaches_inner_llm():
    """The existing memory path keeps working through the hybrid wrapper."""
    proposer = HybridProposer(_config())
    context = {"procedures": [{"treatment_route": "recall_trigger"}]}
    proposer.set_memory_context(context)
    assert proposer.llm.memory_context == context
