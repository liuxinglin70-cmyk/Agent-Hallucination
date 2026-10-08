from __future__ import annotations

import json

import pytest

from detector.api import Detector
from detector.config import DETECTOR_ENV_KEYS, load_config


@pytest.fixture(autouse=True)
def isolated_config_environment(monkeypatch):
    for name in DETECTOR_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    def unexpected():
        raise AssertionError("credential-free parsing must not load .env")
    monkeypatch.setattr("detector.config.load_local_env", unexpected)


def _raw(**fields):
    return {"provider": {"endpoint": "http://unused", "model": "m",
                         "api_key_env": "TEST_CONFIG_KEY"}, **fields}


def _load(tmp_path, raw):
    path = tmp_path / "detector.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_config(path, require_credentials=False)


@pytest.mark.parametrize("entry", ["file", "direct"])
@pytest.mark.parametrize("field", ["max_tokens", "timeout_seconds", "transport_retries",
                                  "votes", "vote_concurrency"])
@pytest.mark.parametrize("value", [True, -1, 1.2, float("nan"), float("inf")])
def test_invalid_route_numbers_rejected_at_both_entries(tmp_path, entry, field, value):
    raw = _raw(judge_route={field: value})
    with pytest.raises(ValueError, match=field):
        if entry == "file":
            _load(tmp_path, raw)
        else:
            Detector(raw, require_credentials=False)


def test_file_and_direct_config_have_the_same_effective_values(tmp_path):
    raw = _raw(judge_route={"votes": 3}, case_timeout_seconds=50,
               causal_audit={"mode": "shadow", "top_k": 2})
    loaded = _load(tmp_path, raw)
    direct = Detector(raw, require_credentials=False).config
    assert direct == loaded
    assert loaded["causal_audit"]["mode"] == "shadow"
    assert loaded["causal_audit"]["top_k"] == 2
    assert loaded["causal_audit"]["negative_feedback_enabled"] is False


@pytest.mark.parametrize("entry", ["file", "direct"])
@pytest.mark.parametrize("raw,field", [
    (_raw(provider=[]), "provider"), (_raw(judge_route=[]), "judge_route"),
    (_raw(case_timeout_seconds=True), "case_timeout_seconds"),
    (_raw(causal_audit={"top_k": True}), "top_k"),
    (_raw(causal_audit={"mode": "unknown"}), "mode"),
    (_raw(causal_audit={"negative_feedback_enabled": "true"}), "negative_feedback_enabled"),
    (_raw(causal_audit={"typo": 1}), "typo"),
    (_raw(typo=1), "typo"),
])
def test_malformed_or_unknown_config_is_not_silently_dropped(tmp_path, entry, raw, field):
    with pytest.raises(ValueError, match=field):
        if entry == "file":
            _load(tmp_path, raw)
        else:
            Detector(raw, require_credentials=False)


def test_direct_config_is_owned_and_environment_is_resolved(monkeypatch):
    monkeypatch.setenv("DETECTOR_JUDGE_MODEL", "override")
    raw = _raw()
    config = Detector(raw, require_credentials=False).config
    assert config["judge_route"]["model"] == "override"
    config["judge_route"]["context_cache"]["enabled"] = False
    assert Detector(raw, require_credentials=False).config["judge_route"]["context_cache"]["enabled"]


def test_declared_missing_scorer_artifact_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="scorer_artifact"):
        _load(tmp_path, _raw(causal_audit={"scorer_artifact": "missing.bin"}))
