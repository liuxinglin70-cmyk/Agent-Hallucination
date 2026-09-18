"""Two-arm development diagnostic: no-evidence arm vs evidence arm.

Fixed upstream: the judge result is frozen from the recorded baseline for
every run of both arms, so the only difference between the arms is whether
the auditable external-evidence snapshot rides along. The judge never
receives the snapshot by construction; the snapshot reaches the recall scan,
the causal upgrade and the defense.

The Lisette snapshot was retrieved and human-verified during development.
This is a pipeline diagnostic, NOT a blind retrieval success: the snapshot
is fixed input, and the report must say so.

Usage::

    python reports/verification_20260917/two_arm_evidence.py [--reps N]
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
from detector.evidence import load_snapshot, snapshot_digest
from detector.result import prediction_state
from evolution.backends import LiveDetectorBackend, source_digest

OUT = Path(__file__).resolve().parent / 'two_arm_evidence'
SNAPSHOT = Path(__file__).resolve().parent / 'evidence_snapshot.json'
BASELINE_ARM = ROOT / 'artifacts/evolution_real/checkpoints/cycle001/arms/508acdf65658'

TARGET = 'OpenDeepSearch/086.json'
CONTROLS = ['Magentic_One/036.json', 'OpenDeepSearch/002.json']


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def compact(result) -> dict:
    """What a reader needs to judge one run, without the full provider noise."""
    proof = (result or {}).get('proof') or {}
    rows = proof.get('causal_upgrade_rows') or []
    positives = [
        row for row in rows
        if row.get('content_error') is True and row.get('causal') is True
    ]
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
        'terminal_basis': (proof.get('terminal_basis') or {}) if isinstance(
            proof.get('terminal_basis'), dict) else {},
        'candidates': [
            {
                'candidate_id': c.get('candidate_id'),
                'claim': str(c.get('claim') or '')[:300],
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
                'conflict_note': str(row.get('conflict_note') or '')[:400],
                'reason': str(row.get('reason') or '')[:400],
            }
            for row in rows
        ],
        'positive_candidates': [row.get('candidate_id') for row in positives],
        'hearings': hearings,
        'stages': {
            key: {
                'state': value.get('state'),
                'attempts': value.get('attempts'),
                'view': value.get('view'),
                'warnings': value.get('warnings'),
            }
            for key, value in ((result or {}).get('stage_status') or {}).items()
        },
        'provenance': {
            key: (result or {}).get('provenance', {}).get(key)
            for key in ('packet_hash', 'evidence_snapshot_digest',
                        'evidence_applied', 'evidence_application_reason',
                        'frozen_judge_replay', 'frozen_stages')
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reps', type=int, default=2,
                        help='repetitions for the target case')
    parser.add_argument('--control-reps', type=int, default=1,
                        help='repetitions for each normal control')
    args = parser.parse_args()

    snapshot = load_snapshot(SNAPSHOT)
    digest = snapshot_digest(snapshot)
    baselines = {}
    for path in BASELINE_ARM.glob('*.json'):
        row = read(path)['row']
        baselines[row['case']] = row

    cases = [TARGET, *CONTROLS]
    missing = [case for case in cases if case not in baselines]
    if missing:
        raise SystemExit(f'baseline arm lacks case rows: {missing}')

    config = load_config(ROOT / 'detector/configs/autodl.json')
    manifest = {
        'kind': 'two-arm development diagnostic of the external-evidence path',
        'not_a_blind_retrieval_success': (
            'The Lisette snapshot is fixed, human-verified input retrieved '
            'during development; it is not an independent blind search.'
        ),
        'target': TARGET,
        'controls': CONTROLS,
        'arms': {
            'A_no_evidence': 'evidence=None',
            'B_evidence': f'snapshot {digest}',
        },
        'freeze': 'judge result frozen from the recorded baseline for both arms',
        'repetitions': {
            'target': args.reps, 'control': args.control_reps,
        },
        'snapshot_digest': digest,
        'snapshot_file': str(SNAPSHOT.relative_to(ROOT)),
        'source_digest': source_digest(),
        'criteria': (
            'Report per arm: terminal state, authority, responsible step, '
            'the citations that carry the finding, the conflict note, stage '
            'health and elapsed time. A corrected target must be a gold=true '
            'case that turns authoritative verified_error at the labelled '
            'step; controls must stay clean. No automatic admission.'
        ),
        'threshold_changes': False,
    }
    manifest_path = OUT / 'protocol.json'
    if manifest_path.exists() and read(manifest_path) != manifest:
        raise SystemExit('frozen protocol differs; refusing to mix observations')
    save(manifest_path, manifest)

    def run(case: str, arm: str, rep: int) -> dict:
        target_path = OUT / 'runs' / f'{case.replace("/", "__")}.{arm}.rep{rep}.json'
        if target_path.exists():
            return read(target_path)
        row = baselines[case]
        envelope = LiveDetectorBackend._frozen_stages(row['result'], set())
        frozen = {
            key: value for key, value in envelope.items()
            if key.startswith('judge_') or key == 'packet_hash'
        }
        record, _ = load_case(ROOT / 'datasets/raw/agenthallu/AgentHallu', case)
        observed = {
            'case': case, 'arm': arm, 'rep': rep,
            'gold': row['gold'],
            'baseline_prediction': row['prediction'],
            'snapshot_digest': digest if arm == 'B_evidence' else None,
        }
        start = time.monotonic()
        try:
            result = Detector(config).detect(
                record, case_id=case,
                frozen_stages=frozen,
                evidence=snapshot if arm == 'B_evidence' else None,
            )
            observed['result'] = result
            observed['prediction'] = prediction_state(
                result, require_authoritative=True,
            )
            observed['summary'] = compact(result)
        except Exception as exc:  # a run failure is recorded, never hidden
            observed['error'] = f'{type(exc).__name__}: {exc}'
        observed['elapsed_seconds'] = round(time.monotonic() - start, 2)
        save(target_path, observed)
        return observed

    outcomes = []
    jobs = [
        (case, arm, rep)
        for case in cases
        for arm in ('A_no_evidence', 'B_evidence')
        for rep in range(1, (
            args.reps if case == TARGET else args.control_reps
        ) + 1)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, *job) for job in jobs]
        for future in as_completed(futures):
            observed = future.result()
            outcomes.append({
                key: value for key, value in observed.items() if key != 'result'
            })
            save(OUT / 'summary.json', {
                'protocol': 'protocol.json',
                'snapshot_digest': digest,
                'target': TARGET,
                'controls': CONTROLS,
                'outcomes': sorted(
                    outcomes,
                    key=lambda item: (item['case'], item['arm'], item['rep']),
                ),
                'complete': len(outcomes) == len(jobs),
            })
            print(json.dumps({
                'case': observed['case'], 'arm': observed['arm'],
                'rep': observed['rep'],
                'state': (observed.get('summary') or {}).get('state'),
                'authority': (observed.get('summary') or {}).get('authority'),
                'step': (observed.get('summary') or {}).get('responsible_step'),
                'prediction': observed.get('prediction'),
                'error': observed.get('error'),
                'elapsed': observed.get('elapsed_seconds'),
            }, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
