"""Repeatable two-arm diagnostic with mechanically retrieved evidence.

Unlike the earlier per-case diagnostic, this one takes its snapshots from
``detector.retrieval``: the only inputs are the case question and a frozen
corpus, so nothing is hand-picked per case. It runs both arms over every
listed case, reports the correction path, and is safe to re-run (results are
keyed and reused).

Usage::

    python reports/verification_20260917/two_arm_retrieved.py [--reps N]
"""

from __future__ import annotations

import argparse
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
from detector.retrieval import build_case_snapshot, load_corpus
from evolution.backends import LiveDetectorBackend, source_digest

OUT = Path(__file__).resolve().parent / 'two_arm_retrieved'
CORPUS = Path(__file__).resolve().parent / 'corpus'
BASELINE_ARM = ROOT / 'artifacts/evolution_real/checkpoints/cycle001/arms/508acdf65658'

# Target: an external-fact failure the corpus covers.
TARGETS = ['OpenDeepSearch/086.json', 'OpenDeepSearch/063.json']
# Controls: authoritative clean cases with no bearing on the corpus.
CONTROLS = ['Magentic_One/036.json', 'OpenDeepSearch/002.json']
# A case the corpus does not cover, to show a coverage gap is reported and
# not papered over (its own arm must abstain from evidence).
UNCOVERED = ['OpenDeepSearch/035.json']


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def compact(result) -> dict:
    proof = (result or {}).get('proof') or {}
    rows = proof.get('causal_upgrade_rows') or []
    hearings = []
    for hearing in proof.get('hearings') or []:
        defense = (hearing.get('defense_result') or {}).get('parsed') or {}
        hearings.append({
            'source': hearing.get('source'),
            'candidate_id': hearing.get('candidate_id'),
            'verdict': defense.get('verdict'),
            'confirmed_step': defense.get('confirmed_step'),
            'evidence_citations': defense.get('evidence_citations'),
            'reason': str(defense.get('reason') or '')[:400],
        })
    return {
        'state': (result or {}).get('state'),
        'authority': (result or {}).get('decision_authority'),
        'responsible_step': (result or {}).get('responsible_step'),
        'decisive_facts': (proof.get('terminal_basis') or {}).get(
            'decisive_facts', {}
        ),
        'candidates': [
            {
                'candidate_id': c.get('candidate_id'),
                'claim': str(c.get('claim') or '')[:260],
                'locus_event_id': c.get('locus_event_id'),
                'citations': c.get('evidence_citations'),
            }
            for c in proof.get('recall_candidates') or []
        ],
        'upgrade_rows': [
            {
                'candidate_id': row.get('candidate_id'),
                'content_error': row.get('content_error'),
                'causal': row.get('causal'),
                'corrected_step': row.get('corrected_step'),
                'citations': row.get('evidence_refs'),
                'conflict_note': str(row.get('conflict_note') or '')[:300],
                'reason': str(row.get('reason') or '')[:300],
            }
            for row in rows
        ],
        'positive_candidates': [
            row.get('candidate_id') for row in rows
            if row.get('content_error') is True and row.get('causal') is True
        ],
        'hearings': hearings,
        'stages': {
            key: {
                'state': value.get('state'),
                'attempts': value.get('attempts'),
            }
            for key, value in ((result or {}).get('stage_status') or {}).items()
        },
        'provenance': {
            key: (result or {}).get('provenance', {}).get(key)
            for key in (
                'packet_hash', 'evidence_snapshot_digest', 'evidence_applied',
                'evidence_application_reason', 'frozen_judge_replay',
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reps', type=int, default=2)
    parser.add_argument('--control-reps', type=int, default=1)
    args = parser.parse_args()

    corpus = load_corpus(CORPUS)
    baselines = {}
    for path in BASELINE_ARM.glob('*.json'):
        row = read(path)['row']
        baselines[row['case']] = row

    cases = TARGETS + CONTROLS + UNCOVERED
    missing = [case for case in cases if case not in baselines]
    if missing:
        raise SystemExit(f'baseline arm lacks rows: {missing}')

    # Retrieval is a pure function of (question, corpus): build each once,
    # record the decision, and pass the same snapshot to every run.
    snapshots: dict[str, dict | None] = {}
    retrieval: dict[str, dict] = {}
    for case in cases:
        record, _ = load_case(ROOT / 'datasets/raw/agenthallu/AgentHallu', case)
        question = record['question']
        if isinstance(question, list):
            question = ' '.join(
                part for turn in question for part in (
                    turn if isinstance(turn, list) else [turn]
                )
            )
        snapshot, report = build_case_snapshot(corpus, str(question))
        snapshots[case] = snapshot
        retrieval[case] = {
            key: value for key, value in report.items() if key != 'query_terms'
        }
        retrieval[case]['query_terms'] = report['query_terms'][:16]
        retrieval[case]['snapshot_digest'] = (
            hashlib.sha256(
                json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16] if snapshot else None
        )

    config = load_config(ROOT / 'detector/configs/autodl.json')
    manifest = {
        'kind': 'two-arm diagnostic with mechanically retrieved evidence',
        'retrieval': 'detector.retrieval over a frozen corpus; label-free',
        'corpus': str(CORPUS.relative_to(ROOT)),
        'targets': TARGETS,
        'controls': CONTROLS,
        'uncovered': UNCOVERED,
        'repetitions': {'target': args.reps, 'control': args.control_reps},
        'freeze': 'judge result frozen from the recorded baseline for both arms',
        'retrieval_report': retrieval,
        'source_digest': source_digest(),
        'threshold_changes': False,
        'criteria': (
            'A target correction counts only when the run is authoritative, '
            'the responsible step equals the labelled step, and the finding '
            'cites the retrieved evidence. Controls must stay clean. An '
            'uncovered case must produce no snapshot and stay at its '
            'baseline behaviour. No automatic admission.'
        ),
    }
    manifest_path = OUT / 'protocol.json'
    if manifest_path.exists() and read(manifest_path) != manifest:
        raise SystemExit('frozen protocol differs; refusing to mix observations')
    save(manifest_path, manifest)
    save(OUT / 'retrieval.json', retrieval)

    def run(case: str, arm: str, rep: int) -> dict:
        target = OUT / 'runs' / f'{case.replace("/", "__")}.{arm}.rep{rep}.json'
        if target.exists():
            return read(target)
        row = baselines[case]
        envelope = LiveDetectorBackend._frozen_stages(row['result'], set())
        frozen = {
            key: value for key, value in envelope.items()
            if key.startswith('judge_') or key == 'packet_hash'
        }
        record, _ = load_case(ROOT / 'datasets/raw/agenthallu/AgentHallu', case)
        snapshot = snapshots[case] if arm == 'B_evidence' else None
        observed = {
            'case': case, 'arm': arm, 'rep': rep,
            'gold': row['gold'],
            'baseline_prediction': row['prediction'],
            'snapshot_digest': retrieval[case]['snapshot_digest'] if snapshot else None,
        }
        start = time.monotonic()
        try:
            result = Detector(config).detect(
                record, case_id=case, frozen_stages=frozen, evidence=snapshot,
            )
            observed['result'] = result
            observed['prediction'] = prediction_state(
                result, require_authoritative=True,
            )
            observed['summary'] = compact(result)
        except Exception as exc:
            observed['error'] = f'{type(exc).__name__}: {exc}'
        observed['elapsed_seconds'] = round(time.monotonic() - start, 2)
        save(target, observed)
        return observed

    jobs = []
    for case in cases:
        reps = args.reps if case in TARGETS else args.control_reps
        if case in UNCOVERED:
            reps = args.control_reps
        for arm in ('A_no_evidence', 'B_evidence'):
            for rep in range(1, reps + 1):
                jobs.append((case, arm, rep))

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, *job) for job in jobs]
        for future in as_completed(futures):
            observed = future.result()
            outcomes.append({
                key: value for key, value in observed.items() if key != 'result'
            })
            summary = observed.get('summary') or {}
            save(OUT / 'summary.json', {
                'protocol': 'protocol.json',
                'retrieval': 'retrieval.json',
                'outcomes': sorted(
                    outcomes,
                    key=lambda item: (item['case'], item['arm'], item['rep']),
                ),
                'complete': len(outcomes) == len(jobs),
            })
            print(json.dumps({
                'case': observed['case'], 'arm': observed['arm'],
                'rep': observed['rep'],
                'state': summary.get('state'),
                'authority': summary.get('authority'),
                'step': summary.get('responsible_step'),
                'prediction': observed.get('prediction'),
                'evidence_applied': summary.get('provenance', {}).get(
                    'evidence_applied'
                ),
                'error': observed.get('error'),
                'elapsed': observed.get('elapsed_seconds'),
            }, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
