from copy import deepcopy

import pytest

from detector.replay import validate_frozen_stages
from detector.tests.test_judge_defense import JUDGE_TRUE


def envelope():
    return {
        "judge_result": {"ok": True, "parsed": deepcopy(JUDGE_TRUE),
                         "provider": {"vote": {"samples": ["judge"]}}},
        "judge_status": {"state": "ok", "warnings": [{"error": "diagnostic"}]},
        "hearings": [{
            "source": "judge", "positive": {
                "step": 2, "contracts": [{"kind": "exact_value", "requirement": "4"}],
            },
            "tracer_result": {"ok": True, "parsed": {
                "step": 2, "quote": "5", "referent": "4",
                "evidence_refs": ["event_002"], "reason": "wrong value",
            }},
            "tracer_status": {"state": "ok"},
        }],
        "metadata": {"tags": ["frozen"]},
    }


@pytest.mark.parametrize("path", [
    ("judge_result", "parsed", "evidence_refs"),
    ("judge_result", "provider", "vote", "samples"),
    ("judge_status", "warnings"),
    ("hearings", 0, "positive", "contracts"),
    ("hearings", 0, "tracer_result", "parsed", "evidence_refs"),
    ("metadata", "tags"),
])
@pytest.mark.parametrize("mutate_input", [False, True])
def test_validated_replay_owns_nested_objects(wrong_answer_packet, path, mutate_input):
    frozen = envelope()
    normalized = validate_frozen_stages(frozen, wrong_answer_packet)
    preserved = deepcopy(normalized if mutate_input else frozen)
    changed = frozen if mutate_input else normalized
    for key in path:
        changed = changed[key]
    changed.append("changed")
    assert (normalized if mutate_input else frozen) == preserved


@pytest.mark.parametrize("path", [
    ("judge_result", "parsed", "evidence_refs"),
    ("judge_result", "provider", "vote", "samples"),
    ("judge_status", "warnings"),
    ("hearings", 0, "positive", "contracts"),
    ("hearings", 0, "tracer_result", "parsed", "evidence_refs"),
])
def test_backend_envelope_cannot_mutate_baseline(path):
    from evolution.backends import LiveDetectorBackend

    raw = envelope()
    hearing = raw["hearings"][0]
    hearing.update(defense_result={"ok": True, "parsed": {
        "verdict": "substantiated", "confirmed_step": 2,
        "evidence_refs": ["event_002"], "reason": "confirmed",
    }}, defense_status={"state": "ok"})
    baseline = {
        "proof": {"judge": raw["judge_result"]["parsed"], "hearings": raw["hearings"]},
        "providers": {"judge": raw["judge_result"]["provider"]},
        "stage_status": {"judge": raw["judge_status"],
                         "step_tracer": {"state": "ok"}, "defense": {"state": "ok"}},
    }
    preserved = deepcopy(baseline)
    changed = LiveDetectorBackend._frozen_stages(baseline, set())
    for key in path:
        changed = changed[key]
    changed.append("changed")
    assert baseline == preserved
