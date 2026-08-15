"""Gap taxonomy contract: closed categories, targets, freeze gate."""

from __future__ import annotations

from sehd_rise2.evolution import gap_taxonomy


def test_every_category_has_an_evolution_target():
    assert set(gap_taxonomy.CATEGORY_EVOLUTION_TARGET) == set(
        gap_taxonomy.GAP_CATEGORIES
    )
    assert set(gap_taxonomy.CATEGORY_EVOLUTION_TARGET.values()) <= set(
        gap_taxonomy.EVOLUTION_TARGETS
    )


def test_attribution_is_diagnostic_only():
    assert gap_taxonomy.DIAGNOSTIC_ONLY_CATEGORIES == {"attribution"}
    assert gap_taxonomy.CATEGORY_EVOLUTION_TARGET[
        "attribution"
    ] == "diagnostic_only"


def test_adversarial_defense_overturn_lives_in_policy():
    assert "adversarial_defense_overturn" in gap_taxonomy.GAP_CATEGORIES[
        "policy"
    ]
    assert "adversarial_excuse" not in gap_taxonomy.GAP_CATEGORIES[
        "verification"
    ]


def test_gap_allowed_accepts_known_names_regardless_of_freeze():
    assert gap_taxonomy.gap_allowed(
        "policy", "strictness_miscalibration", status="known",
    )
    assert gap_taxonomy.gap_allowed(
        "policy", "strictness_miscalibration", status="proposed",
    )


def test_gap_allowed_proposal_channel_closed_when_frozen(monkeypatch):
    assert not gap_taxonomy.gap_allowed(
        "observation", "novel_pattern", status="proposed",
    )
    monkeypatch.setattr(gap_taxonomy, "TAXONOMY_FROZEN", False)
    assert gap_taxonomy.gap_allowed(
        "observation", "novel_pattern", status="proposed",
    )
    assert not gap_taxonomy.gap_allowed(
        "observation", "novel_pattern", status="known",
    )


def test_unknown_category_is_never_allowed():
    assert not gap_taxonomy.category_exists("vibes")
    assert not gap_taxonomy.gap_allowed(
        "vibes", "anything", status="proposed",
    )
