"""Author-compatible scores and independent step-control/evidence reconciliation."""
from __future__ import annotations
from datetime import datetime
import collections
import importlib.util
import json
from pathlib import Path
import shutil
import sys

OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('frozen_stepwise',OUT/'stepwise.py')
s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
ROOT,DATA,STANDARD=s.ROOT,s.DATA,s.STANDARD
read,save,sha=s.read,s.save,s.sha
from evaluate_metric import evaluate_accuracy, binary_to_01, step_to_int

def plain(value):
    if isinstance(value,dict): return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [plain(v) for v in value]
    if hasattr(value,'tolist'): return value.tolist()
    if hasattr(value,'item'): return value.item()
    return value

def evaluate(data): return plain(evaluate_accuracy(data)[0])

def row(case,raw,prediction,accepted):
    label=binary_to_01(raw.get('is_hallucination'))
    assert label in (0,1)
    predicted=binary_to_01(prediction.get('is_hallucination')) if prediction else None
    step=step_to_int(prediction.get('hallucination_step')) if prediction else -1
    gold_step=step_to_int(raw.get('hallucination_step'))
    return {'case':case,'gold':bool(label),'prediction':None if predicted is None else bool(predicted),
            'gold_raw_step':None if gold_step==-1 else gold_step,'raw_step':None if step==-1 else step,
            'accepted':accepted,'correct':predicted==label,'localization_correct':label==1 and step==gold_step and step!=-1}

def pair(before,after):
    right={r['case']:r for r in after}
    paired=[]
    for a in before:
        b=right[a['case']]
        assert a['gold']==b['gold']
        if not a['accepted'] or not b['accepted']: continue
        paired.append({'case':a['case'],'gold':a['gold'],'before_correct':a['correct'],'after_correct':b['correct'],
                       'before_localization':a['localization_correct'],'after_localization':b['localization_correct']})
    def summarize(group,left,right):
        return {'n':len(group),'before_correct':sum(x[left] for x in group),'after_correct':sum(x[right] for x in group),
                'fixes':sum(not x[left] and x[right] for x in group),'regressions':sum(x[left] and not x[right] for x in group)}
    return {'detection':summarize(paired,'before_correct','after_correct'),
            'raw_step_localization':summarize([r for r in paired if r['gold']],'before_localization','after_localization'),
            'detection_by_gold':{str(g):summarize([r for r in paired if r['gold']==g],'before_correct','after_correct') for g in (True,False)},
            'by_family':{name:summarize([r for r in paired if r['case'].startswith(name+'/')],'before_correct','after_correct') for name in sorted({r['case'].split('/')[0] for r in paired})},'rows':paired}

def main():
    s.verify()
    assert read(OUT/'status.json')['state']=='completed'
    plans=read(OUT/'cases.json')
    rows=[]; data=[]; standard=[]; framework=[]; framework_data=[]
    old_standard={r['case']:r for r in read(STANDARD/'final-evaluation.json')['rows']}
    old_framework={r['case']:r for r in read(ROOT/'docs/results/full693-20261020/final-evaluation.json')['rows']}
    skipped=0; prefix_total=0; decoded=0; unusable_prefixes=[]; cases_with_gaps=[]; compliance=[]; queried_keys=set()
    for plan in plans:
        case=plan['case']; raw=read(DATA/case); result=read(OUT/'cases'/case)
        assert result['case']==case
        seen=[]; expected=None; gaps=[]
        for prefix in plan['prefixes']:
            p=OUT/'prefixes'/Path(case).with_suffix('')/f"{prefix['index']:04}.json"
            if expected is not None:
                assert not p.exists(),'prefix queried after first True: '+str(p)
                skipped+=1; continue
            assert p.exists(),'missing prefix before final decision: '+str(p)
            rec=read(p); assert rec['case']==case and rec['prefix_index']==prefix['index']
            assert rec['source_step']==prefix['source_step']
            prefix_total+=1; seen.append(prefix['index']); queried_keys.add((case,prefix['index'])); obj=rec['decoded']
            if rec['state']=='decoded':
                decoded+=1
                last=rec['attempts'][-1]
                path=OUT/'attempts'/Path(case).with_suffix('')/f"{prefix['index']:04}"/f"{last['generation']}-{last['transport']}"/'response.bin'
                assert s.decode(read(path)['choices'][0]['message']['content'])==obj
            if not isinstance(obj,dict) or type(obj.get('is_hallucination')) is not bool:
                gaps.append(prefix['index']); unusable_prefixes.append({'case':case,'prefix_index':prefix['index'],'state':rec['state']})
            if isinstance(obj,dict):
                reason=obj.get('hallucination_reason'); issues=[]
                if 'hallucination_reason' not in obj: issues.append('missing_reason')
                elif reason is not None and (not isinstance(reason,str) or len(reason)>1000): issues.append('reason_schema_or_maxLength1000')
                if obj.get('is_hallucination') is False and reason is not None: issues.append('false_reason_not_null')
                if issues: compliance.append({'case':case,'prefix_index':prefix['index'],'issues':issues})
            # Independent reproduction of author control, without calling runner decision().
            if isinstance(obj,dict) and obj.get('is_hallucination') is True:
                expected={'is_hallucination':True,'hallucination_step':raw['history'][prefix['index']-1].get('step'),'hallucination_reason':obj.get('hallucination_reason')}
            elif isinstance(obj,dict) and obj.get('is_hallucination') is False and prefix['index']==len(plan['prefixes']):
                expected={'is_hallucination':False,'hallucination_step':None,'hallucination_reason':None}
        assert seen==result['prefix_indices']
        assert expected==result['prediction']
        assert (result['state']=='accepted')==(expected is not None)
        if gaps: cases_with_gaps.append({'case':case,'prefix_indices':gaps,'final_state':result['state']})
        output=dict(raw)
        if expected is not None:
            output.update(is_hallucination_prediction=expected['is_hallucination'],hallucination_step_prediction=expected['hallucination_step'],hallucination_reason_prediction=expected['hallucination_reason'])
        save(OUT/'outputs'/case,output); data.append((case,output))
        rows.append(row(case,raw,expected,expected is not None))
        old=old_standard[case]
        assert old['gold']==rows[-1]['gold']
        standard.append({'case':case,'gold':old['gold'],'prediction':old['prediction'],'raw_step':old['raw_step'],
                         'accepted':old['state']=='accepted','correct':old['correct'],'localization_correct':old['localization_correct']})
        f=old_framework[case]; prediction=None
        if f['authority']=='authoritative':
            position=f.get('responsible_step')
            raw_step=raw['history'][position-1].get('step') if f.get('localization_authority')=='authoritative' and isinstance(position,int) and 1<=position<=len(raw['history']) else None
            prediction={'is_hallucination':f['prediction'],'hallucination_step':raw_step}
        framework.append(row(case,raw,prediction,prediction is not None))
        f_out=dict(raw)
        if prediction:
            f_out.update(is_hallucination_prediction=prediction['is_hallucination'],hallucination_step_prediction=prediction['hallucination_step'])
        framework_data.append((case,f_out))
    metrics=evaluate(data)
    confusion={key:0 for key in ('tp','fp','fn','tn','missing')}
    for r in rows:
        key='missing' if r['prediction'] is None else 'tp' if r['gold'] and r['prediction'] else 'fn' if r['gold'] else 'fp' if r['prediction'] else 'tn'
        confusion[key]+=1
    by_family={name:evaluate([(c,r) for c,r in data if c.startswith(name+'/')]) for name in sorted({r['case'].split('/')[0] for r in rows})}
    by_category=plain(evaluate_accuracy(data)[1])
    evaluation={'created':s.now(),'metrics':metrics,'accepted':sum(r['accepted'] for r in rows),'correct':sum(r['correct'] for r in rows),
                'localization_hits':sum(r['localization_correct'] for r in rows),'positive_cases':sum(r['gold'] for r in rows),'confusion':confusion,
                'by_family':by_family,'by_category':by_category,'rows':rows}
    assert len(rows)==693 and evaluation['positive_cases']==443
    assert evaluation['correct']/693==metrics['accuracy']
    assert evaluation['localization_hits']/443==metrics['steps_conditional_accuracy']
    save(OUT/'final-evaluation.json',evaluation)
    comparisons={'standard_to_stepwise':pair(standard,rows),'stepwise_to_framework':pair(rows,framework),'standard_to_framework':pair(standard,framework),
                 'limitations':['Same current model identifier; two paper prompts share nonthinking/1024 per-request settings but stepwise makes more calls and repeats prefixes',
                                'Full framework uses default thinking, different prompts, input views, output budgets, votes and evidence adjudication; difference is not an isolated architecture effect']}
    save(OUT/'paired-comparison.json',comparisons)
    events=[json.loads(x) for x in (OUT/'progress.jsonl').read_text(encoding='utf-8').splitlines()]
    starts=[e for e in events if e['event']=='request_started']; ends=[e for e in events if e['event']=='request_finished']
    ident=lambda r:(r['case'],r['prefix_index'],r['generation'],r['transport'])
    assert len(starts)==len(ends)==len(set(map(ident,starts)))==len(set(map(ident,ends)))
    assert set(map(ident,starts))==set(map(ident,ends))
    assert {(r['case'],r['prefix_index']) for r in starts}==queried_keys
    assert prefix_total+skipped==sum(len(r['prefixes']) for r in plans)
    assert len(list((OUT/'prefixes').rglob('*.json')))==prefix_total
    assert sum(e['event']=='case_committed' for e in events)==len({e['case'] for e in events if e['event']=='case_committed'})==693
    assert sum(e['event']=='prefix_committed' for e in events)==prefix_total
    active=peak=0
    for _,delta in sorted([(r['started'],1) for r in starts]+[(r['finished'],-1) for r in ends]):
        active+=delta; peak=max(peak,active); assert active>=0
    assert active==0 and peak<=64
    for rec in ends:
        plan=next(r for r in plans if r['case']==rec['case']); prefix=plan['prefixes'][rec['prefix_index']-1]
        assert prefix['request_sha256']==rec['request_sha256']
        path=OUT/'attempts'/Path(rec['case']).with_suffix('')/f"{rec['prefix_index']:04}"/f"{rec['generation']}-{rec['transport']}"
        assert read(path/'attempt.json')=={k:v for k,v in rec.items() if k not in ('event','time')}
        assert all(rec[k]==v for k,v in read(path/'started.json').items())
        if rec.get('http_status')==200:
            response=read(path/'response.bin'); assert response.get('usage')==rec.get('usage')
            assert not response['choices'][0]['message'].get('reasoning_content')
            assert ((rec.get('usage') or {}).get('completion_tokens_details') or {}).get('reasoning_tokens',0)==0
            assert (rec.get('usage') or {}).get('completion_tokens',0)<=1024
    totals={k:sum((r.get('usage') or {}).get(k,0) or 0 for r in ends) for k in ('prompt_tokens','completion_tokens','total_tokens')}
    costs={'attempts':len(ends),'unique_prefix_queries':prefix_total,'possible_prefixes':sum(len(r['prefixes']) for r in plans),'skipped_after_first_true':skipped,
           'usage_present':sum(isinstance(r.get('usage'),dict) for r in ends),'missing_usage':sum(not isinstance(r.get('usage'),dict) for r in ends),
           'http_failures':sum(r.get('http_status')!=200 for r in ends),'length_finishes':sum(r.get('finish_reason')=='length' for r in ends),
           'known_tokens_only':totals,'wall_seconds':(datetime.fromisoformat(max(r['finished'] for r in ends))-datetime.fromisoformat(min(r['started'] for r in starts))).total_seconds(),
           'sum_request_seconds':sum(r['elapsed_seconds'] for r in ends),'currency_cost':'Unknown actual billed rates/cache discounts; missing usage is not zero','rows':ends}
    save(OUT/'all-attempt-costs.json',costs)
    save(OUT/'output-compliance.json',{'unusable_prefixes':unusable_prefixes,'cases_with_gaps':cases_with_gaps,'prompt_schema_notes':compliance,
                                     'policy':'Original author continuation/stop rules retained; no gold-selected retries and no silent filtering of scoreable outputs'})
    audit={'created':s.now(),'frozen_identity_matches':s.verify(),'all_case_commits_present':True,'unique_cases':693,'accepted':evaluation['accepted'],
           'unresolved':[r['case'] for r in rows if not r['accepted']],'possible_prefixes':costs['possible_prefixes'],'queried_prefixes':prefix_total,'skipped_after_first_true':skipped,
           'decoded_prefixes':decoded,'unusable_boolean_prefixes':len(unusable_prefixes),'cases_with_gaps':len(cases_with_gaps),'prompt_schema_notes':len(compliance),
           'started':len(starts),'finished':len(ends),'actual_peak_concurrent_requests':peak,'response_models':dict(collections.Counter(r.get('response_model','unavailable') for r in ends)),
           'missing_usage':costs['missing_usage'],'author_scorer_recomputed':True,'prefix_order_and_first_true_rule_independently_replayed':True,
           'no_requests_after_first_true':True,'per_case_raw_outputs_retained':True,'geval_performed':False,'adapter_preflight':read(OUT/'preflight.json')}
    save(OUT/'completion-audit.json',audit)
    fmetrics=evaluate(framework_data)
    aggregate={'created':s.now(),'scoring':'Original author693 denominator, missing predictions wrong; localization uses raw source step /443 for all systems',
               'standard':read(STANDARD/'final-evaluation.json')['metrics'],'stepwise':metrics,'framework_all693':fmetrics,
               'framework_authoritative690':read(ROOT/'docs/results/full693-20261020/final-evaluation.json')['metrics'],
               'paired':{key:{k:v for k,v in val.items() if k!='rows'} for key,val in comparisons.items() if isinstance(val,dict)}}
    save(OUT/'aggregate-results.json',aggregate)
    m=metrics; a=comparisons['standard_to_stepwise']; f=comparisons['stepwise_to_framework']
    text=f'''# DeepSeek逐步检测与论文baseline汇总

论文：[AgentHallu](https://arxiv.org/html/2601.06818v1)，作者提交`{read(OUT/'policy.json')['source_commit']}`。本轮使用同一DeepSeek-V4.1-Flash-1017，temperature=0、非thinking、每请求最多1024输出token，64请求并发。输入为逐步累积前缀；首次严格布尔True即停止，首错使用当前原始step号；只有末步False才输出正常。无检测器提示、检索、多票或判据调整。

## 全693结果

| 运行 | 正确/693 | 检测准确率 | macro-F1 | macro-recall | 原始step定位/443 |
| --- | ---: | ---: | ---: | ---: | ---: |
| DeepSeek标准提示 | 454/693 | {aggregate['standard']['accuracy']:.2%} | {aggregate['standard']['f1_score']:.2%} | {aggregate['standard']['recall']:.2%} | 181/443={aggregate['standard']['steps_conditional_accuracy']:.2%} |
| DeepSeek逐步提示 | {evaluation['correct']}/693 | {m['accuracy']:.2%} | {m['f1_score']:.2%} | {m['recall']:.2%} | {evaluation['localization_hits']}/443={m['steps_conditional_accuracy']:.2%} |
| 完整框架（弃权计错的论文评分重算） | 531/693 | {fmetrics['accuracy']:.2%} | {fmetrics['f1_score']:.2%} | {fmetrics['recall']:.2%} | 206/443={fmetrics['steps_conditional_accuracy']:.2%} |

完整框架原报告690/693权威，权威准确率76.96%、macro-F1 75.98%；上表单独按作者评分将3未决留在693分母，未改写旧报告。逐步提示{evaluation['accepted']}/693可评；TP/FP/FN/TN={confusion['tp']}/{confusion['fp']}/{confusion['fn']}/{confusion['tn']}，另缺预测{confusion['missing']}例。缺预测保留在693分母；定位分母包含全部443阳性，不只计算检出者。

## 同案配对

标准→逐步：共同可评{a['detection']['n']}例，{a['detection']['before_correct']}→{a['detection']['after_correct']}正确，{a['detection']['fixes']}修正/{a['detection']['regressions']}回退；共同阳性{a['raw_step_localization']['n']}例定位{a['raw_step_localization']['before_correct']}→{a['raw_step_localization']['after_correct']}，{a['raw_step_localization']['fixes']}修正/{a['raw_step_localization']['regressions']}回退。

逐步→完整框架：共同可评{f['detection']['n']}例，{f['detection']['before_correct']}→{f['detection']['after_correct']}正确，{f['detection']['fixes']}修正/{f['detection']['regressions']}回退；共同阳性{f['raw_step_localization']['n']}例定位{f['raw_step_localization']['before_correct']}→{f['raw_step_localization']['after_correct']}，{f['raw_step_localization']['fixes']}修正/{f['raw_step_localization']['regressions']}回退。

二种论文提示的模型、thinking、每请求输出上限相同，但逐步会重复前缀并多次调用，属于提示策略比较，并非等总token消融。完整框架还改变thinking、输入组织、预算、多票和证据链，差值不能单独归因于框架结构或模型能力。逐例与分家族/金标方向见paired-comparison.json。

## 论文参照

| 论文模型/方法 | macro-F1 | 检测accuracy | 定位accuracy |
| --- | ---: | ---: | ---: |
| GPT-5 standard（Table4/5） | 70.2% | 70.6% | 32.7% |
| GPT-5 step-by-step（Table5） | 68.5% | 未报告 | 42.7% |
| Gemini2.5Pro standard（Table4） | 64.6% | 68.8% | 41.1% |
| DeepSeek-V3.1 step-by-step（Table5） | 51.1% | 未报告 | 35.9% |

论文使用不同模型版本，数字只作参照，不推断通用能力排序。G-EVAL需要另一个GPT-5评审，本轮未执行。

## 运行及成本

可能前缀{costs['possible_prefixes']}，实际请求前缀{prefix_total}，首次True后跳过{skipped}。共{len(ends)}次尝试，{costs['http_failures']}次HTTP/传输失败，{costs['length_finishes']}次length，{costs['missing_usage']}条缺usage。已知input/output/total={totals['prompt_tokens']}/{totals['completion_tokens']}/{totals['total_tokens']} token；请求墙钟{costs['wall_seconds']:.1f}秒，逐请求累计{costs['sum_request_seconds']:.1f}秒。缺usage不按零，供应商实际账单未知。作者翻倍/覆盖的token_num计数不沿用，保留每次原始usage。

## 合同与审计

所有5269个可能请求事前冻结，原问题和前缀格式、作者V3.1 tokenizer、98,304输入截断算法及JSON宽容解析保持。作者DeepSeek入口不接受上层response_format参数，继续用独立HTTP适配器发送原消息，无额外JSON模式或提示修复。最多3次JSON解码技术生成、每次最多5次传输；均固定原请求，不读金标、不按正确性补测。解析成功但缺字段不额外重试。原作者中间失败后继续下一步的规则保留；{len(unusable_prefixes)}个前缀缺可用布尔判断，影响{len(cases_with_gaps)}例的中间覆盖，另有{len(compliance)}条提示输出合规观察，详见output-compliance.json。

未决：{', '.join(audit['unresolved']) or '无'}。独立重放前缀顺序及首次True停止，{len(starts)}开始/{len(ends)}结束、693唯一commit、实际峰值{peak}并发；输入/请求/源码冻结身份、原始响应及usage、作者评分复算通过。全部逐次尝试、前缀、原始作者源码和旧实验保持；不追加正确性重试。

证据原件：`artifacts/paper_baselines/deepseek_stepwise_20261020/`。标准提示原件`artifacts/paper_baselines/deepseek_standard_20261020/`保持不变。freeze.json/policy.json为冻结协议，requests/为所有可能前缀，attempts/为实际每次原始响应，prefixes/与cases/为独立commit；final-evaluation.json、paired-comparison.json、aggregate-results.json、all-attempt-costs.json及completion-audit.json为派生评分与审计。
'''
    (OUT/'report.md').write_text(text,encoding='utf-8')
    destination=ROOT/'docs/results/deepseek-stepwise-20261020'; destination.mkdir(parents=True,exist_ok=True)
    files=['report.md','policy.json','freeze.json','cases.json','final-evaluation.json','paired-comparison.json','aggregate-results.json','all-attempt-costs.json','completion-audit.json','output-compliance.json','preflight.json','stepwise.py','score.py','test_stepwise.py']
    for file in files: shutil.copyfile(OUT/file,destination/file)
    save(destination/'manifest.json',{'schema':'paper.stepwise.archive.v1','created':s.now(),'raw_root':str(OUT.relative_to(ROOT)).replace('\\','/'),
         'files':{f:sha(destination/f) for f in files},'preservation':'All prefix requests, response attempts and per-prefix/case commits retained at raw_root; prior standard/framework evidence unchanged'})
    print(json.dumps({'metrics':metrics,'confusion':confusion,'audit':audit,'pairs':{k:{n:m for n,m in v.items() if n in ('detection','raw_step_localization')} for k,v in comparisons.items() if isinstance(v,dict)}}),flush=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8'); main()
