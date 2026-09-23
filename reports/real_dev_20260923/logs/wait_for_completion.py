"""Block until the cycle writes cycle_completed or its process exits."""
import json, pathlib, subprocess, time

ROOT = pathlib.Path("artifacts/selfevo-real-dev-20260923")
LEDGER = ROOT / "ledger.jsonl"
deadline = time.time() + 6 * 3600


def events():
    out = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


while time.time() < deadline:
    evs = events()
    names = [e.get("event") for e in evs]
    print(f"[{time.strftime('%H:%M:%S')}] {names}", flush=True)
    if "cycle_completed" in names or "evaluation_preflight_blocked" in names:
        print("DONE", flush=True)
        break
    count = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*evolution cycle*' } | "
         "Measure-Object).Count"],
        capture_output=True, text=True,
    ).stdout.strip()
    if count == "0":
        print("PROCESS EXITED without completion", flush=True)
        break
    time.sleep(60)
else:
    print("TIMEOUT", flush=True)
