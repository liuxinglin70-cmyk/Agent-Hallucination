from pathlib import Path
import json

import pytest

from evolution.registry import ActiveRegistry
from evolution.tests.test_contracts_gates import _candidate


def _prepare(tmp_path):
    registry = ActiveRegistry(tmp_path)
    registry.write([_candidate("IV-1-001")], cycle=1, reason="baseline")
    registry.record_marginal("IV-1-001", 1, cycle=1, fired_rate=1.0)
    registry.write([_candidate("IV-2-001")], cycle=2, reason="current")
    return registry


@pytest.mark.parametrize("operation", ["write", "restore", "clear", "marginal", "net", "retire", "rollback"])
def test_failed_commit_preserves_disk_memory_and_reopen(tmp_path, monkeypatch, operation):
    registry = _prepare(tmp_path)
    before_registry, before_state = registry.registry, registry.state
    before_active = registry.active_path.read_bytes()
    import evolution.registry as module
    original = module.atomic_json

    def fail_commit(path, payload):
        if Path(path).name == "active.json":
            raise OSError("controlled commit failure")
        original(path, payload)

    monkeypatch.setattr(module, "atomic_json", fail_commit)
    actions = {
        "write": lambda: registry.write([], cycle=3, reason="failed"),
        "restore": lambda: registry.restore({**before_registry, "interventions": [], "content_hash": ""}, reason="failed"),
        "clear": registry.clear,
        "marginal": lambda: registry.record_marginal("IV-2-001", -1, cycle=3, fired_rate=0.5),
        "net": lambda: registry.record_net_contribution(-1, cycle=3),
        "retire": lambda: registry.retire(["IV-2-001"], cycle=3),
        "rollback": lambda: registry.rollback_to_snapshot(1),
    }
    with pytest.raises(OSError, match="controlled commit failure"):
        actions[operation]()
    assert registry.active_path.read_bytes() == before_active
    assert registry.registry == before_registry
    assert registry.state == before_state
    reopened = ActiveRegistry(tmp_path)
    assert reopened.registry == before_registry
    assert reopened.state == before_state


@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt])
def test_interruption_after_replace_reconciles_to_published_commit(tmp_path, monkeypatch, exception):
    import evolution.registry as module

    registry = _prepare(tmp_path)
    original = module.atomic_json
    failure = exception("after actual replace")

    def replace_then_interrupt(path, payload):
        original(path, payload)
        if Path(path).name == "active.json":
            raise failure

    monkeypatch.setattr(module, "atomic_json", replace_then_interrupt)
    with pytest.raises(exception) as caught:
        registry.write([], cycle=3, reason="published before interrupt")
    assert caught.value is failure
    reopened = ActiveRegistry(tmp_path)
    assert registry.registry == reopened.registry
    assert registry.state == reopened.state
    assert registry.entries == []
    assert registry.state["cycle"] == 3
    for item in registry.state["snapshots"]:
        if item["file"]:
            assert (tmp_path / "snapshots" / item["file"]).is_file()


@pytest.mark.parametrize("stage", ["first_snapshot", "later_snapshot", "commit"])
def test_unpublished_artifacts_are_cleaned_on_failure(tmp_path, monkeypatch, stage):
    import evolution.registry as module

    registry = ActiveRegistry(tmp_path)
    before = registry.active_path.read_bytes()
    original = module.atomic_json
    count = 0

    def fail(path, payload):
        nonlocal count
        if Path(path).parent.name == "snapshots":
            count += 1
        if (stage == "first_snapshot" and count == 1) or (stage == "later_snapshot" and count == 2) or (
            stage == "commit" and Path(path).name == "active.json"
        ):
            raise OSError("failed stage")
        original(path, payload)

    monkeypatch.setattr(module, "atomic_json", fail)
    with pytest.raises(OSError):
        registry.write([_candidate("IV-1-001")], cycle=1, reason="failure")
    assert registry.active_path.read_bytes() == before
    assert list((tmp_path / "snapshots").glob("*.json")) == []
    assert registry.state["snapshots"] == []


@pytest.mark.parametrize("method", ["write", "marginal", "clear"])
def test_stale_peer_cannot_overwrite_another_committed_generation(tmp_path, method):
    from evolution.registry import RegistryConflictError

    first = ActiveRegistry(tmp_path)
    stale = ActiveRegistry(tmp_path)
    first.write([_candidate("IV-1-001")], cycle=1, reason="other writer")
    before = first.active_path.read_bytes()
    operations = {
        "write": lambda: stale.write([], cycle=2, reason="stale"),
        "marginal": lambda: stale.record_marginal("IV-1-001", -1, cycle=2, fired_rate=1),
        "clear": stale.clear,
    }
    with pytest.raises(RegistryConflictError):
        operations[method]()
    assert first.active_path.read_bytes() == before
    assert ActiveRegistry(tmp_path).registry == first.registry


def test_runtime_loader_extracts_only_verified_deployment(tmp_path):
    from detector.interventions import load_intervention_registry

    registry = _prepare(tmp_path)
    assert load_intervention_registry(registry.active_path) == registry.registry
    assert "state" not in load_intervention_registry(registry.active_path)
    raw = json.loads(registry.active_path.read_text())
    raw["state"]["cycle"] += 1
    registry.active_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="digest"):
        load_intervention_registry(registry.active_path)
    with pytest.raises(ValueError, match="digest"):
        ActiveRegistry(tmp_path)


def test_legacy_separate_state_is_not_migrated_or_modified(tmp_path):
    from evolution.contracts import empty_registry

    active = tmp_path / "active.json"
    state = tmp_path / "state.json"
    active.write_text(json.dumps(empty_registry()))
    state.write_text('{"cycle": 8}')
    before = (active.read_bytes(), state.read_bytes())
    with pytest.raises(ValueError, match="commit schema"):
        ActiveRegistry(tmp_path)
    assert (active.read_bytes(), state.read_bytes()) == before
