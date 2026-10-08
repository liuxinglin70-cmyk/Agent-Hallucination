"""Mechanism modelling: identity, grouping and the discipline around them.

The plan (§4.4, §15.2) requires that a mechanism be decided by what would
repair a case rather than by where its verdict went wrong, and that its
identity survive renaming. Both are properties of ``evolution.mechanisms`` and
``worksheet.mechanism_subgroups``, and both are easy to break silently: a key
that reads a case id still "works" until the day two sources have to merge.
"""

from __future__ import annotations

import copy

from evolution.mechanisms import (
    DEVIATION_KINDS,
    OBLIGATION_KINDS,
    REPAIR_OPERATIONS,
    infer_mechanism,
    mechanism_compatible,
    mechanism_key,
    mechanism_prompt_view,
    member_reading,
    operation_for,
    reading_signature,
    repair_operation_signature,
)
from evolution.worksheet import (
    excluded_failure_cases,
    localization_annotation_choices,
    mechanism_subgroups,
    stable_localization_families,
    worksheet_report,
)


def member(
    *,
    name: str = "member_001",
    judge_positive: bool = False,
    recall_path: str = "not_needed",
    upgrade_positive: bool = False,
    defense_verdict: str = "none",
    predicates: dict | None = None,
    contracts: list | None = None,
) -> dict:
    """One label-free member summary, shaped as `diagnose_failure_families` emits."""
    return {
        "member": name,
        "contracts": list(contracts if contracts is not None else ["verbatim_content"]),
        "stages": {
            "judge": "ok",
            "recall_scanner": recall_path,
            "causal_upgrade": "ok" if upgrade_positive else "not_needed",
            "step_tracer": "ok",
            "defense": "ok",
        },
        "judge_positive": judge_positive,
        "recall_path": recall_path,
        "upgrade_positive": upgrade_positive,
        "defense_verdict": defense_verdict,
        "predicates": dict(predicates if predicates is not None else {}),
        "typed_samples": {"packet_features": ["has_tool_calls"]},
    }


def verbatim_predicates() -> dict:
    return {
        "required_action_present": "satisfied",
        "verbatim_fidelity": "violated",
        "evidence_refs_resolve": "not_applicable",
        "contract_coverage": "covered",
        "delivered_anchor_consistency": "consistent",
        "final_delivery_present": "present",
    }


def action_predicates() -> dict:
    return {
        "required_action_present": "violated",
        "verbatim_fidelity": "not_applicable",
        "evidence_refs_resolve": "not_applicable",
        "contract_coverage": "covered",
        "delivered_anchor_consistency": "consistent",
        "final_delivery_present": "present",
    }


def test_contract_only_omission_is_a_hypothesis():
    cases = [
        member(name="one", contracts=["required_action"], predicates={
            "required_action_present": "not_applicable",
        }),
        member(name="two", contracts=["required_action"], predicates={
            "required_action_present": "not_applicable",
        }),
    ]
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_upgrade_no_upgrade-contract",
        members=cases,
        member_cases=["A/001.json", "B/002.json"],
    )
    assert mechanism["task_obligation"]["kind"] == "required_action"
    assert mechanism["observed_deviation"]["kind"] == "unclassified"
    assert mechanism["status"] == "hypothesis"
    assert mechanism["confidence_basis"] == "contract_only"


def test_mixed_mechanism_identity_does_not_depend_on_member_order():
    cases = [
        member(name="action", predicates=action_predicates()),
        member(name="verbatim", predicates=verbatim_predicates()),
    ]
    kwargs = {"member_cases": ["A/001.json", "B/002.json"]}
    left = infer_mechanism("FAM-fn-judge_false_no_recall_candidates-mixed",
                           members=cases, **kwargs)
    right = infer_mechanism("FAM-fn-judge_false_no_recall_candidates-mixed",
                            members=list(reversed(cases)), **kwargs)
    assert left["status"] == right["status"] == "ambiguous"
    assert left["mechanism_id"] == right["mechanism_id"]
    assert left["signature"] == right["signature"]


def family(
    *,
    family_id: str = "FAM-fn-judge_false_no_recall_candidates-abc",
    members: list | None = None,
    cases: list | None = None,
    target_kind: str = "detection",
    signature: dict | None = None,
    local_states: list | None = None,
) -> dict:
    members = list(members if members is not None else [
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=verbatim_predicates()),
    ])
    cases = list(cases if cases is not None else ["A/001.json", "B/002.json"])
    item = {
        "failure_family": family_id,
        "count": len(cases),
        "failure_cases": cases,
        "sources": sorted({case.split("/")[0] for case in cases}),
        "mechanism_summary": {"family": family_id, "n": len(cases)},
        "mechanism_signature": signature if signature is not None else {
            "judge_positive": False,
            "upgrade_positive": False,
            "recall_path": "not_needed",
            "defense_verdict": "none",
            "positive_source": "none",
        },
        "diagnostics": {
            "n": len(members),
            "member_summaries": members,
            "predicate_signals": {},
            "shared_literals": {},
        },
    }
    if target_kind == "localization":
        item["target_kind"] = "localization"
        item["localization_states"] = list(local_states or ["mislocated"] * len(cases))
    return item


# --------------------------------------------------------------------------
# §4.4 (1) and (2): the repair operation decides membership, not the path.
# --------------------------------------------------------------------------
def test_same_path_different_repair_operation_is_not_one_mechanism():
    """Two cases can fail identically and still need different repairs."""
    shared_path = {
        "judge_positive": False,
        "upgrade_positive": False,
        "recall_path": "not_needed",
        "defense_verdict": "none",
        "positive_source": "none",
    }
    verbatim = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        path_signature=shared_path,
        member_cases=["A/001.json", "B/002.json"],
    )
    omission = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=action_predicates()),
            member(name="member_002", predicates=action_predicates()),
        ],
        path_signature=shared_path,
        member_cases=["C/003.json", "D/004.json"],
    )
    assert verbatim["detector_failure"]["stage"] == omission["detector_failure"]["stage"]
    assert verbatim["task_obligation"]["kind"] == "verbatim_content"
    assert omission["task_obligation"]["kind"] == "required_action"
    assert verbatim["observed_deviation"]["kind"] == "mismatch"
    assert omission["observed_deviation"]["kind"] == "omission"
    assert mechanism_key(verbatim) != mechanism_key(omission)
    assert not mechanism_compatible(verbatim, omission)


def test_different_case_sets_with_the_same_repair_are_one_mechanism():
    """Membership is decided by the repair, not by the cases it came from."""
    left = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        member_cases=["A/001.json", "B/002.json"],
    )
    right = infer_mechanism(
        "FAM-fn-judge_false_upgrade_no_upgrade-def",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
            member(name="member_003", predicates=verbatim_predicates()),
        ],
        member_cases=["E/005.json", "F/006.json", "G/007.json"],
    )
    assert left["mechanism_id"] == right["mechanism_id"]
    assert mechanism_compatible(left, right)


def test_false_convictions_group_by_the_repair_not_by_the_contract_kind():
    """A false conviction has no task-level deviation, so its repair is one thing.

    Found by the E0 dry-run over archived rows: two false convictions whose
    members declared different contract kinds (``exact_value`` versus
    ``required_action``) were split into two mechanisms, although both are
    repaired by withholding the conviction and neither has anything wrong in
    the delivery. The obligation is not part of that repair.
    """
    def false_conviction(contracts: list) -> dict:
        member = {
            "member": "member_001",
            "contracts": contracts,
            "stages": {"judge": "ok"},
            "judge_positive": True,
            "recall_path": "not_run",
            "upgrade_positive": False,
            "defense_verdict": "substantiated",
            "predicates": {
                "required_action_present": "not_applicable",
                "verbatim_fidelity": "not_applicable",
                "evidence_refs_resolve": "resolved",
                "final_delivery_present": "not_applicable",
            },
            "typed_samples": {},
        }
        return infer_mechanism(
            "FAM-fp-judge_conviction-abc",
            members=[member],
            member_cases=["A/001.json"],
        )

    exact_value = false_conviction(["exact_value"])
    required_action = false_conviction(["required_action"])
    # The readings differ in obligation ...
    assert (
        exact_value["task_obligation"]["kind"]
        != required_action["task_obligation"]["kind"]
    )
    # ... but both are the same repair, so they are one mechanism.
    assert exact_value["observed_deviation"]["kind"] == "none"
    assert exact_value["mechanism_id"] == required_action["mechanism_id"]
    # A miss keeps its obligation in the key: there the delivery is what
    # deviated, and the obligation decides which observation is needed.
    miss_action = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[member(name="member_001", predicates=action_predicates())],
        member_cases=["A/001.json"],
    )
    miss_verbatim = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[member(name="member_001", predicates=verbatim_predicates())],
        member_cases=["A/001.json"],
    )
    assert miss_action["mechanism_id"] != miss_verbatim["mechanism_id"]


def test_false_conviction_is_a_different_mechanism_from_a_miss():
    """A false conviction has no task-level deviation; a miss does.

    The two need opposite repairs -- withhold a conviction versus observe a
    deviation -- so they must never share a mechanism even when the detector
    stages that ran look similar.
    """
    miss = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[member(name="member_001", predicates=verbatim_predicates())],
        member_cases=["A/001.json"],
    )
    false_positive = infer_mechanism(
        "FAM-fp-judge_conviction-xyz",
        members=[member(name="member_001", predicates=verbatim_predicates())],
        member_cases=["A/001.json"],
    )
    assert miss["observed_deviation"]["kind"] == "mismatch"
    assert false_positive["observed_deviation"]["kind"] == "none"
    assert miss["mechanism_id"] != false_positive["mechanism_id"]
    assert not mechanism_compatible(miss, false_positive)


# --------------------------------------------------------------------------
# §4.4 (3) and (4) / §15.2 (1) and (2): renaming must not move a mechanism.
# --------------------------------------------------------------------------
def test_mechanism_key_is_invariant_to_case_ids_and_source_names():
    """Renaming cases, and moving them to another source, keeps the mechanism."""
    original = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        path_signature={"judge_positive": False, "upgrade_positive": False},
        member_cases=["BFCL/001.json", "BFCL/002.json"],
    )
    renamed = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        path_signature={"judge_positive": False, "upgrade_positive": False},
        member_cases=["Camel/900.json", "Octotools/901.json"],
    )
    assert original["mechanism_id"] == renamed["mechanism_id"]
    assert original["member_cases"] != renamed["member_cases"]
    # The same must hold when the two cases arrive from different sources and
    # the family id differs, because the coarse id is detector-path derived.
    cross_source = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-def",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        path_signature={"judge_positive": False, "upgrade_positive": False},
        member_cases=["SmolAgents/700.json", "Magentic_One/701.json"],
    )
    assert original["mechanism_id"] == cross_source["mechanism_id"]


def test_mechanism_identity_never_reads_case_gold_or_source():
    """The identity is a function of the reading, so extra row fields cannot move it."""
    reading = {
        "task_obligation_kind": "verbatim_content",
        "deviation_kind": "mismatch",
        "failure": ("recall_scanner", "not_reached"),
    }
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[member(name="member_001", predicates=verbatim_predicates())],
        member_cases=["A/001.json"],
    )
    signature_before = repair_operation_signature(mechanism)
    key_before = mechanism_key(mechanism)
    # Gold, source and any dataset annotation are simply not inputs: adding
    # them to the member summary cannot change the identity.
    noisy_member = member(name="member_001", predicates=verbatim_predicates())
    noisy_member["gold"] = True
    noisy_member["case"] = "A/001.json"
    noisy_member["gold_step"] = 7
    after = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[noisy_member],
        member_cases=["A/001.json"],
    )
    assert repair_operation_signature(after) == signature_before
    assert mechanism_key(after) == key_before
    assert set(reading_signature(reading)) >= {
        "verbatim_content", "mismatch", "recall_scanner", "not_reached",
    }


# --------------------------------------------------------------------------
# §4.4 (5): an ambiguous localization reading is not a confirmed mechanism.
# --------------------------------------------------------------------------
def test_localization_ambiguity_is_not_a_confirmed_mechanism():
    """Only unambiguous localization readings reach `confirmed`.

    The ambiguity gate is upstream: ``stable_localization_families`` keeps a
    mislocated row only when the named step cannot host a text error at all.
    What is left for this layer is that a lone case is not a mechanism, and
    that members disagreeing about their reading are reported as mixed.
    """
    # (1) A single case is never confirmed, however clean its reading.
    single = infer_mechanism(
        "FAM-loc-mislocated-abc",
        target_kind="localization",
        members=[member(name="member_001", judge_positive=True)],
        member_cases=["A/001.json"],
        count=1,
    )
    assert single["status"] == "single_case_research_only"
    # (2) Members disagreeing on the reading (a wrong step versus no step at
    # all) are two mechanisms, not one confirmed one.
    mixed = infer_mechanism(
        "FAM-loc-mislocated-abc",
        target_kind="localization",
        members=[
            member(name="member_001", judge_positive=True),
            member(name="member_002", judge_positive=True),
        ],
        local_states=["mislocated", "step_missing"],
        member_cases=["A/001.json", "B/002.json"],
    )
    assert mixed["status"] == "ambiguous"
    assert {item[1] for item in mixed["observed_readings"]} == {
        "mislocation", "missing_step",
    }
    # (3) Two agreeing viable cases do confirm.
    confirmed = infer_mechanism(
        "FAM-loc-mislocated-abc",
        target_kind="localization",
        members=[
            member(name="member_001", judge_positive=True),
            member(name="member_002", judge_positive=True),
        ],
        member_cases=["A/001.json", "B/002.json"],
        count=2,
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["reading_unanimous"] is True


def test_ambiguous_localization_case_cannot_reach_a_confirmed_mechanism():
    """End to end: a `needs_review` case is filtered before mechanism inference.

    ``BFCL/148`` is the reference shape -- the detector names a step that
    carries its own real error while the annotation picked another. Admitting
    it would ask the framework to teach the detector to abandon a true
    detection, so it must not appear in any mechanism, confirmed or otherwise.
    """
    def row(case: str, step: int, prediction: bool = True) -> dict:
        return {
            "case": case,
            "gold": True,
            "prediction": prediction,
            "authority": "authoritative",
            "responsible_step": step,
            "result": {
                "case_id": case,
                "state": "verified_error",
                "decision_authority": "authoritative",
                "runtime_valid": True,
                "responsible_step": step,
                "stage_status": {
                    "judge": {"state": "ok"},
                    "recall_scanner": {"state": "ok"},
                    "causal_upgrade": {"state": "ok"},
                    "step_tracer": {"state": "ok"},
                    "defense": {"state": "ok"},
                },
                "proof": {
                    "judge": {"is_hallucination": True},
                    "defense": {"verdict": "substantiated"},
                    "terminal_basis": {
                        "decisive_facts": {"positive_source": "judge"},
                    },
                },
            },
        }

    def record(steps: list) -> dict:
        return {"question": "q", "history": steps}

    # The named step carries no text: provably not the origin of a content
    # error, so it is a usable repair task.
    empty_call = {"role": "assistant", "content": "",
                  "tool_calls": [{"name": "ls", "arguments": {"a": True}}]}
    # The named step carries its own text: naming it may be defensible.
    prose = {"role": "assistant", "content": "I have upgraded you to support."}

    viable_rows = [row("A/001.json", 1), row("B/002.json", 1)]
    ambiguous_rows = [row("C/003.json", 2), row("D/004.json", 2)]
    records = {
        "A/001.json": record([empty_call, prose, prose]),
        "B/002.json": record([empty_call, prose, prose]),
        "C/003.json": record([prose, prose, prose]),
        "D/004.json": record([prose, prose, prose]),
    }
    gold_steps = {
        "A/001.json": 3, "B/002.json": 3, "C/003.json": 3, "D/004.json": 3,
    }
    families = stable_localization_families(
        [*viable_rows, *ambiguous_rows],
        gold_steps=gold_steps,
        records=records,
    )
    assert len(families) == 1
    admitted = sorted(families[0]["failure_cases"])
    assert admitted == ["A/001.json", "B/002.json"]
    subgroups, unrepresented = mechanism_subgroups(families)
    assert unrepresented == []
    assert len(subgroups) == 1
    mechanism = subgroups[0]["mechanism"]
    # The family has not been packet-diagnosed here, so the reading is the
    # path-only hypothesis; the point is which cases it covers.
    assert mechanism["mechanism_id"].startswith("M-")
    assert sorted(mechanism["member_cases"]) == ["A/001.json", "B/002.json"]
    mechanism_text = str(mechanism)
    for ambiguous_case in ("C/003.json", "D/004.json"):
        assert ambiguous_case not in mechanism_text
    # The exclusion is reported, not silent.
    choices = localization_annotation_choices(
        [*viable_rows, *ambiguous_rows],
        gold_steps=gold_steps,
        records=records,
    )
    assert sorted(item["case"] for item in choices) == ["C/003.json", "D/004.json"]


def test_disagreeing_members_are_reported_ambiguous_not_confirmed():
    mixed = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=action_predicates()),
        ],
        member_cases=["A/001.json", "B/002.json"],
    )
    assert mixed["status"] == "ambiguous"
    assert mixed["confidence_basis"] == "mixed"
    assert len(mixed["observed_readings"]) == 2


# --------------------------------------------------------------------------
# Structuring properties: vocabularies stay closed and readable.
# --------------------------------------------------------------------------
def test_operation_and_vocabularies_are_closed_and_total():
    """Every reading maps to a declared operation; unknown pairs are gaps."""
    for obligation in OBLIGATION_KINDS:
        assert obligation
    for deviation in DEVIATION_KINDS:
        assert deviation
    assert operation_for("recall_scanner", "not_reached") in REPAIR_OPERATIONS
    assert operation_for("no_such_stage", "no_such_mode") == "unavailable"
    assert "unavailable" in REPAIR_OPERATIONS


def test_mechanism_record_has_the_plan_schema():
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        path_signature={"judge_positive": False, "upgrade_positive": False},
        member_cases=["A/001.json", "B/002.json"],
    )
    for key in (
        "schema_version", "mechanism_id", "family_id", "objective",
        "task_obligation", "observed_deviation", "evidence_bindings",
        "detector_failure", "repair_hypothesis", "member_cases",
        "normal_neighbors", "status", "confidence_basis",
    ):
        assert key in mechanism, key
    assert mechanism["schema_version"] == "evolution.failure_mechanism.v1"
    hypothesis = mechanism["repair_hypothesis"]
    assert hypothesis["operation"] in REPAIR_OPERATIONS
    assert hypothesis["forbidden_shortcuts"]
    assert all(
        binding["role"] for binding in mechanism["evidence_bindings"]
    )


def test_prompt_view_hides_case_identifiers():
    """The proposer-facing view must not carry case ids or source names."""
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            member(name="member_001", predicates=verbatim_predicates()),
            member(name="member_002", predicates=verbatim_predicates()),
        ],
        member_cases=["BFCL/001.json", "Camel/002.json"],
        normal_neighbors=["BFCL/900.json"],
    )
    view = mechanism_prompt_view(mechanism)
    rendered = str(view)
    assert "BFCL/001.json" not in rendered
    assert "Camel/002.json" not in rendered
    assert "BFCL/900.json" not in rendered
    assert view["member_count"] == 2
    assert mechanism_prompt_view(None) is None


def test_excluded_cases_are_reported_not_silently_dropped():
    """The profile split upstream must not make failures disappear.

    A real patrol showed this: a four-case FN family left two cases behind when
    `refine_failure_families` kept only the largest mechanical-profile group.
    The exclusion is correct -- one case cannot carry two provenance cases --
    but it has to be visible, or the patrol's failure count is unreconstructible.
    """
    coarse = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=verbatim_predicates()),
        member(name="member_003", predicates=action_predicates()),
        member(name="member_004", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json", "C/003.json", "D/004.json"])]
    surviving = [{
        **coarse[0],
        "failure_cases": ["A/001.json", "B/002.json"],
        "count": 2,
    }]
    excluded = excluded_failure_cases(coarse, surviving)
    assert len(excluded) == 1
    assert excluded[0]["excluded_cases"] == ["C/003.json", "D/004.json"]
    assert excluded[0]["parent_count"] == 4
    assert "two-case minimum" in excluded[0]["reason"]
    # Nothing is excluded when every case survives.
    assert excluded_failure_cases(coarse, coarse) == []
    # And the report carries it.
    report = worksheet_report(
        [], surviving, unrepresented=[], excluded=excluded,
    )
    assert report["mechanisms"]["excluded"] == excluded


# --------------------------------------------------------------------------
# Subgrouping: the split must not lose or duplicate cases.
# --------------------------------------------------------------------------
def test_subgroups_split_mixed_family_and_keep_every_case_once():
    families = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=verbatim_predicates()),
        member(name="member_003", predicates=action_predicates()),
        member(name="member_004", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json", "C/003.json", "D/004.json"])]
    subgroups, unrepresented = mechanism_subgroups(families)
    assert len(subgroups) == 2
    assert unrepresented == []
    seen = [case for sub in subgroups for case in sub["failure_cases"]]
    assert sorted(seen) == ["A/001.json", "B/002.json", "C/003.json", "D/004.json"]
    assert len({sub["mechanism"]["mechanism_id"] for sub in subgroups}) == 2
    # Each subgroup needs a distinct id (gates key families by it) while the
    # coarse prefix survives so polarity/target-kind routing keeps working.
    ids = {sub["failure_family"] for sub in subgroups}
    assert len(ids) == 2
    coarse = families[0]["failure_family"]
    assert all(item.startswith(coarse) for item in ids)
    assert all(
        sub["coarse_failure_family"] == coarse for sub in subgroups
    )
    assert all(sub["mechanism_subgroup"] for sub in subgroups)


def test_split_never_empties_a_family_below_proposable_size():
    """A two-case family with two readings must stay whole, not vanish.

    The proposer previously received this family. Splitting it into two
    one-case readings would leave it with nothing, and STRUCTURAL would reject
    any draft built on a single provenance case -- a silent loss of the only
    two failures the patrol found.
    """
    families = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json"])]
    subgroups, unrepresented = mechanism_subgroups(families)
    assert len(subgroups) == 1
    assert unrepresented == []
    assert subgroups[0]["failure_cases"] == ["A/001.json", "B/002.json"]
    assert subgroups[0]["mechanism_subgroup"] is False
    # The mixed reading is reported rather than hidden behind one of them.
    assert subgroups[0]["mechanism"]["status"] == "ambiguous"


def test_homogeneous_family_passes_through_whole():
    families = [family()]
    subgroups, unrepresented = mechanism_subgroups(families)
    assert len(subgroups) == 1
    assert unrepresented == []
    assert subgroups[0]["mechanism_subgroup"] is False
    assert subgroups[0]["count"] == 2
    assert subgroups[0]["mechanism"]["status"] == "confirmed"


def test_rare_reading_is_reported_not_proposed_on():
    """A one-case reading cannot be proposed on, but must stay visible."""
    families = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=verbatim_predicates()),
        member(name="member_003", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json", "C/003.json"])]
    subgroups, unrepresented = mechanism_subgroups(families)
    assert len(subgroups) == 1
    assert subgroups[0]["failure_cases"] == ["A/001.json", "B/002.json"]
    assert len(unrepresented) == 1
    assert unrepresented[0]["case_count"] == 1
    assert unrepresented[0]["status"] == "single_case_research_only"
    assert "STRUCTURAL" in unrepresented[0]["reason"]
    # Nothing is lost: 2 proposed cases plus 1 reported case is the whole family.
    assert len(subgroups[0]["failure_cases"]) + unrepresented[0]["case_count"] == 3


def test_path_fallback_tells_a_miss_from_a_false_conviction():
    """``positive_source="none"`` is a miss, not a conviction.

    The sentinel is a string in the path signature, so a truthiness test would
    read every fallback miss as "no task-level deviation" -- the reading that
    belongs to a false conviction, whose repair is the opposite one.
    """
    miss = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=(),
        path_signature={
            "judge_positive": False,
            "upgrade_positive": False,
            "positive_source": "none",
        },
    )
    conviction = infer_mechanism(
        "FAM-fp-judge_conviction-abc",
        members=(),
        path_signature={
            "judge_positive": True,
            "upgrade_positive": False,
            "positive_source": "judge",
        },
    )
    assert miss["observed_deviation"]["kind"] == "unclassified"
    assert conviction["observed_deviation"]["kind"] == "none"
    assert miss["mechanism_id"] != conviction["mechanism_id"]


def test_subgroups_without_member_summaries_fall_back_to_path_signature():
    """A family diagnosed without packet evidence is still a mechanism."""
    families = [family()]
    families[0]["diagnostics"] = {"n": 0, "member_summaries": [], "predicate_signals": {}}
    subgroups, unrepresented = mechanism_subgroups(families)
    assert len(subgroups) == 1
    assert unrepresented == []
    mechanism = subgroups[0]["mechanism"]
    assert mechanism["confidence_basis"] == "path_only"
    assert mechanism["status"] == "hypothesis"
    assert mechanism["detector_failure"]["stage"] == "recall_scanner"
    assert mechanism["repair_hypothesis"]["operation"] == "route_recall"


def test_subgroups_are_independent_of_case_order():
    families = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=action_predicates()),
        member(name="member_003", predicates=verbatim_predicates()),
        member(name="member_004", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json", "C/003.json", "D/004.json"])]
    forward, _ = mechanism_subgroups(copy.deepcopy(families))
    shuffled = copy.deepcopy(families)
    shuffled[0]["failure_cases"] = ["D/004.json", "C/003.json", "B/002.json", "A/001.json"]
    shuffled[0]["diagnostics"]["member_summaries"] = [
        shuffled[0]["diagnostics"]["member_summaries"][index]
        for index in (3, 2, 1, 0)
    ]
    backward, _ = mechanism_subgroups(shuffled)
    assert [
        (sub["mechanism"]["mechanism_id"], sorted(sub["failure_cases"]))
        for sub in forward
    ] == [
        (sub["mechanism"]["mechanism_id"], sorted(sub["failure_cases"]))
        for sub in backward
    ]


def test_worksheet_report_exposes_mechanisms_and_unrepresented():
    families = [family(members=[
        member(name="member_001", predicates=verbatim_predicates()),
        member(name="member_002", predicates=verbatim_predicates()),
        member(name="member_003", predicates=action_predicates()),
    ], cases=["A/001.json", "B/002.json", "C/003.json"])]
    subgroups, unrepresented = mechanism_subgroups(families)
    report = worksheet_report([], subgroups, unrepresented=unrepresented)
    block = report["mechanisms"]
    assert block["count"] == 1
    assert block["mechanisms"][0]["proposable"] is True
    assert block["mechanisms"][0]["case_count"] == 2
    assert len(block["unrepresented"]) == 1
    assert block["unrepresented"][0]["proposable"] is False
    assert block["unrepresented"][0]["objective"] == "detection"


def test_reading_signature_separates_obligations_within_one_path():
    """The grouping key of a reading is what the subgroup split uses."""
    left = member_reading(
        member(name="member_001", predicates=verbatim_predicates()),
        family_id="FAM-fn-x",
        objective="detection",
    )
    right = member_reading(
        member(name="member_001", predicates=action_predicates()),
        family_id="FAM-fn-x",
        objective="detection",
    )
    assert reading_signature(left) != reading_signature(right)
    assert reading_signature(left)[-1] in REPAIR_OPERATIONS
