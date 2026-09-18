"""Live contract-semantics probes, distinct from benchmark accuracy evidence."""
import copy
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.config import load_config
from detector.dataset import load_case
from detector.packet import build_packet
from detector.provider import case_deadline_ctx
from detector.recaller import run_causal_upgrade
from evolution.backends import source_digest

OUT = Path(__file__).resolve().parent / 'contract_diagnostic'

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    OUT.mkdir(exist_ok=True)
    original = ROOT / 'reports/verification_20260917/single_mechanism/BFCL__017.json.rep2.json'
    base = read(original)['result']
    record, _ = load_case(ROOT / 'datasets/raw/agenthallu/AgentHallu', 'BFCL/017.json')
    jobs = [{'name': f'original_rep{rep}', 'record': record,
             'candidates': base['proof']['recall_candidates'],
             'target_candidate': 'c4', 'expected_target_upgrade': False,
             'kind': 'saved real packet and scanner replay; not a new benchmark case'} for rep in (1, 2)]
    for name, question, claim in [
        ('literal_control', 'Post a tweet with text Hello. The tags tool argument must contain exactly the string ProjectUpdate without a leading #.',
         'The tags argument contains #ProjectUpdate rather than the explicitly required literal ProjectUpdate without a leading #.'),
        ('different_name_control', "Post a tweet with text Hello and tag it with 'ProjectUpdates'.",
         'The tweet uses the tag ProjectUpdate, while the requested tag name is ProjectUpdates, a different name.'),
    ]:
        control = {'question': question, 'history': [{'step': 1, 'role': 'assistant', 'content': None,
            'tool_calls': [{'name': 'post_tweet', 'arguments': {'content': 'Hello', 'tags': ['#ProjectUpdate']}}],
            'tool_responses': ['{"posted": true, "content": "Hello", "tags": ["#ProjectUpdate"]}']}]}
        jobs.append({'name': name, 'record': control, 'kind': 'synthetic contract unit probe',
            'target_candidate': 'c1', 'expected_target_upgrade': True,
            'candidates': [{'candidate_id': 'c1', 'claim': claim, 'locus_event_id': 'event_001',
                           'evidence_refs': ['event_001'], 'quote': "'#ProjectUpdate'"}]})
    config = load_config(ROOT / 'detector/configs/autodl.json')
    protocol = {'source_digest': source_digest(), 'config': config, 'jobs': jobs,
        'old_result_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'stage': 'causal_upgrade only, original votes/timeouts; no gold supplied',
        'limit': '4 calls to the stage, workers=1; no result-dependent extra attempts',
        'scope': 'Tests contract semantics, not full detector accuracy or independent generalization'}
    path = OUT / 'protocol.json'
    if path.exists() and read(path) != protocol:
        raise RuntimeError('Protocol changed; refusing to mix observations')
    save(path, protocol)
    outcomes = []
    for job in jobs:
        path = OUT / (job['name'] + '.json')
        if path.exists():
            obs = read(path)
        else:
            start = time.monotonic()
            obs = {'name': job['name'], 'expected_target_upgrade': job['expected_target_upgrade']}
            try:
                with case_deadline_ctx(config['case_timeout_seconds']):
                    result, status = run_causal_upgrade(packet=build_packet(copy.deepcopy(job['record'])),
                        candidates=job['candidates'], config=config)
                obs.update(result=result, status=status,
                    target_upgraded=any(c['candidate_id'] == job['target_candidate']
                        for c in result.get('upgraded_candidates', [])))
                obs['passed'] = bool(result.get('ok')) and obs['target_upgraded'] == job['expected_target_upgrade']
            except Exception as exc:
                obs.update(error=f'{type(exc).__name__}: {exc}', passed=False)
            obs['elapsed_seconds'] = round(time.monotonic() - start, 2)
            save(path, obs)
        compact = {k: v for k, v in obs.items() if k != 'result'}
        outcomes.append(compact)
        save(OUT / 'summary.json', {'complete': len(outcomes) == len(jobs), 'outcomes': outcomes})
        print(json.dumps(compact, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
