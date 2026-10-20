# DeepSeek论文标准提示全693测量

论文：[AgentHallu](https://arxiv.org/html/2601.06818v1)。作者实现固定提交 `9ffe8bc888feaf15d89833f4b0e3c4697f44acc1`。当前DeepSeek请求模型`DeepSeek-V4.1-Flash-1017`，64请求并发；temperature=0、max_tokens=1024、显式关闭thinking，完整轨迹all_at_once。

## 结果

- 有效解析 692/693；准确率 65.512266%；macro-F1 60.952886%；macro-recall 60.651919%。
- 首错定位严格原始step命中 181/443=40.857788%，分母为全部金标阳性。
- TP/FP/FN/TN：346/142/96/108；缺失预测1。

## 同案比较

共同可评检测689例：直接提示452正确→完整框架531正确，框架修正139、回退60。共同阳性440例按原始step比较：180→206，修正82、回退56。完整框架原报告使用canonical事件位置，另行映射为原step才能公平比较。

论文标准GPT-5：accuracy70.6%、macro-F1 70.2%、定位32.7%；Gemini2.5Pro：68.8%、64.6%、41.1%。论文数字是跨时期模型参照，不能用此差值证明框架收益。本轮同底座但thinking、调用次数、预算和截断不同，同案差值仍不单独隔离框架因果收益。

## 协议与适配

直接导入作者提示、格式化、长输入截断及宽容解析函数。作者DeepSeek入口不接受上层传入的response_format，故独立HTTP适配器发送原消息，无JSON模式，无检测器证据链/提示修复。使用论文输出1024上限；显式关闭默认thinking使temperature0生效。作者输入token计数翻倍错误未沿用，使用原始usage。最多3次技术JSON生成、每次最多5次传输；固定同一请求，不按正确性补测。金标只由离线评分读取，全部请求事先冻结。13例触发作者截断算法，截断后最大97,745 token，0例残余超限；详见cases.json。

G-EVAL需要另一个GPT-5评审，本轮未执行，不把首错定位或生成解释当作G-EVAL分数。

## 全部尝试成本

请求706次，HTTP/传输失败1，length结束11，缺usage1次。已知input/output/total token=4582822/76581/4659403；耗时182.8秒，逐请求累计7724.2秒。供应商收费及缓存折扣未核实，不编造金额，缺usage不按0计。

原件：`artifacts/paper_baselines/deepseek_standard_20261020/`；协议policy.json、freeze.json，逐例请求requests/、逐尝试attempts/、案例commit cases/及作者兼容输出outputs/；逐例评分final-evaluation.json、同案paired-comparison.json、终验completion-audit.json及全部成本all-attempt-costs.json。

## 分家族结果

| 家族 | 样本 | 检测accuracy | macro-F1 | 首错命中/阳性 |
| --- | ---: | ---: | ---: | ---: |
| BFCL | 164 | 52.44% | 49.43% | 13/103 |
| Camel | 93 | 63.44% | 56.57% | 38/57 |
| Magentic_One | 94 | 69.15% | 67.18% | 23/54 |
| Octotools | 47 | 70.21% | 66.83% | 16/30 |
| OpenDeepSearch | 100 | 74.00% | 71.03% | 27/58 |
| OpenManus | 104 | 68.27% | 53.51% | 48/84 |
| SmolAgents | 91 | 72.53% | 68.65% | 16/57 |

## 完成审计

独立核对706 started/706 finished、693个唯一案例commit，实际峰值64并发；原始响应、usage、请求摘要、全部冻结源码/输入身份与作者评分重算一致。适配器6项离线测试通过。

未解析案例：OpenManus/091.json。没有按金标补测或更改提示/预算。原JSON解析成功而缺字段的响应遵照作者流程不再次生成；JSON解码失败才最多3次相同请求生成。

额外输出合规观察5例，见output-compliance.json；按作者实际解码/评分计数，未按这些观察追加请求或筛选结果。离线汇总曾因混合字符串/布尔金标读取失败，独立score.py已改用作者binary_to_01；冻结推理运行文件不变，不重跑案例。
