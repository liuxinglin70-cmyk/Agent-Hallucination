"""The detector's environment overrides are part of a measurement's identity.

``detector.config.resolve_route`` merges a set of ``DETECTOR_*`` variables into
the provider route, so the model, endpoint, temperature and timeouts a run
actually used can come from the process environment rather than from the config
file. Every artefact that claims to be a measurement of that machinery -- the
result cache, the cycle checkpoint contract, the defect-audit verdict -- has to
pin the same set, or a run under model B is served the rows model A produced
under a cache directory with no visible difference.

These tests execute the resolver against a recording environment rather than
restating its key list, so a key added to the resolver cannot be left out of
the fingerprint: the first test fails until the tuple grows the same key.
"""

from __future__ import annotations

import json
import os

import pytest

from detector.config import (
    DETECTOR_ENV_KEYS,
    detector_env_fingerprint,
    resolve_route,
)
from evolution.backends import CachingBackend, DetectionBackend, make_backend
from evolution.config import load_config


def _recorded_env_names(monkeypatch) -> set[str]:
    """Every ``os.environ`` name ``resolve_route`` reads, observed."""
    seen: set[str] = set()
    real_get = os.environ.get

    class _Recorder(dict):
        def get(self, key, default=None):
            name = str(key)
            if name.startswith("DETECTOR_"):
                seen.add(name)
            return real_get(name, default)

    recorded = _Recorder({k: v for k, v in os.environ.items()})
    monkeypatch.setattr(os, "environ", recorded)
    for name in ("judge", "defense", "recall"):
        resolve_route(name, None, None)
    return seen


def test_detector_env_keys_cover_the_resolver(monkeypatch):
    """The shared key list is exactly what the resolver consults.

    A key the resolver reads but the fingerprint omits means two different
    measurements share one identity; a key in the list the resolver never reads
    is dead weight that invalidates caches for nothing. Both fail here.
    """
    read_by_resolver = _recorded_env_names(monkeypatch)
    declared = set(DETECTOR_ENV_KEYS)
    missing = sorted(read_by_resolver - declared)
    extra = sorted(declared - read_by_resolver)
    assert missing == [], f"resolver reads keys absent from DETECTOR_ENV_KEYS: {missing}"
    assert extra == [], f"DETECTOR_ENV_KEYS names keys the resolver never reads: {extra}"


def test_env_fingerprint_distinguishes_model_and_temperature(monkeypatch):
    """The overrides that silently change a verdict are all in the fingerprint."""
    for key in DETECTOR_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    baseline = detector_env_fingerprint()
    assert set(baseline) == set(DETECTOR_ENV_KEYS)
    assert all(value == "" for value in baseline.values())

    for key in (
        "DETECTOR_MODEL", "DETECTOR_ENDPOINT", "DETECTOR_TEMPERATURE",
        "DETECTOR_MAX_TOKENS", "DETECTOR_JUDGE_MODEL",
        "DETECTOR_RECALL_MODEL", "DETECTOR_TIMEOUT_SECONDS",
        "DETECTOR_TRANSPORT_RETRIES", "DETECTOR_API_KEY_ENV",
    ):
        monkeypatch.setenv(key, "probe-value")
        changed = detector_env_fingerprint()
        assert changed != baseline, key
        assert changed[key] == "probe-value"
        monkeypatch.delenv(key)
    assert detector_env_fingerprint() == baseline


def _fake_config(tmp_path):
    config = load_config()
    config["work_root"] = str(tmp_path / "work")
    config["dataset_root"] = str(tmp_path / "data")
    config["backend"]["name"] = "detector"
    config["backend"]["cache"] = True
    return config


def test_result_cache_namespace_tracks_the_environment(monkeypatch, tmp_path):
    """Changing ``DETECTOR_MODEL`` must move the cache directory.

    Regression: the namespace was built from the config file digest, the source
    digest and the evidence namespace only, so the same work root served model
    A's cached rows for a model-B request.
    """
    monkeypatch.setenv("AUTODL_API_KEY", "test-only-not-used")
    for key in DETECTOR_ENV_KEYS:
        if key != "AUTODL_API_KEY":
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DETECTOR_MODEL", "model-A")
    first = make_backend(_fake_config(tmp_path))
    assert isinstance(first, CachingBackend)

    monkeypatch.setenv("DETECTOR_MODEL", "model-B")
    second = make_backend(_fake_config(tmp_path))
    assert isinstance(second, CachingBackend)

    assert first.cache_dir != second.cache_dir

    # Same environment again: the namespace is stable, not merely different.
    third = make_backend(_fake_config(tmp_path))
    assert third.cache_dir == second.cache_dir
    assert (first.cache_dir.parent == second.cache_dir.parent)


def test_cache_namespace_ignores_unrelated_environment(monkeypatch, tmp_path):
    """An unrelated variable must not invalidate a run's cache."""
    monkeypatch.setenv("AUTODL_API_KEY", "test-only-not-used")
    for key in DETECTOR_ENV_KEYS:
        if key != "AUTODL_API_KEY":
            monkeypatch.delenv(key, raising=False)
    baseline = make_backend(_fake_config(tmp_path)).cache_dir

    monkeypatch.setenv("EVOLUTION_PROGRESS", "1")
    monkeypatch.setenv("SOMETHING_ELSE", "x")
    assert make_backend(_fake_config(tmp_path)).cache_dir == baseline


def test_cycle_checkpoint_contract_carries_the_detector_environment(tmp_path):
    """A checkpoint may not be resumed under a different provider route.

    The contract is the identity a resume validates, so a cycle interrupted
    under model A must not restart its remaining arms under model B: the rows
    already recorded would be attributed to the wrong measurement conditions.
    """
    from evolution.checkpoint import CycleJournal

    contract = {"cycle": 1, "detector_env": detector_env_fingerprint()}
    first = CycleJournal(tmp_path / "cycle001", contract, resume=False)
    assert first.state["contract"]["detector_env"]["DETECTOR_MODEL"] == ""

    # The same environment resumes.
    resumed = CycleJournal(tmp_path / "cycle001", contract, resume=True)
    assert resumed.state["contract"] == first.state["contract"]

    # A changed route does not.
    changed = {
        "cycle": 1,
        "detector_env": {**detector_env_fingerprint(), "DETECTOR_MODEL": "model-B"},
    }
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        CycleJournal(tmp_path / "cycle001", changed, resume=True)


def test_cycle_writes_the_detector_environment_into_its_contract(tmp_path):
    """The engine's own contract, read back from the checkpoint on disk."""
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_engine_smoke import _config, _make_dataset

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )
    assert engine.run_cycle(1)["state"] == "completed"
    contract = json.loads(
        (tmp_path / "work" / "checkpoints" / "cycle001" / "checkpoint.json")
        .read_text(encoding="utf-8")
    )["contract"]
    assert contract["detector_env"] == detector_env_fingerprint()


def test_swapping_the_model_between_cycles_misses_the_cache(tmp_path, monkeypatch):
    """Two rounds of one run under different models share no cache directory.

    This is the shape the audit measured: the same work root, the model changed
    through the environment, and the second round served the first round's rows.
    Both namespaces come from the real ``make_backend`` path.
    """
    monkeypatch.setenv("AUTODL_API_KEY", "test-only-not-used")
    for key in DETECTOR_ENV_KEYS:
        if key != "AUTODL_API_KEY":
            monkeypatch.delenv(key, raising=False)
    config = _fake_config(tmp_path)

    monkeypatch.setenv("DETECTOR_MODEL", "model-A")
    round_one = make_backend(config)
    monkeypatch.setenv("DETECTOR_MODEL", "model-B")
    round_two = make_backend(config)

    assert round_one.cache_dir.parent == round_two.cache_dir.parent
    assert round_one.cache_dir != round_two.cache_dir
    # The work root is shared, which is what made the collision invisible.
    assert round_one.cache_dir.parent == tmp_path / "work" / "cache"


def test_cached_row_is_not_served_across_models(monkeypatch, tmp_path):
    """End-to-end through the cache: model B's request runs the inner backend.

    The unit above pins the namespace; this pins the consequence the audit
    measured -- a cached row produced under model A used to answer a model-B
    request without the inner backend ever being called. The two namespaces come
    from the real ``make_backend`` path, so this cannot pass by construction.
    """
    monkeypatch.setenv("AUTODL_API_KEY", "test-only-not-used")
    for key in DETECTOR_ENV_KEYS:
        if key != "AUTODL_API_KEY":
            monkeypatch.delenv(key, raising=False)

    config = _fake_config(tmp_path)
    monkeypatch.setenv("DETECTOR_MODEL", "model-A")
    namespace_a = make_backend(config).cache_dir.name
    monkeypatch.setenv("DETECTOR_MODEL", "model-B")
    namespace_b = make_backend(config).cache_dir.name
    assert namespace_a != namespace_b

    calls: list[str] = []

    class Stub(DetectionBackend):
        name = "detector"

        def run_case(self, record, *, case_id, registry=None, gold=None):
            calls.append(case_id)
            return {
                "schema_version": "detector.detection_result.v1",
                "case_id": case_id,
                "state": "verified_clean",
                "occurrence_state": "verified_clean",
                "decision_authority": "authoritative",
                "runtime_valid": True,
                # The cache verifies the registry a row was produced under, so
                # the stub has to report it the way a real detector does.
                "provenance": {
                    "interventions_hash": str(
                        (registry or {}).get("content_hash") or ""
                    ),
                },
                "model_that_answered": os.environ.get("DETECTOR_MODEL", ""),
            }

    registry = {"content_hash": "a" * 64, "interventions": []}
    record = {"question": "q", "history": [{"step": 1, "role": "assistant", "content": "x"}]}
    cache_root = tmp_path / "work" / "cache"

    monkeypatch.setenv("DETECTOR_MODEL", "model-A")
    first = CachingBackend(
        Stub(), cache_root, namespace=namespace_a,
    ).run_case(record, case_id="A/001.json", registry=registry)
    assert first["model_that_answered"] == "model-A"

    monkeypatch.setenv("DETECTOR_MODEL", "model-B")
    second = CachingBackend(
        Stub(), cache_root, namespace=namespace_b,
    ).run_case(record, case_id="A/001.json", registry=registry)
    assert second["model_that_answered"] == "model-B"
    assert calls == ["A/001.json", "A/001.json"]

    # A repeat under model B does come from its own cache, so the split did not
    # simply disable caching.
    third = CachingBackend(
        Stub(), cache_root, namespace=namespace_b,
    ).run_case(record, case_id="A/001.json", registry=registry)
    assert third["model_that_answered"] == "model-B"
    assert calls == ["A/001.json", "A/001.json"]
