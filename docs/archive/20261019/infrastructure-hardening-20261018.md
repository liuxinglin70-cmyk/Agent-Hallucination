# 检测器基座加固与排序层实测（2026-10-18）

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

本次工作对应三项收尾与两项代码加固。核心结论如下：

1. **已测源码现已进入版本历史。** 两轮真实验证（`f04571a3` 清单 / `0fd9bcf0` detector 身份）跑在工作树上，其中 `action_evidence.py`、`action_context.py` 等从未提交。提交 `ae29425` 把该树原样固定，未改行为。
2. **FP 门槛改为配对统计判据**，原文的绝对阈值（`current.fp <= baseline.fp`）不再作为冻结标准；按仓库约定，历史报告保持原字节、只在开头标注。
3. **排序层已接线但离线实测为负**：留一家庭交叉验证下，学习权重对 top-1/top-3 覆盖没有提升（−0.9pp / −0.45pp）。该结论写入文档，不宣称收益。
4. **`OptionalBudget` 已加固**：17 项不变量测试，并在证据里区分"可选额度耗尽"与"案例预算耗尽"。

---

## 1. 三项收尾

### 1.1 源码入历史

| 项目 | 内容 |
| --- | --- |
| 提交 | `ae29425 feat: bind measured next-action mechanism source into version history` |
| 内容 | 26 个修改文件 + 4 个此前未跟踪的生产/测试文件（含 `detector/action_evidence.py`） |
| 行为 | 无变化；两轮实验读数仍对应此树 |
| 边界 | `artifacts/`、`datasets/raw/` 按 `.gitignore` 不入库；凭据仅以环境变量名引用 |

### 1.2 FP 门槛：从计数比较改为配对检验

原判据 `current["fp"] <= baseline_fp` 的比较方式有问题：在本轮 100 例（38 阴性）上，两计数之差的标准误超过 2 例，因此"6→7"与"6→6"的判定强度几乎相同，而真正恶化的"1 修好 / 3 变坏"却因净差为 1 被放过。

新判据在 `experiments/reading.py:false_positive_parity` 中实现，复用 `evolution/metrics.py` 已有的精确二项尾（`mcnemar_improvement_p`），只在**成对不一致的 clean 案例**上检验：

- 新增 ≤ 撤除：直接通过（`not_worse=True`），无需统计掩护；
- 新增 > 撤除：做单侧精确检验，`p < significance_level` 才判失败；
- 两个原始计数（`fp_added` / `fp_removed`）与 `discordant` 一并写入读数。

**实测标定**：该判据比原文更保守。`(6 新增, 1 撤除)` 的 `p=0.0625` 仍不判失败，需要 `(8, 1)` 即 `p=0.0195` 才触发。这是 38 个阴性样本能支撑的分辨力上限，不是实现宽松。用它回看本轮（1 例机制相关 FP、3 例 clean 回退、1 例修正）不会判失败；但这只说明原文门槛的分辨力不足，**不构成对第二轮结论的重新裁决**——第二轮仍按当时冻结标准记录为未通过。

判据定义同时写入 `experiments/plan.py:DEFAULT_CRITERIA["effect"]["false_positive_rule"]`，使运行与声明不会漂移。

### 1.3 P0 报告更正

两处与代码不符已标注（原文保留）：

| 位置 | 不实主张 | 代码事实 |
| --- | --- | --- |
| `P0_FIXES_COMPLETED.md`；`docs/analysis/p0-fixes-completion-report.md` | 已实现 `clipping_map` 裁剪映射表 | 全仓库无此标识符；实际是实现前缀 6→12 与引用匹配宽松化 |
| 同上 | 候选阈值放宽为"至少 1 票 divergent + quorum" | `detector/action_evidence.py:141` 仍为严格多数 `len(divergent) * 2 > len(ballots)` |

---

## 2. 排序层：已接线，离线实测为负

### 2.1 做了什么

新增 `detector/candidate_scorer.py`，把原本闲置的 `causal_audit.scorer_artifact` 接成真正的排序输入：

| 设计决定 | 理由 |
| --- | --- |
| 只改写**同优先级档内**次序 | 优先级表是语义判断，学习信号没有推翻它的证据；测量到的损失恰在档内并列 |
| 无 artifact 时键退化为历史键 | 未配置部署的排序、结果、回放身份逐字节不变 |
| 权重读为 `Decimal` 并精确求和 | 定点量化会把 `1e-13` 压成 0，静默恢复位置序；`float` 会让不同分并列 |
| 非法 artifact 抛 `ScorerArtifactError` | 未知特征、非有限权重、schema 不符都必须拒绝加载，不能静默当作零分 |
| 特征全部取自既有 `risk_signals` | 零 provider 请求，不改变任何阶段可见的内容 |

排序键为 `(priority, -score, -verified, position)`；无 artifact 时为 `(priority, -verified, position)`，与改动前完全一致。

### 2.2 实测结果：没有收益

在 693 例语料、443 个可定位阳性案例上，用留一家庭交叉验证（权重在其余 6 个族上拟合，在留出族上用**真实** `select_causal_candidates` 路径评估）：

| 指标 | 当前排序 | 学习排序 | 差值 |
| --- | ---: | ---: | ---: |
| top-1 命中 | 38.15% | 37.25% | **−0.90pp** |
| top-3 命中 | 65.24% | 64.79% | **−0.45pp** |

逐族看，BFCL、Camel、Magentic_One、OpenDeepSearch 的 top-1 完全相同，OpenManus（45→42）、SmolAgents（10→9）变差，Octotools top-3 少 1 例。**没有一族变好。**

原因是可解释的：7 个特征里 `verified_signals`、`explicit_failure` 在全语料上几乎恒定（`verified_deviation` 供给为 0，工具失败只占 4%），`earlier_position` 与位置序本身高度共线，剩下能区分的信息不足以在档内重新排序。这与 `distinct_signal_patterns == 35` 一致：特征空间本身贫乏。

**结论：接线保留（它是 RSI 循环需要的可写入口，且默认关闭无副作用），但不宣称任何精度收益。** 报告 §8 的"排序层是主要约束"仍成立，而"用这 7 个特征就能学到排序"被本轮否定。

### 2.3 真正有效的是宽度，不是排序

同一探针的覆盖曲线：

| k | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 金标覆盖 | 38.1% | 49.4% | 65.2% | 77.0% | 82.4% | 88.9% | 94.8% | 97.1% |

`top_k=3 → 6` 是 +23.7pp，远大于任何排序改动。约束来自 `max_projection_events=16` 与上下文成本，不是排序函数。这条留给 RSI 第一轮作为可测改动，不在本次改动默认值（`top_k` 仍为 3，部署配置不变，避免无授权地改变成本）。

现状结构事实一并记录：43.8% 的案例连排序键都重复（`duplicate_sort_key_rate=0.989`），63.4% 的案例最高档有 2 个以上事件并列，31.8% 有 4 个以上。

---

## 3. 预算加固

### 3.1 `OptionalBudget` 不变量

`detector/tests/test_optional_budget.py`，17 项，覆盖：

- 可选请求/输入字节/输出 token 三个维度都不能侵入诊断预留；
- 并发 64 线程争抢时恰好放行 `父上限 − 预留` 次，被拒请求不计数（`30/34` 精确断言）；
- 同一案例内换预留值被拒绝，相同预留允许共存；
- 预留超出策略、缺字段、布尔值、负数一律 `ValueError`；
- 截止时间确实比父预算早 `预留秒数`。

### 3.2 归因分离

`AddedBudgetExceeded` 新增 `origin`（`case_budget` / `diagnostic_reservation`）。`health` 与 `warnings` 保持原样，所有既有断言（`health == "budget_exhausted"`）不受影响；新增的 `exhaustion={"resource","origin"}` 让读者能区分：

- 批次排队把可选额度耗光（本轮 13/100 例的真实原因）；
- 该案例自身预算真的用尽。

两者此前记录完全相同，等于把排队延迟翻译成测量失败。

---

## 4. 验证

| 检查 | 结果 |
| --- | --- |
| 新增/受影响测试 | `test_candidate_scorer.py` 9 项、`test_optional_budget.py` 17 项、`test_effect_reading.py` 31 项，全部通过 |
| 因果回归 | `test_causal_diagnostics.py`、`test_causal_audit.py`、`test_predicate_dsl.py` 129 项通过 |
| 排序行为不变性 | 无 artifact 时 top_k 序列与历史键逐项相同（有专门断言） |
| 全量套件 | 见 `artifacts/code_quality/` 本次记录 |

## 5. 未做与边界

- **未改** `top_k` 默认值、部署配置、判定/定位语义、投票与听证规则。
- **未做**同源码 off/on 消融；本文件的排序结论是离线覆盖测量，不含模型行为。
- 排序层的负结果只否定"这 7 个特征 + 每特征独立 log-lift"，不否定"更好的特征或联合排序可带来收益"。
- FP 配对判据的保守性（需 8:1）是其真实分辨力，不应在更大阴性样本出现前被放宽到伪显著。
