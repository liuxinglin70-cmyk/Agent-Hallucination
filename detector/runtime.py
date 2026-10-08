"""Run-scoped deadlines, cancellation, bounded scheduling and single writers."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar

T = TypeVar("T")
R = TypeVar("R")
_DEADLINE: ContextVar[float | None] = ContextVar("execution_deadline", default=None)
_CONTROL: ContextVar["RunControl | None"] = ContextVar("execution_control", default=None)
_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar("request_usage", default=None)
_OWNERS: dict[str, tuple[int, int, Any]] = {}
_OWNER_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def deadline_scope(seconds: float | None = None, *, deadline: float | None = None) -> Iterator[None]:
    parent = _DEADLINE.get()
    limits = [value for value in (parent, deadline) if value is not None]
    if seconds is not None:
        limits.append(time.monotonic() + max(0.0, float(seconds)))
    token = _DEADLINE.set(min(limits) if limits else None)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def current_deadline() -> float | None:
    return _DEADLINE.get()


def remaining_seconds() -> float | None:
    deadline = current_deadline()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


class RunStopped(RuntimeError):
    def __init__(self, reason: Mapping[str, Any]):
        self.reason = dict(reason)
        super().__init__(str(self.reason.get("message") or self.reason.get("error_type") or "run stopped"))


class RunControl:
    def __init__(self, max_provider_inflight: int = 2):
        if max_provider_inflight < 1:
            raise ValueError("max_provider_inflight must be positive")
        self.limit = int(max_provider_inflight)
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self.stopped = threading.Event()
        self.reason: dict[str, Any] = {}
        self.active_requests = 0
        self.peak_requests = 0

    def stop(self, reason: Mapping[str, Any]) -> None:
        with self._lock:
            if not self.stopped.is_set():
                self.reason = dict(reason)
                self.stopped.set()

    def check(self) -> None:
        if self.stopped.is_set():
            raise RunStopped(self.reason)

    @contextmanager
    def request_slot(self) -> Iterator[None]:
        while True:
            self.check()
            remaining = remaining_seconds()
            if remaining is not None and remaining <= 0:
                raise RunStopped({"error_type": "case_deadline_exceeded", "message": "deadline exceeded waiting for provider capacity"})
            if self._slots.acquire(timeout=min(0.1, remaining) if remaining is not None else 0.1):
                break
        try:
            self.check()
            with self._lock:
                self.active_requests += 1
                self.peak_requests = max(self.peak_requests, self.active_requests)
            try:
                yield
            finally:
                with self._lock:
                    self.active_requests -= 1
        finally:
            self._slots.release()


@contextmanager
def run_scope(control: RunControl | None = None, *, max_provider_inflight: int = 2) -> Iterator[RunControl]:
    effective = control or _CONTROL.get() or RunControl(max_provider_inflight)
    token = _CONTROL.set(effective)
    try:
        yield effective
    finally:
        _CONTROL.reset(token)


def current_control() -> RunControl | None:
    return _CONTROL.get()


@contextmanager
def usage_scope() -> Iterator[list[dict[str, Any]]]:
    parent = _USAGE.get()
    observations: list[dict[str, Any]] = []
    token = _USAGE.set(observations)
    try:
        yield observations
    finally:
        _USAGE.reset(token)
        if parent is not None:
            parent.extend(observations)


def record_request(observation: Mapping[str, Any]) -> None:
    target = _USAGE.get()
    if target is not None:
        target.append(dict(observation))


def bounded_map(function: Callable[[T], R], items: Iterable[T], *, workers: int,
                control: RunControl | None = None) -> Iterator[R]:
    """Yield completed work without submitting the whole corpus in advance."""
    if int(workers) < 1:
        raise ValueError("workers must be positive")
    iterator = iter(items)
    effective = control or current_control()
    pool = ThreadPoolExecutor(max_workers=int(workers))
    pending = set()
    try:
        def fill() -> None:
            while len(pending) < int(workers):
                if effective is not None and effective.stopped.is_set():
                    break
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                pending.add(pool.submit(copy_context().run, function, item))
        fill()
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                yield future.result()
            fill()
        # A stop is not a completion. Raising here keeps the caller from having
        # to infer it from a short result list -- a caller that treated a
        # stopped batch as a finished one would report a truncated sample as a
        # measurement.
        if effective is not None and effective.stopped.is_set():
            raise RunStopped(effective.reason or {"error_type": "stopped"})
    except BaseException:
        if effective is not None:
            effective.stop({"error_type": "cancelled", "message": "run interrupted"})
        for future in pending:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def parallel_ordered(function: Callable[[int], R], count: int, *,
                     workers: int) -> list[R | None]:
    """Keep ballot order while sharing bounded scheduling and cancellation."""
    if count <= 0:
        return []
    if count == 1 or int(workers) <= 1:
        return [function(index) for index in range(count)]
    results: list[R | None] = [None] * count

    def indexed(index: int) -> tuple[int, R]:
        return index, function(index)

    for index, result in bounded_map(indexed, range(count), workers=min(int(workers), count)):
        results[index] = result
    return results


@contextmanager
def exclusive_writer(directory: str | Path) -> Iterator[None]:
    """Fail fast across processes; same-thread nesting shares the OS lease."""
    path = Path(directory).resolve() / ".writer.lock"
    key = os.path.normcase(str(path))
    owner = threading.get_ident()
    with _OWNER_LOCK:
        existing = _OWNERS.get(key)
        if existing is not None:
            if existing[0] != owner:
                raise RuntimeError(f"run directory already has a writer: {path.parent}")
            _OWNERS[key] = (owner, existing[1] + 1, existing[2])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a+b")
            try:
                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise RuntimeError(f"run directory already has a writer: {path.parent}") from exc
            _OWNERS[key] = (owner, 1, handle)
    try:
        yield
    finally:
        with _OWNER_LOCK:
            _, count, handle = _OWNERS[key]
            if count > 1:
                _OWNERS[key] = (owner, count - 1, handle)
            else:
                del _OWNERS[key]
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
