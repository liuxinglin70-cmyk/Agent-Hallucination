"""Canonical packet, strict input firewall, and stage views.

The detector consumes exactly two record fields: ``question`` and
``history``. Anything else at the top level is rejected; gold answer fields
never enter or persist in a detector input.
"""

from __future__ import annotations

import ast
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from detector.identity import digest

PACKET_SCHEMA = "detector.packet.v1"
EVENT_WINDOW_PROJECTION_SCHEMA = "detector.event_window_projection.v2"

ALLOWED_RECORD_KEYS = frozenset({"question", "history"})

EVALUATOR_ONLY_FIELDS = frozenset({
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "hallucination_category",
    "hallucination_subcategory",
    "true_answer",
    "explanation",
    "gold_label",
    "gold_steps",
    "gold_explanation",
})

_ANSWER_TOOL_HINTS = (
    "final_answer", "answer", "finish", "terminate", "submit",
    "respond", "conclude",
)

STEP_KIND_CONTENT = "content_step"
STEP_KIND_ACTION = "action_step"
STEP_KIND_EMPTY = "empty_step"

PACKET_FEATURE_NAMES = frozenset({
    "has_tool_calls",
    "has_retrieval_tool",
    "has_answer_tool",
    "has_numeric_content",
    "has_long_prose",
    "multi_turn_question",
    "question_has_numbers",
})

_JUDGE_VIEW_CHARS = 400_000
_JUDGE_EVENT_CHARS = 100_000
_JUDGE_TOOL_CHARS = 100_000

_DEFENSE_VIEW_CHARS = 200_000
_DEFENSE_EVENT_CHARS = 100_000
_DEFENSE_WINDOW = 2

_INDEX_VIEW_CHARS = 48_000
_INDEX_TAIL_EVENTS = 8
_INDEX_EVENT_PREVIEW = 300
_INDEX_TOOL_PREVIEW = 120


class PacketError(ValueError):
    """A raw record cannot safely enter the detector."""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(row: Mapping[str, Any]) -> Any:
    for key in (
        "content", "message", "text", "thought", "response",
        "assistant_content",
    ):
        if key in row:
            return row[key]
    return None


def _question_text(value: Any) -> str:
    """Flatten a benchmark question into prompt text.

    AgentHallu stores single-turn questions as a string and multi-turn
    ones as a list of turns, each turn a list of strings. Turn numbering
    keeps the multi-turn structure visible to the judge.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        raise PacketError("question must be a non-empty string or turn list")
    turns = [
        " ".join(str(part).strip() for part in _as_list(turn))
        for turn in value
    ]
    if any(not turn for turn in turns):
        raise PacketError("question turns must be non-empty")
    if len(turns) == 1:
        return turns[0]
    return "\n".join(
        f"[turn {index}] {turn}" for index, turn in enumerate(turns, 1)
    )


def _tool_name(call: Any) -> str:
    if isinstance(call, str):
        return call
    if not isinstance(call, dict):
        return "unknown_tool"
    nested = call.get("function")
    if isinstance(nested, dict):
        return str(nested.get("name") or "unknown_tool")
    return str(
        call.get("name") or call.get("tool_name") or call.get("tool")
        or "unknown_tool"
    )


def _tool_arguments(call: Any) -> Any:
    if not isinstance(call, dict):
        return None
    nested = call.get("function")
    if isinstance(nested, dict) and "arguments" in nested:
        return nested["arguments"]
    for key in ("arguments", "args", "input", "parameters"):
        if key in call:
            return call[key]
    return None


def _forbidden_nested_keys(record: Mapping[str, Any]) -> set[str]:
    """Gold labels must not hide anywhere inside the trajectory; metadata
    keys are a top-level concern (ALLOWED_RECORD_KEYS rejects them there)."""
    hits: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            hits.update(
                str(key) for key in value if str(key) in EVALUATOR_ONLY_FIELDS
            )
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(record)
    return hits


def validate_record(record: Mapping[str, Any]) -> None:
    """Reject anything that is not ``{question, history}`` (fail-closed)."""
    if not isinstance(record, Mapping):
        raise PacketError("record must be an object")
    extra = sorted(set(record) - ALLOWED_RECORD_KEYS)
    if extra:
        raise PacketError(
            "detector input allows only {question, history}; "
            f"rejected top-level fields: {extra}"
        )
    hits = _forbidden_nested_keys(record)
    if hits:
        raise PacketError(
            "detector input must be label-free: " + ", ".join(sorted(hits))
        )
    build_packet(record)


def build_packet(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compile ``{question, history}`` into the provider-safe packet."""
    if not isinstance(record, dict):
        raise PacketError("record must be an object")
    history = record.get("history")
    if not isinstance(history, list) or not history:
        raise PacketError("history must be a non-empty list")
    question = _question_text(record.get("question"))
    if not question.strip():
        raise PacketError("question must be a non-empty string")

    events: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(history, start=1):
        row = raw if isinstance(raw, dict) else {"content": raw}
        event_id = f"event_{ordinal:03d}"
        calls = _as_list(row.get("tool_calls"))
        responses = _as_list(row.get("tool_responses"))
        interactions: list[dict[str, Any]] = []
        for call_ordinal, call in enumerate(calls, start=1):
            interactions.append({
                "interaction_id": f"{event_id}_tool_{call_ordinal:02d}",
                "tool_name": _tool_name(call),
                "arguments": _tool_arguments(call),
                "observation": (
                    responses[call_ordinal - 1]
                    if call_ordinal <= len(responses) else None
                ),
            })
        events.append({
            "event_id": event_id,
            "position": ordinal,
            "source_step_id": row.get("step"),
            "role": row.get("role"),
            "content": _text(row),
            "tool_interactions": interactions,
        })

    packet = {
        "schema_version": PACKET_SCHEMA,
        "question": question,
        "events": events,
    }
    validate_packet(packet)
    return packet


def validate_packet(packet: Mapping[str, Any]) -> None:
    events = packet.get("events")
    if not isinstance(events, list) or not events:
        raise PacketError("packet events must be a non-empty list")
    ids: list[str] = []
    positions: list[int] = []
    for event in events:
        if not isinstance(event, dict):
            raise PacketError("packet events must be objects")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise PacketError("packet event ids must be non-empty strings")
        position = event.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            raise PacketError("packet positions must be integers")
        ids.append(event_id)
        positions.append(position)
        parent = str(event.get("event_id") or "")
        interactions = event.get("tool_interactions") or []
        if not isinstance(interactions, list):
            raise PacketError("packet tool_interactions must be a list")
        for interaction in interactions:
            if not isinstance(interaction, Mapping):
                raise PacketError("packet tool interactions must be objects")
            iid = interaction.get("interaction_id")
            if not isinstance(iid, str):
                raise PacketError("packet interaction ids must be strings")
            if not iid.startswith(parent + "_tool_"):
                raise PacketError(f"bad interaction id {iid!r}")
            ids.append(iid)
    if positions != list(range(1, len(events) + 1)):
        raise PacketError("packet positions must be 1..N")
    if len(ids) != len(set(ids)):
        raise PacketError("packet ids must be unique")


def packet_reference_index(packet: Mapping[str, Any]) -> dict[str, str]:
    """Map each canonical event or interaction reference to its owning event."""
    refs = {}
    for event in packet.get("events") or ():
        event_id = str(event.get("event_id") or "")
        if event_id:
            refs[event_id] = event_id
        for interaction in event.get("tool_interactions") or ():
            interaction_id = str(interaction.get("interaction_id") or "")
            if interaction_id:
                refs[interaction_id] = event_id
    return refs


def packet_reference_error(
    packet: Mapping[str, Any], refs: Any, *,
    event_ids: Sequence[str] | None = None, required: bool = False,
) -> str | None:
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        return "evidence_refs must be a list of strings"
    if required and not refs:
        return "evidence_refs is required"
    owners = packet_reference_index(packet)
    if any(ref not in owners for ref in refs):
        return "evidence_refs contains an unknown packet event"
    if event_ids is not None:
        allowed = set(event_ids)
        if any(owners[ref] not in allowed for ref in refs):
            return "evidence_refs references events outside the selected scope"
    return None


def event_id_for_step(packet: Mapping[str, Any], step: int | None) -> str | None:
    """Return the canonical event id for a 1-based packet position."""
    if not isinstance(step, int) or isinstance(step, bool):
        return None
    for event in packet.get("events") or []:
        if int(event.get("position") or 0) == step:
            return str(event.get("event_id") or "") or None
    return None


def packet_quote_error(
    packet: Mapping[str, Any], quote: Any, *, locus: str,
    evidence_refs: Sequence[str], required: bool = True,
) -> str | None:
    """Check an exact quote against separate source channels in its cited locus.

    An event reference covers its prose and tools; an interaction reference
    covers only that interaction. Rendering labels are not source evidence.
    """
    if quote is None and not required:
        return None
    if not isinstance(quote, str):
        return "quote must be a string"
    text = quote.strip()
    if not text:
        return "quote is required" if required else None
    owners = packet_reference_index(packet)
    owner = owners.get(locus)
    if owner is None:
        return "quote locus is an unknown packet reference"
    channels = []
    for event in packet.get("events") or ():
        if event.get("event_id") != owner:
            continue
        event_cited = owner in evidence_refs and locus == owner
        if event_cited:
            channels.append(event.get("content"))
        for interaction in event.get("tool_interactions") or ():
            iid = interaction.get("interaction_id")
            if locus != owner and iid != locus:
                continue
            if event_cited or iid in evidence_refs or owner in evidence_refs:
                channels.extend(interaction.get(key) for key in
                                ("tool_name", "arguments", "observation"))
    if any(channel is not None and text in str(channel) for channel in channels):
        return None
    return "quote does not occur in a cited source channel at its locus"


def step_kind(packet: Mapping[str, Any], step: int) -> str | None:
    """Classify one packet position by the *structure of its own event*.

    The localization reading needs to say whether the step a verdict blames
    is a content step (the event carries the assistant's own prose) or an
    action step (the event carries only tool calls). The distinction is
    structural on purpose: it is read off the event the packet already built
    -- whether ``content`` is non-empty, whether ``tool_interactions`` is
    non-empty -- and never from keywords, lengths or any semantic guess about
    what the prose says. A step that both speaks and acts is a content step:
    prose is locally checkable text, and the reader who wants "did it only
    act here" has ``tool_interactions`` in the same event.

    Returns ``None`` for a position outside ``1..len(events)``, so a caller
    can tell "unclassifiable" from "classified as empty".
    """
    for event in packet.get("events") or []:
        if int(event.get("position") or 0) != int(step):
            continue
        content = event.get("content")
        if content not in (None, "") and str(content).strip():
            return STEP_KIND_CONTENT
        if event.get("tool_interactions"):
            return STEP_KIND_ACTION
        return STEP_KIND_EMPTY
    return None




def project_event_window(
    packet: Mapping[str, Any],
    *,
    anchor_event_id: str,
    target_event_id: str | None = None,
    window_before: int = 2,
    window_after: int = 2,
    include_delivered: bool = True,
) -> dict[str, Any]:
    """Project the interval between anchor and target with explicit clipped margins."""
    for name, value in (("window_before", window_before), ("window_after", window_after)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PacketError(f"{name} must be a non-negative integer")
    if not isinstance(include_delivered, bool):
        raise PacketError("include_delivered must be a boolean")
    events = list(packet.get("events") or [])
    if not events:
        raise PacketError("packet contains no events")
    validate_packet(packet)
    by_id = {event["event_id"]: event for event in events}
    if anchor_event_id not in by_id:
        raise PacketError(f"anchor event {anchor_event_id!r} not found in packet")
    if target_event_id is not None and target_event_id not in by_id:
        raise PacketError(f"target event {target_event_id!r} not found in packet")
    anchor_pos = by_id[anchor_event_id]["position"]
    target_pos = by_id[target_event_id]["position"] if target_event_id is not None else None
    first = min(anchor_pos, target_pos if target_pos is not None else anchor_pos)
    last = max(anchor_pos, target_pos if target_pos is not None else anchor_pos)
    requested_start, requested_end = first - window_before, last + window_after
    start, end = max(1, requested_start), min(len(events), requested_end)
    projected_events = deepcopy(events[start - 1:end])
    projected_ids = [event["event_id"] for event in projected_events]
    omitted_ids = [event["event_id"] for event in events
                   if event["event_id"] not in projected_ids]
    reasons = []
    if requested_start < 1:
        reasons.append("window_before_clipped")
    if requested_end > len(events):
        reasons.append("window_after_clipped")
    delivered = deepcopy(delivered_result(packet)) if include_delivered else None
    delivery_ids = []
    if delivered is not None:
        for event_id in (delivered["last_prose_event_id"],
                         (delivered["last_answer_tool_call"] or {}).get("event_id")):
            if event_id and event_id not in delivery_ids:
                delivery_ids.append(event_id)
    projection = {
        "schema_version": EVENT_WINDOW_PROJECTION_SCHEMA,
        "packet_digest": digest(packet),
        "question": packet["question"],
        "anchor_event_id": anchor_event_id,
        "anchor_position": anchor_pos,
        "target_event_id": target_event_id,
        "target_position": target_pos,
        "window_before": window_before,
        "window_after": window_after,
        "events": projected_events,
        "event_ids": projected_ids,
        "content_digest": digest(projected_events),
        "is_window_complete": not reasons,
        "coverage_reasons": reasons,
        # Whether anything *after* the anchor event carries a tool call or
        # non-empty prose. Positions are 1-based, so slicing from
        # ``anchor_pos`` (not ``anchor_pos - 1``) is exactly "after the
        # anchor"; the slice is deliberately exclusive of the anchor itself.
        "has_subsequent_activity": any(
            event.get("tool_interactions") or str(event.get("content") or "").strip()
            for event in events[anchor_pos:]
        ),
        "delivered_result": delivered,
        "delivery_event_ids": delivery_ids,
        "omitted_event_ids": omitted_ids,
        "omitted_event_count": len(omitted_ids),
    }
    projection["projection_hash"] = digest(projection)
    return projection


def evidence_refs_support_event(
    packet: Mapping[str, Any], evidence_refs: Sequence[str], event_id: str,
) -> bool:
    """Whether observed references belong to the specified canonical event."""
    owners = packet_reference_index(packet)
    owner = owners.get(event_id)
    return owner is not None and any(owners.get(ref) == owner for ref in evidence_refs)


def evidence_refs_support_step(
    packet: Mapping[str, Any], evidence_refs: Sequence[str], step: int,
) -> bool:
    event_id = event_id_for_step(packet, step)
    return bool(event_id) and evidence_refs_support_event(packet, evidence_refs, event_id)


def _answer_call(interaction: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize observed direct/top-level wrapped calls without executing code.

    Calls inside functions, conditionals, strings and comments are not evidence
    that a final-answer function ran. Keep the wrapper and raw arguments intact.
    """
    name = str(interaction.get("tool_name") or "").lower()
    if any(hint in name for hint in _ANSWER_TOOL_HINTS):
        return dict(interaction)
    if name not in {"code action space", "python", "python_repl"}:
        return None
    code = interaction.get("arguments")
    if isinstance(code, Mapping):
        code = code.get("code")
    if not isinstance(code, str) or len(code) > 50_000:
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    # Only the final top-level statement is a reliable delivery anchor.
    statement = tree.body[-1] if tree.body else None
    call = getattr(statement, "value", None) if isinstance(statement, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id in {"final_answer", "finish", "submit_answer"}):
        return {**interaction, "tool_name": call.func.id,
                "wrapper_tool_name": interaction.get("tool_name"),
                "anchor_origin": "top_level_python_call"}
    return None


def answer_tool_is_delivery(interaction: Mapping[str, Any]) -> bool:
    """Whether an answer-shaped call *delivers the answer* or reports status.

    Two contract layers, deliberately different, and now stated in one place:
    ``_answer_call`` recognises ``finish`` because agent frameworks route the
    final answer through it in some corpora -- its call is an anchor worth
    keeping. ``delivered_result`` then splits the anchors: a name carrying
    ``terminate``/``finish``/``success`` reports a *completion status*, so it
    becomes ``last_status_tool`` (used only when no real delivery exists),
    while ``final_answer``/``submit_answer``-style names deliver payload and
    become ``last_answer_tool``. The distinction is what keeps a
    ``terminate(status="success")`` call from being read as the delivered
    answer body.
    """
    name = str(interaction.get("tool_name") or "").lower()
    return bool(interaction) and not any(marker in name for marker in ("terminate", "finish", "success"))


def delivered_result(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical extraction of the delivered-result anchors.

    Prefer the last answer delivery over a closing status call. A status call
    remains an anchor when there is no answer tool. Semantic judgement belongs
    to the judge.
    """
    events = list(packet.get("events") or [])
    last_prose = None
    for event in reversed(events):
        content = event.get("content")
        if content not in (None, ""):
            last_prose = event
            break
    last_answer_tool = None
    last_status_tool = None
    for event in reversed(events):
        for interaction in reversed(event.get("tool_interactions") or []):
            answer = _answer_call(interaction)
            if answer is not None:
                anchored = {
                    **answer,
                    "event_id": event.get("event_id"),
                    "position": event.get("position"),
                    "tool_name": answer.get("tool_name"),
                    "arguments": interaction.get("arguments"),
                    "observation": interaction.get("observation"),
                }
                if answer_tool_is_delivery(answer):
                    last_answer_tool = anchored
                    break
                if last_status_tool is None:
                    last_status_tool = anchored
        if last_answer_tool is not None:
            break
    if last_answer_tool is None:
        last_answer_tool = last_status_tool
    return {
        "last_prose_event_id": (
            last_prose.get("event_id") if last_prose else None
        ),
        "last_answer_tool_call": last_answer_tool,
        "has_explicit_final_answer": last_answer_tool is not None,
        "answer_anchor_status": "recognized" if last_answer_tool is not None else (
            "unknown" if any(
                str(i.get("tool_name") or "").lower() in {"code action space", "python", "python_repl"}
                for e in events for i in e.get("tool_interactions") or []
            ) else "absent"
        ),
    }


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _render_event(
    event: Mapping[str, Any],
    *,
    event_cap: int,
    tool_cap: int,
) -> tuple[str, bool]:
    lines = [
        f"EVENT {event['event_id']} (position={event['position']}, "
        f"role={event.get('role') or '?'})",
    ]
    truncated = False
    content = event.get("content")
    if content not in (None, ""):
        text = str(content)
        if len(text) > event_cap:
            text = text[:event_cap] + "…[truncated]"
            truncated = True
        lines.append(text)
    for interaction in event.get("tool_interactions") or []:
        args = interaction.get("arguments")
        args_text = _truncate(args, tool_cap) if args not in (None, "") else "{}"
        if str(args or "") and len(str(args)) > tool_cap:
            truncated = True
        obs = interaction.get("observation")
        obs_text = _truncate(obs, tool_cap) if obs not in (None, "") else "(none)"
        if str(obs or "") and len(str(obs)) > tool_cap:
            truncated = True
        lines.append(
            f"  TOOL {interaction['interaction_id']} "
            f"name={interaction.get('tool_name')} "
            f"arguments={args_text}\n  -> observation: {obs_text}"
        )
    return "\n".join(lines), truncated


def _view_meta() -> dict[str, Any]:
    return {
        "rendered_event_ids": [],
        "truncated_event_ids": [],
        "packet_truncated": False,
    }


def render_full_view(packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Full-fidelity view for the judge."""
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    budget = _JUDGE_VIEW_CHARS
    for event in packet.get("events") or []:
        chunk, truncated = _render_event(
            event, event_cap=_JUDGE_EVENT_CHARS, tool_cap=_JUDGE_TOOL_CHARS,
        )
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    return "\n".join(lines), meta


def render_index_tail_view(
    packet: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Degraded judge fallback: complete index + chronological tail."""
    events = list(packet.get("events") or [])
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    lines.append("\nEVENT INDEX:")
    budget = _INDEX_VIEW_CHARS
    for event in events:
        content = event.get("content")
        content_text = str(content) if content not in (None, "") else ""
        tools = []
        for interaction in event.get("tool_interactions") or []:
            args = interaction.get("arguments")
            args_preview = (
                _truncate(args, _INDEX_TOOL_PREVIEW)
                if args not in (None, "") else "{}"
            )
            obs = interaction.get("observation")
            obs_len = len(str(obs)) if obs not in (None, "") else 0
            tools.append(
                f"tool={interaction.get('tool_name')} args={args_preview} "
                f"obs_chars={obs_len}"
            )
        line = (
            f"{event['event_id']} (position={event['position']}, "
            f"role={event.get('role') or '?'}, content_chars={len(content_text)}, "
            f"preview={_truncate(content_text, _INDEX_EVENT_PREVIEW)!r}) "
            f"tools=[{'; '.join(tools)}]"
        )
        if len(line) > budget:
            meta["packet_truncated"] = True
            break
        lines.append(line)
        budget -= len(line)
    lines.append("\nTAIL EVENTS (full detail, chronological end):")
    for event in events[-_INDEX_TAIL_EVENTS:]:
        chunk, truncated = _render_event(
            event, event_cap=_JUDGE_EVENT_CHARS, tool_cap=_JUDGE_TOOL_CHARS,
        )
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    return "\n".join(lines), meta


def _sparse_excerpt(value: Any, limit: int) -> tuple[str, bool]:
    text = str(value) if value is not None else ""
    if len(text) <= limit:
        return text, False
    if limit < 96:
        return text[:max(0, limit - 20)] + " [excerpt omitted]", True
    width = max(12, (limit - 80) // 4)
    starts = {0, max(0, len(text) - width)}
    for match in re.finditer(r"(?im)\b(?:error|exception|traceback|failed|source_ref|result|answer)\b", text):
        starts.add(max(0, match.start() - width // 3))
        if len(starts) >= 4:
            break
    pieces = [text[start:start + width] for start in sorted(starts)]
    return "\n[excerpt gap]\n".join(pieces), True


def render_sparse_view(packet: Mapping[str, Any], *, max_characters: int = 48000) -> tuple[str, dict[str, Any]]:
    """Chronological channel excerpts retain coordinates and disclose every omission."""
    if isinstance(max_characters, bool) or not isinstance(max_characters, int) or max_characters < 1024:
        raise ValueError("sparse view budget must be an integer >=1024")
    events = list(packet["events"])
    question, task_clipped = _sparse_excerpt(packet["question"], max_characters // 3)
    lines = ["SPARSE TRAJECTORY: excerpts are incomplete; gaps are not evidence of absence.",
             "QUESTION: " + question]
    meta = _view_meta()
    meta.update(degraded=True, projection_kind="sparse_channels", task_truncated=task_clipped,
                packet_digest=digest(packet), omitted_event_ids=[], max_characters=max_characters)
    remaining = max_characters - len("\n".join(lines))
    channel_count = sum(1 + 2 * len(event["tool_interactions"]) for event in events)
    cap = max(32, (remaining - 160 * channel_count) // max(1, channel_count))
    for event in events:
        chunks = [f"EVENT {event['event_id']} (position={event['position']}, role={event.get('role') or '?'})"]
        clipped = False
        content, cut = _sparse_excerpt(event.get("content"), cap)
        clipped |= cut
        if content:
            chunks.append(content)
        for item in event["tool_interactions"]:
            arguments, args_cut = _sparse_excerpt(item.get("arguments"), cap)
            observation, obs_cut = _sparse_excerpt(item.get("observation"), cap)
            clipped |= args_cut or obs_cut
            chunks.append(f"TOOL {item['interaction_id']} name={item['tool_name']} "
                          f"arguments={arguments}\nobservation={observation}")
        chunk = "\n".join(chunks)
        if len(chunk) + 1 > remaining:
            meta["omitted_event_ids"].extend(e["event_id"] for e in events if e["position"] >= event["position"])
            meta["packet_truncated"] = True
            break
        lines.append(chunk)
        remaining -= len(chunk) + 1
        meta["rendered_event_ids"].append(event["event_id"])
        if clipped:
            meta["truncated_event_ids"].append(event["event_id"])
    meta["packet_truncated"] |= task_clipped or bool(meta["truncated_event_ids"])
    text = "\n".join(lines)
    meta["rendered_characters"] = len(text)
    return text, meta


def render_judge_view(packet: Mapping[str, Any], *, max_characters: int = 48000) -> tuple[str, dict[str, Any]]:
    """Use complete short trajectories and bounded sparse projections for long inputs."""
    text, meta = render_full_view(packet)
    if not meta["packet_truncated"] and not meta["truncated_event_ids"] and len(text) <= max_characters and len(packet["question"]) <= 4000:
        meta["degraded"] = False
        return text, meta
    degraded_text, degraded_meta = render_sparse_view(packet, max_characters=max_characters)
    degraded_meta["full_render_chars"] = len(text)
    return degraded_text, degraded_meta


def _render_delivered_block(packet: Mapping[str, Any]) -> str:
    result = delivered_result(packet)
    lines = ["\nDELIVERED RESULT ANCHORS:"]
    lines.append(f"last_prose_event_id={result['last_prose_event_id']}")
    lines.append(f"has_explicit_final_answer={result['has_explicit_final_answer']}")
    tool = result["last_answer_tool_call"]
    if tool:
        lines.append(
            f"last_answer_tool_call={tool['event_id']} name={tool['tool_name']} "
            f"arguments={_truncate(tool.get('arguments'), 20000)}"
        )
        lines.append(
            f"last_answer_observation={_truncate(tool.get('observation'), 20000)}"
        )
    return "\n".join(lines)


def render_defense_view(
    packet: Mapping[str, Any],
    step: int,
    *,
    window: int = _DEFENSE_WINDOW,
    evidence_refs: Sequence[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Defense view: alleged step + cited evidence + neighbours + anchors."""
    events = list(packet.get("events") or [])
    cited = [event for event in events if event.get("position") == step]
    cited_ids = {str(event.get("event_id") or "") for event in cited}
    ref_ids = {
        str(ref).rsplit("_tool_", 1)[0]
        for ref in (evidence_refs or ())
        if str(ref)
    }
    cited_ids.update(
        event_id for event_id in ref_ids
        if any(e.get("event_id") == event_id for e in events)
    )
    expanded = set(cited_ids)
    for event in cited:
        pos = int(event.get("position") or 0)
        expanded.update(
            str(other.get("event_id") or "") for other in events
            if abs(int(other.get("position") or 0) - pos) <= window
        )
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    budget = _DEFENSE_VIEW_CHARS
    for event in events:
        event_id = str(event.get("event_id") or "")
        if event_id not in expanded:
            continue
        tag = "CITED" if event_id in cited_ids else "WINDOW"
        chunk, truncated = _render_event(
            event, event_cap=_DEFENSE_EVENT_CHARS, tool_cap=_DEFENSE_EVENT_CHARS,
        )
        if tag == "CITED" and truncated:
            meta["packet_truncated"] = True
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(f"[{tag}] " + chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    lines.append(_render_delivered_block(packet))
    omitted = [
        str(event.get("event_id") or "") for event in events
        if str(event.get("event_id") or "") not in expanded
    ]
    if omitted:
        shown = omitted[:50]
        suffix = f" …(+{len(omitted) - len(shown)})" if len(omitted) > 50 else ""
        lines.append(
            "\nEVENTS OMITTED FROM THIS VIEW (present in trajectory, not "
            "shown here): " + ", ".join(shown) + suffix
        )
    meta["omitted_event_ids"] = omitted
    return "\n".join(lines), meta


def packet_features(packet: Mapping[str, Any]) -> set[str]:
    """Closed vocabulary of observable features for intervention routing."""
    events = list(packet.get("events") or [])
    question = str(packet.get("question") or "")
    multi_turn = "[turn 2]" in question
    tool_count = 0
    has_retrieval = False
    has_answer_tool = False
    has_numeric = False
    max_content_chars = 0
    for event in events:
        content = str(event.get("content") or "")
        max_content_chars = max(max_content_chars, len(content))
        if any(char.isdigit() for char in content):
            has_numeric = True
        for interaction in event.get("tool_interactions") or []:
            tool_count += 1
            name = str(interaction.get("tool_name") or "").lower()
            if any(word in name for word in ("search", "retriev", "fetch", "browse", "lookup")):
                has_retrieval = True
            if _answer_call(interaction) is not None:
                has_answer_tool = True
    features: set[str] = set()
    if tool_count > 0:
        features.add("has_tool_calls")
    if has_retrieval:
        features.add("has_retrieval_tool")
    if has_answer_tool:
        features.add("has_answer_tool")
    if has_numeric:
        features.add("has_numeric_content")
    if max_content_chars > 400:
        features.add("has_long_prose")
    if multi_turn:
        features.add("multi_turn_question")
    if any(char.isdigit() for char in question):
        features.add("question_has_numbers")
    return features


__all__ = [
    "ALLOWED_RECORD_KEYS",
    "EVALUATOR_ONLY_FIELDS",
    "EVENT_WINDOW_PROJECTION_SCHEMA",
    "PACKET_FEATURE_NAMES",
    "PACKET_SCHEMA",
    "PacketError",
    "STEP_KIND_ACTION",
    "STEP_KIND_CONTENT",
    "STEP_KIND_EMPTY",
    "build_packet",
    "delivered_result",
    "event_id_for_step",
    "evidence_refs_support_event",
    "evidence_refs_support_step",
    "packet_features",
    "packet_reference_index",
    "packet_reference_error",
    "project_event_window",
    "render_defense_view",
    "render_full_view",
    "render_judge_view",
    "step_kind",
    "validate_packet",
    "validate_record",
]
