from evolution.route_failures import route_failure_record, route_failure_state
from copy import deepcopy
from types import SimpleNamespace

import pytest

from detector.identity import digest
from evolution.route_observations import build_route_measurement, route_measurement_sources, measurement_for_candidate
from evolution.tests.route_observation_fixture import measured_fixture


def _measured_record(cycle, measurement):
    value = _record(cycle)
    value["measurement"] = deepcopy(measurement)
    return value


def _seal(value):
    value["digest"] = digest({key: item for key, item in value.items() if key != "digest"})


def _record(cycle, *, revisions=None):
    return route_failure_record(
        cycle=cycle, mechanism_id="M", family_id="F",
        observation={"route": "tracer_directive", "status": "insufficient_for_mechanism",
                     "evidence": {"measurement_identity": "same-artifact"}},
        revisions_observed=revisions,
    )


def test_cycle_numbers_do_not_prove_independent_measurement():
    entry = route_failure_state([_record(1), _record(2)], runtime_identity=None)[("M", "tracer_directive")]
    assert entry["observations"] == 0
    assert entry["insufficient"] is False


def test_numeric_revision_count_does_not_prove_measured_revision():
    entry = route_failure_state([_record(1, revisions=1)], runtime_identity=None)[("M", "tracer_directive")]
    assert entry["insufficient"] is False


def test_unidentified_history_is_diagnostic_only():
    entry = route_failure_state([_record(1)], runtime_identity=None)[("M", "tracer_directive")]
    assert entry["observations"] == 0
    assert entry["diagnostic_observations"] == 1


def test_same_measured_artifact_across_cycles_counts_once():
    *_, runtime, registries, rows, measurement = measured_fixture()
    records = [_measured_record(cycle, measurement) for cycle in (1, 2)]
    entry = route_failure_state(records, runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 1
    assert entry["diagnostic_observations"] == 1
    assert entry["insufficient"] is False


def test_two_independent_invocations_in_same_context_count_twice():
    _, runtime, _, _, first = measured_fixture()
    second = measured_fixture()[-1]
    entry = route_failure_state([_measured_record(1, first), _measured_record(2, second)],
                                runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 2
    assert entry["insufficient"] is True


def test_partial_reuse_of_candidate_sources_is_not_independent():
    _, runtime, _, _, first = measured_fixture(cases=("a", "b"))
    second = measured_fixture(cases=("a", "b"))[-1]
    second["arms"]["candidate"][0]["measurement_sources"] = first["arms"]["candidate"][0]["measurement_sources"]
    _seal(second)
    entry = route_failure_state([_measured_record(1, first), _measured_record(2, second)],
                                runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 1
    assert entry["insufficient"] is False


def test_resolution_does_not_make_old_measurements_fresh():
    _, runtime, _, _, measurement = measured_fixture()
    resolution = {"kind": "resolution", "mechanism_id": "M", "route": "tracer_directive", "cycle": 2}
    entry = route_failure_state([_measured_record(1, measurement), resolution, _measured_record(3, measurement)],
                                runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 0
    assert entry["diagnostic_observations"] == 1


@pytest.mark.parametrize("fault", ["missing_scope", "changed_scope", "missing_pair", "registry", "duplicate_leaf", "source"])
def test_invalid_or_inapplicable_provenance_never_closes_a_route(fault):
    _, runtime, _, _, measurement = measured_fixture(cases=("a", "b"))
    if fault == "missing_scope":
        runtime = None
    elif fault == "changed_scope":
        runtime = {**runtime, "effective_config": digest({"changed": True})}
        _seal(runtime)
    elif fault == "missing_pair":
        measurement["arms"]["control"].pop()
        _seal(measurement)
    elif fault == "registry":
        measurement["registries"]["candidate"] = digest({"different": True})
        _seal(measurement)
    elif fault == "duplicate_leaf":
        measurement["arms"]["candidate"][1]["measurement_sources"] = measurement["arms"]["candidate"][0]["measurement_sources"]
        _seal(measurement)
    else:
        inputs = measurement["arms"]["candidate"][0]["inputs"]
        inputs["source"] = digest({"other_source": True})
        _seal(inputs)
        _seal(measurement)
    entry = route_failure_state([_measured_record(1, measurement), _measured_record(2, measurement)],
                                runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 0
    assert entry["insufficient"] is False


@pytest.mark.parametrize("fault", ["prediction", "frozen_target", "missing_measurement", "different_registry", "changed_candidate"])
def test_producer_rejects_unbound_rows_before_projection(fault):
    candidate, runtime, registries, rows, _ = measured_fixture()
    row = rows["candidate"][0]
    if fault == "prediction":
        row["prediction"] = True
    elif fault == "frozen_target":
        row["result"]["provenance"]["frozen_stages"] = ["tracer_result"]
    elif fault == "missing_measurement":
        row["result"]["execution"].pop("measurement_id")
    elif fault == "different_registry":
        inputs = row["result"]["provenance"]["execution_identity"]
        inputs["registry"] = digest({"other_program": True})
        _seal(inputs)
    else:
        candidate["body"]["directive"] = "changed after evaluation"
    assert build_route_measurement(
        candidate=candidate, partners=[], cases=["a"], control_rows=rows["control"], candidate_rows=rows["candidate"],
        runtime=runtime, control_registry=registries["control"], candidate_registry=registries["candidate"],
    ) is None


def test_measurement_cannot_be_attached_to_another_program():
    candidate, _, _, _, measurement = measured_fixture()
    changed = deepcopy(candidate)
    changed["body"]["directive"] = "another program"
    assert measurement_for_candidate(measurement, changed) is None
    assert measurement_for_candidate(measurement, candidate) == measurement


def test_same_cycle_independent_measurements_survive_store_and_resume(tmp_path):
    from evolution.route_failures import record_route_failures, load_route_failures

    _, runtime, _, _, first = measured_fixture()
    second = measured_fixture()[-1]
    observations = [{"mechanism_id": "M", "family_id": "F", "observation": {
        "route": "tracer_directive", "status": "insufficient_for_mechanism",
        "measurement": measurement, "evidence": {},
    }} for measurement in (first, second)]
    path = tmp_path / "route_failures.jsonl"
    assert len(record_route_failures(path, cycle=1, observations=observations)) == 2
    original = path.read_bytes()
    assert record_route_failures(path, cycle=1, observations=observations) == []
    assert path.read_bytes() == original
    entry = route_failure_state(load_route_failures(path), runtime_identity=runtime)[("M", "tracer_directive")]
    assert entry["observations"] == 2
    assert entry["insufficient"] is True


def test_capability_planner_requires_applicable_runtime_for_route_exclusion():
    from evolution.capability_planning import analyze_capability_requirement
    from evolution.tests.test_capability_planning import mechanism

    _, runtime, _, _, first = measured_fixture()
    records = [_measured_record(1, first), _measured_record(2, measured_fixture()[-1])]
    required = mechanism(
        mechanism_id="M", obligation="unspecified", deviation="mislocation",
        operation="constrain_localization", stage="step_tracer",
        failure_mode="unhostable_step", objective="localization",
    )
    unbound = analyze_capability_requirement(required, routes=["tracer_directive"], route_failure_records=records)
    assert unbound["sufficient_routes"] == ["tracer_directive"]
    bound = analyze_capability_requirement(required, routes=["tracer_directive"],
                                          route_failure_records=records, runtime_identity=runtime)
    assert bound["sufficient_routes"] == []


def test_actual_engine_unit_preserves_lineage_before_row_summary(tmp_path):
    from evolution.backends import DetectionBackend
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_engine_smoke import _config, _make_dataset

    class ControlledBackend(DetectionBackend):
        name = "controlled"
        detector = SimpleNamespace(config={})

    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
                           patrol_pool=cases[12:], mini=True)
    predictions = {case: i % 2 == 0 and i != 4 for i, case in enumerate(cases[:12])}
    candidate, _, _, rows, _ = measured_fixture(cases=cases[:12], predictions=predictions)
    for arm in rows.values():
        for row in arm:
            row["gold"] = int(row["case"].split("/")[-1][:-5]) % 2 == 0
    for row in rows["candidate"]:
        row["result"]["proof"]["deployment"] = {"fired": [
            {"intervention_id": candidate["intervention_id"], "event": "text_injected"}
        ]}
    class Arms:
        def run(self, requested, **kwargs):
            return deepcopy(rows["candidate"])

    with EvolutionEngine(_config(tmp_path), backend=ControlledBackend(), split=split, mini=True) as engine:
        unit = engine._run_unit(candidate, partners=[], active_entries=[], active_rows=rows["control"], arms=Arms())
        assert unit["route_measurement"] is not None
        assert len(route_measurement_sources(unit["route_measurement"])) == len(split.gain)
        assert all("result" not in row for row in unit["candidate_rows"])
        family = [{"failure_family": "FAM-fn-x", "mechanism": {"mechanism_id": "M"}}]
        for cycle in (1, 2):
            recorded = engine.record_route_failures(cycle=cycle, diagnosed_families=family, gate_reports=[unit])
            assert recorded["observed"] == 1
        assert recorded["state"]["insufficient"] == []
        assert recorded["state"]["observed_only"][0]["observations"] == 1
