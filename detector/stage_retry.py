"""Successful model stages reused only within one immutable case retry."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import threading
from typing import Any, Callable, Iterator, Mapping

from detector.identity import digest
from detector.runtime import check_request_deadline, current_control, usage_scope

_CACHE: ContextVar["StageRetryCache | None"] = ContextVar("stage_retry_cache", default=None)


class StageRetryCache:
    def __init__(self):
        self._identity: str | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.attempt = 0
        self.reused: list[dict[str, Any]] = []

    def begin_attempt(self, number: int) -> None:
        with self._lock:
            self.attempt = number
            self.reused = []

    def bind(self, identity: Mapping[str, Any]) -> None:
        value = digest(identity)
        with self._lock:
            if self._identity is not None and self._identity != value:
                raise ValueError("case retry input identity changed; stage reuse refused")
            self._identity = value

    def evaluate(self, stage: str, inputs: Mapping[str, Any], producer: Callable):
        control = current_control()
        if control is not None:
            control.check()
        check_request_deadline()
        if self._identity is None:
            return producer()
        with self._lock:
            key = digest({"case_identity": self._identity, "stage": stage, "inputs": inputs})
            entry = self._entries.get(key)
            if entry is not None:
                if digest(entry["outcome"]) != entry["outcome_digest"]:
                    raise ValueError("stored retry stage changed; reuse refused")
                self.reused.append({"stage": stage, "input_digest": key,
                                    "origin_case_attempt": entry["attempt"],
                                    "request_ids": list(entry["request_ids"]),
                                    "measurement_origin": "prior_case_attempt"})
                return deepcopy(entry["outcome"])
        with usage_scope() as requests:
            outcome = producer()
        result, status = outcome
        vote = (result.get("provider") or {}).get("vote") or {}
        if (result.get("ok") is True and status.get("state") == "ok"
                and vote.get("quorum_met") is not False):
            snapshot = deepcopy(outcome)
            with self._lock:
                self._entries[key] = {"outcome": snapshot, "outcome_digest": digest(snapshot),
                                      "attempt": self.attempt,
                                      "request_ids": [row["request_id"] for row in requests if row.get("request_id")]}
        return outcome


@contextmanager
def stage_retry_scope(cache: StageRetryCache) -> Iterator[None]:
    token = _CACHE.set(cache)
    try:
        yield
    finally:
        _CACHE.reset(token)


def current_stage_retry() -> StageRetryCache | None:
    return _CACHE.get()


def memo_stage(stage: str, inputs: Mapping[str, Any], producer: Callable):
    cache = current_stage_retry()
    return producer() if cache is None else cache.evaluate(stage, inputs, producer)
