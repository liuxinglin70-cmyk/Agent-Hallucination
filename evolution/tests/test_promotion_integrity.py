"""Regressions for the promotion-protocol integrity defects.

The promotion path is the most expensive measurement in the project and the
only one that writes the active registry. These tests pin the ways a frozen
protocol could fail to be frozen, and the ways a rejection could be converted
into an admission without new evidence:

* gate set cut down through ``--stages``, still recorded as four gates passed;
* roles / gold / gold steps edited in place after the control arm was known;
* a run-parameter field silently rewritten by an ``or`` default;
* the frozen coverage floor never consulted at run time, so relaxed thresholds
  re-scored an already-frozen batch;
* the same protocol re-rolled in a new output directory after a rejection;
* a promotion resetting the registry's cycle counter to zero;
* a version published under a v1 protocol claiming its baseline was pinned.

They are offline: a scripted backend, an in-memory registry, a ledger on
``tmp_path``. No provider call is made.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.promotion import (
    PromotionProtocolError,
    PromotionRun,
    freeze_protocol,
    load_protocol,
    protocol_self_hash,
)


class _ScriptedBackend:
    """Deterministic stand-in: convicts exactly the cases it is given."""

    name = "scripted"

    def __init__(self, predictions: dict[str, bool] | None = None) -> None:
        self.predictions = dict(predictions or {})

    def evaluate(self, cases, *, dataset_root, registry, gold_map, **kwargs):
        rows = []
        for case in cases:
            prediction = self.predictions.get(case, True)
            rows.append({
                "case": case,
                "gold": gold_map.get(case),
                "prediction": prediction,
                "state": "verified_error" if prediction else "verified_clean",
                "authority": "authoritative",
                "result": {
                    "state": "verified_error" if prediction else "verified_clean",
                    "decision_authority": "authoritative",
                    "runtime_valid": True,
                    "stage_status": {},
                },
            })
        return rows

    evaluate_fresh = evaluate


class _Registry:
    def __init__(self, entries: list[dict] | None = None, cycle: int = 0) -> None:
        self.entries = [dict(item) for item in (entries or [])]
        self.state = {"cycle": cycle, "interventions": {}}
        self.writes: list[list[dict]] = []

    def check_limits(self, entries, *, library_cfg=None):
        return []

    def write(self, entries, *, cycle, reason, library_cfg=None):
        from evolution.contracts import make_registry

        self.entries = [dict(entry) for entry in entries]
        self.writes.append(self.entries)
        self.state["cycle"] = int(cycle)
        self.last_reason = reason
        return make_registry(self.entries)

    def record_marginal(self, *args, **kwargs):
        return None


class _Ledger:
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid
        self.events: list[tuple[str, dict]] = []

    def append(self, event, payload):
        self.events.append((event, dict(payload)))
        return {"event": event, "payload": dict(payload)}

    def read(self):
        return [{"event": name, "payload": payload}
                for name, payload in self.events]

    def verify(self):
        return {"valid": self._valid, "records": len(self.events),
                "broken_at": None, "reason": ""}


def _batch(tmp_path: Path, *, roles: dict[str, str], gold: dict[str, bool]):
    """Write a v1 batch with real case files and a detector config."""
    data = tmp_path / "data"
    for case in roles:
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q", "history": [], "is_hallucination": gold[case],
        }), encoding="utf-8")
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(sorted(roles)), encoding="utf-8")
    detector_config = tmp_path / "detector.json"
    detector_config.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    return data, batch, detector_config


def _roles() -> dict[str, str]:
    return {
        "A/001.json": "benefit", "A/002.json": "benefit",
        "C/001.json": "protection", "C/002.json": "protection",
        "E/001.json": "protection", "E/002.json": "protection",
    }


def _gold(roles: dict[str, str]) -> dict[str, bool]:
    return {
        "A/001.json": True, "A/002.json": False,
        "C/001.json": True, "C/002.json": True,
        "E/001.json": False, "E/002.json": False,
    }


def _freeze_v1(tmp_path: Path, *, workers: int = 2, timeout=600, config=None):
    roles = _roles()
    gold = _gold(roles)
    data, batch, detector_config = _batch(tmp_path, roles=roles, gold=gold)
    framework = config or {
        "dataset_root": str(data), "gates": {}, "library": {},
        "registry_dir": str(tmp_path / "registry"),
        "work_root": str(tmp_path / "work"),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
    }
    return freeze_protocol(
        tmp_path / "protocol.json",
        batch_path=batch, roles=roles, gold=gold,
        detector_config=detector_config, workers=workers,
        timeout_seconds=timeout, reason="test",
        dataset_root=data, framework_config=framework,
    )


def _candidate():
    """A v1 candidate, built the way the promotion tests build them."""
    from evolution.contracts import make_intervention
    from evolution.promotion import build_candidate

    entry = make_intervention(
        intervention_id="IV-PROMO-001",
        intervention_type="text_boundary",
        hook="judge.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Prefer the annotated responsible step.",
            "not_when": "Never apply when the delivered result already matches.",
        },
        expected_behavior="the judge convicts the deviating delivery",
        target_polarity="precision",
        cycle=1,
        failure_family="FAM-fp-promo",
        failure_cases=["A/001.json", "A/002.json"],
    )
    return build_candidate(
        candidate_id="promotion:test:v001",
        change_kind="draft",
        base_revision="deadbeef",
        artifacts=[entry],
        development={"source": "research", "screening_passed": True},
    )


def _run(tmp_path: Path, protocol: dict, *, backend=None, stages=None,
         registry=None, config=None, output_dir=None):
    candidate = _candidate()
    return PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config=config or {"dataset_root": str(tmp_path / "data"),
                          "gates": {}, "library": {}},
        output_dir=output_dir or (tmp_path / "out"),
        backend=backend or _ScriptedBackend(),
        active_registry=registry or _Registry(),
        ledger=_Ledger(),
        stages=stages or ("structural", "harmlessness", "behavior", "m"),
    ).run()


# ---------------------------------------------------------------------------
# The gate set is part of the frozen protocol, not an operator knob
def test_v1_run_refuses_a_cut_down_gate_set(tmp_path):
    """Dropping a stage used to admit the candidate and record four gates.

    ``gates`` defaults a skipped gate to ``{"passed": True, "skipped": True}``,
    so the failing list comes back empty and the registry write names
    STRUCTURAL/HARMLESSNESS/BEHAVIOR/M regardless of what actually ran.
    """
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = _run(
        tmp_path, protocol,
        stages=("structural", "harmlessness", "behavior"),
        backend=_ScriptedBackend({"A/001.json": True, "A/002.json": True}),
    )
    assert report["state"] == "promotion_blocked_preflight"
    assert "formal gates are mandatory" in report["terminal_reason"]
    assert not report["admitted"]


def test_the_published_reason_names_the_gates_that_ran(tmp_path):
    """The registry must not assert gates the run did not evaluate."""
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    predictions = {
        "A/001.json": True, "A/002.json": True,
        "C/001.json": False, "C/002.json": True,
        "E/001.json": True, "E/002.json": True,
    }
    registry = _Registry()
    report = _run(
        tmp_path, protocol, backend=_ScriptedBackend(predictions),
        registry=registry,
    )
    if report.get("state") == "promoted":
        assert "STRUCTURAL" in registry.last_reason
        assert "M" in registry.last_reason


# ---------------------------------------------------------------------------
# A frozen protocol may not be edited afterwards
def test_editing_a_frozen_v1_protocol_is_refused(tmp_path):
    """Moving one role string used to change the verdict with no trace.

    v1 had no self-hash, so the loader could not tell the file it read from
    the file that was frozen.
    """
    _freeze_v1(tmp_path)
    path = tmp_path / "protocol.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["roles"]["C/001.json"] = "benefit"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PromotionProtocolError) as exc:
        load_protocol(
            path, dataset_root=tmp_path / "data",
            stages=("structural", "harmlessness", "behavior", "m"),
        )
    assert "changed after it was frozen" in str(exc.value)


def test_editing_a_frozen_gold_step_is_refused(tmp_path):
    _freeze_v1(tmp_path)
    path = tmp_path / "protocol.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gold_steps"] = {"A/001.json": 3}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PromotionProtocolError):
        load_protocol(
            path, dataset_root=tmp_path / "data",
            stages=("structural", "harmlessness", "behavior", "m"),
        )


def test_replacing_a_case_file_is_refused(tmp_path):
    """The batch hash covers case ids, not the trajectories they name."""
    _freeze_v1(tmp_path)
    case = tmp_path / "data" / "A" / "001.json"
    case.write_text(json.dumps({
        "question": "replaced", "history": [], "is_hallucination": True,
    }), encoding="utf-8")
    with pytest.raises(PromotionProtocolError) as exc:
        load_protocol(
            tmp_path / "protocol.json", dataset_root=tmp_path / "data",
            stages=("structural", "harmlessness", "behavior", "m"),
        )
    assert "trajectory content changed" in str(exc.value)


def test_a_protocol_without_a_self_hash_is_refused(tmp_path):
    """Old freezes cannot show they are the file that was frozen."""
    _freeze_v1(tmp_path)
    path = tmp_path / "protocol.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("protocol_hash")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PromotionProtocolError) as exc:
        load_protocol(
            path, dataset_root=tmp_path / "data",
            stages=("structural", "harmlessness", "behavior", "m"),
        )
    assert "no protocol_hash" in str(exc.value)


def test_the_self_hash_ignores_load_time_fields():
    protocol = {"a": 1, "loaded_from": "x", "resolved_cases": [1],
                "protocol_hash": "ignored"}
    digest = protocol_self_hash(protocol)
    assert digest == protocol_self_hash({"a": 1})


# ---------------------------------------------------------------------------
# Frozen parameters must be honoured or refused, never silently rewritten
@pytest.mark.parametrize("workers,timeout,expected", [
    (0, 600, "at least 1"),
    (-1, 600, "at least 1"),
    (2, 0, "must be positive"),
    (2, -5, "must be positive"),
])
def test_unsatisfiable_run_parameters_are_refused(
    tmp_path, workers, timeout, expected,
):
    """``workers or 8`` turned a declared 0 into 8 concurrent calls."""
    roles = _roles()
    gold = _gold(roles)
    data, batch, detector_config = _batch(tmp_path, roles=roles, gold=gold)
    protocol = {
        "schema_version": "evolution.promotion_protocol.v3",
        "batch_path": str(batch), "workers": workers,
        "timeout_seconds": timeout,
    }
    from evolution.promotion import _verify_frozen_run_parameters

    with pytest.raises(PromotionProtocolError) as exc:
        _verify_frozen_run_parameters(protocol)
    assert expected in str(exc.value)


def test_a_null_timeout_is_allowed_and_means_unbounded(tmp_path):
    from evolution.promotion import _verify_frozen_run_parameters

    _verify_frozen_run_parameters({"workers": 2, "timeout_seconds": None})


# ---------------------------------------------------------------------------
# The frozen coverage floor must still bind at run time
def test_a_relaxed_threshold_refuses_a_protocol_frozen_against_a_stricter_one(tmp_path):
    """Freezing a floor nobody consults lets the batch be re-scored.

    The gates are re-read from the run's config, so relaxing
    ``harmlessness_min_protected_*`` after the freeze re-introduces exactly the
    single-polarity protection set that freeze time rejects.
    """
    _freeze_v1(tmp_path)
    strict = {"dataset_root": str(tmp_path / "data"),
              "gates": {"harmlessness_min_protected_tp": 2,
                        "harmlessness_min_protected_tn": 2},
              "library": {}}
    load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
        config=strict,
    )
    relaxed = dict(strict, gates={"harmlessness_min_protected_tp": 0,
                                  "harmlessness_min_protected_tn": 0})
    with pytest.raises(PromotionProtocolError) as exc:
        load_protocol(
            tmp_path / "protocol.json", dataset_root=tmp_path / "data",
            stages=("structural", "harmlessness", "behavior", "m"),
            config=relaxed,
        )
    assert "thresholds changed" in str(exc.value)


# ---------------------------------------------------------------------------
# One frozen protocol, one attempt
def test_a_rejected_candidate_cannot_be_re_rolled(tmp_path):
    """Re-running the same frozen protocol must not mint a second attempt.

    The arm files are keyed on the output directory, so a new directory used
    to mean a fresh pair of measurements on evidence a candidate had already
    consumed -- retrying until the provider roll came out favourable.
    """
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    first = _run(
        tmp_path, protocol, backend=_ScriptedBackend(),
        output_dir=tmp_path / "out1",
    )
    assert first["state"] != "promotion_blocked_preflight"
    # A second output directory is a second attempt on consumed evidence.
    again = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    second = _run(
        tmp_path, again, backend=_ScriptedBackend(),
        output_dir=tmp_path / "out2",
    )
    assert second["state"] == "promotion_blocked_preflight"
    assert "already consumed" in second["terminal_reason"]


def test_the_same_attempt_directory_is_idempotent(tmp_path):
    """Resuming the same attempt must not be refused as a re-roll."""
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    first = _run(tmp_path, protocol, backend=_ScriptedBackend(),
                 output_dir=tmp_path / "out")
    again = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    second = _run(tmp_path, again, backend=_ScriptedBackend(),
                  output_dir=tmp_path / "out")
    assert second["state"] == first["state"]


# ---------------------------------------------------------------------------
# The registry cycle counter
def test_a_promotion_does_not_reset_the_registry_cycle(tmp_path):
    """``cycle=int(protocol.get("cycle") or 0)`` reset it to zero.

    Rollback windows, the version id and the full run's "start empty"
    precondition all read this counter.
    """
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    registry = _Registry(cycle=7)
    predictions = {
        "A/001.json": True, "A/002.json": True,
        "C/001.json": False, "C/002.json": True,
        "E/001.json": True, "E/002.json": True,
    }
    report = _run(
        tmp_path, protocol, backend=_ScriptedBackend(predictions),
        registry=registry,
    )
    if report.get("state") == "promoted":
        assert registry.state["cycle"] == 7, (
            "a promotion must not reset the cycle counter to zero"
        )


# ---------------------------------------------------------------------------
# The baseline registry is pinned
def test_the_frozen_baseline_registry_is_checked_at_run_time(tmp_path):
    """A deployment between freeze and run changes the control arm."""
    _freeze_v1(tmp_path)
    protocol = load_protocol(
        tmp_path / "protocol.json", dataset_root=tmp_path / "data",
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    # A registry that already holds an intervention has a different identity
    # than the one frozen against an empty deployment.
    from evolution.contracts import make_intervention

    entry = make_intervention(
        intervention_id="IV-EARLIER",
        intervention_type="text_boundary",
        hook="judge.system",
        packet_features=["has_tool_calls"],
        body={"directive": "existing deployment",
              "not_when": "Never when the delivery already matches."},
        expected_behavior="existing",
        target_polarity="precision",
        cycle=1,
        failure_family="FAM-fp-existing",
        failure_cases=["A/001.json", "A/002.json"],
    )
    report = _run(
        tmp_path, protocol, backend=_ScriptedBackend(),
        registry=_Registry(entries=[entry]),
    )
    assert report["state"] == "promotion_blocked_preflight"
    assert "active registry changed" in report["terminal_reason"]
