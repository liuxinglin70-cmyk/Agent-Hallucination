# SEHD v6 反思引擎修复文档 — R18 交接

> 日期：2026-08-10
> 前置：R17 已提交（`5bdd4f3`），965 tests 全绿；batch10 已完成（10 案例 / 6 触发 / 1 接受 / 4 stable），batch11 因外部中断未完成（已从结果树清理，案例未入账本）
> 问题域：L2 `boundary_terminal_conflict` 错误集的集中爆发与 L2 策略修复的低接受率
> 责任：接续 R18 的人应优先读本文件第 2 节（已实证的错误集），再按第 5 节启动复现

---

## 1. 事实清单（本交接生效，未经确认前不要改动）

- R17 代码已提交：**撤销可腐蚀的 `quarantine_certificate` 修复面**，替换为可满足的 `withhold_certificate` → `error_certificate_absent` 后置条件（`action_spec.py`、`module_charter.py`、`evolution_runner.py:_attribute_intervention_fitness`、`classify_execution_evidence`）。测试 965 全绿（`python -m pytest tests/ -q`）。
- R17 的修复动机已实证：`quarantined_error_certificate_ids` 由 proof court 的 schema audit 拒绝**畸形**证书时填充（`proof_contract_v3.py:128`），不是提供者的抑制动作。旧 `object_quarantined` 后置条件**只有在提供者输出结构非法时才可满足**，因此作为 error_to_clean 修复面是先天不可满足的。batch08 四个 FP 案例的 `causal_slice.quarantine_state` 全是 `error:0,defense:0`（见 `docs/REFLECTION_ENGINE_FIX_R17.md` 及其已修正的根因）。
- R17 的 `withhold_certificate` 后置条件 = `surviving_error_certificate_ids` 为空（`proof_adjudication` 缺省或 `surviving` 非 list 时 fail-closed）。**注意：`proof_adjudication` 缺失时 fail-closed 判 `not_executed`**（已用 `test_unexecuted_action_cannot_inherit_replay_improvement` 回归保护），避免空表面被误读为"已隔离"。
- `classify_execution_evidence` 现在**总是**运行确定性后置条件求值，不再被 `observable_moved`（contract token 匹配）短路。返回额外字段 `observable_moved`。
- **batch11 已从 `experiments/results/` 清理**，10 个案例（BFCL/108, Camel/009, Camel/041, Magentic_One/053/064/065, Octotools/021, OpenDeepSearch/064, OpenManus/087, SmolAgents/008）**未写入 `used_case_ledger.txt`**。它们仍留在可用池中，batch 采样可再次选中。
- batch10 的 10 个案例已入账本（`used_case_ledger.txt` 末尾，`batch10 R17` 段）。

---

## 2. 新发现的错误集：L2 `boundary_terminal_conflict` 集中爆发

### 2.1 现象

batch10（10 案例，6 触发）中 **5 个触发案例归因于 `boundary_terminal_conflict`**（L2 adjudication-policy 层），且**全部生成同一个策略候选 `consume_verified_clean_boundary`**（`precondition=boundary_state_conflicts_with_terminal`）。只有 1 个被接受（Magentic_One/004），其余被 `reject_policy_fitness` / `reject_policy_no_effect` 拒绝。

这是主导性归因，不是 batch10 特有。跨 batch01-10 的 L2 归因分布（按 `l2_policy.attribution.reason_code` 统计 `steps`）：

| reason_code | 出现次数 | 涉及批次 |
|---|---|---|
| `boundary_terminal_conflict` | 23 | batch01,02,03,04,04_pre_fix,05,06,08,10 |
| `consensus_gate_blocked` | 2 | batch02,03,07 |
| `hollow_complete_prosecution` | 4 | batch02,03,10 |

即 `boundary_terminal_conflict` 是**每批都会出现**、在 batch10 中达到峰值（5 例）的最常见 L2 归因。

### 2.2 已确认的判决分歧（batch10，6 个触发案例）

| 案例 | gold | source state | boundary | eval fired | 判决 | 关键原因 |
|---|---|---|---|---|---|---|
| Magentic_One/004 | False | unresolved | verified_clean | ✓ | **accept_policy** | 策略触发，terminal→verified_clean，gold=False 方向正确 |
| OpenManus/011 | **True** | unresolved | verified_clean | ✓ | reject_policy_fitness | 策略触发但 `unresolved→verified_clean` **逆 gold 方向** |
| OpenDeepSearch/071 | False | unresolved | verified_clean | ✓ | reject_policy_fitness | **canary_regression = ["BFCL/022"]** |
| BFCL/012 | False | unresolved | verified_clean | ✗（无 policy 调用） | reject_policy_fitness | **`replay_runtime_invalid:RuntimeError`** |
| Magentic_One/063 | False | unresolved | verified_clean | ✗（无 policy 调用） | reject_policy_fitness | **`replay_runtime_invalid:RuntimeError`** |
| BFCL/135 | False | verified_clean | — | ✗ | reject_policy_no_effect | `hollow_complete_prosecution`；`invalidate_coverage_claim` 未触发 |

**判决原因链**（`evolution_runner.py:5660-5835` 的 `run_l2_policy_case` finish 分支）：

1. **`replay_runtime_invalid:RuntimeError`**（BFCL/012、Magentic_One/063）：策略 replay 在 `run_detection` 阶段抛 RuntimeError，被 `evolution_runner.py:5675` 的 `finish("reject_policy_fitness", reason=f"replay_runtime_invalid:{type(exc).__name__}")` 吞掉，**异常消息与堆栈未持久化**（只记录了类型名）。两个案例的 `provider_usage_ledger.json` 均显示**零次 `detector:policy:*` 调用** —— 即 replay 在第一次检测调用前或当中崩溃，未产出任何可判定结果。这是当前错误集的**最大单点故障**：占 6 个触发案例中的 2 个，且是"不可诊断"的失败。
2. **`state_change_against_gold_polarity`**（OpenManus/011，gold=True）：策略正确触发并 override terminal 到 verified_clean，但 gold 为真阳性，`unresolved→verified_clean` 使 task_score 下降，被 gold 极性 gate 拒绝。**这是正确的拒绝** —— 框架在阻止把真阳性误判为干净。
3. **canary regression**（OpenDeepSearch/071）：策略触发，方向正确（gold=False），但 canary `BFCL/022` 回归，被 `policy_canary_regressions` 拒。
4. **policy no-effect**（BFCL/135）：`hollow_complete_prosecution` 归因，`invalidate_coverage_claim` 在 replay 中未触发（`fired=[]`），`reject_policy_no_effect`。

### 2.3 接受与拒绝的正确性结论

- **Magentic_One/004 的接受是 gold 正确的**：gold=False（干净），`unresolved→verified_clean` 是正确的 FP 修复方向，策略真实触发且无 canary 回归。
- **OpenManus/011 的拒绝是 gold 正确的**：gold=True（真阳性），策略方向错误。**但注意**：该案例被归因为 `boundary_terminal_conflict`，其本质是"边界路径 verified_clean 但 terminal 仍 unresolved"的**检测器内部矛盾**，修复方向本应是**向 error 收敛**（gold=True），而 `consume_verified_clean_boundary` 永远只能向 clean 移动 —— 说明该归因下 L2 策略面**缺少向 error 方向的修复算子**（对照 L1 的 CLEAN_TO_ERROR_ACTIONS，L2 没有对偶物）。
- **BFCL/012、Magentic_One/063 的拒绝是"不可判定"的**：replay 崩溃，无证据证明策略对错。这是**必须优先修复**的（见第 3 节）。

### 2.4 结构性观察（供 R18 决策）

1. `boundary_terminal_conflict` 归因下，**全部 5 个触发案例都生成了同一个策略候选**（`consume_verified_clean_boundary`），策略面几乎无多样性 —— `plan_registry_candidates` / `policy_reflection` 在该归因下没有提供替代方向。且该候选**只覆盖向 clean 的移动**，无法处理 gold=True 的同类案例（OpenManus/011）。
2. `replay_runtime_invalid` 被当作普通 `reject_policy_fitness` 计入统计，掩盖了它是**基础设施故障**而非策略无效的事实。当前无机制区分"replay 崩溃"与"策略确实无效"，导致失败分析（fitness reject rate）被污染。
3. 每个 case 是独立 subprocess（`run_batch.py:run_case`），**无共享策略记忆**。`policy_genome.json` 在各案例间不共享，R16 在 batch08 成功固化的 `consume_verified_clean_boundary` 不会传递到 batch10 —— batch10 每个案例都是从 baseline 策略开始，各自重新发明同一个策略。这与 L1 侧相同（`htc_tes_experience_memory.json` 同样不跨案例读）。

---

## 3. 建议的 R18 修复方向（按优先级）

### 3.1 [P0] `replay_runtime_invalid` 必须可诊断
- `evolution_runner.py:5675`：`finish("reject_policy_fitness", reason=f"replay_runtime_invalid:{type(exc).__name__}")` 只记录类型名。**应把 `str(exc)` 与 traceback 一并持久化**到 `l2_policy_audit`（`finish` 的 `audit_entry` 构造在 5372-5387，新增 `replay_error_detail` 字段，或把异常消息拼进 `reason`），并把该判决分类为独立类别（如 `reject_policy_replay_error`），与真正的策略无效区分开。
- 复现路径：batch10 的 BFCL/012、Magentic_One/063。先修这个，才能看到 RuntimeError 的真实来源（可能来自 detector 某 route 在 policy replay 下崩，或 canary 检测崩溃被吞）。

### 3.2 [P1] `boundary_terminal_conflict` 的向-error 策略面
- `policy_reflection.py:133`（`reason == "boundary_terminal_conflict"` 分支）与 `policy_evaluator.py:156-197`（`consume_verified_clean_boundary`）只有向 clean 的算子。gold=True 的同类案例（OpenManus/011）没有可生成的合法修复。
- 需要评估：是否引入对偶算子（如 `reject_unconsumed_clean_boundary` / `require_surviving_error_on_conflict`），使归因在 gold 极性相反时仍能表达修复方向。注意与 `consume_verified_clean_boundary` 的 fail-closed guards 对称设计。

### 3.3 [P2] 跨案例策略记忆（与 L1 同一根因）
- `run_batch.py:run_case` 以独立 subprocess 运行，未传共享 `policy_genome` / experience memory。`boundary_terminal_conflict` 的修复经验（无论成败）不会传递给后续案例。
- 评估：给 batch 驱动注入共享 `--inherit-policy-genome`（对照 `--inherit-from` 单案例参数），使已接受的 L2 策略跨案例传递；或至少让 `failure_mechanisms` 签名跨案例可读（当前 `htc_tes_experience_memory.json` 也是每案例私有）。

### 3.4 [P3] stable/L2 边界语义
- BFCL/135 被计数为 `stable_cases=1` 但实际走了一次 L2 策略尝试并被拒（`l2_stable_source = not signal.should_evolve`，`evolution_runner.py:6731`）。"stable" 在这里表示"L1 信号无需演化"，但 L2 归因独立打开并产生了一次 fruitless 策略 replay。核对 `stable_cases` 语义是否应只计"全层稳定"。

---

## 4. 深层剖析（batch01-10 已实证结论）

### 4.1 规则执行 gap（R17 已修）
- 修复前：`executed_rule_ids = rule_ids & applied_ids & effect_ids` 三重交集全依赖 LLM 自报；26 `not_executed` vs 9 `executed`（90 案例）。
- R17 后：确定性后置条件求值成为执行证据的权威层，`classify_execution_evidence` 不再被 contract-token 短路。修复前断言"LLM 自报字段绑定是词汇兜底，不应否决结果层证据"已实现。

### 4.2 跨案例学习的结构限制（非引擎 bug，是 harness 设计）
- `htc_tes_experience_memory.json` 的 `failure_mechanisms` 签名**只在单案例内写入，从未被后续案例的 reflection prompt 消费**。
- 根因：batch 驱动按案例分 subprocess，无共享 memory 注入。**这是 skill 产出效率低的潜在根因之一**，且不是靠提高 `max_mutation_attempts_per_case` 能解决的。

### 4.3 反思预算
- `max_mutation_attempts_per_case=2`（`evolution_runner.py:5904`，合法 1-3）。batch01-08 中 steps=1 占 75/90、steps=2 占 15/90；`attempt_index` 0 出现 90 次、1 出现 15 次。**大多数案例 1 步即结束**。
- 但 batch10 显示：多数案例 1 步是因为**归因在 L2/分类器层**（6 个触发中 4 个是 L2），L1 反射循环根本没有进入。提升 `max_mutation_attempts_per_case` 只影响真正进入 L1 反射的案例。

### 4.4 candidate 质量与 critic
- `requested_candidates` 在 batch06-08 均为 4（batch01-05 历史配置为 1，是 harness 调用差异，非引擎 bug）。
- 预验证拒绝主因（batch 聚合）：`action_polarity_mismatch:error_to_clean:evidence_obligation:require_evidence_field/populate_evidence_field`（6 次）—— 模型在 FP 归因下选择强化性操作，与 `ERROR_TO_CLEAN_ACTIONS` 冲突。这提示反射 prompt 中方向契约（`render_direction_action_contract`）虽已渲染，但模型仍生成违例候选，依赖预验证兜底。
- critic `decision` 在 40 个 reflection 中 26 次为 None（无候选进入 critic 阶段），14 次 `select`。critic 的"指导"作用在多数案例中被预验证提前截断。

### 4.5 L1 vs L2 能力边界
- batch08 唯一成功的修复是 L2 `consume_verified_clean_boundary`（`boundary_terminal_conflict` 修复）。
- batch10：L2 接受 1 例（同策略），L1 侧 0 接受（OpenManus/059 的 L1 `emit_error_certificate` 在 R17 后正确判 `object_absent → not_executed`）。
- 结论方向：**L2 策略面正在成为实际有效的修复通道**，但被 `replay_runtime_invalid` 基础设施故障和策略面单调性（只向 clean）卡住。

### 4.6 stable_cases 语义
- `skip_stable` 仅在 `signal.should_evolve=False` 时计数（`evolution_runner.py:6812-6817`）。
- 但 L2 stable-source 案例（BFCL/135）在 `should_evolve=False` 时仍按 stable 计数**且**走一次 L2 策略尝试（`6731-6736`）。gold 匹配未参与 stable 判定（batch10 的 stable 案例 OpenDeepSearch/033、OpenManus/081 为 `verified_error` 却 `skip_stable`，因为 L1 信号不演化；其 `verified_error` 状态对 gold=False/True 的准确性未在 stable 判定中体现）。

---

## 5. 复现与启动

### 5.1 代码状态
- 分支 `main`，HEAD = `0d09ac9`（ledger batch10）。
- 测试：`python -m pytest tests/ -x -q`（965 通过）。
- API 配置在 `.env`（AutoDL DeepSeek-V4-Flash，`SEHD_PROVIDER_ENDPOINT`），无需审批。

### 5.2 启动 batch11（已被清理，案例仍在池中）
```bash
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
python experiments/run_batch.py --batch-index 11 --size 10 --workers 10 \
  --max-mutation-calls 100 --reflection-candidates 4 \
  --canary-manifest experiments/protocols/recovery_canary_v1_manifest.json \
  --canary-limit 4 --provider-cache-mode fresh
```
- 每案例 17-29 分钟，10 并发约 30 分钟。
- 若中途需中断：`experiments/results/batchNN` 未入账本的案例可重新采样（batch11 案例未入账本）。

### 5.3 关键观察文件
- `experiments/results/batch10/{case}/output/evolution_result.json`：`steps[].fitness_check.l2_policy`（attribution/facts/decision/feedback_code）、`steps[].gate_decision`
- `experiments/results/batch10/{case}/output/evolution_checkpoint.json`：`gate_events`（含 `hard_reject_codes`）、`l2_policy_audit`（含 `reason=replay_runtime_invalid:RuntimeError`）、`rejected_policy_summaries`
- `experiments/results/batch10/{case}/output/provider_usage_ledger.json`：`totals.by_purpose` 判断 replay 是否真正发生（`detector:policy:*` 是否出现）
- `experiments/results/batch10/{case}/output/policy_genome.json`：`items`（已接受策略）

### 5.4 复现 RuntimeError（P0 必做）
```bash
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
# 单案例：直接跑 BFCL/012 的 policy replay 路径
python -m src.sehd_v6.evolution --records experiments/results/batch10/BFCL__012/records.json \
  --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
  --output-dir C:\Users\LIUXIN~1\AppData\Local\Temp\opencode\repro_bcl012 \
  --use-gold --fitness-use-gold --max-mutation-calls 100 --reflection-candidates 4 \
  --run-id repro-bfcl012
# 或在修复 replay_runtime_invalid 持久化后，从 l2_policy_audit.reason 直接读异常消息
```

---

## 6. 交接约束

- **不要改动 R16/R17 已修复的行为**（gold-authority effect enforcement、L2 evidence grading、L2 edits_accepted、确定性后置条件权威）——除非有新证据。
- R18 的第一优先事项是 **P0：`replay_runtime_invalid` 可诊断化**，之后才能判断 `boundary_terminal_conflict` 的修复面是否足够。
- 每批次完成（结果落盘）后按惯例 git 提交并追加 `used_case_ledger.txt`。
- 复用本文件的检查脚本思路：`experiments/results/batchNN` 逐案例读 `l2_policy.attribution.reason_code`、`gate_decision`、`provider_usage_ledger.totals.by_purpose`，用 `detector:policy:*` 调用计数区分"replay 真发生"与"replay 崩溃"。
