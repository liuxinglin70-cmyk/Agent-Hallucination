from copy import deepcopy
import json

import pytest

from detector.action_forecast import build_action_forecast
from detector.config import validate_config
from detector.identity import build_execution_identity, digest, source_identity
from detector.interventions import authority_identity
from detector.packet import build_packet
from detector.runner import RUN_SCHEMA, _summarize, record_case
from detector.runtime import added_budget_scope
from detector.tests.test_runner import _envelope
from detector.tests.test_voting_hearings import CONFIG, JUDGE_FALSE
from scripts.check_detector_smoke import check_smoke


@pytest.fixture
def smoke_run(tmp_path, monkeypatch):
    def make(*, change=None, forecast=False, unaligned=False):
        config = validate_config({**CONFIG, "causal_audit": {
            "mode": "tracer_context" if forecast else "off",
            "next_action_mode": "judge_context" if forecast else "off",
        }}, require_credentials=False)
        record = {"question": [["Compute 2 + 2"], ["Report the result"]] if unaligned else "Compute 2 + 2",
                  "history": [{"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expression": "2+2"}}],
                               "tool_responses": [{"result": 4}]},
                              {"role": "assistant", "content": "Successfully completed: the result is 4."}]}
        data = tmp_path / "data"
        data.mkdir()
        (data / "a.json").write_text(json.dumps(record), encoding="utf-8")
        packet = build_packet(record)
        result = _envelope("a.json")
        result["proof"] = {k: result[k] for k in ("state", "decision_authority", "runtime_valid")}
        result["proof"]["judge"] = deepcopy(JUDGE_FALSE)
        result["stage_status"] = {"judge": {"state": "ok"}}
        result["provenance"] = {"execution_identity": build_execution_identity(
            source=source_identity("detector"), config=config, record=record, packet=packet,
            authority=authority_identity(None))}
        if forecast:
            def predict(**kwargs):
                return {"constraints": [{"requirement": "Compute 2 + 2", "allowed_actions": ["calculate"],
                    "forbidden_actions": ["report an incorrect result"], "evidence_ref": "question", "quote": "Compute 2 + 2",
                    "source_type": "user_request", "applies_to": "final_delivery", "time_constraint": "before_delivery"}]}, {"attempts": 1}
            monkeypatch.setattr("detector.action_forecast.provider_call", predict)
            with added_budget_scope(config["causal_audit"]):
                result["proof"]["action_forecast"] = build_action_forecast(packet, config=config)
            result["proof"]["action_forecast_used"] = not unaligned
            result["execution"] = {"requests": [] if unaligned else [{"purpose": "action_forecast", "status": "response"}]}
        if change:
            change(result)
        run_dir = tmp_path / "runs" / "smoke"
        record_case(runs_root=run_dir.parent, run_id=run_dir.name, case_id="a.json", input_record=record,
                    result=result, elapsed_seconds=1, attempt_id="001")
        committed = json.loads((run_dir / "cases/a.json/result.json").read_text(encoding="utf-8"))
        manifest = {"schema_version": RUN_SCHEMA, "run_id": "smoke", "cases": ["a.json"],
                    "case_set_digest": digest({"cases": ["a.json"]}), "dataset_root": str(data),
                    "config_hash": digest(config), "source_identity": source_identity("detector"),
                    "execution_identities": {"a.json": result["provenance"]["execution_identity"]}}
        summary = _summarize(run_id="smoke", cases=["a.json"], rows=[{"case": "a.json", "result": committed}],
                             manifest=manifest, stop_reason={})
        (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
        (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return run_dir, config
    return make


def check(run_dir, config):
    return check_smoke(run_dir, cases=["a.json"], config=config)


def test_healthy_committed_smoke_is_ready_without_reading_gold(smoke_run):
    directory, config = smoke_run()
    raw = directory.parent.parent / "data/a.json"
    value = json.loads(raw.read_text(encoding="utf-8"))
    value.update(is_hallucination=True, hallucination_step=999, true_answer="unused gold")
    raw.write_text(json.dumps(value), encoding="utf-8")
    assert check(directory, config)["ready"] is True


def test_returned_degraded_case_does_not_admit_full_run(smoke_run):
    def degraded(result):
        result["decision_authority"] = result["proof"]["decision_authority"] = "degraded_judge_only"
    directory, config = smoke_run(change=degraded)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed"] == summary["measurement"]["returned"] == 1 and summary["stop"] == {}
    report = check(directory, config)
    assert report["ready"] is False
    assert "case is not authoritative" in report["cases"][0]["errors"]


@pytest.mark.parametrize("change,detail", [
    (lambda r: r["stage_status"].update(defense={"state": "no_quorum"}), "stage defense is not healthy"),
    (lambda r: r["providers"].update(defense={"vote": {"quorum_met": False}}), "stage defense lacks quorum"),
    (lambda r: r["proof"].update(state="verified_error"), "proof and envelope disagree"),
])
def test_authoritative_label_does_not_hide_failed_contracts(smoke_run, change, detail):
    directory, config = smoke_run(change=change)
    report = check(directory, config)
    assert report["ready"] is False
    assert detail in report["cases"][0]["errors"]


@pytest.mark.parametrize("field", ["source_identity", "config_hash", "cases"])
def test_stale_or_different_launch_inputs_are_rejected(smoke_run, field):
    directory, config = smoke_run()
    path = directory / "run.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value[field] = ["different.json"] if field == "cases" else "different"
    path.write_text(json.dumps(value), encoding="utf-8")
    report = check(directory, config)
    assert report["ready"] is False
    assert f"manifest {field} differs from launch inputs" in report["errors"]


def test_modified_committed_result_is_rejected(smoke_run):
    directory, config = smoke_run()
    path = directory / "cases/a.json/result.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["proof"]["judge"]["delivered_result"] = "tampered"
    path.write_text(json.dumps(value), encoding="utf-8")
    report = check(directory, config)
    assert report["ready"] is False
    assert "digest" in " ".join(report["cases"][0]["errors"])


def test_positive_smoke_cannot_omit_required_hearing_stages(smoke_run):
    def positive(result):
        result["state"] = result["occurrence_state"] = result["proof"]["state"] = "verified_error"
    directory, config = smoke_run(change=positive)
    report = check(directory, config)
    assert report["ready"] is False
    assert "required stage defense is not healthy" in report["cases"][0]["errors"]


def test_enabled_smoke_requires_a_successful_model_forecast(smoke_run):
    directory, config = smoke_run(forecast=True)
    report = check(directory, config)
    assert report["ready"] is True, report
    assert report["model_predictions"] > 0 and report["forecast_responses"] == 1


def test_only_unaligned_cases_cannot_validate_forecast_transport(smoke_run):
    directory, config = smoke_run(forecast=True, unaligned=True)
    report = check(directory, config)
    assert report["cases"][0]["ready"] is True, report
    assert report["ready"] is False
    assert "include an aligned case" in report["errors"][0]
