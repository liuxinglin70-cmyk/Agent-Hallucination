# 50 例自进化实验：实时监督与自动修复 Prompt

> **STATUS: SUPERSEDED (v5.7)**
>
> 本文的启动命令引用已退役的 `src.sehd_v6.main` 入口。当前框架入口为
> `python -m src.sehd_v6.evolution`（见仓库 README 的 Standalone Self-Evolution
> 一节）。先运行
> [CLOSED_LOOP_REVIEW_STARTUP_PROMPT.md](CLOSED_LOOP_REVIEW_STARTUP_PROMPT.md)
> 完成闭环审查与小规模验证前置准备，再根据其结论决定是否以更新后的命令启动
> 50 例实验。本文的实时监督规则与诊断-修复-恢复协议仍有效，保留作为参考。

你现在负责在本仓库中直接启动并监督一次 50 例自进化实验。目标不是把 50 例跑完就结束，而是确保自进化闭环真正能够产生可执行、可观测、经过独立证据验证的有效 skill 经验。

## 任务范围

- 工作目录：`C:\Users\liuxinglin\Desktop\agent hallucination`
- 实验规模：50 个已注册 development/evolution source cases
- 运行命令（**入口已更新**，见上方 STATUS 说明；示例参数需按当前 CLI 调整）：

  ```powershell
  python -m src.sehd_v6.evolution --records records.jsonl `
    --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json `
    --output-dir evolution_output --use-gold --fitness-use-gold
  ```

- 使用 `.env` 中已配置的 provider，但绝不打印、复制或写入 API key。
- 不得把 final holdout 当作 mutation source、反馈源或调参集；不得修改数据切分、gold probe 隔离或授权配置来制造成功。

## 开始前检查

1. 查看 `git status`、当前分支和最近的演化审查报告。
2. 确认 evolution manifest、probe manifest、冻结配置和 detector records 存在，且 50 个 source 与 probe/final holdout 不重叠。
3. 先运行与本次改动相关的回归测试；若测试失败，先暂停实验修复，不得直接启动 50 例。
4. 检查 provider route 是否使用预期模型；短 JSON mutation 请求默认使用 `thinking={"type":"disabled"}`，不要因为空响应而盲目重试或把它记录成普通无效 skill。

## 实时监督规则

运行期间持续读取 stdout、最新 result/checkpoint/audit 文件和进程状态。每完成 1 例（或至少每 60 秒）报告一次简短状态，至少包含：

`processed / triggered / accepted / rejected / no_edit / retries / runtime_errors / current_case`

发现下面任一情况，立即暂停继续处理案例，保留当前 checkpoint 和原始日志，转入“诊断—修复—验证—恢复”流程：

- 未捕获异常、进程退出、provider timeout、空 final content、JSON transport 错误或结果文件损坏。
- 任何结构验证、source replay、canary、probe 或 checkpoint 计数出现不一致。
- 连续 3 个候选因同一结构原因失败，说明生成契约或修复反馈可能失效。
- 至少已有 3 个 triggered cases，但连续 5 个 triggered cases 都是 `no_edit`、`source_no_effect` 或无目标模块可执行效果。
- 已处理至少 10 个 source cases 且已有触发候选，但 `accepted_effective_skills == 0`。
- 生成了候选但 observable process delta 始终为空、expected effect 与 target module 不对齐，或规则只是重述已有 baseline。
- 有候选在 source replay 看似提升，却造成 canary/probe 回归、污染 holdout、泄漏 case/gold 字段，或被错误计入最终 champion。
- 任何“结果不理想”的迹象：只产生抽象规则、经验记忆没有新增可用负例/正例、重试没有吸收 rejection feedback，或 50 例只是形式上执行但没有有效经验。

## 诊断—修复—恢复协议

暂停后不要继续消耗案例。按以下顺序处理：

1. 从 traceback、`evolution_result.json`、step audit、gate certificate、rejection summary、experience memory 和 provider metadata 定位根因。先判断是数据/授权、检测底座、反思输入、mutation transport、结构 gate、source replay、fitness、canary/probe 还是 checkpoint/accounting 问题。
2. 只修改导致问题的机制；不得通过降低 semantic deduplication、leakage、runtime validation、canary、probe、holdout 或 provenance 门槛来“修复”失败。
3. 修改后立即运行最小复现测试，再运行完整测试套件。测试未全绿前不得恢复 50 例。
4. 保留原始失败样本，补充回归测试，记录修复前后指标和预期影响。
5. 从最近一个安全 checkpoint 恢复；已成功提交的候选不得重复计数，已失败的 source 可按有界 mutation retry 规则从未变更的 parent genome 重试。
6. 恢复后先观察 1–3 个案例，确认错误类别消失、retry 真正使用 rejection feedback、过程 delta 可观测，再继续批量执行。

如果同一个根因修复后仍连续复现 3 次，停止实验并报告阻塞点、证据和需要人工决定的选项；不要静默跳过、强行接受或无限重试。

## 有效 skill 的判定

只有同时满足以下条件，才把候选记为有效 skill 经验：

- 通过结构与运行时契约校验，目标模块和 operation 明确；
- 在 source replay 中产生目标对齐的 observable process delta，而不是只改变文本或计数；
- expected effect 与实际目标模块一致；
- 未导致稳定 canary、probe 或其他已保护案例回归；
- provenance、case/gold 隔离和 checkpoint 记录完整；
- 被写入 accepted genome/experience memory，并能在后续相关案例中复用；
- 若只是 label-free directional improvement，只能标为 provisional/shadow，不能冒充正式泛化结论。

`structural_accepted=true` 单独不等于有效 skill；`no_edit`、`source_no_effect`、invalid mutation、canary regression 和 runtime invalid 都必须进入负经验反馈。

## 完成条件与最终报告

只有在 50 个 source cases 都完成（或明确记录暂停/阻塞原因），并且没有未处理 runtime error，才结束实验。最终报告至少给出：

- 50 例实际 processed 数，以及 triggered/accepted/rejected/no_edit/retry/runtime error 计数；
- 每个 accepted skill 的 target module、operation、feature、expected effect、source replay delta、canary/probe 证据和 genome hash；
- 所有失败类别的聚合原因，以及中途修复了什么、运行了哪些测试；
- 有效 skill 是否真正进入后续经验复用；
- 仍未解决的风险和下一步建议。

整个过程中保持实时沟通；不要在长时间运行时超过 60 秒没有状态更新。
