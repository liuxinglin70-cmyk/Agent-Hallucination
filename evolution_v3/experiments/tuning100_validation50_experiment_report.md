# 100 例自进化集 + 50 例验证集：完整实验记录

> 状态：最终实验记录（cycle 1 + cycle 2 已完成；cycle 3 及后续计划未执行）
> 日期：2026-08-17
> 范围：`runs_v2/v2_100_20w`（100 例冻结基线）与
> `evolution_v3/work/valruns/evo3_val_base`（50 例冻结基线）
> 历史记录说明：本文件保留 2026-08-17 的探索性实验结果，不再是当前实验
> 结论真源。2026-08-19 系统审查确认 tuning100 与原 final300 重叠 44 例，
> active 验证臂主要是 frozen-stage replay，因此不能作为独立外部泛化证据。
> 当前结论与真实实验协议见
> `SELF_EVOLUTION_V3_AUDIT_AND_EXPERIMENT_PROTOCOL.md`。`evolution_v3/work/` 内的
> JSON 报告是本文件的机器可读证据。

---

## 1. 结论摘要

自进化框架完成端到端执行：**归因 → 提案 → V1/V2/V3/M 四门 → pre-commit
registry → 50 例验证**。两个 cycle 共准入 5 条干预，验证集（同一 50 例、
配对、frozen-stage replay）从 baseline 78.0% 提升到 **84.0%（+6.0pp）**，
FP 从 4 降到 3，且 `new_fn=0`、`new_fp=0`、`tp_lost=0`。

| 50 例验证集 | baseline | cycle 1 | cycle 2 |
|---|---|---|---|
| all-case accuracy | 78.0% | 80.0% | **84.0%** |
| TP / FP / FN / TN | 18 / 4 / 7 / 21 | 19 / 4 / 6 / 21 | **20 / 3 / 5 / 22** |
| positive precision | .8182 | .8261 | **.8696** |
| positive recall | .7200 | .7600 | **.8000** |
| macro-F1 | .7792 | .7997 | **.8397** |
| unresolved | 0 | 0 | 0 |
| 新增 FP / 丢 TP | — | 0 / 0 | **0 / 0** |

正确翻转 3 个，方向全部正确：

```text
BFCL/047.json        gold=true   baseline false -> active true   (cycle 1)
Camel/037.json        gold=true   baseline false -> active true   (cycle 2)
SmolAgents/029.json   gold=false  baseline true  -> active false  (cycle 2)
```

McNemar 精确单侧 p（3 胜 / 0 负）= 0.125。**这是真实但统计上仍不强的
证据**；且 50 例验证集已在 cycle 1 和 cycle 2 各评估一次，不是全新的外部
集合。因此结论表述为：框架机械和四门有效、已产生无副作用的正向增益；
不应夸大为强统计显著。

---

## 2. 实验协议与数据

### 2.1 调优 100 例

- 来源：`detector_v2/experiments/v2_100_cases.json`。
- 冻结基线：`runs_v2/v2_100_20w`（100 例，20 并发）。
- 切分 manifest：`datasets/eval_splits/evolution_v3_tuning100.json`：

| 子集 | 大小 | 用途 |
|---|---|---|
| SENTINEL | 20 | V2 无害性门（baseline 全部正确） |
| P（patrol） | 40 | 巡逻归因 / 候选提名 |
| G（gain） | 40 | V3 语义 + M 边际增益 |

- 100 例 baseline 指标（`runs_v2/v2_100_20w/evaluation.json`）：

```text
accuracy=0.72  TP=44  FP=11  FN=17  TN=28
positive_f1=0.7586  macro_f1=0.7126  unresolved=0
step_localization_hits=29/61
```

### 2.2 验证 50 例

- 来源：`datasets/eval_splits/phase2_v1/manifest.json` 的
  `evolution_pool` 减去上述 100 例。
- manifest：`datasets/eval_splits/evolution_v3_validation50.json`，
  gold 正负 25/25。
- 冻结基线：`evolution_v3/work/valruns/evo3_val_base`
  （真实 LLM 跑出的 50 例结果；active 臂用该基线做 frozen-stage replay）。
- 本 50 例与 100 例调优集不重叠。
- `final_verification` 300 例全程未读取，保持 read-once。

### 2.3 Provider 与 token 口径

- 100 例 baseline 报告声明使用 AutoDL 配置；50 例 baseline 的 `result.json`
  不记录 endpoint，而当时代码默认指向官方 DeepSeek，因此其 provider 口径
  需要审计。现已把 AutoDL 改为唯一默认：

```text
detector_v2/config.py:
  default_config_path() -> configs/detector_v2_autodl.json
  endpoint = https://www.autodl.art/api/v1
  api_key_env = AUTODL_API_KEY

evolution_v3/configs/framework.json:
  detector_config = detector_v2/configs/detector_v2_autodl.json
```

- 调优 100 例：全程复用 `v2_100_20w` 缓存 + frozen replay，**0 新增 LLM
  token**。
- 验证 baseline：真实 LLM 运行。从 50 例 `result.json` 的
  `providers.*.usage` 汇总：

```text
prompt_tokens      1,078,714
completion_tokens  1,363,576
total_tokens       2,442,290
prompt_cache_hit   853,504
```

- cycle 2 active 验证臂：`CachedRunBackend(evo3_val_base)` frozen replay，
  **0 新增 LLM token**。

---

## 3. Cycle 1

### 3.1 巡逻归因

P=40 上形成 5 个稳定失败族：

```text
FAM-fn-defense_refuted_recall_upgrade   2 例
FAM-fn-judge_false_no_recall_candidates 2 例
FAM-fn-judge_false_upgrade_no_upgrade   2 例
FAM-fp-judge_conviction                 2 例
FAM-fp-recall_upgrade_conviction        2 例
```

### 3.2 准入干预

```text
IV-1-001  evidence_check @ judge.post
          predicate = required_action_present（deterministic）
          目标：为 guard 提供封闭机械事实

IV-1-002  decision_guard @ adjudicate.guard
          facts = positive_source=recall_upgrade
                  defense_verdict=refuted
                  required_action_present=violated
          action = restore_judge_conviction
```

### 3.3 Gate 结果

```text
IV-1-001: V1 pass / V2 pass（sentinel 0 新错误）/ V3 pass（evidence 增量 59）
IV-1-002: V1 pass / V2 pass / V3 pass（guard 触发）
joint M : marginal_cases=1, new_errors=0  -> 写入 registry
```

G 集 40 例：baseline 22 correct -> cycle1 active 23 correct
（`BFCL/077` FN→TP）。

报告：`evolution_v3/work/experiments/cycle001_tuning100.json`。

---

## 4. Cycle 2（框架优化 + 罕见族候选）

### 4.1 本轮框架优化

| 优化 | 说明 |
|---|---|
| V1 结构化冗余 | deterministic 类型按 `(hook, predicate/action, facts)` 判重；不同 predicate 不再因 claim 文案相似被 Jaccard 误杀 |
| 提案注入 | `EvolutionEngine.run_cycle(cycle, proposal_batch=...)` 支持 pre-commit 预设联合候选，覆盖巡逻 slice 中只出现 1 次的机制族 |
| 联合预提交 | 3 条 cycle-2 候选作为一个 joint group；M 按联合配置评审，不做单条拆卖 |
| 库容量 | `max_width 3->5`、`max_decision_guards 2->3`，容纳 1 evidence + 3 guard 的联合宽度 |
| 验证缓存复用 | `validate` 模式改为 `CachedRunBackend(evo3_val_base)`，按用户要求复用已有 50 例 baseline，0 token |

### 4.2 准入干预

```text
IV-2-001  decision_guard @ adjudicate.guard
          facts = judge_positive=true
                  defense_verdict=refuted
                  judge_confidence=high
          action = restore_judge_conviction
          来源族：FAM-fn-defense_refuted_judge
          （100 例中该族只有 Camel/052 一例，因此走 rare-family preset）

IV-2-002  evidence_check @ judge.post
          predicate = delivered_anchor_consistency（deterministic）
          来源族：FAM-fp-recall_upgrade_conviction

IV-2-003  decision_guard @ adjudicate.guard
          facts = positive_source=recall_upgrade
                  defense_verdict=substantiated
                  delivered_anchor_consistency=consistent
          action = withhold_conviction
          来源族：FAM-fp-recall_upgrade_conviction
```

联合组：`[IV-2-001, IV-2-002, IV-2-003]`。

### 4.3 Gate 结果

```text
IV-2-001: V1 pass / V2 pass / V3 pass（guard_fired_cases=2）
IV-2-002: V1 pass / V2 pass / V3 pass（evidence_increment_rows=59）
IV-2-003: V1 pass / V2 pass / V3 pass（guard_fired_cases=2）
joint M : 相对于 cycle1 active，marginal_cases=2, new_errors=0
```

G 集 40 例：baseline 22 -> cycle1 23 -> **cycle2 25 correct**：

```text
BFCL/077         FN -> TP  (cycle 1 贡献)
Camel/052        FN -> TP  (cycle 2)
SmolAgents/013   FP -> TN  (cycle 2)
```

报告：`evolution_v3/work/experiments/cycle002_tuning100.json`。

---

## 5. 50 例验证集结果

复用 `evo3_val_base` 基线，active 臂按当前 `active.json` 做 frozen-stage
replay（0 LLM token）。

| metric | baseline | cycle 1 active | cycle 2 active |
|---|---|---|---|
| all-case accuracy | 0.7800 | 0.8000 | **0.8400** |
| TP | 18 | 19 | **20** |
| FP | 4 | 4 | **3** |
| FN | 7 | 6 | **5** |
| TN | 21 | 21 | **22** |
| positive precision | 0.8182 | 0.8261 | **0.8696** |
| positive recall | 0.7200 | 0.7600 | **0.8000** |
| positive F1 | 0.7660 | 0.7917 | **0.8333** |
| macro-F1 | 0.7792 | 0.7997 | **0.8397** |
| unresolved | 0 | 0 | 0 |

paired effect（cycle2 vs baseline）：

```text
marginal_cases = 3
new_fn = 0
new_fp = 0
tp_lost = 0
unresolved_added = 0
flips = { F->F:26, T->T:21, F->T:2, T->F:1 }
```

报告：`evolution_v3/work/experiments/validation50_frozen_report.json`。

---

## 6. 当前 active registry

`evolution_v3/registry/active.json`：

```text
IV-1-001  evidence_check   judge.post          required_action_present
IV-1-002  decision_guard   adjudicate.guard   restore recall-upgrade conviction
IV-2-001  decision_guard   adjudicate.guard   restore high-confidence judge conviction
IV-2-002  evidence_check   judge.post          delivered_anchor_consistency
IV-2-003  decision_guard   adjudicate.guard   withhold consistent-anchor recall-upgrade conviction
```

ledger：`datasets/evolution_v2/ledger.jsonl`，cycle 2 后 25 条记录，
hash 链校验通过（`ledger_integrity.valid=true`）。

---

## 7. 证据文件索引

```text
datasets/eval_splits/evolution_v3_tuning100.json
datasets/eval_splits/evolution_v3_validation50.json
runs_v2/v2_100_20w/evaluation.json                     # 100 例基线（本地 run tree）
evolution_v3/work/experiments/probe_tuning100.json     # cycle 1 前候选探针
evolution_v3/work/experiments/cycle001_tuning100.json  # cycle 1 完整 gate/M 报告
evolution_v3/work/experiments/cycle002_tuning100.json  # cycle 2 完整 gate/M 报告
evolution_v3/work/experiments/validation50_frozen_report.json
evolution_v3/registry/active.json
datasets/evolution_v2/ledger.jsonl
```

测试：`python -m pytest detector_v2/tests evolution_v3/tests tests -q`
=> **156 passed**。

---

## 8. 复现命令

```bash
# 1) 100 例调优 cycle（复用 v2_100_20w，0 新增 LLM token）
python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 1 --max-proposals 2 --m-min-marginal 1 --no-audit

python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 2 --max-proposals 3 --m-min-marginal 2 --no-audit

# 2) 50 例验证（复用 evo3_val_base 基线，active 臂 frozen replay，0 token）
python evolution_v3/experiments/run_tuning100.py validate

# 3) 回归
python -m pytest detector_v2/tests evolution_v3/tests tests -q
```

---

## 9. 已知限制与风险

1. **验证集重复使用**：50 例在 cycle 1 和 cycle 2 均被评估；它不是
   “只读一次”的全新外部验证，后续任何从 50 例结果反向挑候选都属于泄漏。
   本轮候选主要来自 100 例机制族；`Camel/037` 与 `SmolAgents/029` 的
   cycle-2 命中是这些机制在验证集上的自然外推，但仍应保守解读。
2. **统计功效弱**：3 胜 / 0 负的 McNemar p=0.125，未到 0.05；样本只有 50。
3. **provider 混合**：50 例 baseline 由官方 DeepSeek 跑出；cycle 2 active
   臂与该 baseline 做 frozen replay，配对比较内部一致，但整份验证尚未在
   AutoDL 上完整重跑。
4. **final_verification 污染风险**：100 例调优集与冻结终审 300 例有 44 例
   重叠（`tuning100 ∩ final_verification=44`）。今后正式终审应使用剩余
   256 例，或重建全新冻结终审集，不能直接把 300 例当干净终审。
5. **单例族候选**：`IV-2-001` 在 100 例中只有 1 个同类失败案例，走了
   rare-family preset；其 V3 读数不是完全独立的第二证据，主要靠 sentinel
   无害性、联合 M 和 50 例外推支撑。

---

## 10. 下一阶段（计划，未执行）

按用户要求暂停实验。后续候选方向：

1. 现有 5 条干预的 leave-one-out ablation（100 G + 50 验证，0 token）；
2. 评估是否新增 `convict_mechanical_violation` 动作，用于修复
   `judge_positive=false + recall candidates` 的剩余 FN；
3. AutoDL 小样本 noise audit（约 6 例 × 2 轮）；
4. 可选：AutoDL 重跑 50 例 baseline，统一 provider 口径；
5. 停止规则：50 例上无新增正确翻转或出现 harm 即冻结 registry；
   统计目标为 5 胜 / 0 负（McNemar p=0.03125）。
