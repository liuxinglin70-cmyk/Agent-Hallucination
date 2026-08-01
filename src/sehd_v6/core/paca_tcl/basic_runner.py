"""Run PACA-TCL v1 on opened P0 using frozen PACA candidate-census caches.

This experiment isolates the new responsibility/boundary/propagation layers.
PACA candidate caches contain no labels and are converted back to addressable
events from the complete raw trajectory.  P0 remains diagnostic only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from ...utils import ocse_packet as packet
from . import contract_v1 as contract
from ...utils.preflight import discover_records


# ============================================================
# 全局缓存基础设施（v3.5.1 效率优化）
#
# 缓存键 = sha256(endpoint + "|" + model + "|" + body)
# body 包含完整的 system prompt（含 skill 注入）和 user prompt。
# temperature=0 时，相同输入必然产生相同输出，因此：
#   - 同 case + 同 genome + 同 route → 必然命中
#   - 不同 genome → system prompt 不同 → hash 不同 → 不会错误命中
#   - baseline genome（无注入）→ 跨 case 的同 route 调用可命中
#
# 全局缓存不依赖 output_dir，所有 output_dir 共享同一份缓存。
# 原子写入防止并发线程写冲突。
# ============================================================

_GLOBAL_CACHE_ROOT = Path(
    os.environ.get(
        "PACA_GLOBAL_CACHE_ROOT",
        "/root/autodl-tmp/agent_hallucination/global_provider_cache",
    )
)
_global_cache_write_lock = threading.Lock()


def _global_cache_path(request_hash: str) -> Path:
    """全局缓存文件路径（两级目录分散，避免单目录文件过多）"""
    return _GLOBAL_CACHE_ROOT / request_hash[:2] / f"{request_hash}.json"


def _atomic_write_json(path: Path, value: Any) -> None:
    """原子写入 JSON 文件，防止并发写冲突。

    先写入线程独占的临时文件（含 pid + tid 后缀，避免同名冲突），
    然后 os.replace 原子替换。os.replace 在 POSIX 上是原子的，
    保证读到的要么是旧文件要么是新文件。
    """
    import threading as _t
    import os as _os
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件名含 pid + tid，确保不同线程写同一 hash 时不会冲突
    tmp = path.with_suffix(f".{_os.getpid()}.{_t.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        _os.fsync(handle.fileno())
    _os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(packet.canonical_json(row) + "\n" for row in rows), encoding="utf-8")


# v3.5.1: 线程安全的 JSONL 追加写，避免每次完成一例就全量重写
_jsonl_append_lock = threading.Lock()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """v3.5.1: 追加一行到 JSONL 文件（线程安全，O(1) 而非 O(n) 重写）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = packet.canonical_json(row) + "\n"
    with _jsonl_append_lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def private_ref(case_id: str, prefix: str) -> str:
    return prefix + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:20]


def parse_gold_label(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", "", "none", "null"}:
        return False
    raise ValueError("unrecognized gold label")


def parse_gold_steps(value: Any) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, int):
        return {value}
    return {int(token) for token in __import__("re").findall(r"\d+", str(value))}


def p0_case_ids(manifest: dict[str, Any]) -> list[str]:
    rows = (manifest.get("roles") or {}).get("P0_opened_diagnostic")
    if not isinstance(rows, list) or len(rows) != 64:
        raise ValueError("P0 role must contain exactly 64 rows")
    return sorted(str(row["case_id"]) for row in rows)


def _event_for_source_step(built: dict[str, Any], step: Any) -> str | None:
    for event in built["events"]:
        if event.get("source_step_id") == step:
            return str(event["event_id"])
    if isinstance(step, int) and 1 <= step <= len(built["events"]):
        return str(built["events"][step - 1]["event_id"])
    return None


def load_paca_census(*, case_id: str, built: dict[str, Any], cache_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = cache_root / private_ref(case_id, "p0_") / "paca_v1_canary_results.json"
    if not source.is_file():
        return [], {"status": "missing", "path": str(source)}
    value = read_json(source)
    outputs = value.get("outputs") or {}
    raw_candidates = (outputs.get("stage1_scanning") or {}).get("candidates")
    if not isinstance(raw_candidates, list):
        return [], {"status": "invalid", "path": str(source)}
    converted: list[dict[str, Any]] = []
    for index, row in enumerate(raw_candidates, start=1):
        if not isinstance(row, dict):
            continue
        origin = _event_for_source_step(built, row.get("root_step_id"))
        if not origin:
            continue
        dependencies = []
        for step in row.get("information_dependencies") or []:
            ref = _event_for_source_step(built, step)
            if ref and ref not in dependencies:
                dependencies.append(ref)
        converted.append({
            "candidate_id": f"paca_candidate_{index:03d}",
            "origin_hypothesis_event_id": origin,
            "trigger_event_id": origin,
            "axes": [str(row.get("hallucination_dimension") or "F")],
            "candidate_kind": str(row.get("candidate_kind") or "unmatched"),
            "claim_or_commitment": str(row.get("information_introduced") or row.get("observation") or "candidate deviation"),
            "dependency_event_ids": dependencies,
            "module_tag": str(row.get("module_tag") or "Unknown"),
            "observation": str(row.get("observation") or ""),
            "is_decision_point": bool(row.get("is_decision_point")),
            "source_step_id": next(
                (
                    event.get("source_step_id")
                    for event in built["events"]
                    if str(event.get("event_id")) == origin
                ),
                None,
            ),
        })
    return converted, {
        "status": "complete" if len(converted) == len(raw_candidates) else "partial",
        "raw_candidate_count": len(raw_candidates),
        "converted_candidate_count": len(converted),
        "source_hash": packet.stable_hash(raw_candidates),
    }


def system_prompt() -> str:
    return """You are the semantic audit layer of PACA-TCL, a typed causal-ledger detector for agent hallucinations.
The candidates were independently discovered from the complete trajectory. Audit every candidate; never invent or delete one.

A verified error requires ALL of: (1) a verified F/Fa/P/S violation, (2) agent-caused or agent-contributed responsibility, (3) the ordinary-failure/exploration/approximation/authorized-action boundary is cleared, and (4) an addressable origin -> consumption -> material impact chain that was not repaired before impact. Do not infer a contradiction merely because an action is imperfect. A bad tool result is agent-caused only when the agent's target, argument, assumption, or omission caused or materially contributed to it.

Use only IDs present in ADDRESSABLE_REFERENCE_IDS. Event IDs may be used for causal locations. Event IDs and tool interaction IDs may be used as evidence. For omissions, origin_event_id is the decision event where the required action should have occurred. Recovery means explicit correction, successful rollback, or supersession of the same commitment; a workaround is not automatically recovery. If recovery occurs after an irreversible external/process impact, set impacted_then_recovered and residual_impact=true.

Enums:
- violation_state: verified_violation | verified_non_violation | unresolved
- actor_state: agent_caused | agent_contributed | environment_caused | external_input_caused | unresolved
- boundary_state: cleared | triggered | unresolved
- propagation_state: no_consumption | recovered_before_impact | impacted_then_recovered | unrecovered_impact | unresolved
- impact_surfaces: A(final answer), X(external state/cost), R(process debt)
- coverage_state: complete only when every supplied candidate is returned; otherwise incomplete or unresolved.

Return JSON only: {"coverage_state":"complete|incomplete|unresolved","candidates":[...]}. Each candidate must include exactly these semantic fields (extra fields are allowed but ignored): candidate_id, axes, candidate_kind, trigger_event_id, origin_event_id, claim_or_commitment, dependency_event_ids, violation_state, actor_state, boundary_state, propagation_state, residual_impact, positive_evidence_refs, actor_evidence_refs, boundary_evidence_refs, consumption_event_ids, consumption_evidence_refs, impact_event_ids, impact_evidence_refs, recovery_event_id, recovery_evidence_refs, impact_surfaces, rationale.
For non-applicable evidence lists return []; for no recovery return null. Never use a Boolean such as answer_causal instead of the three-segment chain."""


def candidate_system_prompt() -> str:
    return """You perform PACA-TCL dependency-aware candidate census. Enumerate distinct potential hallucination origins in the complete trajectory without deciding the final label.

Scan all four axes: F factual claims/numbers/entities/citations; Fa instruction/context/logic faithfulness; P planning, retrieval, omission, tool arguments, grounding and execution state; S authorization, relevance and scope. Prefer the earliest event that INTRODUCES a claim, commitment, invalid argument, unsupported assumption, or consequential omission. Do not select only a later symptom or final answer when an earlier origin is addressable. Ordinary tool failures, exploration and recovered deviations may still enter the census; later layers decide their boundary.

Return at most 8 deduplicated candidates and do not force a candidate when none is defensible. Use event IDs only. Return JSON:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"candidate_id":"candidate_001","origin_hypothesis_event_id":"event_001","trigger_event_id":"event_001","axes":["F"],"candidate_kind":"short_typed_name","claim_or_commitment":"specific introduced state","dependency_event_ids":[],"module_tag":"Planning|Retrieval|Reasoning|Tool-Use|Human-Interaction|Loop-Detection|Redundancy-Check","observation":"why this event enters the census","is_decision_point":true}]}.
Coverage is complete only after scanning the whole supplied trajectory. Never use dataset identity, source, labels or outside facts."""


def provider_call(*, route: dict[str, Any], system: str, user: str, cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return provider_call_with_schema(
        route=route, system=system, user=user, cache_dir=cache_dir,
        cache_prefix="v1_census", require_candidates=True,
    )


def provider_call_with_schema(
    *,
    route: dict[str, Any],
    system: str,
    user: str,
    cache_dir: Path,
    cache_prefix: str = "v1_census",
    require_candidates: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Provider call with configurable schema validation.

    Args:
        require_candidates: If True (default), validate that the response
            contains a ``candidates`` list (the census/ledger schema). Set
            to False for routes that return other top-level keys, e.g.
            the H3 counterfactual localization route which returns
            ``{"rankings": {...}}``.
    """
    payload = {
        "model": route["model"],
        "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    body = packet.canonical_json(payload).encode("utf-8")
    # v3.5.1: 缓存键含 cache_prefix 前缀，与 evaluator 的 _json_object_provider_call 区分
    # （两者 payload 可能相同但 schema 验证不同，必须隔离缓存）
    request_hash = hashlib.sha256(cache_prefix.encode("utf-8") + b"|" + (route["endpoint"] + "|" + route["model"] + "|").encode("utf-8") + body).hexdigest()

    # v3.5.1: 全局缓存查找（不依赖 cache_dir，所有 output_dir 共享）
    global_target = _global_cache_path(request_hash)
    if global_target.is_file():
        try:
            cached = read_json(global_target)
            if cached.get("request_hash") == request_hash:
                return cached["parsed"], {
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
            req = urlrequest.Request(
                route["endpoint"].rstrip("/") + "/chat/completions",
                data=body,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                method="POST",
            )
            started = time.time()
            with urlrequest.urlopen(req, timeout=int(route.get("timeout_seconds", 300))) as response:
                raw = response.read()
            value = json.loads(raw.decode("utf-8"))
            content = value["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("provider response is not a JSON object")
            if require_candidates and not isinstance(parsed.get("candidates"), list):
                raise ValueError("provider ledger schema invalid: missing candidates")
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
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def json_object_provider_call(
    *, route: dict[str, Any], system: str, user: str, cache_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call a provider for an unconstrained JSON object.

    Mutation and attribution routes do not use the PACA ``candidates``
    envelope. Keeping this wrapper beside the cached provider implementation
    gives those callers the same deterministic transport without pretending
    they implement a ledger schema.
    """
    return provider_call_with_schema(
        route=route,
        system=system,
        user=user,
        cache_dir=cache_dir,
        cache_prefix="generic_json_object",
        require_candidates=False,
    )


def validate_provider_census(value: dict[str, Any], built: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coverage = str(value.get("coverage_state") or "")
    rows = value.get("candidates")
    event_ids = {str(event["event_id"]) for event in built["events"]}
    if coverage not in contract.COVERAGE_STATES or not isinstance(rows, list) or len(rows) > 8:
        return [], {"status": "invalid_top_level", "coverage_state": coverage}
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {
        "candidate_id", "origin_hypothesis_event_id", "trigger_event_id", "axes",
        "candidate_kind", "claim_or_commitment", "dependency_event_ids", "module_tag",
        "observation", "is_decision_point",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            failures.append({"index": index, "reason": "not_object"})
            continue
        candidate_id = str(row.get("candidate_id") or "")
        axes = row.get("axes")
        dependencies = row.get("dependency_event_ids")
        checks = {
            "required": required <= set(row),
            "candidate_id": bool(candidate_id) and candidate_id not in seen,
            "origin": str(row.get("origin_hypothesis_event_id") or "") in event_ids,
            "trigger": str(row.get("trigger_event_id") or "") in event_ids,
            "axes": isinstance(axes, list) and bool(axes) and set(map(str, axes)) <= contract.AXES,
            "dependencies": isinstance(dependencies, list) and all(str(ref) in event_ids for ref in dependencies),
            "candidate_kind": bool(str(row.get("candidate_kind") or "").strip()),
            "claim": bool(str(row.get("claim_or_commitment") or "").strip()),
            "observation": bool(str(row.get("observation") or "").strip()),
            "decision_point": isinstance(row.get("is_decision_point"), bool),
        }
        if all(checks.values()):
            normalized_row = dict(row)
            origin = str(row.get("origin_hypothesis_event_id") or "")
            normalized_row["source_step_id"] = next(
                (
                    event.get("source_step_id")
                    for event in built["events"]
                    if str(event.get("event_id")) == origin
                ),
                None,
            )
            valid.append(normalized_row)
            seen.add(candidate_id)
        else:
            failures.append({"index": index, "failed_checks": sorted(key for key, passed in checks.items() if not passed)})
    status = "complete" if not failures and coverage == "complete" else "partial"
    return valid, {
        "status": status,
        "coverage_state": coverage,
        "raw_candidate_count": len(rows),
        "converted_candidate_count": len(valid),
        "failures": failures,
        "source_hash": packet.stable_hash(rows),
    }


def _addressable_refs(built: dict[str, Any]) -> list[str]:
    refs = []
    for event in built["events"]:
        refs.append(str(event["event_id"]))
        refs.extend(str(item["interaction_id"]) for item in event.get("tool_interactions") or [])
    return refs


def provider_user_prompt(built: dict[str, Any], census: list[dict[str, Any]]) -> str:
    """Materialize the label-free provider input in one auditable function."""

    user = (
        "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) +
        "\nCANDIDATE_CENSUS=\n" + packet.canonical_json(census) +
        "\nADDRESSABLE_REFERENCE_IDS=\n" + packet.canonical_json(_addressable_refs(built))
    )
    forbidden_control_fields = {
        '"is_hallucination"', '"hallucination_step"', '"hallucination_reason"',
        '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"',
    }
    hits = sorted(field for field in forbidden_control_fields if field in user)
    if hits:
        raise ValueError("provider input firewall violation: " + ",".join(hits))
    return user


def candidate_provider_user_prompt(built: dict[str, Any]) -> str:
    user = (
        "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) +
        "\nADDRESSABLE_EVENT_IDS=\n" + packet.canonical_json([event["event_id"] for event in built["events"]])
    )
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in user:
            raise ValueError("candidate provider input firewall violation: " + field)
    return user


def _responsible_step(event_id: str | None, built: dict[str, Any]) -> int | None:
    if not event_id:
        return None
    for event in built["events"]:
        if event["event_id"] == event_id:
            value = event.get("source_step_id")
            if isinstance(value, int):
                return value
            text = str(value or "").strip()
            return int(text) if text.isdigit() else int(event["position"])
    return None


def adjudicate_case(*, case_id: str, record: dict[str, Any], config: dict[str, Any], output_dir: Path, census_root: Path) -> dict[str, Any]:
    built = packet.build_packet(record)
    census, census_audit = load_paca_census(case_id=case_id, built=built, cache_root=census_root)
    candidate_provider: dict[str, Any] = {"calls": 0, "source": "frozen_paca_stage1_cache"}
    if not census or census_audit.get("status") != "complete":
        try:
            provider_value, provider_meta = provider_call(
                route=config.get("candidate_fallback_route") or config["model_route"],
                system=candidate_system_prompt(),
                user=candidate_provider_user_prompt(built),
                cache_dir=output_dir / "candidate_provider_cache",
            )
            census, generated_audit = validate_provider_census(provider_value, built)
            census_audit = {**generated_audit, "fallback_from": census_audit}
            candidate_provider = {**provider_meta, "calls": 1, "source": "provider_fallback"}
        except Exception as exc:
            census = []
            census_audit = {"status": "fallback_failed", "prior": census_audit, "error_type": type(exc).__name__, "error": str(exc)[:500]}
            candidate_provider = {"calls": 1, "source": "provider_fallback", "error_type": type(exc).__name__}
    result = {
        "case_ref": private_ref(case_id, "case_"),
        "gold_label": parse_gold_label(record.get("is_hallucination")),
        "gold_steps": sorted(parse_gold_steps(record.get("hallucination_step"))),
        "census": census,
        "census_audit": census_audit,
        "candidate_provider": candidate_provider,
    }
    if not census:
        result.update({
            "runtime_valid": census_audit["status"] == "complete",
            "state": "verified_clean" if census_audit["status"] == "complete" else "unresolved",
            "responsible_event_id": None,
            "responsible_step": None,
            "ledger": {"coverage_state": "complete" if census_audit["status"] == "complete" else "unresolved", "candidates": []},
            "adjudication": None,
            "provider": {"calls": 0},
        })
        return result

    user = provider_user_prompt(built, census)
    try:
        ledger, provider = provider_call(
            route=config["model_route"],
            system=system_prompt(),
            user=user,
            cache_dir=output_dir / "provider_cache",
        )
        expected = {row["candidate_id"] for row in census}
        returned = {str(row.get("candidate_id") or "") for row in ledger.get("candidates") or [] if isinstance(row, dict)}
        if expected != returned:
            ledger = {**ledger, "coverage_state": "incomplete"}
        adjudication = contract.adjudicate_ledger(ledger, built)
        result.update({
            "runtime_valid": True,
            "state": adjudication["state"],
            "responsible_event_id": adjudication["responsible_event_id"],
            "responsible_step": _responsible_step(adjudication["responsible_event_id"], built),
            "ledger": ledger,
            "adjudication": adjudication,
            "provider": provider,
        })
    except Exception as exc:
        result.update({
            "runtime_valid": False,
            "state": "unresolved",
            "responsible_event_id": None,
            "responsible_step": None,
            "ledger": None,
            "adjudication": None,
            "provider": {"error_type": type(exc).__name__, "error": str(exc)[:500]},
        })
    return result


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = Counter()
    tri_state = Counter()
    runtime_valid = 0
    step_correct = 0
    positive_total = 0
    discovery_hits = 0
    fn_mechanisms = Counter()
    quarantined_candidates = 0
    total_candidates = 0
    layer_states: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        gold = bool(row["gold_label"])
        positive_total += int(gold)
        state = str(row.get("state") or "unresolved")
        tri_state[state] += 1
        runtime_valid += int(bool(row.get("runtime_valid")))
        if state == "unresolved":
            # Fail closed for official binary metrics: abstention on a gold
            # error is FN, while abstention on a clean case is TN. The
            # separate unresolved counter preserves selective-prediction data.
            confusion["unresolved"] += 1
            predicted = False
        else:
            predicted = state == "verified_error"
        if gold and predicted:
            confusion["tp"] += 1
            step_correct += int(row.get("responsible_step") in set(row.get("gold_steps") or []))
        elif gold:
            confusion["fn"] += 1
        elif predicted:
            confusion["fp"] += 1
        else:
            confusion["tn"] += 1

        census_steps = {
            str(item.get("source_step_id"))
            for item in row.get("census") or []
            if item.get("source_step_id") is not None
        }
        hit = bool(census_steps & {str(step) for step in row.get("gold_steps") or []}) if gold else False
        discovery_hits += int(hit)
        adjudication = row.get("adjudication") or {}
        quarantined_candidates += len(adjudication.get("quarantined_candidate_ids") or [])
        ledger_rows = (row.get("ledger") or {}).get("candidates") or []
        total_candidates += len(ledger_rows)
        for candidate in ledger_rows:
            layer_states["violation"][str(candidate.get("violation_state"))] += 1
            layer_states["actor"][str(candidate.get("actor_state"))] += 1
            layer_states["boundary"][str(candidate.get("boundary_state"))] += 1
            layer_states["propagation"][str(candidate.get("propagation_state"))] += 1

        if gold and not predicted:
            if not hit:
                fn_mechanisms["candidate_origin_miss"] += 1
            elif not row.get("runtime_valid"):
                fn_mechanisms["runtime_invalid"] += 1
            elif adjudication.get("quarantined_candidate_ids"):
                fn_mechanisms["candidate_quarantine"] += 1
            elif any(candidate.get("violation_state") == "verified_violation" for candidate in ledger_rows):
                if any(candidate.get("actor_state") in {"environment_caused", "external_input_caused"} or candidate.get("boundary_state") == "triggered" for candidate in ledger_rows):
                    fn_mechanisms["responsibility_or_boundary_filter"] += 1
                elif any(candidate.get("propagation_state") in {"no_consumption", "recovered_before_impact"} for candidate in ledger_rows):
                    fn_mechanisms["propagation_or_recovery_filter"] += 1
                else:
                    fn_mechanisms["chain_or_unresolved"] += 1
            else:
                fn_mechanisms["violation_verification_miss"] += 1

    tp, fp, fn, tn = (confusion[key] for key in ("tp", "fp", "fn", "tn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1_pos = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    clean_precision = tn / (tn + fn) if tn + fn else 0.0
    clean_recall = tn / (tn + fp) if tn + fp else 0.0
    f1_clean = 2 * clean_precision * clean_recall / (clean_precision + clean_recall) if clean_precision + clean_recall else 0.0
    return {
        "case_count": len(rows),
        "runtime_valid_rate": runtime_valid / len(rows) if rows else 0.0,
        "confusion": dict(confusion),
        "macro_f1": (f1_pos + f1_clean) / 2,
        "precision": precision,
        "recall": recall,
        "clean_fpr": fp / (fp + tn) if fp + tn else 0.0,
        "responsible_step_recall_over_all_gold_positive": step_correct / positive_total if positive_total else 0.0,
        "responsible_step_accuracy_on_detected_positive": step_correct / tp if tp else 0.0,
        "candidate_origin_coverage_on_gold_positive": discovery_hits / positive_total if positive_total else 0.0,
        "tri_state_counts": dict(tri_state),
        "candidate_quarantine_rate": quarantined_candidates / total_candidates if total_candidates else 0.0,
        "fn_mechanisms": dict(fn_mechanisms),
        "layer_state_counts": {key: dict(value) for key, value in layer_states.items()},
        "candidate_provider_fallback_count": sum(int((row.get("candidate_provider") or {}).get("source") == "provider_fallback") for row in rows),
    }


def preflight(*, config_path: Path, dataset_root: Path, manifest_path: Path, census_root: Path) -> dict[str, Any]:
    config = read_json(config_path)
    manifest = read_json(manifest_path)
    records = discover_records(dataset_root)
    case_ids = p0_case_ids(manifest)
    counts = Counter()
    candidate_counts = Counter()
    failures = []
    for case_id in case_ids:
        try:
            built = packet.build_packet(records[case_id])
            census, audit = load_paca_census(case_id=case_id, built=built, cache_root=census_root)
            counts["packet_valid"] += 1
            counts["cached_census_complete"] += int(bool(census) and audit.get("status") == "complete")
            counts["fallback_required"] += int(not census or audit.get("status") != "complete")
            candidate_counts[len(census)] += 1
            if census:
                provider_user_prompt(built, census)
            candidate_provider_user_prompt(built)
        except Exception as exc:
            failures.append({"case_ref": private_ref(case_id, "case_"), "error_type": type(exc).__name__, "error": str(exc)[:300]})
    decision = "pass" if counts["packet_valid"] == 64 and not failures and "candidate_fallback_route" in config else "fail"
    return {
        "schema_version": "agenthallu.paca_tcl_preflight.v1",
        "decision": decision,
        "case_count": len(case_ids),
        "counts": dict(counts),
        "cached_candidate_count_distribution": {str(key): value for key, value in sorted(candidate_counts.items())},
        "failures": failures,
        "provider_calls_made": 0,
        "outer_opened": False,
    }


def run(*, config_path: Path, dataset_root: Path, manifest_path: Path, census_root: Path, output_dir: Path) -> dict[str, Any]:
    config = read_json(config_path)
    if config.get("execution_authorized") is not True or config.get("authorized_phase") != "P0_diagnostic_only":
        raise RuntimeError("PACA-TCL P0 execution is not authorized")
    if not os.environ.get(str(config["model_route"]["api_key_environment"])):
        raise RuntimeError("provider API key environment variable is absent")
    manifest = read_json(manifest_path)
    records = discover_records(dataset_root)
    case_ids = p0_case_ids(manifest)
    workers = int(config.get("max_parallel_cases", 4))
    by_case: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                adjudicate_case,
                case_id=case_id,
                record=records[case_id],
                config=config,
                output_dir=output_dir,
                census_root=census_root,
            ): case_id
            for case_id in case_ids
        }
        for future in as_completed(futures):
            case_id = futures[future]
            by_case[case_id] = future.result()
            write_jsonl(output_dir / "predictions.jsonl", [by_case[item] for item in case_ids if item in by_case])
    rows = [by_case[item] for item in case_ids]
    report = {
        "schema_version": "agenthallu.paca_tcl_p0.v1",
        "experiment_id": config["experiment_id"],
        "effectiveness_scope": "opened_P0_diagnostic_only_not_OOF_or_outer",
        "outer_opened": False,
        "model_route": {"endpoint": config["model_route"]["endpoint"], "model": config["model_route"]["model"], "temperature": 0},
        "candidate_source": "frozen_PACA_stage1_provider_outputs_without_labels",
        "metrics": evaluate(rows),
    }
    write_json(output_dir / "metrics.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--paca-census-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preflight_only:
        report = preflight(
            config_path=args.config,
            dataset_root=args.dataset_root,
            manifest_path=args.manifest,
            census_root=args.paca_census_root,
        )
        write_json(args.output_dir / "preflight.json", report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return
    lock = args.output_dir / ".paca_tcl.lock"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(str(os.getpid()) + "\n")
    except FileExistsError as exc:
        raise RuntimeError("output directory is locked") from exc
    try:
        report = run(
            config_path=args.config,
            dataset_root=args.dataset_root,
            manifest_path=args.manifest,
            census_root=args.paca_census_root,
            output_dir=args.output_dir,
        )
    finally:
        lock.unlink(missing_ok=True)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
