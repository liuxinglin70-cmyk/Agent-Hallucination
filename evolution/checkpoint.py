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

from evolution.backends import fingerprint
from evolution.contracts import save_json


class CycleJournal:
    def __init__(self, directory: Path, contract: dict, *, resume: bool):
        self.directory = directory
        self.path = directory / "checkpoint.json"
        self.lock = threading.Lock()
        self.started = time.monotonic()
        if self.path.exists():
            if not resume:
                raise RuntimeError("cycle checkpoint exists; use --resume")
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state["contract"] != contract:
                raise RuntimeError("resume fingerprint mismatch (code/config/data/split/registry changed)")
            if self.state.get("proposing") and "proposal" not in self.state:
                raise RuntimeError("proposal call interrupted before persistence; refusing a new proposal draw")
        else:
            if resume:
                raise RuntimeError("no checkpoint: historical incomplete cycles cannot be reconstructed")
            self.state = {"contract": contract, "status": "started", "used_seconds": 0.0}
        self.previous_seconds = float(self.state.get("used_seconds", 0))
        self.save()

    def save(self, **updates):
        with self.lock:
            self.state.update(updates)
            self.state["used_seconds"] = self.previous_seconds + time.monotonic() - self.started
            save_json(self.path, self.state)

    def evaluate(self, cases, *, registry, fresh, evaluate, kwargs):
        arm = fingerprint({"registry": registry, "fresh": fresh})

        def one(case):
            path = self.directory / "arms" / arm[:20] / (fingerprint(case)[:20] + ".json")
            if path.exists():
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if (envelope["arm"] != arm or envelope["row"]["case"] != case
                        or fingerprint(envelope["row"]) != envelope["row_hash"]):
                    raise RuntimeError("checkpoint observation integrity failure")
                return envelope["row"]
            result = evaluate([case], **{**kwargs, "workers": 1})
            if len(result) != 1 or result[0]["case"] != case:
                raise RuntimeError("backend returned an incomplete case observation")
            row = result[0]
            save_json(path, {"arm": arm, "row": row, "row_hash": fingerprint(row)})
            self.save()
            return row

        with ThreadPoolExecutor(max_workers=max(1, int(kwargs.get("workers", 1)))) as pool:
            return list(pool.map(one, cases))
