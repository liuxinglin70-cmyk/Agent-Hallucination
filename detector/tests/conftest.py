from __future__ import annotations

import pytest


@pytest.fixture
def replay_inputs_for():
    from detector.config import validate_config
    from detector.identity import source_identity
    from detector.packet import build_packet
    from detector.replay import build_replay_inputs

    def build(record, config, snapshot=None):
        return build_replay_inputs(
            packet=build_packet(record),
            config=validate_config(config, require_credentials=False),
            source=source_identity("detector"), snapshot=snapshot,
        )

    return build


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


@pytest.fixture
def wrong_answer_packet(sample_record):
    from copy import deepcopy
    from detector.packet import build_packet

    record = deepcopy(sample_record)
    record["history"][-1]["content"] = "The answer is 5."
    return build_packet(record)
