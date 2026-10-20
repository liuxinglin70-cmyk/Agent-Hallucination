"""Independent post-run evidence reconciliation; never sends requests."""
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('standard_measurement',OUT/'baseline.py')
b=importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
ROOT=b.ROOT
sys.stdout.reconfigure(encoding='utf-8')

def main():
    b.verify()
    status=b.read(OUT/'status.json')
    assert status['state']=='completed', status
    ev=[json.loads(line) for line in (OUT/'progress.jsonl').read_text(encoding='utf-8').splitlines()]
    starts=[x for x in ev if x['event']=='request_started']
    ends=[x for x in ev if x['event']=='request_finished']
    commits=[x for x in ev if x['event']=='case_committed']
    identity=lambda x:(x['case'],x['generation'],x['transport'])
    assert len(starts)==len(ends)==len(set(map(identity,starts)))==len(set(map(identity,ends)))
    assert set(map(identity,starts))==set(map(identity,ends))
    assert len(commits)==len({x['case'] for x in commits})==693
    intervals=sorted([(x['started'],1) for x in starts]+[(x['finished'],-1) for x in ends])
    active=peak=0
    for _,delta in intervals:
        active+=delta
        peak=max(peak,active)
        assert active>=0
    assert active==0 and peak<=64
    responses=0
    for rec in ends:
        path=OUT/'attempts'/Path(rec['case']).with_suffix('')/f"{rec['generation']}-{rec['transport']}"
        assert b.read(path/'attempt.json')=={k:v for k,v in rec.items() if k not in ('time','event')}
        assert rec['request_sha256']==b.sha(OUT/'requests'/rec['case'])
        if rec.get('http_status')==200:
            raw=b.read(path/'response.bin')
            assert raw['usage']==rec.get('usage')
            assert not raw['choices'][0]['message'].get('reasoning_content')
            assert ((raw.get('usage') or {}).get('completion_tokens_details') or {}).get('reasoning_tokens',0)==0
            assert (raw.get('usage') or {}).get('completion_tokens',0)<=1024
            responses+=1
    evaluation=b.read(OUT/'final-evaluation.json')
    assert evaluation['positive_cases']==443 and len(evaluation['rows'])==693
    data=[]
    for case in b.read(OUT/'cases.json'):
        c=case['case']
        raw=b.read(b.DATA/c)
        result=b.read(OUT/'cases'/c)
        output=b.read(OUT/'outputs'/c)
        assert all(output[k]==v for k,v in raw.items())
        request=b.read(OUT/'requests'/c)
        assert set(request)=={'model','messages','temperature','max_tokens','thinking'}
        assert request['thinking']=={'type':'disabled'} and request['max_tokens']==1024 and request['temperature']==0
        if result['state']=='accepted':
            response=[r for r in result['attempts'] if r.get('transport_accepted')][-1]
            last=OUT/'attempts'/Path(c).with_suffix('')/f"{response['generation']}-{response['transport']}"/'response.bin'
            assert b.parse(b.read(last)['choices'][0]['message']['content'])==result['prediction']
            assert output['is_hallucination_prediction']==result['prediction']['is_hallucination']
        data.append((c,output))
    from evaluate_metric import evaluate_accuracy, binary_to_01
    measured,_=evaluate_accuracy(data)
    assert measured['accuracy']==evaluation['metrics']['accuracy']
    assert measured['f1_score']==evaluation['metrics']['f1_score']
    assert measured['steps_conditional_accuracy']==evaluation['metrics']['steps_conditional_accuracy']
    assert binary_to_01('true')==1 and binary_to_01('false')==0
    for row in evaluation['rows']:
        raw=b.read(b.DATA/row['case'])
        assert row['gold'] is (binary_to_01(raw['is_hallucination'])==1)
    correct=sum(binary_to_01(item['is_hallucination'])==binary_to_01(item.get('is_hallucination_prediction')) for _,item in data)
    assert correct==sum(r['correct'] for r in evaluation['rows'])
    assert correct/693==measured['accuracy']
    audit=b.read(OUT/'completion-audit.json')
    audit.update(independent_reconciliation_passed=True, unique_starts=len(starts), unique_finishes=len(ends), unique_commits=len(commits),
                 actual_peak_concurrent_requests=peak, http200_responses=responses,
                 response_models=dict(collections.Counter(x.get('response_model','unavailable') for x in ends)),
                 source_bytes_verified=True, request_hashes_verified=True, raw_usage_verified=True,
                 author_scorer_independently_recomputed=True,
                 adapter_tests={'count':6,'result':'passed','source_sha256':b.sha(OUT/'test_adapter.py')})
    audit['postprocessing']={'scorer':'score.py','sha256':b.sha(OUT/'score.py'),
        'reason':'Initial aggregation assumed int-compatible gold labels. Mixed bool/string labels require original author binary_to_01; frozen inference unchanged, no rerun.'}
    schema_notes=[]
    for row in evaluation['rows']:
        case=b.read(OUT/'cases'/row['case'])
        pred=case.get('prediction')
        if not pred: continue
        issues=[]
        if type(pred['is_hallucination']) is not bool: issues.append('label_type_not_boolean')
        step=pred['hallucination_step']
        if step is not None and (type(step) is not int or step<1): issues.append('step_schema')
        reason=pred['hallucination_reason']
        if reason is not None and (not isinstance(reason,str) or len(reason)>1000): issues.append('reason_schema_or_maxLength1000')
        if binary_to_01(pred['is_hallucination'])==0 and (step is not None or reason is not None): issues.append('false_should_have_null_fields')
        if issues: schema_notes.append({'case':row['case'],'issues':issues})
    b.save(OUT/'output-compliance.json',{'scoring':'Exact original author decoder/metrics; schema observations do not select retries or silently exclude predictions', 'notes':schema_notes})
    audit['prompt_schema_notes']=len(schema_notes)
    b.save(OUT/'completion-audit.json',audit)
    report=(OUT/'report.md').read_text(encoding='utf-8')
    report+='\n## 分家族结果\n\n| 家族 | 样本 | 检测accuracy | macro-F1 | 首错命中/阳性 |\n| --- | ---: | ---: | ---: | ---: |\n'
    for name,m in evaluation['by_family'].items():
        group=[r for r in evaluation['rows'] if r['case'].startswith(name+'/')]
        report+=f"| {name} | {len(group)} | {m['accuracy']:.2%} | {m['f1_score']:.2%} | {sum(r['localization_correct'] for r in group)}/{sum(r['gold'] for r in group)} |\n"
    unresolved=[r for r in evaluation['rows'] if r['state']!='accepted']
    report+='\n## 完成审计\n\n'
    report+=f"独立核对{len(starts)} started/{len(ends)} finished、693个唯一案例commit，实际峰值{peak}并发；原始响应、usage、请求摘要、全部冻结源码/输入身份与作者评分重算一致。适配器6项离线测试通过。\n\n"
    report+='未解析案例：'+('、'.join(r['case'] for r in unresolved) if unresolved else '无')+'。没有按金标补测或更改提示/预算。原JSON解析成功而缺字段的响应遵照作者流程不再次生成；JSON解码失败才最多3次相同请求生成。\n'
    (OUT/'report.md').write_text(report,encoding='utf-8')
    dest=ROOT/'docs/results/deepseek-standard-20261020'
    dest.mkdir(parents=True,exist_ok=True)
    report+=f"\n额外输出合规观察{len(schema_notes)}例，见output-compliance.json；按作者实际解码/评分计数，未按这些观察追加请求或筛选结果。离线汇总曾因混合字符串/布尔金标读取失败，独立score.py已改用作者binary_to_01；冻结推理运行文件不变，不重跑案例。\n"
    (OUT/'report.md').write_text(report,encoding='utf-8')
    files=['report.md','policy.json','freeze.json','cases.json','final-evaluation.json','paired-comparison.json','completion-audit.json','all-attempt-costs.json','output-compliance.json','baseline.py','score.py','test_adapter.py','audit_and_archive.py']
    for filename in files:
        shutil.copyfile(OUT/filename,dest/filename)
    b.save(dest/'manifest.json',{'schema':'paper.standard.archive.v1','created':b.now(),'raw_root':str(OUT.relative_to(ROOT)),
          'source_commit':audit['author_commit'],'files':{p:b.sha(dest/p) for p in files},
          'evidence_retained':'All requests, raw responses, per-attempt logs and original official sources remain at raw_root; summaries are not substitutes'})
    print(json.dumps({'audit':audit,'archive':str(dest)},ensure_ascii=False))

if __name__=='__main__':
    main()
