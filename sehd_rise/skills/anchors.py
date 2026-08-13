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
from typing import Any, Mapping, Sequence


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
_WORD_RE = re.compile(r"[A-Za-z]{3,}")

# Closed label-free mechanism-evidence tokens for the terminal claim locus.
# The FN mechanism classifier consumes exactly this vocabulary.
TOOL_RESULT_CITED = "tool_result_cited"
TOOL_RESULT_ADJACENT = "tool_result_adjacent"
NUMERIC_LOCUS = "numeric_locus"
CITATION_LOCUS = "citation_locus"
LOCUS_EVIDENCE_TOKENS = frozenset({
    TOOL_RESULT_CITED, TOOL_RESULT_ADJACENT, NUMERIC_LOCUS, CITATION_LOCUS,
})

# Function-word tokens excluded from tool-result adoption overlap: a claim
# sharing only stopwords with a result does not depend on that result.
_STOP_TOKENS = frozenset({
    "the", "and", "for", "are", "was", "has", "had", "have", "not", "with",
    "that", "this", "from", "into", "were", "will", "would", "they", "them",
    "then", "than", "you", "your", "its", "his", "her", "our", "their",
    "been", "being", "there", "here", "after", "before",
})


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
        calls = raw.get("tool_calls")
        if not calls:
            continue
        if not isinstance(calls, list):
            calls = [calls]
        for call_index, call in enumerate(calls, start=1):
            if call is not None:
                refs.append(f"event_{event_index:03d}_tool_{call_index:02d}")
    return tuple(refs)


def _adjacent_tool_responses(
    history: list[Any], event_index: int,
) -> tuple[str, ...]:
    """Tool-result texts available at one event (its own row or the previous).

    A claim only reads results delivered on its own row or the row right
    before it; older results no longer ground this claim. ``event_index`` is
    1-based; string responses contribute their text, object responses their
    string values.
    """
    rows = history[max(0, event_index - 2):event_index]
    texts: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        responses = row.get("tool_responses") or ()
        if isinstance(responses, (str, Mapping)):
            responses = (responses,)
        if not isinstance(responses, (list, tuple)):
            continue
        for response in responses:
            if isinstance(response, str):
                texts.append(response)
            elif isinstance(response, Mapping):
                texts.extend(
                    value for value in response.values() if isinstance(value, str)
                )
    return tuple(texts)


def _span_adopts_tool_result(
    span: str, responses: Sequence[str],
) -> bool:
    """Whether a span shows adoption evidence for one of the given results.

    Adoption is label-free and deterministic: a citation marker in the span,
    or non-stopword token / multi-digit overlap with a result text.
    """
    if _CITATION_RE.search(span):
        return True
    lowered = span.lower()
    span_tokens = set(_WORD_RE.findall(lowered)) - _STOP_TOKENS
    span_numbers = set(re.findall(r"\d{2,}", span))
    for response in responses:
        response_lower = str(response).lower()
        if span_tokens & (set(_WORD_RE.findall(response_lower)) - _STOP_TOKENS):
            return True
        if span_numbers & set(re.findall(r"\d{2,}", response_lower)):
            return True
    return False


def project_locus_evidence(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Closed label-free mechanism evidence for the terminal claim locus.

    The locus is the final assistant event: the claim the detector missed
    surfaces in the terminal trajectory there. Tokens come from the closed
    ``LOCUS_EVIDENCE_TOKENS`` vocabulary so the FN mechanism classifier reads
    a fixed set of shapes. Numeric and citation presence are the same proxies
    the anchor projector already exposes; the tool tokens additionally
    require results on the same or previous row plus adoption evidence.
    """
    if not isinstance(record, Mapping):
        return ()
    history = record.get("history")
    if not isinstance(history, list) or not history:
        return ()
    locus_index = -1
    locus_content = ""
    for index, raw in enumerate(history, start=1):
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("role") or "").lower() != "assistant":
            continue
        content = _text(raw)
        if content.strip():
            locus_index = index
            locus_content = content
    if locus_index < 0:
        return ()
    responses = _adjacent_tool_responses(history, locus_index)
    hints: list[str] = []
    if responses:
        hints.append(
            TOOL_RESULT_CITED
            if _span_adopts_tool_result(locus_content, responses)
            else TOOL_RESULT_ADJACENT
        )
    if _NUMBER_RE.search(locus_content):
        hints.append(NUMERIC_LOCUS)
    if _CITATION_RE.search(locus_content):
        hints.append(CITATION_LOCUS)
    return tuple(hints)


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
    """Project the complete fixed assistant-span universe, label-free.

    The projector never truncates: a long trajectory keeps every span so the
    binding search space cannot drop a claim before candidate construction
    starts. Prompt budget is a consumer concern — ``render_binding_menu``
    selects its rows through ``select_relevant_anchors``.
    """
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
    return tuple(anchors)


_MENU_GROUP_COUNT = 5


def select_relevant_anchors(
    anchors: Sequence[ClaimAnchor], limit: int,
) -> tuple[ClaimAnchor, ...]:
    """Relevance-preserving, bounded menu selection over the anchor universe.

    Priority groups: final-assistant-event anchors, tool-grounded anchors,
    citation anchors, origin-neighborhood anchors (first assistant event),
    then the remaining diversity pool. Each group contributes up to
    ``ceil(limit / 5)`` slots before a refill pass, so one dense event cannot
    starve the other groups — a relevant anchor far beyond the first N spans
    of document order still reaches the menu. Deterministic and label-free:
    a pure projection of the anchor features.
    """
    limit = max(0, int(limit))
    if limit == 0 or not anchors:
        return ()
    origin_event = min(anchor.source_event_id for anchor in anchors)
    grouped: dict[int, list[ClaimAnchor]] = {
        key: [] for key in range(_MENU_GROUP_COUNT)
    }
    for anchor in anchors:
        features = anchor.observable_features
        if features.get("anchor_is_final_assistant"):
            key = 0
        elif anchor.anchor_kind == "tool_grounded_claim":
            key = 1
        elif features.get("anchor_has_citation"):
            key = 2
        elif anchor.source_event_id == origin_event:
            key = 3
        else:
            key = 4
        grouped[key].append(anchor)
    quota = -(-limit // _MENU_GROUP_COUNT)
    selected: list[ClaimAnchor] = []
    for key in range(_MENU_GROUP_COUNT):
        selected.extend(grouped[key][:quota])
        if len(selected) >= limit:
            return tuple(selected[:limit])
    for key in range(_MENU_GROUP_COUNT):
        remaining = grouped[key][quota:]
        take = min(len(remaining), limit - len(selected))
        selected.extend(remaining[:take])
        if len(selected) >= limit:
            return tuple(selected)
    return tuple(selected)


__all__ = [
    "CITATION_LOCUS",
    "ClaimAnchor",
    "LOCUS_EVIDENCE_TOKENS",
    "NUMERIC_LOCUS",
    "TOOL_RESULT_ADJACENT",
    "TOOL_RESULT_CITED",
    "project_claim_anchors",
    "project_locus_evidence",
    "select_relevant_anchors",
]
