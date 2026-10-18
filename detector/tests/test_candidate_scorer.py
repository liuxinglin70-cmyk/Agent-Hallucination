"""The learned tie-breaker must order a band and nothing more."""
import json

import pytest

from detector.candidate_scorer import (
    FEATURES, SCORER_ARTIFACT_SCHEMA, ScorerArtifactError, load_scorer,
    row_features, score_row,
)
from detector.config import validate_causal_config
from detector.packet import build_packet
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates


def policy(**changes):
    return validate_causal_config({"mode": "shadow", **changes}, base_dir=".")


def write_artifact(tmp_path, weights, *, schema=SCORER_ARTIFACT_SCHEMA, bias="0"):
    path = tmp_path / "scorer.json"
    path.write_text(json.dumps({"schema_version": schema, "id": "test",
                                "weights": weights, "bias": bias}), encoding="utf-8")
    return str(path)


def tied_packet():
    """Three events in one priority band: nothing but position separates them."""
    return build_packet({"question": "Report the recorded result", "history": [
        {"role": "assistant", "content": "The order amount is 4 USD."},
        {"role": "assistant", "content": "The order amount is 4 USD, confirmed."},
        {"role": "assistant", "content": "The order amount is 4 USD, finalized."},
    ]})


def selection(packet, artifact=None, top_k=3):
    cfg = policy(**({"scorer_artifact": artifact} if artifact else {}), top_k=top_k)
    profile = build_step_risk_profile(packet, policy=cfg)
    return select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)


def test_without_an_artifact_the_order_is_the_historical_one():
    p = tied_packet()
    chosen = selection(p)["top_k_event_ids"]
    cfg = policy(top_k=3)
    profile = build_step_risk_profile(p, policy=cfg)
    legacy = sorted(
        (row for row in profile["by_event"]
         if row["score_status"] in {"scored", "truncated"} and row["risk_signals"]),
        key=lambda row: (row["priority"], -sum(
            signal["verification_status"] == "verified" for signal in row["risk_signals"]),
            row["position"]))
    assert chosen == [row["event_id"] for row in legacy[:3]]


def test_a_learned_score_orders_inside_a_band_but_never_across_one(tmp_path):
    p = build_packet({"question": "Report the recorded result", "history": [
        {"role": "assistant", "content": "thinking"},
        {"role": "assistant", "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
         "tool_responses": ["ERROR: the tool failed"]},
        {"role": "assistant", "content": "The search returned the answer."},
    ]})
    cfg = policy()
    profile = build_step_risk_profile(p, policy=cfg)
    ranked_priority = {row["event_id"]: row["priority"] for row in profile["by_event"]}
    failure = "event_002"
    assert ranked_priority[failure] == 1
    # A scorer that loves long prose must not lift the claim over the failure.
    artifact = write_artifact(tmp_path, {name: "1000" if name == "signal_count" else "0"
                                         for name in FEATURES})
    chosen = selection(p, artifact)["top_k_event_ids"]
    assert chosen[0] == failure


def test_equal_features_produce_equal_scores_under_any_weight_magnitude(tmp_path):
    scorer = load_scorer(write_artifact(
        tmp_path, {name: "0.0000000000001" for name in FEATURES}, bias="0.0000000000001"))
    row = {"position": 2, "risk_signals": [
        {"signal": "unsupported_claim", "verification_status": "unknown"}]}
    features = row_features(row, position_count=3)
    assert score_row(features, scorer) == score_row(dict(features), scorer)
    other = {"position": 2, "risk_signals": [
        {"signal": "unsupported_claim", "verification_status": "unknown"},
        {"signal": "unsupported_claim", "verification_status": "unknown"}]}
    assert score_row(row_features(other, position_count=3), scorer) > score_row(features, scorer)


@pytest.mark.parametrize("weights,schema,match", [
    ({"not_a_feature": "1"}, SCORER_ARTIFACT_SCHEMA, "unknown scorer feature"),
    ({}, SCORER_ARTIFACT_SCHEMA, "must declare feature weights"),
    ({"signal_count": "nan"}, SCORER_ARTIFACT_SCHEMA, "not finite"),
    ({"signal_count": "1"}, "detector.candidate_scorer.v0", "schema must be"),
])
def test_an_artifact_that_cannot_be_applied_refuses_to_load(tmp_path, weights, schema, match):
    with pytest.raises(ScorerArtifactError, match=match):
        load_scorer(write_artifact(tmp_path, weights, schema=schema))


def test_a_missing_artifact_is_not_silently_treated_as_no_scorer(tmp_path):
    with pytest.raises(ScorerArtifactError):
        load_scorer(str(tmp_path / "absent.json"))


def test_empty_order_control_with_no_artifact_scores_zero():
    assert score_row({"signal_count": 9}, None) == 0
