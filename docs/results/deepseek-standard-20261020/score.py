"""Post-measurement scorer: author normalization for mixed boolean/string gold labels.
The frozen inference runner is unchanged. This file is a derivative of its report
function; inference is never invoked. Original failed aggregation remains recorded.
"""
from pathlib import Path
import importlib.util
import sys
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('frozen_baseline',HERE/'baseline.py')
b=importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
OUT,ROOT,DATA=b.OUT,b.ROOT,b.DATA
now,read,save,verify,parse=b.now,b.read,b.save,b.verify,b.parse
import json
from datetime import datetime

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
        rows.append({'case': case, 'gold': (binary_to_01(raw['is_hallucination']) == 1), 'gold_raw_step': (step_to_int(raw.get('hallucination_step')) if raw.get('hallucination_step') is not None else None),
                     'prediction': None if pred==-1 else bool(pred), 'raw_step': None if step==-1 else step,
                     'canonical_step': gold_step_mapping(raw, step)['position'] if step!=-1 else None,
                     'gold_canonical_step': gold_step_mapping(raw, raw.get('hallucination_step'))['position'],
                     'state': result['state'], 'correct': pred==binary_to_01(raw['is_hallucination']),
                     'localization_correct': (binary_to_01(raw['is_hallucination']) == 1) and step==step_to_int(raw.get('hallucination_step'))})
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
                       'direct_localization':r['localization_correct'], 'framework_localization':r['gold'] and framework_raw is not None and step_to_int(framework_raw)==r['gold_raw_step'],
                       'framework_raw_step':framework_raw, 'direct_raw_step':r['raw_step']})
    def pair_summary(group, before, after):
        return {'n':len(group),'direct_correct':sum(r[before] for r in group),'framework_correct':sum(r[after] for r in group),
                'framework_fixes':sum(not r[before] and r[after] for r in group), 'framework_regressions':sum(r[before] and not r[after] for r in group)}
    paired_report = {'direction':'direct standard -> full detector', 'detection':pair_summary(paired,'direct_correct','framework_correct'),
                     'raw_step_localization':pair_summary([r for r in paired if r['gold']],'direct_localization','framework_localization'),
                     'detection_by_gold': {str(g):pair_summary([r for r in paired if r['gold']==g],'direct_correct','framework_correct') for g in (True,False)},
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
    text = f'''# DeepSeek论文标准提示全693测量\n\n论文：[AgentHallu](https://arxiv.org/html/2601.06818v1)。作者实现固定提交 `{audit['author_commit']}`。当前DeepSeek请求模型`DeepSeek-V4.1-Flash-1017`，64请求并发；temperature=0、max_tokens=1024、显式关闭thinking，完整轨迹all_at_once。\n\n## 结果\n\n- 有效解析 {evaluation['accepted']}/693；准确率 {m['accuracy']:.6%}；macro-F1 {m['f1_score']:.6%}；macro-recall {m['recall']:.6%}。\n- 首错定位严格原始step命中 {evaluation['localization_hits']}/443={m['steps_conditional_accuracy']:.6%}，分母为全部金标阳性。\n- TP/FP/FN/TN：{confusion['tp']}/{confusion['fp']}/{confusion['fn']}/{confusion['tn']}；缺失预测{confusion['missing']}。\n\n## 同案比较\n\n共同可评检测{d['n']}例：直接提示{d['direct_correct']}正确→完整框架{d['framework_correct']}正确，框架修正{d['framework_fixes']}、回退{d['framework_regressions']}。共同阳性{l['n']}例按原始step比较：{l['direct_correct']}→{l['framework_correct']}，修正{l['framework_fixes']}、回退{l['framework_regressions']}。完整框架原报告使用canonical事件位置，另行映射为原step才能公平比较。\n\n论文标准GPT-5：accuracy70.6%、macro-F1 70.2%、定位32.7%；Gemini2.5Pro：68.8%、64.6%、41.1%。论文数字是跨时期模型参照，不能用此差值证明框架收益。本轮同底座但thinking、调用次数、预算和截断不同，同案差值仍不单独隔离框架因果收益。\n\n## 协议与适配\n\n直接导入作者提示、格式化、长输入截断及宽容解析函数。作者DeepSeek入口不接受上层传入的response_format，故独立HTTP适配器发送原消息，无JSON模式，无检测器证据链/提示修复。使用论文输出1024上限；显式关闭默认thinking使temperature0生效。作者输入token计数翻倍错误未沿用，使用原始usage。最多3次技术JSON生成、每次最多5次传输；固定同一请求，不按正确性补测。金标只由离线评分读取，全部请求事先冻结。13例触发作者截断算法，截断后最大97,745 token，0例残余超限；详见cases.json。\n\nG-EVAL需要另一个GPT-5评审，本轮未执行，不把首错定位或生成解释当作G-EVAL分数。\n\n## 全部尝试成本\n\n请求{costs['attempts']}次，HTTP/传输失败{costs['http_failures']}，length结束{costs['length_finishes']}，缺usage{costs['missing_usage']}次。已知input/output/total token={totals['prompt_tokens']}/{totals['completion_tokens']}/{totals['total_tokens']}；耗时{costs['wall_seconds']:.1f}秒，逐请求累计{costs['sum_request_seconds']:.1f}秒。供应商收费及缓存折扣未核实，不编造金额，缺usage不按0计。\n\n原件：`artifacts/paper_baselines/deepseek_standard_20261020/`；协议policy.json、freeze.json，逐例请求requests/、逐尝试attempts/、案例commit cases/及作者兼容输出outputs/；逐例评分final-evaluation.json、同案paired-comparison.json、终验completion-audit.json及全部成本all-attempt-costs.json。\n'''
    (OUT/'report.md').write_text(text, encoding='utf-8')
    print(json.dumps({'completed':audit,'metrics':plain(metrics),'paired':d},ensure_ascii=False),flush=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    report()
