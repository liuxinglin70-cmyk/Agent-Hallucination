"""Offline tests for the bounded-revision research entry (doc §8).

Everything runs on mocks: no provider, no real detector, no label file. The
mock backend models a bare-base FN pair plus two clean neighbors, and flips
the failures only when the candidate registry is actually supplied — so a
candidate that never reaches evaluation cannot fake a repair.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from evolution.proposer import ProposalBatch
from evolution.research import (
    canonical_hash,
    ResearchRun,
    run_frozen_repeats,
    run_research,
)


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
    surface=None,
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
        "intervention_surface": surface,
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


def test_resume_skips_routes_that_already_froze_a_passing_version(tmp_path):
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
    assert first["next_stage"] == "frozen_paired_recheck"
    # A resumed run skips both routes: each already froze a passing version.
    second_backend = FakeBackend()
    second_proposer = FakeProposer([])
    second = run_research(
        manifest, tmp_path / "run", second_backend, second_proposer,
    )
    assert second["status"] == "screening_complete"
    assert second["next_stage"] == "frozen_paired_recheck"
    assert second_backend.calls == []
    assert second["budget"]["used"]["screening_case_evaluations"] == 0
    assert second["budget"]["used"]["proposer_calls"] == {}
    assert len(second["versions"]) == len(first["versions"])


def test_resume_redrafts_a_route_starved_by_infrastructure(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    # Route B passes; route C's draft never reaches the provider (the live
    # per-cycle budget bug this test guards against).
    starved = ProposalBatch(notes=[{
        "generator": "llm",
        "provider_called": False,
        "reason": "llm_calls budget exhausted for this cycle",
    }])
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
        starved,
    ])
    first = run_research(manifest, tmp_path / "run", backend, proposer)
    assert first["status"] == "screening_complete"
    assert first["routes"]["C"]["selected"] is None
    # The resumed run redrafts route C; its candidate is evaluated fresh.
    second_backend = FakeBackend()
    second_proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("C")]),
    ])
    second = run_research(
        manifest, tmp_path / "run", second_backend, second_proposer,
    )
    assert second["status"] == "screening_complete"
    assert second["routes"]["C"]["selected"] is not None
    assert second["budget"]["used"]["screening_case_evaluations"] == 4
    selected = second["routes"]["C"]["selected"]
    record = json.loads(
        (tmp_path / "run" / selected / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert record["route"] == "C"
    assert record["candidate"] and record["verdict"]["passed"]


def test_resume_does_not_redraft_after_a_real_empty_provider_return(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    empty = ProposalBatch(notes=[{
        "generator": "llm",
        "provider_called": True,
        "provider_ok": True,
        "reason": "provider interventions field is not a list",
    }])
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
        empty,
    ])
    first = run_research(manifest, tmp_path / "run", backend, proposer)
    assert first["routes"]["C"]["selected"] is None
    # A real empty return is terminal: the resumed run must not re-draft.
    second_backend = FakeBackend()
    second_proposer = FakeProposer([])
    second = run_research(
        manifest, tmp_path / "run", second_backend, second_proposer,
    )
    assert second["routes"]["C"]["selected"] is None
    assert second_backend.calls == []
    assert second["budget"]["used"]["proposer_calls"] == {}


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


def _finish_screening(tmp_path):
    """Screening run that leaves a passing version selected for route B."""
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(tmp_path, dataset_root, baseline_runs=[baseline])
    backend = FakeBackend()
    proposer = FakeProposer([
        ProposalBatch(candidates=[_candidate_with(
            "Read the task text carefully before voting.",
        )]),  # B draft: mechanism-irrelevant, fails
        ProposalBatch(candidates=[_route_candidate("B")]),  # B revision passes
        ProposalBatch(),  # C draft: real empty return (terminal)
    ])
    first = run_research(manifest, tmp_path / "run", backend, proposer)
    assert first["routes"]["B"]["selected"] == "v002"
    assert first["routes"]["C"]["selected"] is None
    return manifest_path(tmp_path), tmp_path / "run"


def manifest_path(tmp_path):
    return tmp_path / "research_manifest.json"


def test_frozen_recheck_reports_every_repetition(tmp_path):
    _, run_dir = _finish_screening(tmp_path)
    meta = {
        case["case_id"]: {
            "raw_gold": bool(case["raw_gold"]),
            "target_step": case.get("target_step"),
            "role": case["role"],
        }
        for case in CASES
    }
    backend = FakeBackend()
    report = run_frozen_repeats(
        manifest_path(tmp_path), run_dir, backend,
        stage="frozen_recheck", case_ids=[c["case_id"] for c in CASES],
        case_meta=meta, repetitions=2, budget_case_evaluations=24,
        workers=1,
    )
    assert report["status"] == "complete"
    h0 = report["arms"]["h0"]
    assert h0["passed"] is None  # contrast arm, not a gate
    # H0 misses both training failures in both repetitions.
    for rep in h0["per_repetition"]:
        assert all(not item["repaired"] for item in rep["training"].values())
    winner = report["arms"]["v002"]
    assert winner["passed"] is True
    assert winner["repetitions"] == 2
    for rep in winner["per_repetition"]:
        assert all(item["repaired"] for item in rep["training"].values())
        assert all(item["clean"] for item in rep["neighbors"].values())
    # 2 arms x 4 cases x 2 repetitions, all fresh.
    assert report["budget"]["used"] == 16
    assert (run_dir / "frozen_recheck" / "report.json").exists()
    assert (run_dir / "frozen_recheck" / "v002" / "rep01" / "identity.json").exists()


def test_frozen_recheck_fails_when_any_repetition_degrades(tmp_path):
    _, run_dir = _finish_screening(tmp_path)

    class FlipFlopBackend(FakeBackend):
        """Alternates the neighbor outcome: one bad repetition fails all."""

        def __init__(self):
            super().__init__()
            self.toggle = 0

        def evaluate_fresh(self, cases, **kwargs):
            self.toggle += 1
            if self.toggle % 2 == 0:
                registry = kwargs.get("registry")
                # Poison one neighbor on alternating calls to model an
                # unstable candidate: the FP appears on the second call.
                rows = super().evaluate_fresh(cases, **kwargs)
                for row in rows:
                    if row["case"] == "BFCL/020.json":
                        row["state"] = "verified_error"
                        row["prediction"] = True
                        row["result"]["state"] = "verified_error"
                return rows
            return super().evaluate_fresh(cases, **kwargs)

    meta = {
        case["case_id"]: {
            "raw_gold": bool(case["raw_gold"]),
            "target_step": case.get("target_step"),
            "role": case["role"],
        }
        for case in CASES
    }
    report = run_frozen_repeats(
        manifest_path(tmp_path), run_dir, FlipFlopBackend(),
        stage="frozen_recheck", case_ids=[c["case_id"] for c in CASES],
        case_meta=meta, repetitions=2, budget_case_evaluations=24,
        workers=1,
    )
    winner = report["arms"]["v002"]
    assert winner["passed"] is False
    flags = [rep["passed"] for rep in winner["per_repetition"]]
    assert flags == [True, False]


def test_unused_dev_validation_uses_frozen_extra_cases(tmp_path):
    _, run_dir = _finish_screening(tmp_path)
    extra = [
        {"case_id": "BFCL/008.json", "role": "regression", "raw_gold": True},
        {"case_id": "BFCL/013.json", "role": "boundary", "raw_gold": False},
    ]
    meta = {
        c["case_id"]: {"raw_gold": c["raw_gold"], "role": c["role"],
                       "target_step": None}
        for c in extra
    }
    backend = FakeBackend()
    report = run_frozen_repeats(
        manifest_path(tmp_path), run_dir, backend,
        stage="unused_dev_validation", case_ids=[c["case_id"] for c in extra],
        case_meta=meta, repetitions=2, budget_case_evaluations=32,
        workers=1,
    )
    assert report["status"] == "complete"
    winner = report["arms"]["v002"]
    # Regression stays repaired; the boundary case (no quoted content) stays
    # clean: the not_when protected it.
    for rep in winner["per_repetition"]:
        assert rep["neighbors"]["BFCL/013.json"]["clean"] is True
    assert report["budget"]["used"] == 8


# ---------------------------------------------------------------------------
# seed_candidate: re-measuring a frozen candidate without re-drafting

def _finished_source_run(tmp_path, name="source_run"):
    """A screening run with one passing route-B version on disk."""
    dataset_root = _write_dataset(tmp_path / name)
    baseline = _write_baseline_ref(tmp_path / name, dataset_root)
    manifest = _write_manifest(
        tmp_path / name, dataset_root, baseline_runs=[baseline],
        routes=("B",),
    )
    proposer = FakeProposer([
        ProposalBatch(candidates=[_route_candidate("B")]),
    ])
    report = run_research(
        manifest, tmp_path / name / "run", FakeBackend(), proposer,
    )
    assert report["routes"]["B"]["selected"] == "v001"
    return tmp_path / name / "run"


def _seed_manifest(tmp_path, dataset_root, baseline_runs, source_run, *,
                   candidate_hash, budget=None):
    config_path = tmp_path / "detector_config_seed.json"
    config_path.write_text(json.dumps({"model": "fake-model"}), encoding="utf-8")
    manifest = {
        "schema_version": "evolution.research_manifest.v1",
        "run_id": "seed_run",
        "mechanism": MECHANISM,
        "family": FAMILY,
        "allowed_interventions": {"type": "text_boundary", "hooks": ["judge.system"]},
        "dataset_root": str(dataset_root),
        "detector_config": str(config_path),
        "cases": CASES,
        "routes": ["B"],
        "baseline_ref": {
            "runs": [{"run_dir": str(run)} for run in baseline_runs],
            "detector_config_hash": "cfg-hash-1",
        },
        "seed_candidate": {
            "source_run": str(source_run),
            "version": "v001",
            "candidate_hash": candidate_hash,
            "route": "B",
        },
        "budget": budget or {
            "baseline_case_evaluations": 0,
            "screening_case_evaluations": 4,
            "proposer_calls_per_route": 0,
            "max_versions": 2,
            "workers": 1,
        },
    }
    path = tmp_path / "seed_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_seed_candidate_is_screened_and_selected_without_proposer(tmp_path):
    source_run = _finished_source_run(tmp_path)
    source_record = json.loads(
        (source_run / "v001" / "candidate.json").read_text(encoding="utf-8"),
    )
    pinned = canonical_hash(source_record["candidate"])
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _seed_manifest(
        tmp_path, dataset_root, [baseline], source_run,
        candidate_hash=pinned,
    )
    backend = FakeBackend()
    proposer = FakeProposer([])  # must never be called: budget is 0 anyway
    report = run_research(manifest, tmp_path / "seeded", backend, proposer)
    assert report["status"] == "screening_complete"
    selection = json.loads(
        (tmp_path / "seeded" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["B"]["selected"] == "v001"
    assert report["budget"]["used"]["proposer_calls"] == {}
    # The seed was re-screened under this run's own identity (fresh results).
    assert backend.calls
    assert all(call["registry"]["interventions"] for call in backend.calls)
    record = json.loads(
        (tmp_path / "seeded" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert record["candidate_hash"] == pinned
    assert record["notes"][0]["seeded_from"]["candidate_hash"] == pinned
    # No families ever reached the proposer: no draft happened.
    assert not [kind for kind, _ in proposer.received if kind == "families"]


def test_seed_candidate_hash_mismatch_fails_closed(tmp_path):
    source_run = _finished_source_run(tmp_path)
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _seed_manifest(
        tmp_path, dataset_root, [baseline], source_run,
        candidate_hash="0" * 64,
    )
    report = run_research(
        manifest, tmp_path / "seeded", FakeBackend(), FakeProposer([]),
    )
    assert report["status"] == "partial"
    assert "hash mismatch" in (report["stopped"] or "")
    # Fail closed before any evaluation budget was spent.
    assert report["budget"]["used"]["screening_case_evaluations"] == 0
    assert not (tmp_path / "seeded" / "v001" / "candidate.json").exists()


def test_failed_seed_terminates_route_without_revision(tmp_path):
    source_run = tmp_path / "failing_source"
    dataset_root = _write_dataset(source_run)
    baseline = _write_baseline_ref(source_run, dataset_root)
    source_manifest = _write_manifest(
        source_run, dataset_root, baseline_runs=[baseline], routes=("B",),
    )
    # A mechanism-irrelevant candidate cannot repair the training failures.
    irrelevant = _candidate_with("Read the task text carefully before voting.")
    proposer = FakeProposer([ProposalBatch(candidates=[irrelevant])])
    run_research(source_manifest, source_run / "run", FakeBackend(), proposer)
    source_record = json.loads(
        (source_run / "run" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    dataset_root2 = _write_dataset(tmp_path)
    baseline2 = _write_baseline_ref(tmp_path, dataset_root2)
    manifest = _seed_manifest(
        tmp_path, dataset_root2, [baseline2], source_run / "run",
        candidate_hash=canonical_hash(source_record["candidate"]),
    )
    report = run_research(
        manifest, tmp_path / "seeded", FakeBackend(), FakeProposer([]),
    )
    # The seed failed screening; with zero proposer budget the route ends
    # without a selection instead of inventing a revision.
    selection = json.loads(
        (tmp_path / "seeded" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["B"]["selected"] is None
    assert report["next_stage"] is None


# ---------------------------------------------------------------------------
# intervention_surface widening + joint candidates (later-round protocol)

def _defense_entry():
    entry = _valid_candidate()
    entry["intervention_id"] = "IV-R-002-DEF"
    entry["hook"] = "defense.system"
    entry["body"] = {
        "directive": (
            "When a hearing concerns quoted-content binding, also check "
            "that the delivered argument matches the quoted text and treat "
            "unquoted additions as content deviation."
        ),
        "not_when": (
            "Never convict re-presentation of the agent's own tool "
            "observations (stripping prefixes, renumbering): that is "
            "presentation, not content deviation."
        ),
    }
    return entry


def test_joint_candidate_deploys_all_entries_on_widened_surface(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[baseline], routes=("C",),
        surface={
            "types": ["text_boundary"],
            "hooks": ["judge.system", "defense.system"],
            "max_entries": 2,
        },
    )
    proposer = FakeProposer([
        ProposalBatch(candidates=[_valid_candidate(), _defense_entry()]),
    ])
    backend = FakeBackend()
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "screening_complete"
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["C"]["selected"] == "v001"
    record = json.loads(
        (tmp_path / "run" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    # The joint candidate is a two-entry list deployed as one version.
    assert isinstance(record["candidate"], list)
    assert len(record["candidate"]) == 2
    assert {e["hook"] for e in record["candidate"]} == {
        "judge.system", "defense.system",
    }
    # Both entries were live in the evaluation registry.
    assert all(
        len(call["registry"]["interventions"]) == 2 for call in backend.calls
    )


def test_undeclared_hook_rejected_without_surface_widening(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[baseline], routes=("C",),
    )
    # Defense entry comes first: it is examined, rejected as undeclared,
    # and the judge entry alone is screened as a single-entry candidate.
    proposer = FakeProposer([
        ProposalBatch(candidates=[_defense_entry(), _valid_candidate()]),
    ])
    report = run_research(manifest, tmp_path / "run", FakeBackend(), proposer)
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["C"]["selected"] == "v001"
    record = json.loads(
        (tmp_path / "run" / "v001" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert isinstance(record["candidate"], dict)
    assert record["candidate"]["hook"] == "judge.system"
    assert any(
        note.get("rejected") == "research_hook_not_allowed"
        for note in record["notes"]
    )


def test_joint_candidate_over_max_entries_fails_closed(tmp_path):
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[baseline], routes=("C",),
        surface={
            "types": ["text_boundary"],
            "hooks": ["judge.system", "defense.system", "scanner.system"],
            "max_entries": 2,
        },
    )
    third = _defense_entry()
    third["intervention_id"] = "IV-R-003-DEF"
    third["hook"] = "scanner.system"
    proposer = FakeProposer([
        ProposalBatch(candidates=[
            _valid_candidate(), _defense_entry(), third,
        ]),
    ])
    # All three entries are surface-valid, so the batch exceeds max_entries
    # and must fail closed instead of being trimmed to two.
    report = run_research(manifest, tmp_path / "run", FakeBackend(), proposer)
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["C"]["selected"] is None
    assert report["next_stage"] is None


# ---------------------------------------------------------------------------
# joint-candidate revision path (list parent through _revise)

def test_joint_candidate_revision_keeps_both_entries(tmp_path):
    """A failing joint candidate is revised as a joint candidate.

    R4-R6 saw joint candidates pass on the first screen, so this path had
    no live coverage: a list parent must survive the diagnosis packet, the
    parent record and the revised candidate record without being flattened
    into a single mapping.
    """
    dataset_root = _write_dataset(tmp_path)
    baseline = _write_baseline_ref(tmp_path, dataset_root)
    manifest = _write_manifest(
        tmp_path, dataset_root, baseline_runs=[baseline], routes=("C",),
        surface={
            "types": ["text_boundary"],
            "hooks": ["judge.system", "defense.system"],
            "max_entries": 2,
        },
    )
    # Draft is mechanism-irrelevant (fails screening); the revision carries
    # the quote operation in BOTH entries so the joint revision passes.
    failing = _candidate_with("Read the task text carefully before voting.")
    failing_defense = _defense_entry()
    failing_defense["body"]["directive"] = "Weigh testimony before voting."
    revised_defense = _defense_entry()
    proposer = FakeProposer([
        ProposalBatch(candidates=[failing, failing_defense]),
        ProposalBatch(candidates=[_valid_candidate(), revised_defense]),
    ])
    backend = FakeBackend()
    report = run_research(manifest, tmp_path / "run", backend, proposer)
    assert report["status"] == "screening_complete"
    selection = json.loads(
        (tmp_path / "run" / "selection.json").read_text(encoding="utf-8"),
    )
    assert selection["routes"]["C"]["selected"] == "v002"
    parent = json.loads(
        (tmp_path / "run" / "v002" / "parent.json").read_text(
            encoding="utf-8",
        ),
    )
    assert isinstance(parent["parent_candidate"], list)
    assert len(parent["parent_candidate"]) == 2
    revised = json.loads(
        (tmp_path / "run" / "v002" / "candidate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert isinstance(revised["candidate"], list)
    assert len(revised["candidate"]) == 2
    assert {e["hook"] for e in revised["candidate"]} == {
        "judge.system", "defense.system",
    }
    # Both revision entries were live in the evaluation registry.
    assert all(
        len(call["registry"]["interventions"]) == 2 for call in backend.calls
    )


@pytest.mark.parametrize('consumer', ['baseline', 'screening'])
def test_research_rejects_source_label_drift_before_persisting(tmp_path, monkeypatch, consumer):
    dataset_root = _write_dataset(tmp_path)
    manifest = _write_manifest(tmp_path, dataset_root, budget={'baseline_case_evaluations': 4, 'screening_case_evaluations': 16, 'proposer_calls_per_route': 3, 'max_versions': 4, 'workers': 1})
    backend = FakeBackend()
    run = ResearchRun(manifest, tmp_path / 'run', backend, FakeProposer([]))
    run.frozen_manifest = run.freeze_manifest()
    evaluate = backend.evaluate_fresh

    def observe(cases, **kwargs):
        rows = evaluate(cases, **kwargs)
        run.gold_map[cases[0]] = not run.gold_map[cases[0]]
        return rows
    monkeypatch.setattr(backend, 'evaluate_fresh', observe)
    with pytest.raises(RuntimeError, match='inputs changed'):
        if consumer == 'baseline':
            run.resolve_baseline()
        else:
            run._evaluate_version('B', 'v001', _valid_candidate())
    assert not list((tmp_path / 'run').glob('baseline/*.json'))
    assert not list((tmp_path / 'run').rglob('results/*.json'))
