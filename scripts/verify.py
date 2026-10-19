"""Reproduce offline checks and bind their reports to the current source."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.util import find_spec
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUT: Path


def save(name: str, payload: object) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_manifest() -> dict[str, str]:
    files = [ROOT / "pyproject.toml", ROOT / "datasets/integrity.json"]
    for folder in ("detector", "evolution", "experiments", "tests", "scripts", "datasets/protocols"):
        files.extend(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and p.suffix in {".py", ".json"})
    files.extend([ROOT / "conftest.py"])
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(files)) if p.is_file()}


def worker_count(requested: int | None) -> int:
    if requested is not None and requested < 1:
        raise ValueError("--jobs must be a positive integer")
    available = find_spec("xdist") is not None
    if requested is not None and requested > 1 and not available:
        raise ValueError("parallel verification requires the test extra: pip install -e .[test]")
    return requested or (min(4, max(1, (os.cpu_count() or 1) - 1)) if available else 1)


def pytest_arguments(scope: str, output: Path, jobs: int) -> list[str]:
    arguments = ["-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "-p", "scripts.verification_plugin", "--durations=20",
                 f"--verification-report={output / 'test_execution.json'}",
                 f"--junitxml={output / 'pytest.xml'}"]
    if jobs > 1:
        arguments.extend(["-n", str(jobs), "--dist=worksteal", "--max-worker-restart=0"])
    if scope != "all":
        arguments.extend(["-m", scope])
    return arguments


def main(argv=None) -> int:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("code_only", "local_data", "platform", "all"), default="code_only")
    parser.add_argument("--jobs", type=int, help="pytest worker processes; 1 runs serially; default reserves one CPU")
    args = parser.parse_args(argv)
    try:
        jobs = worker_count(args.jobs)
    except ValueError as exc:
        parser.error(str(exc))
    started = datetime.now(timezone.utc).isoformat()
    initial = source_manifest()
    identity = hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest()
    OUT = ROOT / "artifacts" / "code_quality" / identity / uuid4().hex
    OUT.mkdir(parents=True, exist_ok=False)
    print(f"report: {OUT}", flush=True)
    sys.path.insert(0, str(ROOT))
    from scripts.source_audit import audit_sources
    save("summary.json", {"passed": False, "status": "running", "started_at_utc": started})
    (OUT / "README.md").write_text("# 代码验证结果\n\n验证正在运行，结果尚未完成。\n", encoding="utf-8")
    (OUT / "pytest.xml").write_text("<testsuites />\n", encoding="utf-8")
    for pending in ("capability_audit.json", "metamorphic_gate.json", "sandbox_check.json",
                    "smoke.json", "capability_smoke.json"):
        save(pending, {"status": "not_run"})
    checks = []
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    # Verification owns its test selection and distribution; user-local pytest
    # options must not silently filter a full run or select a different scheduler.
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONPATH"] = os.pathsep.join([
        str(ROOT / "scripts/offline_guard"), str(ROOT), environment.get("PYTHONPATH", "")])
    if args.scope == "code_only":
        environment["CODE_ONLY_VERIFICATION"] = "1"
    else:
        environment.pop("CODE_ONLY_VERIFICATION", None)

    def run(name: str, arguments: list[str]) -> bool:
        result = run_capture(name, arguments)
        return result.returncode == 0

    def run_capture(name: str, arguments: list[str]):
        before = time.monotonic()
        log_path = OUT / f"{name}.log"
        print(f"{name}: RUNNING (log: {log_path})", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment,
                                    text=True, encoding="utf-8", errors="replace",
                                    stdout=log, stderr=subprocess.STDOUT)
        result.stdout = log_path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": name, "arguments": arguments,
                       "exit_code": result.returncode,
                       "seconds": round(time.monotonic() - before, 3)})
        print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        return result

    def record(name: str, passed: bool, detail: str = "") -> None:
        """A content assertion, recorded beside the subprocess exit codes.

        The exit code alone cannot carry every verdict: ``--help`` exits 0 for
        a CLI whose commands have all been unregistered, and a report command
        that exits 0 while its own body says the thing under test is not
        admissible is the check that cannot fail. Checks that make a claim
        about *content* record it here, under its own name.
        """
        checks.append({"name": name, "exit_code": 0 if passed else 1,
                       **({"detail": detail} if detail else {})})
        print(f"{name}: {'PASS' if passed else 'FAIL'}", flush=True)

    static = audit_sources(ROOT)
    save("source_audit.json", static)
    record("source_audit", static["passed"])
    integrity = None
    if args.scope in {"local_data", "all"}:
        manifest = json.loads((ROOT / "datasets/integrity.json").read_text(encoding="utf-8"))
        raw = ROOT / manifest["root"]
        actual = {p.relative_to(raw).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(raw.rglob("*")) if p.is_file()}
        expected = manifest["files"]
        integrity = {
            "passed": actual == expected,
            "file_count": len(actual), "case_count": manifest["case_count"],
            "sources": manifest["sources"],
            "missing": sorted(expected.keys() - actual.keys()),
            "added": sorted(actual.keys() - expected.keys()),
            "changed": sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k]),
        }
        save("dataset_integrity.json", integrity)
        checks.append({"name": "dataset_integrity", "exit_code": 0 if integrity["passed"] else 1})
        print(f"dataset_integrity: {'PASS' if integrity['passed'] else 'FAIL'}", flush=True)
    print(f"pytest workers: {jobs}", flush=True)
    run("pytest", pytest_arguments(args.scope, OUT, jobs))
    execution_path = OUT / "test_execution.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8")) if execution_path.exists() else {}
    record("test_execution_complete", execution.get("passed") is True,
           "collection and executed test ids must match exactly, without skips or duplicate executions")
    # Importability is not coverage: ``--help`` exits 0 for a CLI whose
    # commands the parser no longer registers. Assert the subcommands the
    # repository's own docs and scripts invoke are actually there.
    for name, arguments, expected_commands in (
        ("detector_cli", ["-m", "detector", "--help"],
         ("detect", "batch", "evaluate")),
        ("evolution_cli", ["-m", "evolution", "--help"],
         ("cycle", "smoke", "capability-audit", "metamorphic-gate",
          "sandbox-check", "capability-smoke")),
        ("experiments_cli", ["-m", "experiments", "--help"],
         ("plan", "status", "baseline", "paired")),
    ):
        result = run_capture(name, arguments)
        missing = [c for c in expected_commands if c not in result.stdout]
        record(f"{name}_registers_commands", result.returncode == 0 and not missing,
               f"missing subcommand(s): {missing}" if missing else "")
    if args.scope in {"local_data", "all"}:
        result = run_capture("capability_audit", ["-m", "evolution", "capability-audit"])
        # The exit code is the report's verdict (cmd_capability_audit), and the
        # body is asserted too: a report whose own content says an executor is not
        # admissible must not be recorded as a pass, whichever half regresses.
        if result.returncode == 0:
            audit_report = json.loads((OUT / "capability_audit.log").read_text(encoding="utf-8"))
            save("capability_audit.json", audit_report)
            inventory = audit_report.get("executable_inventory") or {}
            admitted = bool((inventory.get("runner_admission") or {}).get("all_admitted"))
            blockers = list((inventory.get("sandbox") or {}).get("admission_blockers") or ())
            record("capability_audit_admissible", admitted and not blockers,
                   f"all_admitted={admitted}, sandbox_blockers={blockers}")
        if run("metamorphic_gate", ["-m", "evolution", "metamorphic-gate"]):
            save("metamorphic_gate.json", json.loads((OUT / "metamorphic_gate.log").read_text(encoding="utf-8")))
        if run("sandbox_check", ["-m", "evolution", "sandbox-check"]):
            save("sandbox_check.json", json.loads((OUT / "sandbox_check.log").read_text(encoding="utf-8")))

        # Both smoke commands use mock backends. Their writable roots are verified
        # descendants of a fresh temporary directory, never a live registry or raw data.
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="offline_verification_", dir=artifacts) as temporary:
            temp = Path(temporary).resolve()
            assert temp.is_relative_to(artifacts.resolve()) and temp != artifacts.resolve()
            config = json.loads((ROOT / "evolution/configs/development.json").read_text(encoding="utf-8"))
            config.update({"work_root": str(temp / "work"), "registry_dir": str(temp / "registry"),
                           "ledger_path": str(temp / "ledger.jsonl"),
                           "harness_dir": str(temp / "harness"), "memory_root": str(temp / "memory")})
            config_path = temp / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            for command, source, target in (
                ("smoke", "smoke/experiments/smoke_cycle.json", "smoke.json"),
                ("capability-smoke", "capability_smoke/capability_smoke_report.json", "capability_smoke.json"),
            ):
                if run(command.replace("-", "_"), ["-m", "evolution", command, "--config", str(config_path)]):
                    report = json.loads((temp / "work" / source).read_text(encoding="utf-8"))
                    save(target, report)
                    if command == "smoke":
                        admitted = report.get("marginal_selection", {}).get("selected") or []
                        audit_passed = bool((report.get("audit") or {}).get("passed"))
                        checks.append({"name": "smoke_admission_and_audit", "exit_code":
                                       0 if admitted and audit_passed and report.get("state") == "completed" else 1})
                        # The smoke config leaves the development loop off, so the
                        # cycle must report the single-shot path. This is the
                        # guarantee the wiring was required to keep: an offline
                        # cycle that never opted into the loop takes the same path
                        # it took before the loop could be reached from one.
                        path_ok = report.get("round_path") == "single_shot"
                        checks.append({"name": "smoke_round_path_single_shot", "exit_code":
                                       0 if path_ok else 1})

    test_totals = Counter()
    xml_path = OUT / "pytest.xml"
    if xml_path.exists():
        for suite in ET.parse(xml_path).getroot().iter("testsuite"):
            for key in ("tests", "failures", "errors", "skipped"):
                test_totals[key] += int(suite.get(key, 0))
    test_totals["passed"] = test_totals["tests"] - sum(test_totals[k] for k in ("failures", "errors", "skipped"))
    final = source_manifest()
    checks.append({"name": "source_unchanged_during_checks", "exit_code": 0 if initial == final else 1})
    digest = hashlib.sha256(json.dumps(final, sort_keys=True).encode()).hexdigest()
    save("source_manifest.json", {"sha256": digest, "files": final})
    passed = all(c["exit_code"] == 0 for c in checks)
    summary = {"started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "platform": platform.platform(),
               "scope": args.scope, "network_policy": "loopback_only",
               "pytest_workers": jobs, "pytest_distribution": "worksteal" if jobs > 1 else "serial",
               "not_run": [name for name in ("code_only", "local_data", "platform")
                           if args.scope not in {name, "all"}],
               "source_sha256": digest, "passed": passed, "tests": dict(test_totals), "checks": checks}
    save("summary.json", summary)
    rows = "\n".join(f"| {c['name']} | {'PASS' if c['exit_code'] == 0 else 'FAIL'} |" for c in checks)
    (OUT / "README.md").write_text(
        f"# 代码验证结果\n\n生成时间（UTC）：{started}\n\n"
        f"总状态：**{'PASS' if passed else 'FAIL'}**。pytest：{test_totals['passed']} 通过，"
        f"{test_totals['failures']} 失败，{test_totals['errors']} 错误，{test_totals['skipped']} 跳过。\n\n"
        f"验证范围：`{args.scope}`；pytest进程：{jobs}；原始数据核对：{'已执行' if integrity is not None else '未执行'}。\n\n"
        "| 检查 | 结果 |\n|---|---|\n" + rows + "\n\n"
        "仅所选范围的检查已执行；离线验证不代表真实模型准确率或独立泛化。\n\n"
        "历史真实实验统一见 docs/experiment-log.md；本命令不调用模型服务。\n\n"
        f"源码 SHA-256：`{digest}`。逐文件摘要见 [source_manifest.json](source_manifest.json)。\n\n"
        f"复现：在仓库根目录运行 `python scripts/verify.py --scope {args.scope} --jobs {jobs}`。机器摘要见 [summary.json](summary.json)，"
        "测试详情见 [pytest.xml](pytest.xml)、实时日志[pytest.log](pytest.log)及完整执行清单[test_execution.json](test_execution.json)。\n",
        encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
