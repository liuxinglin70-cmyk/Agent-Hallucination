from copy import deepcopy

import pytest

from detector.judge import _parse_judge
from detector.recaller import _parse_candidates
from detector.step_tracer import _parse_tracer
from detector.tests.test_judge_defense import JUDGE_TRUE


def parse_quote(stage, packet, quote, *, step=2, refs=None):
    refs = refs if refs is not None else [f"event_{step:03d}"]
    if stage == "judge":
        row = deepcopy(JUDGE_TRUE)
        row.update(hallucination_step=step, evidence_refs=refs)
        row["root_cause"].update(step=step, quote=quote)
        return _parse_judge(row, packet)
    if stage == "tracer":
        return _parse_tracer({
            "step": step, "quote": quote, "referent": "correct value",
            "reason": "incorrect result", "evidence_refs": refs,
        }, packet)
    return _parse_candidates({"candidates": [{
        "candidate_id": "c1", "claim": "incorrect result",
        "locus_event_id": f"event_{step:03d}",
        "quote": quote, "evidence_refs": refs,
    }]}, packet)


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
@pytest.mark.parametrize("quote", ["FABRICATED_QUOTE", "calculator", ["4"], {"value": "4"}])
def test_quote_must_be_text_from_selected_event(sample_packet, stage, quote):
    result, error = parse_quote(stage, sample_packet, quote)
    assert result is None
    assert "quote" in error


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
def test_quote_can_reference_assistant_text(sample_packet, stage):
    result, error = parse_quote(stage, sample_packet, "The answer is 4.")
    assert result is not None, error


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
@pytest.mark.parametrize("quote", ["calculator", "2+2", "4"])
def test_quote_can_reference_cited_tool_channel(sample_packet, stage, quote):
    result, error = parse_quote(stage, sample_packet, quote, step=1,
                                refs=["event_001_tool_01"])
    assert result is not None, error


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
def test_quote_cannot_join_distinct_channels(sample_packet, stage):
    result, error = parse_quote(stage, sample_packet, "calculator2+2", step=1)
    assert result is None
    assert "quote" in error


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
def test_quote_cannot_borrow_uncited_interaction(sample_packet, stage):
    packet = deepcopy(sample_packet)
    interaction = deepcopy(packet["events"][0]["tool_interactions"][0])
    interaction.update(interaction_id="event_001_tool_02", tool_name="unrelated")
    packet["events"][0]["tool_interactions"].append(interaction)
    result, error = parse_quote(stage, packet, "unrelated", step=1,
                                refs=["event_001_tool_01"])
    assert result is None
    assert "quote" in error


@pytest.mark.parametrize("stage", ["judge", "tracer", "scanner"])
def test_frozen_replay_rejects_invented_quotes(wrong_answer_packet, stage):
    from detector.replay import validate_frozen_stages
    from detector.tests.test_voting_hearings import JUDGE_FALSE

    row = deepcopy(JUDGE_TRUE if stage == "judge" else JUDGE_FALSE)
    frozen = {"judge_result": {"ok": True, "parsed": row},
              "judge_status": {"state": "ok"}}
    if stage == "judge":
        row["root_cause"]["quote"] = "FABRICATED_QUOTE"
    elif stage == "scanner":
        frozen.update(recall_status={"state": "ok"}, recall_result={
            "ok": True, "candidates": [{
                "candidate_id": "c1", "claim": "wrong value",
                "locus_event_id": "event_002", "evidence_refs": ["event_002"],
                "quote": "FABRICATED_QUOTE",
            }],
        })
    else:
        frozen["hearings"] = [{"source": "judge", "positive": {"step": 2},
            "tracer_status": {"state": "ok"}, "tracer_result": {
                "ok": True, "parsed": {
                    "step": 2, "quote": "FABRICATED_QUOTE", "referent": "4",
                    "reason": "wrong value", "evidence_refs": ["event_002"],
                },
            },
        }]
    with pytest.raises(ValueError, match="quote"):
        validate_frozen_stages(frozen, wrong_answer_packet)


@pytest.mark.parametrize("repaired", [True, False])
def test_live_judge_repairs_quote_once_then_abstains(wrong_answer_packet, monkeypatch, repaired):
    from detector.judge import run_judge
    from detector.tests.test_judge_defense import CONFIG

    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs["purpose"])
        row = deepcopy(JUDGE_TRUE)
        if len(calls) == 1 or not repaired:
            row["root_cause"]["quote"] = "FABRICATED_QUOTE"
        return row, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    result, status = run_judge(packet=wrong_answer_packet, config=CONFIG)
    assert calls == ["judge", "judge_repair"]
    assert result["ok"] is repaired
    assert status["state"] == ("ok" if repaired else "contract_error")
    assert any("quote" in warning["error"] for warning in status["warnings"])

