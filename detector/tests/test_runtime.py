"""Run-scoped execution controls, and the accounting they feed.

Every defect this file guards against was observed in the 2026-09-23 real runs,
so each test states the observation rather than the implementation:

* a rejected credential repeated once per case, writing thousands of identical
  ``invalid`` rows instead of stopping;
* a whole-case retry that re-opened the case deadline, so "one case, one hour"
  became three hours of worker time;
* a batch that submitted its whole case list before running any of it, which
  makes cancellation meaningless and hides how much work remains;
* two runs pointed at one ``work_root`` overwriting each other's state.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from detector.runtime import (
    RunControl,
    RunStopped,
    bounded_map,
    deadline_scope,
    exclusive_writer,
    parallel_ordered,
    remaining_seconds,
    run_scope,
    usage_scope,
    record_request,
)


# --------------------------------------------------------------------------
# deadlines nest instead of resetting
# --------------------------------------------------------------------------


def test_nested_deadlines_take_the_shortest():
    """A retry may not extend the budget the outer scope already set.

    The real defect: ``detect_with_transport_retry`` called ``detect`` three
    times, and each call opened a fresh ``case_timeout_seconds``. A case that
    used its full hour was therefore retried with another full hour, twice.
    """
    with deadline_scope(1000.0):
        outer = remaining_seconds()
        with deadline_scope(5.0):
            inner = remaining_seconds()
            assert inner is not None and inner <= 5.0
        # The inner scope must not have replaced the outer one.
        after = remaining_seconds()
        assert after is not None and after > 5.0
        assert outer > 900
    assert remaining_seconds() is None


def test_deadline_scope_accepts_an_absolute_deadline():
    target = time.monotonic() + 30
    with deadline_scope(deadline=target):
        remaining = remaining_seconds()
    assert remaining is not None and 0 < remaining <= 30


def test_an_expired_deadline_reports_zero_not_negative():
    with deadline_scope(0.001):
        time.sleep(0.01)
        assert remaining_seconds() == 0.0


# --------------------------------------------------------------------------
# a fatal provider condition stops the run
# --------------------------------------------------------------------------


def test_control_refuses_more_work_after_a_fatal_stop():
    """The batch must stop when the credential is rejected.

    Observed: a run kept submitting cases for ten minutes after the provider
    began answering 401, producing 134 identical ``invalid`` rows that a reader
    then has to be told are not measurements.
    """
    control = RunControl(max_provider_inflight=4)
    control.check()  # no stop yet
    control.stop({"error_type": "authentication_error", "message": "http 401"})
    with pytest.raises(RunStopped) as exc:
        control.check()
    assert exc.value.reason["error_type"] == "authentication_error"


def test_bounded_map_stops_submitting_after_the_stop():
    control = RunControl(max_provider_inflight=2)
    started: list[int] = []

    def work(item: int) -> int:
        started.append(item)
        if item == 0:
            control.stop({"error_type": "authentication_error"})
        return item

    with pytest.raises(RunStopped):
        list(bounded_map(work, range(1000), workers=2, control=control))
    assert len(started) < 20, (
        "a stopped run must not keep draining its input: "
        f"{len(started)} cases were started"
    )


def test_bounded_map_never_submits_more_than_workers_at_once():
    """Submission is bounded, so a stop has something to stop.

    The previous implementation built the full future list up front: every case
    was queued before the first result was read, so cancelling left the whole
    corpus pending and the peak concurrency was the corpus, not the workers.
    """
    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def work(item: int) -> int:
        with lock:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        time.sleep(0.005)
        with lock:
            peak["now"] -= 1
        return item

    results = list(bounded_map(work, range(60), workers=3))
    assert sorted(results) == list(range(60))
    assert peak["max"] <= 3, f"more than the worker count ran at once: {peak}"


def test_run_control_caps_provider_inflight_across_threads():
    control = RunControl(max_provider_inflight=2)
    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def call() -> None:
        with control.request_slot():
            with lock:
                peak["now"] += 1
                peak["max"] = max(peak["max"], peak["now"])
            time.sleep(0.01)
            with lock:
                peak["now"] -= 1

    threads = [threading.Thread(target=call) for _ in range(8)]
    with run_scope(control):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert peak["max"] <= 2, (
        "the provider-wide in-flight cap was not honoured: "
        f"peak was {peak['max']}"
    )
    assert control.peak_requests <= 2


# --------------------------------------------------------------------------
# parallel ballots preserve order and semantics
# --------------------------------------------------------------------------


def test_parallel_ordered_returns_index_order_regardless_of_finish_order():
    """Completion order must not decide which ballot a verdict reports.

    ``majority_vote`` is order-independent, but the sample a stage returns is
    "the first ballot matching the winner", so collecting in completion order
    would make the reported detail fields depend on scheduling.
    """
    def work(index: int) -> str:
        # Reverse the delays: index 0 finishes last.
        time.sleep(0.01 * (5 - index))
        return f"b{index}"

    assert parallel_ordered(work, 5, workers=5) == [f"b{i}" for i in range(5)]


def test_parallel_ordered_keeps_abstentions_as_none():
    def work(index: int) -> str | None:
        return None if index == 1 else f"b{index}"

    assert parallel_ordered(work, 3, workers=3) == ["b0", None, "b2"]


def test_parallel_ordered_single_worker_matches_sequential():
    def work(index: int) -> int:
        return index * 2

    assert parallel_ordered(work, 4, workers=1) == [0, 2, 4, 6]
    assert parallel_ordered(work, 4, workers=4) == [0, 2, 4, 6]


# --------------------------------------------------------------------------
# usage observations
# --------------------------------------------------------------------------


def test_usage_scope_collects_per_request_observations():
    with usage_scope() as observations:
        record_request({"purpose": "judge", "usage": {"total_tokens": 5}})
        record_request({"purpose": "judge", "usage": None})
    assert len(observations) == 2
    assert observations[0]["usage"]["total_tokens"] == 5
    assert observations[1]["usage"] is None


def test_record_request_outside_a_scope_is_a_no_op():
    record_request({"purpose": "judge"})  # must not raise


# --------------------------------------------------------------------------
# one writer per run directory
# --------------------------------------------------------------------------


def test_exclusive_writer_refuses_a_second_process(tmp_path):
    """Two processes on one work_root overwrote each other's run state.

    The ledger survives it (it has its own cross-process lock); the registry,
    checkpoints and caches do not, and the observed outcome was two real runs
    silently writing into one directory.
    """
    import subprocess
    import sys
    import textwrap

    root = tmp_path / "run"
    with exclusive_writer(root):
        script = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            from detector.runtime import exclusive_writer
            try:
                with exclusive_writer(%r):
                    print("ACQUIRED")
            except RuntimeError as exc:
                print("REFUSED")
            """
            % (str(__import__("pathlib").Path(__file__).resolve().parents[2]), str(root))
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
        )
    assert completed.returncode == 0, completed.stderr
    assert "REFUSED" in completed.stdout, (
        "a second process acquired a work_root lease that was already held: "
        f"{completed.stdout!r} {completed.stderr!r}"
    )


def test_exclusive_writer_is_released_after_the_block(tmp_path):
    root = tmp_path / "run"
    with exclusive_writer(root):
        pass
    with exclusive_writer(root):  # re-acquirable once released
        pass


def test_exclusive_writer_allows_nesting_in_one_thread(tmp_path):
    root = tmp_path / "run"
    with exclusive_writer(root):
        with exclusive_writer(root):
            pass
    with exclusive_writer(root):
        pass
