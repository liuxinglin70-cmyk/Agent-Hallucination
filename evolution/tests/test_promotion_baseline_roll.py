"""A pinned control roll makes the measured route explicit.

Why this exists: the frozen-judge replay reuses a settled baseline stage for
every registry that does not target that stage's hook. On a localization
candidate whose mechanism lives on the *recall* path, an unpinned control can
replay a judge-positive baseline, so the recall path never runs and the
localization it was supposed to improve never appears -- the gate then reports
the mechanism as unmeasurable (or, worse, as measured) for a route the run
never exercised.

The pin is frozen with the protocol and hashed, so it cannot be swapped after
the batch was fixed, and a roll that does not actually reproduce the declared
path is refused at freeze time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.promotion import (
    PromotionProtocolError,
    freeze_protocol,
    load_protocol,
)


def _roll(case_ids, *, positive_source="recall_upgrade"):
    return {
        case: {
            "case_id": case,
            "state": "verified_error",
            "decision_authority": "authoritative",
            "runtime_valid": True,
            "responsible_step": 1,
            "proof": {
                "terminal_basis": {
                    "decisive_facts": {"positive_source": positive_source},
                },
            },
        }
        for case in case_ids
    }


def _batch(tmp_path: Path, case_ids):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(list(case_ids)), encoding="utf-8")
    return path


def _detector_config(tmp_path: Path) -> Path:
    path = tmp_path / "detector.json"
    path.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    return path


def _run_config(tmp_path: Path) -> dict:
    """A config that can resolve a registry and ledger under ``tmp_path``."""
    return {
        "dataset_root": str(tmp_path),
        "gates": {},
        "library": {},
        "registry_dir": str(tmp_path / "registry"),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
    }


class _NullRegistry:
    state = "empty"
    entries: list = []
    registry: dict = {}


class _NullLedger:
    def append(self, *_args, **_kwargs):
        return None

    def read(self):
        return []


def _swapped_roll_run(tmp_path, protocol):
    from evolution.promotion import PromotionRun

    return PromotionRun(
        candidate={"candidate_id": "x", "artifacts": [], "content_hash": "h"},
        protocol=protocol,
        config=_run_config(tmp_path),
        output_dir=tmp_path / "out",
        active_registry=_NullRegistry(),
        ledger=_NullLedger(),
    )


def _freeze(tmp_path, cases, roles, gold, **kwargs):
    return freeze_protocol(
        tmp_path / "protocol.json",
        batch_path=_batch(tmp_path, cases),
        roles=roles,
        gold=gold,
        gold_steps={},
        detector_config=_detector_config(tmp_path),
        workers=2,
        reason="test",
        **kwargs,
    )


CASES = ["A/001.json", "A/002.json", "B/001.json", "B/002.json"]
ROLES = {c: "benefit" for c in CASES}
GOLD = {c: True for c in CASES}


def test_a_roll_that_covers_the_batch_is_frozen_with_its_hash(tmp_path):
    roll_path = tmp_path / "roll.json"
    roll_path.write_text(json.dumps(_roll(CASES)), encoding="utf-8")
    protocol = _freeze(
        tmp_path, CASES, ROLES, GOLD,
        baseline_roll={
            "path": str(roll_path),
            "declared_path": "recall_upgrade",
            "source_run": "archived-run",
        },
    )
    spec = protocol["baseline_roll"]
    assert spec is not None
    assert spec["declared_path"] == "recall_upgrade"
    assert spec["source_run"] == "archived-run"
    # ``fingerprint`` is the repository's short content digest; the point is
    # that a stable hash of the roll is frozen with the protocol.
    assert spec["roll_hash"]
    assert protocol["require_fresh"] is False


def test_a_roll_that_misses_a_batch_case_is_refused(tmp_path):
    """A partial roll would measure the missing cases under other conditions."""
    roll_path = tmp_path / "roll.json"
    roll_path.write_text(json.dumps(_roll(CASES[:3])), encoding="utf-8")
    with pytest.raises(PromotionProtocolError, match="does not cover the batch"):
        _freeze(
            tmp_path, CASES, ROLES, GOLD,
            baseline_roll={"path": str(roll_path)},
        )


def test_a_roll_that_does_not_reproduce_the_declared_path_is_refused(tmp_path):
    """Declaring ``recall_upgrade`` while the roll shows ``judge`` is refused.

    This is the failure the pin exists to prevent: the protocol would have
    reported a reading for a route the roll never took.
    """
    roll_path = tmp_path / "roll.json"
    roll_path.write_text(
        json.dumps(_roll(CASES, positive_source="judge")), encoding="utf-8",
    )
    with pytest.raises(PromotionProtocolError, match="declared path"):
        _freeze(
            tmp_path, CASES, ROLES, GOLD,
            baseline_roll={
                "path": str(roll_path),
                "declared_path": "recall_upgrade",
            },
        )


def test_a_missing_roll_file_is_refused(tmp_path):
    with pytest.raises(PromotionProtocolError, match="not found"):
        _freeze(
            tmp_path, CASES, ROLES, GOLD,
            baseline_roll={"path": str(tmp_path / "absent.json")},
        )


def test_pinning_and_requiring_fresh_together_is_refused(tmp_path):
    """They are opposite instructions, and silently picking one would hide it."""
    roll_path = tmp_path / "roll.json"
    roll_path.write_text(json.dumps(_roll(CASES)), encoding="utf-8")
    with pytest.raises(PromotionProtocolError, match="mutually exclusive"):
        _freeze(
            tmp_path, CASES, ROLES, GOLD,
            baseline_roll={"path": str(roll_path)},
            require_fresh=True,
        )


def test_require_fresh_alone_is_recorded(tmp_path):
    protocol = _freeze(tmp_path, CASES, ROLES, GOLD, require_fresh=True)
    assert protocol["require_fresh"] is True
    assert protocol["baseline_roll"] is None


def test_promotion_run_refuses_a_swapped_roll(tmp_path):
    """The run re-verifies the roll before spending any provider budget.

    Two files are mutated on purpose: swapping the roll's *content* must be
    caught by the frozen hash, which is the check a post-freeze edit would
    otherwise defeat.
    """
    from evolution.promotion import PromotionRun

    roll_path = tmp_path / "roll.json"
    roll_path.write_text(json.dumps(_roll(CASES)), encoding="utf-8")
    protocol = _freeze(
        tmp_path, CASES, ROLES, GOLD, baseline_roll={"path": str(roll_path)},
    )
    protocol["resolved_cases"] = list(CASES)
    protocol["gold"] = dict(GOLD)
    protocol["gold_steps"] = {}
    roll_path.write_text(
        json.dumps(_roll(CASES, positive_source="judge")), encoding="utf-8",
    )

    with pytest.raises(PromotionProtocolError, match="changed after"):
        _swapped_roll_run(tmp_path, protocol)


def test_run_identity_carries_the_roll_hash(tmp_path):
    """A resumed arm must not be reused under a different pinned baseline."""
    from evolution.promotion import PromotionRun

    roll_path = tmp_path / "roll.json"
    roll_path.write_text(json.dumps(_roll(CASES)), encoding="utf-8")
    protocol = _freeze(
        tmp_path, CASES, ROLES, GOLD, baseline_roll={"path": str(roll_path)},
    )
    protocol["resolved_cases"] = list(CASES)
    protocol["gold"] = dict(GOLD)
    protocol["gold_steps"] = {}

    class _Backend:
        name = "stub"

    run = PromotionRun(
        candidate={"candidate_id": "x", "artifacts": [], "content_hash": "h"},
        protocol=protocol,
        config=_run_config(tmp_path),
        output_dir=tmp_path / "out",
        backend=_Backend(),
        active_registry=_NullRegistry(),
        ledger=_NullLedger(),
    )
    identity = run._arm_identity([], CASES, fresh=False)
    assert identity["baseline_roll_hash"] == protocol["baseline_roll"]["roll_hash"]


def test_an_unpinned_protocol_has_no_roll(tmp_path):
    """Absent means the historical behaviour: no pin, no hash."""
    protocol = _freeze(tmp_path, CASES, ROLES, GOLD)
    assert protocol["baseline_roll"] is None
