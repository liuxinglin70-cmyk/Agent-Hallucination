import json
from pathlib import Path

import pytest

from evolution.checkpoint import CycleJournal
from evolution.contracts import empty_registry


def test_checkpoint_rejects_old_schema_without_rewriting_it(tmp_path):
    path = tmp_path / "checkpoint.json"
    original = b'{"contract": {}, "status": "started"}'
    path.write_bytes(original)
    with pytest.raises(RuntimeError, match="schema"):
        CycleJournal(tmp_path, {}, resume=True)
    assert path.read_bytes() == original


@pytest.mark.parametrize("drift", ["config", "backend", "record"])
def test_completed_cycle_resume_checks_execution_contract_before_reusing_report(tmp_path, drift):
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.proposer import ProposalBatch
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_engine_smoke import _config, _make_dataset

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    backend = MockDetectorBackend()
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    try:
        report = engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        assert report["state"] == "completed"
        assert engine.run_cycle(1, resume=True) == report
        checkpoint = tmp_path / "work" / "checkpoints" / "cycle001" / "checkpoint.json"
        before = checkpoint.read_bytes()
        if drift == "config":
            engine.config["cycles"] += 1
        elif drift == "backend":
            backend.base_predictions[cases[0]] = False
        else:
            path = tmp_path / "data" / cases[0]
            record = json.loads(path.read_text(encoding="utf-8"))
            record["question"] = "changed synthetic task"
            path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(RuntimeError, match="fingerprint mismatch"):
            engine.run_cycle(1, resume=True)
        assert checkpoint.read_bytes() == before
    finally:
        engine.close()


def test_resume_keeps_completed_and_unresolved_observations(tmp_path):
    calls = []
    def evaluate(cases, **kwargs):
        case = cases[0]
        calls.append(case)
        if case == "second" and calls.count(case) == 1:
            raise RuntimeError("interrupted transport")
        return [{"case": case, "prediction": None, "decision_authority": "unresolved"}]

    journal = CycleJournal(tmp_path, {"input": "unchanged"}, resume=False)
    kwargs = dict(registry=empty_registry(), fresh=False, evaluate=evaluate, kwargs={"workers": 1})
    with pytest.raises(RuntimeError, match="interrupted"):
        journal.evaluate(["first", "second"], **kwargs)
    resumed = CycleJournal(tmp_path, {"input": "unchanged"}, resume=True)
    rows = resumed.evaluate(["first", "second"], **kwargs)
    assert calls == ["first", "second", "second"]
    assert [r["prediction"] for r in rows] == [None, None]
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        CycleJournal(tmp_path, {"input": "changed"}, resume=True)
    path = next((tmp_path / "arms").rglob("*.json"))
    envelope = json.loads(path.read_text())
    envelope["row"]["prediction"] = True
    path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="integrity"):
        resumed.evaluate(["first", "second"], **kwargs)


def test_resume_refuses_missing_proposal_or_historical_checkpoint(tmp_path):
    with pytest.raises(RuntimeError, match="no checkpoint"):
        CycleJournal(tmp_path, {}, resume=True)
    journal = CycleJournal(tmp_path, {}, resume=False)
    journal.save(proposing=True)
    with pytest.raises(RuntimeError, match="proposal call interrupted"):
        CycleJournal(tmp_path, {}, resume=True)


def test_cycle_resume_keeps_proposals_and_completed_arms(tmp_path, monkeypatch):
    from evolution.tests.test_engine_smoke import _make_dataset, _config
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.proposer import ProposalBatch
    from evolution.splits import EvolutionSplit

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    proposals = []
    class Proposer:
        name = "resume_test"
        def propose(self, *args, **kwargs):
            proposals.append(1)
            return ProposalBatch(candidates=[], joint_groups=[], notes=[])
    backend = MockDetectorBackend()
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True, proposer=Proposer())
    validate = engine._validate_proposal_batch
    def interrupt(*args, **kwargs):
        raise RuntimeError("interrupted after proposal persisted")
    monkeypatch.setattr(engine, "_validate_proposal_batch", interrupt)
    with pytest.raises(RuntimeError, match="after proposal"):
        engine.run_cycle(1)
    completed_paths = list((tmp_path / "work" / "checkpoints").rglob("arms/*/*.json"))
    assert completed_paths
    original_observations = {str(p): p.read_bytes() for p in completed_paths}
    engine.close()
    engine = EvolutionEngine(config, backend=MockDetectorBackend(), split=split,
                             mini=True, proposer=Proposer())
    report = engine.run_cycle(1, resume=True)
    assert report["state"] == "completed"
    assert proposals == [1]
    assert all(Path(p).read_bytes() == value for p, value in original_observations.items())
    assert engine.ledger.verify()["valid"]


def _resume_engine(tmp, config, split, backend, proposer, **kwargs):
    from evolution.engine import EvolutionEngine

    return EvolutionEngine(
        config, backend=backend, split=split, mini=True, proposer=proposer,
        **kwargs,
    )


def _resume_config(tmp, monkeypatch):
    from evolution.tests.test_engine_smoke import _config, _make_dataset
    from evolution.splits import EvolutionSplit

    cases = _make_dataset(tmp / "data", n=20)
    config = _config(tmp)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}
    return cases, config, split, base, overrides


def test_resume_after_committed_admission_reverts_its_own_write(tmp_path):
    """A cycle that admitted and then died must be resumable, not wedged.

    This is the gap the real interrupted run hit: the admission changed the
    registry hash, and the resume contract included that hash, so the cycle
    could never be resumed on the same ledger. The resumed run must evaluate
    against the registry the cycle started from, and must not admit twice.
    """
    from evolution.backends import MockDetectorBackend
    from evolution.harness import HARNESS_FILENAME, HARNESS_HISTORY_NAME
    from evolution.tests.test_engine_smoke import _scanner_candidate, _scripted_proposer

    cases, config, split, base, overrides = _resume_config(tmp_path, None)
    proposer = _scripted_proposer(_scanner_candidate())
    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    engine = _resume_engine(tmp_path, config, split, backend, proposer)
    engine._save_cycle_report = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("interrupted after registry write")
    )
    with pytest.raises(RuntimeError, match="interrupted after registry write"):
        engine.run_cycle(1)
    # The interrupted run really did commit its admission.
    assert [e["intervention_id"] for e in engine.registry.entries] == ["IV-1-001"]
    engine.close()

    resumed = _resume_engine(
        tmp_path, config, split,
        MockDetectorBackend(base_predictions=base, overrides=overrides),
        _scripted_proposer(_scanner_candidate()),
    )
    report = resumed.run_cycle(1, resume=True)
    assert report["state"] == "completed"
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]
    assert report["active_after_cycle"] == ["IV-1-001"]
    assert resumed.ledger.verify()["valid"] is True
    events = [record["event"] for record in resumed.ledger.read()]
    # Exactly one admission and one registry write, despite two attempts.
    assert events.count("admitted") == 1
    assert events.count("registry_written") == 1
    assert "resume_registry_restored" in events
    # The same generation is not recorded twice in the manifest history.
    history = [
        json.loads(line)
        for line in (tmp_path / "harness" / HARNESS_HISTORY_NAME)
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(history) == 1
    assert (tmp_path / "harness" / HARNESS_FILENAME).is_file()


def test_resume_after_measurement_reuses_journaled_observations(tmp_path):
    """Interrupting after the baselines must not re-evaluate them.

    Reuse is per (arm, case): the same case under a *different* registry is a
    genuinely different observation and must still be taken. So the check is
    that no bare-base observation is retaken, while the candidate arms (which
    the interrupted attempt never reached) still run.
    """
    from evolution.backends import MockDetectorBackend
    from evolution.tests.test_engine_smoke import _scanner_candidate, _scripted_proposer

    cases, config, split, base, overrides = _resume_config(tmp_path, None)
    # The defect-injection audit also calls _run_unit; disabling it makes the
    # interruption land where this test claims, after the patrol and baselines
    # have been observed and journaled.
    config["audit"]["enabled"] = False
    resume_calls: list[tuple[bool, str]] = []

    def counting_backend(sink):
        backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
        original = backend.run_case

        def spy(record, **kwargs):
            registry = kwargs.get("registry") or {}
            sink.append((
                not bool(registry.get("interventions")),
                str(kwargs.get("case_id") or ""),
            ))
            return original(record, **kwargs)

        backend.run_case = spy
        return backend

    engine = _resume_engine(
        tmp_path, config, split, counting_backend([]),
        _scripted_proposer(_scanner_candidate()),
    )
    engine._run_unit = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("interrupted during gates")
    )
    with pytest.raises(RuntimeError, match="interrupted during gates"):
        engine.run_cycle(1)

    journal = tmp_path / "work" / "checkpoints"
    journaled_cases = {
        json.loads(path.read_text(encoding="utf-8"))["row"]["case"]
        for path in journal.rglob("arms/*/*.json")
    }
    assert journaled_cases, "the first attempt must have journaled observations"
    engine.close()

    resumed = _resume_engine(
        tmp_path, config, split, counting_backend(resume_calls),
        _scripted_proposer(_scanner_candidate()),
    )
    report = resumed.run_cycle(1, resume=True)
    assert report["state"] == "completed"
    assert resume_calls, "the resume must still do the remaining work"
    # The candidate arms, which the first attempt never reached, still run.
    assert any(not empty for empty, _case in resume_calls)
    # No bare-base observation is retaken: those were replayed from the journal.
    retaken = sorted(
        case for empty, case in resume_calls
        if empty and case in journaled_cases
    )
    assert retaken == [], retaken


def test_resume_before_proposal_persistence_still_refuses(tmp_path):
    """A lost proposal cannot be re-drawn: that would be a new search."""
    from evolution.checkpoint import CycleJournal

    journal = CycleJournal(tmp_path, {"c": 1}, resume=False)
    journal.save(proposing=True)
    with pytest.raises(RuntimeError, match="refusing a new proposal draw"):
        CycleJournal(tmp_path, {"c": 1}, resume=True)


def test_resume_refuses_when_cycle_is_complete_but_registry_differs(tmp_path):
    """Inconsistent state must fail closed rather than silently re-run."""
    from evolution.backends import MockDetectorBackend
    from evolution.contracts import make_intervention, make_registry
    from evolution.tests.test_engine_smoke import _scanner_candidate, _scripted_proposer

    cases, config, split, base, overrides = _resume_config(tmp_path, None)
    engine = _resume_engine(
        tmp_path, config, split,
        MockDetectorBackend(base_predictions=base, overrides=overrides),
        _scripted_proposer(_scanner_candidate()),
    )
    engine._save_cycle_report = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("stop")
    )
    with pytest.raises(RuntimeError, match="stop"):
        engine.run_cycle(1)
    # Claim the cycle completed in the ledger, then diverge the registry.
    engine._ledger("cycle_completed", {"cycle": 1, "state": "completed"})
    extra = make_intervention(
        intervention_id="IV-9-009",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={"directive": "unrelated", "not_when": "never"},
        expected_behavior="unrelated",
        target_polarity="recall",
        cycle=9,
        failure_family="FAM-fn-x",
        failure_cases=["A/001.json", "A/002.json"],
    )
    engine.registry.restore(
        make_registry([extra]), reason="test divergence",
    )
    engine.close()
    resumed = _resume_engine(
        tmp_path, config, split,
        MockDetectorBackend(base_predictions=base, overrides=overrides),
        _scripted_proposer(_scanner_candidate()),
    )
    with pytest.raises(RuntimeError, match="already recorded complete"):
        resumed.run_cycle(1, resume=True)
