"""Contract tests for the L1 module charter registry."""

import re

import pytest

from src.sehd_v6.core.bccse import genome
from src.sehd_v6.core.bccse import module_charter as charter


SECTION_HEADERS = (
    "MODULE:",
    "STAGE:",
    "INFLUENCEABLE SURFACES:",
    "FORBIDDEN SURFACES:",
    "ALLOWED VERDICTS:",
    "IN-SCOPE FAILURE EXAMPLES:",
    "OUT-OF-SCOPE FAILURE EXAMPLES:",
)

CJK_RANGE = re.compile(r"[\u4e00-\u9fff]")


def test_all_eight_modules_have_charters_with_nonempty_fields():
    assert set(charter.MODULE_CHARTERS) == set(genome.MODULE_IDS)
    for module_id in genome.MODULE_IDS:
        item = charter.MODULE_CHARTERS[module_id]
        assert item.module_id == module_id
        assert item.stage == genome.MODULE_STAGE[module_id]
        assert item.influenceable_surfaces
        assert item.forbidden_surfaces
        assert item.in_scope_examples
        assert item.out_of_scope_examples
        # 1-2 examples keep the reflection prompt readable without padding.
        assert 1 <= len(item.in_scope_examples) <= 2
        assert 1 <= len(item.out_of_scope_examples) <= 2


def test_allowed_verdicts_track_genome_single_source_of_truth():
    for module_id in genome.MODULE_IDS:
        expected = genome.ALLOWED_VERDICTS.get(module_id, frozenset())
        assert charter.MODULE_CHARTERS[module_id].allowed_verdicts == expected
    assert (
        charter.MODULE_CHARTERS["clean_defense_boundary"].allowed_verdicts
        == frozenset({"clean"})
    )
    assert (
        charter.MODULE_CHARTERS["abstention_rule"].allowed_verdicts
        == frozenset({"clean", "error", "unresolved"})
    )
    for module_id in genome.MODULE_IDS:
        if module_id not in ("clean_defense_boundary", "abstention_rule"):
            assert charter.MODULE_CHARTERS[module_id].allowed_verdicts == frozenset()


def test_out_of_scope_examples_point_to_another_owner():
    for module_id, item in charter.MODULE_CHARTERS.items():
        text = " ".join(item.out_of_scope_examples)
        other_modules = [
            other for other in genome.MODULE_IDS if other != module_id
        ]
        other_targets = other_modules + [
            "L2", "policy", "boundary_consumption", "consensus_gate",
            "coverage_validity",
        ]
        assert any(target in text for target in other_targets), module_id


def test_render_charter_text_contains_every_section():
    for module_id in genome.MODULE_IDS:
        text = charter.render_charter_text(module_id)
        for header in SECTION_HEADERS:
            assert header in text, f"{module_id} missing {header}"
        item = charter.MODULE_CHARTERS[module_id]
        for surface in item.influenceable_surfaces + item.forbidden_surfaces:
            assert surface in text
        for example in item.in_scope_examples + item.out_of_scope_examples:
            assert example in text


def test_render_charter_text_verdict_line_matches_permission():
    assert (
        "ALLOWED VERDICTS: clean"
        in charter.render_charter_text("clean_defense_boundary")
    )
    assert (
        "ALLOWED VERDICTS: clean, error, unresolved"
        in charter.render_charter_text("abstention_rule")
    )
    assert (
        "ALLOWED VERDICTS: none"
        in charter.render_charter_text("candidate_trigger")
    )


def test_render_all_charters_covers_all_modules_in_order():
    text = charter.render_all_charters()
    positions = []
    for module_id in genome.MODULE_IDS:
        position = text.find(f"MODULE: {module_id}")
        assert position >= 0, module_id
        positions.append(position)
    assert positions == sorted(positions)


def test_unknown_module_raises_value_error():
    with pytest.raises(ValueError):
        charter.charter_for("nonexistent_module")
    with pytest.raises(ValueError):
        charter.render_charter_text("nonexistent_module")


def test_charter_text_is_pure_english():
    for module_id in genome.MODULE_IDS:
        text = charter.render_charter_text(module_id)
        assert not CJK_RANGE.search(text), module_id
    assert not CJK_RANGE.search(charter.render_all_charters())
