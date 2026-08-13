"""Anchor-mechanism tests: label-free locus evidence for the FN classifier.

The locus projector feeds the FN mechanism classifier with closed tokens
(``tool_result_cited`` / ``tool_result_adjacent`` / ``numeric_locus`` /
``citation_locus``); these tests pin the deterministic label-free behavior
without a provider.
"""

from __future__ import annotations

from sehd_rise.skills.anchors import project_locus_evidence


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
