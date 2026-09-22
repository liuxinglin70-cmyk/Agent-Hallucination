"""The runner registry: what each executor is, and what it may never do.

Before P2.1 the runner space was an implicit if/else: anything that was not
``typed_dsl`` fell through to the built-in predicate table. That shape has a
specific failure mode -- an unregistered runner name silently *runs a
different executor than the one it names* -- and the absence of a declaration
was indistinguishable from "use the default".

These tests pin the declaration's consequences, not just its contents:

* an unregistered runner is an error, never a fallthrough;
* a runner's declared hooks and intervention types are enforced;
* a learned runner may not hold terminal conviction authority, and cannot
  reach it by another route either;
* the artifact it declares is checked against the artifact it actually loaded.
"""

from __future__ import annotations

import pytest

from detector.interventions import (
    RUNNER_REGISTRY,
    SEMANTIC_VERDICTS,
    RunnerSpec,
    SemanticExecutor,
    build_intervention_registry,
    compile_runtime_plan,
    runner_declarations,
    runner_spec,
    validate_intervention,
    validate_intervention_batch,
)
from detector.packet import build_packet


def semantic_entry(*, artifact=None, hook="judge.post",
                   predicate="claim_supported"):
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": hook,
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": predicate,
            "runner": "typed_semantic",
            "output_verdicts": sorted(SEMANTIC_VERDICTS),
        },
        "claim": {
            "expected_behavior": "a typed judgment about the claim",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    if artifact is not None:
        entry["artifact"] = dict(artifact)
    return entry


class FakeExecutor(SemanticExecutor):
    """A stand-in executor whose identity the test controls."""

    name = "fake"

    def __init__(self, *, artifact_id="classifier-a", artifact_digest="aaa",
                 verdict="contradicted"):
        super().__init__(artifact_id=artifact_id, artifact_digest=artifact_digest)
        self._verdict = verdict
        self.calls = 0

    def judge(self, predicate, packet, context):
        self.calls += 1
        return {"verdict": self._verdict, "detail": f"judged {predicate}"}


def packet():
    return build_packet({
        "question": "q",
        "history": [{"step": 1, "role": "assistant", "content": "a",
                     "tool_calls": [{"name": "search", "arguments": "{}"}]}],
    })


# ----------------------------------------------------------------------
# The registry is the authority
# ----------------------------------------------------------------------
def test_every_registered_runner_declares_its_full_contract():
    """An incomplete declaration is what made the runner space implicit."""
    for spec in runner_declarations():
        assert spec["name"]
        assert spec["supported_hooks"], spec["name"]
        assert spec["intervention_types"], spec["name"]
        assert spec["input_schema"], spec["name"]
        assert spec["terminal_authority"] in {
            "none", "evidence_only", "conviction",
        }
        # A runner that is neither deterministic nor learned has no defined
        # reproducibility property, which a gate cannot reason about.
        assert spec["deterministic"] or spec["learned"], spec["name"]


def test_an_unregistered_runner_is_an_error_not_a_fallthrough():
    """The old shape ran the built-in table for any unknown runner name."""
    entry = semantic_entry(artifact={"id": "m", "digest": "d"})
    entry["body"]["runner"] = "no_such_runner"
    with pytest.raises(ValueError, match="is not registered"):
        validate_intervention(entry, require_executable_bounds=True)


def test_a_learned_runner_may_not_hold_terminal_conviction_authority():
    """Section 12.3: a swapped artifact must not become a terminal verdict."""
    with pytest.raises(ValueError, match="may not hold terminal conviction"):
        RunnerSpec(
            name="bad", supported_hooks=frozenset({"judge.post"}),
            intervention_types=frozenset({"evidence_check"}),
            input_schema="x", deterministic=False, learned=True,
            requires_artifact=True, terminal_authority="conviction",
        )


def test_the_semantic_runner_is_evidence_only_and_evidence_check_only():
    spec = runner_spec("typed_semantic")
    assert spec is not None
    assert spec.learned is True
    assert spec.requires_artifact is True
    assert spec.terminal_authority == "evidence_only"
    assert spec.intervention_types == frozenset({"evidence_check"})
    # Excluded on purpose: deciding when the recall net runs is a stronger
    # power than reporting what was observed.
    assert "recall.trigger" not in spec.supported_hooks


def test_a_semantic_runner_cannot_drive_recall_control_flow():
    """The exclusion is enforced, and the reason is named for the repair loop."""
    entry = semantic_entry(artifact={"id": "m", "digest": "d"})
    entry["type"] = "recall_trigger"
    entry["hook"] = "recall.trigger"
    entry["body"]["trigger_verdicts"] = ["contradicted"]
    with pytest.raises(ValueError) as exc:
        validate_intervention(entry, require_executable_bounds=True)
    assert "recall_trigger" in str(exc.value)


# ----------------------------------------------------------------------
# Artifact binding
# ----------------------------------------------------------------------
def test_a_learned_entry_must_declare_its_artifact():
    with pytest.raises(ValueError, match="artifact"):
        validate_intervention(
            semantic_entry(), require_executable_bounds=True,
        )
    partial = semantic_entry(artifact={"id": "classifier-a"})
    with pytest.raises(ValueError, match="digest"):
        validate_intervention(partial, require_executable_bounds=True)


def test_a_deterministic_entry_needs_no_artifact():
    """The requirement follows the declaration, not the entry type."""
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001", "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {"predicate": "required_action_present", "runner": "deterministic",
                 "output_verdicts": ["satisfied", "violated", "not_applicable"]},
        "claim": {"expected_behavior": "x", "target_polarity": "recall",
                  "target_kind": "detection"},
        "provenance": {"cycle": 1, "failure_family": "FAM-fn-x",
                       "failure_cases": ["A/001.json", "A/002.json"]},
    }
    validate_intervention(entry, require_executable_bounds=True)


# ----------------------------------------------------------------------
# Runtime: identity, fail-closed, and the vocabulary
# ----------------------------------------------------------------------
def _plan(executor, entry=None):
    registry = build_intervention_registry([entry or semantic_entry(
        artifact={"id": "classifier-a", "digest": "aaa"},
    )])
    return compile_runtime_plan(registry, semantic_executor=executor)


def test_the_executor_is_checked_against_the_declared_artifact():
    """A pinned digest is decoration unless something compares it.

    Without this comparison a loader could hand over model B while the entry
    names model A, and every downstream check would still pass because it only
    ever saw the declared value.
    """
    swapped = FakeExecutor(artifact_id="classifier-B", artifact_digest="bbb")
    with pytest.raises(ValueError) as exc:
        _plan(swapped).evidence_for("judge.post", packet(), {})
    assert "loaded artifact" in str(exc.value)
    # The mismatch is detected before the executor is asked to judge.
    assert swapped.calls == 0


def test_a_semantic_entry_fails_closed_without_an_executor():
    """A learned judgment cannot be simulated by the deterministic default."""
    with pytest.raises(ValueError, match="needs an executor"):
        _plan(None).evidence_for("judge.post", packet(), {})


def test_a_verdict_outside_the_vocabulary_is_refused():
    beyond = FakeExecutor(verdict="convicted")
    with pytest.raises(ValueError, match="outside the declared vocabulary"):
        _plan(beyond).evidence_for("judge.post", packet(), {})


def test_abstention_is_a_first_class_verdict():
    """A semantic runner that cannot decide must be able to say so."""
    abstaining = FakeExecutor(verdict="abstained")
    plan = _plan(abstaining)
    results = plan.evidence_for("judge.post", packet(), {})
    assert results[0]["verdict"] == "abstained"


def test_semantic_evidence_is_recorded_but_cannot_reach_a_conviction():
    """The two halves of evidence-only, checked together.

    Recorded, so the L1 behaviour gate and a human can both see it; but its
    predicate never enters the closed fact vocabulary, so no guard can consume
    it and no conviction can rest on it.
    """
    executor = FakeExecutor()
    plan = _plan(executor)
    results = plan.evidence_for("judge.post", packet(), {})
    assert results and results[0]["verdict"] == "contradicted"
    assert plan.evidence[0]["predicate"] == "claim_supported"
    assert [item["event"] for item in plan.fired] == ["evidence_appended"]

    # A guard that tries to convict on the semantic verdict is refused,
    # because the fact vocabulary is closed to deterministic predicates.
    guard = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-002", "type": "decision_guard",
        "hook": "adjudicate.guard",
        "trigger": {"packet_features": ["has_tool_calls"],
                    "facts": ["claim_supported=contradicted",
                              "judge_positive=false", "positive_source=none"]},
        "body": {"action": "convict_mechanical_violation",
                 "evidence_predicate": "claim_supported",
                 "action_not_when": "never",
                 "action_not_when_facts": []},
        "claim": {"expected_behavior": "x", "target_polarity": "recall",
                  "target_kind": "detection"},
        "provenance": {"cycle": 1, "failure_family": "FAM-fn-x",
                       "failure_cases": ["A/001.json", "A/002.json"]},
    }
    with pytest.raises(ValueError, match="unknown trigger fact"):
        validate_intervention_batch(
            [semantic_entry(artifact={"id": "m", "digest": "d"}), guard],
            require_executable_bounds=True,
        )


def test_the_registry_declares_the_runners_that_existed_before_it():
    """The historical runners keep their powers: this is not a narrowing."""
    for name in ("deterministic", "typed_dsl"):
        spec = runner_spec(name)
        assert spec is not None
        assert spec.learned is False
        assert spec.requires_artifact is False
        assert spec.terminal_authority == "conviction"
        assert spec.deterministic is True
    # Exactly the registered set, with the Level B component runner added by
    # P2.2. Pinned as an equality rather than a superset on purpose: a runner
    # that appears in the registry without a test noticing it is a runner whose
    # powers nobody declared.
    assert set(RUNNER_REGISTRY) == {
        "deterministic", "typed_dsl", "typed_semantic", "sandboxed_component",
    }


def test_a_drafted_semantic_entry_builds_validates_and_runs():
    """The drafting path: the proposer's constructor must support the entry.

    ``make_intervention`` validates on construction, so a runner the framework
    accepts but the builder cannot express would be unreachable from drafting
    no matter how well the validator is wired.
    """
    from evolution.contracts import make_intervention

    entry = make_intervention(
        intervention_id="IV-1-001", intervention_type="evidence_check",
        hook="judge.post", packet_features=["has_tool_calls"],
        body={"predicate": "claim_supported", "runner": "typed_semantic",
              "output_verdicts": sorted(SEMANTIC_VERDICTS)},
        expected_behavior="a semantic comparison of the claim",
        target_polarity="recall", cycle=1, failure_family="FAM-fn-x",
        failure_cases=["A/001.json", "A/002.json"],
        artifact={"id": "classifier-a", "digest": "aaa"},
    )
    assert entry["artifact"] == {"id": "classifier-a", "digest": "aaa"}
    registry = build_intervention_registry([entry])
    plan = compile_runtime_plan(
        registry, semantic_executor=FakeExecutor(),
    )
    results = plan.evidence_for("judge.post", packet(), {})
    assert results[0]["verdict"] == "contradicted"

    # Omitting the artifact fails at construction, which is where a drafter
    # can still act on the message.
    with pytest.raises(ValueError, match="must declare artifact"):
        make_intervention(
            intervention_id="IV-1-002", intervention_type="evidence_check",
            hook="judge.post", packet_features=["has_tool_calls"],
            body={"predicate": "claim_supported", "runner": "typed_semantic",
                  "output_verdicts": sorted(SEMANTIC_VERDICTS)},
            expected_behavior="x", target_polarity="recall", cycle=1,
            failure_family="FAM-fn-x",
            failure_cases=["A/001.json", "A/002.json"],
        )
