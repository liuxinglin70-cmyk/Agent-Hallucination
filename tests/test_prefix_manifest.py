import json

import pytest

from detector.identity import digest
from experiments.plan import load_measurement
from scripts.freeze_measured_prefix import main
from scripts import freeze_measured_prefix
from tests.test_effect_reading import readings


def test_exported_prefix_is_a_loadable_bound_measurement(readings, tmp_path, capsys, monkeypatch):
    cases, data, _, plan, measure = readings
    root = measure("g0", [False] * 6)
    output = tmp_path / "prefix.json"
    monkeypatch.setattr(freeze_measured_prefix, "DEFAULT_DATASET_ROOT", data, raising=False)
    assert main(["--baseline", str(root), "--order", str(plan.measurement.source),
                 "--out", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    selected = load_measurement(output)
    assert selected.cases == cases
    assert selected.gold_positive == 0 and selected.gold_negative == 6
    assert selected.parent_manifest_digest == plan.measurement.parent_manifest_digest
    assert selected.manifest_digest == digest(payload)
    assert selected.manifest_digest != plan.measurement.manifest_digest


@pytest.mark.parametrize("boundary", ["empty", "existing", "inside_run", "negative_min"])
def test_freeze_rejects_invalid_or_mutating_output(readings, tmp_path, boundary, monkeypatch):
    _, data, _, plan, measure = readings
    root = measure("g0", [False] * 6)
    monkeypatch.setattr(freeze_measured_prefix, "DEFAULT_DATASET_ROOT", data, raising=False)
    output = tmp_path / "prefix.json"
    if boundary == "existing":
        output.write_text("existing evidence", encoding="utf-8")
    elif boundary == "inside_run":
        output = root / "new-prefix.json"
    elif boundary == "empty":
        for path in (root / "cases").glob("*/result.json"):
            path.unlink()
    before = output.read_bytes() if output.exists() else None
    args = ["--baseline", str(root), "--order", str(plan.measurement.source),
            "--out", str(output)]
    if boundary == "negative_min":
        args += ["--min-cases", "-1"]
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert (output.read_bytes() if output.exists() else None) == before
