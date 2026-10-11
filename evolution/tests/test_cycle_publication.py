import json

import pytest

import evolution.checkpoint as checkpoint
from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset


def _engine(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    return EvolutionEngine(config, split=split, mini=True, backend=MockDetectorBackend())


def _state(tmp_path):
    path = tmp_path / "work/checkpoints/cycle001/checkpoint.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("boundary", ["report", "event", "report_after", "event_after", "commit_after"])
def test_committed_cycle_recovers_outputs_without_reexecuting(tmp_path, monkeypatch, boundary):
    with _engine(tmp_path) as engine:
        real_report = engine._save_cycle_report
        real_ledger = engine._ledger
        real_save = checkpoint.save_json
        failure = KeyboardInterrupt("controlled cycle publication failure")

        def fail_report(*args):
            if boundary == "report_after":
                real_report(*args)
            raise failure

        def fail_ledger(event, payload, **kwargs):
            if event == "cycle_completed":
                if boundary == "event_after":
                    real_ledger(event, payload, **kwargs)
                raise failure
            return real_ledger(event, payload, **kwargs)

        def fail_save(path, payload):
            real_save(path, payload)
            if payload.get("status") == "completed":
                raise failure

        if boundary in {"report", "report_after"}:
            monkeypatch.setattr(engine, "_save_cycle_report", fail_report)
        elif boundary in {"event", "event_after"}:
            monkeypatch.setattr(engine, "_ledger", fail_ledger)
        else:
            monkeypatch.setattr(checkpoint, "save_json", fail_save)
        with pytest.raises(KeyboardInterrupt) as caught:
            engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        assert caught.value is failure
        path, state = _state(tmp_path)
        assert state["status"] == "completed"
        assert state["completion"]["report"]["state"] == "completed"
        before = path.read_bytes()
        monkeypatch.setattr(engine, "_save_cycle_report", real_report)
        monkeypatch.setattr(engine, "_ledger", real_ledger)
        monkeypatch.setattr(checkpoint, "save_json", real_save)

        def forbidden(*args, **kwargs):
            pytest.fail("committed recovery reexecuted the cycle")

        monkeypatch.setattr(engine, "_run_cycle", forbidden)
        monkeypatch.setattr(engine.backend, "run_case", forbidden)
        report = engine.run_cycle(1, resume=True)
        assert report == state["completion"]["report"]
        assert path.read_bytes() == before
        persisted = json.loads((tmp_path / "work/cycles/cycle001.json").read_text(encoding="utf-8"))
        assert persisted == report
        assert sum(row["event"] == "cycle_completed" for row in engine.ledger.read()) == 1
        assert engine.run_cycle(1, resume=True) == report
        assert path.read_bytes() == before
        assert engine._journal is None
        assert engine._resuming is False


def test_completion_commit_failure_does_not_publish_terminal_projections(tmp_path, monkeypatch):
    with _engine(tmp_path) as engine:
        real_save = checkpoint.save_json
        failure = OSError("controlled completion commit failure")

        def fail(path, payload):
            if payload.get("status") == "completed":
                raise failure
            return real_save(path, payload)

        monkeypatch.setattr(checkpoint, "save_json", fail)
        with pytest.raises(OSError) as caught:
            engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        assert caught.value is failure
        assert not (tmp_path / "work/cycles/cycle001.json").exists()
        assert not any(row["event"] == "cycle_completed" for row in engine.ledger.read())
        _, state = _state(tmp_path)
        assert state["status"] == "interrupted"
        assert "completion" not in state


@pytest.mark.parametrize("terminal", ["preflight", "frozen"])
def test_actual_early_terminal_paths_share_completion_commit(tmp_path, monkeypatch, terminal):
    with _engine(tmp_path) as engine:
        if terminal == "preflight":
            monkeypatch.setattr("evolution.engine.sentinel_coverage",
                                lambda *args: {"evaluable": False, "reason": "controlled coverage failure"})
            expected = "blocked_evaluation_preflight"
        else:
            monkeypatch.setattr(engine, "_audit", lambda *args: {"frozen": True, "passed": False})
            expected = "frozen_after_defect_injection_audit"
        report = engine.run_cycle(1)
        path, state = _state(tmp_path)
        assert report["state"] == expected
        assert state["status"] == "completed"
        assert state["completion"]["report"] == report
        before = path.read_bytes()
        assert engine.run_cycle(1, resume=True) == report
        assert path.read_bytes() == before
        assert len(engine.cycle_reports) == 1


@pytest.mark.parametrize("tamper", ["missing", "null", "status", "report_hash", "contract", "extra",
                                   "report_cycle", "event_cycle", "event_state", "registry"])
def test_completion_integrity_refuses_tampering_without_mutation(tmp_path, monkeypatch, tamper):
    from detector.identity import digest

    with _engine(tmp_path) as engine:
        engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        path, state = _state(tmp_path)
        completion = state["completion"]
        if tamper == "missing":
            del state["completion"]
        elif tamper == "null":
            state["completion"] = None
        elif tamper == "status":
            state["status"] = "interrupted"
        elif tamper == "report_hash":
            completion["report"]["state"] = "changed"
        elif tamper == "contract":
            completion["contract_digest"] = "0" * 64
        elif tamper == "extra":
            completion["extra"] = True
        elif tamper == "report_cycle":
            completion["report"]["cycle"] = True
            completion["report_hash"] = digest(completion["report"])
        elif tamper == "event_cycle":
            completion["event"]["cycle"] = True
        elif tamper == "event_state":
            completion["event"]["state"] = "changed"
        else:
            completion["registry_hash"] = "invalid"
        if isinstance(state.get("completion"), dict):
            completion["digest"] = digest({key: value for key, value in completion.items() if key != "digest"})
        path.write_text(json.dumps(state), encoding="utf-8")
        before = path.read_bytes()
        ledger = engine.ledger.path.read_bytes()
        with pytest.raises(RuntimeError, match="completion"):
            engine.run_cycle(1, resume=True)
        assert path.read_bytes() == before
        assert engine.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize("conflict", ["report", "typed_report", "event", "duplicate_event"])
def test_completion_projection_conflicts_preserve_existing_bytes(tmp_path, conflict):
    with _engine(tmp_path) as engine:
        engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        path, state = _state(tmp_path)
        report_path = tmp_path / "work/cycles/cycle001.json"
        if conflict in {"report", "typed_report"}:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["cycle"] = True if conflict == "typed_report" else 2
            report_path.write_text(json.dumps(report), encoding="utf-8")
        else:
            engine.ledger.append("cycle_completed", state["completion"]["event"] if conflict == "duplicate_event"
                                 else {"cycle": 1, "state": "changed"})
        before = (path.read_bytes(), report_path.read_bytes(), engine.ledger.path.read_bytes())
        with pytest.raises(RuntimeError, match="conflict"):
            engine.run_cycle(1, resume=True)
        assert (path.read_bytes(), report_path.read_bytes(), engine.ledger.path.read_bytes()) == before


def test_completed_receipt_and_returned_report_are_owned_and_immutable(tmp_path):
    with _engine(tmp_path) as engine:
        report = engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        path, state = _state(tmp_path)
        before = path.read_bytes()
        report["state"] = "changed"
        assert engine.cycle_reports[0] == state["completion"]["report"]
        assert engine.run_cycle(1, resume=True) == state["completion"]["report"]
        journal = checkpoint.CycleJournal(path.parent, state["contract"], resume=True)
        journal.save()
        with pytest.raises(RuntimeError, match="immutable"):
            journal.save(status="interrupted")
        with pytest.raises(RuntimeError, match="new arm"):
            journal._bind_authority(None)
        assert path.read_bytes() == before


def test_full_self_evolution_recovers_completed_cycles_through_same_entry(tmp_path, monkeypatch):
    import evolution.engine as engine_module
    from evolution.splits import FinalHoldout

    cases = _make_dataset(tmp_path / "data", n=24)
    final = [{"case_id": case, "gold": bool(index % 2 == 0)} for index, case in enumerate(cases[20:])]
    split = EvolutionSplit(name="synthetic", pool=cases[:20], sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:20], final=final,
                           final_holdout=FinalHoldout(final), mini=False)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    config["cycles"] = 2
    config["experiment"] = {"mode": "full_self_evolution"}
    monkeypatch.setattr(engine_module, "build_split", lambda **kwargs: split)
    monkeypatch.setattr(EvolutionEngine, "run_final", lambda self: {"verdict": {"overall": "pass"}})
    monkeypatch.setattr(engine_module, "write_meta_report", lambda *args, **kwargs: {"defect_injection_audit": {"passed": True}})
    first = engine_module.run_full_self_evolution(config=config, backend=MockDetectorBackend())
    paths = sorted((tmp_path / "work/checkpoints").glob("cycle*/checkpoint.json"))
    before = [path.read_bytes() for path in paths]
    assert len(paths) == 2

    def forbidden(*args, **kwargs):
        pytest.fail("full recovery reexecuted a committed cycle")

    monkeypatch.setattr(EvolutionEngine, "_run_cycle", forbidden)
    second = engine_module.run_full_self_evolution(config=config, backend=MockDetectorBackend())
    assert second["cycles"] == first["cycles"]
    assert [path.read_bytes() for path in paths] == before


def test_actual_admitted_cycle_recovers_committed_publication_without_redeployment(tmp_path, monkeypatch):
    from evolution.tests.test_checkpoint import _resume_config, _resume_engine
    from evolution.tests.test_engine_smoke import _scanner_candidate, _scripted_proposer

    _, config, split, base, overrides = _resume_config(tmp_path, None)
    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    with _resume_engine(tmp_path, config, split, backend, _scripted_proposer(_scanner_candidate())) as engine:
        failure = OSError("controlled admitted report projection failure")

        def fail(*args):
            raise failure

        monkeypatch.setattr(engine, "_save_cycle_report", fail)
        with pytest.raises(OSError) as caught:
            engine.run_cycle(1)
        assert caught.value is failure
        path, state = _state(tmp_path)
        before = path.read_bytes()
        assert state["completion"]["report"]["active_after_cycle"] == ["IV-1-001"]
        active = engine.registry.active_path.read_bytes()
    with _resume_engine(tmp_path, config, split, MockDetectorBackend(base_predictions=base, overrides=overrides),
                        _scripted_proposer(_scanner_candidate())) as resumed:
        def forbidden(*args, **kwargs):
            pytest.fail("committed admitted cycle reexecuted")

        monkeypatch.setattr(resumed, "_run_cycle", forbidden)
        monkeypatch.setattr(resumed.registry, "write", forbidden)
        monkeypatch.setattr(resumed.registry, "restore", forbidden)
        report = resumed.run_cycle(1, resume=True)
        assert report == state["completion"]["report"]
        assert path.read_bytes() == before
        assert resumed.registry.active_path.read_bytes() == active
        assert sum(row["event"] == "admitted" for row in resumed.ledger.read()) == 1
        assert sum(row["event"] == "cycle_completed" for row in resumed.ledger.read()) == 1
