from detector import semantics


def test_all_localizing_stages_share_submission_policy():
    for prompt in (semantics.JUDGE_SYSTEM, semantics.RECALL_SYSTEM,
                   semantics.UPGRADE_SYSTEM, semantics.TRACER_SYSTEM,
                   semantics.DEFENSE_SYSTEM):
        assert semantics.LOCALIZATION_LAW in prompt

