"""Bounded live capacity probe; no retries and no dataset access."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--levels', default='1,4,8,16,32,64,128')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--words', type=int, default=0)
    parser.add_argument('--max-tokens', type=int, default=128)
    parser.add_argument('--items', type=int, default=0)
    args = parser.parse_args()
    levels = [int(n) for n in args.levels.split(',')]
    if not levels or min(levels) < 1 or max(levels) > 256 or not 1 <= args.rounds <= 10:
        parser.error('levels must be 1..256 and rounds 1..10')
    config = load_config()
    route = config['judge_route']
    url = route['endpoint'].rstrip('/') + '/chat/completions'
    key = os.environ[route['api_key_env']]
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'endpoint': route['endpoint'], 'model': route['model'],
              'levels': levels, 'rounds': args.rounds, 'words': args.words,
              'max_tokens': args.max_tokens, 'items': args.items, 'retries': 0, 'stages': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def request(index, barrier):
        barrier.wait()
        start = time.perf_counter()
        prompt = ('Read this context: ' + 'capacity measurement context. ' * args.words + '\n') if args.words else ''
        prompt += f'Request {index}. Return only JSON: {{"ok":true}}'
        expected = {'ok': True}
        if args.items:
            expected['values'] = list(range(args.items))
            prompt = prompt.replace('Return only JSON: {"ok":true}',
                                    f'Return JSON with ok=true and values containing all integers from 0 to {args.items - 1}, inclusive. No other keys.')
        payload = {'model': route['model'], 'messages': [{'role': 'user', 'content': prompt}],
                   'temperature': 0, 'stream': False, 'max_tokens': args.max_tokens,
                   'response_format': {'type': 'json_object'}}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        row = {'index': index}
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                data = json.load(response)
                choice = data['choices'][0]
                content = choice['message'].get('content', '')
                row.update(status=response.status, model=data.get('model'),
                           usage=data.get('usage'), finish_reason=choice.get('finish_reason'),
                           content=content, headers={k: v for k, v in response.headers.items()
                           if any(s in k.lower() for s in ('ratelimit', 'rate-limit', 'retry-after'))})
                row['ok'] = choice.get('finish_reason') == 'stop' and json.loads(content) == expected
        except urllib.error.HTTPError as error:
            row.update(status=error.code, ok=False, error=error.read().decode(errors='replace')[:500],
                       headers={k: v for k, v in error.headers.items()
                                if any(s in k.lower() for s in ('limit', 'retry-after'))})
        except Exception as error:
            row.update(status=type(error).__name__, ok=False, error=str(error)[:300])
        row['seconds'] = time.perf_counter() - start
        return row

    for level in levels:
        rows = []
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as pool:
            for repeat in range(args.rounds):
                barrier = threading.Barrier(level)
                futures = [pool.submit(request, repeat * level + i, barrier) for i in range(level)]
                rows.extend(f.result() for f in futures)
        duration = time.perf_counter() - start
        latencies = sorted(r['seconds'] for r in rows)
        good = [r for r in rows if r['ok']]
        summary = {'concurrency': level, 'requests': len(rows), 'success': len(good),
                   'seconds': duration, 'successful_rps': len(good) / duration,
                   'p50_seconds': latencies[math.ceil(.5 * len(rows)) - 1],
                   'p95_seconds': latencies[math.ceil(.95 * len(rows)) - 1],
                   'statuses': dict(Counter(str(r['status']) for r in rows)),
                   'tokens': sum((r.get('usage') or {}).get('total_tokens', 0) for r in rows)}
        report['stages'].append({'summary': summary, 'requests': rows})
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(summary), flush=True)
        if len(good) != len(rows):
            print('Stopped escalation after unsuccessful response.', flush=True)
            break


if __name__ == '__main__':
    main()
