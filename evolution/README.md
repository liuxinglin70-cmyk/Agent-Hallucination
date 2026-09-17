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

真实运行输出集中在 `artifacts/evolution/`：`cycles/`、`cache/`、`reports/`、`registry/`、`harness/`、`memory/` 和 `ledger.jsonl`。框架不加载历史活跃干预，首次运行从空库开始。

## H_t 基座

每轮在准入写盘之后记录 H_t = (G, C, T, M, R) 的五组件清单：写出 `harness/harness.json`，把逐组件摘要追加到 `harness/harness_history.jsonl`，并在 cycle 报告中给出与上一代的逐组件差异。清单的组件由实际运行的代码与配置推导（阶段入口、上下文预算、活跃 registry、经验存储、策略配置），不是手写描述；装载时重新推导摘要，被编辑过的清单会被发现，阶段入口被重命名也会使构建失败而不是留下过时描述。

落点由配置 `harness_dir`（默认 `artifacts/evolution/harness`）与 `memory_root`（默认 `artifacts/evolution/memory`）决定，相对路径按仓库根目录解析。同一目录下还有两个配套设施：

- **能力缺口清单** `harness/gaps.jsonl`：可复现、但当前动作词表无法处理的失败族落为去重后备项，保留原因与可行路由，可用 `resolve_gap` 记为已解决。身份剥掉 worksheet 的 `-P<hash>` 画像后缀，因此同一缺口只有一条记录。重复出现不重复追加；原因或路由变化才算新信息。
- **三类经验** `memory/`：情景 `episodic.jsonl`、程序性 `procedural.jsonl`、改进 `improvement.jsonl`。只有**通过门控**的处理路线才写入程序性经验。检索按机制过滤并设上限；结果显式区分 `falsified_intervention_ids`（确实被门控否定）与 `unevaluable_intervention_ids`（未曾被测量），后者不会被当作证伪来抑制同一机制。

**边界：** 清单与缺口是**记录**设施，不参与任何门的判定，因此不会移动准入结果。经验存储已接入草拟：`_proposal_memory_context` 为当前失败族检索已准入程序性经验并附开放缺口，经 `set_memory_context` 进入 proposer 提示；供给情况记入账本 `proposal_memory` / `proposal_memory_used`。记忆是草拟先验，不是约束，也不参与门判定——是否真正改善了草拟质量，需要在后续实验中用带/不带记忆的对照来判定，不能由「已接入」直接推出。

`final` 和 `full-self-evolution` 需要显式的正式协议；默认开发配置不提供 `split_manifest`，因此会拒绝正式终审。详见 [评估协议](../docs/evaluation.md)。

测试：`python -m pytest evolution/tests -q`。全仓库可复现验证：`python scripts/verify.py`。

新运行在 `checkpoints/cycleNNN/` 保存完整提案、审计和逐例评估结果。中断后可运行 `python -m evolution cycle --cycle 1 --mini --resume`，或 `python -m evolution experiment --mini --resume`；使用自定义配置时仍需传入同一个 `--config`。恢复会校验代码、配置、数据、划分和 registry 指纹，复用已经完成的观察（含未决结果），不重新生成已保存的提案。旧运行没有 checkpoint 时不能精确恢复；proposer 返回前中断且没有完整提案时也会拒绝重抽。registry 提交过程中中断可能需要人工核查，当前恢复路径不会越过 registry 指纹不一致继续执行。同一工作目录不能并发启动多个进化进程。

下游干预现在回放原 judge，并重跑受到影响的阶段；新增听证可执行，已有听证仅在输入一致时复用。`judge.system` 仍需要新的配对控制。G 集没有相应 FN/FP 修复机会时，报告标记 `not_evaluable`，准入继续阻断；这不等于机制被否定，也不允许在原 G 集上无限搜索。真实复核后的具体修复与限制见 [修复说明](../reports/evolution_real/fixes.md)。

### 覆盖响应

`coverage_response` 决定「某方向在 gain 集上无机会可修」时怎么做，默认 `strict`（行为与改动前一致：记为未测量、准入阻断）。可显式改为 `replacement`（在已安装的替代评估集上重新测量整个判定单元）或 `replacement_or_skip`（无匹配评估集时记为 `skipped_unmeasured` 并跳过），并可按方向覆盖：

```json
"coverage_response": {
  "mode": "strict",
  "policies": {"recall": "replacement_or_skip"}
}
```

替代评估集通过 `EvolutionEngine.set_replacement_gain_cases(cases)` 在内存中安装；持久化它需要与主 gain 集同等的 gold、协议与暴露保证，属单独决定。未安装时策略报告 `none_installed`。响应动作写入 `instrument.coverage_responses` 与 `instrument.coverage_response_counts`，因此「因不可测量而准入 0」与「重新测量后仍准入 0」可以区分。**该策略可以改变准入结果**，故默认关闭。

自动化退出码：`smoke` 只有在目标干预准入、缺陷审计、账本完整性均通过且 cycle 完成时才返回 0；否则返回 2。`verify-ledger` 对有效账本返回 0、哈希链校验失败返回 2；无法解析的损坏文件也不会返回成功。

当前已验证离线框架闭环和检测器真实服务连通性；尚未在整理后的代码上完成真实模型驱动的整轮进化效果验证。阶段目标见 [当前进展与计划](../docs/progress.md)。
