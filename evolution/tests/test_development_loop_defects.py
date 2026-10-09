"""Defects found in the development loop by the self-evolution audit.

Each test here failed before the fix it names. They are grouped by the failure
they pin, and every one is offline: the backend is the repository's mock and the
proposer is scripted or the offline one, so nothing in this module can reach a
provider.

1. L1 read only the *neighbour* cases for regressions. The target set -- the
   cases the intervention claims to repair *and keep working* -- was scored for
   improvement only, so a version that repaired one member of a two-case family
   while losing the other passed, and with a two-member family one lost true
   positive is half the target set.
2. A replay that could not be scored (provider error, deadline skip, a
   non-authoritative terminal) was written as ``failed_l1``: "we could not
   measure this" archived as "we measured this and it failed", and carried into
   the next round's negative feedback.
3. The recovery branch of ``_develop_candidates`` named a caller-scope local
   and raised ``NameError`` the first time it ran.
4. The archive registered components under ``archive.root`` while retrieval read
   ``component_root or work_root/components``: with the default configuration no
   component a run produced was ever reachable, and the next drafting prompt saw
   an empty store.
5. A development falsification never reached the episodic store, because the
   gate path is the only writer and a version rejected by L1 never reaches a
   gate. The next round's recall then had no record that this mechanism and
   route had already been tried and failed.
6. The data-separation guard covered ``sentinel`` and ``gain`` only. Under the
   mini protocol ``validation`` is the run's only out-of-sample reading, and a
   development replay could consume it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.archive import load_candidates, load_components
from evolution.backends import MockDetectorBackend
from evolution.candidate_versions import CandidateVersionGraph
from evolution.config import load_config
from evolution.contracts import make_intervention
from evolution.development import (
    CandidateDeveloper,
    DevelopmentBudget,
    DevelopmentUnmeasurable,
    check_l1,
    development_report,
)
from evolution.engine import EvolutionEngine
from evolution.memory import episodic_attempts, load_episodes
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit


# --------------------------------------------------------------------------
# Fixtures: a synthetic dataset, an offline engine, a typed-DSL program
# --------------------------------------------------------------------------

PROGRAM = {
    "schema_version": "detector.predicate_program.v1",
    "name": "evo_defect_probe",
    "description": "Violated when the trajectory carries prose but no tool call.",
    "verdicts": ["satisfied", "violated", "not_applicable"],
    "conviction_verdicts": ["violated"],
    "rules": [{
        "conditions": [{"field": "tool_call_count", "operator": "equals",
                        "value": 0}],
        "verdict": "violated",
        "detail": "no tool call in the trajectory",
    }],
    "default_verdict": "not_applicable",
    "default_detail": "no probe matched",
}


def _dataset(root: Path, n: int = 20) -> list[str]:
    cases = []
    for index in range(n):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"task {index}",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
            "true_answer": "gold",
        }), encoding="utf-8")
    return cases


def _config(tmp_path: Path, *, archive_on: str = "off") -> dict:
    config = load_config()
    config.update({
        "dataset_root": str(tmp_path / "data"),
        "work_root": str(tmp_path / "work"),
        "registry_dir": str(tmp_path / "registry"),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
        "harness_dir": str(tmp_path / "harness"),
        "memory_root": str(tmp_path / "memory"),
    })
    config["backend"]["cache"] = False
    # ``preview_cycle`` drafts through whatever proposer the config names, and
    # the default is the live one. A mock backend is not a mock proposer.
    config["proposer"]["mode"] = "offline"
    config["budget"]["patrol_cases"] = 8
    config["audit"]["enabled"] = False
    config["development"].update({
        "enabled": True,
        "max_mechanisms_per_cycle": 1,
        "max_versions_per_mechanism": 1,
        "max_provider_calls": 4,
        "max_l1_cases": 64,
    })
    config["archive"] = {"on": archive_on, "root": str(tmp_path / "candidates")}
    return config


def _split(cases: list[str], *, validation: list[str] | None = None) -> EvolutionSplit:
    return EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], validation=list(validation or ()), mini=True,
    )


def _typed_dsl_candidate(
    family: str, cases: list[str], *, identifier: str = "IV-1-001",
) -> dict:
    return make_intervention(
        intervention_id=identifier,
        intervention_type="recall_trigger",
        hook="recall.trigger",
        packet_features=["has_tool_calls"],
        body={
            "predicate": PROGRAM["name"],
            "runner": "typed_dsl",
            "output_verdicts": list(PROGRAM["verdicts"]),
            "predicate_definition": PROGRAM,
            "trigger_verdicts": ["violated"],
        },
        expected_behavior="recall opens when the trajectory carries no tool call",
        target_polarity="recall",
        cycle=1,
        failure_family=family,
        failure_cases=cases,
    )


def _row(
    case: str, *, arm: str, gold: bool, prediction: bool | None,
    authority: str = "authoritative", fired: str = "",
) -> dict:
    """One development replay row, in the shape ``develop_rows`` produces."""
    proof = (
        {"deployment": {"fired": [{
            "intervention_id": fired, "event": "text_injected",
        }]}}
        if fired else {}
    )
    return {
        "case": case,
        "arm": arm,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "result": {
            "state": (
                "verified_error" if prediction else
                "verified_clean" if prediction is False else "unresolved"
            ),
            "decision_authority": authority,
            "runtime_valid": authority == "authoritative",
            "stage_status": {},
            "proof": proof,
        },
    }


class _FindifyProposer:
    """A scripted proposer that counts its calls and can be made to fail."""

    name = "scripted_defects"

    def __init__(self, candidate: dict) -> None:
        self.candidate = candidate
        self.calls: list[dict] = []
        self.rejection_feedback: list[dict] = []

    def set_rejection_feedback(self, feedback):
        self.rejection_feedback = [dict(item) for item in feedback or ()]

    def propose(self, families, *, cycle, max_proposals=3):
        self.calls.append({"cycle": int(cycle)})
        if not families:
            return ProposalBatch()
        bound = dict(self.candidate)
        bound["provenance"] = {
            "cycle": int(cycle),
            "failure_family": str(families[0]["failure_family"]),
            "failure_cases": list(families[0]["failure_cases"]),
        }
        bound.pop("content_hash", None)
        return ProposalBatch(
            candidates=[bound],
            notes=[{"generator": "scripted", "provider_called": False}],
        )


class _Proposer:
    """Adapts a scripted proposer to the engine's ``propose`` protocol."""

    def __init__(self, candidate: dict) -> None:
        self.inner = _FindifyProposer(candidate)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def propose(self, families, *, cycle, max_proposals=3):
        return self.inner.propose(families, cycle=cycle, max_proposals=max_proposals)


def _engine(
    tmp_path: Path,
    *,
    repairing: bool,
    archive_on: str = "off",
    validation: list[str] | None = None,
    candidate_program: bool = True,
) -> tuple[EvolutionEngine, list[str], EvolutionSplit, _FindifyProposer]:
    cases = _dataset(tmp_path / "data")
    config = _config(tmp_path, archive_on=archive_on)
    split = _split(cases, validation=validation)
    targets = [
        case for case in [*split.gain, *split.patrol_pool]
        if int(case.split("/")[1][:3]) % 2 == 0
    ]
    overrides = {"IV-1-D001": {case: True for case in targets}} if repairing else {}
    backend = MockDetectorBackend(
        base_predictions={case: False for case in targets},
        overrides=overrides,
    )
    proposer = _Proposer(_typed_dsl_candidate("FAM-placeholder", cases[:2]))
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True, proposer=proposer,
    )
    assert candidate_program  # the typed-DSL program is what makes a component
    return engine, cases, split, proposer


def _first_family(engine: EvolutionEngine) -> dict:
    preview = engine.preview_cycle(1)
    return next(
        family for family in preview["families"]
        if (family.get("mechanism") or {}).get("mechanism_id")
    )


# ==========================================================================
# 1. A target case that regresses is a refusal
# ==========================================================================

def test_l1_refuses_a_candidate_that_breaks_another_target_case():
    """Repairing one member of a family while losing another is not a pass.

    The target set is what the intervention claims to fix *and keep working*.
    Scoring it for improvement only let the two-case family below be certified
    by half: A/1 moves from missed to convicted, A/2 -- a true positive the
    baseline already had -- is lost, and L1 reported ``passed``.
    """
    result = check_l1(
        baseline_rows=[
            _row("A/1.json", arm="baseline", gold=True, prediction=False),
            _row("A/2.json", arm="baseline", gold=True, prediction=True),
        ],
        candidate_rows=[
            _row("A/1.json", arm="candidate", gold=True, prediction=True,
                 fired="IV-1-D001"),
            _row("A/2.json", arm="candidate", gold=True, prediction=False,
                 fired="IV-1-D001"),
        ],
        target_cases=["A/1.json", "A/2.json"],
        neighbor_cases=[],
        intervention_id="IV-1-D001",
    )

    assert result["passed"] is False
    # The two readings are separate fields on purpose: this candidate damaged
    # exactly what it claimed to repair, not something unrelated.
    assert result["target_regressed"] == ["A/2.json"]
    assert result["neighbor_regressed"] == []
    assert result["target_improved"] == ["A/1.json"]
    assert result["delta"]["tp_lost"] == 1
    assert result["delta"]["new_errors"] == 1
    assert any("target case" in reason for reason in result["reasons"])
    assert "target_regression" in result["reason_codes"]
    assert "paired_delta_regression" in result["reason_codes"]


def test_l1_still_passes_a_candidate_that_repairs_the_whole_target_set():
    """The stricter reading must not refuse a candidate that is actually good.

    Both target members move and nothing is lost, so the run is a pass -- the
    point of the fix is the regression, not a general tightening.
    """
    result = check_l1(
        baseline_rows=[
            _row("A/1.json", arm="baseline", gold=True, prediction=False),
            _row("A/2.json", arm="baseline", gold=True, prediction=False),
        ],
        candidate_rows=[
            _row("A/1.json", arm="candidate", gold=True, prediction=True,
                 fired="IV-1-D001"),
            _row("A/2.json", arm="candidate", gold=True, prediction=True,
                 fired="IV-1-D001"),
        ],
        target_cases=["A/1.json", "A/2.json"],
        neighbor_cases=[],
        intervention_id="IV-1-D001",
    )

    assert result["passed"] is True
    assert result["target_regressed"] == []
    assert result["reasons"] == []
    assert result["reason_codes"] == []


def test_l1_keeps_no_improvement_a_refusal():
    """"No target case improved" stays a refusal, not a neutral reading.

    The regression check was added beside the existing criterion rather than
    replacing it: a version that neither improved nor regressed a target is
    still not a version worth freezing.
    """
    unchanged = check_l1(
        baseline_rows=[_row("A/1.json", arm="baseline", gold=True, prediction=False)],
        candidate_rows=[_row("A/1.json", arm="candidate", gold=True, prediction=False)],
        target_cases=["A/1.json"], neighbor_cases=[],
    )

    assert unchanged["passed"] is False
    assert unchanged["target_unchanged"] == ["A/1.json"]
    assert unchanged["target_regressed"] == []
    assert "no_target_improvement" in unchanged["reason_codes"]


def test_l1_publishes_machine_readable_reason_codes():
    """The archive reads ``l1.reason_codes``; L1 has to produce them.

    ``ingest_development_run`` has always stored the codes it found, and the
    producer never wrote the key, so every archived failure carried ``[]``.
    """
    result = check_l1(
        baseline_rows=[_row("A/1.json", arm="baseline", gold=True, prediction=False),
                       _row("N/1.json", arm="baseline", gold=False, prediction=False)],
        candidate_rows=[_row("A/1.json", arm="candidate", gold=True, prediction=False),
                        _row("N/1.json", arm="candidate", gold=False, prediction=False)],
        target_cases=["A/1.json"],
        neighbor_cases=["N/1.json"],
        intervention_id="IV-1-D001",
    )

    assert result["reason_codes"], result
    assert len(result["reason_codes"]) == len(result["reasons"])
    assert all(code and " " not in code for code in result["reason_codes"])
    assert set(result["reason_codes"]) >= {
        "no_target_improvement", "hook_never_fired",
    }


def test_an_archived_l1_failure_carries_its_reason_codes(tmp_path):
    """End to end: what L1 produced reaches the archive's machine-readable half.

    This is the reading that was always empty: a rejection recorded with a
    prose reason and no code cannot be counted without parsing the sentence.
    """
    engine, cases, _split_, _proposer = _engine(tmp_path, repairing=False)
    family = _first_family(engine)
    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )
    engine.archive_on = "cycle_end"
    outcome = engine.archive_development_run(
        Path(block["runs"][0]["run_dir"]),
        cycle=1,
        mechanism_id=str((family["mechanism_detail"] or {}).get("mechanism_id") or ""),
        family_id=str(family["failure_family"]),
        mechanism=family.get("mechanism_detail"),
        force=True,
    )

    assert outcome["archived"] is True
    archived = load_candidates(engine.archive_root)
    assert archived, outcome
    failed = [row for row in archived if row["status"] == "failed_l1"]
    assert failed, [row["status"] for row in archived]
    assert failed[0]["reason_codes"], failed[0]
    assert " ".join(failed[0]["reason_codes"])


# ==========================================================================
# 2. "Not measured" is not "failed"
# ==========================================================================

def test_an_outage_is_recorded_as_unmeasured_not_as_a_falsification(tmp_path):
    """A replay that could not be scored must not be archived as a failure.

    The rows below are what a provider outage or a deadline produces: no
    prediction, no authority. Scoring them reads as "no target case improved",
    and the version was then stored as ``failed_l1`` -- "we measured this and it
    did not work" -- and handed to the next round as a negative example.
    """
    targets = ["A/1.json", "A/2.json"]

    def l1_runner(cases, *, candidate, plan):
        return [
            _row(case, arm=arm, gold=True, prediction=None, authority="invalid")
            for arm in ("baseline", "candidate") for case in targets
        ]

    developer = CandidateDeveloper(
        output_dir=tmp_path / "dev",
        budget=DevelopmentBudget(),
        mechanism={
            "mechanism_id": "M-outage",
            "detector_failure": {"stage": "recall_scanner"},
            "repair_hypothesis": {"operation": "route_recall"},
            "member_cases": targets,
        },
        capability_plan={"sufficient_routes": ["recall_trigger"]},
        target_cases=targets,
        neighbor_cases=[],
        l1_runner=l1_runner,
    )
    result = developer.develop(
        lambda *a, **k: [_typed_dsl_candidate("FAM-x", targets)], cycle=1,
    )

    version = result["versions"][0]
    assert version["status"] == "unmeasured", version
    assert version["l1"]["unmeasurable"] is True
    assert version["l1"]["reason_codes"] == ["unmeasurable"]
    assert result["status"] == "discarded"
    # The report says the version was never judged, so a reader cannot mistake
    # "l1_passed: false" for a falsification.
    report = development_report(result)
    assert report["versions"][0]["unmeasured"] is True
    # The sidecar on disk is the record the archive ingests.
    sidecar = json.loads(
        (tmp_path / "dev" / version["version_id"] / "development.json")
        .read_text(encoding="utf-8")
    )
    assert sidecar["status"] == "unmeasured"
    assert sidecar["l1"]["unmeasurable"] is True


def test_develop_rows_refuses_to_hand_back_unscorable_rows(tmp_path):
    """The runner raises rather than returning rows that read as a verdict.

    ``develop_rows`` is the one place that owns the backend for a development
    replay, so it is where a dead provider has to be reported: rows handed back
    are scored, and an unresolved row scores as "no improvement".
    """
    engine, cases, _split_, proposer = _engine(tmp_path, repairing=True)
    candidate = _typed_dsl_candidate("FAM-x", cases[:2], identifier="IV-1-001")

    with pytest.raises(DevelopmentUnmeasurable):
        # A deadline in the past fail-closes every row before it starts, which
        # is exactly what a stalled provider produces at the end of a cycle.
        engine.develop_rows(
            [cases[13]], candidate=candidate, split=engine.split, deadline=0.0,
        )
    # The proposer was never asked: this is the backend, not the drafting path.
    assert proposer.calls == []


def test_the_cycle_gives_the_development_replay_a_deadline(tmp_path):
    """The L1 runner runs under the cycle's own wall-clock budget.

    Without a deadline a stalled provider produced unresolved rows and the loop
    read them as "no target improvement", so the outage was archived as a
    falsified candidate. The deadline is not only computed here: the loop's
    replay must actually run under it, which is what the second half asserts.
    """
    engine, _cases, _split_, _proposer = _engine(tmp_path, repairing=False)
    engine.config["budget"]["cycle_timeout_seconds"] = 600
    deadline = engine._development_deadline()

    assert deadline is not None
    assert deadline > 0
    # No configured budget means no deadline -- absent, not zero.
    engine.config["budget"]["cycle_timeout_seconds"] = 0
    assert engine._development_deadline() is None

    # A budget too small for the replay to start fail-closes it, and the
    # version is reported as unmeasured rather than falsified. That can only
    # happen if the deadline reaches ``develop_rows``.
    engine.config["budget"]["cycle_timeout_seconds"] = 0.001
    family = _first_family(engine)
    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )
    versions = block["runs"][0]["versions"]
    assert versions, block
    assert versions[-1]["status"] == "unmeasured", versions[-1]
    assert versions[-1]["unmeasured"] is True
    assert block["runs"][0]["stopped"] == "no_measurement"


# ==========================================================================
# 3. The recovery branch names the version it recovers
# ==========================================================================

def test_recovery_fills_in_a_missing_intervention_id(tmp_path):
    """Certified work on disk is recovered, even without an id on it.

    The branch that does this referenced a caller-scope local named
    ``identifier``; the name does not exist inside ``_develop_candidates``, so
    the first entry into the branch raised ``NameError`` and lost the version
    the branch exists to save.
    """
    engine, _cases, _split_, _proposer = _engine(tmp_path, repairing=True)
    family = _first_family(engine)
    mechanism = family["mechanism_detail"]
    mechanism_id = str(mechanism["mechanism_id"])
    run_dir = (
        Path(engine.config["work_root"]) / "development" / "cycle001" / mechanism_id
    )
    graph = CandidateVersionGraph(run_dir)
    graph.load_existing()
    # A version on disk that this run certified but never used, written without
    # an intervention id -- the state a ``develop`` run or another path leaves.
    seeded = _typed_dsl_candidate(
        str(family["failure_family"]), list(family["failure_cases"]),
    )
    seeded.pop("intervention_id")
    graph.record(
        "development", "v001", seeded, parent=None, diagnosis=None,
        draft_notes=[], evaluation=None, verdict={}, status="frozen",
    )

    batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )

    run = block["runs"][0]
    assert run.get("recovered"), run
    assert run["recovered"]["version_id"] == "v001"
    assert run["frozen"]["intervention_id"] == "IV-1-D001"
    assert batch is not None
    assert [item["intervention_id"] for item in batch.candidates] == ["IV-1-D001"]


# ==========================================================================
# 4. A component written by a run is reachable by the next draft
# ==========================================================================

def test_an_archived_component_reaches_the_next_drafts_retrieval(tmp_path):
    """The store the archive writes is the store retrieval reads.

    They were two independently-computed paths -- the archive registered parts
    under ``archive.root`` while ``_reusable_components`` read
    ``component_root or work_root/components`` -- so with the default
    configuration no component a run produced could ever be retrieved, and the
    drafting prompt was built from an empty store while the archive reported a
    row.
    """
    engine, _cases, _split_, _proposer = _engine(
        tmp_path, repairing=True, archive_on="cycle_end",
    )
    family = _first_family(engine)
    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )

    run = block["runs"][0]
    assert run.get("frozen"), run
    archive = run["archive"]
    assert archive["archived"] is True, archive
    assert archive["components"], archive
    # The candidate half and the component half are read from their own roots.
    assert load_components(archive["component_root"]), archive
    reusable = engine._reusable_components([{
        "mechanism": family["mechanism_detail"],
    }])
    assert reusable, "the component the run registered is unreachable"
    assert reusable[0]["name"] == PROGRAM["name"]
    assert reusable[0]["capability"] == EvolutionEngine.component_capability(
        family["mechanism_detail"]
    )


def test_a_split_component_root_is_reported_not_silent(tmp_path):
    """The two stores are separate, and that is stated rather than implied.

    The component store and the candidate archive are two directories, so a
    reader that looks for a run's parts under ``archive_root`` finds nothing
    while the run registered one. The engine says where each store is; a
    deliberately-placed ``component_root`` moves the component half and says so.
    """
    cases = _dataset(tmp_path / "data")
    config = _config(tmp_path)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    assert engine.component_store != engine.archive_root
    assert engine.component_store.name == "components"
    assert engine.component_store_warning
    assert str(engine.component_store) in engine.component_store_warning

    # A configured root moves the store, and the warning names the new path.
    config["component_root"] = str(tmp_path / "parts")
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    assert engine.component_store == Path(tmp_path / "parts")
    assert str(Path(tmp_path / "parts")) in engine.component_store_warning
    # Aligning the two paths is possible, and then there is nothing to report.
    config["archive"] = {"on": "off", "root": str(tmp_path / "parts")}
    aligned = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    assert aligned.component_store == aligned.archive_root
    assert aligned.component_store_warning == ""


# ==========================================================================
# 5. A development falsification is remembered
# ==========================================================================

def test_a_development_falsification_is_written_to_the_episodic_store(tmp_path):
    """The gate path is not the only place an attempt is decided.

    A version refused by L1 never reaches a gate, so nothing wrote it to the
    episodic store -- and the next round's recall then had no record that this
    mechanism, along this route, had already been tried and failed.
    """
    engine, _cases, _split_, proposer = _engine(tmp_path, repairing=False)
    family = _first_family(engine)
    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )

    run = block["runs"][0]
    assert run["versions"][-1]["l1_passed"] is False
    episodes = load_episodes(engine.memory_root)
    assert episodes, "the development falsification was not remembered"
    episode = episodes[-1]
    assert episode["gate"] == "DEVELOPMENT"
    assert episode["outcome"] == "rejected"
    assert episode["failure_family"] == str(family["failure_family"])
    assert episode["unevaluable"] is False
    assert episode["reason"]
    # The stored id is qualified with the version. Every version of one
    # mechanism carries the same intervention id, and the store deduplicates on
    # (cycle, intervention_id, outcome), so an unqualified record would swallow
    # the gate's own episode for a later version of the same mechanism that did
    # reach the gates.
    assert episode["intervention_id"] == "IV-1-D001#v001"
    assert run["episodes"][0]["intervention_id"] == "IV-1-D001#v001"
    assert run["episodes"][0]["candidate_intervention_id"] == "IV-1-D001"
    # The failure is a falsification, not an unmeasurable attempt.
    assert [item for item in episodic_attempts(engine.memory_root)
            if item["falsified"]]
    # The record travels: the round reports what it wrote.
    assert [item["gate"] for item in run["episodes"]] == ["DEVELOPMENT"]

    # A development failure does not ban the mechanism from a later cycle.
    # ``set_rejection_feedback`` turns every non-unevaluable entry into a
    # fingerprint that refuses that executable mechanism permanently, which is
    # a stronger claim than "L1 refused this version".
    assert all(
        str(item.get("gate") or "") != "DEVELOPMENT"
        for item in engine.rejection_feedback
    )
    assert all(
        str(item.get("gate") or "") != "DEVELOPMENT"
        for item in proposer.rejection_feedback
    )


def test_a_development_episode_does_not_shadow_a_gate_episode(tmp_path):
    """The version qualifier is load-bearing, not decoration.

    Every version the loop drafts for one mechanism carries the same
    intervention id, and the episodic store deduplicates on
    ``(cycle, intervention_id, outcome)``. Without the qualifier, the record
    written for the version that failed L1 would suppress the gate's own record
    for the later version that passed L1 and reached the gates -- so a rejection
    there would be invisible to the next round's recall.
    """
    engine, _cases, _split_, _proposer = _engine(tmp_path, repairing=False)
    family = _first_family(engine)
    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )
    developed = block["runs"][0]["episodes"][0]
    assert developed  # the L1 falsification was written

    # The gate path writes its own episode for the same cycle and the same
    # intervention id, for a version that did reach a gate. Both must survive.
    engine.rejection_feedback.append({
        "cycle": 1,
        "intervention_id": "IV-1-D001",
        "candidate": {"intervention_id": "IV-1-D001", "type": "text_boundary",
                      "hook": "scanner.system"},
        "gate": "HARMLESSNESS",
        "reason": "sentinel regressed",
    })
    engine._record_cycle_experience(
        cycle=1,
        diagnosed_families=[family],
        gate_reports=[{
            "intervention_id": "IV-1-D001",
            "candidate": {
                "intervention_id": "IV-1-D001", "type": "text_boundary",
                "hook": "scanner.system",
                "provenance": {"failure_family": str(family["failure_family"])},
                "claim": {"target_polarity": "recall"},
            },
            "harmlessness": {"passed": False, "reason": "sentinel regressed"},
        }],
        rejected=[item for item in engine.rejection_feedback
                  if item.get("gate") == "HARMLESSNESS"],
        selected=[],
    )

    gates = {
        str(episode["gate"]) for episode in load_episodes(engine.memory_root)
    }
    assert {"DEVELOPMENT", "HARMLESSNESS"} <= gates, gates


def test_an_unmeasurable_development_version_is_not_a_falsification(tmp_path):
    """The distinction survives into the memory the next round recalls.

    A version whose replay could not be scored was never judged, so it is stored
    as an unevaluable attempt: visible as diagnostic context and explicitly not
    suppressed as a falsified mechanism.
    """
    engine, _cases, _split_, _proposer = _engine(tmp_path, repairing=True)
    family = _first_family(engine)
    engine.backend.overrides = {}  # nothing repairs: L1 cannot show improvement

    _batch, block = engine._develop_candidates(
        cycle=1, families=[family], capability_plan={"requirements": []},
    )
    # This path fails L1 on measurement, not on an outage; the unmeasurable
    # branch itself is covered by the unit test above. What is pinned here is
    # that the two are stored with different flags.
    episodes = load_episodes(engine.memory_root)
    assert episodes
    assert all(item["gate"] == "DEVELOPMENT" for item in episodes)
    assert all(item["unevaluable"] is False for item in episodes)


# ==========================================================================
# 6. The data-isolation guard covers every formal set
# ==========================================================================

def test_development_refuses_to_measure_on_the_validation_set(tmp_path):
    """``validation`` is the mini protocol's only out-of-sample reading.

    The guard computed ``sentinel | gain``, so a development replay could spend
    the one set the run is supposed to leave untouched.
    """
    cases = _dataset(tmp_path / "data")
    validation = cases[12:14]
    engine, _cases, _split_, _proposer = _engine(
        tmp_path, repairing=True, validation=validation,
    )
    candidate = _typed_dsl_candidate("FAM-x", cases[:2], identifier="IV-1-001")

    with pytest.raises(ValueError, match="formal evaluation cases"):
        engine.develop_rows(
            list(validation), candidate=candidate, split=engine.split,
        )
    # The formal sets the B gates score on are still refused as well.
    with pytest.raises(ValueError, match="formal evaluation cases"):
        engine.develop_rows(
            list(engine.split.sentinel), candidate=candidate, split=engine.split,
        )


def test_development_still_measures_patrol_findings(tmp_path):
    """The guard must not refuse the cases development is *meant* to spend."""
    engine, cases, _split_, _proposer = _engine(tmp_path, repairing=True)
    candidate = _typed_dsl_candidate("FAM-x", cases[:2], identifier="IV-1-001")
    rows = engine.develop_rows(
        [cases[13]], candidate=candidate, split=engine.split,
    )
    assert {row["arm"] for row in rows} == {"baseline", "candidate"}


# ==========================================================================
# The stopped reason: budget exhaustion is not an empty answer
# ==========================================================================

def test_an_empty_draft_reports_the_proposers_own_reason(tmp_path):
    """``no_candidates`` alone cannot say why there were none.

    A proposer whose own call budget is spent and a proposer that answers with
    nothing are different situations, and the round used to record the same
    word for both.
    """
    developer = CandidateDeveloper(
        output_dir=tmp_path / "dev",
        budget=DevelopmentBudget(),
        mechanism={
            "mechanism_id": "M-stop",
            "detector_failure": {"stage": "recall_scanner"},
            "repair_hypothesis": {"operation": "route_recall"},
            "member_cases": ["A/1.json", "A/2.json"],
        },
        capability_plan={"sufficient_routes": ["recall_trigger"]},
        target_cases=["A/1.json", "A/2.json"],
        neighbor_cases=[],
    )
    spent = developer.develop(lambda *a, **k: ProposalBatch(notes=[{
        "generator": "llm",
        "provider_called": False,
        "reason": "llm_calls budget exhausted for this cycle",
    }]), cycle=1)
    assert spent["stopped"] == "no_candidates"
    assert spent["stopped_note"]["stopped_reason"] == "budget_exhausted"
    assert "budget" in spent["stopped_note"]["reason"]
    assert spent["budget"]["used"]["provider_calls"] == 0

    silent = developer.develop(
        lambda *a, **k: ProposalBatch(notes=[{
            "generator": "hybrid",
            "provider_called": True,
            "reason": "no intervention in the provider response survived the "
                      "local contract",
        }]), cycle=1,
    )
    assert silent["stopped_note"]["stopped_reason"] == "no_draft_returned"
    assert silent["budget"]["used"]["provider_calls"] == 1

    # A caller that injects a bare list has no notes, and that is reported as
    # absent rather than invented.
    bare = developer.develop(lambda *a, **k: [], cycle=1)
    assert bare["stopped"] == "no_candidates"
    assert bare["stopped_note"] == {}
    assert bare["budget"]["used"]["provider_calls"] == 2
    assert development_report(bare)["stopped_note"] == {}
