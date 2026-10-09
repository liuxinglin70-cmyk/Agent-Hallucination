"""Run-scoped deadlines, cancellation, bounded scheduling and single writers."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar

from detector.identity import digest

T = TypeVar("T")
R = TypeVar("R")
_DEADLINE: ContextVar[float | None] = ContextVar("execution_deadline", default=None)
_CONTROL: ContextVar["RunControl | None"] = ContextVar("execution_control", default=None)
_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar("request_usage", default=None)
_ADDED_BUDGET: ContextVar["AddedBudget | None"] = ContextVar("added_case_budget", default=None)
_ADDED_REQUESTS: ContextVar["AddedBudget | None"] = ContextVar("added_requests", default=None)
_WRITER_LEASES: ContextVar[Mapping[str, object] | None] = ContextVar("writer_leases", default=None)
_OWNERS: dict[str, tuple[object, int, Any]] = {}
_OWNER_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_path_id(value: str, *, nested: bool = True) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or PureWindowsPath(value).drive:
        raise ValueError("path id must be a relative forward-slash identifier")
    parts = value.split("/")
    if not nested and len(parts) != 1:
        raise ValueError("run id must be one path component")
    reserved = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
                *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in parts:
        if (not part or part in {".", ".."} or part.rstrip(" .") != part
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.split(".", 1)[0].upper() in reserved):
            raise ValueError("path id contains an unsafe component")
    return value


def contained_path(root: str | Path, *parts: str) -> Path:
    resolved_root = Path(root).resolve()
    for part in parts:
        validate_path_id(part)
    target = resolved_root.joinpath(*parts).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError("path escapes its declared root")
    return target


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


class RunStopped(BaseException):
    """Run control must bypass handlers for ordinary case failures."""

    def __init__(self, reason: Mapping[str, Any]):
        self.reason = dict(reason)
        super().__init__(str(self.reason.get("message") or self.reason.get("error_type") or "run stopped"))


class CaseDeadlineExceeded(TimeoutError):
    """A case ran out of time while waiting for provider capacity."""


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
                raise CaseDeadlineExceeded("deadline exceeded waiting for provider capacity")
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


class AddedBudgetExceeded(RuntimeError):
    def __init__(self, resource: str):
        self.resource = resource
        super().__init__(f"added case budget exhausted: {resource}")


class AddedBudget:
    """One admission budget across added hearings, repairs and case retries."""

    def __init__(self, policy: Mapping[str, Any]):
        self._policy = deepcopy(dict(policy))
        self.policy_digest = digest(policy)
        self.started: float | None = None
        self.requests = 0
        self.input_bytes = 0
        self.output_tokens = 0
        self.negative_review_started = False
        self._lock = threading.RLock()

    def begin(self) -> None:
        with self._lock:
            if self.started is None:
                self.started = time.monotonic()
        self.check()

    @property
    def deadline(self) -> float | None:
        limits = [value for value in (current_deadline(),
                  self.started + self._policy["max_added_seconds_per_case"]
                  if self.started is not None else None) if value is not None]
        return min(limits) if limits else None

    def check(self) -> None:
        control = current_control()
        if control is not None:
            control.check()
        deadline = self.deadline
        if deadline is not None and time.monotonic() >= deadline:
            raise AddedBudgetExceeded("time")

    def reserve(self, *, system: str, user: str, output_tokens: int,
                extra_input: Mapping[str, Any] | None = None) -> int:
        with self._lock:
            self.begin()
            # UTF-8 bytes bound content without pretending to have the model tokenizer.
            size = len(system.encode("utf-8")) + len(user.encode("utf-8"))
            if extra_input is not None:
                size += len(json.dumps(extra_input, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if self.requests >= self._policy["max_added_provider_requests_per_case"]:
                raise AddedBudgetExceeded("requests")
            if self.input_bytes + size > self._policy["max_added_prompt_tokens_per_case"]:
                raise AddedBudgetExceeded("input")
            available = self._policy["max_added_output_tokens_per_case"] - self.output_tokens
            cap = min(output_tokens, available)
            if cap <= 0:
                raise AddedBudgetExceeded("output")
            self.requests += 1
            self.input_bytes += size
            self.output_tokens += cap
            return cap

    def reserve_context(self, text: str) -> None:
        with self._lock:
            self.begin()
            size = len(text.encode("utf-8"))
            if self.input_bytes + size > self._policy["max_added_prompt_tokens_per_case"]:
                raise AddedBudgetExceeded("input")
            self.input_bytes += size

    def start_negative_review(self) -> bool:
        with self._lock:
            if self.negative_review_started:
                return False
            self.check()
            self.negative_review_started = True
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "policy_digest": self.policy_digest, "reserved_requests": self.requests,
                "reserved_input_bytes": self.input_bytes, "reserved_output_tokens": self.output_tokens,
                "input_budget_method": "utf8_content_bytes_without_tokenizer",
                "elapsed_seconds": max(0.0, time.monotonic() - self.started)
                if self.started is not None else 0.0,
            }


@contextmanager
def added_budget_scope(policy: Mapping[str, Any] | None) -> Iterator[AddedBudget | None]:
    parent = _ADDED_BUDGET.get()
    if parent is not None and (policy is None or parent.policy_digest != digest(policy)):
        raise ValueError("added budget policy changed within one case")
    if policy is None or policy.get("mode") == "off":
        yield None
        return
    effective = parent or AddedBudget(policy)
    token = _ADDED_BUDGET.set(effective)
    try:
        yield effective
    finally:
        _ADDED_BUDGET.reset(token)


def current_added_budget() -> AddedBudget | None:
    return _ADDED_BUDGET.get()


@contextmanager
def added_request_scope(budget: AddedBudget) -> Iterator[None]:
    if budget is not current_added_budget():
        raise ValueError("added requests require the current case budget")
    token = _ADDED_REQUESTS.set(budget)
    try:
        with deadline_scope(deadline=budget.deadline):
            yield
    finally:
        _ADDED_REQUESTS.reset(token)


def current_added_request_budget() -> AddedBudget | None:
    return _ADDED_REQUESTS.get()


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
    """Fail fast; delegated execution contexts share their existing OS lease."""
    path = Path(directory).resolve() / ".writer.lock"
    key = os.path.normcase(str(path))
    leases = _WRITER_LEASES.get() or {}
    with _OWNER_LOCK:
        existing = _OWNERS.get(key)
        if existing is not None:
            if leases.get(key) is not existing[0]:
                raise RuntimeError(f"run directory already has a writer: {path.parent}")
            lease = existing[0]
            _OWNERS[key] = (lease, existing[1] + 1, existing[2])
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
            lease = object()
            _OWNERS[key] = (lease, 1, handle)
    token = _WRITER_LEASES.set({**leases, key: lease})
    try:
        yield
    finally:
        _WRITER_LEASES.reset(token)
        with _OWNER_LOCK:
            _, count, handle = _OWNERS[key]
            if count > 1:
                _OWNERS[key] = (lease, count - 1, handle)
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
