"""Single Detector API: validate -> packet -> judge -> defense -> adjudicate."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from detector.adjudication import adjudicate
from detector.action_evidence import collect_action_candidates, linked_action_evidence
from detector.config import load_config, validate_config
from detector.causal_audit import (
    OfflineFeedbackPolicy, build_causal_diagnostics, causal_recall_decision,
)
from detector.causal_contracts import seal
from detector.identity import build_execution_identity, digest, source_identity
from detector.hearing import hearing_target, judge_target, recall_target, run_hearing
from detector.evidence import (
    snapshot_digest,
    touches_packet,
    validate_snapshot,
)
from detector.interventions import (
    SemanticExecutor,
    authority_identity,
    compile_runtime_plan,
    load_intervention_registry,
)
from detector.judge import run_judge
from detector.packet import build_packet, event_id_for_step, validate_record
from detector.predicate_context import judge_predicate_context
from detector.provider import ProviderError, case_deadline_ctx
from detector.measurement import request_execution
from detector.runtime import (
    AddedBudgetExceeded, CaseDeadlineExceeded, added_budget_scope, added_request_scope, current_added_budget, usage_scope, utc_now, preparation_scope,
)
import time
from detector.recaller import (
    run_causal_upgrade,
    run_recall_scan,
)
from detector.replay import build_replay_inputs, validate_frozen_stages
from detector.result import build_detection_output
from detector.stage_retry import current_stage_retry
from detector.semantic_trace import build_semantic_trace, referent_provenance


@dataclass(frozen=True)
class _CausalReview:
    input_digest: str
    judge_result: Mapping[str, Any]
    judge_status: Mapping[str, Any]
    diagnostics: Mapping[str, Any]
    context: Mapping[str, Any]


def _judge_negative_needs_recall(
    judge_result: Mapping[str, Any], config: Mapping[str, Any],
) -> bool:
    """Use the judge ensemble's uncertainty instead of rescanning 3/3 clean cases."""
    requested = max(
        1, int((config.get("judge_route") or {}).get("votes", 1) or 1),
    )
    if (judge_result.get("view_meta") or {}).get("degraded"):
        return True
    if requested == 1:
        return True
    parsed = judge_result.get("parsed") or {}
    if str(parsed.get("confidence") or "") != "high":
        return True
    vote = (judge_result.get("provider") or {}).get("vote") or {}
    if not vote:
        return True
    cast = int(vote.get("cast") or 0)
    positives = int(vote.get("positives") or 0)
    return cast < requested or positives > 0 or bool(vote.get("tie"))


def _negative_referent_review_bases(judge_result: Mapping[str, Any]) -> list[str]:
    """Agreement cannot independently verify a derivation or model recollection."""
    parsed = judge_result.get("parsed") or {}
    if parsed.get("is_hallucination") is not False:
        return []
    raw = judge_result.get("raw")
    ballots = raw if isinstance(raw, list) and raw else [raw if isinstance(raw, Mapping) else parsed]
    return sorted({ballot["basis"] for ballot in ballots
                   if isinstance(ballot, Mapping) and ballot.get("is_hallucination") is False
                   and ballot.get("basis") in {"derivation", "world_knowledge"}})


class Detector:
    """One configured, stateless-between-calls detector."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        require_credentials: bool = True,
        semantic_executor: "SemanticExecutor | None" = None,
        component_executor_factory: Any | None = None,
        offline_feedback_policy: OfflineFeedbackPolicy | None = None,
    ) -> None:
        if offline_feedback_policy is not None:
            if require_credentials or not isinstance(offline_feedback_policy, OfflineFeedbackPolicy):
                raise ValueError("offline feedback policy requires an uncredentialed offline detector")
        self.offline_feedback_policy = offline_feedback_policy
        self.config = (
            validate_config(config, require_credentials=require_credentials) if config is not None
            else load_config(require_credentials=require_credentials)
        )
        registry_path = self.config.get("intervention_registry")
        self.default_interventions_path = (
            Path(str(registry_path)) if registry_path else None
        )
        # The executor behind any ``typed_semantic`` entry in a registry. It is
        # injected rather than constructed here: the framework does not own a
        # model, and an executor that cannot be supplied must make a learned
        # entry fail closed rather than silently judge as something else.
        self.semantic_executor = semantic_executor
        # The executors behind any ``sandboxed_component`` entries. Unlike the
        # semantic executor these are derived from the registry itself: a
        # component carries its own source, so there is nothing for a caller to
        # supply and nothing for the detector layer to import. Left as None the
        # detector builds them per call, which keeps the Level B path reachable
        # from every caller rather than only from the one that remembered.
        self.component_executor_factory = component_executor_factory

    def _component_executors(
        self, registry: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The sandbox executors a registry's component entries need.

        Empty when the registry names none, which is every registry that
        predates the runner -- so passing the result unconditionally cannot
        change an existing path. A factory is accepted so an embedder can
        supply its own builder; without one, an explicitly supplied factory
        that returns nothing still leaves the entry to fail closed rather than
        run some other executor.
        """
        entries: list[Mapping[str, Any]] = []
        if isinstance(registry, Mapping):
            raw = registry.get("interventions")
            if isinstance(raw, list):
                entries = [entry for entry in raw if isinstance(entry, Mapping)]
        if not entries:
            return {}
        if self.component_executor_factory is not None:
            return dict(self.component_executor_factory(entries) or {})
        return {}

    def detect(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str = "case",
        interventions: Mapping[str, Any] | None = None,
        frozen_judge: Mapping[str, Any] | None = None,
        frozen_stages: Mapping[str, Any] | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detect one ``{question, history}`` record (label-free).

        ``interventions`` is a validated ``detector.intervention_registry.v1``
        payload. Loading is fail-closed: any hook/type/runner that has no
        executor raises before the first LLM call.

        ``frozen_judge`` enables paired replay for self-evolution gates. It
        must be ``{"judge_result": {...}, "judge_status": {...}}`` captured
        from a baseline run. If the intervention registry targets any hook
        whose stage is frozen, replay is refused (fail-closed): freezing
        that stage would mask the intervention under test.

        ``evidence`` is an optional ``detector.evidence.v1`` snapshot of
        retrieved external facts. It travels beside the record, never inside
        it, and reaches the recall scan, the causal upgrade and the defense:
        a stage that decided on stale trajectory evidence alone would
        convict or clear on facts another stage already had. The snapshot
        digest is part of the run's provenance.
        """
        started = time.perf_counter()
        started_at = utc_now()
        with usage_scope() as observations, preparation_scope(), case_deadline_ctx(self.config.get("case_timeout_seconds")), \
                added_budget_scope(self.config["causal_audit"]):
            try:
                result = self._detect(
                    record, case_id=case_id, interventions=interventions,
                    frozen_judge=frozen_judge, frozen_stages=frozen_stages,
                    evidence=evidence,
                )
            except (ProviderError, CaseDeadlineExceeded, AddedBudgetExceeded) as failure:
                if isinstance(failure, AddedBudgetExceeded):
                    exc = ProviderError(
                        "case", str(failure),
                        error_type="added_budget_exceeded", retryable=False,
                    )
                else:
                    exc = failure if isinstance(failure, ProviderError) else ProviderError(
                        "case", str(failure), error_type="case_deadline_exceeded", retryable=False,
                    )
                result = {
                    "schema_version": "detector.detection_result.v1", "case_id": case_id,
                    "state": "unresolved", "occurrence_state": "unresolved",
                    "runtime_valid": False, "decision_authority": "invalid",
                    "responsible_step": None, "proof": {}, "stage_status": {}, "providers": {},
                    "authority_failure_reasons": [{"component": exc.route_name, "code": exc.error_type, **exc.as_dict()}],
                    "provenance": {},
                }
                base = getattr(failure, "base_detection_result", None)
                if base is not None:
                    result["base_detection_result"] = deepcopy(base)
                    result["audit_health"] = {"state": "execution_stopped", "error": exc.as_dict()}
            except BaseException as exc:
                base = getattr(exc, "base_detection_result", None)
                if base is not None:
                    base["execution"] = request_execution(
                        observations, started_at=started_at, finished_at=utc_now(),
                        elapsed_seconds=time.perf_counter() - started,
                    )
                raise
            result["execution"] = request_execution(
                observations, started_at=started_at, finished_at=utc_now(),
                elapsed_seconds=time.perf_counter() - started,
            )
            return result

    def _detect(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str = "case",
        interventions: Mapping[str, Any] | None = None,
        frozen_judge: Mapping[str, Any] | None = None,
        frozen_stages: Mapping[str, Any] | None = None,
        evidence: Mapping[str, Any] | None = None,
        causal_review: _CausalReview | None = None,
    ) -> dict[str, Any]:
        validate_record(record)
        packet = build_packet(record)
        attached_snapshot = (
            validate_snapshot(evidence) if evidence is not None else None
        )
        # A snapshot that names nothing the question or trajectory mentions
        # cannot testify about this case; it must not reopen the review or
        # enter a stage prompt. The decision and its reason are recorded.
        snapshot_application: str | None = None
        snapshot = attached_snapshot
        if attached_snapshot is not None:
            applies, reason = touches_packet(attached_snapshot, packet)
            snapshot_application = reason
            if not applies:
                snapshot = None
        effective_interventions = (
            interventions
            if interventions is not None
            else load_intervention_registry(
                self.default_interventions_path,
                require_executable_bounds=True,
            )
            if self.default_interventions_path
            and self.default_interventions_path.exists()
            else None
        )
        if (
            interventions is None
            and self.default_interventions_path is not None
            and not self.default_interventions_path.exists()
        ):
            raise ValueError(
                "configured intervention registry does not exist: "
                f"{self.default_interventions_path}"
            )
        plan = compile_runtime_plan(
            effective_interventions,
            semantic_executor=self.semantic_executor,
            component_executors=self._component_executors(effective_interventions),
        )
        replay_payload = frozen_judge if frozen_judge is not None else frozen_stages
        frozen = replay_payload
        current_source = source_identity("detector")
        identity_config = {**self.config, **(
            {"offline_feedback_policy": self.offline_feedback_policy.policy_id}
            if self.offline_feedback_policy is not None else {})}
        replay_inputs = build_replay_inputs(
            packet=packet, config=identity_config, source=current_source, snapshot=snapshot,
            entries=plan.entries,
        )
        retry_cache = current_stage_retry()
        if retry_cache is not None:
            retry_cache.bind({"case_id": case_id, **build_execution_identity(
                source=current_source, config=identity_config, record=record, packet=packet,
                evidence=attached_snapshot, registry=effective_interventions, replay=replay_payload,
                authority=authority_identity(effective_interventions, calibration=plan.conviction_calibration),
            )})
        review_input_digest = digest({"case_id": case_id, "packet": packet, "config": self.config,
                                     "registry": effective_interventions, "evidence": attached_snapshot})
        if frozen is not None and "judge_result" not in frozen:
            raise ValueError("frozen stage replay must include judge_result")
        if causal_review is not None:
            if frozen is not None or causal_review.input_digest != review_input_digest:
                raise ValueError("causal review continuation input identity differs")
            judge_result = deepcopy(causal_review.judge_result)
            judge_status = deepcopy(causal_review.judge_status)
        elif frozen is not None and "judge_result" in frozen:
            # A frozen stage that consumes the snapshot may only be replayed
            # under the same evidence: replaying one recorded without it (or
            # with another snapshot) would mask the evidence under test.
            evidence_frozen = sorted(
                key for key in ("recall_result", "upgrade_result", "hearings")
                if key in frozen
            )
            active_digest = (
                snapshot_digest(attached_snapshot)
                if attached_snapshot is not None else ""
            )
            envelope_digest = str(
                frozen.get("evidence_snapshot_digest") or ""
            )
            if evidence_frozen and envelope_digest != active_digest:
                recorded = envelope_digest[:16] if envelope_digest else "none"
                raise ValueError(
                    "frozen stage replay is refused: the frozen stage(s) "
                    f"{evidence_frozen} were recorded under evidence "
                    f"{recorded} but this run carries "
                    f"{active_digest[:16] if active_digest else 'none'}"
                )
            frozen = validate_frozen_stages(
                frozen, packet, snapshot, expected_inputs=replay_inputs, active_entries=plan.entries,
            )
            judge_result = frozen["judge_result"]
            judge_status = frozen["judge_status"]
        else:
            judge_result, judge_status = run_judge(
                packet=packet, config=self.config, plan=plan,
            )
        judge_parsed = judge_result.get("parsed") or {}
        judge_positive = bool(judge_parsed.get("is_hallucination"))
        referent_review_bases = _negative_referent_review_bases(judge_result)
        causal_policy = self.config["causal_audit"]
        # 修复API-001: action_candidates 现在直接从 judge_result 获取
        # 但需要区分"没有候选项"和"生成失败"两种情况
        # judge_status["warnings"] 中会包含失败诊断
        action_candidates = judge_result.get("action_candidates", [])
        action_candidates_degraded = any(
            w.get("component") == "action_candidates"
            for w in judge_status.get("warnings", [])
        )
        causal_diagnostics = causal_review.diagnostics if causal_review is not None else None
        plan.evidence_for(
            "judge.post",
            packet,
            judge_predicate_context(judge_parsed),
        )
        # ``upgrade.pre`` evidence is produced by the causal upgrade stage
        # itself, once, if and when that stage runs. Evaluating it here too
        # duplicated the evidence row and the deployment trace for the same
        # intervention in a single case.

        recall_result = None
        recall_status = None
        upgrade_result = None
        upgrade_status = None
        tracer_result = None
        tracer_status = None
        defense_result = None
        defense_status = None

        # The stage runners accept the snapshot only when one is active, so
        # the no-evidence path keeps its exact call shape (and every existing
        # replay/caching contract built on it).
        stage_kwargs: dict[str, Any] = (
            {"snapshot": snapshot} if snapshot is not None else {}
        )

        def run_recall_net() -> None:
            """Run the recall net (scanner + causal upgrade) on demand."""
            nonlocal recall_result, recall_status, upgrade_result, upgrade_status
            if recall_result is not None:
                return
            if frozen is not None and "recall_result" in frozen:
                recall_result = dict(frozen.get("recall_result") or {})
                recall_status = dict(frozen.get("recall_status") or {})
            else:
                recall_result, recall_status = run_recall_scan(
                    packet=packet, config=self.config, plan=plan,
                    **(stage_kwargs),
                    **({"causal_context": causal_review.context} if causal_review is not None else {}),
                    **({"seed_candidates": action_candidates} if action_candidates else {}),
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
                        **stage_kwargs,
                    )
            elif recall_result.get("ok"):
                upgrade_result = {
                    "ok": True, "upgrades": [], "upgraded_candidate": None,
                    "upgraded_candidates": [],
                }
                upgrade_status = {
                    "stage": "causal_upgrade", "state": "not_needed",
                    "attempts": 0, "view": "not_rendered", "warnings": [],
                }

        # P2修复：策略谓词只执行一次，审计元数据复用这次决策结果，
        # 避免重复触发事件或第二次资源不足时误记为false
        policy_triggered = (
            judge_status.get("state") == "ok"
            and not judge_positive
            and bool(plan.recall_triggered(
                packet,
                judge_positive=judge_positive,
                judge_parsed=judge_parsed,
            ))
        )
        recall_forced = (
            judge_status.get("state") == "ok"
            and not judge_positive
            and (
                policy_triggered
                # An active evidence snapshot reopens the review on its own:
                # the scan is the stage that can confront the delivered
                # result with the retrieved record, and it convicts nothing
                # by itself (the causal upgrade still has to pass both
                # tests). Without this, a unanimous clean judge would leave
                # the snapshot unread on exactly the cases it exists for.
                or snapshot is not None
                # P0-3: A candidate is a hypothesis without verdict authority.
                # It triggers recall to allow the scanner to find other candidates,
                # but any resulting conviction must be independently verified:
                # - The triggering hypothesis itself must be validated or rejected
                # - Any new allegation from the expanded scan must establish its own
                #   task binding, complete evidence, and delivery impact
                # - The triggering reason and the final conviction reason must be
                #   clearly distinguished in the audit trail
                or bool(action_candidates)
                or action_candidates_degraded
            )
        )
        negative_needs_recall = (
            judge_status.get("state") == "ok"
            and not judge_positive
            and (
                recall_forced
                or causal_review is not None
                or bool(referent_review_bases)
                or _judge_negative_needs_recall(judge_result, self.config)
            )
        )
        if negative_needs_recall:
            run_recall_net()
        elif judge_status.get("state") == "ok" and not judge_positive:
            recall_result = {"ok": True, "candidates": []}
            recall_status = {
                "stage": "recall_scanner", "state": "not_needed",
                "attempts": 0, "view": "not_rendered", "warnings": [],
            }
            upgrade_result = {
                "ok": True, "upgrades": [], "upgraded_candidate": None,
                "upgraded_candidates": [],
            }
            upgrade_status = {
                "stage": "causal_upgrade", "state": "not_needed",
                "attempts": 0, "view": "not_rendered", "warnings": [],
            }

        def positives_from_upgrade() -> list[dict[str, Any]]:
            candidates = list(
                (upgrade_result or {}).get("upgraded_candidates")
                or ([(upgrade_result or {}).get("upgraded_candidate")]
                    if (upgrade_result or {}).get("upgraded_candidate") else [])
            )
            return [dict(c) for c in candidates if c]

        hearings: list[dict[str, Any]] = []
        if judge_status.get("state") == "ok" and judge_positive:
            target = judge_target(judge_parsed)
            if action_candidates:
                target["action_evidence"] = linked_action_evidence(packet, action_candidates, target)
            hearings.append(target)

        def hear(positive: Mapping[str, Any]) -> tuple[
            dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
        ]:
            def after_defense(result, status):
                if (positive.get("source") == "judge" and result.get("ok") is True
                        and status.get("state") == "ok"
                        and (result.get("parsed") or {}).get("verdict") == "refuted"):
                    run_recall_net()

            def prepare_diagnostics():
                nonlocal causal_diagnostics
                if causal_diagnostics is None and causal_policy["mode"] == "tracer_context":
                    target_id = event_id_for_step(packet, positive.get("step"))
                    causal_diagnostics = build_causal_diagnostics(
                        packet=packet, config=self.config, budget=current_added_budget(),
                        mandatory_event_ids=[target_id] if target_id else [], context=None, snapshot=snapshot,
                    )
                return causal_diagnostics
            return run_hearing(
                packet=packet, positive=positive, config=self.config, plan=plan,
                frozen=frozen, diagnostics_factory=prepare_diagnostics, snapshot=snapshot,
                on_defense=after_defense,
            )

        judge_overturned = False
        if hearings:
            (
                hearings[0]["tracer_result"], hearings[0]["tracer_status"],
                hearings[0]["defense_result"], hearings[0]["defense_status"],
            ) = hear(hearings[0])
            defense_result = hearings[0]["defense_result"]
            defense_parsed = (
                defense_result.get("parsed")
                if isinstance(defense_result.get("parsed"), Mapping) else {}
            )
            if str(defense_parsed.get("verdict") or "") == "refuted":
                judge_overturned = True

        if negative_needs_recall or judge_overturned:
            # A judge-negative case, or a judge conviction the defense
            # overturned: the recall net hears the case next.
            run_recall_net()
            for candidate in positives_from_upgrade():
                hearing = recall_target(candidate, judge_parsed.get("contracts") or [])
                (
                    hearing["tracer_result"],
                    hearing["tracer_status"],
                    hearing["defense_result"],
                    hearing["defense_status"],
                ) = hear(hearing)
                hearings.append(hearing)

        if hearings:
            first = hearings[0]
            tracer_result, tracer_status = first["tracer_result"], first["tracer_status"]
            defense_result, defense_status = first["defense_result"], first["defense_status"]
        hearing_facts = [
            {
                "candidate_id": h.get("candidate_id"),
                "source": h.get("source"),
                "defense_verdict": str(
                    ((h.get("defense_result") or {}).get("parsed") or {}).get(
                        "verdict"
                    ) or "none"
                ),
            }
            for h in hearings
        ]
        common_guard_facts = {
            "judge_positive": str(judge_positive).lower(),
            "judge_confidence": str(judge_parsed.get("confidence") or ""),
            "upgrade_degraded": str(
                bool(
                    (upgrade_status or {}).get("state") == "degraded_missing_rows"
                    or (upgrade_result or {}).get("error_type")
                    == "degraded_missing_rows"
                )
            ).lower(),
        }
        guard_fact_rows = []
        for hearing in hearings:
            hearing_defense = hearing.get("defense_result") or {}
            hearing_parsed = (
                hearing_defense.get("parsed") or {}
            ) if isinstance(hearing_defense.get("parsed"), Mapping) else {}
            hearing_status = hearing.get("defense_status") or {}
            source = str(hearing.get("source") or "none")
            guard_fact_rows.append({
                **common_guard_facts,
                "positive_source": source,
                "defense_verdict": str(
                    hearing_parsed.get("verdict") or "none"
                ),
                "defense_view_degraded": str(
                    bool(hearing_status)
                    and (
                        hearing_status.get("view") == "degraded"
                        or hearing_status.get("state") != "ok"
                    )
                ).lower(),
                "recall_upgrade": (
                    "present" if source == "recall_upgrade" else "none"
                ),
            })
        if not guard_fact_rows:
            guard_fact_rows.append({
                **common_guard_facts,
                "positive_source": "none",
                "defense_verdict": "none",
                "defense_view_degraded": "false",
                "recall_upgrade": "none",
            })
        guard_actions = plan.guard_actions(
            packet,
            fact_rows=guard_fact_rows,
        )
        # P0-3: 记录触发假设和实际指控的来源
        # P2-1修复：保存原始action_candidates的ID，用于后续比较
        recall_trigger_metadata = {
            "triggered_by_action_candidates": bool(action_candidates),
            "triggered_by_snapshot": snapshot is not None,
            "triggered_by_policy": policy_triggered,
            "triggered_by_judge_uncertainty": (
                judge_status.get("state") == "ok"
                and not judge_positive
                and _judge_negative_needs_recall(judge_result, self.config)
            ),
            "triggered_by_unverified_referent": (
                judge_status.get("state") == "ok" and not judge_positive and bool(referent_review_bases)
            ),
            "unverified_referent_bases": referent_review_bases,
            "action_candidates_count": len(action_candidates) if action_candidates else 0,
            # P2-1修复：保存原始触发候选ID，用于与最终定罪候选进行比较
            "original_action_candidate_ids": [
                c.get("candidate_id") for c in (action_candidates or []) if c.get("candidate_id")
            ],
        }
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
            mechanical_evidence=plan.mechanical_guard_evidence,
            hearings=hearings,
            hearing_facts=hearing_facts,
            predicate_conviction_verdicts=plan.predicate_conviction_verdicts,
            recall_trigger_metadata=recall_trigger_metadata,
        )
        provenance: dict[str, Any] = {
            "packet_hash": digest(packet),
            "replay_inputs": replay_inputs,
            "execution_identity": build_execution_identity(
                source=current_source, config=identity_config,
                record=record, packet=packet, evidence=attached_snapshot,
                registry=effective_interventions,
                authority=authority_identity(effective_interventions, calibration=plan.conviction_calibration),
                replay=replay_payload,
            ),
        }
        if retry_cache is not None:
            provenance["stage_retry_reuse"] = deepcopy(retry_cache.reused)
        if attached_snapshot is not None:
            provenance["evidence_snapshot_digest"] = snapshot_digest(
                attached_snapshot
            )
            provenance["evidence_applied"] = snapshot is not None
            provenance["evidence_application_reason"] = snapshot_application
        if effective_interventions is not None:
            provenance["interventions_hash"] = str(
                effective_interventions.get("content_hash") or ""
            )
        if replay_payload is not None:
            provenance["frozen_judge_replay"] = True
            frozen_keys = [
                key for key in (replay_payload or {})
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
        proof["recall_trigger_metadata"] = deepcopy(recall_trigger_metadata)
        if action_candidates:
            proof["action_candidates"] = action_candidates
        if action_candidates_degraded:
            proof["action_candidates_degraded"] = True
        if judge_result.get("action_forecast") is not None:
            proof["action_forecast"] = judge_result["action_forecast"]
            proof["action_forecast_used"] = judge_result.get("action_forecast_used", False)
            ballots = judge_result.get("raw")
            ballots = ballots if isinstance(ballots, list) else [ballots]
            proof["next_action_vote_comparisons"] = [
                ballot.get("next_action_comparisons", []) for ballot in ballots if isinstance(ballot, Mapping)
            ]
            proof["next_action_vote_health"] = [
                ballot.get("next_action_comparison_health") for ballot in ballots if isinstance(ballot, Mapping)
            ]
        proof["recall_candidates"] = (
            recall_result.get("candidates") if recall_result else None
        )
        proof["causal_upgrade"] = (
            upgrade_result.get("upgraded_candidate") if upgrade_result else None
        )
        proof["causal_upgrades"] = (
            upgrade_result.get("upgraded_candidates") if upgrade_result else None
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
        proof["hearings"] = [
            {
                "source": hearing.get("source"),
                "candidate_id": hearing.get("candidate_id"),
                "positive": hearing_target(hearing),
                "evidence_citations": hearing.get("evidence_citations") or [],
                "tracer_result": hearing.get("tracer_result"),
                "tracer_status": hearing.get("tracer_status"),
                "defense_result": hearing.get("defense_result"),
                "defense_status": hearing.get("defense_status"),
            }
            for hearing in hearings
        ]
        proof["evidence"] = list(plan.evidence)
        proof["deployment"] = plan.deployment_summary()
        proof["external_evidence"] = snapshot
        proof["referent_provenance"] = [referent_provenance(
            packet, hearing_target(hearing), snapshot,
        ) for hearing in hearings]
        output = build_detection_output(
            case_id=case_id, packet=packet, proof=proof, stage_status=stage_status,
            providers=providers, provenance=provenance,
        )
        try:
            if causal_diagnostics is not None:
                output["proof"]["causal_diagnostics"] = causal_diagnostics
            elif causal_policy["mode"] != "off":
                mandatory = list(dict.fromkeys(eid for eid in (
                    event_id_for_step(packet, hearing.get("step")) for hearing in hearings
                ) if eid is not None))
                output["proof"]["causal_diagnostics"] = build_causal_diagnostics(
                    packet=packet, config=self.config, budget=current_added_budget(),
                    mandatory_event_ids=mandatory,
                    context=None if causal_policy["mode"] == "tracer_context" else judge_predicate_context(judge_parsed),
                    snapshot=snapshot,
                )
                causal_diagnostics = output["proof"]["causal_diagnostics"]
            if causal_diagnostics is not None:
                causal_diagnostics["semantic_trace"] = build_semantic_trace(packet)
            if (causal_review is None and causal_policy["mode"] == "tracer_context"
                    and output["state"] == "verified_clean"
                    and (recall_status or {}).get("state") == "not_needed"):
                budget = current_added_budget()
                decision = causal_recall_decision(
                    (causal_diagnostics or {}).get("audit"), judge_result=judge_result,
                    policy=causal_policy, budget=budget, offline_policy=self.offline_feedback_policy,
                )
                output["proof"]["negative_feedback"] = decision
                if decision["state"] == "eligible":
                    review = _CausalReview(review_input_digest, deepcopy(judge_result), deepcopy(judge_status),
                                           deepcopy(causal_diagnostics), seal({
                        "schema_version": "detector.causal_recall_context.v1", "packet_digest": digest(packet),
                        "anomalies": decision["anomalies"], "decision_authority": "none",
                    }))
                    try:
                        if not budget.start_negative_review():
                            output["proof"]["negative_feedback"] = {
                                **decision, "state": "inactive", "reason": "review_already_attempted"}
                            return output
                        with added_request_scope(budget):
                            reviewed = self._detect(
                                record, case_id=case_id, interventions=effective_interventions,
                                evidence=evidence, causal_review=review,
                            )
                    except AddedBudgetExceeded as exc:
                        output["proof"]["negative_feedback"] = {**decision, "state": "budget_exhausted", "reason": exc.resource}
                    else:
                        reviewed["proof"]["negative_feedback"] = {**decision, "state": "reviewed"}
                        reviewed["base_detection_result"] = deepcopy(output)
                        output = reviewed
        except BaseException as exc:
            exc.base_detection_result = deepcopy(output)
            raise
        return output


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
    interventions: Mapping[str, Any] | None = None,
    frozen_judge: Mapping[str, Any] | None = None,
    frozen_stages: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience entry with optional paired-replay stage freezing."""
    detector = Detector(config) if config is not None else _default_detector()
    return detector.detect(
        record, case_id=case_id,
        interventions=interventions, frozen_judge=frozen_judge,
        frozen_stages=frozen_stages, evidence=evidence,
    )


__all__ = ["Detector", "detect"]
