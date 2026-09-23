"""The development loop as a stage inside a cycle (plan §23.3.6).

Before this wiring existed, ``CandidateDeveloper`` (draft -> L0 -> L1 -> revise
-> freeze) was reachable only by typing ``python -m evolution develop``: the
cycle went straight from the proposer to the four gates, so a single draft cost
a full paired promotion run. The first real cycle showed what that costs -- one
drafted candidate, 62 minutes, rejected.

These tests pin the three claims the wiring makes:

1. with the loop off the round is byte-for-byte the old behaviour;
2. with the loop on, candidates that fail L0/L1 are *not* carried to the gates,
   and the round says so instead of silently drafting again;
3. a version that passes is the one that reaches the gates, and the report says
   which path it travelled.
"""

from __future__ import annotations

import json
from pathlib import Path

from evolution.backends import MockDetectorBackend
from evolution.config import DEFAULTS, load_config
from evolution.contracts import make_intervention
from evolution.development import DEVELOPMENT_SCHEMA
from evolution.engine import EvolutionEngine
from evolution.proposer import OfflineProposer, ProposalBatch
from evolution.splits import EvolutionSplit


def _make_dataset(root: Path, n: int = 20) -> list[str]:
    cases = []
    for index in range(n):
        case_id = f"A/{index:03d}.json"
        cases.append(case_id)
        path = root / case_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "question": f"task {index}",
                "history": [
                    {"step": 1, "role": "assistant", "content": "answer"}
                ],
                "is_hallucination": bool(index % 2 == 0),
                "hallucination_step": 1 if index % 2 == 0 else None,
                "true_answer": "gold",
            }),
            encoding="utf-8",
        )
    return cases


def _config(tmp_path: Path, *, enabled: bool, archive_on: str = "off") -> dict:
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["split_manifest"] = str(tmp_path / "manifest.json")
    config["mini_split_manifest"] = str(tmp_path / "mini_manifest.json")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["harness_dir"] = str(tmp_path / "harness")
    config["memory_root"] = str(tmp_path / "memory")
    config["backend"]["name"] = "mock"
    config["backend"]["cache"] = False
    config["budget"]["patrol_cases"] = 8
    config["proposer"]["mode"] = "offline"
    config["development"]["enabled"] = enabled
    config["archive"] = {"on": archive_on, "root": str(tmp_path / "candidates")}
    return config


def _scanner_candidate(*, cycle: int = 1, family: str = "FAM-fn") -> dict:
    return make_intervention(
        intervention_id=f"IV-{cycle}-001",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report missing required actions.",
            "not_when": "Never when all required actions are present.",
        },
        expected_behavior="recall gain on planted FN",
        target_polarity="recall",
        cycle=cycle,
        failure_family=family,
        failure_cases=["A/000.json", "A/001.json"],
    )


class _CountingProposer:
    """A scripted proposer that also reports how many times it was asked.

    The count is the reading that separates the two paths: a single-shot round
    asks once and keeps whatever comes back, while a development round asks once
    per version and keeps only a version that survived a replay.
    """

    name = "scripted_counting"

    def __init__(self, candidate: dict) -> None:
        self.candidate = candidate
        self.calls: list[dict] = []
        self.feedback_seen: list[list] = []
        self.rejection_feedback: list = []

    def set_rejection_feedback(self, feedback):
        self.rejection_feedback = [dict(item) for item in feedback or ()]

    def propose(self, families, *, cycle, max_proposals=3):
        self.calls.append({
            "cycle": int(cycle),
            "families": [str(f.get("failure_family") or "") for f in families],
            "feedback": list(self.rejection_feedback),
        })
        if not families:
            # A round with nothing diagnosed still reaches drafting; the real
            # proposers answer that with an empty batch rather than an error.
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
            joint_groups=[],
            notes=[{
                "intervention_id": str(bound.get("intervention_id") or ""),
                "generator": "scripted",
                "provider_called": False,
            }],
        )


def _split(cases: list[str]) -> EvolutionSplit:
    return EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )


def _planted(cases: list[str]) -> tuple[dict, list[str], list[str]]:
    """The planted failures: two missed patrol cases and two gain targets.

    ``base_predictions`` marks both pairs as missed by the bare base, so the
    patrol diagnoses a recall mechanism and the gain set carries two repairable
    cases. The sentinel is left alone -- the mock defaults to gold there -- so
    the protection set is intact.
    """
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    base = {case: False for case in patrol_true + gain_true}
    return base, patrol_true, gain_true


def _repairs(identifier: str, *groups: list[str]) -> dict[str, dict[str, bool]]:
    """A mock override making ``identifier`` convict every named case."""
    return {
        identifier: {
            case: True for group in groups for case in group
        }
    }


# --------------------------------------------------------------------------
# 1. The default is unchanged behaviour
# --------------------------------------------------------------------------


def test_loop_off_reports_single_shot_and_asks_the_proposer_once(tmp_path):
    """With ``development.enabled`` false nothing about the round changes."""
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=False)
    base, patrol_true, gain_true = _planted(cases)
    proposer = _CountingProposer(_scanner_candidate())
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=proposer,
    )
    report = engine.run_cycle(1)

    assert engine.development_enabled is False
    assert report["round_path"] == "single_shot"
    assert report["development"] is None
    # One drafting call, the candidate goes straight to the gates, and the
    # planted gain cases are still repaired -- i.e. the single-shot path still
    # works, not merely that the new branch is skipped.
    assert len(proposer.calls) == 1
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]
    assert report["active_after_cycle"] == ["IV-1-001"]
    assert report["active_effect_on_g"]["marginal_cases"] >= 1
    assert not (tmp_path / "work" / "development").exists()


def test_loop_off_leaves_no_development_ledger_event(tmp_path):
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=False)
    base, patrol_true, gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    engine.run_cycle(1)
    events = [record["event"] for record in engine.ledger.read()]
    assert "development" not in events


# --------------------------------------------------------------------------
# 2. Enabled: a candidate that fails its replay never reaches the gates
# --------------------------------------------------------------------------


def test_loop_on_rejects_before_the_gates_and_does_not_draft_again(tmp_path):
    """The candidate is falsified by L1, so no gate sees it -- and no second
    draft is drawn behind the loop's back.

    The second half is the claim that matters. Falling back to single-shot
    drafting whenever development certified nothing would make the loop a
    no-op that costs extra calls: every round would end with the same
    draft-and-hope candidate as before.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    base, _patrol_true, _gain_true = _planted(cases)
    # No override for the candidate: the mock's own execution records it as
    # fired, but it convicts nothing the base did not already convict, so the
    # targeted replay cannot show a target improvement.
    proposer = _CountingProposer(_scanner_candidate())
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=proposer,
    )
    report = engine.run_cycle(1)

    assert report["round_path"] == "development"
    development = report["development"]
    assert development["schema_version"] == "evolution.cycle_development.v1"
    assert development["targets"] == 1
    assert development["candidate_count"] == 0
    assert development["frozen_versions"] == []
    run = development["runs"][0]
    assert run["status"] == "discarded"
    assert run["selected_version_id"] in (None, "")
    versions = run["versions"]
    assert versions, "the loop recorded no version at all"
    assert versions[-1]["l1_passed"] is False
    assert versions[-1]["l1_reasons"], versions[-1]
    # The falsification is the loop's own verdict, not an absent measurement.
    assert run["budget"]["used"]["l1_cases"] > 0

    # No candidate reached the gates, and the round did not re-draft.
    assert report["proposals"] == []
    assert report["gates"] == []
    assert report["marginal_selection"]["selected"] == []
    assert report["active_after_cycle"] == []
    assert len(proposer.calls) == len(versions), (
        "the proposer was asked more times than the loop measured versions"
    )

    events = [record["event"] for record in engine.ledger.read()]
    assert "development" in events
    ledger_development = next(
        record["payload"] for record in engine.ledger.read()
        if record["event"] == "development"
    )
    assert ledger_development["candidate_count"] == 0
    assert ledger_development["targets"] == 1
    # The per-run detail stays out of the ledger entry: it is on disk under the
    # run directory and in the cycle report, and a ledger is a hash chain, not
    # a scratch pad.
    assert "runs" not in ledger_development


def test_loop_on_revision_sees_the_previous_verdict(tmp_path):
    """A revision is asked with the falsification attached.

    Without the feedback the loop would re-ask the identical question and, with
    a deterministic proposer, get the identical candidate: the version graph
    would fill with copies and the run would spend its whole budget learning
    nothing.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    config["development"]["max_versions_per_mechanism"] = 2
    # One L1 replay costs two arms over the target *and* neighbour cases, and
    # the mechanism carries six neighbours, so the default cap would stop the
    # loop after a single version -- before the revision this test is about.
    config["development"]["max_l1_cases"] = 64
    base, _patrol_true, _gain_true = _planted(cases)
    proposer = _CountingProposer(_scanner_candidate())
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=proposer,
    )
    report = engine.run_cycle(1)

    assert len(proposer.calls) == 2, proposer.calls
    first, second = proposer.calls
    assert first["feedback"] == []
    assert second["feedback"], "the revision was asked without any diagnosis"
    reasons = str(second["feedback"][0].get("reason") or "")
    assert reasons, second["feedback"]
    # The parent travels with the feedback, so the revision is a revision of a
    # named candidate rather than a fresh guess.
    assert second["feedback"][0].get("candidate")
    # Both versions are recorded, and the second is a child of the first.
    versions = report["development"]["runs"][0]["versions"]
    assert [item["version_id"] for item in versions] == ["v001", "v002"]
    assert versions[1]["parent_version_id"] == "v001"


# --------------------------------------------------------------------------
# 3. Enabled: a passing version reaches the gates, and is the one that does
# --------------------------------------------------------------------------


def test_loop_on_carries_the_frozen_version_into_the_gates(tmp_path):
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    base, patrol_true, gain_true = _planted(cases)
    # The candidate repairs the patrol failures it was developed against and
    # the gain targets, so L1 can show a target improvement and the same
    # override then carries it through the gates.
    proposer = _CountingProposer(_scanner_candidate())
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-D001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=proposer,
    )
    report = engine.run_cycle(1)

    assert report["round_path"] == "development"
    development = report["development"]
    assert development["candidate_count"] == 1
    run = development["runs"][0]
    assert run["selected_version_id"] == "v001"
    assert run["versions"][-1]["l0_passed"] is True
    assert run["versions"][-1]["l1_passed"] is True
    assert run["frozen"]["version_id"] == "v001"
    # The candidate that reached the gates is the frozen version, under the id
    # the drafter assigned -- not a second draft drawn afterwards.
    assert [item["intervention_id"] for item in report["proposals"]] == ["IV-1-D001"]
    assert len(proposer.calls) == 1
    assert report["marginal_selection"]["selected"] == ["IV-1-D001"]
    assert report["active_after_cycle"] == ["IV-1-D001"]
    # The version's status on disk is the one the promotion path accepts.
    sidecar = json.loads((
        Path(run["run_dir"]) / "v001" / "development.json"
    ).read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == DEVELOPMENT_SCHEMA
    assert sidecar["status"] == "frozen"


def test_development_ids_are_unique_across_mechanisms(tmp_path):
    """Two mechanisms developed in one cycle must not share an intervention id.

    A proposer numbers its batch from one on every call, so without the
    drafter assigning the id both mechanisms' candidates would arrive as
    ``IV-<cycle>-001`` and the proposal contract (unique ids in a batch) would
    reject the round for a reason that has nothing to do with the candidates.
    Two mechanisms are supplied directly: a synthetic patrol diagnoses one
    mechanism, and manufacturing a second would test the diagnoser rather than
    the drafting.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    config["development"]["max_mechanisms_per_cycle"] = 2
    base, patrol_true, gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides={
                **_repairs("IV-1-D001", patrol_true, gain_true),
                **_repairs("IV-1-D002", patrol_true, gain_true),
            },
        ),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    # A real diagnosed family, cloned under a second mechanism id: the
    # mechanism shape is what makes a family developable, and cloning it keeps
    # both targets structurally valid.
    preview = engine.preview_cycle(1)
    diagnosed = next(
        family for family in preview["families"]
        if (family.get("mechanism") or {}).get("mechanism_id")
    )
    second = json.loads(json.dumps(diagnosed))
    second["mechanism"]["mechanism_id"] = "M-clone-for-id-test"
    second["failure_family"] = (
        str(diagnosed["failure_family"]) + "-clone-for-id-test"
    )
    batch, block = engine._develop_candidates(
        cycle=1,
        families=[diagnosed, second],
        capability_plan={"requirements": []},
    )

    assert block["targets"] == 2, block["skipped"]
    frozen_ids = [
        run["frozen"]["intervention_id"]
        for run in block["runs"] if run.get("frozen")
    ]
    assert len(frozen_ids) == 2, block["runs"]
    assert len(frozen_ids) == len(set(frozen_ids))
    # The batch the gates would receive carries those ids and nothing else.
    assert batch is not None
    assert [c["intervention_id"] for c in batch.candidates] == frozen_ids
    assert len({c["intervention_id"] for c in batch.candidates}) == len(
        batch.candidates
    )
    # Each candidate remains bound to its own mechanism's family.
    families = [
        str((c.get("provenance") or {}).get("failure_family") or "")
        for c in batch.candidates
    ]
    assert len(set(families)) == 2


def test_development_never_measures_on_the_formal_sets(tmp_path):
    """Development spends patrol findings, never sentinel or gain cases.

    Spending a formal case during development turns it into development
    exposure, and the promotion protocol's candidate-unused rule would then be
    guarding a number the candidate had already been measured on.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    base, patrol_true, gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-D001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    development = report["development"]
    formal = {str(case) for case in [*engine.split.sentinel, *engine.split.gain]}
    for run in development["runs"]:
        for key in ("target_cases", "neighbor_cases"):
            measured = {str(case) for case in run.get(key) or ()}
            assert not (measured & formal), (key, sorted(measured & formal))


def test_development_skips_ineligible_mechanisms_with_a_reason(tmp_path):
    """A mechanism that cannot carry a valid candidate is reported, not dropped.

    A candidate's provenance must name a family with two or more patrol cases;
    a single-case mechanism therefore cannot be developed at all, and the round
    has to say so rather than appear to have found nothing to do.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    base, _patrol_true, _gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    single = [{
        "failure_family": "FAM-fn-abc",
        "count": 1,
        "failure_cases": ["A/012.json"],
        "mechanism": {"mechanism_id": "M-abc", "member_cases": ["A/012.json"]},
    }]
    batch, block = engine._develop_candidates(
        cycle=1, families=single, capability_plan={"requirements": []},
    )
    assert batch is None
    assert block["targets"] == 0
    assert block["reason"]
    assert block["skipped"] and block["skipped"][0]["failure_family"] == "FAM-fn-abc"
    assert "two" in block["skipped"][0]["reason"]


def test_development_cap_is_reported_as_skipped(tmp_path):
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    config["development"]["max_mechanisms_per_cycle"] = 1
    base, _patrol_true, _gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    engine.development_max_mechanisms = 1

    def family(index: int, first: str, second: str) -> dict:
        # Patrol-pool cases only: development refuses to measure on the
        # sentinel or gain sets, so a fixture drawn from them would fail the
        # data-separation check instead of testing the cap.
        return {
            "failure_family": f"FAM-fn-{index}",
            "count": 2,
            "failure_cases": [first, second],
            "mechanism": {
                "mechanism_id": f"M-{index}",
                "member_cases": [first, second],
            },
        }

    _batch, block = engine._develop_candidates(
        cycle=1,
        families=[
            family(1, "A/012.json", "A/013.json"),
            family(2, "A/016.json", "A/017.json"),
        ],
        capability_plan={"requirements": []},
    )
    assert block["targets"] == 1
    assert len(block["skipped"]) == 1
    assert "max_mechanisms_per_cycle" in block["skipped"][0]["reason"]


# --------------------------------------------------------------------------
# 4. §28.6 cycle_end archiving now has a run path
# --------------------------------------------------------------------------


def test_cycle_end_archive_writes_the_development_run(tmp_path):
    """``archive.on=cycle_end`` records what the cycle's development produced.

    Until the loop ran inside a cycle there was no development run directory for
    this policy to ingest: the value was accepted by validation and reachable
    from nowhere, which is why the real run's ``candidates/`` directory did not
    exist.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True, archive_on="cycle_end")
    base, patrol_true, gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-D001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    archive = report["development"]["runs"][0]["archive"]
    assert archive["archived"] is True, archive
    assert archive["policy"] == "cycle_end"
    assert archive["recorded"] >= 1, archive
    stored = list(Path(config["archive"]["root"]).rglob("*.json"))
    assert stored, "the archive root is empty"


def test_cycle_end_archive_is_not_upgraded_from_off(tmp_path):
    """With the policy off, the round writes no archive.

    A storage path that quietly writes under a different policy than the
    configured one is worse than an empty store: the run's own record would
    disagree with what is on disk.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True, archive_on="off")
    base, patrol_true, gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides=_repairs("IV-1-D001", patrol_true, gain_true),
        ),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    run = report["development"]["runs"][0]
    assert run.get("archive") is None
    assert run["archive_policy"] == "off"
    assert not Path(config["archive"]["root"]).exists()


# --------------------------------------------------------------------------
# 5. The path is reported on the stopped-early branch too
# --------------------------------------------------------------------------


def test_preflight_block_names_not_reached(tmp_path):
    """A round that stops before drafting names that explicitly.

    An absent field cannot be told apart from a build that never had the
    concept, so the stopped branch says ``not_reached`` rather than omitting
    ``round_path``.
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    base, patrol_true, _gain_true = _planted(cases)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=OfflineProposer(),
    )
    report = engine._stop_preflight(1, {
        "reason": "no diagnosed failure direction has repair opportunities in gain",
        "blocked_families": [{"failure_family": "FAM-fn-xyz"}],
        "sentinel": {},
        "gain_opportunity": {},
    }, baseline_gain=(), audit=None, patrol={})
    assert report["round_path"] == "not_reached"
    assert report["development"] is None
    assert report["state"] == "blocked_evaluation_preflight"
    assert patrol_true  # the fixture plants the failures the patrol would find


# --------------------------------------------------------------------------
# 6. A loaded config owns its blocks (defect found by these tests)
# --------------------------------------------------------------------------


def test_loaded_config_does_not_share_mutable_blocks_with_defaults():
    """Mutating a loaded config must not change what later loads see.

    This defect surfaced while wiring the loop: ``_deep_merge`` returned a new
    top-level dict, but a block the config file did not mention was carried
    over by reference, so six blocks (``archive``, ``development``,
    ``coverage_response``, ``patrol_registry``, ``harness_dir``,
    ``memory_root``) were the *same objects* as ``DEFAULTS``. A test enabling
    the development loop therefore enabled it for every later test in the
    process, and the same held for the archive policy -- which is how the
    failure was found: tests that passed alone failed in a full run.
    """
    before = {
        "development": json.loads(json.dumps(DEFAULTS["development"])),
        "archive": json.loads(json.dumps(DEFAULTS["archive"])),
    }
    loaded = load_config()
    loaded["development"]["enabled"] = True
    loaded["development"]["max_mechanisms_per_cycle"] = 99
    loaded["archive"]["on"] = "cycle_end"
    loaded["archive"]["root"] = "somewhere"

    assert DEFAULTS["development"] == before["development"]
    assert DEFAULTS["archive"] == before["archive"]
    fresh = load_config()
    assert fresh["development"]["enabled"] is False
    assert fresh["development"]["max_mechanisms_per_cycle"] == 1
    assert fresh["archive"]["on"] == "off"
    # The merge itself must still work: a value the config file sets wins, and
    # a default it does not mention survives.
    assert fresh["budget"]["patrol_cases"] == 20
    assert fresh["backend"]["name"] == "detector"


def test_loop_on_without_a_developable_mechanism_falls_back_to_single_shot(
    tmp_path,
):
    """Enabling the loop does not forbid the single-shot path.

    ``proposal_families`` can be empty while a family was still diagnosed --
    the preflight blocks a round only when a family exists *and* nothing is
    measurable, and a round with no diagnosed family at all proceeds. Turning
    the loop on must not turn those rounds into "no candidates ever".
    """
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path, enabled=True)
    # No injected failures: nothing is diagnosed, so nothing is developable.
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions={}, overrides={}),
        split=_split(cases),
        mini=True,
        proposer=_CountingProposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    assert report["state"] == "completed"
    assert report["development"]["candidate_count"] == 0
