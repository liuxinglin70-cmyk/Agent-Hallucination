"""Actual promotion publication remains recoverable across persistence failures."""
import json
from pathlib import Path

import pytest

from evolution.ledger import Ledger
from evolution.promotion import PromotionRun
from evolution.registry import ActiveRegistry
from evolution.tests.test_promotion import _candidate, _standard_protocol, _ScriptedBackend


def _run(root, *, candidate=None, protocol=None, backend=None, registry=None, ledger=None):
    return PromotionRun(
        candidate=candidate or _candidate(), protocol=protocol or _standard_protocol(root),
        config={"dataset_root": str(root), "gates": {}, "library": {}},
        output_dir=root / "out", backend=backend or _ScriptedBackend(
            control_prediction={"A/001.json": False, "B/001.json": False}),
        active_registry=registry or ActiveRegistry(root / "registry"),
        ledger=ledger or Ledger(root / "ledger.jsonl"),
    )


@pytest.mark.parametrize("stage", ["version.json", "promotion.json", "candidate_promoted", "promotion_attempt"])
def test_published_promotion_survives_projection_failure_and_reopens(tmp_path, monkeypatch, stage):
    import evolution.promotion as module

    run = _run(tmp_path)
    save, append = module.save_json, run.ledger.append

    def fail_save(path, payload):
        if Path(path).name == stage:
            raise OSError("controlled projection failure")
        return save(path, payload)

    def fail_append(event, payload, **kwargs):
        if event == stage:
            raise OSError("controlled projection failure")
        return append(event, payload, **kwargs)

    monkeypatch.setattr(module, "save_json", fail_save)
    monkeypatch.setattr(run.ledger, "append", fail_append)
    with pytest.raises(OSError, match="controlled projection failure"):
        run.run()
    registry = ActiveRegistry(tmp_path / "registry")
    assert len(registry.entries) == 1
    publications = registry.state.get("publications")
    assert publications, "the deployment commit must contain its recoverable terminal"
    assert len(publications) == 1
    monkeypatch.setattr(module, "save_json", save)
    monkeypatch.setattr(run.ledger, "append", append)
    resumed = _run(tmp_path, candidate=run.candidate, protocol=run.protocol,
                   registry=registry, ledger=Ledger(tmp_path / "ledger.jsonl"))

    def no_measurement(*args, **kwargs):
        raise AssertionError("a committed promotion must not be measured again")

    monkeypatch.setattr(resumed.backend, "evaluate_fresh", no_measurement)
    report = resumed.run()
    assert report["state"] == "promoted" and report["admitted"] is True
    assert json.loads((tmp_path / "out" / "promotion.json").read_text(encoding="utf-8")) == report
    assert json.loads((tmp_path / "out" / "version.json").read_text(encoding="utf-8")) == report["version"]
    assert len(registry.state["publications"]) == 1
    events = [row["event"] for row in resumed.ledger.read()]
    assert events.count("candidate_promoted") == events.count("promotion_attempt") == 1
    assert resumed.ledger.verify()["valid"] is True


@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt])
def test_actual_commit_replacement_interrupt_recovers_without_redeployment(tmp_path, monkeypatch, exception):
    import evolution.registry as module

    run = _run(tmp_path)
    original = module.atomic_json
    failure = exception("interrupt after replace")

    def interrupt(path, payload):
        original(path, payload)
        if Path(path).name == "active.json":
            raise failure

    monkeypatch.setattr(module, "atomic_json", interrupt)
    with pytest.raises(exception) as caught:
        run.run()
    assert caught.value is failure
    monkeypatch.setattr(module, "atomic_json", original)
    reopened = ActiveRegistry(tmp_path / "registry")
    before = reopened.active_path.read_bytes()
    report = _run(tmp_path, candidate=run.candidate, protocol=run.protocol, registry=reopened).run()
    assert report["state"] == "promoted"
    assert reopened.active_path.read_bytes() == before


@pytest.mark.parametrize("stage", ["candidate_promoted", "promotion_attempt"])
@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt])
def test_actual_event_append_then_interrupt_is_idempotent(tmp_path, monkeypatch, stage, exception):
    run = _run(tmp_path)
    original = run.ledger.append
    failure = exception("interrupt after append")

    def interrupt(event, payload, **kwargs):
        result = original(event, payload, **kwargs)
        if event == stage:
            raise failure
        return result

    monkeypatch.setattr(run.ledger, "append", interrupt)
    with pytest.raises(exception) as caught:
        run.run()
    assert caught.value is failure
    resumed = _run(tmp_path, candidate=run.candidate, protocol=run.protocol)
    report = resumed.run()
    before = resumed.ledger.path.read_bytes()
    assert resumed.run() == report
    assert resumed.ledger.path.read_bytes() == before
    events = [row["event"] for row in resumed.ledger.read()]
    assert events.count("candidate_promoted") == events.count("promotion_attempt") == 1
    assert resumed.ledger.verify()["valid"] is True


def test_precommit_failure_does_not_publish_terminal_or_deployment(tmp_path, monkeypatch):
    import evolution.registry as module

    run = _run(tmp_path)
    before = run.registry.active_path.read_bytes()
    original = module.atomic_json

    def interrupt(path, payload):
        if Path(path).name == "active.json":
            raise OSError("interrupt before commit")
        return original(path, payload)

    monkeypatch.setattr(module, "atomic_json", interrupt)
    with pytest.raises(OSError, match="interrupt before commit"):
        run.run()
    assert run.registry.active_path.read_bytes() == before
    assert not run.registry.state.get("publications")
    assert not (tmp_path / "out" / "version.json").exists()
    assert not (tmp_path / "out" / "promotion.json").exists()
    assert not run.ledger.read()


@pytest.mark.parametrize("change", ["candidate", "protocol", "config", "stages"])
def test_committed_promotion_refuses_identity_drift(tmp_path, change):
    run = _run(tmp_path)
    run.run()
    resumed = _run(tmp_path, candidate=run.candidate, protocol=run.protocol)
    target = getattr(resumed, change)
    if change == "stages":
        target.pop()
    else:
        target["changed"] = True
    before_registry = resumed.registry.active_path.read_bytes()
    before_ledger = resumed.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="identity changed"):
        resumed.run()
    assert resumed.registry.active_path.read_bytes() == before_registry
    assert resumed.ledger.path.read_bytes() == before_ledger


def test_committed_receipt_survives_clear_and_does_not_redeploy(tmp_path):
    from detector.interventions import load_intervention_registry

    run = _run(tmp_path)
    report = run.run()
    deployment = load_intervention_registry(run.registry.active_path)
    assert set(deployment) == {"schema_version", "interventions", "content_hash"}
    run.registry.clear()
    before = run.registry.active_path.read_bytes()
    assert run.run() == report
    assert run.registry.entries == []
    assert run.registry.active_path.read_bytes() == before


def test_committed_projection_conflict_preserves_original_bytes(tmp_path):
    run = _run(tmp_path)
    run.run()
    path = tmp_path / "out" / "version.json"
    original = b'{"conflicting": true}'
    path.write_bytes(original)
    before = run.registry.active_path.read_bytes()
    with pytest.raises(ValueError, match="projection conflicts"):
        run.run()
    assert path.read_bytes() == original
    assert run.registry.active_path.read_bytes() == before


def test_ledger_idempotency_conflicts_and_parallel_writers_preserve_chain(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("history", {"original": True})
    historical = ledger.path.read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ledger.append("published", {"value": 1}, idempotency_key="publication"), range(8)))
    assert all(row == results[0] for row in results)
    assert ledger.path.read_bytes().startswith(historical)
    assert len(ledger.read()) == 2 and ledger.verify()["valid"] is True
    before = ledger.path.read_bytes()
    for event, payload in (("different", {"value": 1}), ("published", {"value": 2})):
        with pytest.raises(ValueError, match="conflicts"):
            ledger.append(event, payload, idempotency_key="publication")
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("name", ["version.json", "promotion.json"])
def test_existing_output_without_commit_cannot_deploy(tmp_path, name):
    run = _run(tmp_path)
    path = tmp_path / "out" / name
    original = b'{"existing": true}'
    path.write_bytes(original)
    before = run.registry.active_path.read_bytes()
    with pytest.raises(ValueError, match="output already exists"):
        run.run()
    assert run.registry.active_path.read_bytes() == before
    assert path.read_bytes() == original


def test_changed_source_cannot_resume_committed_promotion(tmp_path, monkeypatch):
    import evolution.promotion as module

    run = _run(tmp_path)
    run.run()
    before = run.ledger.path.read_bytes()
    monkeypatch.setattr(module, "source_identity", lambda *args: "changed-source")
    with pytest.raises(ValueError, match="identity changed"):
        run.run()
    assert run.ledger.path.read_bytes() == before


@pytest.mark.parametrize("change", ["candidate", "gate", "event"])
def test_resealed_inconsistent_publication_is_refused(tmp_path, change):
    from detector.identity import digest

    run = _run(tmp_path)
    run.run()
    path = run.registry.active_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = next(iter(payload["state"]["publications"].values()))["record"]
    if change == "candidate":
        record["report"]["candidate_id"] = "unrelated-candidate"
    elif change == "gate":
        record["report"]["gates"]["m"]["passed"] = False
    else:
        record["events"][0]["payload"]["registry_hash"] = "0" * 64
    payload["digest"] = digest({key: value for key, value in payload.items() if key != "digest"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    before_registry = path.read_bytes()
    before_ledger = run.ledger.path.read_bytes()
    resumed = _run(tmp_path, candidate=run.candidate, protocol=run.protocol)
    with pytest.raises(ValueError):
        resumed.run()
    assert path.read_bytes() == before_registry
    assert run.ledger.path.read_bytes() == before_ledger


def test_mechanism_aligned_promotion_recovers_real_commit(tmp_path, monkeypatch):
    import evolution.promotion as module
    from evolution.tests.test_promotion_alignment import (
        _mechanism, _bound_candidate, _freeze, _Backend, GOLD, ALL_CASES, PROTECTION, CHALLENGE,
    )
    from evolution.promotion import load_protocol

    mechanism = _mechanism()
    candidate = _bound_candidate(mechanism)
    protocol = load_protocol(_freeze(tmp_path, mechanism=mechanism)["loaded_from"],
                             dataset_root=str(tmp_path / "data"), stages=("structural", "harmlessness", "behavior", "m"))
    protocol["candidate_content_hash"] = candidate["content_hash"]
    config = {"dataset_root": str(tmp_path / "data"), "gates": {}, "library": {}}
    registry = ActiveRegistry(tmp_path / "registry")
    ledger = Ledger(tmp_path / "ledger.jsonl")
    run = PromotionRun(candidate=candidate, protocol=protocol, config=config,
                       output_dir=tmp_path / "out", active_registry=registry, ledger=ledger,
                       backend=_Backend(
                           {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False) for case in ALL_CASES},
                           {case: GOLD[case] for case in ALL_CASES}))
    original = module.save_json

    def fail(path, payload):
        if Path(path).name == "version.json":
            raise OSError("aligned projection failure")
        return original(path, payload)

    monkeypatch.setattr(module, "save_json", fail)
    with pytest.raises(OSError, match="aligned projection failure"):
        run.run()
    monkeypatch.setattr(module, "save_json", original)
    report = run.run()
    assert report["state"] == "promoted"
    assert report["promotion_diagnosis"]["reason_code"] == "promoted"
    assert report["version"]["registry_hash"] == ActiveRegistry(tmp_path / "registry").registry_hash
    assert ledger.verify()["valid"] is True


@pytest.mark.parametrize("detection_delta", [True, False])
def test_public_promotion_return_is_the_persisted_terminal(tmp_path, monkeypatch, detection_delta):
    import evolution.promotion as module

    protocol = _standard_protocol(tmp_path)
    monkeypatch.setattr(module, "load_protocol", lambda *args, **kwargs: protocol)
    report = module.promote(
        candidate=_candidate(), protocol_path=tmp_path / "protocol.json", output_dir=tmp_path / "out",
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {},
                "registry_dir": str(tmp_path / "registry"), "ledger_path": str(tmp_path / "ledger.jsonl")},
        backend=_ScriptedBackend(control_prediction={"A/001.json": False, "B/001.json": False},
                                 detection_delta=detection_delta),
    )
    stored = json.loads((tmp_path / "out" / "promotion.json").read_text(encoding="utf-8"))
    assert report == stored
    assert report["candidate_status"] == ("active" if report["admitted"] else "frozen")


def test_public_promotion_recovers_its_committed_candidate_status(tmp_path, monkeypatch):
    import evolution.promotion as module

    candidate, protocol = _candidate(), _standard_protocol(tmp_path)
    monkeypatch.setattr(module, "load_protocol", lambda *args, **kwargs: protocol)
    config = {"dataset_root": str(tmp_path), "gates": {}, "library": {},
              "registry_dir": str(tmp_path / "registry"), "ledger_path": str(tmp_path / "ledger.jsonl")}
    backend = _ScriptedBackend(control_prediction={"A/001.json": False, "B/001.json": False})
    original = module.save_json

    def fail(path, payload):
        if Path(path).name == "version.json":
            raise OSError("public projection failure")
        return original(path, payload)

    monkeypatch.setattr(module, "save_json", fail)
    arguments = dict(candidate=candidate, protocol_path=tmp_path / "protocol.json",
                     output_dir=tmp_path / "out", config=config, backend=backend)
    with pytest.raises(OSError, match="public projection failure"):
        module.promote(**arguments)
    monkeypatch.setattr(module, "save_json", original)

    def no_measurement(*args, **kwargs):
        raise AssertionError("public recovery cannot remeasure")

    monkeypatch.setattr(backend, "evaluate_fresh", no_measurement)
    report = module.promote(**arguments)
    assert report["candidate_status"] == "active"
    assert report == json.loads((tmp_path / "out" / "promotion.json").read_text(encoding="utf-8"))
    publication = next(iter(ActiveRegistry(tmp_path / "registry").state["publications"].values()))
    assert publication["record"]["report"] == report
