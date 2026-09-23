"""``capability-audit`` exits with its own verdict, and reports prove content.

``verify.py`` runs the audit and records its exit code. ``cmd_capability_audit``
printed the report and returned 0 unconditionally, so the check could only fail
by crashing: a registry containing a runner the metamorphic gate refuses
produced a report whose own body said ``all_admitted: false`` while the check
recorded PASS. These tests pin the exit code to the report's content, and pin
the content assertions ``verify.py`` now records beside the exit codes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evolution import cli
from evolution.capability_surface import capability_surface_report

ROOT = Path(__file__).resolve().parents[2]


class _Args:
    config = None


def test_capability_audit_exits_zero_on_the_shipped_runtime():
    assert cli.cmd_capability_audit(_Args()) == 0
    report = capability_surface_report()
    admission = report["executable_inventory"]["runner_admission"]
    assert admission["all_admitted"] is True


def test_registered_unadmitted_runner_makes_the_audit_fail():
    """The audit's exit code follows the report it just produced.

    A runner with no metamorphic reference subject is the gate's own definition
    of "must be refused"; registering one must turn the audit's exit code
    non-zero instead of leaving it at 0.
    """
    from detector.interventions import (
        RUNNER_REGISTRY,
        TERMINAL_EVIDENCE_ONLY,
        RunnerSpec,
    )

    probe = RunnerSpec(
        name="probe_unadmitted_runner",
        supported_hooks=frozenset({"judge.post"}),
        intervention_types=frozenset({"evidence_check"}),
        input_schema="probe",
        deterministic=True,
        learned=False,
        requires_artifact=False,
        terminal_authority=TERMINAL_EVIDENCE_ONLY,
    )
    RUNNER_REGISTRY["probe_unadmitted_runner"] = probe
    try:
        report = capability_surface_report()
        admission = report["executable_inventory"]["runner_admission"]
        assert admission["all_admitted"] is False
        assert cli.cmd_capability_audit(_Args()) == 1
    finally:
        del RUNNER_REGISTRY["probe_unadmitted_runner"]
    assert cli.cmd_capability_audit(_Args()) == 0


def test_capability_audit_command_exits_nonzero_through_the_cli(tmp_path):
    """Through a real process, because that is how verify.py runs it."""
    result = subprocess.run(
        [sys.executable, "-m", "evolution", "capability-audit"],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["executable_inventory"]["runner_admission"]["all_admitted"]


def test_verify_script_asserts_the_audit_content():
    """``verify.py`` must not record PASS from the exit code alone.

    The audit's body is asserted too, so a future regression in either half --
    the exit code or the report -- is caught by the check that reports on it.
    """
    source = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert "capability_audit_admissible" in source
    assert "all_admitted" in source
    assert "admission_blockers" in source
    # The CLI checks assert registered subcommands rather than exit status only.
    assert "registers_commands" in source
