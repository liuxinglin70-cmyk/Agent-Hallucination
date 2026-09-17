"""Append-only hash-chained ledger.

The ledger lives at ``artifacts/evolution/ledger.jsonl``. It is append-only:
the framework never rewrites an existing line. Every record carries its own
content hash and the previous record hash, so ``verify`` can prove integrity
without trusting the framework that wrote it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from evolution.config import resolve_path


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        last = ""
        with self.path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if not last:
            return "0" * 64
        return str(json.loads(last).get("entry_hash") or "0" * 64)

    def append(self, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            record: dict[str, Any] = {
                "event": str(event),
                "payload": dict(payload),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            prev_hash = self.last_hash()
            record["prev_hash"] = prev_hash
            canonical = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            record["entry_hash"] = _digest(canonical)
            record["event_id"] = record["entry_hash"][:20]
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
        return record

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        return records

    def verify(self) -> dict[str, Any]:
        records = self.read()
        previous = "0" * 64
        for index, record in enumerate(records):
            if str(record.get("prev_hash") or "") != previous:
                return {
                    "valid": False,
                    "records": len(records),
                    "broken_at": index,
                    "reason": "prev_hash chain broken",
                }
            hashable = {
                key: value for key, value in record.items()
                if key not in {"entry_hash", "event_id"}
            }
            canonical = json.dumps(
                hashable, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            expected = _digest(canonical)
            if str(record.get("entry_hash") or "") != expected:
                return {
                    "valid": False,
                    "records": len(records),
                    "broken_at": index,
                    "reason": "entry_hash mismatch",
                }
            previous = str(record.get("entry_hash") or "")
        return {"valid": True, "records": len(records), "broken_at": None, "reason": ""}


def load_ledger(config: Mapping[str, Any]) -> Ledger:
    return Ledger(resolve_path(config["ledger_path"]))


__all__ = ["Ledger", "load_ledger"]
