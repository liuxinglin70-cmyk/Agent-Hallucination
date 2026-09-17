# 自进化框架

`evolution` 调用 `detector` 的公开检测接口，将进化建模为可执行干预的准入与治理。公开入口为 `EvolutionEngine`、`run_experiment` 和 `python -m evolution`。

默认配置 `configs/development.json` 使用 hybrid proposer，开发池 30 例（sentinel 6、gain 4、patrol 20），开发复测集 10 例。默认两轮、8 路 case 并发。配置中的相对路径按仓库根目录解析。proposer 从检测器的 judge 路由继承模型与端点；`proposer.thinking_level` 默认留空，表示不发送 `thinking` 字段——底座模型 `GLM-5.3-flash` 会对任何显式的思考档位间歇性返回 400。该选项仅在换用接受该字段的供应商时填写。

`proposer.max_output_tokens` 默认为 32768。`GLM-5.3-flash` 强制思考，实测 9 次真实 proposer 调用的完成 token 为 6.2k–12.5k，其中 85%–92% 是 reasoning；旧值 4096 会在 JSON 数组之前被截断，返回 `truncated_output` 且候选数为 0。该上限只是天花板，不产生额外费用，按配置值 5 次真实运行均无截断（最大 11098 token）。

```bash
python -m evolution capability-audit
python -m evolution smoke
python -m evolution capability-smoke
python -m evolution preview --cycle 1 --mini
python -m evolution experiment --mini
python -m evolution governance --mini
python -m evolution meta --mini
python -m evolution verify-ledger
```

前 3 条命令不调用真实模型服务。`smoke` 检查准入与缺陷拒绝；`capability-smoke` 使用独立合成数据检查“新 DSL 谓词 + guard”带来的机制增益。其运行状态与真实 registry 隔离。

真实运行输出集中在 `artifacts/evolution/`：`cycles/`、`cache/`、`reports/`、`registry/` 和 `ledger.jsonl`。框架不加载历史活跃干预，首次运行从空库开始。

`final` 和 `full-self-evolution` 需要显式的正式协议；默认开发配置不提供 `split_manifest`，因此会拒绝正式终审。详见 [评估协议](../docs/evaluation.md)。

测试：`python -m pytest evolution/tests -q`。全仓库可复现验证：`python scripts/verify.py`。

新运行在 `checkpoints/cycleNNN/` 保存完整提案、审计和逐例评估结果。中断后可运行 `python -m evolution cycle --cycle 1 --mini --resume`，或 `python -m evolution experiment --mini --resume`；使用自定义配置时仍需传入同一个 `--config`。恢复会校验代码、配置、数据、划分和 registry 指纹，复用已经完成的观察（含未决结果），不重新生成已保存的提案。旧运行没有 checkpoint 时不能精确恢复；proposer 返回前中断且没有完整提案时也会拒绝重抽。registry 提交过程中中断可能需要人工核查，当前恢复路径不会越过 registry 指纹不一致继续执行。同一工作目录不能并发启动多个进化进程。

下游干预现在回放原 judge，并重跑受到影响的阶段；新增听证可执行，已有听证仅在输入一致时复用。`judge.system` 仍需要新的配对控制。G 集没有相应 FN/FP 修复机会时，报告标记 `not_evaluable`，准入继续阻断；这不等于机制被否定，也不允许在原 G 集上无限搜索。真实复核后的具体修复与限制见 [修复说明](../reports/evolution_real/fixes.md)。

自动化退出码：`smoke` 只有在目标干预准入、缺陷审计、账本完整性均通过且 cycle 完成时才返回 0；否则返回 2。`verify-ledger` 对有效账本返回 0、哈希链校验失败返回 2；无法解析的损坏文件也不会返回成功。

当前已验证离线框架闭环和检测器真实服务连通性；尚未在整理后的代码上完成真实模型驱动的整轮进化效果验证。阶段目标见 [当前进展与计划](../docs/progress.md)。
