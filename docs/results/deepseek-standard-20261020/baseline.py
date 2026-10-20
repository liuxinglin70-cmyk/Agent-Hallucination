"""Frozen, independent AgentHallu standard-prompt measurement; no detector pipeline."""
from __future__ import annotations
import concurrent.futures as cf
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
DATA = ROOT / 'datasets/raw/agenthallu/AgentHallu'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT / 'author'))
from Lib.utils import format_step_block, result_revise, loads_json_lenient

LOCK = threading.RLock()
STOP = threading.Event()

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)

def event(value):
    with LOCK:
        with (OUT / 'progress.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': now(), **value}, ensure_ascii=False) + '\n')

def parse(content):
    obj = json.loads(loads_json_lenient(result_revise(content.strip())))
    if not isinstance(obj, dict) or not all(k in obj for k in ('is_hallucination', 'hallucination_step', 'hallucination_reason')):
        raise ValueError('missing author-required fields')
    # Do not repair meanings or enforce a stronger semantic contract than the author.
    if obj['is_hallucination'] not in (True, False, 'true', 'false'):
        raise ValueError('label not evaluable by author scorer')
    step = obj['hallucination_step']
    if step is not None and not isinstance(step, int):
        int(step)  # matches author step_to_int; retain original field
    return obj

def prepare():
    from transformers import AutoTokenizer
    from Lib.model_client.deepseek_inference import DeepSeekInferenceModel
    from Lib.model_client.prompts import ALL_AT_ONCE_USER_PROMPT, ALL_AT_ONCE_FORMAT_PROMPT
    if (OUT / 'freeze.json').exists():
        verify()
        print('Already frozen', flush=True)
        return
    model = DeepSeekInferenceModel.__new__(DeepSeekInferenceModel)
    model.tokenizer = AutoTokenizer.from_pretrained(OUT / 'author/tokenizer', local_files_only=True)
    model.token_limit, model.min_resp_tok = 98304, 20000
    model.method = 'all_at_once'
    model.USER_PROMPT, model.FORMAT_PROMPT = ALL_AT_ONCE_USER_PROMPT, ALL_AT_ONCE_FORMAT_PROMPT
    config = read(ROOT / 'detector/configs/autodl.json')['provider']
    cases = read(ROOT / 'artifacts/detector/live_checks/full693_20261020/full693-cases.json')
    assert len(cases) == len(set(cases)) == 693
    manifest = []
    for case in cases:
        raw = read(DATA / case)
        # This projection is the only data passed to author prompt construction.
        question, history = raw.get('question', ''), raw.get('history', [])
        steps = ['Step ' + str(e.get('step')) + ': ' + format_step_block(e.get('step'), e.get('content'), e.get('tool_calls', []), e.get('tool_responses', [])) for e in history]
        user, system = model.get_user_prompt(question, '\n'.join(steps))
        messages = model.chat_messages(user, system)
        before = model._token_len(model.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True))
        if before > model.token_limit:
            text = model.truncate_text_by_tokens(steps, before - model.token_limit + 1024)
            user, system = model.get_user_prompt(question, text)
            messages = model.chat_messages(user, system)
        after = model._token_len(model.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True))
        body = {'model': config['model'], 'messages': messages, 'temperature': 0.0,
                'max_tokens': 1024, 'thinking': {'type': 'disabled'}}
        req = OUT / 'requests' / case
        save(req, body)
        projected = json.dumps({'question': question, 'history': history}, ensure_ascii=False, sort_keys=True).encode('utf-8')
        manifest.append({'case': case, 'raw_sha256': sha(DATA / case), 'input_projection_sha256': hashlib.sha256(projected).hexdigest(),
                         'request_sha256': sha(req), 'tokens_before': before, 'tokens_after': after,
                         'author_truncation_triggered': before > model.token_limit, 'still_over_author_limit': after > model.token_limit})
    save(OUT / 'cases.json', manifest)
    policy = {'schema': 'paper-standard.v1', 'created': now(), 'authorization': 'User explicitly requests current DeepSeek, paper standard prompt, concurrency64',
              'method': 'all_at_once', 'cases': 693, 'concurrency': 64, 'endpoint': config['endpoint'], 'api_key_env': config['api_key_env'],
              'model': config['model'], 'temperature': 0, 'max_tokens': 1024, 'thinking': 'disabled',
              'timeout_seconds_per_request': 900, 'transport_attempts': 5, 'json_generation_attempts': 3,
              'retries': 'identical frozen payload only; transport max5 per generation, JSON parsing max3; no gold/semantic retries',
              'source': 'https://github.com/liuxuannan/AgentHallu', 'source_commit': (OUT/'author/commit.txt').read_text().strip(),
              'paper': 'https://arxiv.org/html/2601.06818v1', 'gold_sent': False,
              'metrics': 'author evaluate_metric.py; raw source-step exact match on all443 positives; missing predictions counted wrong',
              'adaptations': ['Official all_at_once passes response_format but DeepSeek.generate rejects that keyword; thin raw HTTP adapter preserves exact messages without JSON mode',
                              'max_tokens=1024 maps paper cap to supported provider parameter; no prompt repair',
                              'thinking disabled explicitly so temperature0 is effective; detector comparison had default thinking',
                              'Original duplicated prompt-token counter not reused; raw usage and all attempts retained',
                              'Original truncation algorithm and pinned DeepSeek-V3.1 tokenizer retained, including any residual over-limit prompts'],
              'geval': 'not run: separate GPT-5 explanation judge, outside DeepSeek-only inference',
              'no_tuning': True, 'probe': 'first frozen case, counts toward693; compatibility only, no gold read by runner'}
    save(OUT / 'policy.json', policy)
    pinned = ['baseline.py', 'cases.json', 'policy.json'] + [p.relative_to(OUT).as_posix() for p in (OUT/'author').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    save(OUT / 'freeze.json', {'created': now(), 'git_head': os.popen('git rev-parse HEAD').read().strip(),
                              'files': {p: sha(OUT/p) for p in sorted(pinned)}, 'cases': manifest})
    print(json.dumps({'prepared': len(cases), 'truncated': sum(x['author_truncation_triggered'] for x in manifest), 'residual_over_limit': sum(x['still_over_author_limit'] for x in manifest), 'max_tokens_after': max(x['tokens_after'] for x in manifest)}), flush=True)

def verify():
    freeze = read(OUT / 'freeze.json')
    for p, digest in freeze['files'].items():
        assert sha(OUT/p) == digest, 'changed frozen file: ' + p
    for row in freeze['cases']:
        assert sha(DATA/row['case']) == row['raw_sha256'], 'changed input: ' + row['case']
        assert sha(OUT/'requests'/row['case']) == row['request_sha256'], 'changed request: ' + row['case']
    return True

def halt(reason, case):
    with LOCK:
        if not STOP.is_set():
            save(OUT/'attention.json', {'time': now(), 'reason': reason, 'case': case, 'action': 'stop dispatch; preserve inflight attempts; no automatic restart'})
        STOP.set()

def run_case(row, policy, key, probe=False):
    from detector.provider import _isolated_transport
    case = row['case']
    dest = OUT/'cases'/case
    if dest.exists():
        return read(dest)
    body = (OUT/'requests'/case).read_bytes()
    attempts_root = OUT/'attempts'/Path(case).with_suffix('')
    attempts_root.mkdir(parents=True, exist_ok=True)
    if any(attempts_root.iterdir()):
        halt('uncommitted prior attempts require explicit reconciliation; not replaying', case)
        return None
    started = time.monotonic()
    result = {'case': case, 'started': now(), 'state': 'unresolved', 'prediction': None, 'attempts': []}
    for gen in range(1, 4):
        if STOP.is_set():
            break
        response = None
        for transport in range(1, 6):
            if STOP.is_set():
                break
            attempt = f'{gen}-{transport}'
            path = attempts_root/attempt
            path.mkdir()
            rec = {'case': case, 'generation': gen, 'transport': transport, 'started': now(), 'request_sha256': row['request_sha256']}
            save(path/'started.json', rec)
            event({'event': 'request_started', **rec})
            t = time.monotonic()
            try:
                code, raw = _isolated_transport(url=policy['endpoint'].rstrip('/')+'/chat/completions',
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key}, body=body, timeout=policy['timeout_seconds_per_request'])
                (path/'response.bin').write_bytes(raw)
                rec['http_status'] = code
                response = json.loads(raw.decode('utf-8'))
                rec['usage'] = response.get('usage')
                rec['response_model'] = response.get('model')
                if code != 200:
                    raise ValueError('HTTP '+str(code)+': '+str(response.get('error', response))[:1200])
                choice = response['choices'][0]
                msg = choice['message']
                rec['finish_reason'] = choice.get('finish_reason')
                rec['reasoning_characters'] = len(msg.get('reasoning_content') or '')
                usage = rec['usage'] or {}
                reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0
                if rec['reasoning_characters'] or reasoning or (usage.get('completion_tokens') or 0) > 1024:
                    halt('provider does not honor nonthinking /1024 output protocol', case)
                    raise ValueError('protocol incompatibility')
                content = msg.get('content')
                if not isinstance(content, str) or not content.strip():
                    raise ValueError('empty message content')
                rec['content_characters'] = len(content)
                rec['transport_accepted'] = True
            except Exception as exc:
                rec['error'] = str(exc).replace(key, '[REDACTED]')[:1800]
                code = rec.get('http_status')
                if code in (400, 401, 402, 403, 404, 413, 422):
                    halt('provider fatal HTTP '+str(code), case)
            finally:
                rec['finished'] = now()
                rec['elapsed_seconds'] = time.monotonic()-t
                rec['usage_missing'] = not isinstance(rec.get('usage'), dict)
                save(path/'attempt.json', rec)
                result['attempts'].append(rec)
                event({'event': 'request_finished', **rec})
            if rec.get('transport_accepted'):
                break
            response = None
            if not STOP.is_set() and transport < 5:
                STOP.wait(min(2**transport, 30))
        if response is None or STOP.is_set():
            break
        try:
            obj = parse(response['choices'][0]['message']['content'])
            result.update(state='accepted', prediction=obj)
            break
        except Exception as exc:
            result['parse_errors'] = result.get('parse_errors', []) + [str(exc)]
            # Author retries JSON decoding failures only. A decoded object with
            # missing/unevaluable fields is retained as unresolved, not regenerated.
            try:
                json.loads(loads_json_lenient(result_revise(response['choices'][0]['message']['content'].strip())))
            except (ValueError, TypeError):
                continue
            break
    result.update(finished=now(), elapsed_seconds=time.monotonic()-started)
    save(dest, result)
    event({'event': 'case_committed', 'case': case, 'state': result['state'], 'attempts': len(result['attempts'])})
    return result

def status(state, inflight=0):
    committed = [read(p) for p in (OUT/'cases').rglob('*.json')] if (OUT/'cases').exists() else []
    save(OUT/'status.json', {'time': now(), 'state': state, 'pid': os.getpid(), 'committed': len(committed),
         'accepted': sum(x['state']=='accepted' for x in committed), 'total': 693, 'inflight': inflight,
         'requests_finished': sum(len(x['attempts']) for x in committed), 'stop_requested': STOP.is_set()})

def run():
    # Exclusive file creation prevents duplicate supervisors. Never trust a reused PID.
    fd = os.open(OUT/'runner.lock', os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(fd, json.dumps({'pid': os.getpid(), 'started': now()}).encode())
    os.close(fd)
    try:
        verify()
        if (OUT/'attention.json').exists():
            raise RuntimeError('attention.json exists; manual reconciliation required')
        from detector.env import load_local_env
        load_local_env()
        policy = read(OUT/'policy.json')
        key = os.environ[policy['api_key_env']]
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        rows = read(OUT/'cases.json')
        status('compatibility_probe')
        first = run_case(rows[0], policy, key, True)
        if not first or first['state'] != 'accepted' or STOP.is_set():
            halt('first frozen case compatibility probe failed', rows[0]['case'])
        else:
            event({'event': 'compatibility_probe_passed', 'case': rows[0]['case']})
            pending = iter([r for r in rows[1:] if not (OUT/'cases'/r['case']).exists()])
            with cf.ThreadPoolExecutor(max_workers=64) as pool:
                active = set()
                for _ in range(64):
                    row = next(pending, None)
                    if row is not None:
                        active.add(pool.submit(run_case, row, policy, key))
                last_verify = time.monotonic()
                while active:
                    done, active = cf.wait(active, timeout=15, return_when=cf.FIRST_COMPLETED)
                    for future in done:
                        try:
                            future.result()
                        except Exception as exc:
                            halt('worker exception: '+str(exc).replace(key, '[REDACTED]'), 'worker')
                    if time.monotonic()-last_verify >= 60:
                        try:
                            verify()
                        except Exception as exc:
                            halt(str(exc), 'identity')
                        last_verify = time.monotonic()
                    if not STOP.is_set():
                        for _ in done:
                            row = next(pending, None)
                            if row is not None:
                                active.add(pool.submit(run_case, row, policy, key))
                    status('running' if not STOP.is_set() else 'draining', len(active))
        verify()
        status('stopped' if STOP.is_set() else 'completed')
        if not STOP.is_set():
            report()
    finally:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        (OUT/'runner.lock').unlink(missing_ok=True)

def report():
    from evaluate_metric import evaluate_accuracy, binary_to_01, step_to_int
    from detector.localization import gold_step_mapping
    verify()
    manifest = read(OUT/'cases.json')
    rows, author_data = [], []
    for m in manifest:
        case = m['case']
        result = read(OUT/'cases'/case)
        raw = read(DATA/case)
        obj = result.get('prediction')
        original = dict(raw)
        if obj:
            original.update(is_hallucination_prediction=obj['is_hallucination'], hallucination_step_prediction=obj['hallucination_step'], hallucination_reason_prediction=obj['hallucination_reason'])
        save(OUT/'outputs'/case, original)
        author_data.append((case, original))
        pred = binary_to_01(obj['is_hallucination']) if obj else -1
        step = step_to_int(obj['hallucination_step']) if obj else -1
        rows.append({'case': case, 'gold': bool(raw['is_hallucination']), 'gold_raw_step': raw.get('hallucination_step'),
                     'prediction': None if pred==-1 else bool(pred), 'raw_step': None if step==-1 else step,
                     'canonical_step': gold_step_mapping(raw, step)['position'] if step!=-1 else None,
                     'gold_canonical_step': gold_step_mapping(raw, raw.get('hallucination_step'))['position'],
                     'state': result['state'], 'correct': pred==int(raw['is_hallucination']),
                     'localization_correct': bool(raw['is_hallucination']) and step==step_to_int(raw.get('hallucination_step'))})
    metrics, categories = evaluate_accuracy(author_data)
    def plain(x):
        if isinstance(x, dict): return {k: plain(v) for k,v in x.items()}
        if isinstance(x, (list, tuple)): return [plain(v) for v in x]
        if hasattr(x, 'tolist'): return x.tolist()
        if hasattr(x, 'item'): return x.item()
        return x
    family = {}
    for name in sorted({r['case'].split('/')[0] for r in rows}):
        group = [(c,r) for c,r in author_data if c.startswith(name+'/')]
        family[name] = plain(evaluate_accuracy(group)[0])
    confusion = {k: 0 for k in ('tp','fp','fn','tn','missing')}
    for r in rows:
        key = 'missing' if r['prediction'] is None else 'tp' if r['gold'] and r['prediction'] else 'fn' if r['gold'] else 'fp' if r['prediction'] else 'tn'
        confusion[key] += 1
    evaluation = {'created': now(), 'metrics': plain(metrics), 'confusion': confusion, 'accepted': sum(r['state']=='accepted' for r in rows),
                  'localization_hits': sum(r['localization_correct'] for r in rows), 'positive_cases': sum(r['gold'] for r in rows),
                  'by_family': family, 'by_category': plain(categories), 'rows': rows}
    save(OUT/'final-evaluation.json', evaluation)
    framework = {r['case']: r for r in read(ROOT/'docs/results/full693-20261020/final-evaluation.json')['rows']}
    paired = []
    for r in rows:
        f = framework[r['case']]
        if f['authority'] != 'authoritative' or r['state'] != 'accepted': continue
        raw = read(DATA/r['case'])
        position = f['responsible_step']
        framework_raw = raw['history'][position-1].get('step') if f.get('localization_authority')=='authoritative' and isinstance(position,int) and 1<=position<=len(raw['history']) else None
        paired.append({'case':r['case'], 'gold':r['gold'], 'direct_correct':r['correct'], 'framework_correct':f['prediction']==r['gold'],
                       'direct_localization':r['localization_correct'], 'framework_localization':r['gold'] and framework_raw is not None and framework_raw==r['gold_raw_step'],
                       'framework_raw_step':framework_raw, 'direct_raw_step':r['raw_step']})
    def pair_summary(group, before, after):
        return {'n':len(group),'direct_correct':sum(r[before] for r in group),'framework_correct':sum(r[after] for r in group),
                'framework_fixes':sum(not r[before] and r[after] for r in group), 'framework_regressions':sum(r[before] and not r[after] for r in group)}
    paired_report = {'direction':'direct standard -> full detector', 'detection':pair_summary(paired,'direct_correct','framework_correct'),
                     'raw_step_localization':pair_summary([r for r in paired if r['gold']],'direct_localization','framework_localization'),
                     'limitations':['Same current model identifier, different reasoning mode, prompts, generation budgets, multi-call mechanisms and input truncation; does not isolate architecture alone'], 'rows':paired}
    save(OUT/'paired-comparison.json', paired_report)
    attempts = [read(p) for p in (OUT/'attempts').rglob('attempt.json')]
    totals = {k:sum((r.get('usage') or {}).get(k,0) or 0 for r in attempts) for k in ('prompt_tokens','completion_tokens','total_tokens')}
    costs = {'attempts':len(attempts),'usage_present':sum(isinstance(r.get('usage'),dict) for r in attempts),
             'missing_usage':sum(not isinstance(r.get('usage'),dict) for r in attempts), 'known_tokens_only':totals,
             'http_failures':sum(r.get('http_status')!=200 for r in attempts), 'length_finishes':sum(r.get('finish_reason')=='length' for r in attempts),
             'sum_request_seconds':sum(r['elapsed_seconds'] for r in attempts), 'wall_seconds':(datetime.fromisoformat(max(r['finished'] for r in attempts))-datetime.fromisoformat(min(r['started'] for r in attempts))).total_seconds(),
             'currency_cost':'not inferred: AutoDL billed rate and cache discounts not verified; unknown usage is not zero', 'rows':attempts}
    save(OUT/'all-attempt-costs.json', costs)
    audit = {'created':now(),'frozen_identity_matches':verify(),'cases':len(rows),'unique_cases':len({r['case'] for r in rows}),
             'all_case_commits_present':len(rows)==693,'accepted':evaluation['accepted'], 'unresolved':[r['case'] for r in rows if r['state']!='accepted'],
             'gold_positive':evaluation['positive_cases'],'attempts':len(attempts),'missing_usage':costs['missing_usage'],
             'author_commit':read(OUT/'policy.json')['source_commit'],'max_concurrency':64,
             'requests_payload_only_question_history':True, 'geval_performed':False}
    save(OUT/'completion-audit.json', audit)
    m = evaluation['metrics']; d=paired_report['detection']; l=paired_report['raw_step_localization']
    text = f'''# DeepSeek论文标准提示全693测量\n\n论文：[AgentHallu](https://arxiv.org/html/2601.06818v1)。作者实现固定提交 `{audit['author_commit']}`。当前DeepSeek请求模型`DeepSeek-V4.1-Flash-1017`，64请求并发；temperature=0、max_tokens=1024、显式关闭thinking，完整轨迹all_at_once。\n\n## 结果\n\n- 有效解析 {evaluation['accepted']}/693；准确率 {m['accuracy']:.6%}；macro-F1 {m['f1_score']:.6%}；macro-recall {m['recall']:.6%}。\n- 首错定位严格原始step命中 {evaluation['localization_hits']}/443={m['steps_conditional_accuracy']:.6%}，分母为全部金标阳性。\n- TP/FP/FN/TN：{confusion['tp']}/{confusion['fp']}/{confusion['fn']}/{confusion['tn']}；缺失预测{confusion['missing']}。\n\n## 同案比较\n\n共同可评检测{d['n']}例：直接提示{d['direct_correct']}正确→完整框架{d['framework_correct']}正确，框架修正{d['framework_fixes']}、回退{d['framework_regressions']}。共同阳性{l['n']}例按原始step比较：{l['direct_correct']}→{l['framework_correct']}，修正{l['framework_fixes']}、回退{l['framework_regressions']}。完整框架原报告使用canonical事件位置，另行映射为原step才能公平比较。\n\n论文标准GPT-5：accuracy70.6%、macro-F1 70.2%、定位32.7%；Gemini2.5Pro：68.8%、64.6%、41.1%。论文数字是跨时期模型参照，不能用此差值证明框架收益。本轮同底座但thinking、调用次数、预算和截断不同，同案差值仍不单独隔离框架因果收益。\n\n## 协议与适配\n\n直接导入作者提示、格式化、长输入截断及宽容解析函数。作者DeepSeek入口不接受上层传入的response_format，故独立HTTP适配器发送原消息，无JSON模式，无检测器证据链/提示修复。使用论文输出1024上限；显式关闭默认thinking使temperature0生效。作者输入token计数翻倍错误未沿用，使用原始usage。最多3次技术JSON生成、每次最多5次传输；固定同一请求，不按正确性补测。金标只由离线评分读取，全部请求事先冻结。保留作者截断算法的残余超限行为，详见cases.json。\n\nG-EVAL需要另一个GPT-5评审，本轮未执行，不把首错定位或生成解释当作G-EVAL分数。\n\n## 全部尝试成本\n\n请求{costs['attempts']}次，HTTP/传输失败{costs['http_failures']}，length结束{costs['length_finishes']}，缺usage{costs['missing_usage']}次。已知input/output/total token={totals['prompt_tokens']}/{totals['completion_tokens']}/{totals['total_tokens']}；耗时{costs['wall_seconds']:.1f}秒，逐请求累计{costs['sum_request_seconds']:.1f}秒。供应商收费及缓存折扣未核实，不编造金额，缺usage不按0计。\n\n原件：`artifacts/paper_baselines/deepseek_standard_20261020/`；协议policy.json、freeze.json，逐例请求requests/、逐尝试attempts/、案例commit cases/及作者兼容输出outputs/；逐例评分final-evaluation.json、同案paired-comparison.json、终验completion-audit.json及全部成本all-attempt-costs.json。\n'''
    (OUT/'report.md').write_text(text, encoding='utf-8')
    print(json.dumps({'completed':audit,'metrics':plain(metrics),'paired':d},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'prepare':prepare,'run':run,'report':report,'verify':verify}[sys.argv[1]]()
