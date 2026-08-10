# SEHD v6 反思引擎修复文档 — R17

> 日期：2026-08-10
> 前置：R16 已提交（gold-authority effect enforcement + L2 evidence grading + L2 edits_accepted），961 tests 全绿
> 问题域：反思引擎（mutation/reflection loop）的规则执行 gap 与 skill 产出效率

---

## 1. 问题总览

batch06-08（R13–R16）30 个案例的聚合统计：

| 指标 | 数值 |
|---|---|
| 基线正确（stable） | 14/30 (46.7%) |
| FP 未修复（gold=False 触发未接受） | 10 |
| FP 修复 | 5 |
| FN 未修复（gold=True 触发未接受） | 1 |
| **修复成功率（accepted/triggered）** | **5/16 = 31.2%** |
| 经验轨迹 | 20 条（成功仅 2 条） |

用户对 skill 产出效率不满意。深入分析发现两个根因，一个在**规则执行层**，一个在**反思循环预算层**。

---

## 2. 根因一：规则执行 gap（executed_rule_ids 恒空）

### 2.1 现象

L1 变异生成的 `quarantine_certificate` 规则**全部编译成功但从未被判定为 executed**。4 个 FP 案例（batch08）的轨迹：

| 案例 | 注入 | LLM 自报 applied | LLM 自报 output_fields | causal_exec status |
|---|---|---|---|---|
| BFCL/095 step0 | ✓ (2 routes) | ✗ 空 | ✗ 空 | not_executed |
| BFCL/134 step0 | ✓ (3 routes) | ✗ 空 | ✗ 空 | not_executed |
| Camel/017 step0 | ✓ (3 routes) | ✓ | `candidates` | not_executed |
| Magentic/049 step0 | ✓ (3 routes) | ✓ | `candidates`, `candidates[0].quarantined`, `violation_state` | not_executed |

两种失败模式：
- **模式 A（BFCL/095, BFCL/134）**：规则注入到提示词，但 DeepSeek-V4-Flash 完全不返回 `rule_effects`/`applied_rule_ids` → `applied_ids = ∅`。
- **模式 B（Camel/017, Magentic/049）**：LLM 应用了规则并自报，但报告的字段（`candidates`, `violation_state`）与 ActionSpec 的 `output_field`（`certificate_id`）不匹配 → `effect_ids = ∅`。

### 2.2 根因代码链

`executed_rule_ids` 在 `_attribute_intervention_fitness`（evolution_runner.py:1914）中计算：

```python
executed_ids = sorted(rule_ids & applied_ids & effect_ids)
```

- `rule_ids`：编译后规则 id（内容哈希）—— 几乎恒非空
- `applied_ids`：来自 `_experience_intervention_trace`，读 provider 自报的 `bccse_applied_rule_ids`（skill_integration.py:458-487 从 LLM 返回的 `rule_effects` 提取）
- `effect_ids`：要求 LLM 自报的 `output_fields` 能绑定 ActionSpec 的 `output_field`（`_field_binds_output_field`，evolution_runner.py:1835-1852）

**三重交集全部依赖 LLM 自报**。规则条件本身是自然语言，只被哈希成 rule_id 并注入提示词，**从未被确定性求值**。执行判定完全信任 LLM 是否 (a) 承认应用、(b) 报告正确的字段名。

### 2.3 已有但被绕过的确定性检查

`evaluate_action_spec_postcondition`（action_spec.py:392-549）对 `object_quarantined` 后置条件有**确定性**检查：读 `proof_adjudication.quarantined_error_certificate_ids` 是否非空（action_spec.py:433-457）。这是对实际结果模式的真实验证，不依赖 LLM 自报。

但它被 `classify_execution_evidence`（action_spec.py:572）短路了：

```python
if not observable_moved:
    return {"execution_class": "no_observable_movement", ...}
```

`observable_moved` 来自 `_expected_effect_audit` 的 contract 字段 token 匹配（evolution_runner.py:393-429），需要 delta token 命中 `quarantined_certificates:` 前缀。但 LLM 自报的是 `candidates`/`violation_state`，delta 是 `candidate_count:increased`、`prosecution_certificates:increased` —— 永远不会出现 `quarantined_certificates:` token。于是**后置条件检查从未被触发**，而它恰恰是唯一能证明"证书真的被隔离了"的确定性证据。

### 2.4 修复方案（第一性原理）

**执行证据的权威层级：确定性后置条件检查 > LLM 自报字段绑定。**

1. 在 `_attribute_intervention_fitness`（evolution_runner.py:1892-1914）中，对 `rule_ids & applied_ids`（至少 LLM 承认应用了）的每条规则，运行 `evaluate_action_spec_postcondition` 的确定性检查；只要后置条件满足（如 `quarantined_error_certificate_ids` 非空），就计入 `executed_ids`，**不要求** `_field_binds_output_field` 匹配。字段绑定是词汇层兜底，不应否决结果层证据。
2. `classify_execution_evidence`（action_spec.py:572）的 `not observable_moved` 短路改为：当后置条件可确定性求值（postcondition ∈ `_POSTCONDITION_CHECKS` 且有结果表面可查）时，**始终运行**后置条件检查，不再被 contract token 匹配短路。
3. `_field_binds_output_field` 增强：增加对结果路径的语义映射 —— provider 报告的 `candidates`/`candidates[i].quarantined`/`certificates` 等集合键，若其行内存在 `certificate_id` 字段且规则后置条件涉及证书隔离，也应视为绑定。

**预期效果**：Camel/017、Magentic/049 这类 LLM 确实隔离了证书的规则将被判定为 executed，进入 fitness 评估，而非被 `intervention_not_executed` 一刀切判负。

---

## 3. 根因二：反思循环预算过浅（skill 产出效率低）

### 3.1 现象

- `max_mutation_attempts_per_case=2`（evolution_runner.py:5889 默认值，合法范围 1–3，3381 行硬限制）
- 所有触发案例 **steps=1 或 2 即结束**，`halt=False`，`total_eval` 仅 0–2（上限 200）
- 触发案例平均只消耗 6–12 次 mutation_calls

### 3.2 后果

反思引擎在单个案例上几乎不做深度探索：
- 第 1 轮生成候选 → fitness `no_effect`/`worse` → retry 1 次
- 第 2 轮仍失败 → 循环直接结束（attempt_index+1 >= 2 不满足 retry 条件）
- 没有"换模块重试"、"换操作符重试"、"跨轮次学习失败模式"的机制

31.2% 的修复成功率直接受限于此：**一次失败的 mutation 没有第二次机会去调整策略**。

### 3.3 修复方向（需进一步设计）

1. 将 `max_mutation_attempts_per_case` 提升（如 5–8），允许每个案例做多轮"失败-归因-换策略"的深度反思。
2. **反思引擎应消费自身的失败机制签名**（`htc_tes_experience_memory.failure_mechanisms`）来引导下一轮候选生成 —— 例如 BFCL/095 第 0 步 `responsible_step_missing` 后，第 1 轮应直接避开产生该签名的操作面（`blocked_action_surfaces` 机制已存在，但只在同案例内用一次）。
3. 检查 4198 行注释声称的"max_mutation_attempts_per_case=2 有界同源搜索预算"的设计意图，确认提高上限不会破坏 convergence/halt 语义。

---

## 4. 其他待深入分析的问题（交接后执行）

1. **反思引擎是否跨案例学习？** 检查 `htc_tes_experience_memory` 的失败机制签名是否被后续案例的 reflection prompt 消费。若没有，经验记忆是"写入但不读取"的死数据。
2. **candidate 生成质量**：`requested_candidates=4` 但 `generated_candidates` 常为 2–4，prevalidation 后更少。检查 reflection prompt 与 `render_action_spec_contract` 的约束是否足够具体。
3. **critic 的有效性**：critic 的 `decision`/`reason` 是否真正指导了候选选择，还是只是走形式。
4. **L2 策略 vs L1 规则的能力边界**：batch08 唯一成功的修复是 L2 `consume_verified_clean_boundary`。是否 L1 规则对 FP 的修复能力先天弱于 L2 策略？是否应优先扩展 L2 策略面。
5. **stable 案例基线正确的验证**：stable 案例的"正确"是基于 gold 判定的吗？需确认 `stable_cases` 的语义。

---

## 5. 复现与验证路径

```bash
# 复现规则执行 gap（单元级）
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
python -m pytest tests/test_evolution_hardening.py -x -q

# 复现（整批）
python experiments/run_batch.py --batch-index 10 --size 10 --workers 10 \
  --max-mutation-calls 100 --reflection-candidates 4 \
  --canary-manifest experiments/protocols/recovery_canary_v1_manifest.json \
  --canary-limit 4 --provider-cache-mode fresh

# 关键观察文件
# experiments/results/batch08/{case}/output/evolution_result.json  → steps[].fitness_check.causal_execution
# experiments/results/batch08/{case}/output/htc_tes_experience_memory.json  → 经验轨迹与失败机制
```

---

## 6. 已确认的事实清单（交接用）

- R16 已修复：gold-authority effect enforcement、L2 evidence grading、L2 edits_accepted（commit 21ee022）
- batch08 R16：5 triggered，1 accepted（L2 `boundary_terminal_conflict` 修复，首次成功 L2 修复），961 tests 绿
- 4 个 FP 案例的 quarantine 规则全部 `not_executed`（详见 §2.1 表）
- `max_mutation_attempts_per_case=2` 是硬编码默认值（evolution_runner.py:5889），合法范围 1–3（3381）
- 经验轨迹生成正确：batch08 有 7 条轨迹，记录了方向/成败/特征/失败机制签名
- provider 自报是执行判定的唯一依据（skill_integration.py:456-504），无确定性兜底
