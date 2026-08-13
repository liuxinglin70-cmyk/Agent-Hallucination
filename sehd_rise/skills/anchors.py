"""Deterministic, label-free claim anchors for constructive Skill grounding.

An anchor is an observable span in an assistant trajectory event.  It is not
a candidate and carries no error, evidence, certificate, or verdict authority.
The projector is deliberately provider-free: the same label-free trajectory
always produces the same spans and ids before evolution or reflection runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


_GOLD_KEYS = frozenset({
    "is_hallucination", "hallucination_step", "hallucination_reason",
    "hallucination_category", "hallucination_subcategory", "gold_label",
    "gold_steps", "gold_explanation", "outer_feedback", "true_answer",
    "agent_answer",
})
_CONTENT_KEYS = ("content", "message", "text", "thought", "response", "assistant_content")
_SPAN_RE = re.compile(r"[^\n.!?。！？]+(?:[.!?。！？]+|$)", re.UNICODE)
_NUMBER_RE = re.compile(r"\d")
_CITATION_RE = re.compile(r"https?://|\[[0-9]+\]|\([A-Z][A-Za-z]+,?\s+\d{4}\)")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _owned_gold_hits(value: Any) -> set[str]:
    """Reject evaluator-owned fields anywhere in the projector envelope."""
    if isinstance(value, Mapping):
        hits = {str(key) for key in value if str(key) in _GOLD_KEYS}
        for item in value.values():
            hits.update(_owned_gold_hits(item))
        return hits
    if isinstance(value, (list, tuple)):
        hits: set[str] = set()
        for item in value:
            hits.update(_owned_gold_hits(item))
        return hits
    return set()


def _text(row: Mapping[str, Any]) -> str:
    for key in _CONTENT_KEYS:
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""


def _tool_interaction_ids(history: list[Any], stop: int) -> tuple[str, ...]:
    refs: list[str] = []
    for event_index, raw in enumerate(history[:stop], start=1):
        if not isinstance(raw, Mapping):
            continue
        calls = raw.get("tool_calls") or ()
        if not isinstance(calls, list):
            calls = [calls]
        for call_index, call in enumerate(calls, start=1):
            if call is not None:
                refs.append(f"event_{event_index:03d}_tool_{call_index:02d}")
    return tuple(refs)


@dataclass(frozen=True)
class ClaimAnchor:
    anchor_id: str
    record_id: str
    source_event_id: str
    step_index: int
    role: str
    span_start: int
    span_end: int
    span_text: str
    span_text_hash: str
    anchor_kind: str
    observable_features: Mapping[str, Any]
    upstream_ref_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observable_features"] = dict(self.observable_features)
        payload["upstream_ref_ids"] = list(self.upstream_ref_ids)
        return payload


def project_claim_anchors(record: Mapping[str, Any]) -> tuple[ClaimAnchor, ...]:
    """Project a fixed assistant-span universe without reading evaluator data."""
    if not isinstance(record, Mapping):
        raise TypeError("claim anchor record must be an object")
    leaked = sorted(_owned_gold_hits(record))
    if leaked:
        raise ValueError("claim anchor input must be label-free: " + ", ".join(leaked))
    history = record.get("history")
    if not isinstance(history, list) or not history:
        return ()

    label_free_projection = []
    for raw in history:
        row = raw if isinstance(raw, Mapping) else {"content": raw}
        label_free_projection.append({
            "role": str(row.get("role") or ""),
            "content": _text(row),
            "tool_calls": row.get("tool_calls") or [],
            "tool_responses": row.get("tool_responses") or [],
        })
    record_id = "record_" + _canonical_hash(label_free_projection)[:16]

    anchors: list[ClaimAnchor] = []
    assistant_event_indexes = [
        index for index, raw in enumerate(history, start=1)
        if isinstance(raw, Mapping)
        and str(raw.get("role") or "").lower() == "assistant"
        and bool(_text(raw).strip())
    ]
    final_assistant_event = assistant_event_indexes[-1] if assistant_event_indexes else -1
    for event_index, raw in enumerate(history, start=1):
        if not isinstance(raw, Mapping):
            continue
        role = str(raw.get("role") or "").lower()
        if role != "assistant":
            continue
        content = _text(raw)
        if not content.strip():
            continue
        upstream_refs = _tool_interaction_ids(history, event_index - 1)
        matches = list(_SPAN_RE.finditer(content))
        for span_index, match in enumerate(matches):
            raw_span = match.group(0)
            leading = len(raw_span) - len(raw_span.lstrip())
            trailing = len(raw_span.rstrip())
            start = match.start() + leading
            end = match.start() + trailing
            span = content[start:end]
            if not span:
                continue
            kind = (
                "question" if span.rstrip().endswith(("?", "？"))
                else "code" if "```" in span
                else "tool_grounded_claim" if upstream_refs
                else "assistant_assertion"
            )
            span_hash = _canonical_hash(" ".join(span.split()))
            event_id = f"event_{event_index:03d}"
            anchor_key = {
                "record_id": record_id,
                "source_event_id": event_id,
                "span_start": start,
                "span_end": end,
                "span_text_hash": span_hash,
            }
            anchors.append(ClaimAnchor(
                anchor_id="anchor_" + _canonical_hash(anchor_key)[:16],
                record_id=record_id,
                source_event_id=event_id,
                step_index=event_index,
                role=role,
                span_start=start,
                span_end=end,
                span_text=span,
                span_text_hash=span_hash,
                anchor_kind=kind,
                observable_features={
                    "anchor_kind": kind,
                    "anchor_role": role,
                    "anchor_depends_on_tool": bool(upstream_refs),
                    "anchor_has_number": bool(_NUMBER_RE.search(span)),
                    "anchor_has_citation": bool(_CITATION_RE.search(span)),
                    "anchor_is_final_assistant": event_index == final_assistant_event,
                    "anchor_is_first_in_event": span_index == 0,
                    "anchor_is_last_in_event": span_index == len(matches) - 1,
                },
                upstream_ref_ids=upstream_refs,
            ))
    return tuple(anchors[:64])


__all__ = ["ClaimAnchor", "project_claim_anchors"]
