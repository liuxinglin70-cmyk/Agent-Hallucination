from scripts.analyze_detector_calls import summarize_results, timeline_summary


def test_saved_call_analysis_distinguishes_latency_queue_and_missing_observations():
    report = summarize_results([
        {"execution": {"elapsed_seconds": 4, "requests": [
            {"request_id": "r1", "purpose": "judge", "elapsed_seconds": 3, "queue_seconds": 1,
             "attempt": 1, "status": "response", "usage": {"completion_tokens": 10}},
            {"request_id": "r2", "purpose": "judge_repair", "elapsed_seconds": 2, "queue_seconds": 0,
             "attempt": 2, "status": "transport_error", "usage": None},
        ]}},
        {},
    ])
    assert report["requests"] == 2 and report["repair_requests"] == 1
    assert report["cases_without_request_observations"] == 1
    assert report["by_purpose"]["judge"]["queue_seconds_sum"] == 1
    assert report["by_purpose"]["judge_repair"]["transport_retry_requests"] == 1
    assert report["by_purpose"]["judge_repair"]["usage_missing"] == 1
    assert report["case_seconds_sum"] == 4
    assert "not batch wall time" in report["interpretation"]


def test_timeline_separates_case_attempts_and_overlapping_intervals():
    result = timeline_summary([
        {"case_attempt": 1, "started_seconds": 1, "finished_seconds": 5},
        {"case_attempt": 1, "started_seconds": 2, "finished_seconds": 4},
        {"case_attempt": 2, "started_seconds": 1, "finished_seconds": 5},
        {"elapsed_seconds": 2},
    ])
    assert result == {"timed_requests": 3, "untimed_requests": 1,
                      "request_interval_union_seconds": 8.0, "overlap_work_seconds": 2.0,
                      "peak_overlapping_request_intervals": 2}
