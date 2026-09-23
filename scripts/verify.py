"""Reproduce offline checks and bind their reports to the current source."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "latest"


def save(name: str, payload: object) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_manifest() -> dict[str, str]:
    files = [ROOT / "pyproject.toml", ROOT / "datasets/integrity.json"]
    for folder in ("detector", "evolution", "tests", "scripts", "datasets/protocols"):
        files.extend(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and p.suffix in {".py", ".json"})
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    initial = source_manifest()
    save("summary.json", {"passed": False, "status": "running", "started_at_utc": started})
    (OUT / "README.md").write_text("# 最新代码验证结果\n\n验证正在运行，结果尚未完成。\n", encoding="utf-8")
    (OUT / "pytest.xml").write_text("<testsuites />\n", encoding="utf-8")
    for pending in ("capability_audit.json", "metamorphic_gate.json", "sandbox_check.json",
                    "smoke.json", "capability_smoke.json"):
        save(pending, {"status": "not_run"})
    checks = []
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    def run(name: str, arguments: list[str]) -> bool:
        before = time.monotonic()
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment,
                                text=True, encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / f"{name}.log").write_text(result.stdout, encoding="utf-8")
        checks.append({"name": name, "arguments": arguments,
                       "exit_code": result.returncode,
                       "seconds": round(time.monotonic() - before, 3)})
        print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        return result.returncode == 0

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
    run("pytest", ["-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "--junitxml=reports/latest/pytest.xml"])
    run("detector_cli", ["-m", "detector", "--help"])
    run("evolution_cli", ["-m", "evolution", "--help"])
    if run("capability_audit", ["-m", "evolution", "capability-audit"]):
        save("capability_audit.json", json.loads((OUT / "capability_audit.log").read_text(encoding="utf-8")))
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
               "scope": "Offline regression and mock mechanism validation; no live provider accuracy measurement.",
               "source_sha256": digest, "passed": passed, "tests": dict(test_totals), "checks": checks}
    save("summary.json", summary)
    rows = "\n".join(f"| {c['name']} | {'PASS' if c['exit_code'] == 0 else 'FAIL'} |" for c in checks)
    (OUT / "README.md").write_text(
        f"# 最新代码验证结果\n\n生成时间（UTC）：{started}\n\n"
        f"总状态：**{'PASS' if passed else 'FAIL'}**。pytest：{test_totals['passed']} 通过，"
        f"{test_totals['failures']} 失败，{test_totals['errors']} 错误，{test_totals['skipped']} 跳过。\n\n"
        f"原始数据：{integrity['file_count']} 个文件，{integrity['case_count']} 个案例；完整性核对"
        f"{'通过' if integrity['passed'] else '失败'}。\n\n"
        "| 检查 | 结果 |\n|---|---|\n" + rows + "\n\n"
        "这些结果包括本地 HTTP 命令链回归与 mock 机制验证，不是真实模型准确率或独立泛化结论。\n\n"
        "另行执行的真实服务检查见 [../live/README.md](../live/README.md)（两例合成联调）与 "
        "[../real/README.md](../real/README.md)（真实数据集小规模路径与重复运行）；两者都有独立时间与源码记录，"
        "本命令不会重新调用模型服务。\n\n"
        f"源码 SHA-256：`{digest}`。逐文件摘要见 [source_manifest.json](source_manifest.json)。\n\n"
        "复现：在仓库根目录运行 `python scripts/verify.py`。机器摘要见 [summary.json](summary.json)，"
        "测试详情见 [pytest.xml](pytest.xml) 和 [pytest.log](pytest.log)。\n",
        encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
