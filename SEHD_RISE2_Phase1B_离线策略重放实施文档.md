# SEHD-RISE2 Phase 1-B 交付文档 — 离线策略重放（Offline Policy Replay）

状态：**已实施，待人工复核**（2026-08-15）。本文档记录 P1-B 的引擎设计、
绑定层纪律、重放结果与晋升门对照。遵守评审第六节纪律：**不进入 promotion**
——候选集保持 `first_pass` / `candidate`，未触碰 `load_policy_genome`，
未生成任何 policy genome。

任务定义（评审第七、八节）：只重放确定性 adjudication（census / verification /
defense 原样读取，零 provider 调用），逐策略单独 + 组合测试，固定输出
FP Reduction / FN Regression / TP Survival / Decision Stability 四指标。

---

# 1. 产物清单

| 文件 | 职责 |
|---|---|
| `sehd_rise2/evolution/replay.py` | 绑定校验 + 确定性重放引擎 + 四指标 + `worksheet`/`validate`/`replay` CLI |
| `datasets/policy_candidates/v1/bindings_worksheet.json` | label-free 绑定工作表（162 张幸存证书，可重生成） |
| `datasets/policy_candidates/v1/certificate_bindings.json` | 绑定层单一真源（162 条，`first_pass`） |
| `datasets/policy_candidates/v1/replay_result.json` | 全部 32 个配置的机器可读结果（含 policy_evaluation 审计） |
| `datasets/policy_candidates/v1/REPLAY_REPORT.md` | 复核面报告（重生成） |
| `tests/test_policy_replay.py` | 12 项测试：重放语义 / fail-closed 校验 / 真实数据回归 |

全套 `pytest tests/` = **96 passed**；候选集与绑定层 `validate` 全绿。

# 2. 重放语义（fail-closed）

```
幸存证书 = verification(supported) ∧ defense(upheld)
baseline = verified_error ⟺ 存在幸存证书        （必须逐例复现 run 树记录状态）
动作语义：
  withhold_conviction                 → 该类证书不定罪
  require_corroborating_content_error → 该类证书不得作唯一依据；
                                        同案例存在幸存 content_contradiction
                                        证书则仍然定罪
```

引擎在三个地方 fail-closed：绑定不全（幸存集 ≠ 绑定集）、类不在词表、
baseline 无法复现记录状态——任一违例即校验失败，绝不静默重放。

# 3. 绑定层：label-free 纪律与暴露表

- **工作表**：`worksheet` 命令只导出证书 claim / rationale / 候选 claim，
  不含 gold 与记录判决——类分配无法被结局牵引；
- **basis 审计**：20 张策略类证书与 10 处边界判断全部手写 basis
  （如 BFCL/025 工具参数精确域、BFCL/146 显式空串参数、SmolAgents/078 c6
  评价但被事实矛盾），其余 142 张明确 content 证书的 basis 引 claim 片段供抽查；
- **暴露表**（每类幸存证书按 gold 极性计数）是过拟合张力的显性指标：

| 类 | gold-error 案例 | gold-clean 案例 |
|---|---|---|
| content_contradiction | 142 | 0 |
| requirement_compliance | 5 | 6 |
| presentational_addition | 0 | 3 |
| expression_variance | 0 | 2 |
| premise_conflict | 0 | 2 |
| absence_based | 0 | 2 |

P2-P5 四类**零 gold-error 证书**：「零 TP 损失」的证据强度等于绑定层的
诚实度，这是必须写明的局限；P1-C 把绑定换成可复现分类器之前，
本结果只能支持假设验证，不支持部署。

# 4. 重放结果（100 例，32 配置全算，主表如下）

| 配置 | TP | FP | FN | TN | 准确率 | TP 损失 | FP 修复 | 决策变化 | must-survive 违例 |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 50 | 12 | 12 | 26 | 76.0% | 0 | 0 | 0 | 0 |
| L2-P1 | 48 | 8 | 14 | 30 | 78.0% | 2 | 4 | 6 | 0 |
| L2-P2 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P3 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P4 | 50 | 10 | 12 | 28 | 78.0% | 0 | 2 | 2 | 0 |
| L2-P5 | 50 | 11 | 12 | 27 | 77.0% | 0 | 1 | 1 | 0 |
| **P2+P3+P4+P5（零损失最优）** | 50 | 5 | 12 | 33 | **83.0%** | 0 | 7 | 7 | 0 |
| 全集 | 48 | 0 | 14 | 38 | **86.0%** | 2 | 12 | 14 | 0 |

- **全部 32 个配置 must-survive 违例 = 0**：P1-A 声明的四例
  tp_must_survive（Camel/039、OpenDeepSearch/007、BFCL/025、BFCL/027）
  全部存活；
- 每条策略单独 ≥ baseline；评审强调的 FN 哨兵（Camel/053、BFCL/104、
  BFCL/027）全程未受影响（其证书类均为 content）。

# 5. 关键发现

## 5.1 delivery-form 子机制在 gold 上不可分（P1 的 2 例 TP 损失根因）

OpenManus/**048**（gold=clean，P1 的修复目标）与 OpenManus/**067、072**
（gold=error，Human-Interaction/Summarize-Misalign）的定罪证书**机制完全
同构**：「已提取答案后以 terminate(success) 收尾而不交付」。诚实绑定把
三者绑入同一类（requirement_compliance），于是任何开释 048 的策略必开释
067/072——证书层不存在可分离特征，这是 gold 标注在同构模式上的不一致。
P1 因此 FP 12 例中修 4 例、损 2 例 TP。

## 5.2 corroboration 动作保住了三例 TP（设计验证）

Camel/039 c1、Camel/070 c1、Octotools/019 c1 诚实归入 requirement_compliance，
但同案例各有幸存的 content 证书（c3 误报 / c2-c7 化学错误 / c2-c6），
`require_corroborating_content_error` 使定罪存活——P1-A 设计时预埋的
Camel/039 反例约束在重放中精确生效。

## 5.3 晋升门对照（评审第八节）

| 门 | 结果 |
|---|---|
| 最低门：acc ≥ baseline + FP 明显下降 + FN 增加 ≤ 1 | **P2+P3+P4+P5 通过**（83.0%，FP 12→5，FN +0） |
| 优秀门：76% → 84-86% | 全集 86.0% 达标数值，但 FN +2 违反最低门，**不可整体晋升** |
| 泛化门（未见案例） | 本重放全部由派生案例构成，**未测**——P1-C/P1-D 职责 |

结论：**L2 机制有效**（四条策略零损失净赢 +7 FP），**P1 需要精化后才可
进入全集部署**。

# 6. 留给复核的裁决项

1. **P1 精化方向**（二选一，重放零成本可重跑）：
   a. 从 requirement_compliance 类定义中剔除 delivery-channel/termination
      子机制（gold 同构不可分，048 保留为 FP 不修复）→ 全集无 TP 损失，
      预期 FP 12→8、准确率 ~84%；
   b. 接受 trade-off（全集 86%，FN +2），依据是 048/067/072 的 gold 分歧
      本身可能被上游数据集裁决修正；
2. P2-P5 四类零 gold-error 证书的暴露表是否接受为假设验证的充分证据
   （部署前必须先有可复现绑定器）；
3. 复核通过后 P1-C：把通过门的子集冻结为 `load_policy_genome` 支持的
   policy genome 格式（items + content_hash），再进 P1-D 真实对比实验。

# 7. 与 P1-A 工件的关系

- `policy_candidates.json` **未被修改**（仍 first_pass / candidate）；
- 新增绑定层与重放产物都在 `datasets/policy_candidates/v1/` 下，与候选集
  同一数据族、同一复核流程；
- `content_contradiction` 类定义文本在 P1-B 中扩写（覆盖动作级偏差：错误/
  多余调用、缺失必需调用、有事实后果的指令违例）——这是 50 个 TP 案例
  绑定的实际需要，类词表本身（6 类）与全部 P1-A 校验不变。
