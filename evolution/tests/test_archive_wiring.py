"""§28.6: the two stores were writable but nothing wrote them.

``archive.archive_candidate`` / ``ingest_development_run`` / ``record_component``
existed and had tests, and no run path called them. That is the shape of a
capability that looks finished and is not: the storage works, so a test of the
storage passes, and the question nobody answered is *who writes it, and when*.

These tests pin the answered version of that question:

* the policy is declared in configuration and defaults to ``off``, so adding
  the wiring cannot change what an existing run records;
* archiving happens when a development run **ends**, which is the point at
  which a version has left the loop and its verdicts are final;
* every version is archived -- pass *and* fail -- because "we already tried
  this" is the question the store exists to answer;
* components come only from versions that passed L1, since local usefulness is
  the claim a component makes;
* neither store can reach the Active Registry, whatever the policy says.

One patrol serves the whole module, and the runs it produces are module-scoped
fixtures. Patrol is the expensive step in this harness, and it does not depend
on the mock's per-candidate overrides -- those change only what a development
replay measures -- so running it once and varying the overrides is both faster
and a truer test of the thing under scrutiny.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from evolution.archive import (
    archive_report,
    candidates_for_mechanism,
    load_candidates,
    load_components,
    repeated_failure_reasons,
    retrieve_components,
)
from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.contracts import make_intervention
from evolution.development import CandidateDeveloper, DevelopmentBudget
from evolution.engine import EvolutionEngine
from evolution.splits import EvolutionSplit

#: ``resolve_path``'s base, i.e. the repository root, for the assertions below.
ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# A synthetic workspace, offline
# --------------------------------------------------------------------------

def _workspace(root: pathlib.Path, n: int = 16):
    data = root / "data"
    cases = []
    for index in range(n):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"task {index}",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
            "true_answer": "gold",
        }), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"b_stage": {"evolution_set": {
            "size": len(cases), "cases": cases,
        }}}),
        encoding="utf-8",
    )
    config = load_config()
    config.update({
        "dataset_root": str(data),
        "split_manifest": str(root / "manifest.json"),
        "mini_split_manifest": str(root / "manifest.json"),
        "work_root": str(root / "work"),
        "registry_dir": str(root / "registry"),
        "ledger_path": str(root / "ledger.jsonl"),
        "harness_dir": str(root / "harness"),
        "memory_root": str(root / "memory"),
    })
    config["backend"].update({"name": "mock", "cache": False})
    # ``preview_cycle`` drafts proposals, and the default proposer mode is
    # ``llm``: without this line the patrol would every so often reach the live
    # provider. The development-loop tests in this suite inject an offline
    # proposer for a reason, and a mock backend does not imply an offline
    # proposer.
    config["proposer"]["mode"] = "offline"
    config["budget"].update({"patrol_cases": 8, "stable_failure_rerun_cases": 4})
    config["audit"]["enabled"] = False
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    return config, split, cases


#: An inline typed-DSL program, spelled out because it is the object the
#: component store accepts: a stable digest, a closed vocabulary, and two
#: consumers. A deterministic built-in predicate would not do -- it is
#: framework code, not something a run produced.
PROGRAM = {
    "schema_version": "detector.predicate_program.v1",
    "name": "evo_delivery_probe",
    "description": "Violated when the trajectory carries prose but no tool call.",
    "verdicts": ["satisfied", "violated", "not_applicable"],
    "conviction_verdicts": ["violated"],
    "rules": [
        {
            "conditions": [
                {"field": "tool_call_count", "operator": "equals", "value": 0},
            ],
            "verdict": "violated",
            "detail": "no tool call in the trajectory",
        }
    ],
    "default_verdict": "not_applicable",
    "default_detail": "no delivery probe matched",
}


def _candidate(family_id: str, cases: list[str], *, identifier: str = "IV-1-001") -> dict:
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
        failure_family=family_id,
        failure_cases=cases,
    )


class Harness:
    """One patrol, reused: the mechanism it found, and the wherewithal to run.

    A development run needs its own engine because the mock's overrides decide
    whether the candidate repairs anything, and the override map belongs to the
    backend. The patrol result does not depend on those overrides, so it is
    computed once here and each run gets its own engine over the same split.
    """

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        config, split, cases = _workspace(root)
        config["archive"]["on"] = "development_end"
        self.config = config
        self.split = split
        self.cases = cases
        self.targets = [
            case for case in [*split.gain, *split.patrol_pool]
            if int(case.split("/")[1][:3]) % 2 == 0
        ]
        probe = EvolutionEngine(
            config,
            backend=MockDetectorBackend(
                base_predictions={case: False for case in self.targets},
                overrides={},
            ),
            split=split,
            mini=True,
        )
        self.patrol_engine = probe
        preview = probe.preview_cycle(1)
        family = (preview.get("families") or [])[0]
        self.family = family
        self.mechanism = family["mechanism_detail"]
        self.requirement = next(
            item for item in (
                (preview.get("capability_plan") or {}).get("requirements") or ()
            )
            if item["mechanism_id"] == self.mechanism["mechanism_id"]
        )
        self.mechanism_cases = [str(case) for case in family["failure_cases"]]
        self.counter = 0

    def run_development(self, *, repairing: bool, label: str) -> dict:
        """Draft, measure and freeze one version; return the run's identity."""
        from evolution.contracts import make_registry

        self.counter += 1
        identifier = f"IV-T{self.counter:02d}"
        output = self.root / f"development_{label}"
        # Each run gets a clean registry: development reads the active entries
        # it was given, and reusing one would make the second run's candidate a
        # duplicate of the first's under the STRUCTURAL identity check.
        registry_dir = self.root / f"registry_{label}"
        config = dict(self.config)
        config["registry_dir"] = str(registry_dir)
        # Its own archive root as well as its own registry. Sharing one root
        # would make the failing run's store contain the passing run's frozen
        # record, and every assertion about "what this run archived" would then
        # be reading a sibling's writes.
        config["archive"] = {
            **dict(config.get("archive") or {}),
            "root": str(self.root / f"candidates_{label}"),
        }
        config["work_root"] = str(self.root / f"work_{label}")
        backend = MockDetectorBackend(
            base_predictions={case: False for case in self.targets},
            overrides=(
                {identifier: {case: True for case in self.targets}}
                if repairing else {}
            ),
        )
        engine = EvolutionEngine(
            config, backend=backend, split=self.split, mini=True,
        )
        candidate = _candidate(
            str(self.family["failure_family"]), self.mechanism_cases,
            identifier=identifier,
        )
        developer = CandidateDeveloper(
            output_dir=output,
            budget=DevelopmentBudget(max_versions_per_mechanism=3),
            mechanism=self.mechanism,
            capability_plan=self.requirement,
            target_cases=self.mechanism_cases,
            neighbor_cases=[],
            active_entries=engine.registry.entries,
            target_kind=str(self.family.get("target_kind") or "detection"),
            l1_runner=lambda cs, *, candidate, plan: engine.develop_rows(
                cs, candidate=candidate, split=self.split,
            ),
        )
        drafts = [candidate]
        result = developer.develop(
            lambda *a, **k: [drafts.pop(0)] if drafts else [], cycle=1,
        )
        if result.get("selected_version_id"):
            developer.freeze(str(result["selected_version_id"]))
        return {
            "identifier": identifier,
            "engine": engine,
            "result": result,
            "output": output,
            "make_registry": make_registry,
        }

    def archive(self, run: dict, *, policy: str | None = None, force: bool = False):
        engine = run["engine"]
        if policy is not None:
            engine.archive_on = policy
        return engine.archive_development_run(
            run["output"],
            cycle=1,
            mechanism_id=str(self.mechanism["mechanism_id"]),
            family_id=str(self.family["failure_family"]),
            mechanism=self.mechanism,
            force=force,
        )


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """One patrol for the module, instead of one per test."""
    return Harness(tmp_path_factory.mktemp("archive_wiring"))


@pytest.fixture(scope="module")
def passing_run(harness):
    return harness.run_development(repairing=True, label="pass")


@pytest.fixture(scope="module")
def failing_run(harness):
    return harness.run_development(repairing=False, label="fail")


# --------------------------------------------------------------------------
# The policy is declared, and off by default
# --------------------------------------------------------------------------

def test_the_archive_policy_defaults_to_off():
    """Adding the wiring must not change what an existing run records."""
    config = load_config()
    assert config["archive"]["on"] == "off"
    assert config["archive"]["root"] == ""


def test_a_relative_archive_root_is_resolved_at_load_time(tmp_path, monkeypatch):
    """``archive.root`` is the one configured path that was not resolved.

    Every other configured path is resolved against the repository root by
    ``load_config``, so downstream code cannot depend on the caller's cwd.
    ``archive.root`` was read verbatim, which made a relative value mean
    "relative to whatever directory this cycle happened to be launched from":
    two runs of the same configuration from different cwds archived into two
    silently different trees, and the cycle report echoed a relative path
    beside absolute ``run_dir`` values.
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "schema_version": "evolution.framework.v1",
        "archive": {"on": "cycle_end", "root": "artifacts/some-run/candidates"},
    }), encoding="utf-8")
    loaded = load_config(config_path)
    root = loaded["archive"]["root"]
    assert pathlib.Path(root).is_absolute(), root
    # Resolved against the repository root, like every other configured path.
    assert pathlib.Path(root) == (
        ROOT / "artifacts" / "some-run" / "candidates"
    ).resolve()

    # The same string resolves identically from another cwd, which is the
    # property that makes the archive one tree rather than two.
    monkeypatch.chdir(tmp_path)
    again = load_config(config_path)["archive"]["root"]
    assert again == root

    # An empty root keeps its meaning: the engine derives work_root/candidates.
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(
        '{"schema_version": "evolution.framework.v1", "archive": {"root": ""}}',
        encoding="utf-8",
    )
    assert load_config(empty_path)["archive"]["root"] == ""


def test_an_unknown_archive_policy_is_refused(tmp_path):
    config, split, _cases = _workspace(tmp_path)
    config["archive"]["on"] = "whenever"
    with pytest.raises(ValueError, match="not one of"):
        EvolutionEngine(
            config, backend=MockDetectorBackend(), split=split, mini=True,
        )


def test_off_writes_nothing_and_says_so(harness):
    """The refusal is reported, not raised: a cycle has already spent budget.

    A store root of its own, so "nothing was written" cannot be satisfied by a
    sibling test's writes.
    """
    run = harness.run_development(repairing=True, label="off_policy")
    outcome = harness.archive(run, policy="off")
    assert outcome["archived"] is False
    assert outcome["policy"] == "off"
    assert "archive.on is 'off'" in outcome["reason"]
    assert load_candidates(run["engine"].archive_root) == []
    assert load_components(run["engine"].archive_root) == []


# --------------------------------------------------------------------------
# With the policy on: every version, and components only from passes
# --------------------------------------------------------------------------

def test_a_passing_run_is_archived_with_its_component(harness, passing_run):
    """The frozen version is recorded, and its program becomes a component.

    The candidate record and the component are read from their own roots: the
    archive writes candidates under ``archive.root`` and components under
    ``component_store``, and reading both from one of them is how a run that
    registered a part appeared to have registered nothing (the store the next
    drafting prompt reads was the other one).
    """
    assert passing_run["result"].get("selected_version_id"), (
        "the repairing mock must pass L1"
    )
    outcome = harness.archive(passing_run)
    assert outcome["archived"] is True
    root = passing_run["engine"].archive_root
    components_root = passing_run["engine"].component_store
    candidates = load_candidates(root)
    assert candidates, outcome
    assert all(
        record["mechanism_id"] == harness.mechanism["mechanism_id"]
        for record in candidates
    )
    components = load_components(components_root)
    assert components, outcome["components"]
    assert components[0]["name"] == PROGRAM["name"]
    assert components[0]["digest"]
    # Stored under the capability the mechanism's *repair* requires, which is
    # the vocabulary retrieval matches on. An earlier version of this code read
    # the capability from cycle reports, which a ``develop`` run never
    # populates, so every component was stored unclassified and ranked below
    # every mechanism-matched part.
    assert components[0]["capability"], components[0]
    expected = EvolutionEngine.component_capability(harness.mechanism)
    assert components[0]["capability"] == expected
    # The archive's own report reads each store from its own root, so a split
    # layout still reports both halves.
    report = archive_report(components_root, component_root=components_root)
    assert report["components"]["total"] == len(components)
    assert archive_report(root)["candidates"]["total"] == len(candidates)
    assert report["metric_role"] == "diagnostic-only"


def test_a_failing_run_is_archived_with_its_reason(harness, failing_run):
    """Failure is the point of the store: it is what a later run recalls."""
    assert not failing_run["result"].get("selected_version_id"), (
        "this mock repairs nothing"
    )
    outcome = harness.archive(failing_run)
    assert outcome["archived"] is True
    root = failing_run["engine"].archive_root
    candidates = load_candidates(root)
    assert candidates
    assert all(str(record["status"]) != "frozen" for record in candidates)
    # A version that failed L1 was not shown to be useful, so none of its parts
    # may be offered to the next round's drafting prompt.
    assert load_components(failing_run["engine"].component_store) == []
    assert outcome["components"] == []


def test_archiving_is_idempotent(harness, passing_run):
    """The same run archived twice must not inflate its own history."""
    first = harness.archive(passing_run)
    second = harness.archive(passing_run)
    assert first["recorded"] == second["recorded"]
    assert first["report"]["candidates"]["total"] == (
        second["report"]["candidates"]["total"]
    )
    assert first["report"]["components"]["total"] == (
        second["report"]["components"]["total"]
    )


def test_archiving_can_be_forced_past_the_policy(harness):
    """``force`` is for a test or a one-off analysis, and is explicit."""
    run = harness.run_development(repairing=True, label="forced")
    outcome = harness.archive(run, policy="off", force=True)
    assert outcome["archived"] is True
    assert load_candidates(run["engine"].archive_root)


# --------------------------------------------------------------------------
# Neither store can reach the registry
# --------------------------------------------------------------------------

def test_the_archive_module_exports_no_path_to_the_active_registry():
    """The three-layer separation is structural, not documentary."""
    from evolution import archive

    names = [str(name) for name in archive.__all__]
    for forbidden in ("ActiveRegistry", "promote", "admit", "make_registry"):
        assert forbidden not in names, forbidden
    source = pathlib.Path(archive.__file__).read_text(encoding="utf-8")
    assert "ActiveRegistry" not in source
    assert "from evolution.registry" not in source


def test_archiving_leaves_the_registry_untouched(harness, passing_run):
    """A write to the stores must not add an entry to the deployed registry."""
    engine = passing_run["engine"]
    before = list(engine.registry.entries)
    harness.archive(passing_run)
    assert engine.registry.entries == before


def test_archived_candidates_are_recallable_by_mechanism(harness, failing_run):
    """The store's purpose: a later run can ask what was already tried."""
    mechanism_id = str(harness.mechanism["mechanism_id"])
    harness.archive(failing_run)
    root = failing_run["engine"].archive_root
    recalled = candidates_for_mechanism(root, mechanism_id)
    assert recalled
    reasons = repeated_failure_reasons(root, mechanism_id)
    assert reasons["candidates"] == len(recalled)
    # Counting is per candidate, so the total is not an inflated sum over the
    # two half-overlapping reason fields.
    assert sum(reasons["reasons"].values()) >= reasons["candidates"]


def test_a_component_registered_from_a_run_is_retrievable(harness, passing_run):
    """End to end: a run's part must be findable by the next round.

    The retrieval reads the same store the registration wrote. The two used to
    be different paths, which made the registration unreachable: the archive
    reported a component and the next round's drafting prompt saw an empty
    store.
    """
    outcome = harness.archive(passing_run)
    assert outcome["components"], outcome
    mechanism_id = str(harness.mechanism["mechanism_id"])
    capability = str(outcome["components"][0].get("capability") or "")
    assert capability, "a component without a capability cannot be ranked"
    found = retrieve_components(
        passing_run["engine"].component_store,
        mechanism_id=mechanism_id,
        capability=capability,
        limit=5,
    )
    assert found["components"], (capability, found)
    assert found["components"][0]["name"] == PROGRAM["name"]
    # And the engine's own retrieval -- the one the drafting prompt is built
    # from -- sees it too.
    reusable = passing_run["engine"]._reusable_components([{
        "mechanism": harness.mechanism,
    }])
    assert [item["name"] for item in reusable] == [PROGRAM["name"]], reusable


# --------------------------------------------------------------------------
# Store paths come from the modules that define them
# --------------------------------------------------------------------------

def test_every_experience_store_path_is_module_defined():
    """A store path must be read from its writer, not remembered.

    A reporting script named the memory store ``episodes.jsonl`` from memory;
    the store is ``episodic.jsonl``, so a run that had written one episode was
    reported as having written nothing. The names are therefore pinned here
    against the modules that define them, and this test fails if one is
    renamed without the readers being updated.
    """
    from evolution.gaps import GAP_STORE_NAME
    from evolution.memory import (
        CORRECTION_NAME,
        EPISODIC_NAME,
        IMPROVEMENT_NAME,
        PROCEDURAL_NAME,
        RECALL_NAME,
        memory_paths,
    )
    from evolution.engine import ROUTE_FAILURE_NAME

    assert EPISODIC_NAME == "episodic.jsonl"
    assert GAP_STORE_NAME == "gaps.jsonl"
    paths = memory_paths("root")
    for name, expected in (
        ("episodic", EPISODIC_NAME),
        ("procedural", PROCEDURAL_NAME),
        ("improvement", IMPROVEMENT_NAME),
        ("recall", RECALL_NAME),
        ("corrections", CORRECTION_NAME),
    ):
        assert paths[name].name == expected, name
    # The two harness stores share one directory, which is why a reader that
    # guessed the directory right and the name wrong could still look plausible.
    assert str(ROUTE_FAILURE_NAME).endswith(".jsonl")
    assert GAP_STORE_NAME.endswith(".jsonl")


def test_the_engine_writes_the_stores_it_declares():
    """The engine's store attributes are the module constants, not literals."""
    from evolution.engine import ROUTE_FAILURE_NAME
    from evolution.gaps import GAP_STORE_NAME

    config, split, _cases = _workspace(pathlib.Path(".") / "artifacts" / "_storepath_probe")
    try:
        engine = EvolutionEngine(
            config, backend=MockDetectorBackend(), split=split, mini=True,
        )
    finally:
        import shutil

        shutil.rmtree(engine.config["work_root"], ignore_errors=True)
        shutil.rmtree(engine.config["registry_dir"], ignore_errors=True)
        pathlib.Path("artifacts/_storepath_probe").exists() and shutil.rmtree(
            "artifacts/_storepath_probe", ignore_errors=True,
        )
    assert engine.gap_store.name == GAP_STORE_NAME
    assert engine.route_failure_store.name == ROUTE_FAILURE_NAME
    assert engine.component_store.name == "components"
