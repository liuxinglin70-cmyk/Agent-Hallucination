"""Task-convention overlay: closed vocabulary routed by observable features.

The court's system prompts carry only the universal hallucination law
(compare against tool observations, user statements and the task). Every
dataset-specific judgement convention — whether a tool-argument formatting
difference convicts, whether a restated observation must match exactly —
lives here as a code constant and is routed per case by MECHANICAL packet
features, never by dataset identity. Routing is label-free, deterministic
and lands in the Detection Truth for audit.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

TASK_VALUE_SIGNAL = re.compile(
    r"'[A-Za-z0-9_\-]{1,24}'"          # quoted literal token ('RMS')
    r'|\b\d+(?:\.\d+)?\b',             # numeric literal
)
_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?")
_RETRIEVAL_TOOL = re.compile(
    r"search|retriev|fetch|query|lookup|browse|read|get_", re.IGNORECASE,
)
_CONTROL_TOOL = re.compile(
    r"brake|headlight|engine|door|window|climate|lock|light|wiper|"
    r"throttle|steer|gear|accel|park", re.IGNORECASE,
)
_STATUS_FIELD = re.compile(
    r'"(?:status|success|confirmed|completed|ok|done|is_\w+|has_\w+|'
    r'\w*_status)"\s*:\s*(?:true|false)', re.IGNORECASE,
)

# Closed vocabulary of mechanically decidable packet features. Route
# conditions and evolved-convention triggers both draw from this source.
PACKET_FEATURES: dict[str, str] = {
    "task_specifies_values": (
        "the task text contains at least one quoted literal token or "
        "numeric literal that names a specific value the work must use"
    ),
    "tool_calls_with_arguments": (
        "the trajectory contains a tool call carrying non-empty arguments"
    ),
    "numeric_observations": (
        "at least one tool observation contains a numeric literal"
    ),
    "numeric_argument_restatement": (
        "a numeric literal appears both in a tool observation and in a "
        "strictly later TOOL ARGUMENT"
    ),
    "numeric_report_restatement": (
        "a numeric literal appears both in a tool observation and in a "
        "strictly later assistant PROSE message"
    ),
    "retrieval_tools": (
        "at least one tool name matches the retrieval-tool pattern"
    ),
    "final_prose_report": (
        "the last assistant event carries prose content beyond 400 chars"
    ),
    "multi_tool_sequence": (
        "the trajectory contains three or more tool calls"
    ),
    "structured_status_observations": (
        "at least one tool observation is a structured status object "
        "(boolean or status fields such as success/confirmed/booking_status)"
    ),
    "vehicle_or_device_controls": (
        "at least one tool name matches the vehicle/device-control pattern "
        "(brake, headlight, engine, door, window, climate, lock)"
    ),
}

CONVENTIONS: dict[str, dict[str, Any]] = {
    "tool_argument_exact_match": {
        "requires": ("task_specifies_values", "tool_calls_with_arguments"),
        "directive": (
            "CONVENTION tool_argument_exact_match: a tool argument the task "
            "itself fixed is an exact interface — the call must pass THAT "
            "value in THAT form. An argument differing from the "
            "task-specified value, even only in formatting (e.g. '2.0000' "
            "written where the task fixes '2.0', or a wrong code where the "
            "task names the right one), is a deviation. Applies ONLY to "
            "arguments the task text fixed; an argument value the task "
            "left to the agent's choice is judged by substance, not form."
        ),
    },
    "observation_restatement_fidelity": {
        "requires": (
            "numeric_observations", "numeric_argument_restatement",
        ),
        "directive": (
            "CONVENTION observation_restatement_fidelity: a later TOOL "
            "ARGUMENT restating a value a tool observation produced must "
            "match that observation exactly — the tool call already "
            "applied the task's transformation, so its observation is the "
            "authoritative value. An observed '2.0' passed later as "
            "'2.0000' is a misreported tool result. This exactness is "
            "scoped to TOOL ARGUMENTS only; it never applies to prose."
        ),
    },
    "report_presentation_tolerance": {
        "requires": (
            "numeric_observations", "numeric_report_restatement",
        ),
        "directive": (
            "CONVENTION report_presentation_tolerance: a PROSE report "
            "restating an observed numeric value is judged by substance, "
            "not form. Adding a natural unit or currency mark, dropping "
            "insignificant trailing zeros, or an equivalent formatting of "
            "the same value ('780.0' written as '780 kilometers', '10.5' "
            "as '10.5 gallons', '728.0' as '$728.00') is normal numerical "
            "presentation, not a deviation. It is a deviation only when "
            "the reported value's MAGNITUDE or meaning differs from the "
            "observation. This tolerance never applies to tool arguments."
        ),
    },
}


def _has_quoted_or_numeric(text: Any) -> bool:
    return bool(TASK_VALUE_SIGNAL.search(str(text or "")))


def packet_features(packet: Mapping[str, Any]) -> set[str]:
    """Decide every closed-vocabulary feature for one packet."""
    events = list(packet.get("events") or [])
    question = packet.get("question")
    # (event position, numeric value) produced by an observation vs. any
    # numeric the agent wrote later — arguments or prose, distinguished.
    observed: list[tuple[int, float]] = []
    argument_restated: list[tuple[int, float]] = []
    report_restated: list[tuple[int, float]] = []
    tool_call_count = 0
    has_args = False
    retrieval = False
    controls = False
    status_observation = False
    for event in events:
        position = int(event.get("position") or 0)
        for interaction in event.get("tool_interactions") or []:
            tool_call_count += 1
            args = interaction.get("arguments")
            if args not in (None, "", {}):
                has_args = True
            tool_name = str(interaction.get("tool_name") or "")
            if _RETRIEVAL_TOOL.search(tool_name):
                retrieval = True
            if _CONTROL_TOOL.search(tool_name):
                controls = True
            observation = str(interaction.get("observation") or "")
            if _STATUS_FIELD.search(observation):
                status_observation = True
            for match in _NUMERIC.finditer(observation):
                try:
                    observed.append((position, float(match.group(0))))
                except ValueError:
                    continue
            for match in _NUMERIC.finditer(str(args or "")):
                try:
                    argument_restated.append((position, float(match.group(0))))
                except ValueError:
                    continue
        for match in _NUMERIC.finditer(str(event.get("content") or "")):
            try:
                report_restated.append((position, float(match.group(0))))
            except ValueError:
                continue
    argument_restatement = any(
        restated_value == observed_value
        and restated_position > observed_position
        for restated_position, restated_value in argument_restated
        for observed_position, observed_value in observed
    )
    report_restatement = any(
        restated_value == observed_value
        and restated_position > observed_position
        for restated_position, restated_value in report_restated
        for observed_position, observed_value in observed
    )
    last_content = max(
        (
            len(str(event.get("content") or ""))
            for event in events
            if str(event.get("role") or "") != "tool"
        ),
        default=0,
    )
    features = {
        "task_specifies_values": _has_quoted_or_numeric(question),
        "tool_calls_with_arguments": has_args,
        "numeric_observations": bool(observed),
        "numeric_argument_restatement": argument_restatement,
        "numeric_report_restatement": report_restatement,
        "retrieval_tools": retrieval,
        "final_prose_report": last_content > 400,
        "multi_tool_sequence": tool_call_count >= 3,
        "structured_status_observations": status_observation,
        "vehicle_or_device_controls": controls,
    }
    return {name for name, present in features.items() if present}


def route_conventions(features: Mapping[str, Any]) -> list[str]:
    """Convention ids whose required features are all present."""
    routed = []
    for convention_id, spec in CONVENTIONS.items():
        if all(feature in features for feature in spec["requires"]):
            routed.append(convention_id)
    return routed


def render_convention_block(convention_ids: Sequence[str]) -> str:
    """The prompt block appended to every stage when conventions route in."""
    directives = [
        CONVENTIONS[cid]["directive"]
        for cid in convention_ids
        if cid in CONVENTIONS
    ]
    if not directives:
        return ""
    return (
        "\nJUDGEMENT CONVENTIONS routed for THIS task (apply exactly as "
        "stated; they override generic tolerance intuitions inside their "
        "scope):\n" + "\n".join(f"- {text}" for text in directives)
    )


def render_evolved_block(
    evolved: Sequence[Mapping[str, Any]],
    features: Mapping[str, Any],
) -> str:
    """The routed prompt block for one evolved-convention genome entry.

    Each entry carries a dual boundary: the directive (when the behaviour
    IS a deviation) and the exclusion (when it is NOT). An entry without
    an exclusion never routes — one-sided amplifiers measurably overflow
    onto clean cases.
    """
    blocks = []
    for entry in evolved:
        trigger = {
            str(name) for name in entry.get("trigger_features") or ()
        }
        if not trigger or not trigger <= set(features):
            continue
        directive = str(entry.get("directive") or "")
        exclusion = str(entry.get("exclusion") or "")
        if not directive or not exclusion:
            continue
        blocks.append(f"- {directive} NOT WHEN {exclusion}")
    if not blocks:
        return ""
    return (
        "\nEVOLVED JUDGEMENT CONVENTIONS routed for THIS task (apply "
        "exactly as stated, both sides of each boundary):\n"
        + "\n".join(blocks)
    )
