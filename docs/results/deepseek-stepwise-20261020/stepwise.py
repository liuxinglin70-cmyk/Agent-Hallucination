"""Independent frozen AgentHallu step-by-step measurement. No detector inference."""
from __future__ import annotations
import concurrent.futures as cf
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
STANDARD=ROOT/'artifacts/paper_baselines/deepseek_standard_20261020'
spec=importlib.util.spec_from_file_location('frozen_standard_helpers',STANDARD/'baseline.py')
base=importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
read,save,sha=base.read,base.save,base.sha
DATA=base.DATA
from Lib.utils import format_step_block, result_revise, loads_json_lenient

LOCK=threading.RLock()
STOP=threading.Event()

def now(): return datetime.now(timezone.utc).isoformat()

def event(value):
    with LOCK:
        with (OUT/'progress.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':now(),**value},ensure_ascii=False)+'\n')

def decode(content):
    return json.loads(loads_json_lenient(result_revise(content.strip())))

def decision(obj, final, source_step):
    # SOURCE: pinned Lib/api_model.py step_by_step_api; strict booleans, current step.
    if isinstance(obj,dict) and 'is_hallucination' in obj:
        if obj['is_hallucination'] is True:
            return {'is_hallucination':True,'hallucination_step':source_step,'hallucination_reason':obj.get('hallucination_reason')}
        if obj['is_hallucination'] is False and final:
            return {'is_hallucination':False,'hallucination_step':None,'hallucination_reason':None}
    return None

def prepare():
    if (OUT/'freeze.json').exists():
        verify(); print('Already frozen',flush=True); return
    base.verify()
    from transformers import AutoTokenizer
    from Lib.model_client.deepseek_inference import DeepSeekInferenceModel
    from Lib.model_client.prompts import STEP_BY_STEP_USER_PROMPT, STEP_BY_STEP_FORMAT_PROMPT
    model=DeepSeekInferenceModel.__new__(DeepSeekInferenceModel)
    model.tokenizer=AutoTokenizer.from_pretrained(STANDARD/'author/tokenizer',local_files_only=True)
    model.method='step_by_step'
    model.USER_PROMPT,model.FORMAT_PROMPT=STEP_BY_STEP_USER_PROMPT,STEP_BY_STEP_FORMAT_PROMPT
    model.token_limit,model.min_resp_tok=98304,20000
    previous=read(STANDARD/'policy.json')
    plan=[]
    for original in read(STANDARD/'cases.json'):
        case=original['case']
        raw=read(DATA/case)
        question,history=raw.get('question',''),raw.get('history',[])
        assert history,case
        steps=[]
        row={'case':case,'raw_sha256':sha(DATA/case),'input_projection_sha256':original['input_projection_sha256'],'prefixes':[]}
        for index,entry in enumerate(history,1):
            steps.append('Step '+str(entry.get('step'))+': '+format_step_block(entry.get('step'),entry.get('content'),entry.get('tool_calls',[]),entry.get('tool_responses',[])))
            user,system=model.get_user_prompt(question,'\n'.join(steps))
            messages=model.chat_messages(user,system)
            before=model._token_len(model.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True))
            if before>model.token_limit:
                truncated=model.truncate_text_by_tokens(steps,before-model.token_limit+1024)
                user,system=model.get_user_prompt(question,truncated)
                messages=model.chat_messages(user,system)
            after=model._token_len(model.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)) if before>model.token_limit else before
            relative=(Path('requests')/Path(case).with_suffix('')/f'{index:04}.json').as_posix()
            save(OUT/relative,{'model':previous['model'],'messages':messages,'temperature':0.0,'max_tokens':1024,'thinking':{'type':'disabled'}})
            row['prefixes'].append({'index':index,'source_step':entry.get('step'),'request_path':relative,'request_sha256':sha(OUT/relative),
                                    'tokens_before':before,'tokens_after':after,'author_truncation_triggered':before>98304,'residual_over_limit':after>98304})
        plan.append(row)
        if len(plan)%100==0: print(json.dumps({'prepared_cases':len(plan),'possible_prefixes':sum(len(r['prefixes']) for r in plan)}),flush=True)
    assert len(plan)==693
    save(OUT/'cases.json',plan)
    policy={'schema':'paper.step_by_step.v1','created':now(),'authorization':'User requests continue original paper stepwise detection and aggregate results; retains preceding current DeepSeek64concurrency setup',
            'source_commit':previous['source_commit'],'paper':previous['paper'],'method':'step_by_step','cases':693,'possible_prefixes':sum(len(r['prefixes']) for r in plan),
            'concurrency':64,'model':previous['model'],'endpoint':previous['endpoint'],'api_key_env':previous['api_key_env'],
            'temperature':0,'max_tokens':1024,'thinking':'disabled','timeout_seconds_per_request':900,'transport_attempts':5,'json_generation_attempts':3,
            'control':'Cumulative question+prefix only; first strict boolean True stops case and selects CURRENT raw source step; final strict False yields clean. Invalid intermediate JSON/fields advances as upstream. Invalid final step unresolved, never fabricated clean.',
            'retry':'Same frozen payload only: max5 transport tries per generation, max3 generations for JSON decode failures only; decoded missing fields are not regenerated',
            'gold_sent':False,'scoring':'Pinned author evaluate_metric.py, normalize mixed bool/string gold using binary_to_01; raw-step localization on all443 gold positives; missing predictions remain in693',
            'adaptations':previous['adaptations']+['Stepwise result requires only upstream is_hallucination key, strict booleans; no invented hallucination_step requirement',
                'Decoded non-object responses treated unusable, instead of crashing upstream .get; logged without extra semantic retries'],
            'geval':'Not run: separate GPT-5 evaluator outside current DeepSeek-only measurement','further_semantic_retries':False}
    save(OUT/'policy.json',policy)
    files=['stepwise.py','score.py','test_stepwise.py','preflight.json','cases.json','policy.json']
    save(OUT/'freeze.json',{'created':now(),'files':{p:sha(OUT/p) for p in files},'standard_freeze_sha256':sha(STANDARD/'freeze.json'),
                           'author_reference':str((STANDARD/'author').relative_to(ROOT)).replace('\\','/'),'cases':plan})
    allprefixes=[p for r in plan for p in r['prefixes']]
    print(json.dumps({'frozen_cases':len(plan),'prefixes':len(allprefixes),'truncations':sum(p['author_truncation_triggered'] for p in allprefixes),
                      'residual_over_limit':sum(p['residual_over_limit'] for p in allprefixes),'max_tokens_after':max(p['tokens_after'] for p in allprefixes)}),flush=True)

def verify():
    freeze=read(OUT/'freeze.json')
    assert sha(STANDARD/'freeze.json')==freeze['standard_freeze_sha256']
    base.verify()
    for p,digest in freeze['files'].items(): assert sha(OUT/p)==digest,'frozen file changed: '+p
    for row in freeze['cases']:
        assert sha(DATA/row['case'])==row['raw_sha256'],'input changed: '+row['case']
        for prefix in row['prefixes']:
            assert sha(OUT/prefix['request_path'])==prefix['request_sha256'],'request changed: '+prefix['request_path']
    return True

def halt(reason,case):
    with LOCK:
        if not STOP.is_set(): save(OUT/'attention.json',{'time':now(),'reason':reason,'case':case,'action':'Stop new dispatch; preserve evidence; reconcile before restart'})
        STOP.set()

def query(case,prefix,policy,key):
    from detector.provider import _isolated_transport
    relative=Path(case).with_suffix('')/f"{prefix['index']:04}"
    commit=OUT/'prefixes'/relative.with_suffix('.json')
    if commit.exists(): return read(commit)
    root=OUT/'attempts'/relative
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):
        halt('uncommitted prior request requires reconciliation; not replaying',case)
        return None
    result={'case':case,'prefix_index':prefix['index'],'source_step':prefix['source_step'],'started':now(),'state':'unusable','decoded':None,'attempts':[]}
    body=(OUT/prefix['request_path']).read_bytes()
    for generation in range(1,4):
        if STOP.is_set(): break
        response=None
        for transport in range(1,6):
            if STOP.is_set(): break
            path=root/f'{generation}-{transport}'; path.mkdir()
            rec={'case':case,'prefix_index':prefix['index'],'generation':generation,'transport':transport,'started':now(),'request_sha256':prefix['request_sha256']}
            save(path/'started.json',rec); event({'event':'request_started',**rec}); start=time.monotonic()
            try:
                code,raw=_isolated_transport(url=policy['endpoint'].rstrip('/')+'/chat/completions',headers={'Content-Type':'application/json','Authorization':'Bearer '+key},body=body,timeout=policy['timeout_seconds_per_request'])
                (path/'response.bin').write_bytes(raw)
                rec['http_status']=code
                response=json.loads(raw.decode('utf-8')); rec['usage']=response.get('usage'); rec['response_model']=response.get('model')
                if code!=200: raise ValueError('HTTP '+str(code)+': '+str(response.get('error',response))[:1200])
                choice=response['choices'][0]; message=choice['message']; usage=rec['usage'] or {}
                rec['finish_reason']=choice.get('finish_reason'); rec['reasoning_characters']=len(message.get('reasoning_content') or '')
                reasoning=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0
                if rec['reasoning_characters'] or reasoning or (usage.get('completion_tokens') or 0)>1024:
                    halt('provider ignored nonthinking or1024 protocol',case); raise ValueError('protocol incompatibility')
                if not isinstance(message.get('content'),str) or not message['content'].strip(): raise ValueError('empty message content')
                rec['transport_accepted']=True
            except Exception as exc:
                rec['error']=str(exc).replace(key,'[REDACTED]')[:1800]
                if rec.get('http_status') in (400,401,402,403,404,413,422): halt('fatal provider HTTP '+str(rec['http_status']),case)
            finally:
                rec.update(finished=now(),elapsed_seconds=time.monotonic()-start,usage_missing=not isinstance(rec.get('usage'),dict))
                save(path/'attempt.json',rec); result['attempts'].append(rec); event({'event':'request_finished',**rec})
            if rec.get('transport_accepted'): break
            response=None
            if not STOP.is_set() and transport<5: STOP.wait(min(2**transport,30))
        if response is None or STOP.is_set(): break
        try:
            result['decoded']=decode(response['choices'][0]['message']['content'])
            result['state']='decoded'
            break
        except Exception as exc:
            result.setdefault('parse_errors',[]).append(str(exc))
    result['finished']=now()
    save(commit,result)
    event({'event':'prefix_committed','case':case,'prefix_index':prefix['index'],'state':result['state'],'attempts':len(result['attempts'])})
    return result

def run_case(row,policy,key):
    case=row['case']; path=OUT/'cases'/case
    if path.exists(): return read(path)
    result={'case':case,'started':now(),'state':'unresolved','prediction':None,'prefix_indices':[]}
    for prefix in row['prefixes']:
        if STOP.is_set(): return None
        record=query(case,prefix,policy,key)
        if record is None or STOP.is_set(): return None
        result['prefix_indices'].append(prefix['index'])
        answer=decision(record['decoded'],prefix['index']==len(row['prefixes']),prefix['source_step'])
        if answer is not None:
            result.update(prediction=answer,state='accepted',stop_index=prefix['index'],stop_rule='first_true' if answer['is_hallucination'] else 'final_false')
            break
    result['finished']=now()
    save(path,result); event({'event':'case_committed','case':case,'state':result['state'],'prefix_count':len(result['prefix_indices']),'stop_index':result.get('stop_index')})
    return result

def status(state,inflight=0):
    cases=[read(p) for p in (OUT/'cases').rglob('*.json')] if (OUT/'cases').exists() else []
    save(OUT/'status.json',{'time':now(),'state':state,'pid':os.getpid(),'total':693,'committed':len(cases),'accepted':sum(c['state']=='accepted' for c in cases),'inflight_cases':inflight,'stop_requested':STOP.is_set()})

def run():
    descriptor=os.open(OUT/'runner.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(descriptor,json.dumps({'pid':os.getpid(),'started':now()}).encode()); os.close(descriptor)
    try:
        verify()
        if (OUT/'attention.json').exists(): raise RuntimeError('attention requires reconciliation before restart')
        from detector.env import load_local_env
        load_local_env(); policy=read(OUT/'policy.json'); key=os.environ[policy['api_key_env']]
        if os.name=='nt': ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        rows=read(OUT/'cases.json'); status('compatibility_probe')
        first=query(rows[0]['case'],rows[0]['prefixes'][0],policy,key)
        if not first or first['state']!='decoded' or not isinstance(first['decoded'],dict) or type(first['decoded'].get('is_hallucination')) is not bool:
            halt('first frozen prefix incompatible',rows[0]['case'])
        if not STOP.is_set():
            event({'event':'compatibility_probe_passed','case':rows[0]['case'],'prefix_index':1})
            pending=iter([r for r in rows if not (OUT/'cases'/r['case']).exists()])
            with cf.ThreadPoolExecutor(max_workers=64) as pool:
                active=set()
                for _ in range(64):
                    row=next(pending,None)
                    if row is not None: active.add(pool.submit(run_case,row,policy,key))
                last_verify=time.monotonic()
                while active:
                    done,active=cf.wait(active,timeout=15,return_when=cf.FIRST_COMPLETED)
                    for f in done:
                        try: f.result()
                        except Exception as exc: halt('worker exception: '+str(exc).replace(key,'[REDACTED]'),'worker')
                    if time.monotonic()-last_verify>=60:
                        try: verify()
                        except Exception as exc: halt(str(exc),'identity')
                        last_verify=time.monotonic()
                    if not STOP.is_set():
                        for _ in done:
                            row=next(pending,None)
                            if row is not None: active.add(pool.submit(run_case,row,policy,key))
                    status('running' if not STOP.is_set() else 'draining',len(active))
        verify(); status('stopped' if STOP.is_set() else 'completed')
        if not STOP.is_set():
            import importlib
            sys.path.insert(0,str(OUT)); importlib.import_module('score').main()
    finally:
        if os.name=='nt': ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        (OUT/'runner.lock').unlink(missing_ok=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'prepare':prepare,'run':run,'verify':verify}[sys.argv[1]]()
