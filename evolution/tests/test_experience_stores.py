"""Regression coverage for the three experience stores.

The stores only earn their keep if two properties hold: the
falsified-versus-unevaluable distinction survives into retrieval (otherwise a
rejection that meant "not measured" suppresses a mechanism as if refuted), and
retrieval is bounded and filtered (otherwise a growing store becomes an
ever-growing prompt).
"""

from __future__ import annotations

import pytest

from evolution.memory import (
    EPISODIC_SCHEMA,
    IMPROVEMENT_SCHEMA,
    PROCEDURAL_SCHEMA,
    episodic_attempts,
    load_episodes,
    load_improvements,
    load_procedures,
    memory_summary,
    recall,
    record_episode,
    record_improvement,
    record_procedure,
)


def test_episodic_records_the_attempt(tmp_path):
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        outcome="rejected", gate="M", reason="marginal_cases=0 < 1",
        intervention_id="IV-1-002", intervention_type="recall_trigger",
        hook="recall.trigger", treatment_routes=["recall_trigger"],
    )
    records = load_episodes(tmp_path)
    assert len(records) == 1
    assert records[0]["schema_version"] == EPISODIC_SCHEMA
    assert records[0]["gate"] == "M"


def test_reviewed_correction_preserves_original_and_changes_retrieval(tmp_path):
    from evolution.memory import record_episode_correction, CORRECTION_NAME
    original = record_episode(tmp_path, cycle=1, failure_family="FAM-fn-x",
        polarity="recall", outcome="rejected", gate="HARMLESSNESS",
        reason="coverage too small", intervention_id="IV-1-001")
    path = tmp_path / "episodic.jsonl"
    before = path.read_bytes()
    kwargs = dict(unevaluable=True, reason="review: not enough protected negatives",
                  evidence="saved cycle report, offline paired replay")
    record_episode_correction(tmp_path, original, **kwargs)
    record_episode_correction(tmp_path, original, **kwargs)
    assert path.read_bytes() == before
    assert len((tmp_path / CORRECTION_NAME).read_text(encoding="utf-8").splitlines()) == 1
    attempt = episodic_attempts(tmp_path)[0]
    assert attempt["falsified"] is False
    assert attempt["original_reason"] == "coverage too small"
    retrieved = recall(tmp_path, failure_family="FAM-fn-x")
    assert retrieved["unevaluable_intervention_ids"] == ["IV-1-001"]
    assert retrieved["falsified_intervention_ids"] == []
    with pytest.raises(ValueError, match="not an original episode"):
        record_episode_correction(tmp_path, {**original, "reason": "different run"}, **kwargs)


def test_unevaluable_rejection_is_not_treated_as_falsified(tmp_path):
    """A rejection from missing coverage must not suppress the mechanism."""
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        outcome="rejected", gate="M", reason="no recall opportunities",
        intervention_id="IV-1-002", unevaluable=True,
    )
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-bar", polarity="recall",
        outcome="rejected", gate="BEHAVIOR", reason="never fired",
        intervention_id="IV-1-003", unevaluable=False,
    )
    attempts = {item["intervention_id"]: item for item in episodic_attempts(tmp_path)}
    assert attempts["IV-1-002"]["falsified"] is False
    assert attempts["IV-1-003"]["falsified"] is True


def test_recall_separates_falsified_from_unevaluable(tmp_path):
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        outcome="rejected", gate="M", reason="not measured",
        intervention_id="IV-1-002", treatment_routes=["recall_trigger"],
        unevaluable=True,
    )
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        outcome="rejected", gate="BEHAVIOR", reason="never fired",
        intervention_id="IV-1-003", treatment_routes=["recall_trigger"],
    )
    result = recall(tmp_path, failure_family="FAM-fn-foo", polarity="recall")
    assert result["unevaluable_intervention_ids"] == ["IV-1-002"]
    assert result["falsified_intervention_ids"] == ["IV-1-003"]


def test_admitted_attempt_is_neither_falsified_nor_unevaluable(tmp_path):
    record_episode(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        outcome="admitted", intervention_id="IV-1-001",
    )
    attempt = episodic_attempts(tmp_path)[0]
    assert attempt["falsified"] is False


def test_procedural_memory_records_only_admitted_routes(tmp_path):
    record_procedure(
        tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
        treatment_route="recall_trigger", intervention_type="recall_trigger",
        hook="recall.trigger", intervention_id="IV-1-001",
        expected_behavior="force recall for unanchored deliveries",
        failure_boundary="recall_triggered_cases=1",
    )
    records = load_procedures(tmp_path)
    assert len(records) == 1
    assert records[0]["schema_version"] == PROCEDURAL_SCHEMA
    assert records[0]["treatment_route"] == "recall_trigger"
    assert records[0]["failure_boundary"] == "recall_triggered_cases=1"


def test_improvement_memory_records_bottleneck_outcome(tmp_path):
    record_improvement(
        tmp_path, cycle=1, bottleneck="no route", patch_kind="evolved_predicate",
        outcome="worked", note="unit normalisation fixed it",
    )
    records = load_improvements(tmp_path)
    assert len(records) == 1
    assert records[0]["schema_version"] == IMPROVEMENT_SCHEMA
    assert records[0]["outcome"] == "worked"


def test_recall_is_filtered_and_bounded(tmp_path):
    for index in range(10):
        record_improvement(
            tmp_path, cycle=index + 1, bottleneck="no route",
            patch_kind=f"kind-{index}", outcome="worked",
        )
    result = recall(
        tmp_path, bottleneck="no route", limit=3,
    )
    assert result["matched"] == 10
    assert result["returned"] == 3


def test_recall_prefers_the_relevant_mechanism(tmp_path):
    record_procedure(
        tmp_path, cycle=1, failure_family="FAM-fn-recall", polarity="recall",
        treatment_route="recall_trigger", intervention_type="recall_trigger",
        hook="recall.trigger", intervention_id="IV-1-001",
    )
    record_procedure(
        tmp_path, cycle=2, failure_family="FAM-fp-precision", polarity="precision",
        treatment_route="withhold_guard", intervention_type="decision_guard",
        hook="adjudicate.guard", intervention_id="IV-2-001",
    )
    result = recall(tmp_path, failure_family="FAM-fn-recall", polarity="recall")
    assert result["entries"]
    assert result["entries"][0]["failure_family"] == "FAM-fn-recall"


def test_recall_excludes_unrelated_entries(tmp_path):
    record_procedure(
        tmp_path, cycle=1, failure_family="FAM-fn-recall", polarity="recall",
        treatment_route="recall_trigger", intervention_type="recall_trigger",
        hook="recall.trigger", intervention_id="IV-1-001",
    )
    result = recall(tmp_path, failure_family="FAM-unrelated", polarity="boundary")
    assert result["returned"] == 0


def test_recall_on_empty_store_is_empty(tmp_path):
    result = recall(tmp_path, failure_family="FAM-fn-foo")
    assert result == {
        "matched": 0, "returned": 0, "entries": [],
        "falsified_intervention_ids": [], "unevaluable_intervention_ids": [],
    }


def test_resumed_cycle_does_not_double_count_experience(tmp_path):
    for _ in range(2):
        record_episode(
            tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
            outcome="rejected", intervention_id="IV-1-002", unevaluable=True,
        )
        record_procedure(
            tmp_path, cycle=1, failure_family="FAM-fn-foo", polarity="recall",
            treatment_route="recall_trigger", intervention_type="recall_trigger",
            hook="recall.trigger", intervention_id="IV-1-001",
        )
    assert len(load_episodes(tmp_path)) == 1
    assert len(load_procedures(tmp_path)) == 1


def test_unsupported_schema_fails_loudly(tmp_path):
    (tmp_path / "episodic.jsonl").write_text(
        '{"schema_version": "evolution.experience.episodic.v99"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported schema"):
        load_episodes(tmp_path)


def test_memory_summary_counts_each_kind(tmp_path):
    record_episode(
        tmp_path, cycle=1, failure_family="F", polarity="recall",
        outcome="admitted", intervention_id="IV-1-001",
    )
    record_episode(
        tmp_path, cycle=1, failure_family="F", polarity="recall",
        outcome="rejected", intervention_id="IV-1-002", unevaluable=True,
    )
    record_procedure(
        tmp_path, cycle=1, failure_family="F", polarity="recall",
        treatment_route="recall_trigger", intervention_type="recall_trigger",
        hook="recall.trigger", intervention_id="IV-1-001",
    )
    record_improvement(
        tmp_path, cycle=1, bottleneck="no route", patch_kind="evolved_predicate",
        outcome="worked",
    )
    summary = memory_summary(tmp_path)
    assert summary["episodic"] == {
        "records": 2, "falsified": 0, "unevaluable": 1, "admitted": 1,
    }
    assert summary["procedural"]["records"] == 1
    assert summary["improvement"]["records"] == 1
