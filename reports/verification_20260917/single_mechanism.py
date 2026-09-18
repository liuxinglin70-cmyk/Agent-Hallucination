"""Bounded live recall diagnostic; never admits candidates or changes thresholds."""
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.api import Detector
from detector.config import load_config
from detector.dataset import load_case
from detector.result import prediction_state
from evolution.backends import LiveDetectorBackend, source_digest
from evolution.contracts import make_registry

OUT = Path(__file__).resolve().parent / 'single_mechanism'
CASES = ['BFCL/017.json', 'OpenDeepSearch/086.json',
         'Magentic_One/036.json', 'OpenDeepSearch/002.json']

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def save(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    OUT.mkdir(exist_ok=True)
    cycle = read(ROOT / 'artifacts/evolution_real/cycles/cycle001.json')
    candidate = next(g['candidate'] for g in cycle['gates']
                     if g['candidate']['intervention_id'] == 'IV-1-001')
    baselines = {}
    for p in (ROOT / 'artifacts/evolution_real/checkpoints/cycle001/arms/508acdf65658').glob('*.json'):
        row = read(p)['row']
        baselines[row['case']] = row
    config = load_config(ROOT / 'detector/configs/autodl.json')
    manifest = {'kind': 'development mechanism diagnostic, not independent validation',
        'candidate': candidate, 'cases': CASES, 'repetitions': 2, 'max_runs': 8,
        'freeze': 'original judge and packet binding; all downstream stages live',
        'selection': 'two original failure-family members and two authoritative baseline TN controls',
        'threshold_changes': False, 'source_digest': source_digest(),
        'config': config,
        'baseline_sha256': {c: hashlib.sha256(json.dumps(baselines[c], sort_keys=True).encode()).hexdigest() for c in CASES},
        'criteria': 'Report trigger, candidates, authoritative corrections, new errors and technical failures separately. No automatic admission. Two repeats assess gross instability only.'}
    path = OUT / 'protocol.json'
    if path.exists() and read(path) != manifest:
        raise RuntimeError('Frozen protocol differs; refuse to mix observations')
    save(path, manifest)

    def run(case, rep):
        target = OUT / (case.replace('/', '__') + f'.rep{rep}.json')
        if target.exists():
            return read(target)
        row = baselines[case]
        envelope = LiveDetectorBackend._frozen_stages(row['result'], {'recall.trigger'})
        frozen = {k: v for k, v in envelope.items() if k.startswith('judge_') or k == 'packet_hash'}
        record, _ = load_case(ROOT / 'datasets/raw/agenthallu/AgentHallu', case)
        start = time.monotonic()
        observed = {'case': case, 'rep': rep, 'gold': row['gold'], 'baseline_prediction': row['prediction']}
        try:
            result = Detector(config).detect(record, case_id=case,
                interventions=make_registry([candidate]), frozen_stages=frozen)
            observed.update(result=result, prediction=prediction_state(result, require_authoritative=True))
        except Exception as exc:
            observed['error'] = f'{type(exc).__name__}: {exc}'
        observed['elapsed_seconds'] = round(time.monotonic() - start, 2)
        save(target, observed)
        return observed

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, c, r) for c in CASES for r in (1, 2)]
        for future in as_completed(futures):
            obs = future.result()
            result = obs.get('result', {})
            compact = {k: v for k, v in obs.items() if k != 'result'}
            compact.update(authority=result.get('decision_authority'),
                stages=result.get('stage_status'),
                recall_candidates=len(result.get('proof', {}).get('recall_candidates', [])))
            outcomes.append(compact)
            save(OUT / 'summary.json', {'protocol': 'protocol.json', 'outcomes': outcomes, 'complete': len(outcomes) == 8})
            print(json.dumps(compact, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
