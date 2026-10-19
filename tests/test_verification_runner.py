"""Parallel verification must retain every case and fail closed on incomplete runs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import verify

pytestmark = pytest.mark.platform
ROOT = Path(__file__).resolve().parents[1]


def run_example(tmp_path, source, workers):
    test = tmp_path / "test_example.py"
    test.write_text(source, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    report = tmp_path / "execution.json"
    arguments = [sys.executable, "-m", "pytest", "-q", "-c", str(config),
                 "-p", "no:cacheprovider", "-p", "scripts.verification_plugin",
                 f"--verification-report={report}", str(test)]
    if workers > 1:
        arguments.extend(["-n", str(workers), "--dist=worksteal", "--max-worker-restart=0"])
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True,
                            text=True, encoding="utf-8", timeout=40)
    assert report.is_file(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize("workers", [1, 2])
def test_serial_and_parallel_cover_the_same_cases_once(tmp_path, workers):
    result, report = run_example(tmp_path, "def test_one(): pass\ndef test_two(): pass\n", workers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["passed"] is True
    assert report["collected_count"] == report["executed_count"] == 2
    assert set(report["collected"]) == set(report["executed"])


@pytest.mark.parametrize("source", [
    "import pytest\ndef test_skipped(): pytest.skip('controlled skip')\n",
    "import pytest\n@pytest.fixture\ndef bad(): raise RuntimeError('controlled setup failure')\ndef test_bad(bad): pass\n",
])
def test_incomplete_test_is_not_a_verification_pass(tmp_path, source):
    _, report = run_example(tmp_path, source, 2)
    assert report["passed"] is False
    assert report["skipped"] or report["missing"]


def test_crashed_worker_cannot_omit_a_case_and_pass(tmp_path):
    result, report = run_example(tmp_path, "import os\ndef test_crash(): os._exit(6)\n", 2)
    assert result.returncode != 0
    assert report["passed"] is False
    assert report["missing"]


def test_parallel_request_requires_dependency_and_positive_count(monkeypatch):
    monkeypatch.setattr(verify, "find_spec", lambda name: None)
    assert verify.worker_count(None) == verify.worker_count(1) == 1
    with pytest.raises(ValueError, match="test extra"):
        verify.worker_count(2)
    with pytest.raises(ValueError, match="positive"):
        verify.worker_count(0)
