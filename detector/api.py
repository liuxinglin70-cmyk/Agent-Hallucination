"""Single Detector API: validate -> packet -> judge -> defense -> adjudicate."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from detector.adjudication import adjudicate
from detector.config import load_config, validate_config
from detector.identity import build_execution_identity, digest, source_identity
from detector.defense import _parse_defense, _validate_defense_evidence, run_defense
from detector.evidence import (
    snapshot_digest,
    touches_packet,
    validate_snapshot,
)
from detector.interventions import (
    SemanticExecutor,
    compile_runtime_plan,
    load_intervention_registry,
)
from detector.judge import _parse_judge, run_judge
from detector.packet import build_packet, validate_record
from detector.predicate_context import judge_predicate_context
from detector.provider import ProviderError, accumulate_usage, case_deadline_ctx
from detector.runtime import usage_scope, utc_now
import time
from detector.recaller import (
    _parse_candidates,
    _parse_upgrades,
    assemble_upgraded_candidates,
    run_causal_upgrade,
    run_recall_scan,
)
from detector.result import build_detection_output
from detector.step_tracer import _parse_tracer, run_step_tracer


_FROZEN_STAGE_HOOKS: dict[str, frozenset[str]] = {
    "judge_result": frozenset({"judge.system"}),
    "recall_result": frozenset({"scanner.system", "recall.trigger"}),
    "upgrade_result": frozenset({"upgrade.pre", "upgrade.system"}),
    "tracer_result": frozenset({"tracer.system"}),
    "defense_result": frozenset({"defense.pre", "defense.system"}),
}


def _judge_negative_needs_recall(
    judge_result: Mapping[str, Any], config: Mapping[str, Any],
) -> bool:
    """Use the judge ensemble's uncertainty instead of rescanning 3/3 clean cases."""
    requested = max(
        1, int((config.get("judge_route") or {}).get("votes", 1) or 1),
    )
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


def _frozen_masked_stages(
    frozen: Mapping[str, Any], targeted_hooks: set[str],
) -> list[str]:
    """Frozen stages whose live inputs an active intervention would change.

    Replaying such a stage would mask the intervention under test, so the
    replay must be refused fail-closed. ``judge.post`` is absent: it is a
    post-hoc deterministic evidence hook that still runs during replay.
    Hearing rows count as frozen whenever any hearing carries them.
    """
    def stage_frozen(stage_key: str) -> bool:
        if stage_key in frozen:
            return True
        return any(
            stage_key in entry for entry in frozen.get("hearings") or ()
        )

    return sorted(
        {
            stage_key.removesuffix("_result")
            for stage_key, hooks in _FROZEN_STAGE_HOOKS.items()
            if targeted_hooks & hooks and stage_frozen(stage_key)
        }
    )


def _frozen_stage(
    frozen: Mapping[str, Any], name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = frozen.get(f"{name}_result")
    status = frozen.get(f"{name}_status")
    if not isinstance(result, Mapping) or not isinstance(status, Mapping):
        raise ValueError(f"frozen {name} replay requires result and status objects")
    result_copy = dict(result)
    status_copy = dict(status)
    if bool(result_copy.get("ok")) != (status_copy.get("state") == "ok"):
        raise ValueError(f"frozen {name} result/status authority mismatch")
    return result_copy, status_copy


def _validate_frozen_stages(
    frozen: Mapping[str, Any], packet: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize replay payloads through the same contracts as live stages."""
    if not isinstance(frozen, Mapping):
        raise ValueError("frozen stage replay payload must be an object")
    normalized = dict(frozen)
    if frozen.get("packet_hash") and frozen["packet_hash"] != digest(packet):
        raise ValueError("frozen stages belong to a different packet")
    judge_result, judge_status = _frozen_stage(frozen, "judge")
    if judge_result.get("ok"):
        parsed, error = _parse_judge(judge_result.get("parsed"), packet)
        if parsed is None:
            raise ValueError(f"frozen judge_result contract error: {error}")
        judge_result["parsed"] = parsed
    normalized["judge_result"] = judge_result
    normalized["judge_status"] = judge_status

    if "recall_result" in frozen:
        recall_result, recall_status = _frozen_stage(frozen, "recall")
        if recall_result.get("ok"):
            candidates, error = _parse_candidates(
                {"candidates": recall_result.get("candidates") or []}, packet, snapshot,
            )
            if candidates is None:
                raise ValueError(f"frozen recall_result contract error: {error}")
            recall_result["candidates"] = candidates
        normalized["recall_result"] = recall_result
        normalized["recall_status"] = recall_status

    if "upgrade_result" in frozen:
        upgrade_result, upgrade_status = _frozen_stage(frozen, "upgrade")
        recall_candidates = (
            normalized.get("recall_result") or {}
        ).get("candidates") or []
        if upgrade_result.get("ok"):
            upgrades, missing, error = _parse_upgrades(
                {
                    "upgrades": upgrade_result.get("upgrades") or [],
                },
                recall_candidates,
                packet, snapshot,
            )
            if upgrades is None or missing:
                raise ValueError(
                    f"frozen upgrade_result contract error: {error or missing}"
                )
            upgraded_candidates = assemble_upgraded_candidates(
                recall_candidates, upgrades,
            )
            upgrade_result["upgraded_candidates"] = upgraded_candidates
            upgrade_result["upgraded_candidate"] = (
                upgraded_candidates[0] if upgraded_candidates else None
            )
            upgrade_result["upgrades"] = upgrades
        else:
            upgrade_result["upgraded_candidates"] = []
            upgrade_result["upgraded_candidate"] = None
        normalized["upgrade_result"] = upgrade_result
        normalized["upgrade_status"] = upgrade_status

    if "hearings" in frozen:
        entries = frozen.get("hearings")
        if not isinstance(entries, list):
            raise ValueError("frozen hearings must be a list")
        normalized_hearings: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError("frozen hearing entry must be an object")
            clean = {
                "source": str(entry.get("source") or ""),
                "candidate_id": entry.get("candidate_id"),
                "positive": dict(entry.get("positive") or {}),
            }
            if "tracer_result" in entry or "tracer_status" in entry:
                tracer_result, tracer_status = _frozen_stage(entry, "tracer")
                if tracer_result.get("ok"):
                    parsed, error = _parse_tracer(
                        tracer_result.get("parsed"), packet,
                    )
                    if parsed is None:
                        raise ValueError(
                            f"frozen tracer_result contract error: {error}"
                        )
                    tracer_result["parsed"] = parsed
                clean["tracer_result"] = tracer_result
                clean["tracer_status"] = tracer_status
            if "defense_result" in entry or "defense_status" in entry:
                defense_result, defense_status = _frozen_stage(entry, "defense")
                if defense_result.get("ok"):
                    parsed, error = _parse_defense(defense_result.get("parsed"))
                    if parsed is None:
                        raise ValueError(
                            f"frozen defense_result contract error: {error}"
                        )
                    positive = {"step": clean["positive"].get("step")}
                    parsed, error = _validate_defense_evidence(
                        parsed, packet, positive, snapshot,
                    )
                    if parsed is None:
                        raise ValueError(
                            f"frozen defense_result evidence error: {error}"
                        )
                    defense_result["parsed"] = parsed
                clean["defense_result"] = defense_result
                clean["defense_status"] = defense_status
            normalized_hearings.append(clean)
        normalized["hearings"] = normalized_hearings
    return normalized


class Detector:
    """One configured, stateless-between-calls detector."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        require_credentials: bool = True,
        semantic_executor: "SemanticExecutor | None" = None,
        component_executor_factory: Any | None = None,
    ) -> None:
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
        with usage_scope() as observations, case_deadline_ctx(self.config.get("case_timeout_seconds")):
            try:
                result = self._detect(
                    record, case_id=case_id, interventions=interventions,
                    frozen_judge=frozen_judge, frozen_stages=frozen_stages,
                    evidence=evidence,
                )
            except ProviderError as exc:
                result = {
                    "schema_version": "detector.detection_result.v1", "case_id": case_id,
                    "state": "unresolved", "occurrence_state": "unresolved",
                    "runtime_valid": False, "decision_authority": "invalid",
                    "responsible_step": None, "proof": {}, "stage_status": {}, "providers": {},
                    "authority_failure_reasons": [{"component": exc.route_name, "code": exc.error_type, **exc.as_dict()}],
                    "provenance": {},
                }
            usage: dict[str, Any] = {}
            for observation in observations:
                if isinstance(observation.get("usage"), Mapping):
                    accumulate_usage(usage, observation["usage"])
            result["execution"] = {
                "schema_version": "detector.execution.v1", "started_at": started_at,
                "finished_at": utc_now(), "elapsed_seconds": time.perf_counter() - started,
                "requests": observations, "provider_attempts": len(observations),
                "usage": usage or None,
                "usage_missing_attempts": sum(o.get("usage") is None for o in observations),
            }
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
        targeted_hooks = {
            str(entry.get("hook") or "") for entry in plan.entries
        }

        replay_payload = frozen_judge if frozen_judge is not None else frozen_stages
        frozen = replay_payload
        if frozen is not None and any(
            str(key).endswith(("_result", "_status")) for key in frozen
        ) and "judge_result" not in frozen:
            raise ValueError("frozen stage replay must include judge_result")
        if frozen is not None and "judge_result" in frozen:
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
            frozen = _validate_frozen_stages(frozen, packet, snapshot)
            judge_result = frozen["judge_result"]
            judge_status = frozen["judge_status"]
            masked = _frozen_masked_stages(frozen, targeted_hooks)
            if masked:
                raise ValueError(
                    "frozen stage replay is refused: active interventions "
                    f"target the frozen stage(s) {masked} (it would mask "
                    "the intervention under test)"
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

        recall_forced = (
            judge_status.get("state") == "ok"
            and not judge_positive
            and (
                plan.recall_triggered(
                    packet,
                    judge_positive=judge_positive,
                    judge_parsed=judge_parsed,
                )
                # An active evidence snapshot reopens the review on its own:
                # the scan is the stage that can confront the delivered
                # result with the retrieved record, and it convicts nothing
                # by itself (the causal upgrade still has to pass both
                # tests). Without this, a unanimous clean judge would leave
                # the snapshot unread on exactly the cases it exists for.
                or snapshot is not None
            )
        )
        negative_needs_recall = (
            judge_status.get("state") == "ok"
            and not judge_positive
            and (
                recall_forced
                or _judge_negative_needs_recall(judge_result, self.config)
            )
        )
        if negative_needs_recall:
            # Multi-vote consensus is already a recall signal. Scanner is
            # reserved for dissent, abstention or low-confidence negatives.
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

        def positive_from_judge() -> dict[str, Any]:
            return {
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

        def positive_from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "source": "recall_upgrade",
                "step": candidate.get("corrected_step"),
                "reason": candidate.get("claim"),
                # Information asymmetry: defense/tracer for a recall upgrade
                # receive candidate + trajectory + task contract only, never
                # the judge's correct_result / is_hallucination.
                "delivered_result": "",
                "correct_result": "",
                "evidence_refs": candidate.get("evidence_refs") or [],
                "evidence_citations": candidate.get("evidence_citations") or [],
                "quote": candidate.get("quote") or "",
                "confidence": "",
                # Task contracts are allowed; judge verdict anchors are not.
                "contracts": judge_parsed.get("contracts") or [],
            }

        hearings: list[dict[str, Any]] = []
        if judge_status.get("state") == "ok" and judge_positive:
            hearings.append(positive_from_judge())

        # Upstream replay may introduce new hearings. Reuse a hearing only
        # when its actual input is unchanged, not merely its candidate id.
        flow_frozen = frozen is not None

        def frozen_hearing(positive: Mapping[str, Any]) -> dict[str, Any] | None:
            if not flow_frozen:
                return None
            return next(
                (
                    entry for entry in frozen.get("hearings") or ()
                    if isinstance(entry, Mapping)
                    and str(entry.get("source") or "")
                    == str(positive.get("source") or "")
                    and str(entry.get("candidate_id") or "")
                    == str(positive.get("candidate_id") or "")
                    # Compare exactly the fields the frozen envelope carries
                    # (proof.hearings[].positive is the projected 7-key shape).
                    # Including contracts/confidence, which the projection
                    # drops, made this comparison always fail, so every
                    # "frozen" hearing silently re-ran the live tracer and
                    # defense and re-introduced the provider noise the freeze
                    # exists to remove.
                    and all(
                        (entry.get("positive") or {}).get(key) == positive.get(key)
                        for key in ("source", "step", "reason", "quote",
                                    "evidence_refs", "delivered_result",
                                    "correct_result")
                    )
                ),
                None,
            )

        def run_hearing(positive: Mapping[str, Any]) -> tuple[
            dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
        ]:
            """Tracer + defense for one positive; defense is decisive."""
            entry = frozen_hearing(positive)
            if entry is not None and "tracer_result" in entry:
                tracer_r = dict(entry["tracer_result"])
                tracer_s = dict(entry["tracer_status"])
            else:
                tracer_r, tracer_s = run_step_tracer(
                    packet=packet,
                    delivered_result=str(positive.get("delivered_result") or ""),
                    correct_result=str(positive.get("correct_result") or ""),
                    config=self.config,
                    plan=plan,
                    positive=positive,
                )
            if entry is not None and "defense_result" in entry:
                defense_r = dict(entry["defense_result"])
                defense_s = dict(entry["defense_status"])
            else:
                defense_r, defense_s = run_defense(
                    packet=packet,
                    positive=positive,
                    config=self.config,
                    plan=plan,
                    **stage_kwargs,
                )
            return tracer_r, tracer_s, defense_r, defense_s

        judge_overturned = False
        if hearings:
            tracer_result, tracer_status, defense_result, defense_status = (
                run_hearing(hearings[0])
            )
            hearings[0]["tracer_result"] = tracer_result
            hearings[0]["tracer_status"] = tracer_status
            hearings[0]["defense_result"] = defense_result
            hearings[0]["defense_status"] = defense_status
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
                hearing = positive_from_candidate(candidate)
                hearing["candidate_id"] = candidate.get("candidate_id")
                (
                    hearing["tracer_result"],
                    hearing["tracer_status"],
                    hearing["defense_result"],
                    hearing["defense_status"],
                ) = run_hearing(hearing)
                hearings.append(hearing)

        defense_parsed = (
            (defense_result or {}).get("parsed") or {}
        ) if isinstance((defense_result or {}).get("parsed"), Mapping) else {}
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
        )
        provenance: dict[str, Any] = {
            "packet_hash": digest(packet),
            "execution_identity": build_execution_identity(
                source=source_identity("detector"), config=self.config,
                record=record, packet=packet, evidence=attached_snapshot,
                registry=effective_interventions,
                replay=replay_payload,
            ),
        }
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
                "positive": {
                    key: hearing.get(key) for key in (
                        "source", "step", "reason", "evidence_refs", "quote",
                        "delivered_result", "correct_result",
                    )
                },
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
