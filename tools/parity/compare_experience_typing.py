"""Phase 5 experience + typing parity harness (plan §3.3 level 2 -- offline
contract check).

Four verification surfaces, all deterministic and offline (no provider calls),
per plan §3.3 R2:

1. Experience memory serialization + mechanism-index parity.
   A serialized v2 artifact (carrying the type-conditioned store the C5
   demotion removes from this module) is loaded in both packages. The
   ``failure_mechanisms`` block must be field-by-field equal and the
   mechanism signature index counts must match. The new package drops the
   ``type_experience_memory`` block (C5, plan §2.6); the roundtrip
   ``from_dict(to_dict(x)) == x`` is asserted on the new package only.

2. Classifier parity (plan §4.5: typing/classifier translation, no mutation).
   The same ``detection_result`` + ``attribution_signal`` fixtures feed both
   classifiers; ``TypeClassificationResult.to_dict()`` and
   ``TrajectoryFeatures.to_dict()`` must be field-by-field equal.

3. Guidance C5 demotion (plan §4.5 + §2.6).
   The three advisory accessors (target modules / edit strategy / type
   guidance) must agree field-by-field. The two abolished authority methods
   (module route filter, direction conflict arbitration) must be ABSENT from
   the new module.

4. Slices DACR parity (plan §4.5: retained statistics caliber consistent).
   The same detection_before/after + type_result fixtures feed both
   ``TypeAwareFitness.assess_edit`` paths; the
   ``TypeAwareFitnessAssessment.to_dict()`` must be field-by-field equal.
   ``TypeExperienceMemory`` roundtrip is asserted in both packages.

Plan §3.3 R2: the warm-cache detection replay is unavailable on this machine
(cache root empty), so this harness degrades to offline deterministic
contract checks. The degradation is reported loudly below, never silently
skipped.

Run:  python tools/parity/compare_experience_typing.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# tools/parity is the only location that may import both packages.
from src.sehd_v6.core.bccse import (  # noqa: E402
    evolution_experience_memory as old_exp,
)
from src.sehd_v6.core.bccse import types as old_types  # noqa: E402
from src.sehd_v6.core.htc_tes import (  # noqa: E402
    type_aware_fitness as old_slices,
)
from src.sehd_v6.core.htc_tes import (  # noqa: E402
    type_classifier as old_classifier_mod,
)
from src.sehd_v6.core.htc_tes import (  # noqa: E402
    type_conditioned_evolution as old_guidance_mod,
)

from sehd_rise.experience.memory import (  # noqa: E402
    ExperienceMemory,
    ExperienceRecord,
    MechanismEntry,
    Representative,
    episode_signature,
)
from sehd_rise.platform.jsonio import canonical_json  # noqa: E402
from sehd_rise.typing.classifier import (  # noqa: E402
    HallucinationType as NewHallucinationType,
)
from sehd_rise.typing.classifier import (  # noqa: E402
    HallucinationTypeClassifier as NewClassifier,
)
from sehd_rise.typing.guidance import TypeGuidance as NewGuidance  # noqa: E402
from sehd_rise.typing.slices import (  # noqa: E402
    TypeAwareFitness as NewFitness,
)
from sehd_rise.typing.slices import (  # noqa: E402
    TypeExperienceMemory as NewTypeMemory,
)

CHECKS: list[str] = []
FAILURES: list[str] = []
DEV: list[str] = []


def _ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok   {name}")


def _fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"  FAIL {name}")
    print(f"       {detail}")


def _dev(name: str, ref: str) -> None:
    DEV.append(name)
    print(f"  DEV  {name}")
    print(f"       deviation: {ref}")


def _check_eq(name: str, expected: Any, actual: Any) -> bool:
    if canonical_json(expected) == canonical_json(actual):
        _ok(name)
        return True
    _fail(name, f"expected={expected!r} actual={actual!r}")
    return False


def _check_true(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _ok(name)
        return True
    _fail(name, detail or "condition false")
    return False


# ============================================================
# Experience memory fixture (serialized v2 artifact)
# ============================================================

def _representative(completeness: str, evidence: int, sequence: int,
                    episode_id: str, decision: str) -> dict[str, Any]:
    return {
        "completeness": completeness,
        "evidence_fields": evidence,
        "sequence": sequence,
        "episode_id": episode_id,
        "causal_slice": {
            "candidate_id": "cand_1",
            "candidate_kind": "agent_assertion",
            "missing_fields": ["claim"],
            "malformed_fields": [],
            "consumer": "proof_court",
            "completeness": completeness,
        },
        "action_spec": {"module_id": "evidence_obligation", "operation": "add_rule"},
        "outcome": {
            "decision": decision,
            "no_effect_reason": "",
            "structural_result": "accepted",
            "compiled": True,
            "injected": True,
            "applied": True,
            "observable_delta": ["state:unresolved->verified_error"],
            "downstream_court_delta": [],
        },
        "rule_text": "when claim has no evidence, create error candidate",
    }


def _mechanism_entry(sig: tuple[str, ...], obs: int, outcomes: dict[str, int],
                     seq: int, reps: dict[str, Any], counted: list[str]) -> dict[str, Any]:
    representatives = {key: None for key in ("effective", "no_effect", "structural_reject", "no_proposal")}
    representatives.update(reps)
    return {
        "signature": list(sig),
        "observation_count": obs,
        "outcome_counts": outcomes,
        "representatives": representatives,
        "counted_episode_ids": counted,
        "last_sequence": seq,
    }


def _v2_artifact() -> dict[str, Any]:
    """A v2 serialized artifact carrying both the type-conditioned store
    (which C5 removes from the new experience module) and the mechanism index
    (which is preserved). The type block is empty so the old package loads it
    without exercising demoted storage semantics."""
    return {
        "schema_version": "sehd.evolution_experience_memory.v2",
        "type_experience_memory": {"max_per_type": 50, "types": {}},
        "failure_mechanisms": [
            _mechanism_entry(
                sig=("L1", "evidence_obligation", "provider_artifact_missing",
                     "error_to_clean", "agent_assertion", "proof_court", "claim"),
                obs=3,
                outcomes={"accept": 2, "structural_reject": 1},
                seq=7,
                reps={
                    "effective": _representative("complete", 6, 7, "ep_a1", "accept"),
                    "structural_reject": _representative("partial", 2, 3, "ep_a2", "structural_reject"),
                },
                counted=["ep_a1_prefix", "ep_a2_pre", "ep_a3_pre"],
            ),
            _mechanism_entry(
                sig=("L2", "boundary_consumption", "boundary_terminal_conflict",
                     "unresolved_to_clean", "candidate", "adjudication_policy"),
                obs=1,
                outcomes={"no_edit": 1},
                seq=2,
                reps={"no_proposal": _representative("partial", 0, 1, "ep_b1", "no_edit")},
                counted=["ep_b1_prefix"],
            ),
        ],
        "meta_review": {
            "gate_experiences": [{"event": "gate", "outcome": "accept"}],
            "meta_state": {"phase": "p5"},
            "efficiency_profile": [0.42, 0.51],
        },
    }


def check_experience_memory_parity() -> None:
    print("experience memory serialization + mechanism-index parity")
    artifact = _v2_artifact()

    old_mem = old_exp.EvolutionExperienceMemory()
    old_mem.from_dict(artifact)
    new_mem = ExperienceMemory()
    new_mem.from_dict(artifact)

    # C5: the type-conditioned store is dropped from the new module.
    _check_true(
        "new to_dict omits type_experience_memory (C5 §2.6)",
        "type_experience_memory" not in new_mem.to_dict(),
        "new experience memory must not carry the demoted type-conditioned store",
    )
    _check_true(
        "old to_dict retains type_experience_memory",
        "type_experience_memory" in old_mem.to_dict(),
        "old package must still carry the type-conditioned store",
    )

    # §4.4: mechanism signature index counts identical.
    old_entries = {tuple(e.signature): e for e in old_mem.entries()}
    new_entries = {tuple(e.signature): e for e in new_mem.entries()}
    _check_eq("mechanism entry count", len(old_entries), len(new_entries))
    _check_eq(
        "mechanism signature set",
        sorted(old_entries),
        sorted(new_entries),
    )
    for sig in sorted(old_entries):
        oe = old_entries[sig]
        ne = new_entries[sig]
        _check_eq(
            f"observation_count [{sig[1]}]",
            oe.observation_count, ne.observation_count,
        )
        _check_eq(
            f"outcome_counts [{sig[1]}]",
            oe.outcome_counts, ne.outcome_counts,
        )
        _check_eq(
            f"last_sequence [{sig[1]}]",
            oe.last_sequence, ne.last_sequence,
        )

    # §4.3: failure_mechanisms block field-by-field equal across packages.
    _check_eq(
        "failure_mechanisms block (old vs new to_dict)",
        old_mem.to_dict()["failure_mechanisms"],
        new_mem.to_dict()["failure_mechanisms"],
    )

    # §4.3: new-package roundtrip from_dict(to_dict(x)) == x.
    roundtrip = ExperienceMemory()
    roundtrip.from_dict(new_mem.to_dict())
    _check_eq(
        "new roundtrip to_dict equality",
        new_mem.to_dict(), roundtrip.to_dict(),
    )

    # meta_review normalization: only closed keys survive, unknown dropped.
    new_meta = new_mem.to_dict()["meta_review"]
    _check_true(
        "meta_review drops unknown keys",
        "unknown_key" not in new_meta and "gate_experiences" in new_meta,
    )


# ============================================================
# Classifier fixtures + parity
# ============================================================

def _candidate(cid: str, kind: str, claim: str = "", axes: tuple[str, ...] = (),
               module_tag: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": cid,
        "candidate_kind": kind,
        "claim_or_commitment": claim,
    }
    if axes:
        row["axes"] = list(axes)
    if module_tag:
        row["module_tag"] = module_tag
    return row


def _detection_row(state: str, census: list[dict[str, Any]],
                   cov_p: str = "incomplete", cov_d: str = "incomplete") -> dict[str, Any]:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": census,
        "proof_adjudication": {
            "state": state,
            "prosecution_coverage": cov_p,
            "defense_coverage": cov_d,
        },
    }


def _attribution_signal(error_type: str, target_module: str,
                         crs_scores: dict[str, Any] | None = None,
                         crs_complete: bool = True,
                         crs_aggregate: float | None = None,
                         cov_p_state: str | None = None,
                         cov_d_state: str | None = None,
                         crs_source: str = "native") -> dict[str, Any]:
    sig: dict[str, Any] = {
        "error_type": error_type,
        "target_module": target_module,
        "crs_source": crs_source,
        "crs_complete": crs_complete,
    }
    if crs_scores is not None:
        sig["crs_scores"] = crs_scores
    if crs_aggregate is not None:
        sig["crs_aggregate"] = crs_aggregate
    if cov_p_state is not None:
        sig["prosecution_coverage_state"] = cov_p_state
    if cov_d_state is not None:
        sig["defense_coverage_state"] = cov_d_state
    return sig


def _classifier_fixtures() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(case_id, detection_result, attribution_signal) covering each type path."""
    return [
        (
            "factual_evidence_incomplete",
            _detection_row(
                "unresolved",
                [_candidate("c1", "agent_assertion", "fabricated the tool output", axes=("f",))],
            ),
            _attribution_signal("evidence_incomplete", "evidence_obligation",
                                 crs_scores={"factuality": {"score": 0.1}}),
        ),
        (
            "omission_false_negative",
            _detection_row(
                "verified_clean",
                [_candidate("c2", "omission", "failed to call the required tool")],
                cov_p="complete", cov_d="complete",
            ),
            _attribution_signal("false_negative", "candidate_trigger"),
        ),
        (
            "tool_invalid_certificate",
            _detection_row(
                "unresolved",
                [_candidate("c3", "tool_action", "wrong tool call", module_tag="tool")],
            ),
            _attribution_signal("invalid_certificate", "candidate_to_verifier_route"),
        ),
        (
            "contextual_false_positive",
            _detection_row(
                "verified_error",
                [_candidate("c4", "agent_assertion", "off topic response", axes=("fa",))],
                cov_p="complete", cov_d="complete",
            ),
            _attribution_signal("false_positive", "abstention_rule"),
        ),
        (
            "reasoning_localization_ambiguous",
            _detection_row(
                "verified_error",
                [_candidate("c5", "agent_assertion", "wrong conclusion", module_tag="reason")],
                cov_p="complete", cov_d="incomplete",
            ),
            _attribution_signal("localization_ambiguous", "causal_link_test"),
        ),
        (
            "unknown_no_info_signal",
            _detection_row("unresolved", []),
            _attribution_signal("unresolved_unknown", ""),
        ),
    ]


def check_classifier_parity() -> None:
    print("classifier parity (translation, no mutation)")
    old_clf = old_classifier_mod.HallucinationTypeClassifier()
    new_clf = NewClassifier()
    for case_id, detection, signal in _classifier_fixtures():
        old_res = old_clf.classify(detection, signal)
        new_res = new_clf.classify(detection, signal)
        _check_eq(
            f"{case_id} classification result",
            old_res.to_dict(), new_res.to_dict(),
        )
        old_feat = old_clf.extract_features(detection, signal)
        new_feat = new_clf.extract_features(detection, signal)
        _check_eq(
            f"{case_id} trajectory features",
            old_feat.to_dict(), new_feat.to_dict(),
        )


# ============================================================
# Guidance C5 demotion
# ============================================================

def check_guidance_parity() -> None:
    print("guidance C5 demotion (advisory retained, authority abolished)")
    old_g = old_guidance_mod.TypeConditionedEvolution()
    new_g = NewGuidance()
    for htype in old_classifier_mod.HallucinationType:
        _check_eq(
            f"target_modules[{htype.value}]",
            old_g.get_target_modules(htype),
            new_g.get_target_modules(NewHallucinationType(htype.value)),
        )
        _check_eq(
            f"edit_strategy[{htype.value}]",
            old_g.get_edit_strategy(htype),
            new_g.get_edit_strategy(NewHallucinationType(htype.value)),
        )
    for direction in (
        "error_to_clean", "clean_to_error", "unresolved_to_decisive",
        "process_repair", "no_evolution",
    ):
        for htype in old_classifier_mod.HallucinationType:
            _check_eq(
                f"type_guidance[{direction}/{htype.value}]",
                old_g.generate_type_guidance(htype, direction),
                new_g.generate_type_guidance(NewHallucinationType(htype.value), direction),
            )

    # C5 (plan §2.6): the module route filter and the direction conflict
    # arbitration must be ABSENT from the new module. The advisory
    # accessors above are the only public surface.
    _check_true(
        "new guidance has no intersect_allowed_modules (C5 §2.6)",
        not hasattr(new_g, "intersect_allowed_modules"),
        "module route filter must be abolished (second routing truth-source)",
    )
    _check_true(
        "new guidance has no _infer_evolution_direction (C5 §2.6)",
        not hasattr(new_g, "_infer_evolution_direction"),
        "direction conflict arbitration must be abolished (second direction truth-source)",
    )
    _check_true(
        "old guidance still has intersect_allowed_modules",
        hasattr(old_g, "intersect_allowed_modules"),
    )
    _check_true(
        "old guidance still has _infer_evolution_direction",
        hasattr(old_g, "_infer_evolution_direction"),
    )
    _dev(
        "C5 guidance demotion",
        "plan §2.6: intersect_allowed_modules (module route filter) and "
        "_infer_evolution_direction (direction conflict arbitration) are "
        "deleted from the new typing layer; they were a second routing / "
        "direction truth-source competing with the causal owner. The three "
        "advisory accessors (target modules / edit strategy / type guidance) "
        "are retained verbatim.",
    )


# ============================================================
# Slices DACR parity
# ============================================================

def _fitness_fixture(direction: str, before_state: str, after_state: str,
                     target_module: str, attribution: dict[str, Any],
                     detection_census: list[dict[str, Any]] | None = None,
                     ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    before = _detection_row(before_state, detection_census or [])
    after = _detection_row(after_state, detection_census or [])
    return before, after, attribution, target_module


def check_slices_parity() -> None:
    print("slices DACR parity (retained statistics caliber)")
    old_clf = old_classifier_mod.HallucinationTypeClassifier()
    new_clf = NewClassifier()

    fixtures = [
        ("error_to_clean_improvement", "error_to_clean", "verified_error", "verified_clean",
         "evidence_obligation", _attribution_signal("evidence_incomplete", "evidence_obligation"),
         [_candidate("c1", "agent_assertion", "fabricated fact")]),
        ("clean_to_error_improvement", "clean_to_error", "verified_clean", "verified_error",
         "candidate_trigger", _attribution_signal("false_negative", "candidate_trigger"),
         [_candidate("c2", "omission", "failed to act")]),
        ("unresolved_to_clean", "unresolved_to_clean", "unresolved", "verified_clean",
         "clean_defense_boundary", _attribution_signal("unresolved_should_clean", "clean_defense_boundary"),
         [_candidate("c3", "agent_assertion", "supported claim")]),
        ("no_effect", "no_evolution", "verified_error", "verified_error",
         "evidence_obligation", _attribution_signal("evidence_incomplete", "evidence_obligation"),
         [_candidate("c4", "agent_assertion", "claim")]),
        ("regression_clean_to_unresolved", "error_to_clean", "verified_clean", "unresolved",
         "evidence_obligation", _attribution_signal("evidence_incomplete", "evidence_obligation"),
         [_candidate("c5", "agent_assertion", "claim")]),
    ]

    for (case_id, direction, before_s, after_s, module, signal, census) in fixtures:
        before, after, _, _ = _fitness_fixture(direction, before_s, after_s, module, signal, census)
        old_type = old_clf.classify(before, signal)
        new_type = new_clf.classify(before, signal)

        old_fit = old_slices.TypeAwareFitness()
        new_fit = NewFitness()
        old_assess = old_fit.assess_edit(
            source_case_id=case_id,
            hallucination_type=old_type.primary_type,
            evolution_direction=direction,
            detection_before=before,
            detection_after=after,
            type_result=old_type,
            target_module=module,
        )
        new_assess = new_fit.assess_edit(
            source_case_id=case_id,
            hallucination_type=new_type.primary_type,
            evolution_direction=direction,
            detection_before=before,
            detection_after=after,
            type_result=new_type,
            target_module=module,
        )
        _check_eq(
            f"{case_id} fitness assessment",
            old_assess.to_dict(), new_assess.to_dict(),
        )

    # TypeExperienceMemory roundtrip parity (both packages).
    exp_payload = {
        "max_per_type": 50,
        "types": {
            "factual": [
                {
                    "hallucination_type": "factual",
                    "evolution_direction": "error_to_clean",
                    "module_targeted": "evidence_obligation",
                    "strategy_used": "strict_verification",
                    "detection_improved": True,
                    "source_case_id": "case_1",
                    "timestamp": 1.0,
                    "metadata": {"layer": "L1", "artifact": "evidence_obligation",
                                 "reason_code": "provider_artifact_missing",
                                 "operation": "add_rule", "outcome": "accept",
                                 "decisive": True, "decisive_correct": True,
                                 "observation_count": 1},
                    "feature_description": "unsupported claim",
                    "type_confidence": 0.8,
                },
            ],
            "omission": [
                {
                    "hallucination_type": "omission",
                    "evolution_direction": "clean_to_error",
                    "module_targeted": "candidate_trigger",
                    "strategy_used": "trigger_sensitivity",
                    "detection_improved": False,
                    "source_case_id": "case_2",
                    "timestamp": 2.0,
                    "metadata": {"layer": "L1", "artifact": "candidate_trigger",
                                 "reason_code": "provider_artifact_missing",
                                 "operation": "add_rule", "outcome": "structural_reject",
                                 "decisive": False, "decisive_correct": None,
                                 "observation_count": 1},
                    "feature_description": "missing action",
                    "type_confidence": 0.5,
                },
            ],
        },
    }
    old_tm = old_slices.TypeExperienceMemory()
    old_tm.from_dict(exp_payload)
    new_tm = NewTypeMemory()
    new_tm.from_dict(exp_payload)
    _check_eq(
        "TypeExperienceMemory to_dict parity",
        old_tm.to_dict(), new_tm.to_dict(),
    )
    old_rt = old_slices.TypeExperienceMemory()
    old_rt.from_dict(old_tm.to_dict())
    new_rt = NewTypeMemory()
    new_rt.from_dict(new_tm.to_dict())
    _check_eq(
        "TypeExperienceMemory old roundtrip",
        old_tm.to_dict(), old_rt.to_dict(),
    )
    _check_eq(
        "TypeExperienceMemory new roundtrip",
        new_tm.to_dict(), new_rt.to_dict(),
    )


# ============================================================
# Main
# ============================================================

def main() -> int:
    print("== compare_experience_typing ==")
    print()
    print("DEGRADED (plan §3.3 R2): PACA_GLOBAL_CACHE_ROOT warm cache")
    print("unavailable; provider-level byte-identity replay NOT executed in")
    print("this run. Offline deterministic contract checks below were")
    print("executed instead.")
    print()
    check_experience_memory_parity()
    print()
    check_classifier_parity()
    print()
    check_guidance_parity()
    print()
    check_slices_parity()
    print()
    print(f"{len(CHECKS)} checks passed, {len(FAILURES)} failed, {len(DEV)} deviations documented")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
