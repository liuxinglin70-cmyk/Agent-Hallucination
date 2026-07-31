#!/usr/bin/env python3
"""Run a 50-card gold-free R0->R1 skill-evolution pilot.

This runner is intentionally not a gold-label generator. It first asks a
skill-evolution model to produce a detector skill, then asks a loaded-skill
detection agent to produce hallucination candidates. Quality feedback is built
from deterministic evidence checks plus a reverse-audit model that reviews the
detection output against cited evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DATASET_NAME = "open_swe_agent_full_trace_cards_20260617"
DEFAULT_PHASE = "gold_free_r0_r1_coderforge_50"
DEFAULT_SOURCE_ID = "coderforge_verified_eval"
DEFAULT_AGENT_SYSTEM_ID = "openhands_coderforge"
DEFAULT_BASE_URL = "https://www.autodl.art/api/v1"
DEFAULT_SKILL_MODEL = "qwen3.6-plus"
DEFAULT_DETECTOR_MODEL = "qwen3.6-plus"
DEFAULT_FEEDBACK_MODEL = "DeepSeek-V4-Pro"
DEFAULT_API_CONCURRENCY = 15
VALID_TYPES = {
    "requirement_intent",
    "repository_fact",
    "api_symbol_dependency",
    "resource_path_config",
    "tool_observation_execution",
    "structural_inconsistency",
    "patch_effect_behavior",
    "dead_ineffective_code",
    "completion_progress_claim",
    "documentation_comment",
    "ordinary_failure",
    "insufficient_evidence",
    "supported_or_clean",
}
VALID_SUPPORT_STATUS = {"supported", "unsupported", "contradicted", "uncertain"}
OUTCOME_ONLY_PATHS = {"reward", "resolved", "test_result.report.resolved", "test_result.resolved"}
OUTCOME_AS_GOLD_TEXT_PATTERNS = [
    re.compile(r"\breward\s*(?:=|:|is|of|metric|metrics)\b", re.IGNORECASE),
    re.compile(r"\bresolved\s*(?:=|:|status|flag|true|false)\b", re.IGNORECASE),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_path(path: str) -> str:
    return path.replace("[]", "")


def get_path(value: Any, path: str) -> Any:
    current = value
    for raw_part in normalize_path(path).split("."):
        part = raw_part.strip()
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            extracted = []
            for item in current:
                if isinstance(item, dict) and part in item:
                    extracted.append(item[part])
            current = extracted
        else:
            return None
    return current


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def compact_value(value: Any, char_limit: int) -> Any:
    if isinstance(value, str):
        text = re.sub(r"\n{3,}", "\n\n", value.strip())
        if len(text) <= char_limit:
            return text
        head = text[: char_limit // 2]
        tail = text[-char_limit // 2 :]
        return f"{head}\n...[pilot-pack-truncated {len(text) - char_limit} chars]...\n{tail}"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    rendered = stable_json(value)
    if len(rendered) <= char_limit:
        return value
    return {
        "type": type(value).__name__,
        "chars": len(rendered),
        "preview": compact_value(rendered, char_limit),
    }


def identity_key(card: dict[str, Any]) -> str:
    split_assignment = card.get("split_assignment") or {}
    if split_assignment.get("identity_key_sha256"):
        return str(split_assignment["identity_key_sha256"])
    task = card.get("task_identity") or {}
    for field in ("instance_id", "trajectory_id", "run_id"):
        if task.get(field):
            return str(task[field]).strip().lower()
    return str(card.get("card_id"))


def select_pilot_cards(
    cards: list[dict[str, Any]],
    train_n: int = 30,
    dev_n: int = 10,
    test_n: int = 10,
    source_id: str = DEFAULT_SOURCE_ID,
    agent_system_id: str = DEFAULT_AGENT_SYSTEM_ID,
) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    for split, target in [("train", train_n), ("dev", dev_n), ("test", test_n)]:
        candidates = [
            card
            for card in cards
            if card.get("experiment_split") == split
            and (card.get("source") or {}).get("source_id") == source_id
            and (card.get("source") or {}).get("agent_system_id") == agent_system_id
        ]
        candidates.sort(key=lambda card: str(card.get("card_id", "")))
        if len(candidates) < target:
            raise ValueError(f"not enough {source_id}/{agent_system_id} {split} cards: need {target}, got {len(candidates)}")
        selected[split] = candidates[:target]

    all_cards = [card for rows in selected.values() for card in rows]
    card_ids = [card.get("card_id") for card in all_cards]
    duplicate_ids = sorted({card_id for card_id in card_ids if card_ids.count(card_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate selected card_id values: {duplicate_ids[:5]}")

    identity_to_splits: dict[str, set[str]] = {}
    for split, rows in selected.items():
        for card in rows:
            identity_to_splits.setdefault(identity_key(card), set()).add(split)
    leaked = {key: sorted(splits) for key, splits in identity_to_splits.items() if len(splits) > 1}
    if leaked:
        examples = list(leaked.items())[:5]
        raise ValueError(f"identity leakage across pilot splits: {examples}")
    return selected


def evidence_id(group: str, ref: dict[str, Any]) -> str:
    return f"{group}:{ref.get('path')}"


def all_evidence_id_map(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    hard_refs = card.get("hard_evidence_refs") or {}
    for group in ("patch", "test_or_reward", "tool_observations"):
        for ref in hard_refs.get(group, []) or []:
            item = dict(ref)
            item["group"] = group
            item["value"] = get_path(card.get("source_payload") or {}, str(ref.get("path") or ""))
            refs[evidence_id(group, ref)] = item
    for ref in card.get("trajectory_refs") or []:
        item = dict(ref)
        item["group"] = "trajectory"
        item["value"] = get_path(card.get("source_payload") or {}, str(ref.get("path") or ""))
        refs[evidence_id("trajectory", ref)] = item
    for ref in card.get("agent_metadata_refs") or []:
        item = dict(ref)
        item["group"] = "agent_metadata"
        item["value"] = get_path(card.get("source_payload") or {}, str(ref.get("path") or ""))
        refs[evidence_id("agent_metadata", ref)] = item
    return refs


def evidence_pack_for_card(card: dict[str, Any], char_limit: int = 1800) -> dict[str, Any]:
    evidence = []
    for evid, ref in sorted(all_evidence_id_map(card).items()):
        evidence.append(
            {
                "evidence_id": evid,
                "group": ref.get("group"),
                "path": ref.get("path"),
                "shape": ref.get("shape"),
                "value": compact_value(ref.get("value"), char_limit),
            }
        )
    task = card.get("task_identity") or {}
    return {
        "card_id": card.get("card_id"),
        "trajectory_id": task.get("trajectory_id") or task.get("instance_id") or card.get("card_id"),
        "experiment_split": card.get("experiment_split"),
        "source": card.get("source"),
        "task_identity": task,
        "allowed_evidence_ids": [item["evidence_id"] for item in evidence],
        "evidence_pack": evidence,
        "label_policy": card.get("label_policy"),
        "gold_free_warning": "Outcome fields are evidence only. Do not treat reward/resolved/test_output as hallucination gold.",
    }


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("no JSON object found in model response")


def normalize_detections(payload: dict[str, Any], method: str) -> list[dict[str, Any]]:
    rows = payload.get("detections") or payload.get("audit_results") or payload.get("predictions") or []
    normalized = []
    for row in rows:
        item = dict(row)
        item.setdefault("method", method)
        item["has_hallucination"] = bool(item.get("has_hallucination"))
        item["evidence_ids"] = item.get("evidence_ids") or item.get("evidence_checked") or []
        if isinstance(item["evidence_ids"], str):
            item["evidence_ids"] = [part.strip() for part in item["evidence_ids"].split(",") if part.strip()]
        item["detected_type"] = item.get("detected_type") or item.get("category") or "insufficient_evidence"
        item["support_status"] = item.get("support_status") or "uncertain"
        normalized.append(item)
    return normalized


def hard_check_detections(detections: list[dict[str, Any]], cards_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = Counter()
    examples: dict[str, list[dict[str, Any]]] = {
        "invalid_schema": [],
        "invalid_evidence": [],
        "outcome_as_gold": [],
        "missing_boundary": [],
    }
    for detection in detections:
        metrics["detections"] += 1
        required = {"card_id", "trajectory_id", "has_hallucination", "detected_type", "unsupported_unit", "evidence_ids", "support_status"}
        schema_ok = required <= set(detection) and detection.get("detected_type") in VALID_TYPES and detection.get("support_status") in VALID_SUPPORT_STATUS
        if schema_ok:
            metrics["json_schema_valid"] += 1
        else:
            examples["invalid_schema"].append({"card_id": detection.get("card_id"), "detected_type": detection.get("detected_type")})

        card = cards_by_id.get(str(detection.get("card_id")))
        allowed_ids = set(all_evidence_id_map(card).keys()) if card else set()
        cited_ids = set(detection.get("evidence_ids") or [])
        evidence_ok = bool(cited_ids) and cited_ids <= allowed_ids
        if evidence_ok:
            metrics["evidence_ref_valid"] += 1
        else:
            examples["invalid_evidence"].append(
                {
                    "card_id": detection.get("card_id"),
                    "bad_ids": sorted(cited_ids - allowed_ids),
                    "missing_all_evidence": not bool(cited_ids),
                }
            )

        text = stable_json(
            {
                "unsupported_unit": detection.get("unsupported_unit"),
                "rationale": detection.get("rationale"),
                "why_not_merely_a_bug": detection.get("why_not_merely_a_bug"),
            }
        ).lower()
        outcome_ids = [
            evid
            for evid in cited_ids
            if evid.startswith("test_or_reward:") and evid.split(":", 1)[1] in OUTCOME_ONLY_PATHS
        ]
        outcome_terms = [pattern.pattern for pattern in OUTCOME_AS_GOLD_TEXT_PATTERNS if pattern.search(text)]
        if outcome_ids or outcome_terms:
            metrics["outcome_as_gold_violations"] += 1
            examples["outcome_as_gold"].append(
                {"card_id": detection.get("card_id"), "outcome_ids": outcome_ids, "outcome_terms": outcome_terms}
            )

        if str(detection.get("why_not_merely_a_bug") or "").strip():
            metrics["ordinary_failure_boundary_present"] += 1
        else:
            examples["missing_boundary"].append({"card_id": detection.get("card_id")})

        if detection.get("detected_type") == "insufficient_evidence" or detection.get("support_status") == "uncertain":
            metrics["abstentions_or_uncertain"] += 1

    result = dict(metrics)
    for key in (
        "detections",
        "json_schema_valid",
        "evidence_ref_valid",
        "ordinary_failure_boundary_present",
        "outcome_as_gold_violations",
        "abstentions_or_uncertain",
    ):
        result.setdefault(key, 0)
    result["examples"] = {key: value[:10] for key, value in examples.items() if value}
    return result


def summarize_detections(detections: list[dict[str, Any]], expected_card_ids: Iterable[str] = ()) -> dict[str, Any]:
    detected_card_ids = {str(item.get("card_id")) for item in detections if item.get("card_id") is not None}
    expected = {str(card_id) for card_id in expected_card_ids}
    return {
        "detections": len(detections),
        "cards_with_detection": len(detected_card_ids),
        "missing_card_ids": sorted(expected - detected_card_ids),
        "detected_type_distribution": dict(sorted(Counter(str(item.get("detected_type")) for item in detections).items())),
        "support_status_distribution": dict(sorted(Counter(str(item.get("support_status")) for item in detections).items())),
        "has_hallucination_distribution": dict(
            sorted(Counter(str(bool(item.get("has_hallucination"))) for item in detections).items())
        ),
    }


def summarize_revision(skill_r1: dict[str, Any]) -> dict[str, Any]:
    revision_log = skill_r1.get("revision_log") or {}
    return {
        "repairs": revision_log.get("repairs") or [],
        "regression_risks": revision_log.get("regression_risks") or [],
        "feedback_used": revision_log.get("feedback_used") or [],
    }


def prompt_header() -> str:
    return (
        "Return JSON only. Do not include markdown fences. This is a gold-free experiment: "
        "do not convert reward, resolved, public failure, hidden failure, or test output into ground-truth labels. "
        "A positive hallucination requires an unsupported or contradicted claim/action/code artifact plus cited evidence."
    )


def render_skill_r0_prompt(train_packs: list[dict[str, Any]], taxonomy: dict[str, Any]) -> str:
    return (
        f"{prompt_header()}\n\n"
        "You are the skill-evolution model. Induce skill_r0 for a loaded-skill hallucination detection agent.\n"
        "Use the taxonomy as the starting map, not as gold labels. The skill must tell the detector what units to extract, "
        "what evidence ids to cite, when to abstain, and how to separate ordinary failure from hallucination.\n"
        "Return exactly: {\"skill_version\":\"r0\", \"skills\":[...], \"output_schema\":..., \"gold_free_boundaries\":[...] }.\n\n"
        f"TAXONOMY_JSON:\n{json.dumps(taxonomy, ensure_ascii=False)}\n\n"
        f"TRAIN_EVIDENCE_PACKS_JSON:\n{json.dumps(train_packs, ensure_ascii=False)}"
    )


def render_detection_prompt(skill: dict[str, Any], packs: list[dict[str, Any]], split_name: str) -> str:
    return (
        f"{prompt_header()}\n\n"
        "You are a loaded-skill detection agent. Apply the supplied skill to each evidence pack. "
        "Use only allowed_evidence_ids from each pack. If evidence is insufficient, abstain with detected_type "
        "`insufficient_evidence` and support_status `uncertain`.\n"
        "Return exactly: {\"detections\":[{\"card_id\":\"...\", \"trajectory_id\":\"...\", "
        "\"has_hallucination\":true/false, \"detected_type\":\"taxonomy_or_boundary_id\", "
        "\"unsupported_unit\":\"...\", \"evidence_ids\":[\"group:path\"], "
        "\"support_status\":\"supported|unsupported|contradicted|uncertain\", "
        "\"why_not_merely_a_bug\":\"...\", \"rationale\":\"...\"}]}.\n\n"
        f"SPLIT: {split_name}\n"
        f"SKILL_JSON:\n{json.dumps(skill, ensure_ascii=False)}\n\n"
        f"EVIDENCE_PACKS_JSON:\n{json.dumps(packs, ensure_ascii=False)}"
    )


def render_feedback_prompt(
    skill: dict[str, Any],
    dev_packs: list[dict[str, Any]],
    dev_detections: list[dict[str, Any]],
    hard_metrics: dict[str, Any],
) -> str:
    return (
        f"{prompt_header()}\n\n"
        "You are the reverse-audit feedback model. Do not produce ground-truth hallucination labels. "
        "Review the loaded-skill detector outputs against their cited evidence. Judge whether each detection is "
        "evidence-supported, over-inferred, missing evidence, confusing ordinary failure with hallucination, or properly abstaining. "
        "Return quality feedback for skill revision.\n"
        "Return exactly: {\"reverse_audits\":[{\"card_id\":\"...\", \"detection_quality\":\"supported|over_inferred|missing_evidence|ordinary_failure_confusion|good_abstention\", "
        "\"feedback\":\"...\", \"required_skill_repair\":\"...\"}], \"global_feedback\":[...] }.\n\n"
        f"CURRENT_SKILL_JSON:\n{json.dumps(skill, ensure_ascii=False)}\n\n"
        f"HARD_CHECK_METRICS_JSON:\n{json.dumps(hard_metrics, ensure_ascii=False)}\n\n"
        f"DEV_PACKS_JSON:\n{json.dumps(dev_packs, ensure_ascii=False)}\n\n"
        f"DEV_DETECTIONS_JSON:\n{json.dumps(dev_detections, ensure_ascii=False)}"
    )


def render_revision_prompt(
    skill_r0: dict[str, Any],
    dev_detections: list[dict[str, Any]],
    hard_metrics: dict[str, Any],
    feedback: dict[str, Any],
) -> str:
    return (
        f"{prompt_header()}\n\n"
        "You are the skill-evolution model. Revise skill_r0 into skill_r1 using quality feedback. "
        "Do not memorize card ids. Improve evidence-route rules, abstention rules, ordinary-failure boundaries, "
        "and evidence citation discipline. Return the complete revised skill.\n"
        "Return exactly: {\"skill_version\":\"r1\", \"skills\":[...], \"output_schema\":..., "
        "\"revision_log\":{\"repairs\":[...], \"regression_risks\":[...], \"feedback_used\":[...]}}.\n\n"
        f"SKILL_R0_JSON:\n{json.dumps(skill_r0, ensure_ascii=False)}\n\n"
        f"DEV_DETECTIONS_JSON:\n{json.dumps(dev_detections, ensure_ascii=False)}\n\n"
        f"HARD_CHECK_METRICS_JSON:\n{json.dumps(hard_metrics, ensure_ascii=False)}\n\n"
        f"REVERSE_AUDIT_FEEDBACK_JSON:\n{json.dumps(feedback, ensure_ascii=False)}"
    )


def chat_completion(args: argparse.Namespace, model: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    if not args.api_key:
        raise RuntimeError("AUTODL_API_KEY is required for model calls")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise research experiment component. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": args.temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_error = ""
    for attempt in range(1, args.attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            return {
                "ok": True,
                "model": model,
                "response_text": message.get("content") or "",
                "finish_reason": choice.get("finish_reason"),
                "usage": body.get("usage") or {},
            }
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = repr(exc)
            if attempt < args.attempts:
                time.sleep(min(2 * attempt, 10))
    return {"ok": False, "model": model, "error": last_error, "response_text": "", "usage": {}}


def model_json_call(args: argparse.Namespace, model: str, prompt: str, raw_path: Path, max_tokens: int) -> dict[str, Any]:
    raw = chat_completion(args, model, prompt, max_tokens)
    payload = {
        "created_at": now_iso(),
        "model": model,
        "prompt_sha256": sha256_text(prompt),
        "prompt_chars": len(prompt),
        "ok": raw.get("ok"),
        "finish_reason": raw.get("finish_reason"),
        "usage": raw.get("usage") or {},
        "error": raw.get("error"),
        "response_text": raw.get("response_text") or "",
    }
    write_json(raw_path, payload)
    if not raw.get("ok"):
        raise RuntimeError(f"model call failed for {raw_path.name}: {raw.get('error')}")
    try:
        return extract_json_object(raw["response_text"])
    except Exception as exc:
        payload["parse_error"] = repr(exc)
        write_json(raw_path, payload)
        raise


def safe_file_stem(value: Any, max_chars: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return (text[:max_chars] or "card") + "_" + sha256_text(str(value))[:8]


def run_model_json_jobs(args: argparse.Namespace, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not jobs:
        return []
    max_workers = max(1, min(args.api_concurrency, len(jobs)))
    results: list[dict[str, Any] | None] = [None] * len(jobs)

    def run_one(job: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return (
            job["index"],
            model_json_call(args, job["model"], job["prompt"], job["raw_path"], job["max_tokens"]),
        )

    if max_workers == 1:
        for job in jobs:
            index, payload = run_one(job)
            results[index] = payload
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(run_one, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    index, payload = future.result()
                except Exception as exc:
                    raise RuntimeError(f"model job failed: {job['name']}: {exc}") from exc
                results[index] = payload

    return [result for result in results if result is not None]


def detect_split(
    args: argparse.Namespace,
    skill: dict[str, Any],
    packs: list[dict[str, Any]],
    split_name: str,
    raw_dir: Path,
    raw_prefix: str,
    method: str,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for index, pack in enumerate(packs):
        card_id = pack.get("card_id", f"{split_name}_{index}")
        jobs.append(
            {
                "index": index,
                "name": f"{raw_prefix}:{card_id}",
                "model": args.detector_model,
                "prompt": render_detection_prompt(skill, [pack], split_name),
                "raw_path": raw_dir / f"{raw_prefix}_{index:03d}_{safe_file_stem(card_id)}.json",
                "max_tokens": args.detector_tokens,
            }
        )
    detections: list[dict[str, Any]] = []
    for payload in run_model_json_jobs(args, jobs):
        detections.extend(normalize_detections(payload, method))
    return detections


def reverse_audit_feedback(
    args: argparse.Namespace,
    skill: dict[str, Any],
    dev_packs: list[dict[str, Any]],
    dev_detections: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    raw_dir: Path,
) -> dict[str, Any]:
    detections_by_card: dict[str, list[dict[str, Any]]] = {}
    for detection in dev_detections:
        detections_by_card.setdefault(str(detection.get("card_id")), []).append(detection)

    jobs: list[dict[str, Any]] = []
    for index, pack in enumerate(dev_packs):
        card_id = str(pack.get("card_id", f"dev_{index}"))
        card_detections = detections_by_card.get(card_id, [])
        card = cards_by_id.get(card_id)
        card_hard = hard_check_detections(card_detections, {card_id: card} if card else {})
        jobs.append(
            {
                "index": index,
                "name": f"reverse_audit:{card_id}",
                "model": args.feedback_model,
                "prompt": render_feedback_prompt(skill, [pack], card_detections, card_hard),
                "raw_path": raw_dir / f"r0_dev_reverse_audit_feedback_{index:03d}_{safe_file_stem(card_id)}.json",
                "max_tokens": args.feedback_tokens,
            }
        )

    reverse_audits: list[dict[str, Any]] = []
    global_feedback: list[Any] = []
    payloads = run_model_json_jobs(args, jobs)
    for payload in payloads:
        reverse_audits.extend(payload.get("reverse_audits") or [])
        feedback_items = payload.get("global_feedback") or []
        if isinstance(feedback_items, list):
            global_feedback.extend(feedback_items)
        else:
            global_feedback.append(feedback_items)

    return {
        "reverse_audits": reverse_audits,
        "global_feedback": global_feedback,
        "per_card_feedback_calls": len(payloads),
    }


def usage_summary(raw_dir: Path) -> dict[str, Any]:
    totals: dict[str, Counter] = {}
    for path in raw_dir.glob("*.json"):
        payload = read_json(path)
        model = payload.get("model", "unknown")
        bucket = totals.setdefault(model, Counter())
        usage = payload.get("usage") or {}
        bucket["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        bucket["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        bucket["total_tokens"] += int(usage.get("total_tokens") or 0)
        bucket["calls"] += 1
    return {model: dict(counter) for model, counter in sorted(totals.items())}


def render_report(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    lines = [
        "# AutoDL Gold-Free R0->R1 CoderForge 50 Pilot",
        "",
        f"Generated at: {result['created_at']}",
        "",
        "## Decision",
        "",
        "This run is a gold-free skill-evolution pilot. It reports evidence-quality feedback and does not report final hallucination F1.",
        "",
        "## Setup",
        "",
        f"- Source: `{result['source_id']}` / `{result['agent_system_id']}`",
        f"- Split counts: `{metrics['split_counts']}`",
        f"- Skill model: `{result['models']['skill_model']}`",
        f"- Detector model: `{result['models']['detector_model']}`",
        f"- Feedback model: `{result['models']['feedback_model']}`",
        f"- API concurrency: `{result['api_concurrency']}`",
        "",
        "## Hard Checks",
        "",
        f"- R0 dev: `{metrics['r0_dev_hard_checks']}`",
        f"- R1 test: `{metrics['r1_test_hard_checks']}`",
        "",
        "## Gold-Free Quality Summary",
        "",
        f"- R0 dev distribution: `{metrics.get('r0_dev_distribution', {})}`",
        f"- R1 test distribution: `{metrics.get('r1_test_distribution', {})}`",
        f"- R0->R1 revision summary: `{metrics.get('r0_to_r1_revision', {})}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, path in result["paths"].items():
        lines.append(f"- `{key}`: `{path}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Judge feedback is reverse audit of detector outputs, not ground-truth label generation.",
            "- `reward`, `resolved`, `test_output`, and evaluator logs are evidence fields, not labels.",
            "- Test split is used only after `skill_r1` is frozen.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_cards(args: argparse.Namespace) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    root = args.selected_cards_root or args.dataset_root
    prefix = "selected_cards" if args.selected_cards_root else "evidence_cards"
    for split in ("train", "dev", "test"):
        path = root / f"{prefix}_{split}.jsonl"
        cards.extend(read_jsonl(path))
    if not cards:
        raise ValueError(f"no cards loaded from {root}")
    return cards


def run(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = args.project_root / "data" / args.phase
    output_dir = args.project_root / "outputs" / args.phase
    raw_dir = output_dir / "raw_calls"
    results_dir = args.project_root / "results"
    for path in (data_dir, output_dir, raw_dir, results_dir):
        path.mkdir(parents=True, exist_ok=True)

    taxonomy = read_json(args.taxonomy)
    selected = select_pilot_cards(
        load_cards(args),
        train_n=args.train_n,
        dev_n=args.dev_n,
        test_n=args.test_n,
        source_id=args.source_id,
        agent_system_id=args.agent_system_id,
    )
    all_selected = selected["train"] + selected["dev"] + selected["test"]
    cards_by_id = {str(card["card_id"]): card for card in all_selected}
    packs = {
        split: [evidence_pack_for_card(card, char_limit=args.pack_chars) for card in rows]
        for split, rows in selected.items()
    }

    for split, rows in selected.items():
        write_jsonl(data_dir / f"selected_cards_{split}.jsonl", rows)
        write_jsonl(data_dir / f"evidence_packs_{split}.jsonl", packs[split])

    prompt_manifest = {
        "created_at": now_iso(),
        "phase": args.phase,
        "source_id": args.source_id,
        "agent_system_id": args.agent_system_id,
        "input_mode": "preselected_cards" if args.selected_cards_root else "full_split_cards",
        "split_counts": {split: len(rows) for split, rows in selected.items()},
        "models": {
            "skill_model": args.skill_model,
            "detector_model": args.detector_model,
            "feedback_model": args.feedback_model,
        },
        "api_concurrency": args.api_concurrency,
        "test_split_policy": "test is used only after skill_r1 is frozen",
        "gold_free_policy": "no judge-created truth labels; hard checks + reverse audit quality feedback only",
    }
    write_json(data_dir / "pilot_manifest.json", prompt_manifest)

    if args.dry_run:
        prompts = {
            "skill_r0": render_skill_r0_prompt(packs["train"], taxonomy),
            "r0_dev_detection": render_detection_prompt({"skill_version": "r0_dry_run"}, packs["dev"], "dev"),
            "feedback": render_feedback_prompt({"skill_version": "r0_dry_run"}, packs["dev"], [], {}),
            "revision": render_revision_prompt({"skill_version": "r0_dry_run"}, [], {}, {}),
            "r1_test_detection": render_detection_prompt({"skill_version": "r1_dry_run"}, packs["test"], "test"),
        }
        dry_run_payload = {
            "created_at": now_iso(),
            "prompt_chars": {key: len(value) for key, value in prompts.items()},
            "prompt_sha256": {key: sha256_text(value) for key, value in prompts.items()},
            "selected_counts": {split: len(rows) for split, rows in selected.items()},
            "api_concurrency": args.api_concurrency,
        }
        write_json(results_dir / f"{args.phase}_dry_run.json", dry_run_payload)
        return {
            "created_at": now_iso(),
            "phase": args.phase,
            "dry_run": True,
            "source_id": args.source_id,
            "agent_system_id": args.agent_system_id,
            "models": prompt_manifest["models"],
            "api_concurrency": args.api_concurrency,
            "metrics": {"split_counts": dry_run_payload["selected_counts"]},
            "paths": {
                "manifest": str(data_dir / "pilot_manifest.json"),
                "dry_run": str(results_dir / f"{args.phase}_dry_run.json"),
            },
        }

    skill_r0 = model_json_call(
        args,
        args.skill_model,
        render_skill_r0_prompt(packs["train"], taxonomy),
        raw_dir / "skill_r0_induction.json",
        args.skill_tokens,
    )
    write_json(output_dir / "skill_r0.json", skill_r0)

    r0_dev_detections = detect_split(
        args,
        skill_r0,
        packs["dev"],
        "dev",
        raw_dir,
        "r0_dev_detection",
        "skill_r0_dev",
    )
    write_jsonl(output_dir / "r0_dev_detections.jsonl", r0_dev_detections)
    r0_hard = hard_check_detections(r0_dev_detections, cards_by_id)
    write_json(output_dir / "r0_dev_hard_checks.json", r0_hard)

    feedback = reverse_audit_feedback(
        args,
        skill_r0,
        packs["dev"],
        r0_dev_detections,
        cards_by_id,
        raw_dir,
    )
    write_jsonl(output_dir / "r0_dev_feedback.jsonl", feedback.get("reverse_audits") or [])
    write_json(output_dir / "r0_dev_feedback_summary.json", feedback)

    skill_r1 = model_json_call(
        args,
        args.skill_model,
        render_revision_prompt(skill_r0, r0_dev_detections, r0_hard, feedback),
        raw_dir / "skill_r1_revision.json",
        args.skill_tokens,
    )
    write_json(output_dir / "skill_r1.json", skill_r1)

    r1_test_detections = detect_split(
        args,
        skill_r1,
        packs["test"],
        "test",
        raw_dir,
        "r1_test_detection",
        "skill_r1_test",
    )
    write_jsonl(output_dir / "r1_test_detections.jsonl", r1_test_detections)
    r1_hard = hard_check_detections(r1_test_detections, cards_by_id)
    write_json(output_dir / "r1_test_hard_checks.json", r1_hard)

    result = {
        "created_at": now_iso(),
        "phase": args.phase,
        "dry_run": False,
        "source_id": args.source_id,
        "agent_system_id": args.agent_system_id,
        "models": prompt_manifest["models"],
        "api_concurrency": args.api_concurrency,
        "metrics": {
            "split_counts": {split: len(rows) for split, rows in selected.items()},
            "r0_dev_hard_checks": r0_hard,
            "r1_test_hard_checks": r1_hard,
            "r0_dev_distribution": summarize_detections(
                r0_dev_detections, [str(card.get("card_id")) for card in selected["dev"]]
            ),
            "r1_test_distribution": summarize_detections(
                r1_test_detections, [str(card.get("card_id")) for card in selected["test"]]
            ),
            "r0_to_r1_revision": summarize_revision(skill_r1),
            "usage": usage_summary(raw_dir),
        },
        "paths": {
            "manifest": str(data_dir / "pilot_manifest.json"),
            "skill_r0": str(output_dir / "skill_r0.json"),
            "r0_dev_detections": str(output_dir / "r0_dev_detections.jsonl"),
            "r0_dev_feedback": str(output_dir / "r0_dev_feedback.jsonl"),
            "skill_r1": str(output_dir / "skill_r1.json"),
            "r1_test_detections": str(output_dir / "r1_test_detections.jsonl"),
            "metrics": str(results_dir / f"{args.phase}_metrics.json"),
            "report": str(results_dir / f"{args.phase}_report.md"),
        },
    }
    write_json(results_dir / f"{args.phase}_metrics.json", result)
    (results_dir / f"{args.phase}_report.md").write_text(render_report(result), encoding="utf-8", newline="\n")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--dataset-root", type=Path, default=Path("data") / DATASET_NAME)
    parser.add_argument("--selected-cards-root", type=Path)
    parser.add_argument("--taxonomy", type=Path, default=Path("taxonomy") / "coding_agent_hallucination_taxonomy_v0.2.json")
    parser.add_argument("--phase", default=DEFAULT_PHASE)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--agent-system-id", default=DEFAULT_AGENT_SYSTEM_ID)
    parser.add_argument("--train-n", type=int, default=30)
    parser.add_argument("--dev-n", type=int, default=10)
    parser.add_argument("--test-n", type=int, default=10)
    parser.add_argument("--base-url", default=os.environ.get("AUTODL_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("AUTODL_API_KEY"))
    parser.add_argument("--skill-model", default=os.environ.get("SKILL_MODEL", DEFAULT_SKILL_MODEL))
    parser.add_argument("--detector-model", default=os.environ.get("DETECTOR_MODEL", DEFAULT_DETECTOR_MODEL))
    parser.add_argument("--feedback-model", default=os.environ.get("FEEDBACK_MODEL", DEFAULT_FEEDBACK_MODEL))
    parser.add_argument("--pack-chars", type=int, default=1800)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--skill-tokens", type=int, default=6000)
    parser.add_argument("--detector-tokens", type=int, default=6000)
    parser.add_argument("--feedback-tokens", type=int, default=6000)
    parser.add_argument(
        "--api-concurrency",
        type=int,
        default=int(os.environ.get("AUTODL_API_CONCURRENCY", DEFAULT_API_CONCURRENCY)),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.api_concurrency < 1:
        raise SystemExit("--api-concurrency must be >= 1")
    if not args.dry_run and not args.api_key:
        raise SystemExit("AUTODL_API_KEY must be set unless --dry-run is used")
    return args


def main(argv: list[str] | None = None) -> int:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
