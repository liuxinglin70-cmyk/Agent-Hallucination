"""Offline policy for every test domain and inherited Python subprocess."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.offline_guard import sitecustomize  # noqa: F401

ROOT = Path(__file__).resolve().parent
guard_path = str(ROOT / "scripts" / "offline_guard")
paths = [guard_path, str(ROOT), *os.environ.get("PYTHONPATH", "").split(os.pathsep)]
os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if p))

# These modules read the local corpus or frozen evidence; synthetic tests in
# mixed modules are marked individually at their definitions.
LOCAL_DATA_MODULES = {
    "tests/test_baseline_693_dryrun.py",
    "tests/test_real_experiment_instruments.py",
    "tests/test_preflight_real_run.py",
    "tests/test_cost_model_693.py",
    "evolution/tests/test_localization_conflict_and_split.py",
}
PLATFORM_MODULES = {
    "tests/test_cli_integration.py",
    "evolution/tests/test_sandbox.py",
    "evolution/tests/test_capability_audit_exit.py",
}


@pytest.fixture(autouse=True)
def code_only_data_boundary(request, monkeypatch):
    if request.node.get_closest_marker("code_only"):
        monkeypatch.setenv("CODE_ONLY_VERIFICATION", "1")
    else:
        monkeypatch.delenv("CODE_ONLY_VERIFICATION", raising=False)


def pytest_collection_modifyitems(items):
    for item in items:
        path = Path(item.path).relative_to(ROOT).as_posix()
        if path in LOCAL_DATA_MODULES:
            item.add_marker(pytest.mark.local_data)
        elif path in PLATFORM_MODULES:
            item.add_marker(pytest.mark.platform)
        elif not any(item.get_closest_marker(name) for name in ("local_data", "platform")):
            item.add_marker(pytest.mark.code_only)
