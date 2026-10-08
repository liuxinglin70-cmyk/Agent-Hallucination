from __future__ import annotations

import pytest


@pytest.fixture
def synthetic_conviction_calibration(tmp_path, monkeypatch, sample_record):
    import json
    from detector import conviction_calibration as calibration

    corpus = tmp_path / "calibration_corpus"
    corpus.mkdir()
    for index in range(calibration.MIN_CLEAN_CASES):
        (corpus / f"clean_{index}.json").write_text(
            json.dumps({**sample_record, "is_hallucination": False}), encoding="utf-8")
    view = calibration.ConvictionCalibration(
        artifact=calibration.build_artifact(corpus), corpus_root=corpus)
    monkeypatch.setattr(calibration, "default_calibration", lambda: view)
    return view


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
