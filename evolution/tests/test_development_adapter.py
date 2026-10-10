from copy import deepcopy

import pytest

from evolution.development_adapter import make_development_drafter
from evolution.proposer import ProposalBatch
from evolution.tests.test_engine_smoke import _scanner_candidate


def inputs():
    parent = _scanner_candidate()
    family = {"diagnostics": {"member_summaries": [{"causal_failure": {"classification": "late"}}]}}
    mechanism = {"member_cases": ["private_target.json"], "normal_neighbors": ["private_normal.json"]}
    diagnosis = {"version_id": "v001", "l1": {"target_regressed": mechanism["member_cases"],
                 "neighbor_regressed": mechanism["normal_neighbors"], "delta": {"new_errors": 1}}}
    return parent, family, mechanism, diagnosis


class ControlledProposer:
    def __init__(self, operation):
        self.rejection_feedback = [{"reason": ["previous reason"]}]
        self.operation = operation
        self.batch = ProposalBatch(candidates=[_scanner_candidate()], joint_groups=[],
                                   notes=[{"detail": {"status": "original"}}])
        self.seen = None

    def set_rejection_feedback(self, feedback):
        self.rejection_feedback = feedback

    def propose(self, families, **kwargs):
        self.seen = deepcopy(self.rejection_feedback)
        self.operation(families, self.rejection_feedback)
        return self.batch


def callback(proposer, family):
    return make_development_drafter(proposer=proposer, cycle=1, family=family,
                                   mechanism_id="M-fixture", identifier="IV-1-unique")


@pytest.mark.parametrize("component", ["parent", "family", "mechanism", "previous"])
def test_revision_does_not_mutate_its_parent_or_caller_state(component):
    parent, family, mechanism, diagnosis = inputs()

    def mutate(families, feedback):
        if component == "parent":
            feedback[-1]["candidate"]["body"]["directive"] = "changed"
        elif component == "family":
            families[0]["diagnostics"]["member_summaries"].clear()
        elif component == "mechanism":
            families[0]["mechanism"]["member_cases"].clear()
        else:
            feedback[0]["reason"].append("changed")

    proposer = ControlledProposer(mutate)
    before = deepcopy((parent, family, mechanism, diagnosis, proposer.rejection_feedback))
    callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    assert (parent, family, mechanism, diagnosis, proposer.rejection_feedback) == before


def test_revision_output_does_not_alias_proposers_candidate_or_notes():
    parent, family, mechanism, diagnosis = inputs()
    proposer = ControlledProposer(lambda *args: None)
    result = callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    result.candidates[0]["body"]["directive"] = "changed"
    result.notes[0]["detail"]["status"] = "changed"
    assert proposer.batch.candidates[0]["body"]["directive"] != "changed"
    assert proposer.batch.notes[0]["detail"]["status"] == "original"


def test_revision_feedback_contains_complete_parent_and_aliases():
    parent, family, mechanism, diagnosis = inputs()
    proposer = ControlledProposer(lambda *args: None)
    callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    revision = proposer.seen[-1]
    assert revision["parent_program"] == {key: parent[key] for key in ("type", "hook", "trigger", "body", "claim")}
    assert revision["development_diagnosis"]["target_regressed"] == ["target_1"]
    assert revision["development_diagnosis"]["neighbor_regressed"] == ["normal_1"]


@pytest.mark.parametrize("failure", [RuntimeError("provider fixture"), KeyboardInterrupt()])
def test_failed_or_cancelled_proposal_restores_prior_feedback(failure):
    parent, family, mechanism, diagnosis = inputs()

    def stop(families, feedback):
        feedback[0]["reason"].append("changed")
        raise failure

    proposer = ControlledProposer(stop)
    before = deepcopy(proposer.rejection_feedback)
    with pytest.raises(type(failure)):
        callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    assert proposer.rejection_feedback == before


def test_feedback_setter_failure_restores_prior_snapshot():
    parent, family, mechanism, diagnosis = inputs()

    class PartialSetter(ControlledProposer):
        def set_rejection_feedback(self, feedback):
            self.rejection_feedback = feedback
            if len(feedback) > 1:
                raise ValueError("setter fixture")

    proposer = PartialSetter(lambda *args: None)
    before = deepcopy(proposer.rejection_feedback)
    with pytest.raises(ValueError, match="setter fixture"):
        callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    assert proposer.rejection_feedback == before


def test_l1_counterexamples_reach_actual_llm_prompt_without_gold(monkeypatch):
    from evolution.proposer import HybridProposer
    from evolution.tests.test_hybrid_proposer import _capture_prompt, _config

    captured = _capture_prompt(monkeypatch)
    config = _config()
    config["budget"]["llm_calls"] = 1
    proposer = HybridProposer(config)
    previous = [{"candidate": _scanner_candidate(), "not_evaluable": True, "reason": "prior feedback"}]
    proposer.set_rejection_feedback(previous)
    parent, family, mechanism, diagnosis = inputs()
    mechanism["member_cases"].append("second_target.json")
    family.update(failure_family="FAM-loc-fixture", count=2, failure_cases=mechanism["member_cases"])
    diagnosis["l1"]["delta"]["rows"] = [{
        "case": "private_normal.json", "gold": "GOLD_SECRET", "correct_result": "EXPECTED_SECRET",
        "baseline_prediction": True, "active_prediction": False,
    }]
    callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    assert '"parent_program"' in captured["user"]
    assert '"trigger":' in captured["user"]
    assert '"claim":' in captured["user"]
    assert '"member": "normal_1"' in captured["user"]
    assert '"baseline_prediction": true' in captured["user"]
    assert '"active_prediction": false' in captured["user"]
    assert '"regressed": true' in captured["user"]
    assert "GOLD_SECRET" not in captured["user"]
    assert "EXPECTED_SECRET" not in captured["user"]
    assert proposer.rejection_feedback == previous


def test_unmeasurable_revision_is_not_falsified_feedback():
    parent, family, mechanism, diagnosis = inputs()
    diagnosis["l1"].update(unmeasurable=True, reason_codes=["coverage_error"])
    proposer = ControlledProposer(lambda *args: None)
    callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    assert proposer.seen[-1]["not_evaluable"] is True
    assert proposer.seen[-1]["evaluation_state"] == "unmeasurable"


def test_llm_feedback_setter_owns_an_independent_input_snapshot():
    from evolution.proposer import HybridProposer
    from evolution.tests.test_hybrid_proposer import _config

    proposer = HybridProposer(_config())
    feedback = [{"candidate": _scanner_candidate(), "not_evaluable": True}]
    proposer.set_rejection_feedback(feedback)
    before = deepcopy(proposer.rejection_feedback)
    feedback[0]["candidate"]["body"]["directive"] = "changed"
    assert proposer.rejection_feedback == before


def test_initial_draft_uses_a_stable_family_snapshot():
    parent, family, mechanism, diagnosis = inputs()
    seen = []
    proposer = ControlledProposer(lambda families, feedback: seen.append(deepcopy(families)))
    draft = callback(proposer, family)
    family["diagnostics"]["member_summaries"].clear()
    draft(mechanism, {}, None, None, 1)
    assert seen[0][0]["diagnostics"]["member_summaries"]


def test_proposer_changes_after_return_do_not_change_frozen_revision_output():
    parent, family, mechanism, diagnosis = inputs()
    proposer = ControlledProposer(lambda *args: None)
    result = callback(proposer, family)(mechanism, {}, parent, diagnosis, 1)
    before = deepcopy(result)
    proposer.batch.candidates[0]["body"]["directive"] = "changed"
    proposer.batch.notes[0]["detail"]["status"] = "changed"
    assert result == before
