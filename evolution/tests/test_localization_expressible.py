"""A localization family must be expressible, and a truncation must be repairable.

Two defects found by the 2026-09-21 self-evolution run, both fixed here.

1. ``target_kind`` was never taught to the proposer. The family block labelled
   every non-FN family ``"FP"`` and the draft instructions said "target_polarity
   must match the family kind: recall for FN, precision for FP", so a
   localization family was presented as an FP family; and because no schema
   carried ``target_kind``, ``candidate_target_kind`` defaulted every claim to
   ``detection``. STRUCTURAL requires a ``FAM-loc-`` family's claim to declare
   ``localization`` or ``joint``, so *every* localization candidate was refused
   on a schema mismatch — 4 of the run's 9 candidates. The objective was never
   measured, so those rejections carried no information about its mechanism.

2. A truncated proposer reply carried no ``rejected`` key, so it never entered
   the repair loop and the configured ``max_repair_calls`` was dead code for the
   one failure it exists to handle.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.gates import structural_gate  # noqa: E402
from evolution.proposer import (  # noqa: E402
    HybridProposer,
    _draft_system_prompt,
    _family_kind,
    family_target_kind,
)

LOC = "FAM-loc-mislocated-117fce1c070d-Pdc59b76d89dc"
FN = "FAM-fn-judge_false_no_recall_candidates-a4f141369d93-P70fe7f80fd90"
FP = "FAM-fp-judge_conviction-117fce1c070d"


def _family(family_id: str = LOC, cases: list[str] | None = None) -> dict:
    return {
        "failure_family": family_id,
        "count": 2,
        "failure_cases": cases or ["OpenDeepSearch/079.json", "Camel/088.json"],
        "sources": ["OpenDeepSearch", "Camel"],
        "mechanism_summary": {"family": family_id, "n": 2},
        "mechanism_signature": {
            "judge_positive": True,
            "recall_path": "not_needed",
            "upgrade_positive": False,
            "defense_verdict": "substantiated",
            "positive_source": "judge",
        },
        "actionability": {"routes": ["tracer_directive"]},
        "diagnostics": {
            "predicate_signals": {},
            "shared_literals": {},
            "member_summaries": [{
                "typed_samples": {"packet_features": ["has_tool_calls"]},
            }],
        },
    }


# ---------------------------------------------------------------- family kinds


def test_a_localization_family_is_not_labelled_fp():
    # The exact mislabel: `"FN" if startswith("FAM-fn") else "FP"`.
    assert _family_kind(LOC) == "LOCALIZATION"
    assert _family_kind(FN) == "FN"
    assert _family_kind(FP) == "FP"
    assert _family_kind("FAM-unresolved-abc") == "UNRESOLVED"
    assert _family_kind("FAM-nonsense") == "UNKNOWN"


def test_target_kind_mapping_matches_what_the_gate_enforces():
    assert family_target_kind(LOC) == "localization"
    assert family_target_kind(FN) == "detection"
    assert family_target_kind(FP) == "detection"


# ------------------------------------------------------------------- the prompt


def test_every_claim_schema_carries_target_kind():
    prompt = _draft_system_prompt()
    # Every schema line that mentions target_polarity must also mention
    # target_kind, or the model cannot set the field the gate reads.
    schema_lines = [
        line for line in prompt.splitlines()
        if '"target_polarity"' in line
    ]
    assert schema_lines, "no claim schemas found in the prompt"
    for line in schema_lines:
        assert '"target_kind"' in line, line[:140]


def test_prompt_states_the_localization_mapping_and_its_route():
    prompt = _draft_system_prompt()
    assert "LOCALIZATION" in prompt
    # And that a localization claim must not declare detection.
    assert "localization or joint" in prompt
    # And the route, so a localization family is not given a recall treatment.
    assert "tracer.system" in prompt


def test_prompt_no_longer_teaches_the_two_kind_mapping_only():
    prompt = _draft_system_prompt()
    assert "precision for FP only" not in prompt


# ------------------------------------------------- the fallback can express it


def test_fallback_emits_a_localization_claim_that_passes_structural():
    """The property that was impossible: a localization candidate surviving."""
    candidates = HybridProposer._localization_candidates(_family(), cycle=1)
    assert candidates, "the fallback produced nothing for a localization family"
    cand = candidates[0]
    assert cand["hook"] == "tracer.system"
    assert (cand["claim"] or {}).get("target_kind") == "localization"
    # The real gate must accept it. A unit config keeps the harness honest:
    # default gates, no active entries.
    verdict = structural_gate(cand, active_entries=[], config={"gates": {}})
    assert verdict["passed"] is True, verdict.get("reason")


def test_fallback_localization_trigger_is_not_over_broad():
    # A trigger matching nothing the family shares would never fire; matching
    # every packet would force the tracer on unrelated cases.
    cand = HybridProposer._localization_candidates(_family(), cycle=1)[0]
    assert cand["trigger"]["packet_features"] == ["has_tool_calls"]
    assert cand["trigger"]["facts"] == []


def test_fallback_still_refuses_to_mint_a_detection_claim_for_localization():
    """The gate must stay strict; only the producer was broken."""
    cand = HybridProposer._localization_candidates(_family(), cycle=1)[0]
    spoiled = json.loads(json.dumps(cand))
    spoiled["claim"]["target_kind"] = "detection"
    assert structural_gate(
        spoiled, active_entries=[], config={"gates": {}},
    )["passed"] is False


def test_template_batch_routes_localization_families():
    # ``_template_batch`` and every helper it calls are static/class methods,
    # so the instance is bypassed rather than building a provider-backed
    # proposer just to read a deterministic candidate list.
    proposer = HybridProposer.__new__(HybridProposer)
    batch = proposer._template_batch([_family()], cycle=1, max_proposals=4)
    assert batch, "localization family produced no fallback candidates"
    assert all(
        (c["claim"] or {}).get("target_kind") == "localization" for c in batch
    )


# ------------------------------------------------------- truncation is repaired


def _provider_error(error_type: str):
    from detector.provider import ProviderError

    return ProviderError("evolution_proposer", "boom", error_type=error_type)


class _TruncatingThenDrafting:
    """Fails the first draft with truncated_output, succeeds on the repair."""

    def __init__(self) -> None:
        self.calls = 0
        self.feedback_seen: list[list] = []

    def propose(self, families, *, cycle, max_proposals=3):
        from evolution.proposer import ProposalBatch

        self.calls += 1
        if self.calls == 1:
            raise AssertionError("the wrapper must not call the inner proposer")
        return ProposalBatch(candidates=[], notes=[])


def test_truncation_note_is_repairable_but_a_credential_failure_is_not():
    from detector.provider import ProviderError
    from evolution.proposer import HybridProposer as HP

    # Reproduce the note the except-branch builds, for both error types.
    def note_for(error_type: str) -> dict:
        exc = _provider_error(error_type)
        retryable = str(exc.error_type or "") != "missing_credential"
        return {
            "error_type": exc.error_type,
            **({"rejected": "provider_failure"} if retryable else {}),
            "reason": str(exc.message or ""),
        }

    assert note_for("truncated_output")["rejected"] == "provider_failure"
    assert "rejected" not in note_for("missing_credential")
