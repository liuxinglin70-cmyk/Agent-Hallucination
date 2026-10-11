"""Durable, run-scoped observations; resume never redraws a completed arm.

An observation is keyed by case input, registry and evaluation mode. Global
caches may retry infrastructure failures on later cycles; this journal retains
every outcome, including failures, for the current paired comparison.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import threading
import time

from detector.identity import digest
from detector.interventions import authority_identity
from evolution.contracts import save_json


class CycleJournal:
    def __init__(
        self,
        directory: Path,
        contract: dict,
        *,
        resume: bool,
        registry_before: dict | None = None,
    ):
        self.directory = directory
        self.path = directory / "checkpoint.json"
        self.lock = threading.Lock()
        self.started = time.monotonic()
        if self.path.exists():
            if not resume:
                raise RuntimeError("cycle checkpoint exists; use --resume")
            self._state = self.load_state(self.path, contract)
        else:
            if resume:
                raise RuntimeError("no checkpoint: historical incomplete cycles cannot be reconstructed")
            self._state = {
                "schema_version": "evolution.cycle_checkpoint.v3",
                "contract": contract, "status": "started", "used_seconds": 0.0,
                "authority": None,
            }
        if registry_before is not None and "registry_before" not in self._state:
            # The registry the cycle started from. Resuming needs it to
            # re-establish that starting point if the interrupted run already
            # committed its own admission.
            self._state["registry_before"] = registry_before
        self.previous_seconds = float(self._state.get("used_seconds", 0))
        self.save()

    @staticmethod
    def load_state(path: Path, contract: dict) -> dict:
        if not path.exists():
            raise RuntimeError("no checkpoint: historical incomplete cycles cannot be reconstructed")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema_version") != "evolution.cycle_checkpoint.v3":
            raise RuntimeError("unsupported cycle checkpoint schema")
        if state["contract"] != contract:
            raise RuntimeError("resume fingerprint mismatch (code/config/data/split/registry changed)")
        CycleJournal._verify_authority(state)
        if state.get("proposing") and "proposal" not in state:
            raise RuntimeError(
                "proposal call interrupted before persistence; refusing a "
                "new proposal draw for the same cycle. This refusal is "
                "deliberate: re-drawing would be additional proposal search "
                "on an already-fixed patrol. Recover by starting a new "
                "work_root/registry_dir/ledger_path for a fresh cycle."
            )
        return state

    @staticmethod
    def _verify_authority(state: dict, *, calibration=None) -> None:
        if "authority" not in state:
            raise RuntimeError("checkpoint lacks its authority snapshot")
        previous = state["authority"]
        if previous is None:
            return
        from detector.conviction_calibration import default_calibration
        view = calibration if calibration is not None else default_calibration()
        current = view.identity(list(previous["status"]))
        if current != previous:
            raise RuntimeError("checkpoint authority input changed; refusing a new observation")

    @property
    def state(self) -> dict:
        with self.lock:
            if self._state is None:
                raise RuntimeError("checkpoint commit is unknown; reopen before reuse")
            return json.loads(json.dumps(self._state, ensure_ascii=False, allow_nan=False))

    def _persist_state(self, **updates):
        if self._state is None:
            raise RuntimeError("checkpoint commit is unknown; reopen before reuse")
        previous = self._state
        candidate = json.loads(json.dumps({**previous, **updates}, ensure_ascii=False, allow_nan=False))
        candidate["used_seconds"] = self.previous_seconds + time.monotonic() - self.started
        try:
            save_json(self.path, candidate)
        except BaseException:
            # Atomic replacement can finish before a signal reaches the caller.
            try:
                persisted = json.loads(self.path.read_text(encoding="utf-8"))
                if digest(persisted) not in {digest(previous), digest(candidate)}:
                    raise RuntimeError("checkpoint commit changed during publication")
                self._state = persisted
            except BaseException:
                self._state = None
            raise
        self._state = candidate

    def save(self, **updates):
        with self.lock:
            self._persist_state(**updates)

    def _bind_authority(self, authority):
        with self.lock:
            if self._state is None:
                raise RuntimeError("checkpoint commit is unknown; reopen before reuse")
            view = None
            if authority is not None or self._state.get("authority") is not None:
                from detector.conviction_calibration import default_calibration
                view = default_calibration()
            self._verify_authority(self._state, calibration=view)
            if authority is None:
                return
            previous = self._state["authority"]
            names = sorted(set(authority["status"]) | set((previous or {}).get("status", {})))
            if view.identity(list(authority["status"])) != authority:
                raise RuntimeError("checkpoint authority input changed; refusing a new observation")
            combined = view.identity(names)
            if combined != previous:
                self._persist_state(authority=combined)

    def evaluate(self, cases, *, registry, fresh, evaluate, kwargs):
        arm = digest({"registry": registry, "fresh": fresh})
        authority = authority_identity(registry)
        self._bind_authority(authority)

        def one(case):
            path = self.directory / "arms" / arm[:20] / (digest(case)[:20] + ".json")
            if path.exists():
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if envelope.get("authority") != authority:
                    raise RuntimeError("checkpoint authority input changed; refusing a new observation")
                if (envelope["arm"] != arm or envelope["row"]["case"] != case
                        or digest(envelope["row"]) != envelope["row_hash"]):
                    raise RuntimeError("checkpoint observation integrity failure")
                return envelope["row"]
            result = evaluate([case], **{**kwargs, "workers": 1})
            if len(result) != 1 or result[0]["case"] != case:
                raise RuntimeError("backend returned an incomplete case observation")
            row = result[0]
            self._bind_authority(authority)
            save_json(path, {"arm": arm, "authority": authority, "row": row, "row_hash": digest(row)})
            self.save()
            return row

        with ThreadPoolExecutor(max_workers=max(1, int(kwargs.get("workers", 1)))) as pool:
            return list(pool.map(one, cases))
