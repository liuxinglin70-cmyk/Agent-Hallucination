"""Render a self-contained HTML progress dashboard for a running experiment.

Read-only: it never touches the registry, the ledger or any experiment input.
It reads the ledger's append-only event stream, the cycle reports and the
backend's per-case progress log, and rewrites one HTML file in place so a
browser can simply be refreshed.

Usage:
    python output/selfevo-run/progress_dashboard.py --work-root artifacts/selfevo-bigrun \
        --out artifacts/selfevo-bigrun/progress.html [--watch] [--interval 10]
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(REPO))

# The stages a cycle moves through, in order, with what each one means. The
# dashboard shows them as a pipeline so a stall is visible as "stuck at X"
# rather than as a missing number.
STAGE_ORDER = [
    ("cycle_started", "启动"),
    ("audit", "缺陷注入审计"),
    ("patrol", "巡检与诊断"),
    ("evaluation_preflight", "评估预检"),
    ("proposed", "候选提案"),
    ("rejected", "门控拒绝"),
    ("admitted", "准入"),
    ("registry_written", "写入 registry"),
    ("cycle_completed", "轮次完成"),
]

EVENT_LABELS = {
    "cycle_started": "轮次启动",
    "audit": "缺陷注入审计",
    "patrol": "巡检与诊断",
    "evaluation_preflight": "评估预检",
    "evaluation_preflight_blocked": "预检阻断",
    "proposal_memory": "记忆检索",
    "proposal_memory_used": "记忆使用",
    "proposed": "生成候选",
    "rejected": "候选被拒",
    "admitted": "候选准入",
    "registry_written": "写入 registry",
    "cycle_completed": "轮次完成",
    "cycle_interrupted": "轮次中断",
    "governance": "治理核算",
    "retired": "退休",
    "rolled_back": "回滚",
    "harness": "H_t 清单",
    "capability_gaps": "能力缺口",
    "resume_registry_restored": "恢复 registry",
    "final_verification": "终审",
    "full_self_evolution_started": "全自我进化启动",
    "promotion_attempt": "晋升尝试",
}


def read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def read_progress(path: Path) -> list[dict]:
    """Per-case progress lines emitted by the backend under EVOLUTION_PROGRESS."""
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
                    continue
            except ValueError:
                pass
        match = re.match(
            r"\[evolution\]\s+(.*)$", line,
        )
        if match:
            rows.append({"text": match.group(1)})
    return rows


def read_checkpoint_rows(work_root: Path) -> list[dict]:
    """Per-case rows the checkpoint journal writes as each case lands.

    This is the live signal that actually exists during a run: the journal
    evaluates one case per call, so the batch-level progress log never fires.
    Each file holds one finished row with its own ``elapsed_seconds``.
    """
    rows = []
    for path in sorted(work_root.glob("checkpoints/cycle*/arms/*/*.json")):
        payload = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        row = (payload or {}).get("row")
        if isinstance(row, dict):
            row = dict(row)
            row["arm"] = (payload or {}).get("arm")
            rows.append(row)
    return rows


def read_cycle_reports(work_root: Path) -> list[dict]:
    reports = []
    cycles = work_root / "cycles"
    if cycles.is_dir():
        for path in sorted(cycles.glob("cycle*.json")):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError):
                continue
    return reports


def summarize_cycle(report: dict) -> dict:
    """The few numbers a reader wants from one cycle report."""
    patrol = report.get("patrol") or {}
    metrics = patrol.get("metrics") or {}
    detection = (patrol.get("detection_families") or [])
    localization = (patrol.get("localization_families") or [])
    proposals = report.get("proposals") or []
    gates = report.get("gates") or {}
    admitted = [
        name for name, verdict in gates.items()
        if verdict.get("passed") is True
    ] if isinstance(gates, dict) else []
    return {
        "cycle": report.get("cycle"),
        "state": report.get("state"),
        "patrol_size": patrol.get("patrol_size"),
        "tp": metrics.get("tp"),
        "fp": metrics.get("fp"),
        "fn": metrics.get("fn"),
        "tn": metrics.get("tn"),
        "unresolved": metrics.get("unresolved"),
        "detection_families": len(detection),
        "localization_families": len(localization),
        "proposals": len(proposals),
        "active_after": report.get("active_after_cycle"),
    }


def summarize_arm_rows(rows: list[dict]) -> dict:
    """Confusion counts and latency over the rows the checkpoint has landed."""
    by_arm: dict[str, dict] = {}
    elapsed = []
    for row in rows:
        arm = str(row.get("arm") or "?")
        bucket = by_arm.setdefault(
            arm, {"n": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "unresolved": 0},
        )
        bucket["n"] += 1
        gold = row.get("gold")
        prediction = row.get("prediction")
        if prediction is None:
            bucket["unresolved"] += 1
        elif prediction is True and gold is True:
            bucket["tp"] += 1
        elif prediction is True and gold is False:
            bucket["fp"] += 1
        elif prediction is False and gold is True:
            bucket["fn"] += 1
        else:
            bucket["tn"] += 1
        value = row.get("elapsed_seconds")
        if isinstance(value, (int, float)):
            # A cache hit lands in ~0s. Counting it as a fast case would report
            # a median latency that describes the cache rather than the model,
            # so cached rows are excluded from the latency distribution and
            # counted separately.
            if float(value) < 1.0:
                bucket["cached"] = bucket.get("cached", 0) + 1
            else:
                elapsed.append(float(value))
    summary = {"arms": by_arm, "cases_done": len(rows)}
    if elapsed:
        ordered = sorted(elapsed)
        summary["elapsed"] = {
            "n": len(ordered),
            "min": round(ordered[0], 1),
            "median": round(ordered[len(ordered) // 2], 1),
            "max": round(ordered[-1], 1),
            "total": round(sum(ordered), 1),
        }
    return summary


def _is_driver(cmdline: list[str]) -> bool:
    """Whether a process is the evolution driver, not a shell that launched it.

    Matching on the module text alone picked the ``bash -c ... evolution
    experiment ...`` wrapper, whose child list is one shell and no workers, so
    the page reported "0 in-flight" while twelve calls were live. The wrapper
    is indistinguishable from the driver by text; it is distinguishable by
    argv shape, because the driver's argv *is* python running the package.
    """
    if not cmdline:
        return False
    program = str(cmdline[0]).lower()
    if not program.endswith(("python", "python.exe", "pythonw.exe")):
        return False
    joined = " ".join(cmdline)
    return "-m evolution" in joined


def live_concurrency() -> dict:
    """The driver and its in-flight provider calls, from the OS process table.

    Without this the page can only show cases that already finished, so a slow
    case looks identical to a stalled run: two cases in flight for twenty
    minutes shows as "landed 10" with nothing changing. The provider's own
    per-call ceiling is reported alongside the worker ages so "still working"
    and "past its budget" are distinguishable at a glance.
    """
    snapshot: dict = {"driver_pid": None, "driver_elapsed": None, "workers": []}
    try:
        import psutil
    except ImportError:
        snapshot["note"] = "psutil unavailable; concurrency not shown"
        return snapshot
    now = time.time()
    drivers = []
    for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
        if _is_driver(process.info.get("cmdline") or []):
            drivers.append(process)
    if not drivers:
        return snapshot
    # If a driver was relaunched, the newest one owns the live calls.
    driver = max(drivers, key=lambda item: item.info.get("create_time") or 0)
    snapshot["driver_pid"] = driver.pid
    snapshot["driver_elapsed"] = round(
        now - (driver.info.get("create_time") or now), 1
    )
    if len(drivers) > 1:
        snapshot["other_drivers"] = sorted(item.pid for item in drivers
                                           if item.pid != driver.pid)
    for child in driver.children(recursive=True):
        try:
            cmdline = " ".join(child.cmdline() or [])
            if "provider_worker" not in cmdline:
                continue
            cpu = child.cpu_times()
            snapshot["workers"].append({
                "pid": child.pid,
                "age_seconds": round(now - child.create_time(), 1),
                "cpu_seconds": round(cpu.user + cpu.system, 1),
            })
        except Exception:
            continue
    snapshot["workers"].sort(key=lambda item: item["age_seconds"], reverse=True)
    return snapshot


def stage_progress(work_root: Path, landed: set[str]) -> list[dict]:
    """How far the current cycle is through its case-evaluation waves.

    The engine evaluates the sentinel, then the gain set, then the patrol, each
    as one wave with its own concurrency limit. So the number of workers is
    bounded by the size of the wave in progress, not by the configured
    concurrency -- a fact worth showing, because "only 2 workers" during the
    tail of a 12-case wave is correct behaviour, not low parallelism.
    """
    contract_path = None
    for candidate in sorted(work_root.glob("checkpoints/cycle*/checkpoint.json")):
        contract_path = candidate
    if contract_path is None:
        return []
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    split = (contract.get("contract") or {}).get("split") or {}
    waves = [
        ("哨兵 sentinel", list(split.get("sentinel") or [])),
        ("收益集 gain", list(split.get("gain") or [])),
    ]
    cycles = []
    cycles_dir = work_root / "cycles"
    patrol_cases: list[str] = []
    if cycles_dir.is_dir():
        for path in sorted(cycles_dir.glob("cycle*.json")):
            try:
                report = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            for case in (report.get("patrol") or {}).get("cases") or []:
                patrol_cases.append(str(case))
    if patrol_cases:
        waves.append(("巡检 patrol（单臂）", sorted(set(patrol_cases))))
    out = []
    for label, cases in waves:
        if not cases:
            out.append({"label": label, "total": 0, "done": 0, "pending": []})
            continue
        done = [case for case in cases if case in landed]
        out.append({
            "label": label,
            "total": len(cases),
            "done": len(done),
            "pending": [case for case in cases if case not in landed],
        })
    return out


def render(
    *,
    work_root: Path,
    ledger: list[dict],
    progress: list[dict],
    cycles: list[dict],
    arm_rows: list[dict] | None = None,
    live: dict | None = None,
    waves: list[dict] | None = None,
    now: float,
) -> str:
    by_event = collections.Counter(
        str(entry.get("event") or "") for entry in ledger
    )
    cycle_entries = [
        entry for entry in ledger
        if str(entry.get("event") or "").startswith("cycle")
        or str(entry.get("event") or "") in EVENT_LABELS
    ]
    started = None
    for entry in ledger:
        if entry.get("event") == "cycle_started":
            started = entry.get("timestamp")
            break
    last_ts = ledger[-1].get("timestamp") if ledger else None

    arm_rows = list(arm_rows or [])
    arm_summary = summarize_arm_rows(arm_rows)
    live = dict(live or {})
    waves = list(waves or [])

    # Liveness panel: the question "is it working or stuck?" is answered here.
    workers = live.get("workers") or []
    wave_rows = []
    for wave in waves:
        total = wave.get("total") or 0
        if not total:
            continue
        done = wave.get("done") or 0
        pending = wave.get("pending") or []
        pct = round(100 * done / total)
        state = "done" if done == total else ("running" if done else "queued")
        pending_text = ", ".join(pending[:6]) + (
            f" (+{len(pending) - 6})" if len(pending) > 6 else ""
        )
        wave_rows.append(
            f"<tr><td>{html.escape(wave['label'])}</td>"
            f"<td>{done}/{total}</td>"
            f"<td><div class='bar'><i style='width:{pct}%'></i></div></td>"
            f"<td class='{'ok' if state == 'done' else 'warn'}'>{state}</td>"
            f"<td class='detail'>{html.escape(pending_text) or '—'}</td></tr>"
        )

    if workers:
        oldest = max(w["age_seconds"] for w in workers)
        # The provider abandons a single call at its total ceiling, so an age
        # beyond it means something is wrong rather than merely slow.
        live_state = (
            f"{len(workers)} 个 provider 调用在飞，最久 {oldest:.0f}s"
            f"（单次调用上限 900s）"
        )
        live_cls = "ok" if oldest < 900 else "warn"
    elif live.get("driver_pid"):
        live_state = "驱动在运行，但当前没有在飞的 provider 调用（阶段间隙或缓存命中）"
        live_cls = "clean"
    elif live.get("note"):
        live_state = live["note"]
        live_cls = "clean"
    else:
        live_state = "未检测到运行中的驱动进程"
        live_cls = "warn"

    driver_line = ""
    if live.get("driver_pid"):
        driver_line = (
            f"驱动 pid={live['driver_pid']} · 已运行 "
            f"{(live.get('driver_elapsed') or 0) / 60:.0f} 分钟"
        )

    recent_cases = sorted(
        arm_rows,
        key=lambda r: float(r.get("elapsed_seconds") or 0),
        reverse=True,
    )[:40]
    texts = [row.get("text") for row in progress if row.get("text")][-25:][::-1]

    live_rows = []
    for arm, counts in sorted(arm_summary.get("arms", {}).items()):
        live_rows.append(
            "<tr>"
            f"<td>{html.escape(arm)}</td>"
            f"<td>{counts['n']}</td>"
            f"<td>{counts['tp']}/{counts['fp']}/{counts['fn']}/{counts['tn']}</td>"
            f"<td>{counts['unresolved']}</td>"
            "</tr>"
        )
    elapsed = arm_summary.get("elapsed") or {}
    latency_html = ""
    if elapsed:
        cached_n = sum(int(c.get("cached") or 0) for c in arm_summary.get("arms", {}).values())
        latency_html = (
            f"<div class='sub' style='margin-top:8px'>实测逐例耗时 n={elapsed['n']} · "
            f"最小 {elapsed['min']}s · 中位 {elapsed['median']}s · "
            f"最大 {elapsed['max']}s（另有 {cached_n} 例为缓存命中，不计入耗时分布）</div>"
        )

    rows = []
    for item in cycles:
        summary = summarize_cycle(item)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(summary['cycle']))}</td>"
            f"<td>{html.escape(str(summary['state']))}</td>"
            f"<td>{summary['patrol_size']}</td>"
            f"<td>{summary['tp']}/{summary['fp']}/{summary['fn']}/{summary['tn']}</td>"
            f"<td>{summary['detection_families']}</td>"
            f"<td>{summary['localization_families']}</td>"
            f"<td>{summary['proposals']}</td>"
            f"<td>{html.escape(str(summary['active_after']))}</td>"
            "</tr>"
        )

    event_rows = []
    for entry in cycle_entries[-40:][::-1]:
        name = str(entry.get("event") or "")
        payload = entry.get("payload") or {}
        detail = ""
        if name == "cycle_completed":
            detail = str(payload.get("state") or "")
        elif name == "audit":
            detail = f"passed={payload.get('passed')} cache={payload.get('cache')}"
        elif name == "patrol":
            detail = f"{len(payload.get('cases') or [])} cases"
        elif name == "proposed":
            detail = f"{payload.get('count', payload.get('proposals', ''))}"
        elif name == "rejected":
            detail = str(payload.get("gate") or "") + " " + str(
                payload.get("reason") or ""
            )[:70]
        elif name == "admitted":
            detail = str(payload.get("intervention_id") or "")
        elif name == "registry_written":
            detail = f"cycle={payload.get('cycle')}"
        event_rows.append(
            "<tr>"
            f"<td>{html.escape(str(entry.get('timestamp') or '')[:19])}</td>"
            f"<td>{html.escape(EVENT_LABELS.get(name, name))}</td>"
            f"<td class='detail'>{html.escape(detail)}</td>"
            "</tr>"
        )

    case_rows = []
    for row in recent_cases:
        prediction = row.get("prediction")
        gold = row.get("gold")
        state = str(row.get("state") or "")
        cls = "ok" if prediction is True else (
            "clean" if prediction is False else "warn"
        )
        correct = (prediction is gold)
        case_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('case')))}</td>"
            f"<td>{html.escape(str(gold))}</td>"
            f"<td class='{cls}'>{html.escape(str(prediction))}</td>"
            f"<td class='{'ok' if correct else 'warn'}'>{'✓' if correct else '✗'}</td>"
            f"<td>{html.escape(str(row.get('responsible_step') or ''))}</td>"
            f"<td>{html.escape(str(row.get('elapsed_seconds', '')))}</td>"
            "</tr>"
        )

    stage_html = []
    seen = set(by_event)
    for event, label in STAGE_ORDER:
        count = by_event.get(event, 0)
        cls = "stage done" if count else "stage"
        stage_html.append(
            f"<div class='{cls}'><span>{html.escape(label)}</span>"
            f"<b>{count}</b></div>"
        )

    counters = []
    for event, count in sorted(by_event.items()):
        counters.append(
            f"<div class='counter'><span>{html.escape(EVENT_LABELS.get(event, event))}</span>"
            f"<b>{count}</b></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>自进化验证实验进度</title>
<meta http-equiv="refresh" content="15">
<style>
 body{{font-family:ui-sans-serif,system-ui,'Segoe UI',Roboto,'Microsoft YaHei',sans-serif;
   margin:0;background:#0f1115;color:#e6e8eb}}
 header{{padding:18px 24px;background:#161a21;border-bottom:1px solid #262c36}}
 h1{{font-size:18px;margin:0 0 6px}}
 .sub{{color:#8b95a5;font-size:12px}}
 main{{padding:18px 24px;display:grid;gap:18px}}
 section{{background:#161a21;border:1px solid #262c36;border-radius:8px;padding:14px 16px}}
 h2{{font-size:13px;margin:0 0 10px;color:#9aa6b8;text-transform:uppercase;letter-spacing:.06em}}
 .stages{{display:flex;flex-wrap:wrap;gap:8px}}
 .stage{{background:#1d222b;border:1px solid #2c3441;border-radius:6px;padding:8px 12px;
   font-size:12px;display:flex;gap:8px;align-items:center}}
 .stage.done{{border-color:#2f6f4f;background:#16241c}}
 .stage b{{color:#7fd1a6}}
 .counters{{display:flex;flex-wrap:wrap;gap:8px}}
 .counter{{background:#1d222b;border:1px solid #2c3441;border-radius:6px;padding:6px 10px;font-size:12px}}
 .counter b{{margin-left:6px;color:#8fc7ff}}
 table{{width:100%;border-collapse:collapse;font-size:12px}}
 th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #232a34}}
 th{{color:#8b95a5;font-weight:600}}
 td.ok{{color:#7fd1a6}} td.clean{{color:#8b95a5}} td.warn{{color:#e0b56a}}
 .ok{{color:#7fd1a6}} .clean{{color:#8b95a5}} .warn{{color:#e0b56a}}
 .bar{{background:#232a34;border-radius:4px;height:8px;width:120px;overflow:hidden}}
 .bar i{{display:block;height:100%;background:#4a8fd4}}
 .detail{{color:#9aa6b8}}
 .cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
 .log{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#9aa6b8;
   white-space:pre-wrap;max-height:320px;overflow:auto}}
 .stale{{color:#e0b56a}}
</style></head><body>
<header>
  <h1>自进化框架验证实验 — 实时进度</h1>
  <div class="sub">工作目录 {html.escape(str(work_root))} ·
    账本事件 {len(ledger)} 条 ·
    最近事件 {html.escape(str(last_ts or '—')[:19])} ·
    页面刷新 {time.strftime('%H:%M:%S', time.localtime(now))}</div>
</header>
<main>
  <section><h2>是否在正常运行</h2>
    <div class="{live_cls}" style="font-size:13px;margin-bottom:8px">{html.escape(live_state)}</div>
    <div class="sub">{html.escape(driver_line) or '—'}</div>
    <table style="margin-top:10px"><thead><tr><th>评估波次</th><th>已完成</th><th>进度</th><th>状态</th><th>在飞/待办案例</th></tr></thead>
    <tbody>{''.join(wave_rows) or '<tr><td colspan="5">尚无检查点</td></tr>'}</tbody></table>
    <div class="sub" style="margin-top:8px">说明：引擎按波次依次评估（哨兵 → 收益集 → 巡检），每波并发上限取该波案例数与配置并发数的较小者。所以波次尾部只剩 2 例时显示 2 个 worker 是正常行为，不是并发被压低。</div>
    {'<table style="margin-top:10px"><thead><tr><th>在飞调用 pid</th><th>已运行(s)</th><th>CPU(s)</th></tr></thead><tbody>' + ''.join(f"<tr><td>{w['pid']}</td><td>{w['age_seconds']:.0f}</td><td>{w['cpu_seconds']}</td></tr>" for w in workers[:12]) + '</tbody></table>' if workers else ''}
  </section>
  <section><h2>流水线</h2><div class="stages">{''.join(stage_html)}</div></section>
  <section><h2>事件计数</h2><div class="counters">{''.join(counters) or '<i>暂无事件</i>'}</div></section>
  <section><h2>逐轮读数</h2>
    <table><thead><tr><th>轮</th><th>状态</th><th>巡检例数</th>
      <th>TP/FP/FN/TN</th><th>检测族</th><th>定位族</th><th>提案</th><th>轮后 active</th>
    </tr></thead><tbody>{''.join(rows) or '<tr><td colspan="8">尚无完成的轮次</td></tr>'}</tbody></table>
  </section>
  <section><h2>最近事件</h2>
    <table><thead><tr><th>时间</th><th>事件</th><th>细节</th></tr></thead>
    <tbody>{''.join(event_rows) or '<tr><td colspan="3">暂无</td></tr>'}</tbody></table>
  </section>
  <section><h2>已落盘的逐例行（按耗时降序，最多 40 条）</h2>
    <table><thead><tr><th>臂</th><th>已落盘</th><th>TP/FP/FN/TN</th><th>未决</th></tr></thead>
    <tbody>{''.join(live_rows) or '<tr><td colspan="4">暂无</td></tr>'}</tbody></table>
    {latency_html}
  </section>
  <div class="cols">
    <section><h2>逐例明细（按耗时降序）</h2>
      <table><thead><tr><th>案例</th><th>gold</th><th>判定</th><th>对?</th><th>责任步</th><th>耗时(s)</th></tr></thead>
      <tbody>{''.join(case_rows) or '<tr><td colspan="6">暂无</td></tr>'}</tbody></table>
    </section>
    <section><h2>后台输出尾部</h2>
      <div class="log">{html.escape(chr(10).join(str(t) for t in texts)) or '暂无'}</div>
    </section>
  </div>
</main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--progress-log", default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args()

    work_root = Path(args.work_root)
    out = Path(args.out)
    progress_log = Path(args.progress_log) if args.progress_log else (
        work_root / "progress.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    while True:
        ledger = read_ledger(work_root / "ledger.jsonl")
        progress = read_progress(progress_log)
        cycles = read_cycle_reports(work_root)
        arm_rows = read_checkpoint_rows(work_root)
        landed = {str(row.get("case") or "") for row in arm_rows}
        live = live_concurrency()
        waves = stage_progress(work_root, landed)
        document = render(
            work_root=work_root, ledger=ledger, progress=progress,
            cycles=cycles, arm_rows=arm_rows, live=live, waves=waves,
            now=time.time(),
        )
        out.write_text(document, encoding="utf-8", newline="\n")
        print(f"[dashboard] {len(ledger)} ledger events, "
              f"{len(arm_rows)} landed case rows, "
              f"{len(live.get('workers') or [])} in-flight -> {out}", flush=True)
        if not args.watch:
            return 0
        time.sleep(max(2.0, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
