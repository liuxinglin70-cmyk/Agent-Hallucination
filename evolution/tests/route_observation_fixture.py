from types import SimpleNamespace

from detector.identity import build_execution_identity
from detector.measurement import request_execution
from detector.packet import build_packet
from detector.tests.test_runner import _envelope
from evolution.backends import backend_runtime_identity
from evolution.contracts import make_registry
from evolution.route_observations import build_route_measurement
from evolution.tests.test_engine_smoke import _scanner_candidate


def measured_fixture(cases=("a",), candidate=None, predictions=None):
    if candidate is None:
        candidate = _scanner_candidate(family="FAM-fn-x")
        candidate["hook"] = "tracer.system"
        candidate.pop("content_hash", None)
    runtime = backend_runtime_identity(SimpleNamespace(name="controlled", detector=SimpleNamespace(config={})))
    registries = {"control": make_registry([]), "candidate": make_registry([candidate])}
    rows = {}
    record = {"question": "q", "history": [{"role": "assistant", "step": 1, "content": "a"}]}
    for arm, registry in registries.items():
        rows[arm] = []
        for case in cases:
            prediction = (predictions or {}).get(case, False)
            result = _envelope(case, state="verified_error" if prediction else "verified_clean")
            result["execution"] = request_execution([], started_at="fixed", finished_at="fixed", elapsed_seconds=0)
            result["provenance"] = {"execution_identity": build_execution_identity(
                source=runtime["detector_source"], config={}, record=record,
                packet=build_packet(record), registry=registry,
            )}
            rows[arm].append({"case": case, "prediction": prediction, "result": result,
                              "authority": "authoritative", "state": result["state"], "measurement": "authoritative"})
    measurement = build_route_measurement(
        candidate=candidate, partners=[], cases=list(cases), control_rows=rows["control"],
        candidate_rows=rows["candidate"], runtime=runtime,
        control_registry=registries["control"], candidate_registry=registries["candidate"],
    )
    assert measurement is not None
    return candidate, runtime, registries, rows, measurement
