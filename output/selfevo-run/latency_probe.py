"""Read-only latency probe: one real case through the detector, timed.

Uses the real provider so the experiment plan can be sized from measurement
instead of assumption. Does not write registry/ledger.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pathlib import Path
from detector.env import load_local_env
load_local_env()
from detector.api import Detector
from detector.config import load_config as load_detector_config
from detector.dataset import load_case
from detector.interventions import build_intervention_registry

config = load_detector_config("detector/configs/autodl.json")
detector = Detector(config)
cases = sys.argv[1:] or ["BFCL/041.json", "Octotools/023.json"]
for case in cases:
    record, gold = load_case("datasets/raw/agenthallu/AgentHallu", case)
    started = time.monotonic()
    try:
        result = detector.detect(record, case_id=case,
                                 interventions=build_intervention_registry([]))
        elapsed = time.monotonic() - started
        status = result.get("stage_status") or {}
        stages = {k: (v or {}).get("state") for k, v in status.items()}
        print(json.dumps({
            "case": case, "elapsed_s": round(elapsed, 1),
            "state": result.get("state"),
            "authority": result.get("decision_authority"),
            "responsible_step": result.get("responsible_step"),
            "gold": gold.get("is_hallucination"),
            "gold_step": gold.get("hallucination_step"),
            "stages": stages,
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"case": case, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
