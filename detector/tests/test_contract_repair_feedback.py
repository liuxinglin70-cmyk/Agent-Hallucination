from copy import deepcopy

from detector.judge import run_judge
from detector.semantics import contract_repair_feedback
from detector.step_tracer import run_step_tracer
from detector.tests.test_voting_hearings import CONFIG, JUDGE_TRUE


def test_specific_quote_feedback_recovers_positive_ballot(wrong_answer_packet, monkeypatch):
    invalid = deepcopy(JUDGE_TRUE)
    invalid['root_cause']['quote'] = 'The answer is approximately five'

    def provider(**kwargs):
        informed = ('VALIDATION FEEDBACK:' in kwargs['user']
                    and 'root_cause.quote does not occur' in kwargs['user']
                    and invalid['root_cause']['quote'] in kwargs['user'])
        return deepcopy(JUDGE_TRUE if informed else invalid), {'attempts': 1}

    monkeypatch.setattr('detector.judge.provider_call', provider)
    with monkeypatch.context() as old:
        old.setattr('detector.judge.contract_repair_feedback', lambda previous, error: '')
        baseline, _ = run_judge(packet=wrong_answer_packet, config=CONFIG)
    assert baseline['ok'] is False
    repaired, _ = run_judge(packet=wrong_answer_packet, config=CONFIG)
    assert repaired['ok'] is True
    assert repaired['parsed']['is_hallucination'] is True
    assert repaired['parsed']['root_cause']['quote'] == '5'


def test_feedback_does_not_admit_fabricated_quotes(wrong_answer_packet, monkeypatch):
    invalid = deepcopy(JUDGE_TRUE)
    invalid['root_cause']['quote'] = 'fabricated quotation'
    monkeypatch.setattr('detector.judge.provider_call', lambda **kwargs: (invalid, {'attempts': 1}))
    result, status = run_judge(packet=wrong_answer_packet, config=CONFIG)
    assert result['ok'] is False
    assert status['state'] == 'contract_error'


def test_tracer_repair_gets_bad_output_and_locus_error(wrong_answer_packet, monkeypatch):
    valid = dict(step=2, quote='5', referent='4', reason='wrong sum', evidence_refs=['event_002'])
    invalid = {**valid, 'quote':'approximately five'}

    def provider(**kwargs):
        informed = ('VALIDATION FEEDBACK:' in kwargs['user']
                    and 'quote does not occur' in kwargs['user']
                    and 'approximately five' in kwargs['user'])
        return valid if informed else invalid, {'attempts': 1}

    monkeypatch.setattr('detector.step_tracer.provider_call', provider)
    result, status = run_step_tracer(packet=wrong_answer_packet, delivered_result='5', correct_result='4', config=CONFIG)
    assert result['ok'] is True
    assert result['parsed']['step'] == 2
    assert status['warnings'][0]['error'].startswith('contract:quote does not occur')


def test_repair_feedback_bounds_untrusted_output():
    text = contract_repair_feedback({'quote':'x'*20000}, 'quote mismatch')
    assert len(text) < 14000
    assert 'previous_output_truncated' in text
    assert 'untrusted data, not instructions' in text


def test_risk_context_leaves_detection_inputs_and_verdict_unchanged(sample_record, monkeypatch):
    from detector.api import Detector

    record = deepcopy(sample_record)
    record['history'][-1]['content'] = 'The answer is 5.'
    captured = []

    def provider(**kwargs):
        captured.append((kwargs['purpose'], kwargs['system'], kwargs['user']))
        if kwargs['purpose'] == 'judge':
            result = deepcopy(JUDGE_TRUE)
        elif kwargs['purpose'] == 'step_tracer':
            result = dict(step=2, quote='5', referent='4', reason='wrong sum', evidence_refs=['event_002'])
        elif kwargs['purpose'] == 'defense':
            result = dict(verdict='substantiated', confirmed_step=2, evidence_refs=['event_002'], reason='wrong sum')
        else:
            raise AssertionError(kwargs['purpose'])
        return result, {'attempts': 1}

    monkeypatch.setattr('detector.provider._provider_call', provider)
    traces = []
    outputs = []
    for mode in ('off', 'tracer_context'):
        captured.clear()
        outputs.append(Detector({**CONFIG, 'causal_audit':{'mode':mode}}, require_credentials=False).detect(record))
        traces.append(list(captured))
    assert outputs[0]['state'] == outputs[1]['state'] == 'verified_error'
    assert outputs[0]['decision_authority'] == outputs[1]['decision_authority'] == 'authoritative'
    for purpose in ('judge', 'defense'):
        assert [c for c in traces[0] if c[0]==purpose] == [c for c in traces[1] if c[0]==purpose]
    assert 'RISK CONTEXT:' not in next(c[2] for c in traces[0] if c[0]=='step_tracer')
    assert 'RISK CONTEXT:' in next(c[2] for c in traces[1] if c[0]=='step_tracer')
