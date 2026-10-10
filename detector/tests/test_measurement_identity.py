from copy import deepcopy

import pytest

from detector.measurement import case_execution, request_execution, measurement_sources


def _execution():
    return request_execution(
        [], started_at="same", finished_at="same", elapsed_seconds=0.0,
    )


def test_identical_inputs_and_clock_readings_have_distinct_measurements():
    first, second = _execution(), _execution()
    assert first["measurement_id"] != second["measurement_id"]
    assert len(first["measurement_id"]) == 32


def test_copying_a_cached_artifact_preserves_its_measurement():
    original = _execution()
    assert deepcopy(original)["measurement_id"] == original["measurement_id"]


def test_case_aggregation_preserves_attempt_measurement_identities():
    first, second = _execution(), _execution()
    attempts = [{"execution": first}, {"execution": second}]
    combined = case_execution(
        attempts, started_at="same", finished_at="same", elapsed_seconds=0.0,
    )
    assert [item["execution"]["measurement_id"] for item in combined["case_attempts"]] == [
        first["measurement_id"], second["measurement_id"],
    ]
    assert combined["measurement_id"] not in {first["measurement_id"], second["measurement_id"]}


def test_reaggregation_cannot_make_reused_attempts_independent():
    attempts = [{"execution": _execution()}]
    kwargs = dict(started_at="same", finished_at="same", elapsed_seconds=0.0)
    first = case_execution(attempts, **kwargs)
    second = case_execution(deepcopy(attempts), **kwargs)
    assert first["measurement_id"] == second["measurement_id"]


def test_legacy_attempts_do_not_gain_measurement_identity_by_aggregation():
    legacy = {"requests": [], "provider_attempts": 0, "usage_missing_attempts": 0}
    combined = case_execution(
        [{"execution": legacy}], started_at="same", finished_at="same", elapsed_seconds=0.0,
    )
    assert combined["measurement_id"] is None


def test_real_detector_entrypoint_creates_identity_per_invocation(monkeypatch):
    from detector.api import Detector
    from detector.tests.test_api import CONFIG
    from detector.tests.test_runner import _envelope

    detector = Detector(CONFIG, require_credentials=False)
    monkeypatch.setattr(detector, "_detect", lambda *args, **kwargs: _envelope("a"))
    first = detector.detect({}, case_id="a")
    second = detector.detect({}, case_id="a")
    assert first["execution"]["measurement_id"] != second["execution"]["measurement_id"]
    assert first["execution"]["provider_attempts"] == 0


@pytest.mark.parametrize("invalid", [None, True, "", "x" * 32, "A" * 32])
def test_one_unidentified_attempt_makes_aggregate_identity_unknown(invalid):
    first, second = _execution(), _execution()
    second["measurement_id"] = invalid
    combined = case_execution(
        [{"execution": first}, {"execution": second}],
        started_at="same", finished_at="same", elapsed_seconds=0.0,
    )
    assert combined["measurement_id"] is None


def test_actual_cache_hit_preserves_measurement_and_new_run_changes_it(tmp_path, sample_record):
    from evolution.backends import CachingBackend, DetectionBackend
    from detector.tests.test_runner import _envelope

    class MeasuredBackend(DetectionBackend):
        name = "measured_fake"
        calls = 0

        def run_case(self, record, *, case_id, registry=None, gold=None):
            self.calls += 1
            result = _envelope(case_id)
            result["execution"] = _execution()
            return result

    backend = MeasuredBackend()
    cached = CachingBackend(backend, tmp_path / "cache")
    record = sample_record
    original = cached.run_case(record, case_id="a")
    replayed = cached.run_case(record, case_id="a")
    assert backend.calls == 1
    assert replayed["execution"]["measurement_id"] == original["execution"]["measurement_id"]
    fresh = backend.run_case(record, case_id="a")
    assert fresh["execution"]["measurement_id"] != original["execution"]["measurement_id"]


@pytest.mark.parametrize("fault", ["changed_id", "missing_child", "duplicate_child", "nested_schema"])
def test_unverifiable_case_lineage_never_returns_original_sources(fault):
    children = [_execution(), _execution()]
    combined = case_execution(
        [{"execution": child} for child in children],
        started_at="same", finished_at="same", elapsed_seconds=0,
    )
    assert measurement_sources(combined) == tuple(child["measurement_id"] for child in children)
    if fault == "changed_id":
        combined["measurement_id"] = "0" * 64
    elif fault == "missing_child":
        combined["case_attempts"][0]["execution"].pop("measurement_id")
    elif fault == "duplicate_child":
        combined = case_execution(
            [{"execution": children[0]}, {"execution": children[0]}],
            started_at="same", finished_at="same", elapsed_seconds=0,
        )
    else:
        combined["case_attempts"][0]["execution"]["schema_version"] = "unknown"
    assert measurement_sources(combined) == ()


def test_actual_mock_invocations_own_synthetic_measurements(sample_record):
    from evolution.backends import MockDetectorBackend

    backend = MockDetectorBackend()
    first = backend.run_case(sample_record, case_id="a", gold=False)
    second = backend.run_case(sample_record, case_id="a", gold=False)
    assert measurement_sources(first["execution"]) != measurement_sources(second["execution"])
    assert first["provenance"]["measurement_kind"] == "synthetic"
    assert first["provenance"]["execution_identity"] == second["provenance"]["execution_identity"]


def test_mock_cache_hit_preserves_original_invocation(sample_record, tmp_path):
    from evolution.backends import CachingBackend, MockDetectorBackend

    cached = CachingBackend(MockDetectorBackend(), tmp_path / "cache")
    first = cached.run_case(sample_record, case_id="a", gold=False)
    second = cached.run_case(sample_record, case_id="a", gold=False)
    assert measurement_sources(first["execution"]) == measurement_sources(second["execution"])


def test_mock_without_a_packet_cannot_certify_route_provenance():
    from evolution.backends import MockDetectorBackend

    result = MockDetectorBackend().run_case({"question": "q", "history": []}, case_id="a", gold=False)
    assert result["provenance"]["execution_identity"] is None
