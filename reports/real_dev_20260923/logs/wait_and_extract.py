"""Wait for the cycle to settle, then extract readings. Not an experiment.

Reads only: the ledger for completion, then delegates to extract_readings.py.
Prints a short progress line every minute so the log shows the wait was alive.
"""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path("artifacts/selfevo-real-dev-20260923")
LEDGER = ROOT / "ledger.jsonl"
REPORT = ROOT / "cycles" / "cycle001.json"
deadline = time.time() + 5 * 3600


def names() -> list[str]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line).get("event"))
            except ValueError:
                pass
    return out


started = time.time()
while time.time() < deadline:
    events = names()
    if REPORT.exists():
        try:
            payload = json.loads(REPORT.read_text(encoding="utf-8-sig"))
        except ValueError:
            payload = {}
        if payload.get("state"):
            print(f"cycle report state={payload['state']} after "
                  f"{(time.time()-started)/60:.1f} min", flush=True)
            break
    if "cycle_completed" in events or "evaluation_preflight_blocked" in events:
        print(f"ledger terminal event after {(time.time()-started)/60:.1f} min",
              flush=True)
        # Give the report write a moment to land.
        time.sleep(20)
        break
    mins = (time.time() - started) / 60
    print(f"[{time.strftime('%H:%M:%S')}] +{mins:.0f}min events={events}", flush=True)
    time.sleep(60)

print("=== EXTRACTED READINGS ===", flush=True)
result = subprocess.run(
    [sys.executable, "reports/real_dev_20260923/extract_readings.py"],
    capture_output=True, text=True,
)
print(result.stdout, flush=True)
if result.returncode != 0:
    print("extractor stderr:", result.stderr, flush=True)
pathlib.Path("reports/real_dev_20260923/readings.json").write_text(
    result.stdout, encoding="utf-8",
)
print("wrote reports/real_dev_20260923/readings.json", flush=True)
