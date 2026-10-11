"""Actual memory writers preserve persisted content under replay and contention."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

import evolution.memory as memory
from evolution.contracts import read_jsonl


def _write(root, kind, *, changed=False, cycle=1):
    if kind == "episodic":
        return memory.record_episode(root, cycle=cycle, failure_family="FAM-controlled", polarity="recall",
                                     outcome="rejected", intervention_id="IV-controlled",
                                     reason="changed" if changed else "original", unevaluable=not changed)
    if kind == "procedural":
        return memory.record_procedure(root, cycle=cycle, failure_family="FAM-controlled", polarity="recall",
                                       treatment_route="text_boundary@judge.system", intervention_type="text_boundary",
                                       hook="judge.system", intervention_id="IV-controlled",
                                       expected_behavior="changed" if changed else "original")
    return memory.record_improvement(root, cycle=cycle, bottleneck="controlled", patch_kind="text_boundary",
                                     outcome="rejected", note="changed" if changed else "original")


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_same_key_changed_content_is_refused_without_rewriting(tmp_path, kind):
    original = _write(tmp_path, kind)
    path = memory.memory_paths(tmp_path)[kind]
    before = path.read_bytes()
    with pytest.raises(ValueError, match="conflict"):
        _write(tmp_path, kind, changed=True)
    assert path.read_bytes() == before
    assert read_jsonl(path) == [original]


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_check_and_append_are_one_thread_transaction(tmp_path, monkeypatch, kind):
    original = memory.append_jsonl
    first, second, release = Event(), Event(), Event()
    counter_lock, physical_lock = Lock(), Lock()
    arrivals = 0

    def staged_append(*args, **kwargs):
        nonlocal arrivals
        with counter_lock:
            arrivals += 1
            (first if arrivals == 1 else second).set()
        assert release.wait(timeout=5)
        with physical_lock:
            return original(*args, **kwargs)

    monkeypatch.setattr(memory, "append_jsonl", staged_append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(_write, tmp_path, kind)
        assert first.wait(timeout=5)
        concurrent = pool.submit(_write, tmp_path, kind)
        second.wait(timeout=0.5)
        release.set()
        assert initial.result(timeout=5) == concurrent.result(timeout=5)
    assert len(read_jsonl(memory.memory_paths(tmp_path)[kind])) == 1


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_replay_returns_exact_persisted_content_and_keeps_bytes(tmp_path, kind):
    expected = _write(tmp_path, kind)
    path = memory.memory_paths(tmp_path)[kind]
    before = path.read_bytes()
    assert _write(tmp_path, kind) == expected == read_jsonl(path)[0]
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("after_append", [False, True])
def test_append_failure_propagates_and_replay_observes_actual_terminal(tmp_path, monkeypatch, kind, exception, after_append):
    _write(tmp_path, kind)
    path = memory.memory_paths(tmp_path)[kind]
    before = path.read_bytes()
    original = memory.append_jsonl
    failure = exception("controlled append interruption")

    def fail(*args, **kwargs):
        if after_append:
            original(*args, **kwargs)
        raise failure

    monkeypatch.setattr(memory, "append_jsonl", fail)
    with pytest.raises(exception) as caught:
        _write(tmp_path, kind, cycle=2)
    assert caught.value is failure
    if after_append:
        assert path.read_bytes().startswith(before)
        monkeypatch.setattr(memory, "append_jsonl", original)
        current = path.read_bytes()
        assert _write(tmp_path, kind, cycle=2) == read_jsonl(path)[1]
        assert path.read_bytes() == current
    else:
        assert path.read_bytes() == before


def test_corrections_share_transaction_but_reclassification_remains_separate(tmp_path):
    original = _write(tmp_path, "episodic", changed=True)
    path = memory.memory_paths(tmp_path)["episodic"]
    before = path.read_bytes()
    options = dict(unevaluable=True, reason="reviewed missing coverage", evidence="controlled saved report")
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: memory.record_episode_correction(tmp_path, original, **options), range(8)))
    assert all(row == records[0] for row in records)
    assert len(read_jsonl(memory.memory_paths(tmp_path)["corrections"])) == 1
    assert path.read_bytes() == before
    assert memory.episodic_attempts(tmp_path)[0]["falsified"] is False
    memory.record_episode_correction(tmp_path, original, unevaluable=False,
                                     reason="reviewed usable coverage", evidence="new controlled evidence")
    assert len(read_jsonl(memory.memory_paths(tmp_path)["corrections"])) == 2
    assert memory.episodic_attempts(tmp_path)[0]["falsified"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_unknown_or_duplicate_history_is_not_repaired_or_extended(tmp_path, kind):
    row = _write(tmp_path, kind)
    path = memory.memory_paths(tmp_path)[kind]
    memory.append_jsonl(path, row)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="duplicate"):
        _write(tmp_path, kind)
    assert path.read_bytes() == before
    path.write_text('{"schema_version":"unknown"}\n', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        _write(tmp_path, kind)
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_malformed_persisted_key_cannot_become_a_second_logical_identity(tmp_path, kind):
    import json

    row = _write(tmp_path, kind)
    row["cycle"] = "1"
    path = memory.memory_paths(tmp_path)[kind]
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        _write(tmp_path, kind)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["episodic.jsonl", "procedural.jsonl", "improvement.jsonl", "episode_corrections.jsonl", "../escaped.json"])
def test_recall_output_cannot_overwrite_history_or_escape_root(tmp_path, name):
    _write(tmp_path, "episodic")
    before = memory.memory_paths(tmp_path)["episodic"].read_bytes()
    with pytest.raises(ValueError):
        memory.write_recall(tmp_path, {"replacement": True}, name=name)
    assert memory.memory_paths(tmp_path)["episodic"].read_bytes() == before


@pytest.mark.parametrize("kind", ["episodic", "procedural", "improvement"])
def test_delegated_contexts_still_serialize_check_and_append(tmp_path, kind):
    from detector.runtime import bounded_map, exclusive_writer

    with exclusive_writer(tmp_path):
        rows = list(bounded_map(lambda _: _write(tmp_path, kind), range(8), workers=4))
    assert all(row == rows[0] for row in rows)
    assert read_jsonl(memory.memory_paths(tmp_path)[kind]) == [rows[0]]


def _evidence_record(root, kind, evidence):
    if kind == "procedural":
        return memory.record_procedure(root, cycle=1, failure_family="F", polarity="recall",
                                       treatment_route="text_boundary", intervention_type="text_boundary",
                                       hook="judge.system", intervention_id="IV-controlled", evidence=evidence)
    return memory.record_improvement(root, cycle=1, bottleneck="controlled", patch_kind="text_boundary",
                                     outcome="worked", evidence=evidence)


@pytest.mark.parametrize("kind", ["procedural", "improvement"])
def test_input_return_and_persisted_evidence_own_their_content(tmp_path, kind):
    evidence = {"items": [{"valid": True}], "sequence": (1, 2)}
    record = _evidence_record(tmp_path, kind, evidence)
    path = memory.memory_paths(tmp_path)[kind]
    assert record == read_jsonl(path)[0]
    before = path.read_bytes()
    evidence["items"][0]["valid"] = False
    assert record["evidence"]["items"][0]["valid"] is True
    record["evidence"]["items"][0]["valid"] = False
    assert read_jsonl(path)[0]["evidence"]["items"][0]["valid"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["procedural", "improvement"])
def test_json_type_changes_are_conflicts_and_nonfinite_values_cannot_append(tmp_path, kind):
    _evidence_record(tmp_path, kind, {"reading": True})
    path = memory.memory_paths(tmp_path)[kind]
    before = path.read_bytes()
    with pytest.raises(ValueError, match="conflict"):
        _evidence_record(tmp_path, kind, {"reading": 1})
    with pytest.raises(ValueError):
        _evidence_record(tmp_path, kind, {"reading": float("nan")})
    assert path.read_bytes() == before


def test_recall_snapshot_returns_owned_persisted_content(tmp_path):
    import json

    source = {"entries": [{"note": "original"}]}
    returned = memory.write_recall(tmp_path, source)
    before = memory.memory_paths(tmp_path)["recall"].read_bytes()
    source["entries"][0]["note"] = "changed input"
    assert returned["entries"][0]["note"] == "original"
    returned["entries"][0]["note"] = "changed return"
    assert json.loads(before)["entries"][0]["note"] == "original"
    assert memory.memory_paths(tmp_path)["recall"].read_bytes() == before


@pytest.mark.platform
def test_other_process_cannot_append_under_existing_store_lease(tmp_path):
    import subprocess
    import sys
    from detector.runtime import exclusive_writer

    _write(tmp_path, "episodic")
    path = memory.memory_paths(tmp_path)["episodic"]
    before = path.read_bytes()
    script = """import sys
from evolution.memory import record_episode
try:
    record_episode(sys.argv[1],cycle=2,failure_family='F',polarity='recall',outcome='rejected',intervention_id='other')
except RuntimeError as failure:
    assert 'already has a writer' in str(failure)
else:
    raise AssertionError('competing process acquired the memory store')
"""
    with exclusive_writer(tmp_path):
        child = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                               capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize("admitted", [False, True])
def test_actual_engine_memory_replay_refuses_changed_evaluation(tmp_path, admitted):
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_engine_smoke import _config, _make_dataset, _scanner_candidate

    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    candidate = _scanner_candidate()
    report = {"candidate": candidate, "behavior": {"behavior_note": "original"}}
    rejection = {"intervention_id": candidate["intervention_id"], "gate": "BEHAVIOR",
                 "reason": "original", "not_evaluable": True}
    with EvolutionEngine(_config(tmp_path), split=split, mini=True, backend=MockDetectorBackend()) as engine:
        arguments = dict(cycle=1, diagnosed_families=[], gate_reports=[report],
                         rejected=[] if admitted else [rejection], selected=[candidate] if admitted else [])
        engine._record_cycle_experience(**arguments)
        paths = memory.memory_paths(engine.memory_root)
        path = paths["procedural"] if admitted else paths["episodic"]
        before = path.read_bytes()
        engine._record_cycle_experience(**arguments)
        assert path.read_bytes() == before
        report["behavior"]["behavior_note"] = "changed"
        rejection.update(reason="changed", not_evaluable=False)
        with pytest.raises(ValueError, match="conflict"):
            engine._record_cycle_experience(**arguments)
        assert path.read_bytes() == before
        if not admitted:
            assert memory.episodic_attempts(engine.memory_root)[0]["falsified"] is False
