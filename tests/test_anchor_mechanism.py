"""Anchor-mechanism tests: label-free locus evidence for the FN classifier.

The locus projector feeds the FN mechanism classifier with closed tokens
(``tool_result_cited`` / ``tool_result_adjacent`` / ``numeric_locus`` /
``citation_locus``); these tests pin the deterministic label-free behavior
without a provider. The anchor-universe / menu tests pin the
relevance-preserving enumeration that keeps a long trajectory's relevant
anchors inside the planner menu.
"""

from __future__ import annotations

import copy

from sehd_rise.skills.anchors import project_claim_anchors, project_locus_evidence
from sehd_rise.skills.binding import render_binding_menu


def test_locus_evidence_tool_result_cited() -> None:
    """A cited tool result on the previous row grounds the final claim."""
    record = {
        "history": [
            {"role": "user", "content": "Check the rate."},
            {"role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
            {"role": "tool", "tool_responses": ["The rate is 17.4% in 2022."]},
            {"role": "assistant", "content": "The rate is 17.4% [1]."},
        ],
    }
    assert project_locus_evidence(record) == (
        "tool_result_cited", "numeric_locus", "citation_locus",
    )


def test_locus_evidence_plain_numeric_claim() -> None:
    """No tool activity: only the numeric proxy fires."""
    record = {
        "history": [
            {"role": "user", "content": "What is the rate?"},
            {"role": "assistant", "content": "The rate is 17.4%."},
        ],
    }
    assert project_locus_evidence(record) == ("numeric_locus",)


def test_locus_evidence_plain_text_claim() -> None:
    """A claim with neither tools nor numbers yields no hints."""
    record = {
        "history": [
            {"role": "user", "content": "Did it work?"},
            {"role": "assistant", "content": "The check completed successfully."},
        ],
    }
    assert project_locus_evidence(record) == ()


def test_locus_evidence_tool_result_adjacent_without_adoption() -> None:
    """An adjacent result without citation or token overlap only marks
    adjacency: the claim cannot be proven to depend on the result."""
    record = {
        "history": [
            {"role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
            {"role": "tool", "tool_responses": ["Some unrelated result text."]},
            {"role": "assistant", "content": "All done here."},
        ],
    }
    assert project_locus_evidence(record) == ("tool_result_adjacent",)


def _long_trajectory_record() -> dict:
    """Twenty plain spans, then a tool-grounded claim near the tail."""
    history = [{"role": "user", "content": "Check the release date."}]
    history.extend(
        {"role": "assistant", "content": f"Statement number {index} about things."}
        for index in range(20)
    )
    history.append({
        "role": "assistant", "tool_calls": [{"name": "search", "arguments": {}}],
    })
    history.append({
        "role": "tool", "tool_responses": ["The release date is 1999."],
    })
    history.append({"role": "assistant", "content": "The release date is 1999 [1]."})
    history.append({"role": "assistant", "content": "All done."})
    return {"history": history}


def test_anchor_universe_is_not_truncated_at_sixty_four() -> None:
    """A 70-span trajectory keeps all 70 anchors: no deterministic deletion
    before candidate search starts."""
    record = {
        "history": [
            {"role": "user", "content": "Enumerate."},
            *[
                {"role": "assistant", "content": f"Statement number {index}."}
                for index in range(70)
            ],
        ],
    }
    assert len(project_claim_anchors(record)) == 70


def test_menu_includes_relevant_anchor_beyond_first_sixteen() -> None:
    """The tool-grounded anchor sits past the first 16 spans of document
    order; the relevance-ordered menu still carries it."""
    record = _long_trajectory_record()
    anchors = project_claim_anchors(record)
    tool_anchors = [a for a in anchors if a.anchor_kind == "tool_grounded_claim"]
    assert tool_anchors, "fixture must produce a tool-grounded anchor"
    tool_anchor = tool_anchors[0]
    # Document order: the tool anchor is not among the first 16 spans.
    assert [a.anchor_id for a in anchors].index(tool_anchor.anchor_id) >= 16

    menu = render_binding_menu({}, "claim_anchor", record=record, limit=16)
    assert len(menu) == 16
    assert tool_anchor.anchor_id in {row["entity_id"] for row in menu}


def test_menu_is_deterministic_over_long_trajectory() -> None:
    """The relevance selection is a pure projection: same record, same menu."""
    first = render_binding_menu(
        {}, "claim_anchor", record=_long_trajectory_record(), limit=16,
    )
    second = render_binding_menu(
        {}, "claim_anchor", record=copy.deepcopy(_long_trajectory_record()),
        limit=16,
    )
    assert [row["entity_id"] for row in first] == [row["entity_id"] for row in second]


def test_anchor_tool_dependency_ignores_stale_tool_calls() -> None:
    """An early tool call never grounds a later unrelated claim: without an
    adjacent result and adoption evidence, the claim stays a plain assertion."""
    record = {
        "history": [
            {"role": "user", "content": "Check something."},
            {"role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
            {"role": "tool", "tool_responses": ["Some result."]},
            {"role": "assistant", "content": "Step one is done."},
            {"role": "assistant", "content": "Step two is done."},
            {"role": "assistant", "content": "The final fact is that lions are mammals."},
        ],
    }
    anchors = project_claim_anchors(record)
    late = next(a for a in anchors if "lions" in a.span_text)
    assert late.observable_features["anchor_depends_on_tool"] is False
    assert late.anchor_kind == "assistant_assertion"
    assert late.upstream_ref_ids == ()


def test_anchor_tool_dependency_requires_adopted_result() -> None:
    """A tool result cited on the claim's own event grounds the claim."""
    record = {
        "history": [
            {"role": "user", "content": "What is the answer?"},
            {"role": "assistant", "content": "The answer is 42, per the result.",
             "tool_responses": ["Result: the answer is 42."]},
        ],
    }
    anchors = project_claim_anchors(record)
    claim = next(a for a in anchors if "42" in a.span_text)
    assert claim.observable_features["anchor_depends_on_tool"] is True
    assert claim.anchor_kind == "tool_grounded_claim"
    assert claim.upstream_ref_ids == ("event_002_toolresult_01",)


def test_anchor_tool_dependency_adjacent_result_without_adoption() -> None:
    """An adjacent result without citation or token overlap does not ground
    the claim: adjacency alone is not dependency."""
    record = {
        "history": [
            {"role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
            {"role": "tool", "tool_responses": ["The config value is 8080."]},
            {"role": "assistant", "content": "Everything is ready now."},
        ],
    }
    anchors = project_claim_anchors(record)
    claim = next(a for a in anchors if "ready" in a.span_text)
    assert claim.observable_features["anchor_depends_on_tool"] is False
    assert claim.anchor_kind == "assistant_assertion"
