# 交接 Prompt — SEHD v6 反思引擎修复 + 深度分析

> 使用方法：将下方整段（`=== 复制以下内容 ===` 到 `=== 复制结束 ===`）粘贴给一个新的 ZCode agent 会话即可。

=== 复制以下内容 ===

继续 SEHD v6 自进化框架的迭代开发任务。仓库在 `C:\Users\liuxinglin\Desktop\agent hallucination`，当前分支 main，最新提交是 `21ee022`（R16）。R16 已修复三个问题：gold-authority effect enforcement（`_enforce_expected_effect_observation`）、L2 evidence grading（`_l2_promotion_evidence` 接入 `promote()`）、L2 edits_accepted（`finish()` 闭包）。961 个测试全绿。**不要改动这些已修复的行为，除非有新的证据。**

## 任务目标

框架的自进化循环必须：(1) 正确产出 skill 经验轨迹；(2) 产出的 skill 能有效改进基座检测器的幻觉检测能力，最终达到 SOTA 级别幻觉检测。当前我对 skill 产出效率非常不满意，需要你从底层优化，而不是打补丁。

## 第一步：修复规则执行 gap（必须优先做）

阅读 `docs/REFLECTION_ENGINE_FIX_R17.md` 的第 2 节（根因一），那里有完整的诊断。

核心问题：L1 变异生成的 `quarantine_certificate` 规则全部编译成功但从未判定为 executed。`executed_rule_ids = rule_ids & applied_ids & effect_ids`（evolution_runner.py:1914）三重交集全部依赖 LLM 自报（`rule_effects`/`applied_rule_ids` 字段）。两种失败模式已在 batch08 中实证：
- 模式 A（BFCL/095、BFCL/134）：DeepSeek-V4-Flash 完全不返回 rule_effects 遥测 → applied_ids 为空
- 模式 B（Camel/017、Magentic/049）：LLM 自报了应用但报告字段（`candidates`、`violation_state`）与 ActionSpec 的 `output_field`（`certificate_id`）不匹配 → effect_ids 为空

而 `evaluate_action_spec_postcondition`（action_spec.py:392-549）对 `object_quarantined` 有**确定性**检查（读 `proof_adjudication.quarantined_error_certificate_ids` 是否非空），但它被 `classify_execution_evidence`（action_spec.py:572）的 `not observable_moved` 短路了，从未被触发。

按第一性原理修复：**确定性后置条件检查 > LLM 自报字段绑定**。具体要求：
1. `_attribute_intervention_fitness`（evolution_runner.py:1892-1914）：对 `rule_ids & applied_ids` 的每条规则运行确定性后置条件检查，满足即计入 `executed_ids`，不再要求 `_field_binds_output_field` 匹配（字段绑定是词汇兜底，不否决结果层证据）。
2. `classify_execution_evidence`（action_spec.py:572）：当后置条件可确定性求值时始终运行，不被 contract token 匹配短路。
3. `_field_binds_output_field` 增强集合键语义映射（`candidates`/`certificates`/`candidates[i].quarantined` 等）。
4. 为每个修复点写单元测试，模拟模式 A 和模式 B 两种场景。

## 第二步：深入分析反思引擎与框架的潜在问题

阅读 `docs/REFLECTION_ENGINE_FIX_R17.md` 的第 3、4 节。已知问题：`max_mutation_attempts_per_case=2` 硬编码默认值（evolution_runner.py:5889，合法范围 1-3），所有触发案例 steps=1-2 即结束、halt=False、total_eval 仅 0-2（上限 200），修复成功率仅 31.2%（5/16）。你需要用 batch01-08 的完整数据（`experiments/results/batch*` 目录）做更深入的分析，重点：

1. **反思引擎是否跨案例学习**：检查 `htc_tes_experience_memory.json` 的 `failure_mechanisms` 签名是否被后续案例的 reflection prompt 消费。若只是"写入不读取"，经验记忆就是死数据 —— 这是 skill 产出效率低的潜在根因之一。
2. **candidate 生成质量**：`requested_candidates=4` 但 `generated_candidates` 常为 2-4，prevalidation 后更少。检查 reflection prompt 与 `render_action_spec_contract` 的约束。
3. **critic 有效性**：critic 的 decision/reason 是否真正指导候选选择。
4. **L2 策略 vs L1 规则的能力边界**：batch08 唯一成功的修复是 L2 `consume_verified_clean_boundary`。评估是否应优先扩展 L2 策略面。
5. **反思深度预算**：评估提高 `max_mutation_attempts_per_case` 的可行性与风险（阅读 4198 行、5580 行注释的设计意图）。
6. **stable 案例基线正确性的验证**：确认 stable_cases 语义。

基于分析结果实施底层优化（不是打补丁），并验证 skill 产出效率的提升。

## 第三步：小规模实验验证（迭代纪律）

1. 用 gold 标签数据，每批次并发 10 个自进化实验，每个实验仅用一条数据，每批次数据集不同（从 `experiments/protocols/used_case_ledger.txt` 排除已用案例）。
2. 每次修复后必须重新运行本批次数据，输出预期结果才算该批次完成。
3. 实时监控实验，详细分析案例；用逆向思维定位模块问题；按 /plugin:pristine 思考，从底层优化，不打补丁。
4. 每迭代一个批次就 git 提交。
5. 持续迭代，直到你判断可以开启真实的自进化实验才停止。

## 实验运行命令

```bash
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
python experiments/run_batch.py --batch-index 10 --size 10 --workers 10 \
  --max-mutation-calls 100 --reflection-candidates 4 \
  --canary-manifest experiments/protocols/recovery_canary_v1_manifest.json \
  --canary-limit 4 --provider-cache-mode fresh
```

（batch09 曾用 batch-index 9 启动但已手动终止，`experiments/results/batch09` 目录不完整，不要复用它；用 batch-index 10 起步。API 配置在 `.env`，调用 autodl 的 DeepSeek-V4-Flash，中途不需要任何审批，默认最大权限，不要分派 subagent。）

## 环境与约束

- 平台 win32，Shell 是 Git Bash，工作目录 `C:\Users\liuxinglin\Desktop\agent hallucination`
- 测试：`python -m pytest tests/ -x -q`（当前 961 个通过）
- 分析产物：`experiments/results/batchNN/<case>/output/evolution_result.json`（steps、fitness_check、causal_execution）、`htc_tes_experience_memory.json`（经验轨迹与失败机制）
- 参考文档：`docs/REFLECTION_ENGINE_FIX_R17.md`、`docs/SEHD_V6_ACADEMIC_DOCUMENT.md`
- 每个案例约 17-29 分钟，10 并发约 30 分钟/批次

=== 复制结束 ===
