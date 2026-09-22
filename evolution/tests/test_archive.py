"""Candidate archive and reusable components: the two missing stores of §9.1.

Three properties carry this module, and each is a rule rather than a feature:

* the archive keeps *failures* with their reasons, because a store that only
  remembers successes cannot answer why a route keeps failing;
* a component records local usefulness, never correctness -- nothing here can
  write the Active Registry;
* retrieval is bounded and ranked, so a large store yields a small prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evolution.archive import (
    CANDIDATE_ARCHIVE_SCHEMA,
    COMPONENT_KINDS,
    COMPONENT_SCHEMA,
    archive_candidate,
    archive_report,
    candidates_for_mechanism,
    component_digest,
    component_prompt_view,
    load_candidates,
    load_components,
    record_component,
    repeated_failure_reasons,
    retrieve_components,
)


def program(name: str = "evo_missing_search_tool", value: str = "search") -> dict:
    """A valid typed-DSL program; the store validates before it records."""
    return {
        "schema_version": "detector.predicate_program.v1",
        "name": name,
        "description": "question requires a search tool but none was called",
        "verdicts": ["satisfied", "violated", "not_applicable"],
        "conviction_verdicts": ["violated"],
        "default_verdict": "not_applicable",
        "rules": [
            {"field": "tool_names", "operator": "any_contains", "value": value,
             "verdict": "satisfied", "detail": "a search-like tool was called"},
            {"field": "question_text", "operator": "contains",
             "value": f"use {value}", "verdict": "violated",
             "detail": "required but no such tool"},
        ],
    }


# ----------------------------------------------------------------------
# Candidate archive
# ----------------------------------------------------------------------
def test_the_archive_keeps_failures_not_only_successes(tmp_path):
    """A store of successes cannot answer why a route keeps failing."""
    archive_candidate(
        tmp_path, cycle=1, mechanism_id="M-aaa", candidate_hash="h1",
        status="failed_l1", reason_codes=["no_target_improvement"],
        failure_reason="no target case improved on the development replay",
    )
    archive_candidate(
        tmp_path, cycle=1, mechanism_id="M-aaa", candidate_hash="h2",
        status="ready_for_freeze",
    )
    records = load_candidates(tmp_path)
    assert {record["status"] for record in records} == {
        "failed_l1", "ready_for_freeze",
    }
    failed = [r for r in records if r["status"] == "failed_l1"][0]
    assert failed["reason_codes"] == ["no_target_improvement"]
    assert failed["failure_reason"]


def test_a_candidate_is_deduplicated_by_mechanism_and_content(tmp_path):
    """The same artifact through two runs is one candidate.

    Re-recording it would inflate every "how often did this fail" count, which
    is the one question the archive exists to answer.
    """
    archive_candidate(tmp_path, cycle=1, mechanism_id="M-aaa", candidate_hash="h1")
    archive_candidate(tmp_path, cycle=2, mechanism_id="M-aaa", candidate_hash="h1")
    assert len(load_candidates(tmp_path)) == 1
    # The same content under a different mechanism is a different candidate:
    # whether it fails there is a separate finding.
    archive_candidate(tmp_path, cycle=2, mechanism_id="M-bbb", candidate_hash="h1")
    assert len(load_candidates(tmp_path)) == 2


def test_an_archived_candidate_needs_a_content_hash(tmp_path):
    with pytest.raises(ValueError, match="content hash"):
        archive_candidate(tmp_path, cycle=1, mechanism_id="M-aaa", candidate_hash="")


def test_repeated_reasons_are_counted_per_candidate(tmp_path):
    """One failure counts once, even when both reason fields carry it."""
    for index in range(2):
        archive_candidate(
            tmp_path, cycle=1, mechanism_id="M-aaa",
            candidate_hash=f"h{index}", status="failed_l1",
            reason_codes=["no_target_improvement"],
            failure_reason="no_target_improvement",
        )
    archive_candidate(
        tmp_path, cycle=1, mechanism_id="M-aaa", candidate_hash="h9",
        status="ready_for_freeze",
    )
    report = repeated_failure_reasons(tmp_path, "M-aaa")
    assert report["candidates"] == 3
    assert report["reasons"] == {"no_target_improvement": 2}
    assert report["repeated"] == {"no_target_improvement": 2}


def test_reasons_are_scoped_to_one_mechanism_when_asked(tmp_path):
    """A reason recurring across mechanisms says something different.

    On one mechanism it means a route that cannot repair it; across
    mechanisms it means a general capability gap. Merging them reports neither.
    """
    archive_candidate(tmp_path, cycle=1, mechanism_id="M-aaa",
                      candidate_hash="h1", reason_codes=["hook_unreachable"])
    archive_candidate(tmp_path, cycle=1, mechanism_id="M-bbb",
                      candidate_hash="h2", reason_codes=["hook_unreachable"])
    assert repeated_failure_reasons(tmp_path, "M-aaa")["repeated"] == {}
    assert repeated_failure_reasons(tmp_path)["repeated"] == {"hook_unreachable": 2}
    assert len(candidates_for_mechanism(tmp_path, "M-bbb")) == 1


# ----------------------------------------------------------------------
# Reusable components
# ----------------------------------------------------------------------
def test_a_component_is_content_addressed_and_deduplicated(tmp_path):
    first = record_component(
        tmp_path, name="evo_missing_search_tool", kind="extractor",
        program=program(), cycle=1, mechanism_id="M-aaa",
    )
    second = record_component(
        tmp_path, name="evo_missing_search_tool", kind="extractor",
        program=program(), cycle=7, mechanism_id="M-bbb",
    )
    assert first["digest"] == second["digest"]
    assert len(load_components(tmp_path)) == 1
    assert component_digest(program()) == first["digest"]
    # A different rule body is a different part.
    assert component_digest(program(value="calculator")) != first["digest"]


def test_the_same_program_under_two_roles_is_two_reachable_parts(tmp_path):
    """The kind is an input to retrieval, so it is part of the store's key.

    Collapsing the second registration into the first would leave the caller
    unable to find the role it just declared.
    """
    record_component(
        tmp_path, name="evo_missing_search_tool", kind="extractor",
        program=program(), cycle=1,
    )
    record_component(
        tmp_path, name="evo_missing_search_tool", kind="evidence_formatter",
        program=program(), cycle=1,
    )
    assert len(load_components(tmp_path)) == 2
    assert retrieve_components(tmp_path, kind="extractor")["returned"] == 1
    assert retrieve_components(tmp_path, kind="evidence_formatter")["returned"] == 1


def test_a_component_records_a_kind_from_the_closed_vocabulary(tmp_path):
    """Retrieval matches on the kind, so an unclassified part is unreachable."""
    with pytest.raises(ValueError, match="unknown component kind"):
        record_component(
            tmp_path, name="evo_missing_search_tool", kind="bogus",
            program=program(), cycle=1,
        )
    for kind in COMPONENT_KINDS:
        record = record_component(
            tmp_path, name="evo_missing_search_tool", kind=kind,
            program=program(), cycle=1,
        )
        assert record["kind"] == kind


def test_a_component_name_must_match_its_program(tmp_path):
    """Retrieval and the executor must not disagree about what a part is."""
    with pytest.raises(ValueError, match="program.name"):
        record_component(
            tmp_path, name="something_else", kind="parser",
            program=program(), cycle=1,
        )


def test_an_invalid_program_is_never_recorded(tmp_path):
    """The store validates with the same validator the executor uses."""
    broken = program()
    broken["rules"] = [{"field": "no_such_field", "operator": "contains",
                        "value": "x", "verdict": "violated", "detail": "d"}]
    with pytest.raises(ValueError):
        record_component(
            tmp_path, name="evo_missing_search_tool", kind="parser",
            program=broken, cycle=1,
        )
    assert load_components(tmp_path) == []


def test_a_component_claims_usefulness_not_correctness(tmp_path):
    """§9.1: the component layer cannot promote anything.

    Nothing in the record may read as an admission claim -- no status, no
    gate verdict, no registry reference. ``conviction_verdicts`` is present
    and is *not* an exception: it is part of the predicate program's own
    contract (which verdict the DSL treats as a finding), not a claim that
    this component should be deployed.
    """
    record = record_component(
        tmp_path, name="evo_missing_search_tool", kind="semantic_executor",
        program=program(), cycle=1, mechanism_id="M-aaa",
        capability="observation", evidence={"used_by": "IV-1-001"},
    )
    assert record["schema_version"] == COMPONENT_SCHEMA
    # The record's own fields are exactly the store's contract: no status, no
    # verdict, no admission.
    assert set(record) == {
        "schema_version", "cycle", "name", "kind", "digest", "mechanism_id",
        "source_candidate_hash", "capability", "verdicts",
        "conviction_verdicts", "program", "evidence",
    }
    # And the module exposes no path to the registry at all.
    import evolution.archive as archive_module

    for name in archive_module.__all__:
        assert "registr" not in name.lower()
        assert "admit" not in name.lower()
        assert "promot" not in name.lower()


def test_retrieval_is_bounded_and_ranked(tmp_path):
    """A large store must yield a small prompt, most relevant first."""
    for index in range(8):
        record_component(
            tmp_path, name=f"evo_part_{index}", kind="extractor",
            program=program(name=f"evo_part_{index}", value=f"tool{index}"),
            cycle=index + 1,
            mechanism_id="M-aaa" if index < 3 else "M-zzz",
            capability="observation",
        )
    result = retrieve_components(
        tmp_path, mechanism_id="M-aaa", capability="observation", limit=4,
    )
    assert result["store_size"] == 8
    assert result["returned"] == 4
    assert result["matched"] == 8
    # The mechanism's own parts rank first.
    assert all(
        entry["mechanism_id"] == "M-aaa" for entry in result["components"][:3]
    )
    # A kind that nothing matches returns nothing rather than everything.
    assert retrieve_components(tmp_path, kind="localization_helper")["returned"] == 0


def test_retrieval_refuses_an_unknown_kind(tmp_path):
    with pytest.raises(ValueError, match="unknown component kind"):
        retrieve_components(tmp_path, kind="not_a_kind")


def test_the_prompt_view_carries_the_program_but_no_provenance(tmp_path):
    """A program cannot be reused from a name alone."""
    record = record_component(
        tmp_path, name="evo_missing_search_tool", kind="extractor",
        program=program(), cycle=3, mechanism_id="M-secret",
        source_candidate_hash="deadbeef",
    )
    view = component_prompt_view(record)
    assert view["program"]["rules"]
    assert "M-secret" not in str(view)
    assert "deadbeef" not in str(view)
    assert "cycle" not in view


def test_the_archive_report_names_what_is_in_both_stores(tmp_path):
    archive_candidate(tmp_path, cycle=1, mechanism_id="M-aaa",
                      candidate_hash="h1", status="failed_l1")
    record_component(tmp_path, name="evo_missing_search_tool", kind="extractor",
                     program=program(), cycle=1, mechanism_id="M-aaa")
    report = archive_report(tmp_path)
    assert report["schema_version"] == CANDIDATE_ARCHIVE_SCHEMA
    assert report["metric_role"] == "diagnostic-only"
    assert report["candidates"]["total"] == 1
    assert report["candidates"]["by_status"] == {"failed_l1": 1}
    assert report["components"]["total"] == 1
    assert report["components"]["by_kind"] == {"extractor": 1}
    # The boundary with the registry is stated where a reader will see it.
    assert "only the promotion protocol writes the Active Registry" in report["note"]


# ----------------------------------------------------------------------
# Wiring: components must actually reach the drafting prompt
# ----------------------------------------------------------------------
def test_the_engine_retrieves_components_for_the_capability_the_repair_needs(
    tmp_path,
):
    """Retrieval matches the repair's capability, not the actionability verdict.

    ``actionable`` (can this framework act?) and ``control_flow`` (what kind of
    action is needed?) are different vocabularies that share a field name.
    Matching on the former finds nothing, because no component is registered
    under it.
    """
    from evolution.backends import MockDetectorBackend
    from evolution.config import load_config
    from evolution.engine import EvolutionEngine
    from evolution.proposer import OfflineProposer
    from evolution.splits import EvolutionSplit
    from evolution.smoke import PROGRAM

    import json as _json

    data = tmp_path / "data"
    cases = []
    for index in range(20):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "question": f"task {index}",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
        }), encoding="utf-8")
    config = load_config()
    config.update({
        "dataset_root": str(data), "work_root": str(tmp_path / "w"),
        "registry_dir": str(tmp_path / "r"), "ledger_path": str(tmp_path / "l.jsonl"),
        "harness_dir": str(tmp_path / "h"), "memory_root": str(tmp_path / "m"),
    })
    config["backend"].update({"name": "mock", "cache": False})
    config["budget"].update({"patrol_cases": 8})
    config["audit"]["enabled"] = False

    store = Path(config["work_root"]) / "components"
    record_component(
        store, name=PROGRAM["name"], kind="extractor", program=PROGRAM,
        cycle=1, capability="control_flow",
    )
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    backend = MockDetectorBackend(
        base_predictions={c: False for c in [*gain_true, *patrol_true]},
    )
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )

    seen: list[dict] = []

    class Spy(OfflineProposer):
        def set_components(self, components):
            seen.extend(components or [])

    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True, proposer=Spy(),
    )
    report = engine.run_cycle(1)
    assert report["state"] == "completed", report.get("state")
    assert seen, "no reusable component reached the proposer"
    assert seen[0]["name"] == PROGRAM["name"]
    assert seen[0]["kind"] == "extractor"


def test_a_component_for_a_different_capability_is_not_offered(tmp_path):
    """Offering an irrelevant part spends prompt budget for nothing."""
    from evolution.archive import retrieve_components

    record_component(
        tmp_path, name="evo_missing_search_tool", kind="extractor",
        program=program(), cycle=1, capability="observation",
    )
    assert retrieve_components(
        tmp_path, capability="control_flow",
    )["returned"] == 0
    assert retrieve_components(
        tmp_path, capability="observation",
    )["returned"] == 1
