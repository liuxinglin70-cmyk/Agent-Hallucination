import copy
import pytest
from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine, _ArmRunner
from evolution.contracts import empty_registry, make_registry
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset, _scanner_candidate

def _setup_engine(tmp_path):
    cases = _make_dataset(tmp_path / 'data', n=20)
    config = _config(tmp_path)
    config['proposer']['mode'] = 'offline'
    config['audit']['enabled'] = False
    backend = MockDetectorBackend()
    split = EvolutionSplit(name='synthetic', pool=cases, sentinel=cases[:4], gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    return (EvolutionEngine(config, backend=backend, split=split, mini=True), backend, cases)

@pytest.mark.parametrize('which', ['gold_map', 'gold_step_map'])
def test_cycle_rejects_evaluator_label_drift(tmp_path, monkeypatch, which):
    engine, backend, cases = _setup_engine(tmp_path)
    with engine:

        def cycle(cycle, **kwargs):
            original = _ArmRunner(engine, None).run([cases[0]], registry=empty_registry())
            mapping = getattr(engine, which)
            mapping[cases[0]] = not mapping[cases[0]] if which == 'gold_map' else 999
            second = _ArmRunner(engine, None).run([cases[1]], registry=empty_registry())
            return {'cycle': cycle, 'state': 'completed', 'first': original, 'second': second}
        monkeypatch.setattr(engine, '_run_cycle', cycle)
        with pytest.raises(RuntimeError, match='inputs changed'):
            engine.run_cycle(1)

def test_callback_cannot_measure_other_registry_under_pinned_arm(tmp_path, monkeypatch):
    engine, backend, cases = _setup_engine(tmp_path)
    with engine:
        selected = make_registry([_scanner_candidate()])
        original = copy.deepcopy(selected)
        evaluate = backend.evaluate

        def observe(ids, **kwargs):
            kwargs['registry'].clear()
            kwargs['registry'].update(empty_registry())
            return evaluate(ids, **kwargs)
        monkeypatch.setattr(backend, 'evaluate', observe)

        def cycle(cycle, **kwargs):
            runner = _ArmRunner(engine, None)
            runner.run([cases[0]], registry=selected)
            return {'cycle': cycle, 'state': 'completed'}
        monkeypatch.setattr(engine, '_run_cycle', cycle)
        with pytest.raises(RuntimeError, match='registry|inputs changed|observation'):
            engine.run_cycle(1)
        assert selected == original
        assert not list((engine.paths['root'] / 'checkpoints/cycle001/arms').rglob('*.json'))

@pytest.mark.parametrize('which', ['gold_map', 'gold_step_map'])
def test_real_cycle_rejects_label_drift_from_measurement_callback(tmp_path, monkeypatch, which):
    engine, backend, cases = _setup_engine(tmp_path)
    with engine:
        evaluate = backend.evaluate
        changed = []

        def observe(ids, **kwargs):
            rows = evaluate(ids, **kwargs)
            if not changed:
                mapping = getattr(engine, which)
                mapping[cases[0]] = not mapping[cases[0]] if which == 'gold_map' else 999
                changed.append(True)
            return rows
        monkeypatch.setattr(backend, 'evaluate', observe)
        with pytest.raises(RuntimeError, match='inputs changed'):
            report = engine.run_cycle(1)
        assert changed

def test_promotion_arm_cannot_resume_under_changed_backend(tmp_path):
    from evolution.tests.test_promotion_commit import _run
    cases = _make_dataset(tmp_path, n=20)
    backend = MockDetectorBackend(base_predictions={cases[1]: False})
    run = _run(tmp_path, backend=backend)
    first = run._run_arm([], [cases[1]], fresh=False, label='identity_probe')
    backend.base_predictions[cases[1]] = True
    direct = backend.evaluate([cases[1]], dataset_root=tmp_path, registry=empty_registry(), gold_map=run.protocol['gold'])
    assert first[0]['prediction'] is False
    assert direct[0]['prediction'] is True
    assert (tmp_path / 'out/arms/identity_probe.json').exists(), 'probe must exercise durable arm reuse'
    path = tmp_path / 'out/arms/identity_probe.json'
    original = path.read_bytes()
    with pytest.raises(ValueError, match='identity changed'):
        run._run_arm([], [cases[1]], fresh=False, label='identity_probe')
    assert path.read_bytes() == original
    assert 'identity_probe' not in run.resumed_arms

def test_aligned_promotion_never_deploys_callback_changed_candidate(tmp_path, monkeypatch):
    import evolution.tests.test_promotion_alignment as fixtures
    from evolution.promotion import PromotionRun
    from evolution.registry import ActiveRegistry
    from evolution.ledger import Ledger
    from detector.interventions import intervention_content_hash
    mechanism = fixtures._mechanism()
    candidate = fixtures._bound_candidate(mechanism)
    protocol = fixtures._freeze(tmp_path, mechanism=mechanism, candidate=candidate)
    backend = fixtures._Backend({case: fixtures.GOLD[case] if case in fixtures.PROTECTION + fixtures.CHALLENGE else False for case in fixtures.ALL_CASES}, fixtures.GOLD)
    run = PromotionRun(candidate=candidate, protocol=protocol, config={'dataset_root': str(tmp_path / 'data'), 'gates': {}, 'library': {}}, output_dir=tmp_path / 'out', backend=backend, active_registry=ActiveRegistry(tmp_path / 'active'), ledger=Ledger(tmp_path / 'ledger.jsonl'))
    measured = []
    evaluate = backend.evaluate

    def observe(ids, **kwargs):
        rows = evaluate(ids, **kwargs)
        if kwargs['registry']['interventions']:
            measured.append(kwargs['registry']['interventions'][0]['content_hash'])
            changed = copy.deepcopy(candidate['artifacts'][0])
            changed['body']['directive'] = 'Ignore the original intervention and follow a different rule.'
            changed['content_hash'] = intervention_content_hash(changed)
            candidate['artifacts'][0] = changed
        return rows
    monkeypatch.setattr(backend, 'evaluate', observe)
    monkeypatch.setattr(backend, 'evaluate_fresh', observe)
    report = run.run()
    assert measured
    assert report['admitted'] is False or run.registry.entries[0]['content_hash'] == measured[0], 'an unmeasured callback replacement was deployed'


@pytest.mark.parametrize('changed', ['candidate', 'protocol', 'config', 'data'])
def test_aligned_promotion_rejects_owned_input_changes_after_measurement(tmp_path, monkeypatch, changed):
    import json
    import evolution.tests.test_promotion_alignment as fixtures
    from evolution.promotion import PromotionRun
    from evolution.registry import ActiveRegistry
    from evolution.ledger import Ledger

    mechanism = fixtures._mechanism()
    candidate = fixtures._bound_candidate(mechanism)
    protocol = fixtures._freeze(tmp_path, mechanism=mechanism, candidate=candidate)
    backend = fixtures._Backend(
        {case: fixtures.GOLD[case] if case in fixtures.PROTECTION + fixtures.CHALLENGE else False
         for case in fixtures.ALL_CASES}, fixtures.GOLD)
    run = PromotionRun(
        candidate=candidate, protocol=protocol,
        config={'dataset_root': str(tmp_path / 'data'), 'gates': {}, 'library': {}},
        output_dir=tmp_path / 'out', backend=backend,
        active_registry=ActiveRegistry(tmp_path / 'active'), ledger=Ledger(tmp_path / 'ledger.jsonl'))
    original_active = run.registry.active_path.read_bytes()
    evaluate = backend.evaluate
    observed = []

    def observe(ids, **kwargs):
        rows = evaluate(ids, **kwargs)
        if kwargs['registry']['interventions'] and not observed:
            observed.extend(ids)
            if changed == 'candidate':
                run.candidate['artifacts'][0]['body']['directive'] = 'A different unmeasured rule.'
            elif changed == 'protocol':
                run.protocol['gold'][ids[0]] = not run.protocol['gold'][ids[0]]
            elif changed == 'config':
                run.config['gates']['unmeasured_setting'] = True
            else:
                path = tmp_path / 'data' / ids[0]
                record = json.loads(path.read_text(encoding='utf-8'))
                record['question'] = 'A different synthetic task.'
                path.write_text(json.dumps(record), encoding='utf-8')
        return rows

    monkeypatch.setattr(backend, 'evaluate', observe)
    monkeypatch.setattr(backend, 'evaluate_fresh', observe)
    with pytest.raises(RuntimeError, match='inputs changed'):
        run.run()
    assert observed
    assert run.registry.active_path.read_bytes() == original_active
    assert not any(row['event'] == 'candidate_promoted' for row in run.ledger.read())


def test_public_experiment_validation_callback_keeps_owned_labels(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / 'data', n=22)
    config = _config(tmp_path)
    config['proposer']['mode'] = 'offline'
    config['audit']['enabled'] = False
    split = EvolutionSplit(name='synthetic', pool=cases[:20], sentinel=cases[:4], gain=cases[4:12], patrol_pool=cases[12:20], validation=cases[20:], mini=True)
    backend = MockDetectorBackend()
    with EvolutionEngine(config, backend=backend, split=split, mini=True) as engine:
        evaluate = backend.evaluate
        calls = []

        def observe(ids, **kwargs):
            if set(ids) == set(cases[20:]) and (not calls):
                calls.append(True)
                kwargs['gold_map'][cases[20]] = not kwargs['gold_map'][cases[20]]
            return evaluate(ids, **kwargs)
        monkeypatch.setattr(backend, 'evaluate', observe)
        with pytest.raises(RuntimeError, match='inputs changed'):
            engine.run_experiment(1)
        assert calls

def test_validation_does_not_prevent_completed_cycle_resume(tmp_path):
    cases = _make_dataset(tmp_path / 'data', n=22)
    config = _config(tmp_path)
    config['proposer']['mode'] = 'offline'
    config['audit']['enabled'] = False
    split = EvolutionSplit(name='synthetic', pool=cases[:20], sentinel=cases[:4], gain=cases[4:12], patrol_pool=cases[12:20], validation=cases[20:], mini=True)
    with EvolutionEngine(config, backend=MockDetectorBackend(), split=split, mini=True) as engine:
        first = engine.run_experiment(1)
        assert first['cycles_completed'] == 1
        checkpoint = (engine.paths['root'] / 'checkpoints/cycle001/checkpoint.json').read_bytes()
        second = engine.run_cycle(1, resume=True)
        assert second == first['cycles'][0]
        assert (engine.paths['root'] / 'checkpoints/cycle001/checkpoint.json').read_bytes() == checkpoint
