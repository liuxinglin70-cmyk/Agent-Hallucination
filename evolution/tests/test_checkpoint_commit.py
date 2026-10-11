import json

import pytest

import evolution.checkpoint as checkpoint
from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("after_write", [False, True])
def test_checkpoint_failure_retains_actual_committed_state(tmp_path, monkeypatch, failure_type, after_write):
    journal = checkpoint.CycleJournal(tmp_path, {"synthetic": True}, resume=False)
    original = journal.state
    before = journal.path.read_bytes()
    failure = failure_type("controlled checkpoint failure")
    real_save = checkpoint.save_json

    def fail(path, payload):
        if after_write:
            real_save(path, payload)
        raise failure

    monkeypatch.setattr(checkpoint, "save_json", fail)
    with pytest.raises(failure_type) as caught:
        journal.save(status="completed", proposal={"synthetic": True})
    assert caught.value is failure
    assert journal.state == json.loads(journal.path.read_text(encoding="utf-8"))
    if after_write:
        assert journal.state["status"] == "completed"
    else:
        assert journal.state == original
        assert journal.path.read_bytes() == before
    monkeypatch.setattr(checkpoint, "save_json", real_save)
    journal.save(error_type="controlled")
    assert journal.state == json.loads(journal.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", ["contract", "registry", "updates", "snapshot"])
def test_checkpoint_owns_committed_json(tmp_path, entry):
    contract = {"synthetic": {"note": "original"}}
    registry = {"synthetic": {"note": "original"}}
    journal = checkpoint.CycleJournal(tmp_path, contract, registry_before=registry, resume=False)
    updates = {"synthetic": {"note": "original"}}
    journal.save(proposal=updates)
    before = journal.path.read_bytes()
    if entry == "contract":
        contract["synthetic"]["note"] = "changed"
    elif entry == "registry":
        registry["synthetic"]["note"] = "changed"
    elif entry == "updates":
        updates["synthetic"]["note"] = "changed"
    else:
        journal.state["proposal"]["synthetic"]["note"] = "changed"
    assert journal.state == json.loads(before)
    journal.save()
    assert journal.state["proposal"] == {"synthetic": {"note": "original"}}


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("broken", ["checkpoint", "ledger", "both"])
def test_actual_engine_keeps_original_cycle_failure(tmp_path, monkeypatch, failure_type, broken):
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    primary = failure_type("controlled cycle failure")
    checkpoint_failure = OSError("controlled checkpoint recording failure")
    ledger_failure = OSError("controlled ledger recording failure")
    real_save = checkpoint.save_json

    def fail_save(path, payload):
        if broken in {"checkpoint", "both"} and payload.get("status") == "interrupted":
            raise checkpoint_failure
        real_save(path, payload)

    with EvolutionEngine(_config(tmp_path), split=split, mini=True, backend=MockDetectorBackend()) as engine:
        real_ledger = engine._ledger

        def fail_ledger(event, payload):
            if broken in {"ledger", "both"} and event == "cycle_interrupted":
                raise ledger_failure
            return real_ledger(event, payload)

        def fail_cycle(*args, **kwargs):
            raise primary

        monkeypatch.setattr(checkpoint, "save_json", fail_save)
        monkeypatch.setattr(engine, "_ledger", fail_ledger)
        monkeypatch.setattr(engine, "_run_cycle", fail_cycle)
        with pytest.raises(failure_type) as caught:
            engine.run_cycle(1)
        assert caught.value is primary
        expected = []
        if broken in {"checkpoint", "both"}:
            expected.append(("checkpoint", checkpoint_failure))
        if broken in {"ledger", "both"}:
            expected.append(("ledger", ledger_failure))
        assert primary.cycle_recording_errors == tuple(expected)
        assert engine._journal is None
        assert engine._resuming is False
        state = json.loads((tmp_path / "work/checkpoints/cycle001/checkpoint.json").read_text(encoding="utf-8"))
        assert state["status"] == ("started" if broken in {"checkpoint", "both"} else "interrupted")
        events = [r for r in engine.ledger.read() if r["event"] == "cycle_interrupted"]
        assert len(events) == (0 if broken in {"ledger", "both"} else 1)


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_authority_binding_uses_same_checkpoint_commit(tmp_path, monkeypatch, after_write, failure_type):
    from detector.identity import digest

    class View:
        def identity(self, names):
            payload = {"synthetic": True, "status": {name: {"admitted": False} for name in sorted(names)}}
            return {**payload, "digest": digest(payload)}

    view = View()
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: view)
    journal = checkpoint.CycleJournal(tmp_path, {"synthetic": True}, resume=False)
    original = journal.state
    real_save = checkpoint.save_json
    failure = failure_type("controlled authority publication failure")

    def fail(path, payload):
        if after_write:
            real_save(path, payload)
        raise failure

    monkeypatch.setattr(checkpoint, "save_json", fail)
    with pytest.raises(failure_type) as caught:
        journal._bind_authority(view.identity(["first"]))
    assert caught.value is failure
    assert journal.state == json.loads(journal.path.read_text(encoding="utf-8"))
    assert journal.state["authority"] == (view.identity(["first"]) if after_write else None)
    if not after_write:
        assert journal.state == original
    monkeypatch.setattr(checkpoint, "save_json", real_save)
    journal._bind_authority(view.identity(["first"]))
    assert journal.state["authority"] == view.identity(["first"])


@pytest.mark.parametrize("corruption", ["missing", "malformed", "foreign"])
def test_unknown_checkpoint_commit_blocks_reuse_without_masking_failure(tmp_path, monkeypatch, corruption):
    journal = checkpoint.CycleJournal(tmp_path, {"synthetic": True}, resume=False)
    failure = KeyboardInterrupt("controlled ambiguous commit")

    def fail(path, payload):
        if corruption == "missing":
            path.unlink()
        elif corruption == "malformed":
            path.write_text("{", encoding="utf-8")
        else:
            path.write_text(json.dumps({**payload, "foreign": True}), encoding="utf-8")
        raise failure

    monkeypatch.setattr(checkpoint, "save_json", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        journal.save(status="completed")
    assert caught.value is failure
    before = journal.path.read_bytes() if journal.path.exists() else None
    with pytest.raises(RuntimeError, match="commit is unknown"):
        _ = journal.state
    with pytest.raises(RuntimeError, match="commit is unknown"):
        journal.save(status="interrupted")
    with pytest.raises(RuntimeError, match="commit is unknown"):
        journal._bind_authority(None)
    assert (journal.path.read_bytes() if journal.path.exists() else None) == before


def test_checkpoint_rejects_nonfinite_input_before_publication(tmp_path):
    journal = checkpoint.CycleJournal(tmp_path, {}, resume=False)
    before = journal.path.read_bytes()
    with pytest.raises(ValueError):
        journal.save(proposal={"synthetic": float("nan")})
    assert journal.path.read_bytes() == before
    assert journal.state == json.loads(before)


def test_checkpoint_serializes_owned_updates(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    journal = checkpoint.CycleJournal(tmp_path, {}, resume=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: journal.save(**{f"synthetic_{index}": {"index": index}}), range(12)))
    assert journal.state == json.loads(journal.path.read_text(encoding="utf-8"))
    for index in range(12):
        assert journal.state[f"synthetic_{index}"] == {"index": index}


def test_actual_resume_preparation_failure_releases_journal(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    with EvolutionEngine(_config(tmp_path), split=split, mini=True, backend=MockDetectorBackend()) as engine:
        first = KeyboardInterrupt("controlled initial cycle interruption")

        def fail_cycle(*args, **kwargs):
            raise first

        monkeypatch.setattr(engine, "_run_cycle", fail_cycle)
        with pytest.raises(KeyboardInterrupt):
            engine.run_cycle(1)
        failure = RuntimeError("controlled resume preparation failure")

        def fail_restore(*args):
            raise failure

        monkeypatch.setattr(engine, "_restore_resume_registry", fail_restore)
        with pytest.raises(RuntimeError) as caught:
            engine.run_cycle(1, resume=True)
        assert caught.value is failure
        assert engine._journal is None
        assert engine._resuming is False
        events = [r for r in engine.ledger.read() if r["event"] == "cycle_interrupted"]
        assert [r["payload"]["error_type"] for r in events] == ["KeyboardInterrupt", "RuntimeError"]
