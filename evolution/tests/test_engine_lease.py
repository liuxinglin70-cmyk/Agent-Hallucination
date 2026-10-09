from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextvars import copy_context
import gc
from threading import Event
import weakref

import pytest

from detector.runtime import exclusive_writer
from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine
from evolution import cli
from evolution.smoke import build_capability_smoke_engine
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset, _scanner_candidate


def engine_fixture(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["proposer"]["mode"] = "offline"
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
                           patrol_pool=cases[12:], mini=True)
    return config, split


def construct(config, split):
    return EvolutionEngine(config, backend=MockDetectorBackend(), split=split, mini=True)


def test_independent_engines_in_one_context_cannot_adopt_one_lease(tmp_path):
    config, split = engine_fixture(tmp_path)
    with construct(config, split):
        with pytest.raises(RuntimeError, match="writer"):
            construct(config, split)
    with construct(config, split):
        pass


def test_engine_can_close_in_another_context_and_releases_os_lease(tmp_path):
    config, split = engine_fixture(tmp_path)
    engine = construct(config, split)
    with ThreadPoolExecutor(1) as pool:
        pool.submit(engine.close).result()
    engine.close()
    with exclusive_writer(tmp_path / "work"):
        pass


def test_engine_gc_releases_lease_without_cross_context_token_reset(tmp_path):
    config, split = engine_fixture(tmp_path)
    box = [construct(config, split)]
    reference = weakref.ref(box[0])
    copied = copy_context()
    copied.run(box.clear)
    copied.run(gc.collect)
    assert reference() is None
    with exclusive_writer(tmp_path / "work"):
        pass


def test_failed_engine_initialization_releases_lease_immediately(tmp_path, monkeypatch):
    config, split = engine_fixture(tmp_path)
    def rejected(*args, **kwargs):
        raise ValueError("registry initialization failed")
    monkeypatch.setattr("evolution.engine.load_active_registry", rejected)
    with pytest.raises(ValueError, match="registry initialization"):
        construct(config, split)
    with exclusive_writer(tmp_path / "work"):
        pass


def test_smoke_second_entry_refuses_before_deleting_owned_data(tmp_path):
    root = tmp_path / 'smoke'
    assert root.resolve().is_relative_to(tmp_path.resolve())
    with build_capability_smoke_engine(root) as first:
        marker = root / 'data' / 'owned_evidence.txt'
        marker.write_text('owned state must survive rejected contender', encoding='utf-8')
        before = marker.read_bytes()
        try:
            second = build_capability_smoke_engine(root)
        except RuntimeError:
            second = None
        if second is not None:
            second.close()
        assert marker.exists(), 'smoke reset deleted owned data before checking the writer lease'
        assert marker.read_bytes() == before
        assert second is None, 'second engine must not own the same root'



def test_closed_engine_cannot_write_while_successor_holds_root(tmp_path):
    config, split = engine_fixture(tmp_path)
    first = construct(config, split)
    first.close()
    with construct(config, split):
        error = None
        report_path = tmp_path / 'work' / 'reports' / 'proposal_preview_cycle001.json'
        before = report_path.read_bytes() if report_path.exists() else None
        try:
            first.preview_cycle(1)
        except RuntimeError as exc:
            error = exc
        after = report_path.read_bytes() if report_path.exists() else None
        assert error is not None, 'closed engine wrote a report while successor owned the root'
        assert before == after



def test_cli_init_reset_obeys_running_engine_writer_lease(tmp_path, monkeypatch):
    config, split = engine_fixture(tmp_path)
    monkeypatch.setattr(cli, 'load_config', lambda path: config)
    with construct(config, split) as engine:
        engine.registry.write([_scanner_candidate()], cycle=1, reason='synthetic review fixture')
        before = engine.registry.active_path.read_bytes()
        with pytest.raises(RuntimeError, match='writer'):
            cli.cmd_init(Namespace(config=None, reset=True))
        assert engine.registry.active_path.read_bytes() == before



def test_cli_smoke_refuses_before_deleting_owned_root(tmp_path, monkeypatch):
    from copy import deepcopy
    config, split = engine_fixture(tmp_path)
    smoke_root = Path(config['work_root']) / 'smoke'
    assert smoke_root.resolve().is_relative_to(tmp_path.resolve())
    owned_config = deepcopy(config)
    for key, value in {'work_root': smoke_root, 'registry_dir': smoke_root / 'registry',
                       'ledger_path': smoke_root / 'ledger.jsonl', 'harness_dir': smoke_root / 'harness',
                       'memory_root': smoke_root / 'memory'}.items():
        owned_config[key] = str(value)
    monkeypatch.setattr(cli, 'load_config', lambda path: config)
    monkeypatch.setattr(cli, 'build_split', lambda **kwargs: split)
    with construct(owned_config, split):
        marker = smoke_root / 'owned_evidence.txt'
        marker.write_text('owned state', encoding='utf-8')
        try:
            cli.cmd_smoke(Namespace(config=None))
        except RuntimeError:
            pass
        assert marker.exists(), 'CLI smoke deleted owned root contents before lease rejection'



def test_closed_engine_cannot_reenter(tmp_path):
    config, split = engine_fixture(tmp_path)
    engine = construct(config, split)
    engine.close()
    with pytest.raises(RuntimeError, match="closed"):
        with engine:
            pytest.fail("closed engine reentered")
    with construct(config, split):
        pass


def test_close_waits_for_current_operation_before_releasing_lease(tmp_path, monkeypatch):
    config, split = engine_fixture(tmp_path)
    engine = construct(config, split)
    engine.route_failure_store.parent.mkdir(parents=True, exist_ok=True)
    engine.route_failure_store.touch()
    entered, release, closing = Event(), Event(), Event()
    def reading(path):
        entered.set()
        assert release.wait(5)
        return []
    def close():
        closing.set()
        engine.close()
    monkeypatch.setattr("evolution.engine.load_route_failures", reading)
    with ThreadPoolExecutor(2) as pool:
        operation = pool.submit(engine.route_failure_records)
        try:
            assert entered.wait(5)
            shutdown = pool.submit(close)
            assert closing.wait(5)
            assert not shutdown.done()
            with pytest.raises(RuntimeError, match="writer"):
                with exclusive_writer(tmp_path / "work"):
                    pass
        finally:
            release.set()
        assert operation.result(timeout=5) == []
        shutdown.result(timeout=5)
    with exclusive_writer(tmp_path / "work"):
        pass
