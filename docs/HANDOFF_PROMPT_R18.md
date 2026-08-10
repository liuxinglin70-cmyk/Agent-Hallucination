# 交接 Prompt — SEHD v6 R18：boundary_terminal_conflict 错误集与 replay 可诊断化

> 使用方法：将下方整段（`=== 复制以下内容 ===` 到 `=== 复制结束 ===`）粘贴给一个新的 opencode agent 会话即可。

=== 复制以下内容 ===

继续 SEHD v6 自进化框架的迭代开发任务。仓库在 `C:\Users\liuxinglin\Desktop\agent hallucination`，当前分支 `main`，最新提交是 `0d09ac9`（ledger batch10）。R16 修复了 gold-authority effect enforcement、L2 evidence grading、L2 edits_accepted（commit `21ee022`）；R17 修复了规则执行 gap（commit `5bdd4f3`）：撤销可腐蚀的 `quarantine_certificate` 修复面，替换为可满足的 `withhold_certificate` → `error_certificate_absent` 后置条件，`classify_execution_evidence` 现在总是运行确定性后置条件求值。965 个测试全绿。**不要改动这些已修复的行为，除非有新的证据。**

## 任务目标

框架的自进化循环必须 (1) 正确产出 skill 经验轨迹；(2) 产出的 skill 能有效改进基座检测器的幻觉检测能力，最终达到 SOTA 级别幻觉检测。当前 skill 产出效率仍不达标。上一轮（R17 + batch10）已实证发现一个集中爆发的错误集：L2 `boundary_terminal_conflict`。本轮从底层修复它，而不是打补丁。

## 第一步：先读诊断文档（必须优先做）

阅读 `docs/REFLECTION_ENGINE_FIX_R18.md`（本交接的核心诊断），重点：

1. **第 2 节「新发现的错误集」**：batch10 中 6 个触发案例有 5 个归因于 L2 `boundary_terminal_conflict`，全部生成同一个策略候选 `consume_verified_clean_boundary`，仅 1 个被接受。判决分歧表里有两个案例（BFCL/012、Magentic_One/063）是 `reject_policy_fitness (replay_runtime_invalid:RuntimeError)` —— replay 在第一次检测调用前崩溃，**异常消息与堆栈未持久化**，是当前最大单点故障。
2. **第 3 节「建议的 R18 修复方向」**：P0 是 `replay_runtime_invalid` 可诊断化。

## 第二步：P0 修复 — replay_runtime_invalid 必须可诊断

`evolution_runner.py:5675` 的 `finish("reject_policy_fitness", reason=f"replay_runtime_invalid:{type(exc).__name__}")` 只记录异常类型名。要求：

1. 把 `str(exc)` 与完整 traceback 一并持久化到 `l2_policy_audit`（如新增 `replay_error_detail` 字段，或写入 `reason`），使 `replay_runtime_invalid` 不再掩盖真实故障源。
2. 将 `replay_runtime_invalid` 从普通 `reject_policy_fitness` 中独立分类（如 `reject_policy_replay_error`），与"策略确实无效"区分开，避免失败分析被基础设施故障污染。
3. 用 batch10 的 BFCL/012、Magentic_One/063 复现（单案例命令在 `docs/REFLECTION_ENGINE_FIX_R18.md` §5.4），定位 RuntimeError 的真实来源。**注意**：这两个案例的 `provider_usage_ledger.json` 显示零次 `detector:policy:*` 调用，说明崩溃发生在 replay 的检测调用阶段。
4. 为持久化逻辑写单元测试。

## 第三步：P1 评估 — boundary_terminal_conflict 的向-error 策略面

`consume_verified_clean_boundary`（`policy_evaluator.py:156-197`）只能把 terminal 移到 verified_clean。batch10 的 OpenManus/011 是 gold=True（真阳性）的同归因案例，其正确方向是向 error 收敛，但 L2 策略面没有对偶算子。评估是否需要引入向-error 的算子（如对称的 fail-closed guards），并在 `policy_reflection.py:133` 的 `boundary_terminal_conflict` 分支提供方向多样性。对照 L1 的 `CLEAN_TO_ERROR_ACTIONS` 设计。

## 第四步：小规模实验验证（迭代纪律）

1. 用 gold 标签数据，每批次并发 10 个自进化实验，每个实验仅用一条数据，每批次数据集不同（从 `experiments/protocols/used_case_ledger.txt` 排除已用案例）。
2. 每次修复后必须重新运行本批次数据，输出预期结果才算该批次完成。
3. 实时监控实验，详细分析案例；用逆向思维定位模块问题；按 /plugin:pristine 思考，从底层优化，不打补丁。
4. 每迭代一个批次就 git 提交（并追加 `used_case_ledger.txt`）。
5. 持续迭代，直到你判断可以开启真实的自进化实验才停止。

## 实验运行命令

```bash
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
python experiments/run_batch.py --batch-index 11 --size 10 --workers 10 \
  --max-mutation-calls 100 --reflection-candidates 4 \
  --canary-manifest experiments/protocols/recovery_canary_v1_manifest.json \
  --canary-limit 4 --provider-cache-mode fresh
```

（batch10 已入账本；batch11 曾启动但因外部中断未完成，`experiments/results/batch11` 已清理，案例未入账本，仍可用 batch-index 11 重新采样起步。API 配置在 `.env`，调用 autodl 的 DeepSeek-V4-Flash，中途不需要任何审批，默认最大权限，不要分派 subagent。）

## 环境与约束

- 平台 win32，Shell 是 PowerShell 5.1，工作目录 `C:\Users\liuxinglin\Desktop\agent hallucination`
- 测试：`python -m pytest tests/ -x -q`（当前 965 个通过）
- 分析产物：`experiments/results/batchNN/<case>/output/evolution_result.json`（`steps[].fitness_check.l2_policy`、`steps[].gate_decision`）、`evolution_checkpoint.json`（`gate_events`、`l2_policy_audit`、`rejected_policy_summaries`）、`provider_usage_ledger.json`（`totals.by_purpose`，判断 replay 是否真发生）
- 参考文档：`docs/REFLECTION_ENGINE_FIX_R18.md`（本交接核心）、`docs/REFLECTION_ENGINE_FIX_R17.md`（已修正根因）、`docs/SEHD_V6_ACADEMIC_DOCUMENT.md`
- 每个案例约 17-29 分钟，10 并发约 30 分钟/批次

=== 复制结束 ===
