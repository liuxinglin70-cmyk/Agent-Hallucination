"""Real PACA-TCL v3.5 evaluator adapter for BCCSE skill evolution.

The evolution core deliberately depends on an abstract evaluator.  This module
provides the production adapter: it evaluates a typed genome through the frozen
v3.5 detector, returns only anonymous compiler records on the evolution
partition, reuses the sealed v3.5 artifact for the exact baseline genome, and
performs paired parent/child replay without allowing selection cases to produce
mutation experience.

P0 use is architecture-diagnostic only.  CESV remains auxiliary, CRS weights
remain frozen, and the adapter never opens D or outer partitions by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest

from ...utils import ocse_packet as packet
from . import proof_engine as v35
from .skill_integration import (
    compile_genome_for_paca,
    integrated_v35_provider,
)
from .basic_runner import (
    _atomic_write_json,
    _global_cache_path,
    private_ref,
    read_json,
    write_json,
    write_jsonl,
)
from ..bccse.types import (
    EvaluationOutcome,
    ExperienceRecord,
    FailureEpisode,
    anonymous_ref,
)
from ..bccse.step_attributor import ErrorType
from ..bccse.genome import SkillGenome, create_baseline_genome
from ..bccse.mutator import MutationProvider
from ..bccse.promotion_gates import G1_CHECKS, MetricSnapshot, ReplayEvidence


_CACHE_DIRS = (
    "layered_v2",
    "construct_census_cache",
    "focused_prosecution_cache",
    "defense_cache",
    "appeal_prosecutor_cache",
    "appeal_defender_cache",
    "counterfactual_cache",
)
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("prediction JSONL rows must be objects")
    return rows


def _json_object_provider_call(
    *,
    route: dict[str, Any],
    system: str,
    user: str,
    cache_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generic cached JSON-object transport without PACA census assumptions."""

    payload = {
        "model": route["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # response_format not supported by all models (e.g. DeepSeek-V4-Pro
    # returns HTTP 400 when response_format is sent). Only add it for
    # models that support it (Flash supports it; Pro does not).
    model_name = str(route.get("model", ""))
    if "Pro" not in model_name:
        payload["response_format"] = {"type": "json_object"}
    body = packet.canonical_json(payload).encode("utf-8")
    # v3.5.1: 缓存键含 "eval_jsonobj" 前缀，与 v1 的 provider_call 区分
    # （两者 payload 可能相同但 schema 验证不同，必须隔离缓存）
    request_hash = hashlib.sha256(
        b"eval_jsonobj|" + (route["endpoint"] + "|" + route["model"] + "|").encode("utf-8") + body
    ).hexdigest()

    # v3.5.1: 全局缓存查找（不依赖 cache_dir，所有 output_dir 共享）
    global_target = _global_cache_path(request_hash)
    if global_target.is_file():
        try:
            cached = read_json(global_target)
            if cached.get("request_hash") == request_hash:
                parsed = cached.get("parsed")
                if not isinstance(parsed, dict):
                    raise ValueError("cached mutation response root is not an object")
                return parsed, {
                    "cache_hit": True,
                    "request_hash": request_hash,
                    "usage": cached.get("usage") or {},
                    "cache_source": "global",
                }
        except Exception:
            pass  # 缓存文件损坏，继续调用大模型

    api_key = os.environ[str(route["api_key_environment"])]
    retries = int(route.get("transport_retries", 2))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urlrequest.Request(
                route["endpoint"].rstrip("/") + "/chat/completions",
                data=body,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            started = time.time()
            with urlrequest.urlopen(
                request,
                timeout=int(route.get("timeout_seconds", 300)),
            ) as response:
                raw = response.read()
            value = json.loads(raw.decode("utf-8"))
            parsed = json.loads(value["choices"][0]["message"]["content"])
            if not isinstance(parsed, dict):
                raise ValueError("mutation provider response root is not an object")
            cached = {
                "request_hash": request_hash,
                "response_hash": hashlib.sha256(raw).hexdigest(),
                "parsed": parsed,
                "usage": value.get("usage") or {},
                "latency_seconds": time.time() - started,
                "contains_credentials": False,
            }
            # v3.5.1: 写入全局缓存（原子写，防止并发冲突）
            try:
                _atomic_write_json(global_target, cached)
            except Exception:
                pass  # 写缓存失败不影响主流程
            return parsed, {
                "cache_hit": False,
                "request_hash": request_hash,
                "usage": cached["usage"],
                "latency_seconds": cached["latency_seconds"],
                "cache_source": "global",
            }
        except (
            urlerror.URLError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _walk_provider_meta(value: Any):
    if isinstance(value, Mapping):
        if value.get("request_hash"):
            yield value
        for child in value.values():
            yield from _walk_provider_meta(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_provider_meta(child)


def _prediction_state(row: Mapping[str, Any]) -> bool | None:
    state = str(row.get("state") or "")
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


def _row_correct(row: Mapping[str, Any]) -> bool:
    predicted = _prediction_state(row)
    return predicted is not None and predicted is bool(row.get("gold_label"))


def _candidate_origins(row: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for candidate in row.get("census") or ():
        if not isinstance(candidate, Mapping):
            continue
        value = (
            candidate.get("origin_responsible_step")
            or candidate.get("origin_step")
            or candidate.get("responsible_step")
        )
        if value is not None:
            refs.append(str(value))
    # v3.5 commonly exposes event-addressed candidates while P0 gold is
    # step-addressed. Do not fabricate a miss across incomparable namespaces.
    gold_steps = tuple(str(item) for item in row.get("gold_steps") or ())
    return tuple(dict.fromkeys(refs or gold_steps))


def experience_record_from_row(row: Mapping[str, Any]) -> ExperienceRecord:
    """Convert one v3.5 row into the compiler-only, raw-text-free view."""

    state = str(row.get("state") or "")
    gold_steps = tuple(str(item) for item in row.get("gold_steps") or ())
    health = row.get("evidence_health") or {}
    proof = row.get("proof_adjudication") or {}
    quarantined = (
        len(proof.get("quarantined_error_certificate_ids") or ())
        + len(proof.get("quarantined_defense_certificate_ids") or ())
    )
    provider_metas = tuple(_walk_provider_meta(row.get("providers") or {}))
    return ExperienceRecord(
        record_key=str(row.get("case_ref") or ""),
        partition="evolution",
        gold_positive=bool(row.get("gold_label")),
        predicted_positive=_prediction_state(row),
        gold_responsible_ref=gold_steps[0] if gold_steps else None,
        predicted_responsible_ref=(
            None if row.get("responsible_step") is None
            else str(row.get("responsible_step"))
        ),
        candidate_origin_refs=_candidate_origins(row),
        analysis_dimension="trajectory_causal_validity",
        origin_relation="earliest_agent_controlled_origin",
        evidence_obligation="complete_admissible_causal_chain",
        clean_boundary="supported_or_correct_or_ordinary_failure",
        certificate_valid=bool(row.get("runtime_valid")),
        unresolved=state == "unresolved",
        model_calls=len(provider_metas),
        latency_seconds=sum(float(meta.get("latency_seconds") or 0.0) for meta in provider_metas),
        metadata={
            "proof_state": state,
            "wide_construct_state": row.get("wide_construct_state"),
            "construct_view_status": health.get("construct_view_status"),
            "candidate_count": len(row.get("census") or ()),
            "required_candidate_count": int(health.get("required_candidate_count") or 0),
            "resolved_candidate_count": int(health.get("resolved_candidate_count") or 0),
            "quarantined_certificate_count": quarantined,
        },
    )


def metric_snapshot(rows: Sequence[Mapping[str, Any]]) -> MetricSnapshot:
    metrics = v35.evaluate([dict(row) for row in rows])
    metas = tuple(
        meta
        for row in rows
        for meta in _walk_provider_meta(row.get("providers") or {})
    )
    case_count = len(rows)
    unresolved = sum(str(row.get("state") or "") == "unresolved" for row in rows)
    return MetricSnapshot(
        macro_f1=float(metrics.get("macro_f1") or 0.0),
        precision=float(metrics.get("precision") or 0.0),
        hallucination_recall=float(metrics.get("recall") or 0.0),
        clean_fpr=float(metrics.get("clean_fpr") or 0.0),
        responsible_step_accuracy=float(
            metrics.get("responsible_step_accuracy_all_gold_positive") or 0.0
        ),
        candidate_origin_coverage=float(
            metrics.get("candidate_origin_coverage_on_gold_positive") or 0.0
        ),
        valid_output_rate=float(metrics.get("runtime_valid_rate") or 0.0),
        causal_chain_validity=float(metrics.get("causal_chain_validity") or 0.0),
        unresolved_rate=unresolved / case_count if case_count else 0.0,
        invalid_rate=1.0 - float(metrics.get("runtime_valid_rate") or 0.0),
        model_calls=len(metas),
        latency_seconds=sum(float(meta.get("latency_seconds") or 0.0) for meta in metas),
        cost=0.0,
    )


@dataclass
class DeepSeekMutationProvider(MutationProvider):
    """Use the registered JSON provider transport for reflective mutation."""

    route: dict[str, Any]
    cache_dir: Path
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if str(self.route.get("model") or "") != model:
            raise ValueError("mutation model differs from the registered route")
        value, meta = _json_object_provider_call(
            route=self.route,
            system=system,
            user=user,
            cache_dir=Path(self.cache_dir),
        )
        self.calls.append(dict(meta))
        return value


@dataclass
class PacaTclV35EvolutionEvaluator:
    """Stateful real evaluator with baseline artifacts and genome caches."""

    config: dict[str, Any]
    records: Mapping[str, dict[str, Any]]
    census_root: Path
    output_root: Path
    baseline_predictions_path: Path
    anonymization_salt: str
    baseline_cache_root: Path | None = None
    max_parallel_cases: int = 4
    _baseline_by_ref: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)
    _rows: dict[tuple[str, tuple[str, ...]], tuple[dict[str, Any], ...]] = field(
        init=False, default_factory=dict,
    )

    def __post_init__(self) -> None:
        if not self.anonymization_salt:
            raise ValueError("anonymization salt is required")
        self.census_root = Path(self.census_root)
        self.output_root = Path(self.output_root)
        self.baseline_predictions_path = Path(self.baseline_predictions_path)
        if self.baseline_cache_root is not None:
            self.baseline_cache_root = Path(self.baseline_cache_root)
        for row in _read_jsonl(self.baseline_predictions_path):
            ref = str(row.get("case_ref") or "")
            if not ref or ref in self._baseline_by_ref:
                raise ValueError("baseline predictions contain missing/duplicate case_ref")
            self._baseline_by_ref[ref] = row

    def _baseline_rows(self, case_keys: Sequence[str]) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for case_key in case_keys:
            ref = private_ref(case_key, "case_")
            if ref not in self._baseline_by_ref:
                raise KeyError(f"baseline prediction absent for requested case: {ref}")
            rows.append(dict(self._baseline_by_ref[ref]))
        return tuple(rows)

    def _genome_root(self, genome: SkillGenome) -> Path:
        root = self.output_root / "genomes" / genome.content_hash
        marker = root / ".baseline_cache_seeded"
        if self.baseline_cache_root is not None and not marker.exists():
            for name in _CACHE_DIRS:
                source = self.baseline_cache_root / name
                if source.is_dir():
                    shutil.copytree(source, root / name, dirs_exist_ok=True)
            root.mkdir(parents=True, exist_ok=True)
            marker.write_text("seeded\n", encoding="utf-8")
        return root

    def _execute(
        self,
        genome: SkillGenome,
        case_keys: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        baseline = create_baseline_genome()
        if genome.content_hash == baseline.content_hash:
            return self._baseline_rows(case_keys)
        root = self._genome_root(genome)
        # Round 13 structural fix: conditional integration.
        # Only inject the genome delta for cases that are unresolved in the
        # baseline.  Already-resolved cases use the baseline prediction directly,
        # preventing candidate_origin_coverage regressions (Rounds 11-12 showed
        # that global prompt injection drops coverage from 1.0 to 0.5).
        baseline_rows = self._baseline_rows(case_keys)
        unresolved_keys: list[str] = []
        resolved_rows: dict[str, dict[str, Any]] = {}
        for case_key, row in zip(case_keys, baseline_rows):
            state = str(row.get("state") or "")
            if state == "unresolved":
                unresolved_keys.append(case_key)
            else:
                resolved_rows[case_key] = dict(row)
        by_case: dict[str, dict[str, Any]] = dict(resolved_rows)
        if unresolved_keys:
            with integrated_v35_provider(genome=genome, config=self.config):
                with ThreadPoolExecutor(max_workers=self.max_parallel_cases) as executor:
                    futures = {
                        executor.submit(
                            v35.adjudicate_case,
                            case_id=case_key,
                            record=self.records[case_key],
                            config=self.config,
                            output_dir=root,
                            census_root=self.census_root,
                        ): case_key
                        for case_key in unresolved_keys
                    }
                    for future in as_completed(futures):
                        by_case[futures[future]] = future.result()
        rows = tuple(by_case[key] for key in case_keys)
        write_jsonl(root / "latest_predictions.jsonl", rows)
        return rows

    def _get_rows(
        self,
        genome: SkillGenome,
        case_keys: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        key = (genome.content_hash, tuple(case_keys))
        if key not in self._rows:
            self._rows[key] = self._execute(genome, case_keys)
        return self._rows[key]

    def evaluate(
        self,
        *,
        genome: SkillGenome,
        case_keys: Sequence[str],
        partition: str,
        evaluation_id: str,
    ) -> EvaluationOutcome:
        if partition not in {"evolution", "selection", "evolution_replay", "oof", "outer"}:
            raise ValueError(f"unregistered evaluation partition: {partition}")
        if partition == "outer":
            raise PermissionError("outer cannot be opened by the evolution evaluator")
        if len(case_keys) != len(set(case_keys)):
            raise ValueError("duplicate case keys are forbidden")
        if not all(key in self.records for key in case_keys):
            raise KeyError("evaluation contains an unknown case key")
        rows = self._get_rows(genome, case_keys)
        experiences = (
            tuple(experience_record_from_row(row) for row in rows)
            if partition == "evolution" else ()
        )
        payload_hashes = tuple(sorted({
            str(meta["request_hash"])
            for row in rows
            for meta in _walk_provider_meta(row.get("providers") or {})
        }))
        return EvaluationOutcome(
            metrics=metric_snapshot(rows),
            experience_records=experiences,
            per_case_correct={
                str(row.get("case_ref")): _row_correct(row)
                for row in rows
            },
            provider_payload_hashes=payload_hashes,
            evaluation_id=evaluation_id,
        )

    def metamorphic(
        self,
        *,
        genome: SkillGenome,
        case_keys: Sequence[str],
    ) -> Mapping[str, bool]:
        del case_keys
        compiled = compile_genome_for_paca(genome)
        text = "\n".join(stage.text for stage in compiled.stages.values())
        lowered = text.lower()
        outcomes = {
            "event_renaming_invariance": not re.search(
                r"\b(?:event|interaction)[_-]?\d+\b", lowered,
            ),
            "source_identity_invariance": not re.search(
                r"\b(?:source|case|agent|model)[_-]?id\b", lowered,
            ),
            "gold_field_rejection": not any(
                marker in lowered for marker in (
                    "gold_label", "gold_explanation", "outer_feedback",
                )
            ),
            "recovery_non_erasure": not re.search(
                r"recover(?:y|ed).{0,48}(?:erase|negate).{0,24}incident", lowered,
            ),
            "process_only_not_official_positive": not re.search(
                r"process[_ -]?(?:only|debt).{0,48}(?:official[_ -]?positive|hallucination)",
                lowered,
            ),
            "external_observation_not_agent_origin": not re.search(
                r"(?:external[_ -]?observation|tool[_ -]?output).{0,48}agent[_ -]?origin",
                lowered,
            ),
        }
        assert set(outcomes) == set(G1_CHECKS)
        return outcomes

    def _anonymous_case_map(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            record = experience_record_from_row(row)
            result[anonymous_ref(record, salt=self.anonymization_salt)] = row
        return result

    def paired_replay(
        self,
        *,
        parent: SkillGenome,
        child: SkillGenome,
        case_keys: Sequence[str],
        episodes: Sequence[FailureEpisode],
    ) -> ReplayEvidence:
        parent_rows = self._get_rows(parent, case_keys)
        child_rows = self._get_rows(child, case_keys)
        parent_by_ref = self._anonymous_case_map(parent_rows)
        child_by_ref = self._anonymous_case_map(child_rows)
        corrected = 0
        for episode in episodes:
            support = episode.support_refs[0]
            child_row = child_by_ref.get(support)
            parent_row = parent_by_ref.get(support)
            if child_row is None or parent_row is None:
                continue
            error = episode.error_type
            if error == ErrorType.FALSE_POSITIVE:
                fixed = _prediction_state(child_row) is not True
            elif error == ErrorType.FALSE_NEGATIVE:
                fixed = _prediction_state(child_row) is True
            elif error == ErrorType.LOCALIZATION_ERROR:
                gold_steps = tuple(str(item) for item in child_row.get("gold_steps") or ())
                fixed = bool(gold_steps) and str(child_row.get("responsible_step")) in gold_steps
            elif error == ErrorType.CANDIDATE_MISS:
                gold_steps = set(str(item) for item in child_row.get("gold_steps") or ())
                fixed = bool(gold_steps & set(_candidate_origins(child_row)))
            elif error == ErrorType.INVALID_CERTIFICATE:
                fixed = bool(child_row.get("runtime_valid"))
            elif error == ErrorType.UNRESOLVED_ERROR:
                fixed = _prediction_state(child_row) is not None
            else:
                fixed = (
                    len(tuple(_walk_provider_meta(child_row.get("providers") or {})))
                    <= len(tuple(_walk_provider_meta(parent_row.get("providers") or {})))
                )
            corrected += int(fixed)

        boundary_refs = {
            ref
            for episode in episodes
            for ref in (episode.paired_positive_ref, episode.paired_negative_ref)
        }
        boundary_total = regressions = 0
        for ref in boundary_refs:
            parent_row = parent_by_ref.get(ref)
            child_row = child_by_ref.get(ref)
            if parent_row is None or child_row is None or not _row_correct(parent_row):
                continue
            boundary_total += 1
            regressions += int(not _row_correct(child_row))
        parent_metrics = metric_snapshot(parent_rows)
        child_metrics = metric_snapshot(child_rows)
        return ReplayEvidence(
            targeted_error_total=len(episodes),
            targeted_error_corrected=corrected,
            paired_boundary_total=boundary_total,
            paired_boundary_regressions=regressions,
            parent_candidate_origin_coverage=parent_metrics.candidate_origin_coverage,
            child_candidate_origin_coverage=child_metrics.candidate_origin_coverage,
            total_outputs=len(child_rows),
            valid_outputs=sum(bool(row.get("runtime_valid")) for row in child_rows),
        )
