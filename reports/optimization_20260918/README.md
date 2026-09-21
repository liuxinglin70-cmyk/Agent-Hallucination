# 证据覆盖优化与固定划分预检

本轮沿用 `feat/evidence-retrieval`，原始数据、种子、划分和门限不变。运行协议先于真实请求冻结于 [protocol.json](protocol.json)，独立工作目录为 `artifacts/evolution_evidence_20260918`（运行时目录，已回收；结果归档在 [run/](run/)），不会复用历史失败实验或旧基线。

## 已验证改动

- 语料新增 UNESCO 原报告、欧盟获奖项目报告、临床试验原始 API 记录和足球官方统计短摘录；保留原始文件、获取失败记录与哈希。详见 [来源缺口](source_gaps.md)、[重建脚本](build_corpus.py)、[语料清单](corpus/corpus.json)。
- [同代码旧语料复测](coverage_delta.json)为 0/10，新语料的原 mini S/G 共 10 例生成 **1 个有效快照**；S 为 0/6，G 为 1/4。新快照为 OpenDeepSearch/080，提取值与 UNESCO PDF 物理第 111 页核对一致。[逐例覆盖表](coverage_mini/README.md)、[机械决策与缺口](coverage_mini/retrieval.json)。
- 既有诊断配置仅做离线覆盖审计，S 为 0/10，G 为 2/10，命中的是已有语料覆盖的两个案例。[诊断覆盖表](coverage_diagnostic/README.md)。没有将诊断配置作为失败后的重抽方案。
- 后端仅配置 corpus 时，现在从 dataset_root 读取投影后的问题；仍不读取答案作为检索输入。证据缓存命名空间纳入来源正文、固定快照正文及问题覆盖映射的指纹，避免内容更新后复用旧证据。
- 五个定位阶段共用 [定位口径](../../docs/localization.md)：提交步首次引入错误时允许定位，仅重复早先错误时不能成为新的根因。未修改评分函数或映射标注步号。

## 验证

[pytest](pytest.txt)：385 passed。[完整离线检查](offline/summary.json)：所有检查通过；原始 695 个文件哈希一致。离线报告的 XML 归档路径修正有明确记录，没有修改任何测试结果。

## 真实运行

固定原 mini 协议的真实预检已完成，终态为 **blocked_evaluation_preflight**。命令：

```powershell
python -m evolution experiment --mini --config reports/optimization_20260918/run_config.json --cycles 2
```

预检结果为 **2 TP、1 TN、1 FP、1 FN、1 unresolved**；受保护 TN=1，低于 2 的下限，因而停止。没有生成候选、启动第二轮、切换诊断划分或重跑选结果。

`SmolAgents/019` 的 Judge 有输出，但 Defense 触发 `case_deadline_exceeded`，终态 `degraded_judge_only`，计分 prediction=null。观察到总墙钟耗时超过配置的 900 秒，截止时间最终生效；本轮未修改超时参数，未宣称严格墙钟上限问题已修复。

[验收审计](completion_audit.json)从六例原始记录重算出相同预检结论；[实验结果](run/reports/experiment.json)、[逐案例原始记录](run/checkpoints/cycle001/arms)、[账本](run/ledger.jsonl)均归档。账本 3 条记录，哈希链有效；gates、split、budget 与原配置完全一致，冻结输入哈希全部匹配。

覆盖只有 1/10，说明多数案例仍缺可用证据。Unicode 实体、人物答案、化学推导、物理计算和表格解析仍是明确的能力缺口；本轮不宣称已解决，也不把 schema 合法当成全面证据质量保证。共享定位指令不等于真实定位稳定性已经得到证明。
