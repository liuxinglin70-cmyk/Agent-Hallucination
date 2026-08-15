"""Capability gap taxonomy: the closed vocabulary of missing capabilities.

Frozen 2026-08-15 after the final review of 49 annotated failures
(see SEHD_RISE2_Phase0_失败理解层实施文档.md §3.5). The three
verification names below were proposed during annotation (one case each)
and were kept because each covers a mechanism the existing names do not.
Taxonomy is the single source of truth for annotations, aggregation
reports and the Meta Skill diagnosis protocol; ``gap_allowed`` rejects
every name outside it.

Category semantics (final review, 2026-08-15):

- observation     the deviation never became a candidate (census miss)
- verification    the candidate existed but could not be proven or
                  disproven from the cited evidence; includes the three
                  mechanisms discovered in this dataset: a real
                  requirement deviation dismissed via an out-of-task
                  excuse (``excuse_based_rejection``), contradictory
                  sources adjudicated by flattening
                  (``source_credibility_conflict``), and an agent-stated
                  derivation accepted without re-derivation
                  (``unverified_derivation``)
- policy          the evidence chain was complete but the decision
                  boundary was wrong — FP convictions AND defense
                  overturns of true positives both live here, because in
                  both the court HAD the evidence and mis-weighed it
- attribution     polarity correct, root-cause localization wrong;
                  DIAGNOSTIC ONLY — it never spawns a detector capability
- infrastructural provider/transport failure, not a capability
- no_gap          detector behaviour was correct, or the gold label is
                  disputed (arbitration queue, never counted as a gap)
"""

from __future__ import annotations

TAXONOMY_FROZEN = True

GAP_CATEGORIES: dict[str, tuple[str, ...]] = {
    "observation": (
        "missing_required_action",
        "unsupported_claim_grounding",
        "hidden_contradiction",
        "census_view_degraded",
        "pattern_intermittence",
    ),
    "verification": (
        "evidence_insufficient",
        "external_knowledge_limit",
        "exactness_boundary",
        "excuse_based_rejection",
        "source_credibility_conflict",
        "unverified_derivation",
    ),
    "policy": (
        "adversarial_defense_overturn",
        "strictness_miscalibration",
        "expression_tolerance",
        "evidence_weighting",
    ),
    "attribution": (
        "locus_vs_manifestation",
        "tie_ambiguity",
    ),
    "infrastructural": (),
    "no_gap": (),
}

DIAGNOSTIC_ONLY_CATEGORIES = frozenset({"attribution"})

EVOLUTION_TARGETS: dict[str, str] = {
    "l1_candidate": "candidate-discovery extension (observation gaps)",
    "evidence_skill": "verification evidence extension (verification gaps)",
    "l2_policy": "decision-boundary adjustment (policy gaps)",
    "diagnostic_only": "measurement layer only; no detector capability",
    "none": "no evolution target",
}

CATEGORY_EVOLUTION_TARGET: dict[str, str] = {
    "observation": "l1_candidate",
    "verification": "evidence_skill",
    "policy": "l2_policy",
    "attribution": "diagnostic_only",
    "infrastructural": "none",
    "no_gap": "none",
}


def category_exists(category: str) -> bool:
    return str(category) in GAP_CATEGORIES


def gap_name_known(category: str, name: str) -> bool:
    return str(name) in GAP_CATEGORIES.get(str(category), ())


def gap_allowed(category: str, name: str, *, status: str) -> bool:
    """Vocabulary gate for one capability gap.

    Known names always pass regardless of status. The proposal channel
    (an unknown name with ``status="proposed"``) existed only while the
    taxonomy was unfrozen and is closed: frozen means exactly that every
    annotation must fall inside the frozen vocabulary.
    """
    if not category_exists(category):
        return False
    if gap_name_known(category, name):
        return True
    return not TAXONOMY_FROZEN and str(status) == "proposed"


__all__ = [
    "CATEGORY_EVOLUTION_TARGET",
    "DIAGNOSTIC_ONLY_CATEGORIES",
    "EVOLUTION_TARGETS",
    "GAP_CATEGORIES",
    "TAXONOMY_FROZEN",
    "category_exists",
    "gap_allowed",
    "gap_name_known",
]
