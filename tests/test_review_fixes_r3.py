"""Regression tests for the third-round deep review fixes (R30-R36).

Each test pins one concrete defect fixed in the review round:

- R30: a raised contrast replay increments an audited error counter, so the
  deliberately fail-open neighbour gate cannot silently stop guarding;
- R31: ``canary_preserved`` reports distinct non-None sentinels when the
  state cannot be resolved, so an audit consumer recomputing stability as
  ``before_state == after_state`` cannot read a failure as preserved;
- R32: an out-of-range proof count degrades to 0 instead of raising
  OverflowError out of the fitness scorer;
- R33: ``build_causal_reflection_prompt`` accepts optional ``meta_guidance``
  and injects it as bounded XML without gold/case identity;
- R34: the L1 reflection call site injects the same Meta projection as the
  L2 policy selection site (G2 gate-feedback loop closure);
- R35: ``commit_validated_meta_edits`` applies candidates that clear the
  evidence gate, so the Meta loop stops re-deriving and discarding the same
  recommendation every run (G1 closure);
- R36: the evidence gate is fail-closed — thin evidence, unregistered
  targets and non-advisory operations all stay in shadow.
"""

from __future__ import annotations

import inspect

from src.sehd_v6.core.bccse import evolution_runner
from src.sehd_v6.core.bccse import fitness


# ============================================================
# R30: contrast replay failures are observable
# ============================================================

def test_r30_budget_usage_registers_contrast_replay_errors() -> None:
    """The counter must exist in the budget template, not just on demand.

    A key created only inside the ``except`` branch would keep the audit
    schema unstable between a clean run and a degraded one.
    """
    source = inspect.getsource(evolution_runner.run_online_evolution)
    assert '"contrast_replay_errors": 0,' in source


def test_r30_contrast_replay_exception_increments_counter() -> None:
    source = inspect.getsource(evolution_runner.run_online_evolution)
    marker = 'budget_usage["contrast_replay_errors"] += 1'
    assert marker in source
    # The increment belongs to the contrast replay handler: it must sit
    # between the replay call and the fail-open audit row, otherwise the
    # neighbour gate's unreliability is still unobservable.
    handler_start = source.index('except Exception as exc:\n                        inverted = False')
    handler_end = source.index('"runtime_error"', handler_start)
    assert handler_start < source.index(marker) < handler_end


def test_r30_contrast_gate_stays_fail_open() -> None:
    """The guard is an overfitting check, not a verdict authority.

    Pinning this keeps a later reviewer from "hardening" the neighbour gate
    into a fail-closed veto that a transient provider outage could use to
    reject every otherwise-admissible edit.
    """
    source = inspect.getsource(evolution_runner.run_online_evolution)
    handler = source[
        source.index('except Exception as exc:\n                        inverted = False'):
    ]
    handler = handler[: handler.index('fitness_audit["contrast_replay"]')]
    assert "inverted = False" in handler
    assert "inverted = True" not in handler


# ============================================================
# R31: unresolved canary states use distinct sentinels
# ============================================================

def test_r31_canary_state_error_avoids_none_equality() -> None:
    source = inspect.getsource(evolution_runner.run_online_evolution)
    start = source.index("def canary_preserved(")
    handler = source[start : source.index("preserved = (", start)]
    assert '"before_state": None' not in handler
    assert '"after_state": None' not in handler
    before = handler.index('"before_state": "')
    after = handler.index('"after_state": "')
    before_value = handler[before:].split('"')[3]
    after_value = handler[after:].split('"')[3]
    # Distinct values: equal sentinels would reintroduce the false-preserved
    # reading that None == None produced.
    assert before_value != after_value
    assert before_value and after_value


# ============================================================
# R32: proof counts never raise out of the scorer
# ============================================================

def test_r32_oversized_proof_count_degrades_to_zero() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": float("inf")}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 0


def test_r32_nan_proof_count_degrades_to_zero() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": float("nan")}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 0


def test_r32_absent_key_still_returns_fallback() -> None:
    """The fallback path must survive the widened except clause."""
    row = {"proof_adjudication": {"other_key": 3}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 7


def test_r32_valid_proof_count_is_unchanged() -> None:
    row = {"proof_adjudication": {"valid_error_certificate_count": 4}}
    assert fitness._proof_count(row, "valid_error_certificate_count", 7) == 4


# ============================================================
# R33/R34: gate feedback reaches L1 reflection (G2)
# ============================================================

def _guided_meta_state():
    from src.sehd_v6.core.bccse.meta_skill import (
        OP_ADD_GUIDANCE,
        OP_ENABLE_RANKING,
        MetaEdit,
        apply_meta_edit,
        create_baseline_meta_skill,
    )

    state = create_baseline_meta_skill()
    state = apply_meta_edit(state, MetaEdit(
        edit_id="r33_guidance",
        operation=OP_ADD_GUIDANCE,
        target="prefer_registered_runtime_route",
        rationale="recurring policy_route_unavailable",
    ))
    return apply_meta_edit(state, MetaEdit(
        edit_id="r33_ranking",
        operation=OP_ENABLE_RANKING,
        target="owned_output_match",
        rationale="recurring exact false reject",
    ))


def _l1_prompt(meta_guidance):
    from src.sehd_v6.core.bccse.reflection_engine import (
        build_causal_reflection_prompt,
    )

    from tests.fixtures.causal_evolution import signal_for

    return build_causal_reflection_prompt(
        signal=signal_for("unresolved"),
        current_skill_text="- baseline rule",
        detection_context="trace",
        allowed_target_modules=("evidence_obligation",),
        meta_guidance=meta_guidance,
    )


def test_r33_baseline_meta_state_leaves_l1_prompt_unchanged() -> None:
    """An empty baseline must not perturb the prompt or its cache key."""
    _, without = _l1_prompt(None)
    assert "<meta_guidance" not in without


def test_r33_guidance_is_injected_into_l1_prompt() -> None:
    from src.sehd_v6.core.bccse.meta_review import (
        project_meta_review_for_prompt,
    )

    projection = project_meta_review_for_prompt(_guided_meta_state())
    _, user = _l1_prompt(projection)
    assert "<meta_guidance" in user
    assert 'authority="evolution_guidance_only"' in user
    assert "prefer_registered_runtime_route" in user
    assert "owned_output_match" in user


def test_r33_l1_guidance_carries_no_gate_authority() -> None:
    """Guidance is advisory: it must not smuggle a profile or hash in."""
    from src.sehd_v6.core.bccse.meta_review import (
        project_meta_review_for_prompt,
    )

    state = _guided_meta_state()
    projection = project_meta_review_for_prompt(state)
    _, user = _l1_prompt(projection)
    section = user[user.index("<meta_guidance"):]
    section = section[: section.index("</meta_guidance>")]
    # Only the two closed-vocabulary lists cross the boundary; the gate
    # profile and the content hash stay out of the prompt.
    assert "gate_profile" not in section
    assert state.content_hash not in section
    assert "guidance_tokens" in section
    assert "ranking_preferences" in section


def test_r34_l1_and_l2_share_one_meta_projection() -> None:
    """G2: both reflection surfaces consume the same bounded projection.

    Before the fix only the L2 policy path received Meta guidance, so L1
    rule reflection (the large majority of mutations) ran blind to the
    recorded gate history.
    """
    source = inspect.getsource(evolution_runner.run_online_evolution)
    injections = [
        line for line in source.splitlines()
        if "meta_guidance=meta_guidance," in line
    ]
    assert len(injections) == 2, (
        "expected the projection at both the L1 causal-reflection and the "
        "L2 policy-selection call sites"
    )
    # One shared projection helper, not two divergent renderings.
    assert source.count("project_meta_review_for_prompt(meta_skill_state)") == 2


def test_r34_l1_injection_is_gated_on_non_empty_guidance() -> None:
    source = inspect.getsource(evolution_runner.run_online_evolution)
    assert source.count(
        "if (meta_skill_state.guidance_tokens"
    ) == 2, "both call sites must skip injection on a clean baseline"


# ============================================================
# R35/R36: the meta review loop commits (G1)
# ============================================================

def _experience(code: str, sequence: int):
    from src.sehd_v6.core.bccse.meta_review import GateExperience

    return GateExperience(
        gate_event_id=f"ev{sequence}",
        mechanism_signature="sig",
        layer="l2",
        artifact="policy",
        stage="gate",
        evidence_grade="reject",
        activation_state="none",
        feedback_codes=(code,),
        execution_trace_class="none",
        follow_up_status="pending",
        validation_source="",
        sequence=sequence,
    )


def test_r35_sufficient_evidence_commits_into_meta_state() -> None:
    from src.sehd_v6.core.bccse.meta_review import (
        MIN_COMMIT_EVIDENCE,
        commit_validated_meta_edits,
        generate_meta_edit_candidates,
    )
    from src.sehd_v6.core.bccse.meta_skill import create_baseline_meta_skill

    state = create_baseline_meta_skill()
    rows = [
        _experience("policy_route_unavailable", i)
        for i in range(MIN_COMMIT_EVIDENCE)
    ]
    candidates = generate_meta_edit_candidates(rows, [], state)
    assert candidates, "the fixture must produce a candidate to commit"

    committed, audit = commit_validated_meta_edits(state, candidates, rows, [])
    assert committed.generation == state.generation + 1
    assert "prefer_registered_runtime_route" in committed.guidance_tokens
    assert [row["committed"] for row in audit] == [True]
    # The state is immutable: the caller's copy is untouched.
    assert state.guidance_tokens == ()


def test_r35_committed_state_stops_reproposing_the_same_edit() -> None:
    """Idempotence is what actually closes the loop.

    Without the commit the same candidate was re-derived and re-discarded on
    every run; after it, the improved state must suppress the proposal.
    """
    from src.sehd_v6.core.bccse.meta_review import (
        MIN_COMMIT_EVIDENCE,
        commit_validated_meta_edits,
        generate_meta_edit_candidates,
    )
    from src.sehd_v6.core.bccse.meta_skill import create_baseline_meta_skill

    state = create_baseline_meta_skill()
    rows = [
        _experience("policy_route_unavailable", i)
        for i in range(MIN_COMMIT_EVIDENCE)
    ]
    committed, _ = commit_validated_meta_edits(
        state, generate_meta_edit_candidates(rows, [], state), rows, [],
    )
    assert generate_meta_edit_candidates(rows, [], committed) == []


def test_r36_thin_evidence_stays_in_shadow() -> None:
    from src.sehd_v6.core.bccse.meta_review import (
        commit_validated_meta_edits,
        generate_meta_edit_candidates,
    )
    from src.sehd_v6.core.bccse.meta_skill import create_baseline_meta_skill

    state = create_baseline_meta_skill()
    rows = [_experience("policy_route_unavailable", 0)]
    candidates = generate_meta_edit_candidates(rows, [], state)
    # One occurrence is enough to propose but never enough to commit.
    assert candidates
    committed, audit = commit_validated_meta_edits(state, candidates, rows, [])
    assert committed.content_hash == state.content_hash
    assert [row["blocked_reason"] for row in audit] == ["insufficient_evidence"]


def test_r36_profile_switch_is_never_auto_committed() -> None:
    """A gate-profile switch is a lineage decision, not an inference."""
    from src.sehd_v6.core.bccse.meta_review import commit_validated_meta_edits
    from src.sehd_v6.core.bccse.meta_skill import (
        OP_SWITCH_PROFILE,
        PROFILE_STEADY,
        MetaEdit,
        create_baseline_meta_skill,
    )

    state = create_baseline_meta_skill()
    rows = [_experience("policy_route_unavailable", i) for i in range(50)]
    edit = MetaEdit(
        edit_id="switch",
        operation=OP_SWITCH_PROFILE,
        target=PROFILE_STEADY,
        rationale="overwhelming evidence",
    )
    committed, audit = commit_validated_meta_edits(state, [edit], rows, [])
    assert committed.gate_profile == state.gate_profile
    assert audit[0]["blocked_reason"] == "operation_requires_manual_review"


def test_r36_unregistered_target_has_no_evidence_source() -> None:
    """Fail-closed: an evidence source must be registered to count."""
    from src.sehd_v6.core.bccse.meta_review import evaluate_commit_evidence
    from src.sehd_v6.core.bccse.meta_skill import OP_ADD_GUIDANCE, MetaEdit

    rows = [_experience("policy_route_unavailable", i) for i in range(10)]
    row = evaluate_commit_evidence(
        MetaEdit(
            edit_id="x",
            operation=OP_ADD_GUIDANCE,
            target="require_independent_anchor",
            rationale="unrelated",
        ),
        rows,
        [],
    )
    assert row["committed"] is False
    assert row["blocked_reason"] == "target_has_no_registered_evidence_source"
    # Unrelated feedback never counts toward an unregistered target.
    assert row["evidence_observed"] == 0


def test_r36_runner_persists_the_committed_state() -> None:
    """The runner must write back the committed state, not its input."""
    source = inspect.getsource(evolution_runner.run_online_evolution)
    assert "commit_validated_meta_edits(" in source
    assert (
        'causal_memory.meta_review["meta_state"] = committed_meta_state.to_dict()'
        in source
    ), "the meta_review authority must receive the post-commit state"
    assert 'causal_memory.meta_review["meta_commit_audit"]' in source
