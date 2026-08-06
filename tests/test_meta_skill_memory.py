"""P6: persistent meta-review state preparation (memory v2 / checkpoint v5).

Behavior-preserving schema groundwork: the meta_review sub-domains are
reserved and initialized empty; a v1 memory artifact migrates to v2 only at
the single ``from_dict`` boundary; a v4 checkpoint loads by adding default
empty Meta fields and upgrading to v5 in memory.  No generation or gate
behavior changes, and no model call is added.
"""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.evolution_experience_memory import (
    META_REVIEW_KEYS,
    SCHEMA_VERSION,
    EvolutionExperienceMemory,
)


def test_v2_roundtrip_canonical_equality():
    memory = EvolutionExperienceMemory()
    payload = memory.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert set(payload["meta_review"]) == set(META_REVIEW_KEYS)
    restored = EvolutionExperienceMemory()
    restored.from_dict(payload)
    assert restored.to_dict() == payload


def test_v1_migrates_to_v2_at_single_boundary():
    memory = EvolutionExperienceMemory()
    v1 = memory.to_dict()
    v1["schema_version"] = "sehd.evolution_experience_memory.v1"
    del v1["meta_review"]
    migrated = EvolutionExperienceMemory()
    migrated.from_dict(v1)
    # In-memory shape is immediately v2 with empty meta sub-domains.
    assert migrated.to_dict()["schema_version"] == SCHEMA_VERSION
    assert set(migrated.meta_review) == set(META_REVIEW_KEYS)


def test_legacy_type_only_explicit_migration():
    migrated = EvolutionExperienceMemory()
    migrated.from_dict({"version": "htc_tes_legacy", "type_stats": {}})
    assert migrated.to_dict()["schema_version"] == SCHEMA_VERSION
    assert set(migrated.meta_review) == set(META_REVIEW_KEYS)


def test_unknown_schema_rejected():
    memory = EvolutionExperienceMemory()
    with pytest.raises(ValueError):
        memory.from_dict({"schema_version": "sehd.evolution_experience_memory.v9"})


def test_meta_review_starts_empty():
    memory = EvolutionExperienceMemory()
    for key, value in memory.meta_review.items():
        assert value == {} or value == []


def test_meta_review_unknown_keys_dropped_on_load():
    payload = EvolutionExperienceMemory().to_dict()
    payload["meta_review"]["rogue_key"] = [1, 2, 3]
    restored = EvolutionExperienceMemory()
    restored.from_dict(payload)
    assert "rogue_key" not in restored.meta_review


def test_interrupted_and_uninterrupted_memory_normalize_identically():
    # A v1 artifact (as written before an interruption) and a fresh v2 both
    # normalize to the same canonical v2 in-memory shape.
    fresh = EvolutionExperienceMemory().to_dict()
    legacy = dict(fresh)
    legacy["schema_version"] = "sehd.evolution_experience_memory.v1"
    legacy.pop("meta_review")
    a = EvolutionExperienceMemory()
    a.from_dict(legacy)
    b = EvolutionExperienceMemory()
    b.from_dict(fresh)
    assert a.to_dict() == b.to_dict()
