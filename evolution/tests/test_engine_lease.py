from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import gc
import weakref

import pytest

from detector.runtime import exclusive_writer
from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset


def engine_fixture(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
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
