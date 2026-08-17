"""Single Detector API: validate -> packet -> judge -> defense -> adjudicate."""

from __future__ import annotations

from typing import Any, Mapping

from detector_v2.adjudication import adjudicate
from detector_v2.config import load_config
from detector_v2.defense import run_defense
from detector_v2.interventions import compile_runtime_plan
from detector_v2.judge import run_judge
from detector_v2.packet import build_packet, validate_record
from detector_v2.recaller import run_causal_upgrade, run_recall_scan
from detector_v2.result import build_detection_output
from detector_v2.step_tracer import run_step_tracer


class Detector:
    """One configured, stateless-between-calls detector."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        require_credentials: bool = True,
    ) -> None:
        self.config = (
            config if config is not None
            else load_config(require_credentials=require_credentials)
        )

    def detect(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str = "case",
        artifacts: Mapping[str, Any] | None = None,
        interventions: Mapping[str, Any] | None = None,
        frozen_judge: Mapping[str, Any] | None = None,
        frozen_stages: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detect one ``{question, history}`` record (label-free).

        ``interventions`` is a validated ``detector_v2.intervention_registry.v1``
        payload. Loading is fail-closed: any hook/type/runner that has no
        executor raises before the first LLM call.

        ``frozen_judge`` enables paired replay for self-evolution gates. It
        must be ``{"judge_result": {...}, "judge_status": {...}}`` captured
        from a baseline run. If the intervention registry targets ``judge.*``
        hooks, replay is refused (fail-closed): freezing the judge would mask
        the intervention under test.
        """
        validate_record(record)
        packet = build_packet(record)
        plan = compile_runtime_plan(artifacts, interventions)

        frozen = frozen_judge if frozen_judge is not None else frozen_stages
        if frozen is not None and "judge_result" in frozen:
            if not isinstance(frozen, Mapping):
                raise ValueError("frozen stage replay payload must be an object")
            judge_result = dict(frozen.get("judge_result") or {})
            judge_status = dict(frozen.get("judge_status") or {})
            if not judge_result.get("ok") or not isinstance(
                judge_result.get("parsed"), Mapping
            ):
                raise ValueError("frozen judge_result is not a valid ok result")
            if judge_status.get("state") != "ok":
                raise ValueError("frozen judge_status.state must be ok")
            if any(
                str((entry.get("hook") or "")) == "judge.system"
                for entry in plan.interventions.entries
            ):
                raise ValueError(
                    "frozen judge replay is refused while a judge.system "
                    "intervention is active (it would mask the intervention)"
                )
        else:
            judge_result, judge_status = run_judge(
                packet=packet, config=self.config, plan=plan,
            )
        judge_parsed = judge_result.get("parsed") or {}
        judge_positive = bool(judge_parsed.get("is_hallucination"))
        plan.evidence_for(
            "judge.post",
            packet,
            {
                "judge_parsed": judge_parsed,
                "evidence_refs": judge_parsed.get("evidence_refs") or [],
                "contracts": judge_parsed.get("contracts") or [],
                "delivered_result": judge_parsed.get("delivered_result") or "",
                "quote": (judge_parsed.get("root_cause") or {}).get("quote") or "",
            },
        )

        recall_result = None
        recall_status = None
        upgrade_result = None
        upgrade_status = None
        tracer_result = None
        tracer_status = None
        defense_result = None
        defense_status = None

        frozen = frozen_judge if frozen_judge is not None else frozen_stages
        if judge_status.get("state") == "ok" and not judge_positive:
            if frozen is not None and "recall_result" in frozen:
                recall_result = dict(frozen.get("recall_result") or {})
                recall_status = dict(frozen.get("recall_status") or {})
            else:
                recall_result, recall_status = run_recall_scan(
                    packet=packet, config=self.config, plan=plan,
                )
            if recall_result.get("ok") and recall_result.get("candidates"):
                if frozen is not None and "upgrade_result" in frozen:
                    upgrade_result = dict(frozen.get("upgrade_result") or {})
                    upgrade_status = dict(frozen.get("upgrade_status") or {})
                else:
                    upgrade_result, upgrade_status = run_causal_upgrade(
                        packet=packet,
                        candidates=recall_result["candidates"],
                        config=self.config,
                        plan=plan,
                    )
            elif recall_result.get("ok"):
                upgrade_result = {
                    "ok": True, "upgrades": [], "upgraded_candidate": None,
                }
                upgrade_status = {
                    "stage": "causal_upgrade", "state": "not_needed",
                    "attempts": 0, "view": "not_rendered", "warnings": [],
                }

        positive = None
        if judge_status.get("state") == "ok" and judge_positive:
            positive = {
                "source": "judge",
                "step": judge_parsed.get("hallucination_step"),
                "reason": judge_parsed.get("hallucination_reason"),
                "delivered_result": judge_parsed.get("delivered_result"),
                "correct_result": judge_parsed.get("correct_result"),
                "evidence_refs": judge_parsed.get("evidence_refs") or [],
                "contracts": judge_parsed.get("contracts") or [],
                "confidence": judge_parsed.get("confidence") or "",
                "quote": (judge_parsed.get("root_cause") or {}).get("quote") or "",
            }
        elif upgrade_result and upgrade_result.get("upgraded_candidate"):
            candidate = upgrade_result["upgraded_candidate"]
            positive = {
                "source": "recall_upgrade",
                "step": candidate.get("corrected_step"),
                "reason": candidate.get("claim"),
                # Information asymmetry: defense/tracer for a recall upgrade
                # receive candidate + trajectory + task contract only, never
                # the judge's correct_result / is_hallucination.
                "delivered_result": "",
                "correct_result": "",
                "evidence_refs": candidate.get("evidence_refs") or [],
                "quote": candidate.get("quote") or "",
                "confidence": "",
                # Task contracts are allowed; judge verdict anchors are not.
                "contracts": judge_parsed.get("contracts") or [],
            }

        if positive:
            if frozen is not None and "tracer_result" in frozen:
                tracer_result = dict(frozen.get("tracer_result") or {})
                tracer_status = dict(frozen.get("tracer_status") or {})
            else:
                tracer_result, tracer_status = run_step_tracer(
                    packet=packet,
                    delivered_result=str(positive.get("delivered_result") or ""),
                    correct_result=str(positive.get("correct_result") or ""),
                    config=self.config,
                    plan=plan,
                )
            if frozen is not None and "defense_result" in frozen:
                defense_result = dict(frozen.get("defense_result") or {})
                defense_status = dict(frozen.get("defense_status") or {})
            else:
                defense_result, defense_status = run_defense(
                    packet=packet,
                    positive=positive,
                    config=self.config,
                    plan=plan,
                )

        defense_parsed = (
            (defense_result or {}).get("parsed") or {}
        ) if isinstance((defense_result or {}).get("parsed"), Mapping) else {}
        guard_facts = {
            "judge_positive": str(judge_positive).lower(),
            "judge_confidence": str(judge_parsed.get("confidence") or ""),
            "positive_source": str((positive or {}).get("source") or "none"),
            "defense_verdict": str(defense_parsed.get("verdict") or "none"),
            "defense_view_degraded": str(
                (defense_status or {}).get("view") == "degraded"
            ).lower(),
            "upgrade_degraded": str(
                bool(
                    (upgrade_status or {}).get("state") == "degraded_missing_rows"
                    or (upgrade_result or {}).get("error_type")
                    == "degraded_missing_rows"
                )
            ).lower(),
            "recall_upgrade": "present" if positive and positive.get("source") == "recall_upgrade" else "none",
        }
        guard_actions = plan.guard_actions(packet, guard_facts)
        proof = adjudicate(
            packet=packet,
            judge_result=judge_result,
            judge_status=judge_status,
            recall_result=recall_result,
            recall_status=recall_status,
            upgrade_result=upgrade_result,
            upgrade_status=upgrade_status,
            tracer_result=tracer_result,
            tracer_status=tracer_status,
            defense_result=defense_result,
            defense_status=defense_status,
            guard_actions=guard_actions,
        )
        provenance: dict[str, Any] = {}
        if artifacts is not None:
            provenance["artifacts_hash"] = str(
                artifacts.get("content_hash") or ""
            )
        if interventions is not None:
            provenance["interventions_hash"] = str(
                interventions.get("content_hash") or ""
            )
        if frozen_judge is not None or frozen_stages is not None:
            provenance["frozen_judge_replay"] = True
            frozen_keys = [
                key for key in (frozen_stages or frozen_judge or {})
                if key.endswith("_result")
            ]
            provenance["frozen_stages"] = sorted(frozen_keys)
        stage_status = {
            "judge": judge_status,
            "recall_scanner": recall_status,
            "causal_upgrade": upgrade_status,
            "step_tracer": tracer_status,
            "defense": defense_status,
        }
        providers = {
            "judge": judge_result.get("provider"),
            "recall_scanner": recall_result.get("provider") if recall_result else None,
            "causal_upgrade": upgrade_result.get("provider") if upgrade_result else None,
            "step_tracer": tracer_result.get("provider") if tracer_result else None,
            "defense": defense_result.get("provider") if defense_result else None,
        }
        proof["judge"] = judge_result.get("parsed")
        proof["recall_candidates"] = (
            recall_result.get("candidates") if recall_result else None
        )
        proof["causal_upgrade"] = (
            upgrade_result.get("upgraded_candidate") if upgrade_result else None
        )
        proof["causal_upgrade_rows"] = (
            upgrade_result.get("upgrades") if upgrade_result else None
        )
        proof["step_tracer"] = (
            tracer_result.get("parsed") if tracer_result else None
        )
        proof["defense"] = (
            defense_result.get("parsed") if defense_result else None
        )
        proof["causal_upgrade_coverage"] = (
            upgrade_result.get("coverage") if upgrade_result else None
        )
        proof["evidence"] = list(plan.interventions.evidence)
        proof["deployment"] = plan.deployment_summary()
        return build_detection_output(
            case_id=case_id,
            packet=packet,
            proof=proof,
            stage_status=stage_status,
            providers=providers,
            provenance=provenance,
        )


_DEFAULT_DETECTOR: Detector | None = None


def _default_detector() -> Detector:
    global _DEFAULT_DETECTOR
    if _DEFAULT_DETECTOR is None:
        _DEFAULT_DETECTOR = Detector()
    return _DEFAULT_DETECTOR


def detect(
    record: Mapping[str, Any],
    *,
    case_id: str = "case",
    config: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, Any] | None = None,
    interventions: Mapping[str, Any] | None = None,
    frozen_judge: Mapping[str, Any] | None = None,
    frozen_stages: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience entry with optional paired-replay stage freezing."""
    detector = Detector(config) if config is not None else _default_detector()
    return detector.detect(
        record, case_id=case_id, artifacts=artifacts,
        interventions=interventions, frozen_judge=frozen_judge,
        frozen_stages=frozen_stages,
    )


__all__ = ["Detector", "detect"]
