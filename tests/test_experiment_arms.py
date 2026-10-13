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
    assert CONFIG["causal_audit"] == {"mode": "off"}
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



def test_paired_unknown_arm_is_rejected_even_without_measurements(tmp_path):
    from experiments.runner import paired_sample
    with pytest.raises(ValueError, match="arm"):
        paired_sample(load_plan(), tmp_path / "runs", ["unknown", "g0"])
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("arms", [["g0"], ["g0", "g0"], ["g0", "g1", "g0_roll2"]])
def test_paired_cli_requires_two_distinct_arms_before_reading(tmp_path, arms):
    from experiments.__main__ import main
    arguments = ["paired", "--runs-root", str(tmp_path / "runs")]
    for arm in arms:
        arguments.extend(["--arm", arm])
    with pytest.raises(SystemExit) as failure:
        main(arguments)
    assert failure.value.code == 2
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("count", [-1, 0, 1])
def test_baseline_cli_prefix_is_validated_before_batch(tmp_path, monkeypatch, count):
    from experiments.__main__ import main
    plan = load_plan()
    monkeypatch.setattr("experiments.__main__.load_plan", lambda **kwargs: plan)
    seen = []
    from types import SimpleNamespace
    from experiments.report import StageReport
    report = StageReport(stage="baseline", arm="g0", requested=[], rows=[]).to_dict()
    report["measurement_prefix"] = {"size": 0}
    monkeypatch.setattr("experiments.__main__.run_measurement_stage",
                        lambda **kwargs: seen.append(kwargs) or SimpleNamespace(report=report))
    arguments = ["baseline", "--cases", str(count), "--runs-root", str(tmp_path / "runs")]
    if count < 0:
        with pytest.raises(SystemExit) as failure:
            main(arguments)
        assert failure.value.code == 2
        assert not seen
    else:
        assert main(arguments) == 0
        assert seen[0]["cases"] == plan.measurement.cases[:count]
