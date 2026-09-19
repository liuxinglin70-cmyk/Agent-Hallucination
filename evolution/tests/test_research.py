"""Offline tests for the bounded-revision research entry (doc §8).

Everything runs on mocks: no provider, no real detector, no label file. The
mock backend models a bare-base FN pair plus two clean neighbors, and flips
the failures only when the candidate registry is actually supplied — so a
candidate that never reaches evaluation cannot fake a repair.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evolution.proposer import ProposalBatch
from evolution.research import ResearchRun, run_research


MECHANISM = {
    "name": "quoted_content_vs_delivered",
    "description": (
        "delivered content must match the exact text the task quotes"
    ),
    "repair_operation": (
        "locate the quoted required content in the task text, compare it "
        "with the final delivered parameters, flag unquoted additions"
    ),
    "target_error_description": (
        "agent wrote extra template text instead of the quoted line"
    ),
}

FAMILY = {
    "failure_family": "FAM-fn-quoted-content-vs-delivered",
    "mechanism_summary": "task-quoted content vs delivered parameter",
    "actionability": {"routes": ["text_boundary"]},
}

CASES = [
    {
        "case_id": "BFCL/002.json", "role": "training_failure",
        "raw_gold": True, "target_step": 6,
        "target_error": "wrote template text instead of the quoted line",
    },
    {
        "case_id": "BFCL/153.json", "role": "training_failure",
        "raw_gold": True, "target_step": 14,
        "target_error": "embedded tags and mentions into the quoted body",
    },
    {"case_id": "BFCL/020.json", "role": "normal_neighbor", "raw_gold": False},
    {"case_id": "BFCL/053.json", "role": "normal_neighbor", "raw_gold": False},
]

FAILURE_IDS = ["BFCL/002.json", "BFCL/153.json"]


def _valid_candidate():
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-R-001",
        "type": "text_boundary",
        "hook": "judge.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "directive": (
                "When the task text quotes the exact required content, "
                "compare the final delivered content parameter against the "
                "quoted text before voting and flag unquoted additions or "
                "separately-requested elements embedded into the body."
            ),
            "not_when": (
                "Never flag paraphrase, renumbering or summary presentation "
                "when the task does not quote the exact required content."
            ),
        },
        "claim": {
            "expected_behavior": "judge re-checks the quoted-content binding",
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": FAMILY["failure_family"],
            "failure_cases": list(FAILURE_IDS),
        },
    }


def _candidate_with(directive, not_when="Never flag anything without quoted text."):
    candidate = _valid_candidate()
    candidate["body"]["directive"] = directive
    candidate["body"]["not_when"] = not_when
    return candidate


def _route_candidate(route):
    """Distinct but valid drafts per route: real routes rarely converge."""
    suffix = "route B" if route == "B" else "route C wording"
    return _candidate_with(
        _valid_candidate()["body"]["directive"] + f" Check the delivered body first ({suffix}).",
        _valid_candidate()["body"]["not_when"],
    )


class FakeBackend:
    """Bare-base FN pair; flips only under a real candidate registry."""

    name = "fake"

    def __init__(self, *, repair=True, target_steps=None):
        self.repair = repair
        self.target_steps = target_steps or {}
        self.calls = []

    def evaluate_fresh(
        self, cases, *, dataset_root, registry=None, gold_map=None,
        workers=1, deadline=None,
    ):
        self.calls.append({"cases": list(cases), "registry": registry})
        # The mock models a candidate as effective only when its directive
        # actually describes the quoted-content operation; any other text
        # leaves the bare-base predictions untouched.
        directive = ""
        for entry in (registry or {}).get("interventions") or []:
            directive += str((entry.get("body") or {}).get("directive") or "")
        effective = self.repair and "quote" in directive.lower()
        rows = []
        for case_id in cases:
            gold = bool((gold_map or {}).get(case_id))
            # Bare base misses every gold=true failure (that is why they are
            # training failures); an effective candidate flips exactly those.
            if effective and gold:
                state, prediction = "verified_error", True
            else:
                state, prediction = "verified_clean", False
            result = {
                "case_id": case_id,
                "state": state,
                "decision_authority": "authoritative",
                "runtime_valid": True,
                "responsible_step": (
                    self.target_steps.get(case_id)
                    if (effective and gold) else None
                ),
                "provenance": {
                    "interventions_hash": str(
                        (registry or {}).get("content_hash") or ""
                    ),
                },
            }
            rows.append({
                "case": case_id,
                "gold": gold,
                "prediction": prediction,
                "state": state,
                "authority": "authoritative",
                "result": result,
                "backend": self.name,
            })
        return rows


class FakeProposer:
    name = "fake"

    def __init__(self, batches):
        # One ProposalBatch per propose() call, in order.
        self.batches = list(batches)
        self.received = []

    def set_memory_context(self, context):
        self.received.append(("memory", context))

    def set_rejection_feedback(self, feedback):
        self.received.append(("feedback", feedback))

    def set_research_packet(self, packet):
        self.received.append(("packet", packet))

    def propose(self, families, *, cycle, max_proposals=3):
        self.received.append(("families", families))
        batch = self.batches.pop(0) if self.batches else ProposalBatch()
        return batch


def _write_dataset(tmp_path):
    root = tmp_path / "datasets" / "raw" / "agenthallu" / "AgentHallu"
    root.mkdir(parents=True)
    files = {}
    for case in CASES:
        case_id = case["case_id"]
        raw = {
            "question": f"task text for {case_id}",
            "history": [
                {"step": 1, "role": "assistant", "content": "did things"},
            ],
            # Label fields exist in the raw record; the research entry must
            # never read them through project_record.
            "is_hallucination": case["raw_gold"],
            "true_answer": "hidden-answer-must-not-leak",
        }
        payload = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        case_path = root / case_id
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_bytes(payload)
        # The integrity manifest keys are relative to datasets/raw.
        files[f"agenthallu/AgentHallu/{case_id}"] = hashlib.sha256(
            payload,
        ).hexdigest()
    (tmp_path / "datasets" / "integrity.json").write_text(
        json.dumps({"files": files}), encoding="utf-8",
    )
    return root


def _write_baseline_ref(tmp_path, dataset_root, config_hash="cfg-hash-1"):
    run_dir = tmp_path / "h0_ref_run"
    for case in CASES:
        case_id = case["case_id"]
        gold = bool(case["raw_gold"])
        result = {
            "case_id": case_id,
            "state": "verified_error" if gold else "verified_clean",
            "decision_authority": "authoritative",
            "runtime_valid": True,
            "responsible_step": None,
        }
        path = run_dir / "cases" / case_id.replace("/", "__") / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"config_hash": config_hash}), encoding="utf-8",
    )
    return run_dir


def _write_manifest(
    tmp_path,
    dataset_root,
    *,
    budget=None,
    baseline_runs=None,
    config_hash="cfg-hash-1",
    routes=("B", "C"),
):
    config_path = tmp_path / "detector_config.json"
    config_path.write_text(json.dumps({"model": "fake-model"}), encoding="utf-8")
    manifest = {
        "schema_version": "evolution.research_manifest.v1",
        "run_id": "test_run",
        "mechanism": MECHANISM,
        "family": FAMILY,
        "allowed_interventions": {"type": "text_boundary", "hooks": ["judge.system"]},
        "dataset_root": str(dataset_root),
        "detector_config": str(config_path),
        "cases": CASES,
        "routes": list(routes),
        "baseline_ref": {
            "runs": [
                {"run_dir": str(run)} for run in (baseline_runs or [])
            ],
            "detector_config_hash": config_hash,
        },
        "budget": budget or {
            "baseline_case_evaluations": 0,
            "screening_case_evaluations": 16,
            "proposer_calls_per_route": 3,
            "max_versions": 4,
            "workers": 1,
        },
    }
    manifest_path = tmp_path / "research_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_screening_pass_reaches_selection(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),  # route B draft
        ProposalBatch(candidates=[_route_candidate("C")]),  # route C draft
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "screening_complete"
    assert report["next_stage"] == "frozen_paired_recheck"
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["B"]["selected"] == "v001"
    assert selection["routes"]["C"]["selected"] == "v002"
    out = tmp_path / "run"
    assert (out / "manifest.json").exists()
    assert (out / "baseline" / "summary.json").exists()
    assert (out / "v001" / "candidate.json").exists()
    assert (out / "v001" / "results" / "BFCL__002.json").exists()
    assert (out / "v002" / "candidate.json").exists()
    assert (out / "report.md").exists()
    # Two drafts evaluated 4 cases each; no revision was needed.
    assert report["budget"]["used"]["screening_case_evaluations"] == 8
    assert report["budget"]["used"]["proposer_calls"] == {"B": 1, "C": 1}
    # The baseline came from the reference run: every backend call is a
    # version screening with a candidate attached, never a bare baseline.
    assert backend.calls
    assert all(call["registry"]["interventions"] for call in backend.calls)


def test_route_c_receives_full_packet_and_route_b_does_not(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_valid_candidate()]),
        ProposalBatch(candidates=[_valid_candidate()]),
    ])
    run_research(manifest, tmp_path / "run", backend, proposer)
    packet_events = [
        payload for kind, payload in proposer.received if kind == "packet"
    ]
    assert packet_events[0] is None  # route B drafts without a packet
    packet = packet_events[1]
    assert packet["mechanism"]["name"] == MECHANISM["name"]
    # The packet carries post-projection records only: no gold, no answers.
    texts = json.dumps(packet, ensure_ascii=False)
    assert "hidden-answer-must-not-leak" not in texts
    assert "is_hallucination" not in texts
    training = packet["training_cases"]
    assert [item["record"]["question"] for item in training] == [
        "task text for BFCL/002.json", "task text for BFCL/153.json",
    ]


def test_first_round_contract_rejects_other_types_and_hooks(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    wrong_type = _valid_candidate()
    wrong_type["type"] = "decision_guard"
    wrong_hook = _valid_candidate()
    wrong_hook["hook"] = "judge.post"
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[wrong_type, wrong_hook]),
        ProposalBatch(candidates=[wrong_hook]),
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["next_stage"] is None
    versions = report["versions"]
    assert all(not v["has_candidate"] for v in versions)
    notes = json.loads(
        (tmp_path / "run" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )["notes"]
    rejected = {note.get("rejected") for note in notes}
    assert "research_type_not_allowed" in rejected
    assert "research_hook_not_allowed" in rejected
    # No candidate survived, so no evaluation budget was spent at all.
    assert report["budget"]["used"]["screening_case_evaluations"] == 0


def test_case_id_leakage_is_rejected_before_evaluation(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    leaky = _valid_candidate()
    leaky["body"]["directive"] = (
        "Always compare the delivered content with the quoted line for "
        "BFCL/002.json before voting."
    )
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[leaky]),
        ProposalBatch(candidates=[leaky]),
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["next_stage"] is None
    assert backend.calls == []
    assert report["budget"]["used"]["screening_case_evaluations"] == 0


def test_revision_records_parent_and_uses_feedback(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    ineffective = _valid_candidate()
    ineffective["body"]["directive"] = "Read the task text carefully before voting."
    proposer = FakeProposer([
        # Calls happen in execution order: B draft, B revision, C draft,
        # C revision.
        ProposalBatch(candidates=[ineffective]),   # B draft (no repair)
        ProposalBatch(candidates=[_route_candidate("B")]),  # B revision
        ProposalBatch(candidates=[_candidate_with("Yet another weaker directive.")]),  # C draft
        ProposalBatch(candidates=[_route_candidate("C")]),  # C revision
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "screening_complete"
    assert report["next_stage"] == "frozen_paired_recheck"
    assert report["budget"]["used"]["screening_case_evaluations"] == 16
    assert report["budget"]["used"]["proposer_calls"] == {"B": 2, "C": 2}
    # The selected versions are the revisions, and they record the parent.
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["B"]["selected"] == "v002"
    revision = json.loads(
        (tmp_path / "run" / "v002" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert revision["kind"] == "revision"
    assert revision["parent_hash"]
    diagnosis = json.loads(
        (tmp_path / "run" / "v002" / "diagnosis.json").read_text(
            encoding="utf-8",
        ),
    )
    assert "not repaired" in " ".join(diagnosis["reasons"])
    # Route B's revision went through rejection feedback, not the packet.
    feedback_events = [
        payload for kind, payload in proposer.received if kind == "feedback"
    ]
    assert feedback_events[0] == []  # B draft: no feedback yet
    assert feedback_events[1][0]["gate"] == "RESEARCH_SCREEN"


def test_target_misaligned_repair_fails_screening(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    # The candidate flips both failures but localizes them at the wrong step.
    backend = FakeBackend(target_steps={"BFCL/002.json": 99, "BFCL/153.json": 99})
    proposer = FakeProposer([
        ProposalBatch(candidates=[_valid_candidate()]),
        ProposalBatch(candidates=[_valid_candidate()]),
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["next_stage"] is None
    v1 = json.loads(
        (tmp_path / "run" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert v1["verdict"]["passed"] is False
    training = v1["verdict"]["training"]
    assert all(not item["repaired"] for item in training.values())
    assert all(not item["target_aligned"] for item in training.values())


def test_screening_budget_exhaustion_stops_partial(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[baseline],
        budget={
            "baseline_case_evaluations": 0,
            "screening_case_evaluations": 4,  # exactly route B's draft
            "proposer_calls_per_route": 3,
            "max_versions": 4,
            "workers": 1,
        },
    )
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_valid_candidate()]),
        # A distinct candidate for route C: it needs real evaluations, which
        # the exhausted budget must refuse.
        ProposalBatch(candidates=[_route_candidate("C")]),
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "partial"
    assert report["stopped"]
    # Route B's version is complete and judged; route C never spent budget.
    assert report["budget"]["used"]["screening_case_evaluations"] == 4
    assert report["routes"]["C"]["versions"] == []


def test_resume_reuses_stored_results_without_new_backend_calls(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
        ProposalBatch(candidates=[_route_candidate("C")]),
    ])
    first = run_research(manifest, tmp_path / "run", backend, proposer)
    assert first["budget"]["used"]["screening_case_evaluations"] == 8
    calls_after_first_run = len(backend.calls)
    # A resumed run with identical inputs redrafts (drafting is not
    # deterministic) but must reuse the stored evaluation results: the new
    # version's identity matches the stored one, so nothing is re-run.
    second_backend = FakeBackend()
    # The resumed run drafts the identical candidates again.
    second_proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
        ProposalBatch(candidates=[_route_candidate("C")]),
    ])
    second = run_research(manifest, tmp_path / "run", second_backend, second_proposer)
    assert second["status"] == "screening_complete"
    assert second["next_stage"] == "frozen_paired_recheck"
    # The identical candidate's stored results were found and reused, so the
    # resumed run spent nothing on the backend.
    assert second_backend.calls == []
    assert second["budget"]["used"]["screening_case_evaluations"] == 0
    resumed = json.loads(
        (tmp_path / "run" / "v003" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert resumed["evaluation"]["reused"] is True
    assert resumed["evaluation"]["source_version"] == "v001"


def test_identity_mismatch_forces_new_version_not_overwrite(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
        ProposalBatch(candidates=[_route_candidate("C")]),
    ])
    run_research(manifest, tmp_path / "run", backend, proposer)
    # Re-evaluating an existing version slot with a changed candidate must
    # be refused: identity is immutable once results exist.
    run = ResearchRun(
        manifest, tmp_path / "run", FakeBackend(), FakeProposer([]),
    )
    run.frozen_manifest = run.freeze_manifest()
    changed = _candidate_with("A different directive entirely.")
    with pytest.raises(ValueError, match="different evaluation identity"):
        run._evaluate_version("B", "v001", changed)


def test_fresh_baseline_fallback_spends_baseline_budget(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[],
        budget={
            "baseline_case_evaluations": 4,
            "screening_case_evaluations": 16,
            "proposer_calls_per_route": 3,
            "max_versions": 4,
            "workers": 1,
        },
    )
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_valid_candidate()]),
        ProposalBatch(candidates=[_valid_candidate()]),
    ])
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "screening_complete"
    assert report["budget"]["used"]["baseline_case_evaluations"] == 4
    # The fresh baseline runs the bare registry (no candidate attached).
    assert backend.calls[0]["registry"]["interventions"] == []
