"""The convicting predicates read obligations, not answer-format lines.

Under review, two of the three built-in convicting predicates fired on gold-clean
cases, and every false positive came from a text pattern that is not an
obligation:

* ``use the`` inside "Use the complete name with article if any" (an answer
  *format* instruction) parsed as a requirement to use something called
  "complete name";
* the movement branch read ``proceed to`` as a movement marker, so "proceed to
  close the ticket" invented a place to travel to;
* a destination inside a question or a hypothetical ("perhaps we could swing by
  the nearest tire shop?") was read as an instruction;
* the pending-engine check fired when the permission question was merely
  *present* somewhere in the trajectory, including inside a quoted string the
  agent had posted, and when the ask was not terminal at all;
* the delivery check knew only two delivery channels (final prose, answer tool)
  and called a run that delivered its answer in a tool observation
  "no delivery".

Each test below pins one of those readings. They are the *predicate* half of the
fix; ``test_conviction_calibration.py`` pins the guard that keeps an unmeasured
predicate from ever authorizing a conviction, and the corpus-level numbers are
recorded in ``reports/20260923_predicate_calibration/``.
"""

from __future__ import annotations

from detector.packet import build_packet
from detector.predicates import (
    _detect_required_actions,
    _movement_requirements,
    _permission_pending,
    run_predicate,
)


def _packet(question, history):
    return build_packet({"question": question, "history": history})


def _terminal(status="success"):
    return {
        "step": 99,
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": "terminate", "arguments": {"status": status}}],
        "tool_responses": [
            "Observed output of cmd `terminate` executed:\n"
            "The interaction has been completed with status: success"
        ],
    }


# ---------------------------------------------------------------------------
# required_action_present: the action branch
# ---------------------------------------------------------------------------

def test_answer_format_instruction_is_not_a_required_tool_action():
    """The exact false positive from the corpus (Camel/023).

    "Use the complete name with article if any" asks for a *format*; there is no
    tool word anywhere in it, so nothing here is a tool obligation.
    """
    question = (
        "In Valentina Re's contribution to the 2017 book \"World Building\", "
        "what horror movie does the author cite as having popularized "
        "metalepsis? Use the complete name with article if any."
    )
    assert _detect_required_actions(question) == set()


def test_clearance_phrases_do_not_become_required_actions():
    """ "You are welcome to use the same credit card" is permission, not duty.

    Also the corpus's BFCL/128 shape: the phrase sits inside a courtesy clause
    and names an object, not a tool.
    """
    for question in (
        "You are welcome to use the same credit card and access token.",
        "Use the plural form of each fruit.",
        "Don't use the prefix nano in your answer.",
        "Use the term minimal conflict set in your response.",
    ):
        assert _detect_required_actions(question) == set(), question


def test_a_tool_obligation_is_still_read():
    """The double condition does not throw away the real case."""
    assert _detect_required_actions(
        "You must use the search tool to answer."
    ) == {"search"}
    assert _detect_required_actions(
        "Please call the calculator function with the two operands."
    ) == {"calculator"}
    assert _detect_required_actions(
        "You must invoke the weather API before booking."
    ) == {"weather"}


def test_structured_required_action_spelling_still_wins():
    """The explicit machine-readable form is unaffected by the prose rules."""
    assert _detect_required_actions(
        "required_action: \"lookup_customer\""
    ) == {"lookup_customer"}


def test_missing_tool_obligation_still_convicts():
    """A real missing required call is still a violation.

    The predicate exists to catch this; the fix must narrow its reading, not
    silence it.
    """
    packet = _packet(
        "You must use the search tool before answering.",
        [{
            "step": 1,
            "role": "assistant",
            "content": "The answer is 42.",
        }],
    )
    result = run_predicate("required_action_present", packet, {})
    assert result["verdict"] == "violated"
    assert "search" in result["detail"]


# ---------------------------------------------------------------------------
# required_action_present: the movement branch
# ---------------------------------------------------------------------------

def test_infinitive_directive_is_not_a_destination():
    """The corpus's BFCL/019, 022, 038, 152 shape.

    "proceed to close the ticket", "proceed to start the engine", "proceed to
    display only the last 10 lines" -- each names an action, and none names a
    place. The old marker table made a destination out of the verb phrase.
    """
    for text in (
        "Kindly proceed to close the ticket for me.",
        "Once fueled, proceed to start the engine confidently.",
        "Proceed to display only the last 10 lines once you've done this.",
        "Please proceed to delete that one.",
        "Then proceed to book the flight using the secured credit card.",
    ):
        assert _movement_requirements([text]) == [], text


def test_a_destination_is_still_read():
    assert _movement_requirements(
        ["Please swing by the nearest tire shop."]
    ) == ["nearest tire shop"]
    assert _movement_requirements(
        ["I want to go to Fire Station 301 DCA ARFF using the metro."]
    ) == ["fire station 301 dca arff"]


def test_destination_in_a_question_or_hypothesis_is_not_an_instruction():
    """ "perhaps we could swing by the nearest tire shop?" asks, not requires."""
    assert _movement_requirements(
        ["If it falls under 37.5 PSI, perhaps we could swing by the nearest "
         "tire shop?"]
    ) == []
    assert _movement_requirements(
        ["Could you go to the warehouse and check the inventory?"]
    ) == []


def test_a_conjunction_is_not_a_destination():
    """ "plan a visit and need to know the travel distance" (BFCL/080)."""
    assert _movement_requirements(
        ["I'm eager to plan a visit and need to know the travel distance."]
    ) == []


def test_a_destination_served_by_a_non_movement_tool_is_satisfied():
    """ "go to the tire shop" answered by ``find_nearest_tire_shop``.

    The old test demanded a tool whose *name* contained a movement verb, which
    encoded "travelling is done by a navigation tool" and read a completed
    destination as unreached (BFCL/066).
    """
    packet = _packet(
        "If the pressure is low, go to the nearest tire shop.",
        [{
            "step": 1,
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "find_nearest_tire_shop", "arguments": {}}],
            "tool_responses": ["123 Main Street, Rivermist"],
        }],
    )
    assert run_predicate("required_action_present", packet, {})["verdict"] != (
        "violated"
    )


def test_an_unreached_destination_still_convicts():
    packet = _packet(
        "Please swing by the nearest tire shop.",
        [{
            "step": 1,
            "role": "assistant",
            "content": "Done.",
        }],
    )
    assert run_predicate("required_action_present", packet, {})["verdict"] == (
        "violated"
    )


# ---------------------------------------------------------------------------
# required_action_present: the pending-permission branch
# ---------------------------------------------------------------------------

def test_permission_ask_must_be_terminal():
    """An agent that asked, was answered, and acted is not pending."""
    mid = _packet(
        "Start the engine.",
        [
            {"step": 1, "role": "assistant",
             "content": "The doors are unlocked. Would you like me to lock them?"},
            {"step": 2, "role": "assistant",
             "content": "Locked all doors and started the engine."},
        ],
    )
    assert _permission_pending(mid) is False
    assert run_predicate("required_action_present", mid, {})["verdict"] != (
        "violated"
    )


def test_permission_ask_that_ends_the_trajectory_still_convicts():
    """The real case the branch exists for: the run stops and waits."""
    pending = _packet(
        "Start the engine for my trip.",
        [{
            "step": 1,
            "role": "assistant",
            "content": (
                "The engine could not be started because the doors are "
                "unlocked. Would you like me to lock all doors and then start "
                "the engine for you?"
            ),
        }],
    )
    assert _permission_pending(pending) is True
    assert run_predicate("required_action_present", pending, {})["verdict"] == (
        "violated"
    )


def test_a_quoted_permission_question_is_not_the_agent_asking():
    """BFCL/063: the question was inside the agent's own posted comment."""
    packet = _packet(
        "Start the engine and then comment about it.",
        [{
            "step": 1,
            "role": "assistant",
            "content": (
                'Your comment "Is this pressure too low? Should I take any '
                'action?" has been successfully added to the tweet!'
            ),
        }],
    )
    assert _permission_pending(packet) is False
    assert run_predicate("required_action_present", packet, {})["verdict"] != (
        "violated"
    )


def test_an_event_with_a_tool_call_does_not_count_as_asking():
    """A tool-calling event is the agent acting, not waiting."""
    packet = _packet(
        "Start the engine for my trip.",
        [{
            "step": 1,
            "role": "assistant",
            "content": "Would you like me to start the engine?",
            "tool_calls": [{"name": "startEngine",
                            "arguments": {"ignitionMode": "START"}}],
            "tool_responses": ["{\"engineState\": \"running\"}"],
        }],
    )
    assert _permission_pending(packet) is False


# ---------------------------------------------------------------------------
# final_delivery_present: delivery channels
# ---------------------------------------------------------------------------

def _bare_terminal(question="Who won the 2018 Jerlov Award?"):
    return _packet(question, [{
        "step": 1,
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": "terminate", "arguments": {"status": "success"}}],
        "tool_responses": [
            "Observed output of cmd `terminate` executed:\n"
            "The interaction has been completed with status: success"
        ],
    }])


def test_bare_terminal_success_is_still_missing():
    """The predicate's real case: success with nothing delivered anywhere."""
    result = run_predicate("final_delivery_present", _bare_terminal(), {})
    assert result["verdict"] == "missing"


def test_answer_carried_by_a_tool_observation_is_a_delivery():
    """The corpus's OpenManus shape (048/062/069 and 165 error cases).

    The answer is in the observation of the extraction call and the run closes
    with ``terminate(status=success)``. There is no final prose event, and
    calling that "no delivery" is a false positive about the *framework*, not
    about the run.
    """
    packet = _packet(
        "Who won the 2018 Jerlov Award?",
        [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "browser_use",
                                "arguments": {"action": "extract_content"}}],
                "tool_responses": [
                    "Observed output of cmd `browser_use` executed: Extracted "
                    "from page: {'text': 'The 2018 Jerlov Award winner is "
                    "Dr. Raymond C. Smith.'}"
                ],
            },
            {
                "step": 2,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "terminate",
                                "arguments": {"status": "success"}}],
                "tool_responses": [
                    "Observed output of cmd `terminate` executed:\n"
                    "The interaction has been completed with status: success"
                ],
            },
        ],
    )
    assert run_predicate("final_delivery_present", packet, {})["verdict"] == (
        "present"
    )


def test_the_runtime_envelope_alone_is_not_a_delivery():
    """The fixed frame must not count as content.

    Every terminating call carries "Observed output of cmd `x` executed:" and
    the completion banner, and a bare ``{'success': True}`` is the same shape.
    If any of those counted, the predicate could never find a missing delivery.
    """
    packet = _packet(
        "Anything?",
        [{
            "step": 1,
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "noop", "arguments": {"status": "success"}}],
            "tool_responses": [
                "Observed output of cmd `noop` executed: {'success': True}"
            ],
        }],
    )
    assert run_predicate("final_delivery_present", packet, {})["verdict"] == (
        "missing"
    )


def test_short_observation_payload_is_not_a_delivery():
    """ "ok" is not an answer; the threshold is what separates the two."""
    packet = _packet(
        "Anything?",
        [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "noop", "arguments": {}}],
                "tool_responses": [
                    "Observed output of cmd `noop` executed: ok"
                ],
            },
            _terminal(),
        ],
    )
    assert run_predicate("final_delivery_present", packet, {})["verdict"] == (
        "missing"
    )


def test_final_prose_is_still_a_delivery():
    packet = _packet(
        "Anything?",
        [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
                "tool_responses": ["Observed output of cmd `search` executed: x"],
            },
            _terminal(),
            {"step": 100, "role": "assistant",
             "content": "The answer is 42."},
        ],
    )
    assert run_predicate("final_delivery_present", packet, {})["verdict"] == (
        "present"
    )
