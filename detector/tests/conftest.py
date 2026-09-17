from __future__ import annotations

import pytest


@pytest.fixture
def sample_record():
    return {
        "question": "What is 2 + 2?",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"name": "calculator", "arguments": {"expr": "2+2"}}
                ],
                "tool_responses": ["4"],
            },
            {
                "step": 2,
                "role": "assistant",
                "content": "The answer is 4.",
            },
        ],
    }


@pytest.fixture
def sample_packet(sample_record):
    from detector.packet import build_packet

    return build_packet(sample_record)
