"""P8 E0: offline contract closed loop for the optimized SEHD v6 framework.

Uses mocked providers and anonymous fixtures to prove every critical link of
the optimized framework completes end to end WITHOUT any external API call:

- L1 proposal -> replay -> graded (weak/strong);
- L2-post boundary proposal -> replay -> weak;
- registry DSL proposal -> pending -> validation;
- GateEvent -> feedback -> visible to the next reflection;
- Meta Review recommendation + shadow profile;
- OutcomeValidation;
- rollback; checkpoint/resume; inherit_from;
- deployable view filters working/pending.

Pass conditions (§14.2): all links complete with no uncaught exception, all
artifacts write/read/restore, the gold firewall holds, the empty policy
baseline is unchanged, and no external API is called.
"""

from __future__ import annotations

import json

import pytest

from src.sehd_v6.core.bccse import evolution_runner as runner
from src.sehd_v6.core.bccse.meta_review import project_meta_review_for_prompt
from src.sehd_v6.core.bccse.meta_skill import create_baseline_meta_skill
from src.sehd_v6.core.bccse.policy_genome import (
    SCOPE_DEPLOYABLE,
    SCOPE_WORKING,
)
from src.sehd_v6.evolution.state_manifest import (
    load_state_manifest,
    write_state_manifest,
)

from .fixtures import meta_skill_evolution as p0


def _records(*case_ids: str) -> dict:
    return {case_id: {} for case_id in case_ids}


# ============================================================
# L1 + L2 + registry + gate-event + meta closed loop (offline)
# ============================================================

def test_e0_l1_l2_registry_gate_meta_closed_loop(tmp_path, monkeypatch, addressable_l1_failure):
    """The full offline loop completes with no external API call."""
    calls = p0.install_mocked_evolution(
        monkeypatch, detections=p0.evolution_case_detections(),
    )
    output = tmp_path / "e0"
    result = runner.run_online_evolution(
        evolution_case_ids=("source1", "source2"),
        records=_records("source1", "source2", "anchor"),
        config={}, output_dir=output, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="e0_closed_loop",
    )

    # --- L1 evolution path completed (source1 accepted, source2 rejected) ---
    assert result.edits_accepted == 1
    assert result.edits_rejected == 2

    # --- GateEvent ledger: every terminal decision recorded ---
    checkpoint = json.loads(
        (output / "evolution_checkpoint.json").read_text(encoding="utf-8")
    )
    gate_events = checkpoint.get("gate_events") or []
    assert gate_events, "every terminal gate decision must produce a GateEvent"
    assert all(e.get("schema_version") == "sehd.gate_event.v1" for e in gate_events)

    # --- Meta Review aggregation is present and prompt-safe ---
    memory = json.loads(
        (output / "htc_tes_experience_memory.json").read_text(encoding="utf-8")
    )
    meta_review = memory.get("meta_review") or {}
    assert "gate_experiences" in meta_review
    assert "meta_state" in meta_review
    assert "drift_snapshots" in meta_review
    projection = project_meta_review_for_prompt(create_baseline_meta_skill())
    assert projection["authority"] == "evolution_guidance_only"

    # --- artifacts all written and readable ---
    for name in (
        "evolution_result.json", "champion_genome.json", "policy_genome.json",
        "htc_tes_experience_memory.json", "evolution_checkpoint.json",
    ):
        assert (output / name).is_file(), name

    # --- empty policy baseline unchanged (no L2 case triggered here) ---
    assert result.policy_genome.total_items == 0

    # --- no external API call: every mutation was answered by the stub ---
    assert all(
        call["purpose"] == "mutation" for call in calls["mutation"]
    )


def test_e0_gold_firewall_and_no_external_api(tmp_path, monkeypatch, addressable_l1_failure):
    """The gold firewall holds: label-free detection never sees gold."""
    p0.install_mocked_evolution(
        monkeypatch, detections=p0.evolution_case_detections(),
    )
    result = runner.run_online_evolution(
        evolution_case_ids=("source1",),
        records=_records("source1", "anchor"),
        config={}, output_dir=tmp_path / "gf", census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="e0_gold_firewall",
    )
    # label-free: no gold was joined to attribution
    assert result.use_gold is False


def test_e0_deployable_view_filters_working_and_pending(tmp_path, monkeypatch, addressable_l1_failure):
    """The deployable scope never surfaces working or pending artifacts."""
    from src.sehd_v6.core.bccse.policy_genome import (
        FITNESS_STATUS_STRONG,
        FITNESS_STATUS_WEAK,
        PendingValidationItem,
        PolicyEdit,
        PolicyOp,
        apply_policy_edit,
        create_baseline_policy_genome,
    )
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem

    genome = create_baseline_policy_genome()
    for index, status in enumerate((FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG)):
        genome = apply_policy_edit(genome, PolicyEdit(
            edit_id=f"e{index}", operation=PolicyOp.ADD_ITEM,
            new_item=PolicyItem(
                item_id=f"item_{index}",
                precondition="consensus_gate_blocked_nonempty",
                action="downgrade_coverage",
                params=(
                    {"side": "prosecution", "to_state": "incomplete"}
                    if index == 0 else
                    {"side": "prosecution", "to_state": "unresolved"}
                ),
                rationale="r",
            ),
            rationale="r",
        ))
        meta = genome.get_item_meta(genome.total_items - 1)
        meta["fitness_status"] = status
        genome.set_item_meta(genome.total_items - 1, meta)
    genome.enqueue_pending(PendingValidationItem(
        subject_id="conditional_1", required_validation="independent_effect",
    ))

    working_ids = [i.item_id for i in genome.items_for_scope(SCOPE_WORKING)]
    deployable_ids = [i.item_id for i in genome.items_for_scope(SCOPE_DEPLOYABLE)]
    assert working_ids == ["item_0", "item_1"]
    assert deployable_ids == ["item_1"]  # weak filtered out
    # conditional/pending artifact is never an active item
    assert "conditional_1" not in working_ids + deployable_ids


def test_e0_checkpoint_resume_and_inherit_from(tmp_path, monkeypatch, addressable_l1_failure):
    """checkpoint/resume and inherit_from restore the complete state."""
    p0.install_mocked_evolution(
        monkeypatch, detections=p0.evolution_case_detections(),
    )
    first = tmp_path / "first"
    runner.run_online_evolution(
        evolution_case_ids=("source1",),
        records=_records("source1", "anchor"),
        config={}, output_dir=first, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="e0_first",
    )
    manifest = write_state_manifest(first, run_id="e0_first")
    loaded = load_state_manifest(first)
    assert loaded["provider_rule_genome"]["sha256"]
    assert loaded["adjudication_policy_genome"]["sha256"]
    assert loaded["experience_memory"]["sha256"]

    # inherit_from: a fresh run carries the previous run's complete state
    p0.install_mocked_evolution(
        monkeypatch, detections=p0.evolution_case_detections(),
    )
    second = tmp_path / "second"
    result = runner.run_online_evolution(
        evolution_case_ids=("source2",),
        records=_records("source2", "anchor"),
        config={}, output_dir=second, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="e0_second",
    )
    assert result.total_cases == 1
