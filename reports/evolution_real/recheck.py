"""Read-only audit of the interrupted run; optional bounded live mechanism replay.

Run from the repository root. Original artifacts and registries are never changed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.api import Detector
from detector.config import load_config
from detector.dataset import load_case
from detector.interventions import compile_runtime_plan
from detector.packet import build_packet
from detector.predicate_dsl import run_predicate_program, _extract_view
from detector.predicates import run_predicate
from detector.result import prediction_state
from evolution.backends import LiveDetectorBackend
from evolution.contracts import make_registry, empty_registry
from evolution.ledger import Ledger
from evolution.metrics import paired_delta, binary_metrics
from evolution.splits import bool_gold

OUT = Path(__file__).resolve().parent
RUN = ROOT / 'artifacts/evolution_real'
DATA = ROOT / 'datasets/raw/agenthallu/AgentHallu'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    cycle = read(RUN / 'cycles/cycle001.json')
    base = {}
    empty_hash = empty_registry()['content_hash']
    for path in (RUN / 'cache').rglob('*.json'):
        result = read(path)
        if result.get('provenance', {}).get('interventions_hash') == empty_hash:
            base[result['case_id']] = result
    return cycle, base


def row(case, result):
    _, gold = load_case(DATA, case)
    return dict(case=case, gold=bool_gold(gold['is_hallucination']),
                prediction=prediction_state(result, require_authoritative=True),
                state=result['state'], authority=result['decision_authority'])


def offline():
    cycle, base = sources()
    records = Ledger(RUN / 'ledger.jsonl').read()
    report = {'scope': 'original 30 development cases; no validation labels read',
              'ledger': Ledger(RUN / 'ledger.jsonl').verify(),
              'baseline_cases': len(base), 'original_hashes': {}, 'candidates': [],
              'last_event': records[-1]['event'],
              'cycle2_payload_persisted': records[-1]['payload']}
    for path in [RUN / 'ledger.jsonl', RUN / 'cycles/cycle001.json', RUN / 'registry/active.json']:
        report['original_hashes'][str(path.relative_to(ROOT))] = digest(path)
    base_rows = [row(case, result) for case, result in base.items()]
    patrol_rows = [r for r in base_rows if r['case'] not in cycle['split']['sentinel'] + cycle['split']['gain']]
    report['patrol_metrics'] = binary_metrics(patrol_rows)
    report['patrol_noncorrect'] = [r for r in patrol_rows if r['prediction'] != r['gold']]
    report['patrol_runtime_failures'] = [{**r,
        'authority_failure_reasons': base[r['case']].get('authority_failure_reasons'),
        'hearing_status': [{'candidate_id': h.get('candidate_id'),
                            'defense_status': h.get('defense_status'),
                            'verdict': ((h.get('defense_result') or {}).get('parsed') or {}).get('verdict')}
                           for h in base[r['case']]['proof'].get('hearings') or []]}
        for r in patrol_rows if r['prediction'] is None]
    report['baseline_by_split'] = {}
    for split in ['sentinel', 'gain']:
        selected = [r for r in base_rows if r['case'] in cycle['split'][split]]
        report['baseline_by_split'][split] = paired_delta(selected, selected)
    for gate in cycle['gates']:
        candidate = gate['candidate']
        item = {'id': candidate['intervention_id'], 'original_behavior': gate['behavior'],
                'original_m': gate['m'], 'fresh': gate['fresh'], 'replay': []}
        original_cases = {r['case'] for r in gate['candidate_rows']}
        item['recomputed_delta_vs_cached_base'] = paired_delta(
            [r for r in base_rows if r['case'] in original_cases], gate['candidate_rows'])
        for case, result in sorted(base.items()):
            record, gold = load_case(DATA, case)
            packet = build_packet(record)
            judge = result['proof']['judge']
            if not isinstance(judge, dict):
                item.setdefault('unavailable_judge_cases', []).append(case)
                continue
            context = dict(judge_parsed=judge, judge_positive=judge['is_hallucination'],
                           contracts=judge.get('contracts') or [],
                           delivered_result=judge.get('delivered_result') or '',
                           evidence_refs=judge.get('evidence_refs') or [],
                           quote=(judge.get('root_cause') or {}).get('quote') or '')
            split = ('gain' if case in cycle['split']['gain'] else
                     'sentinel' if case in cycle['split']['sentinel'] else 'patrol')
            if candidate['type'] == 'recall_trigger':
                plan = compile_runtime_plan(make_registry([candidate]))
                forced = plan.recall_triggered(packet, judge_positive=judge['is_hallucination'], judge_parsed=judge)
                program = candidate['body']['predicate_definition']
                predicate = run_predicate_program(program, packet, context)
                item['replay'].append(dict(case=case, split=split, gold=bool_gold(gold['is_hallucination']),
                    base_prediction=prediction_state(result, require_authoritative=True),
                    judge_positive=judge['is_hallucination'], forced=forced,
                    baseline_recall_state=(result.get('stage_status', {}).get('recall_scanner') or {}).get('state'),
                    newly_opens_recall=forced and (result.get('stage_status', {}).get('recall_scanner') or {}).get('state') == 'not_needed',
                    predicate_verdict=predicate['verdict'], events=plan.fired,
                    missing_answer_anchor=not bool(_extract_view('last_answer_tool_observation', packet, context)),
                    contains_none=any('None' in x for x in _extract_view('tool_observations', packet, context)),
                    wrapper_final_answer=any('final_answer(' in x for x in _extract_view('tool_arguments', packet, context))))
        item['trigger_counts'] = dict(Counter(r['split'] for r in item['replay'] if r['forced']))
        item['new_path_counts'] = dict(Counter(r['split'] for r in item['replay'] if r['newly_opens_recall']))
        report['candidates'].append(item)
    report['focus_gold'] = {}
    report['focus_input_diagnostics'] = {}
    for case in ['BFCL/017.json', 'OpenDeepSearch/086.json', 'BFCL/041.json', 'OpenDeepSearch/077.json']:
        record, gold = load_case(DATA, case)
        report['focus_gold'][case] = gold
        packet = build_packet(record)
        report['focus_input_diagnostics'][case] = {
            'literal_presence_only_not_semantic_entailment': {term: term in json.dumps(record) for term in ['diff', '116', 'brake', 'pressBrakePedal']},
            'anchor_predicate': run_predicate('delivered_anchor_consistency', packet,
                {'delivered_result': base[case]['proof']['judge']['delivered_result']})}
    # Prove whether upgrade-only intervention can reach its hook with identical
    # upstream decisions. Provider functions are blocked, not mocked with answers.
    from unittest.mock import patch
    from contextlib import ExitStack
    config = load_config(ROOT / 'detector/configs/autodl.json')
    report['upgrade_only_frozen_upstream'] = []
    candidate = cycle['gates'][2]['candidate']
    for case in ['BFCL/017.json', 'OpenDeepSearch/086.json', 'BFCL/041.json']:
        record, _ = load_case(DATA, case)
        frozen = LiveDetectorBackend._frozen_stages(base[case], {'upgrade.system'})
        with ExitStack() as stack:
            for name in ['run_judge', 'run_recall_scan', 'run_causal_upgrade', 'run_step_tracer', 'run_defense']:
                # Function names are checked before patching; missing names are
                # not silently patched into the module.
                import detector.api as api
                if hasattr(api, name):
                    stack.enter_context(patch('detector.api.' + name, side_effect=RuntimeError('unexpected provider stage: ' + name)))
            result = Detector(config).detect(record, case_id=case, interventions=make_registry([candidate]), frozen_stages=frozen)
        report['upgrade_only_frozen_upstream'].append({**row(case, result),
            'deployment': result['proof']['deployment'], 'stages': result['stage_status']})
    save(OUT / 'recheck.json', report)
    print(json.dumps({'ledger': report['ledger'], 'candidates': [
        {'id': c['id'], 'triggers': c['trigger_counts'],
         'delta': c['recomputed_delta_vs_cached_base']['marginal_cases']} for c in report['candidates']]}, ensure_ascii=False), flush=True)


def live():
    # Three cases, three fixed repetitions, one frozen-judge candidate arm.
    # This is a diagnostic experiment, never a replacement for original admission.
    cycle, base = sources()
    selected = ['BFCL/017.json', 'OpenDeepSearch/086.json', 'BFCL/041.json']
    candidate = cycle['gates'][1]['candidate']
    config = load_config(ROOT / 'detector/configs/autodl.json')
    out = OUT / 'recheck_live'
    out.mkdir(exist_ok=True)
    manifest = {'kind': 'conditional-on-original-judge mechanism diagnostic',
                'cases': selected, 'repetitions': 3, 'candidate': candidate,
                'control': 'original settled stages replayed; judged-clean controls need no live stages',
                'freeze': 'judge result and original ensemble vote only in candidate arms',
                'max_case_runs': 9, 'workers': 3,
                'detector_config_sha256': digest(ROOT / 'detector/configs/autodl.json'),
                'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / 'detector').glob('*.py'))},
                'baseline_hashes': {case: hashlib.sha256(json.dumps(base[case], sort_keys=True).encode()).hexdigest() for case in selected}}
    manifest_path = out / 'protocol.json'
    if manifest_path.exists() and read(manifest_path) != manifest:
        raise RuntimeError('Saved replay protocol differs; refusing to mix results')
    save(manifest_path, manifest)
    if (out / 'summary.json').exists() and len(read(out / 'summary.json')['outcomes']) == 9:
        print('All nine original attempts are recorded; preserving successes and technical failures.', flush=True)
        return
    controls = []
    for case in selected:
        record, _ = load_case(DATA, case)
        detector = Detector(config)
        frozen = LiveDetectorBackend._frozen_stages(base[case], set())
        # An unexpected provider call in a purported zero-call control is an error.
        from unittest.mock import patch
        with (patch('detector.api.run_judge', side_effect=RuntimeError('unexpected control judge')),
              patch('detector.api.run_recall_scan', side_effect=RuntimeError('unexpected control recall'))):
            result = detector.detect(record, case_id=case, interventions=empty_registry(), frozen_stages=frozen)
        save(out / (case.replace('/', '__') + '.control.json'), result)
        controls.append(row(case, result))

    def run(case, rep):
        path = out / (case.replace('/', '__') + f'.rep{rep}.json')
        if path.exists():
            return {'case': case, 'rep': rep, 'cached': True, **row(case, read(path))}
        record, _ = load_case(DATA, case)
        envelope = LiveDetectorBackend._frozen_stages(base[case], {'recall.trigger'})
        frozen = {k: v for k, v in envelope.items() if k.startswith('judge_')}
        started = time.monotonic()
        result = Detector(config).detect(record, case_id=case, interventions=make_registry([candidate]), frozen_stages=frozen)
        save(path, result)
        return {'case': case, 'rep': rep, 'elapsed': round(time.monotonic()-started, 2), **row(case, result)}

    outcomes = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(run, case, rep): (case, rep) for case in selected for rep in range(1, 4)}
        for future in as_completed(futures):
            case, rep = futures[future]
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = dict(case=case, rep=rep, error=f'{type(exc).__name__}: {exc}')
            outcomes.append(outcome)
            save(out / 'summary.json', {'protocol': 'protocol.json', 'controls': controls, 'outcomes': outcomes})
            print(json.dumps(outcome, ensure_ascii=False), flush=True)


def complete_hearings():
    """Complete only technical replay failures, preserving the original attempt.

    Supply the recorded judge at run_judge's boundary, leaving all downstream
    production functions live. No model-generated downstream answer is mocked.
    """
    from copy import deepcopy
    import threading
    from unittest.mock import patch
    from contextlib import ExitStack
    import detector.api as api
    cycle, base = sources()
    parent = OUT / 'recheck_live'
    prior = read(parent / 'summary.json')
    if len(prior['outcomes']) != 9:
        raise RuntimeError('Wait for all original diagnostic attempts before completion')
    targets = [r for r in prior['outcomes'] if 'missing a hearing freeze entry' in r.get('error', '')]
    out = parent / 'hearing_completion'
    out.mkdir(exist_ok=True)
    protocol = {'reason': 'original frozen API rejects newly created hearing',
                'selection': 'all and only missing-hearing technical failures; one replacement attempt each',
                'targets': targets, 'judge': 'exact original cached result and ensemble vote',
                'downstream': 'unmodified live scanner, upgrade, tracer, defense; no frozen_stages parameter',
                'parent_protocol_sha256': digest(parent / 'protocol.json')}
    save(out / 'protocol.json', protocol)
    candidate = cycle['gates'][1]['candidate']
    config = load_config(ROOT / 'detector/configs/autodl.json')
    local = threading.local()

    def replay_judge(**kwargs):
        if kwargs['packet'] != local.packet:
            raise RuntimeError('Recorded judge does not match diagnostic packet')
        envelope = LiveDetectorBackend._frozen_stages(base[local.case], {'recall.trigger'})
        return deepcopy(envelope['judge_result']), deepcopy(envelope['judge_status'])

    def capture(name, original):
        def call(**kwargs):
            result = original(**kwargs)
            local.stages.append({'stage': name, 'result': result})
            save(local.stage_path, local.stages)
            return result
        return call

    def one(item):
        case, rep = item['case'], item['rep']
        name = case.replace('/', '__') + f'.rep{rep}'
        path = out / (name + '.json')
        local.case = case
        record, _ = load_case(DATA, case)
        local.packet = build_packet(record)
        local.stage_path = out / (name + '.stages.json')
        local.stages = []
        started = time.monotonic()
        if path.exists():
            result = read(path)
        else:
            result = Detector(config).detect(record, case_id=case, interventions=make_registry([candidate]))
            result['diagnostic_judge_replay'] = {'source': 'original empty-registry result', 'method': 'run_judge boundary replay'}
            save(path, result)
        return {**row(case, result), 'rep': rep, 'elapsed': round(time.monotonic()-started, 2)}

    outcomes = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(api, 'run_judge', replay_judge))
        for name in ['run_recall_scan', 'run_causal_upgrade', 'run_step_tracer', 'run_defense']:
            original = getattr(api, name)
            stack.enter_context(patch.object(api, name, capture(name, original)))
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(one, item): item for item in targets}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'case': item['case'], 'rep': item['rep'], 'error': f'{type(exc).__name__}: {exc}'}
                outcomes.append(result)
                save(out / 'summary.json', {'outcomes': outcomes, 'protocol': 'protocol.json'})
                print(json.dumps(result), flush=True)


def summarize_live():
    parent = OUT / 'recheck_live'
    original = read(parent / 'summary.json')
    completion_path = parent / 'hearing_completion/summary.json'
    completion = read(completion_path) if completion_path.exists() else {'outcomes': []}
    output = {'original_attempts': original['outcomes'], 'technical_completions': completion['outcomes'],
              'warning': 'Conditional mechanism diagnostic; technical completions are not independent extra replicates.',
              'cases': {}}
    for case in read(parent / 'protocol.json')['cases']:
        _, gold = load_case(DATA, case)
        runs = []
        for rep in range(1, 4):
            name = case.replace('/', '__') + f'.rep{rep}.json'
            path = parent / name
            supplemented = False
            if not path.exists():
                path = parent / 'hearing_completion' / name
                supplemented = True
            if not path.exists():
                runs.append({'rep': rep, 'result_missing': True})
                continue
            result = read(path)
            proof = result['proof']
            runs.append({**row(case, result), 'rep': rep,
                'technical_completion': supplemented, 'path': str(path.relative_to(ROOT)),
                'responsible_step': result.get('responsible_step'),
                'recall_candidates': len(proof.get('recall_candidates') or []),
                'upgrade_positive': len(proof.get('causal_upgrades') or []),
                'forced_recall': any(r.get('event') == 'recall_triggered' for r in proof.get('deployment', {}).get('fired', [])),
                'stages': {k: v.get('state') for k, v in result.get('stage_status', {}).items()},
                'hearing_verdicts': [((h.get('defense_result') or {}).get('parsed') or {}).get('verdict') for h in proof.get('hearings') or []],
                'upgrade_reasons': [r.get('reason') for r in proof.get('causal_upgrade_rows') or []],
                'hearing_reasons': [((h.get('defense_result') or {}).get('parsed') or {}).get('reason') for h in proof.get('hearings') or []]})
        output['cases'][case] = {'gold_step': gold['hallucination_step'], 'runs': runs,
            'completed': sum(not r.get('result_missing') for r in runs),
            'correct': sum(r.get('prediction') is True for r in runs),
            'clean': sum(r.get('prediction') is False for r in runs),
            'unresolved_or_missing': sum(r.get('prediction') is None for r in runs)}
    save(parent / 'analysis.json', output)
    print(json.dumps({k: {x: v[x] for x in ['completed','correct','clean','unresolved_or_missing']} for k,v in output['cases'].items()}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--complete-hearings', action='store_true')
    parser.add_argument('--summarize-live', action='store_true')
    args = parser.parse_args()
    offline()
    if args.live:
        live()
    if args.complete_hearings:
        complete_hearings()
    if args.summarize_live:
        summarize_live()
