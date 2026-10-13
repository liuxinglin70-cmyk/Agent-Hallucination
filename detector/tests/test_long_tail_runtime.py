import pytest

from detector.config import validate_causal_config
from detector.runtime import AddedBudget, AddedBudgetExceeded, CaseDeadlineExceeded, deadline_scope


def test_baseline_wait_does_not_remove_repair_context(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    budget = AddedBudget(validate_causal_config({}, base_dir='.'))
    budget.reserve_context('initial')
    with budget.baseline_wait():
        now[0] = 90
    budget.reserve_context('repair')
    assert budget.snapshot()['elapsed_seconds'] == 0
    assert budget.snapshot()['baseline_wait_seconds'] == 90
    now[0] = 121
    with pytest.raises(AddedBudgetExceeded):
        budget.check()


def test_overlapping_baseline_wait_is_counted_once(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    budget = AddedBudget(validate_causal_config({}, base_dir='.'))
    budget.begin()
    with budget.baseline_wait():
        now[0] = 10
        with budget.baseline_wait():
            now[0] = 50
        now[0] = 60
    assert budget.snapshot()['baseline_wait_seconds'] == 60
    assert budget.snapshot()['elapsed_seconds'] == 0


def test_baseline_wait_never_extends_case_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    budget = AddedBudget(validate_causal_config({}, base_dir='.'))
    with deadline_scope(deadline=5):
        budget.begin()
        with budget.baseline_wait():
            now[0] = 6
        with pytest.raises(CaseDeadlineExceeded):
            budget.reserve_context('repair')


def test_provider_base_wait_exclusion_applies_to_actual_wrapper(monkeypatch):
    from detector.provider import provider_call
    from detector.runtime import added_budget_scope, added_request_scope
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    def slow(**kwargs):
        now[0] += 90
        return {}, {}
    monkeypatch.setattr('detector.provider._provider_call', slow)
    with added_budget_scope(validate_causal_config({}, base_dir='.')) as budget:
        budget.reserve_context('initial')
        provider_call(route={}, system='', user='', purpose='step_tracer')
        budget.reserve_context('repair')
        with added_request_scope(budget), pytest.raises(AddedBudgetExceeded):
            provider_call(route={}, system='', user='', purpose='causal_audit')


def test_request_progress_is_available_before_case_finishes(monkeypatch):
    import json
    from detector.provider import provider_call
    from detector.runtime import request_observer_scope, usage_scope
    events = []
    monkeypatch.setenv('TEST_PROGRESS_KEY', 'fake')
    def transport(**kwargs):
        assert [event['event'] for event in events] == ['request_queued', 'request_started']
        assert all('observed_at' in event for event in events)
        assert all('user' not in event and 'headers' not in event for event in events)
        return 200, json.dumps({'choices': [{'message': {'content': '{"ok": true}'}}],
                                'usage': {'total_tokens': 3}}).encode()
    monkeypatch.setattr('detector.provider._isolated_transport', transport)
    with request_observer_scope(events.append), usage_scope() as requests:
        provider_call(route={'endpoint': 'https://fixture.invalid/v1', 'api_key_env': 'TEST_PROGRESS_KEY'},
                      system='', user='', purpose='judge')
    assert events[-1]['event'] == 'request_finished'
    assert len({event['request_id'] for event in events}) == 1
    assert len(requests) == 1
    assert requests[0]['usage']['total_tokens'] == 3


def test_observer_io_failure_is_not_retried_as_transport(monkeypatch):
    from detector.runtime import observe_request, request_observer_scope
    def broken(event):
        raise OSError('disk fixture')
    with request_observer_scope(broken), pytest.raises(RuntimeError, match='persisted'):
        observe_request({'event': 'request_started'})


def test_interrupted_batch_keeps_request_journal_without_result(tmp_path, monkeypatch):
    import json
    from detector.runner import run_batch
    from detector.runtime import observe_request, record_request
    from detector.tests.test_runner import _FakeDetector, _write_dataset
    class Interrupted(_FakeDetector):
        def detect(self, record, *, case_id, interventions=None):
            observe_request({'event': 'request_started', 'purpose': 'judge', 'request_id': 'fixture'})
            journal = next((tmp_path / 'runs').glob('*/cases/*/attempts/*/requests.jsonl'))
            assert 'request_started' in journal.read_text(encoding='utf-8')
            record_request({'purpose': 'judge', 'request_id': 'fixture', 'usage': {'total_tokens': 3}})
            raise RuntimeError('interruption fixture')
    monkeypatch.setattr('detector.runner.Detector', Interrupted)
    with pytest.raises(RuntimeError, match='interruption fixture'):
        run_batch(dataset_root=_write_dataset(tmp_path / 'data'), cases=['a.json'],
                  run_id='interrupted', runs_root=tmp_path / 'runs', config={}, workers=1)
    journal = next((tmp_path / 'runs').glob('*/cases/*/attempts/*/requests.jsonl'))
    events = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    assert [event['event'] for event in events] == ['request_started', 'request_finished']
    assert not list((tmp_path / 'runs').glob('*/cases/*/result.json'))


def test_provider_total_timeout_includes_capacity_queue(monkeypatch):
    from detector.provider import ProviderError, provider_call
    from detector.runtime import RunControl, run_scope
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    monkeypatch.setenv('TEST_PROGRESS_KEY', 'fake')
    class BusySlots:
        def acquire(self, timeout):
            now[0] += 1
            return now[0] >= 4
        def release(self):
            pass
    control = RunControl(1)
    control._slots = BusySlots()
    def forbidden(**kwargs):
        raise AssertionError('expired provider call must not send HTTP after queue wait')
    monkeypatch.setattr('detector.provider._isolated_transport', forbidden)
    with run_scope(control), pytest.raises(ProviderError):
        provider_call(route={'endpoint': 'https://fixture.invalid/v1', 'api_key_env': 'TEST_PROGRESS_KEY',
                             'total_timeout_seconds': 3, 'transport_retries': 0},
                      system='', user='', purpose='judge')
    assert now[0] == 3
    assert not control.stopped.is_set()
