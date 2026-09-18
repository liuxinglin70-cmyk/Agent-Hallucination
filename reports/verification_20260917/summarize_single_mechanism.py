"""Summarize saved diagnostic observations without executing model calls."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'single_mechanism'

def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def main():
    protocol = read(OUT / 'protocol.json')
    rows = []
    for case in protocol['cases']:
        gold = read(ROOT / 'datasets/raw/agenthallu/AgentHallu' / case)
        for rep in range(1, protocol['repetitions'] + 1):
            path = OUT / (case.replace('/', '__') + f'.rep{rep}.json')
            if not path.exists():
                continue
            obs = read(path)
            result = obs.get('result', {})
            proof = result.get('proof', {})
            hearings = proof.get('hearings') or []
            rows.append({
                'case': case, 'rep': rep, 'gold': obs['gold'],
                'baseline_prediction': obs['baseline_prediction'],
                'prediction': obs.get('prediction'),
                'authority': result.get('decision_authority'),
                'triggered': any(e.get('event') == 'recall_triggered' for e in
                    proof.get('deployment', {}).get('fired', [])),
                'candidate_count': len(proof.get('recall_candidates') or []),
                'upgrade_count': len(proof.get('causal_upgrades') or []),
                'hearing_count': len(hearings),
                'responsible_step': proof.get('responsible_step'),
                'gold_step': gold.get('hallucination_step'),
                'upgrade_reasons': [u.get('reason') for u in proof.get('causal_upgrades') or []],
                'hearing_verdicts': [h.get('defense_result', {}).get('parsed', {}).get('verdict') for h in hearings],
                'authority_failure_reasons': result.get('authority_failure_reasons', []),
                'error': obs.get('error'), 'elapsed_seconds': obs['elapsed_seconds'],
                'raw': path.name,
            })
    counts = Counter()
    for row in rows:
        counts['attempts'] += 1
        counts['triggered'] += row['triggered']
        if row['prediction'] is None:
            counts['unmeasured'] += 1
        elif row['gold'] and row['prediction']:
            counts['label_level_corrections'] += 1
        elif not row['gold'] and row['prediction']:
            counts['new_false_positives'] += 1
    out = {'complete': len(rows) == protocol['max_runs'],
           'counts': dict(counts), 'rows': rows,
           'warning': 'Label-level agreement is not proof of the benchmark failure mechanism. Inspect gold locus and supporting evidence separately; repeats are not independent cases.'}
    (OUT / 'analysis.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'complete': out['complete'], 'counts': out['counts']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
