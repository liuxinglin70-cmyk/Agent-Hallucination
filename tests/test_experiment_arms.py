from dataclasses import replace

import pytest

from experiments.plan import load_plan
from experiments.runner import run_measurement_stage


def stage(tmp_path, monkeypatch, plan, arm, **kwargs):
    seen = []
    def batch(**options):
        seen.append(options)
        return {}
    monkeypatch.setattr("experiments.runner.run_batch", batch)
    outcome = run_measurement_stage(plan=plan, stage="baseline", arm=arm, cases=[],
                                    runs_root=tmp_path / "runs", run_id="test", dataset_root=tmp_path / "data",
                                    **kwargs)
    return outcome, seen


@pytest.mark.parametrize("arm", ["g0", "g0_roll2"])
def test_declared_empty_arm_overrides_default_registry(tmp_path, monkeypatch, arm):
    outcome, seen = stage(tmp_path, monkeypatch, load_plan(), arm)
    assert seen[0]["interventions"] is not None
    assert seen[0]["interventions"]["interventions"] == []
    assert outcome.report["arm_role"] in {"baseline", "noise_floor"}


def test_unknown_arm_is_refused_before_batch(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="arm"):
        stage(tmp_path, monkeypatch, load_plan(), "unknown", registry={"interventions": []})


def test_empty_arm_rejects_supplied_active_registry(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="empty"):
        stage(tmp_path, monkeypatch, load_plan(), "g0", registry={"interventions": [{"unexpected": True}]})


def test_arm_policy_reaches_effective_detector_config_without_mutating_plan(tmp_path, monkeypatch):
    from detector.tests.test_api import CONFIG
    plan = load_plan()
    plan = replace(plan, arms={**plan.arms, "g0": {"role": "baseline", "registry": "empty",
        "detector_policy": {"causal_audit": {"mode": "shadow", "top_k": 7}}}})
    _, seen = stage(tmp_path, monkeypatch, plan, "g0", config=CONFIG)
    assert seen[0]["config"]["causal_audit"]["mode"] == "shadow"
    assert seen[0]["config"]["causal_audit"]["top_k"] == 7
    assert "causal_audit" not in CONFIG
    assert plan.arms["g0"]["detector_policy"]["causal_audit"] == {"mode": "shadow", "top_k": 7}


@pytest.mark.parametrize("explicit_run_id", [False, True])
def test_default_cli_resolves_g0_with_offline_batch(tmp_path, monkeypatch, capsys, explicit_run_id):
    from experiments.__main__ import main
    plan = load_plan()
    plan = replace(plan, measurement=replace(plan.measurement, cases=[]))
    monkeypatch.setattr("experiments.__main__.load_plan", lambda **kwargs: plan)
    seen = []
    monkeypatch.setattr("experiments.runner.run_batch", lambda **kwargs: seen.append(kwargs) or {})
    arguments = ["baseline", "--runs-root", str(tmp_path / "runs")]
    if explicit_run_id:
        arguments.extend(["--run-id", "test"])
    assert main(arguments) == 0
    assert seen[0]["interventions"]["interventions"] == []
    assert '"arm": "g0"' in capsys.readouterr().out
