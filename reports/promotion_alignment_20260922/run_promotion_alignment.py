"""P0.4 dry-run: mechanism-aligned promotion, offline and deterministic.

The v1 protocol scored a candidate by polarity over a frozen batch. That let a
candidate collect credit for repairing cases that belong to other mechanisms,
and let unrelated regressions cancel against gains. This script exercises the
v2 protocol, which freezes the mechanism, the evaluator's membership evidence,
the candidate content hash and the baseline registry together, and scores the
candidate only on confirmed members of its own mechanism.

Five runs, all offline (scripted arms, no provider call):

1. a repaired mechanism with clean protection and challenge -> promoted;
2. the same mechanism with no repairable error in the gain set -> unmeasurable,
   and the candidate arm is never spent;
3. a protection regression alongside a real mechanism gain -> rejected;
4. a mechanism-external challenge regression -> rejected on its own grounds;
5. a second promotion attempt on the same frozen protocol -> blocked, so frozen
   evidence cannot be re-rolled until it passes.

Run from the repository root:

    python reports/promotion_alignment_20260922/run_promotion_alignment.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.contracts import make_intervention, make_registry  # noqa: E402
from evolution.mechanisms import infer_mechanism  # noqa: E402
from evolution.promotion import (  # noqa: E402
    PromotionRun,
    build_candidate,
    freeze_protocol,
    load_protocol,
)

OUT = pathlib.Path(__file__).resolve().parent / "promotion_alignment.json"

# Mechanism gain / protection / challenge. The gain cases are confirmed members
# of the target mechanism; the challenge cases are explicitly external.
GAIN = ["A/001.json", "A/002.json"]
PROTECTION = ["C/001.json", "C/002.json", "E/001.json", "E/002.json"]
CHALLENGE = ["X/001.json", "X/002.json"]
ALL = [*GAIN, *PROTECTION, *CHALLENGE]
GOLD = {
    "A/001.json": True, "A/002.json": True,
    "C/001.json": True, "C/002.json": True,
    "E/001.json": False, "E/002.json": False,
    "X/001.json": True, "X/002.json": False,
}


def mechanism() -> dict:
    def member(index: int) -> dict:
        return {
            "member": f"member_{index:03d}",
            "contracts": ["required_action"],
            "stages": {},
            "judge_positive": False,
            "recall_path": "not_needed",
            "upgrade_positive": False,
            "defense_verdict": "none",
            "predicates": {"required_action_present": "violated"},
            "typed_samples": {},
        }
    return infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-dryrun",
        members=[member(1), member(2)],
        member_cases=["DEV/001.json", "DEV/002.json"],
        count=2,
    )


def membership(mech: dict) -> dict:
    out = {}
    for case in ALL:
        if case in GAIN:
            out[case] = {
                "mechanism_id": mech["mechanism_id"], "status": "confirmed",
                "evidence": [f"worksheet signature match for {case}"],
                "repairable": True,
            }
        elif case in PROTECTION:
            out[case] = {
                "mechanism_id": mech["mechanism_id"], "status": "confirmed",
                "evidence": ["baseline-correct control case"], "repairable": False,
            }
        else:
            out[case] = {
                "mechanism_id": "M-external-other", "status": "external",
                "evidence": ["similar surface features, different mechanism"],
                "repairable": False,
            }
    return out


def artifact() -> dict:
    return make_intervention(
        intervention_id="IV-P-001",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "report actions the question required that never ran",
            "not_when": "never when every required action ran",
        },
        expected_behavior="recall gains candidates on this mechanism",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-judge_false_no_recall_candidates-dryrun",
        failure_cases=["DEV/001.json", "DEV/002.json"],
    )


class Backend:
    name = "scripted"

    def __init__(self, control: dict, candidate: dict) -> None:
        self.control = dict(control)
        self.candidate = dict(candidate)
        self.calls: list[str] = []

    def evaluate(self, cases, *, dataset_root, registry=None, gold_map=None,
                 gold_step_map=None, workers=1, deadline=None):
        active = bool((registry or {}).get("interventions"))
        self.calls.append("candidate" if active else "control")
        rows = []
        for case in cases:
            gold = bool((gold_map or {}).get(case))
            source = self.candidate if active else self.control
            prediction = source.get(case, gold)
            rows.append({
                "case": case, "gold": gold, "prediction": prediction,
                "state": "verified_error" if prediction else "verified_clean",
                "authority": "authoritative", "responsible_step": None,
                "result": {
                    "case_id": case,
                    "state": "verified_error" if prediction else "verified_clean",
                    "decision_authority": "authoritative", "runtime_valid": True,
                    "responsible_step": None,
                    "stage_status": {"judge": {"state": "ok"}},
                    "proof": {"deployment": {"fired": [
                        {"intervention_id": str(entry.get("intervention_id")),
                         "event": "text_injected"}
                        for entry in (registry or {}).get("interventions") or ()
                    ]}},
                },
                "backend": self.name,
            })
        return rows

    evaluate_fresh = evaluate


class Registry:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.state = {"cycle": 0}

    def check_limits(self, entries, *, library_cfg=None):
        return []

    def write(self, entries, *, cycle, reason, library_cfg=None):
        self.entries = [dict(entry) for entry in entries]
        return make_registry(self.entries)


class Ledger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event, payload):
        self.events.append((str(event), dict(payload)))
        return {"event": event, "payload": payload}

    def verify(self):
        return {"valid": True, "records": len(self.events), "broken_at": None,
                "reason": ""}


def workspace(root: pathlib.Path, mech: dict):
    data = root / "data"
    for case in ALL:
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q",
            "history": [{"step": 1, "role": "assistant", "content": "a"}],
            "is_hallucination": GOLD[case],
            "hallucination_step": None,
            "true_answer": "gold",
        }), encoding="utf-8")
    batch = root / "batch.json"
    roles = {
        **{case: "benefit" for case in GAIN},
        **{case: "protection" for case in PROTECTION},
        **{case: "challenge" for case in CHALLENGE},
    }
    batch.write_text(json.dumps(sorted(roles)), encoding="utf-8")
    detector_config = root / "detector.json"
    detector_config.write_text(json.dumps({"x": 1}), encoding="utf-8")
    candidate = build_candidate(
        candidate_id="development:dryrun:v001", change_kind="revision",
        base_revision="dryrun", artifacts=[artifact()],
        development={"source": "development", "l0": {"passed": True},
                     "l1": {"passed": True}},
        target_kind=mech["objective"], mechanism=mech,
        development_cases=["DEV/001.json", "DEV/002.json"],
    )
    protocol = freeze_protocol(
        root / "protocol.json", batch_path=batch, roles=roles,
        gold={case: GOLD[case] for case in ALL}, gold_steps={},
        development_cases=[], detector_config=detector_config,
        workers=2, timeout_seconds=600, reason="P0.4 dry-run",
        mechanism=mech, mechanism_membership=membership(mech),
        candidate=candidate, dataset_root=data,
        framework_config={"dataset_root": str(data)},
        baseline_registry_hash=make_registry([])["content_hash"],
    )
    protocol["loaded_from"] = str(root / "protocol.json")
    return data, protocol, candidate


def run(tmp_path: pathlib.Path, *, control: dict, candidate: dict,
        output_name: str = "out") -> dict:
    mech = mechanism()
    data, protocol, bound = workspace(tmp_path, mech)
    loaded = load_protocol(
        protocol["loaded_from"], dataset_root=str(data),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    backend = Backend(control, candidate)
    run = PromotionRun(
        candidate=bound, protocol=loaded,
        config={"dataset_root": str(data), "gates": {}, "library": {}},
        output_dir=tmp_path / output_name, backend=backend,
        active_registry=Registry(), ledger=Ledger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    return {
        "state": report.get("state"),
        "admitted": report.get("admitted"),
        "reason_code": (report.get("promotion_diagnosis") or {}).get("reason_code"),
        "category": (report.get("promotion_diagnosis") or {}).get("category"),
        "arms_measured": list(backend.calls),
        "mechanism_opportunity": report.get("opportunity", {}).get("mechanism"),
        "marginal_cases": (
            (report.get("benefit") or {}).get("detection") or {}
        ).get("marginal_cases"),
    }


def main() -> int:
    fixed = {case: GOLD[case] for case in ALL}
    base = tempfile.mkdtemp(prefix="p04_alignment_")
    try:
        root = pathlib.Path(base)
        clean = {case: (GOLD[case] if case not in GAIN else False)
                 for case in ALL}
        repaired = {case: GOLD[case] for case in ALL}

        promoted = run(root / "a", control=clean, candidate=repaired)
        no_opportunity = run(root / "b", control={case: GOLD[case] for case in ALL},
                             candidate={case: GOLD[case] for case in ALL})
        protection_regression = run(
            root / "c", control=clean,
            candidate={**repaired, "E/001.json": True},
        )
        challenge_regression = run(
            root / "d", control=clean,
            candidate={**repaired, "X/001.json": False},
        )
        # A second attempt on the same frozen evidence, from another directory.
        mech = mechanism()
        replay_root = root / "e"
        data, protocol, bound = workspace(replay_root, mech)
        loaded = load_protocol(
            protocol["loaded_from"], dataset_root=str(data),
            stages=("structural", "harmlessness", "behavior", "m"),
        )
        PromotionRun(
            candidate=bound, protocol=loaded,
            config={"dataset_root": str(data), "gates": {}, "library": {}},
            output_dir=replay_root / "out1", backend=Backend(clean, repaired),
            active_registry=Registry(), ledger=Ledger(),
            stages=("structural", "harmlessness", "behavior", "m"),
        ).run()
        second = PromotionRun(
            candidate=bound, protocol=loaded,
            config={"dataset_root": str(data), "gates": {}, "library": {}},
            output_dir=replay_root / "out2", backend=Backend(clean, repaired),
            active_registry=Registry(), ledger=Ledger(),
            stages=("structural", "harmlessness", "behavior", "m"),
        ).run()
        rereoll = {
            "state": second.get("state"),
            "reason_code": (second.get("promotion_diagnosis") or {}).get("reason_code"),
            "terminal_reason": second.get("terminal_reason"),
            "admitted": second.get("admitted"),
        }
        payload = {
            "schema_version": "evolution.promotion_alignment_dryrun.v1",
            "evidence_grade": "diagnostic-only",
            "note": (
                "Scripted offline arms: no provider call, no admission beyond "
                "the offline registry stub. Demonstrates the promotion rules, "
                "not detector quality. Real promotion still requires a real "
                "service run and an independently frozen protocol."
            ),
            "mechanism_id": mech["mechanism_id"],
            "case_roles": {"gain": GAIN, "protection": PROTECTION,
                           "challenge": CHALLENGE},
            "promoted": promoted,
            "no_mechanism_opportunity": no_opportunity,
            "protection_regression": protection_regression,
            "challenge_regression": challenge_regression,
            "second_attempt_on_frozen_evidence": rereoll,
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(json.dumps({
            "out": OUT.relative_to(REPO).as_posix(),
            "promoted": {k: promoted[k] for k in ("state", "reason_code")},
            "no_opportunity": {k: no_opportunity[k]
                               for k in ("state", "reason_code", "arms_measured")},
            "protection_regression": protection_regression["reason_code"],
            "challenge_regression": challenge_regression["reason_code"],
            "second_attempt": rereoll,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
