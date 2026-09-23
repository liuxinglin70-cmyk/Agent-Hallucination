"""Append-only hash-chained ledger.

The ledger lives at ``artifacts/evolution/ledger.jsonl``. It is append-only:
the framework never rewrites an existing line. Every record carries its own
content hash and the previous record hash, so ``verify`` can prove integrity
without trusting the framework that wrote it.

Appends take a file lock in addition to the in-process thread lock. Two
processes sharing a work root previously both read the same ``last_hash`` and
appended from it, which lost records and broke the chain -- and every later
run refuses to start on a broken chain. The framework assumes the caller runs
one cycle at a time (``reports/real_evolution_20260923`` records an incident
where that assumption was violated); the lock turns that assumption from an
unrecoverable data loss into a serialized wait.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from evolution.config import resolve_path

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


LOCK_WAIT_SECONDS = 900.0
LOCK_POLL_SECONDS = 0.05


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Exclusive cross-process lock around read-last-hash + append."""
        handle = open(self.lock_path, "a+b")
        try:
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    elif msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"could not lock the ledger at {self.lock_path} "
                            f"within {LOCK_WAIT_SECONDS:.0f}s; another process "
                            "may be writing to the same work root"
                        )
                    time.sleep(LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                try:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            handle.close()

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
        with self._lock, self._file_lock():
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
                handle.flush()
                os.fsync(handle.fileno())
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
