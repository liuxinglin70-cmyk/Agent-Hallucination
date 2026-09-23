"""Deterministic predicates for detector intervention hooks.

These are the M1 predicates from the BEHAVIOR self-evolution design. They are
deliberately mechanical: no LLM call, no case ids, no hallucination verdicts.
An ``evidence_check`` intervention may only append entries to ``proof.evidence``;
these predicates are the only executable evidence checkers until a new runner
is registered here.

Every predicate returns ``{"predicate", "verdict", "detail"}`` where ``verdict``
is from the predicate's own closed vocabulary.

A verdict is the predicate's *opinion*, and three of these opinions can be
convicting (``PREDICATE_CONVICTION_VERDICTS``): a ``decision_guard`` may bind
one and convict without the judge, the recall net or the defense hearing. That
authority is not free -- it is granted only while a calibration record shows the
predicate has zero false positives on the labelled gold-clean corpus
(``detector.conviction_calibration``). The extraction rules below are therefore
written to state what they can actually establish:

* ``required_action_present`` reads an obligation only when the sentence names an
  explicit tool-semantics object (tool / function / api / command / endpoint)
  and the name in front of it is identifier-shaped. "Use the complete name with
  article if any" is an answer-format instruction, not a tool requirement, and
  "collaborate" is not a tool.
* A movement requirement is a *destination* ("swing by the nearest tire shop"),
  not an infinitive directive ("proceed to close the ticket"), and it is
  satisfied when some tool call refers to the target -- not only when a tool
  whose name contains a movement verb does.
* A pending engine start is the trajectory *ending* on a request for
  permission, in the agent's own unquoted words.
* ``final_delivery_present`` reads a delivery channel as any of: the last prose
  event, an answer-like tool call, or a content-bearing tool observation. A
  terminal success whose observations carry the answer is a delivery on this
  corpus's agent frameworks, so the absence of prose alone establishes nothing.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from detector.packet import delivered_result as packet_delivered_result

PREDICATE_VERDICTS: dict[str, frozenset[str]] = {
    "required_action_present": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "verbatim_fidelity": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "evidence_refs_resolve": frozenset({
        "resolved", "unresolved", "not_applicable",
    }),
    "contract_coverage": frozenset({
        "covered", "uncovered", "not_applicable",
    }),
    "delivered_anchor_consistency": frozenset({
        "consistent", "inconsistent", "insufficient",
    }),
    "final_delivery_present": frozenset({
        "present", "missing", "not_applicable",
    }),
}

PREDICATES = frozenset(PREDICATE_VERDICTS)

# Only these predicate outcomes prove a task-level mechanical violation. Other
# negative-looking outcomes diagnose evidence quality or judge coverage and may
# guide proposals, but they cannot authorize a terminal conviction.
#
# This table says which *verdicts* are convicting, not that the predicates are
# entitled to convict: that is decided by the calibration guard
# (``detector.conviction_calibration``), which refuses the authority outright
# while a predicate's measured clean-set false-positive count is not zero.
PREDICATE_CONVICTION_VERDICTS: dict[str, frozenset[str]] = {
    "required_action_present": frozenset({"violated"}),
    "verbatim_fidelity": frozenset({"violated"}),
    "final_delivery_present": frozenset({"missing"}),
}

_ACTION_MARKERS = (
    "must use", "required to use", "must call", "must invoke",
    "required to call", "should use", "has to use", "shall use",
    "use the", "call the", "run the", "query the", "execute the",
)

#: What makes an obligation an *action* obligation: the sentence names the kind
#: of thing to act on. Without this, "use the" inside "Use the complete name
#: with article if any" (an answer-format instruction) parses as a requirement
#: to use something called "complete name".
_TOOL_OBJECT_RE = re.compile(r"\b(tool|function|api|command|endpoint)\b", re.I)

#: The name in front of the tool word, bounded so a sentence fragment cannot
#: become a "tool name". ``_LEAD_IN_RE`` drops the article: "the search tool"
#: names ``search``.
_NAME_SHAPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z0-9_]+){0,2}")
_LEAD_IN_RE = re.compile(r"^(?:the|a|an)\s+", re.I)
_TOOL_OBJECT_WINDOW = 40

_VERBATIM_MARKERS = (
    "verbatim", "exactly", "exact text", "exact phrase", "literal",
    "word-for-word", "word for word", "must say", "must write",
    "must output", "must return", "must reply", "has to say",
    "required to say", "required to write",
)

_QUOTE_RE = re.compile(r"[\"'\u201c]([^\"'\u201d\n]{1,400})[\"'\u201d]")

#: Spans the agent is quoting rather than saying. A quoted "Should I take any
#: action?" inside a posted comment is the agent's content, not the agent
#: asking the user for permission.
_QUOTED_SPAN_RE = re.compile(r"\"[^\"]*\"|\u201c[^\u201d]*\u201d|'[^']*'")

_EXACT_VALUE_RE = re.compile(
    r"\b(?:exactly|exact value|must equal|must be|equals?)\s+"
    r"([+-]?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

#: Phrases that ask the *user* to decide. Read only from the agent's own words.
_PERMISSION_PHRASES = (
    "would you like me to", "should i", "do you want me to",
    "shall i", "may i",
)

#: Tools whose name reads as moving to a place. The satisfaction test no longer
#: requires one of these -- a destination may be served by a tool named for the
#: destination (``find_nearest_tire_shop``) -- but the vocabulary still
#: documents which names this module regards as movement.
_MOVEMENT_TOOLS = frozenset({
    "cd", "chdir", "navigate", "goto", "go", "travel", "drive",
    "move", "visit", "head", "swing", "walk",
})

_STOPWORDS = frozenset({"the", "a", "an", "to", "and", "of", "in", "on"})

#: The runtime wraps every tool observation in this envelope; the completion
#: banner is fixed text every terminating call carries. Neither is delivery.
_OBSERVATION_ENVELOPE_RE = re.compile(
    r"Observed output of cmd `[^`]*` executed:"
)
_COMPLETION_BANNER_RE = re.compile(
    r"The interaction has been completed with status: success"
)

#: How much text a tool observation must still carry, envelope and banner
#: removed, to count as a delivery channel.
_MIN_DELIVERY_CHARS = 20


def _delivery_payload(observation: Any) -> str:
    """The observation with its fixed runtime frame removed."""
    text = _OBSERVATION_ENVELOPE_RE.sub("", str(observation or ""))
    return _COMPLETION_BANNER_RE.sub("", text).strip()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _flatten_question(value: Any, parts: list[str]) -> None:
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, Mapping):
        parts.append(str(value.get("content") or value.get("text") or ""))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_question(item, parts)


def _question_text(packet: Mapping[str, Any]) -> str:
    question = packet.get("question")
    if isinstance(question, str):
        return question
    if isinstance(question, list):
        parts: list[str] = []
        _flatten_question(question, parts)
        return "\n".join(parts)
    return str(question or "")


def _event_texts(packet: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in packet.get("events") or []:
        chunks = [str(event.get("content") or "")]
        for interaction in event.get("tool_interactions") or []:
            chunks.extend((
                str(interaction.get("tool_name") or ""),
                str(interaction.get("arguments") or ""),
                str(interaction.get("observation") or ""),
            ))
        rows.append({
            "event_id": str(event.get("event_id") or ""),
            "text": "\n".join(chunks),
        })
    return rows


def _tool_names(packet: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for event in packet.get("events") or []:
        for interaction in event.get("tool_interactions") or []:
            name = str(interaction.get("tool_name") or "").lower()
            if name:
                names.add(name)
    return names


def _detect_required_actions(question: str) -> set[str]:
    """Extraction of explicitly required tool actions, under a double condition.

    A marker alone does not state a tool obligation, so two things must hold:

    1. the text right after the marker names a tool-semantics object (``tool``,
       ``function``, ``api``, ``command``, ``endpoint``), and
    2. the phrase in front of that object is identifier-shaped and short.

    "Use the complete name with article if any" fails both: there is no tool
    word, and ``complete name with article if any`` is a sentence, not a name.
    """
    actions: set[str] = set()
    lowered = question.lower()
    for marker in _ACTION_MARKERS:
        start = 0
        while True:
            pos = lowered.find(marker, start)
            if pos < 0:
                break
            start = pos + max(1, len(marker))
            tail = question[pos + len(marker): pos + len(marker) + 80]
            obj = _TOOL_OBJECT_RE.search(tail)
            if obj is None or obj.start() > _TOOL_OBJECT_WINDOW:
                continue
            phrase = _LEAD_IN_RE.sub("", tail[:obj.start()].strip(" \t:-"))
            shape = _NAME_SHAPE_RE.match(phrase)
            if shape is None:
                continue
            name = " ".join(shape.group(0).split()).strip(" .-").lower()
            if not name or name in {"tool", "function", "api", "command", "endpoint"}:
                continue
            if any(ch in name for ch in ("?", "!", ";")):
                continue
            actions.add(name)
    # Explicit structured spelling in natural-language questions.
    for match in re.finditer(
        r"required_action\s*[:=]\s*[\"']([^\"']+)[\"']", question, re.I,
    ):
        actions.add(match.group(1).strip().lower())
    return {name for name in actions if name}



def _detect_verbatim_phrases(question: str) -> list[str]:
    phrases: list[str] = []
    for match in _QUOTE_RE.finditer(question):
        prefix = question[max(0, match.start() - 90): match.start()].lower()
        if any(marker in prefix for marker in _VERBATIM_MARKERS):
            phrase = _clean(match.group(1))
            if len(phrase) >= 2:
                phrases.append(phrase)
    return phrases


def _detect_contract_kinds(question: str) -> set[str]:
    kinds: set[str] = set()
    if _detect_required_actions(question):
        kinds.add("required_action")
    if _detect_verbatim_phrases(question):
        kinds.add("verbatim_content")
    if _EXACT_VALUE_RE.search(question):
        kinds.add("exact_value")
    lowered = question.lower()
    if any(
        marker in lowered
        for marker in ("json format", "in the format", "exact form", "as yaml")
    ):
        kinds.add("exact_form")
    return kinds


def _delivered_texts(packet: Mapping[str, Any]) -> list[str]:
    anchors = packet_delivered_result(packet)
    rows: list[str] = []
    texts = _event_texts(packet)
    for row in texts:
        if row["event_id"] == anchors.get("last_prose_event_id"):
            rows.append(row["text"])
    tool = anchors.get("last_answer_tool_call") or {}
    if tool:
        rows.append(str(tool.get("arguments") or ""))
        rows.append(str(tool.get("observation") or ""))
    return [_clean(row) for row in rows if _clean(row)]


#: Markers that introduce a *destination*. ``proceed to`` is deliberately not
#: one: it takes an infinitive verb phrase ("proceed to close the ticket",
#: "proceed to display only the last 10 lines", "proceed to start the engine"),
#: so reading it as a movement marker invents a place to travel to out of an
#: instruction to act. Measured on the corpus: keeping it adds exactly one
#: error-set hit, whose extracted target ("juxtapose it") is not a place, and
#: no clean-set hit -- it is a marker whose matches are all misreads.
_DESTINATION_MARKERS = (
    "swing by", "head over to", "head to", "drive to", "walk to", "stop by",
    "drop by", "travel to", "go to", "visit",
)

_DESTINATION_RE = re.compile(
    r"(?:" + "|".join(re.escape(marker) for marker in _DESTINATION_MARKERS)
    + r")\s+(?:the\s+)?",
    re.IGNORECASE,
)

_DESTINATION_TAIL_RE = re.compile(
    r"([A-Za-z0-9_ .'-]+?)"
    # ``using`` / ``via`` close a destination the way a conjunction does:
    # "go to Fire Station 301 DCA ARFF using the metro" names the station, not
    # the station *and its transport*.
    r"(?=\s+(?:and|or|to|for|before|while|with|when|using|via|,|\.)|[.?]|$)"
)

#: A clause that starts with one of these does not name a place; it continues
#: the sentence ("...visit and need to know the travel distance").
_DESTINATION_STOPWORDS = frozenset({
    "and", "or", "but", "then", "also", "so", "because", "if", "when", "while",
    "as", "that", "to", "for", "with", "without", "in", "on", "at", "by",
    "from", "need", "needs", "visit", "yet", "nor", "than", "which", "who",
})

#: A destination inside a question or a hypothetical clause is not an
#: instruction: "perhaps we could swing by the nearest tire shop?" asks whether
#: to act, it does not require it.
_HYPOTHETICAL_RE = re.compile(r"\b(?:if|whether|perhaps|maybe|in case|unless)\b", re.I)

_MIN_DESTINATION_CHARS = 6


def _clause_at(text: str, position: int) -> str:
    """The sentence containing ``position``, approximated by hard punctuation."""
    start = max(
        text.rfind(".", 0, position), text.rfind("?", 0, position),
        text.rfind("!", 0, position), text.rfind("\n", 0, position),
    ) + 1
    end = len(text)
    for stop in ".?!\n":
        found = text.find(stop, position)
        if found >= 0:
            end = min(end, found + 1)
    return text[start:end]


def _movement_requirements(texts: Iterable[str]) -> list[str]:
    """Destinations the task asks the agent to travel to.

    Three conditions, each aimed at one misread: a destination is long enough to
    be a place, does not start with a function word ("...plan a visit and need
    to know" names no place), and does not sit in an interrogative or
    hypothetical clause.
    """
    found: list[str] = []
    for text in texts:
        for match in _DESTINATION_RE.finditer(text):
            tail = _DESTINATION_TAIL_RE.match(text[match.end(): match.end() + 80])
            if tail is None:
                continue
            target = " ".join(tail.group(1).split())
            if len(target) < _MIN_DESTINATION_CHARS:
                continue
            if target.split()[0].lower().strip(".-'") in _DESTINATION_STOPWORDS:
                continue
            clause = _clause_at(text, match.start())
            if "?" in clause or _HYPOTHETICAL_RE.search(clause):
                continue
            if target.lower() not in found:
                found.append(target.lower())
    return found


def _unquoted(text: str) -> str:
    """The agent's own words, with quoted spans removed."""
    return _QUOTED_SPAN_RE.sub(" ", str(text or ""))


def _permission_pending(packet: Mapping[str, Any]) -> bool:
    """Whether the trajectory *ends* waiting for the user's permission.

    Two conditions, both needed. The ask has to be in the last event -- an
    agent that asked permission mid-trajectory and then acted is not pending --
    and that event must carry no tool call, so a "Would you like me to ...?"
    quoted inside a posted message is not read as the agent asking.
    """
    events = list(packet.get("events") or [])
    if not events:
        return False
    last = events[-1]
    if last.get("tool_interactions"):
        return False
    text = _unquoted(last.get("content")).lower()
    if "?" not in text:
        return False
    return any(phrase in text for phrase in _PERMISSION_PHRASES)


def predicate_required_action_present(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    question = _question_text(packet)
    required = _detect_required_actions(question)
    names = _tool_names(packet)
    missing = sorted(
        action for action in required
        if not any(action in name for name in names)
    )

    # Conservative completion signals from explicit task contracts. These are
    # deliberately narrow: only movement actions and a pending-permission
    # engine-start are considered mechanically checkable.
    contracts = [
        str(item.get("requirement") or "")
        for item in context.get("contracts") or ()
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "required_action"
    ]
    contract_texts = [question, *contracts]
    movements = _movement_requirements(contract_texts)
    movement_violated = []
    for target in movements:
        tokens = {
            token for token in target.split()
            if token not in _STOPWORDS
        }
        # A destination is satisfied when some tool call refers to it. The test
        # used to require the *tool name* to contain a movement verb, which
        # encodes "travelling is done by a navigation tool": a destination
        # served by ``find_nearest_tire_shop`` was read as never reached.
        satisfied = False
        for event in packet.get("events") or []:
            for interaction in event.get("tool_interactions") or []:
                name = str(interaction.get("tool_name") or "").lower()
                args = str(interaction.get("arguments") or "").lower()
                haystack = f"{name} {args}"
                if tokens and any(token in haystack for token in tokens):
                    satisfied = True
        if not satisfied:
            movement_violated.append(target)

    start_pending = False
    engine_requested = any(
        "start" in text.lower() and (
            "engine" in text.lower() or "ignition" in text.lower()
        )
        for text in contract_texts
    )
    if engine_requested and _permission_pending(packet):
        start_pending = True

    if movement_violated:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": f"movement action(s) never completed: {movement_violated}",
        }
    if start_pending:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": "engine start was requested but trajectory ends asking permission",
        }
    if missing:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": f"required actions missing from tool calls: {missing}",
        }
    if not required and contracts:
        return {
            "predicate": "required_action_present",
            "verdict": "not_applicable",
            "detail": "required_action contract is not covered by a mechanical parser",
        }
    if not required and not contracts:
        return {
            "predicate": "required_action_present",
            "verdict": "not_applicable",
            "detail": "no explicit required_action found in question or contracts",
        }
    return {
        "predicate": "required_action_present",
        "verdict": "satisfied",
        "detail": "explicit required actions are present or completed",
    }


def predicate_verbatim_fidelity(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    phrases = _detect_verbatim_phrases(_question_text(packet))
    if not phrases:
        return {
            "predicate": "verbatim_fidelity",
            "verdict": "not_applicable",
            "detail": "no explicit verbatim_content contract found",
        }
    delivered = " ".join(_delivered_texts(packet))
    missing = [phrase for phrase in phrases if phrase not in delivered]
    if not missing:
        return {
            "predicate": "verbatim_fidelity",
            "verdict": "satisfied",
            "detail": f"all {len(phrases)} verbatim phrases delivered literally",
        }
    return {
        "predicate": "verbatim_fidelity",
        "verdict": "violated",
        "detail": f"verbatim phrase(s) missing from delivered text: {missing[:3]}",
    }


def _packet_ids(packet: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for event in packet.get("events") or []:
        ids.add(str(event.get("event_id") or ""))
        for interaction in event.get("tool_interactions") or []:
            ids.add(str(interaction.get("interaction_id") or ""))
    return ids


def predicate_evidence_refs_resolve(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    refs = context.get("evidence_refs") or []
    refs = [str(ref) for ref in refs if str(ref)]
    if not refs:
        return {
            "predicate": "evidence_refs_resolve",
            "verdict": "not_applicable",
            "detail": "no evidence refs supplied",
        }
    ids = _packet_ids(packet)
    unknown = [ref for ref in refs if ref not in ids]
    if unknown:
        return {
            "predicate": "evidence_refs_resolve",
            "verdict": "unresolved",
            "detail": f"evidence refs not in packet: {unknown[:5]}",
        }
    quote = _clean(context.get("quote") or context.get("root_cause_quote") or "")
    if quote:
        texts = _event_texts(packet)
        ref_prefixes = {ref.rsplit("_tool_", 1)[0] for ref in refs}
        located = any(
            row["event_id"] in ref_prefixes and quote in row["text"]
            for row in texts
        )
        if not located:
            return {
                "predicate": "evidence_refs_resolve",
                "verdict": "unresolved",
                "detail": "refs exist but the cited quote cannot be located",
            }
    return {
        "predicate": "evidence_refs_resolve",
        "verdict": "resolved",
        "detail": f"all {len(refs)} evidence refs resolve to packet ids",
    }


def predicate_contract_coverage(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    detected = _detect_contract_kinds(_question_text(packet))
    if not detected:
        return {
            "predicate": "contract_coverage",
            "verdict": "not_applicable",
            "detail": "no explicit mechanical contract markers detected",
        }
    contracts = context.get("contracts") or []
    covered = {
        str(item.get("kind") or "")
        for item in contracts
        if isinstance(item, Mapping) and item.get("kind")
    }
    missing = sorted(detected - covered)
    if not missing:
        return {
            "predicate": "contract_coverage",
            "verdict": "covered",
            "detail": f"judge contracts cover detected kinds: {sorted(detected)}",
        }
    return {
        "predicate": "contract_coverage",
        "verdict": "uncovered",
        "detail": f"judge contracts omit detected kinds: {missing}",
    }


def predicate_delivered_anchor_consistency(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    delivered = _clean(context.get("delivered_result") or "")
    if not delivered:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "insufficient",
            "detail": "judge delivered_result is empty",
        }
    anchors = _delivered_texts(packet)
    if not anchors:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "insufficient",
            "detail": "no mechanical delivery anchors in packet",
        }
    consistent = any(
        delivered == anchor
        or (len(delivered) >= 6 and anchor in delivered)
        for anchor in anchors
    )
    if consistent:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "consistent",
            "detail": "judge delivered_result matches a mechanical delivery anchor",
        }
    return {
        "predicate": "delivered_anchor_consistency",
        "verdict": "insufficient",
        "detail": "no literal anchor match; this does not establish a semantic contradiction",
    }


def predicate_final_delivery_present(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Detect a terminal success with no delivery anchor of any kind.

    Three delivery channels count, not two. A trajectory may deliver its answer
    as the last prose event, through an answer-like tool call, or **in a tool
    observation** -- which is how this corpus's OpenManus-style runs deliver:
    the answer sits in the observation of the final call and the run closes with
    ``terminate(status=success)``. Reading only prose and answer-tool anchors
    called three correct runs "no delivery", which is what a false positive
    looks like when a predicate knows one framework's shape and not another's.

    A content-bearing observation is one whose text, after the runtime envelope
    ("Observed output of cmd `x` executed:") and the fixed completion banner are
    removed, still carries at least ``_MIN_DELIVERY_CHARS`` characters. The
    threshold is loose on purpose: this predicate is not judging whether the
    answer is *correct*, only whether a channel exists. Its conviction authority
    comes from the calibration record, which refuses it unless the loose rule
    still produces zero clean-set false positives.
    """
    anchors = packet_delivered_result(packet)
    has_prose = bool(anchors.get("last_prose_event_id"))
    answer_tool = anchors.get("last_answer_tool_call") or {}
    answer_tool_name = str(answer_tool.get("tool_name") or "").lower()
    has_answer_tool = bool(answer_tool) and not any(
        marker in answer_tool_name for marker in ("terminate", "finish", "success")
    )
    terminal_success = any(
        "success" in str(interaction.get("arguments") or "").lower()
        or "success" in str(interaction.get("observation") or "").lower()
        for event in packet.get("events") or ()
        for interaction in event.get("tool_interactions") or ()
    )
    has_observation = any(
        len(_delivery_payload(interaction.get("observation")))
        >= _MIN_DELIVERY_CHARS
        for event in packet.get("events") or ()
        for interaction in event.get("tool_interactions") or ()
    )
    if terminal_success and not (
        has_prose or has_answer_tool or has_observation
    ):
        return {
            "predicate": "final_delivery_present",
            "verdict": "missing",
            "detail": (
                "trajectory reports success without a prose, answer-tool or "
                "observation delivery anchor"
            ),
        }
    if not terminal_success:
        return {
            "predicate": "final_delivery_present",
            "verdict": "not_applicable",
            "detail": "no mechanical terminal success signal found",
        }
    return {
        "predicate": "final_delivery_present",
        "verdict": "present",
        "detail": "terminal success has a prose, answer-tool or observation anchor",
    }


_RUNNERS: dict[str, Any] = {
    "required_action_present": predicate_required_action_present,
    "verbatim_fidelity": predicate_verbatim_fidelity,
    "evidence_refs_resolve": predicate_evidence_refs_resolve,
    "contract_coverage": predicate_contract_coverage,
    "delivered_anchor_consistency": predicate_delivered_anchor_consistency,
    "final_delivery_present": predicate_final_delivery_present,
}


def run_predicate(
    name: str,
    packet: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one deterministic predicate and enforce its verdict vocabulary."""
    runner = _RUNNERS.get(name)
    if runner is None:
        raise ValueError(f"predicate {name!r} has no deterministic executor")
    result = dict(runner(packet, dict(context or {})))
    verdict = str(result.get("verdict") or "")
    if verdict not in PREDICATE_VERDICTS[name]:
        raise ValueError(
            f"predicate {name} returned verdict {verdict!r} outside its vocabulary"
        )
    result["runner"] = "deterministic"
    return result


__all__ = [
    "PREDICATE_CONVICTION_VERDICTS",
    "PREDICATE_VERDICTS",
    "PREDICATES",
    "run_predicate",
]
