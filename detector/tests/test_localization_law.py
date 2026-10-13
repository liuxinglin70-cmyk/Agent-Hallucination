from detector import semantics


def test_all_localizing_stages_share_submission_policy():
    for prompt in (semantics.JUDGE_SYSTEM, semantics.RECALL_SYSTEM,
                   semantics.UPGRADE_SYSTEM, semantics.TRACER_SYSTEM,
                   semantics.DEFENSE_SYSTEM):
        assert semantics.LOCALIZATION_LAW in prompt


def test_tracer_search_continues_past_later_adoption_to_the_origin():
    prompt = semantics.TRACER_SYSTEM
    assert "do not stop at the first backwards match" in prompt
    assert "Inspect all earlier events" in prompt
    assert "including tool observations and human replies" in prompt
    assert "would leave those copies unchanged" in prompt
    assert "Stop at the first step where it is" not in prompt
    assert "while keeping the rest" not in prompt


def test_honest_failure_does_not_exclude_required_action_breach():
    for prompt in (semantics.JUDGE_SYSTEM, semantics.UPGRADE_SYSTEM, semantics.DEFENSE_SYSTEM):
        prompt = ' '.join(prompt.split())
        assert 'Check each required deliverable separately' in prompt
        assert 'cannot satisfy a later request' in prompt
        assert 'does not erase an independently established missing required action' in prompt
        assert 'do not invent a prerequisite' in prompt
    assert 'Reporting failure does not discharge' in semantics.JUDGE_SYSTEM
    assert 'failure to answer without' not in semantics.JUDGE_SYSTEM

