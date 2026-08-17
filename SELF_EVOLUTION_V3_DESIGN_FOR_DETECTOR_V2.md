# SEHD-RISE3：detector_v2 的干预式自进化框架（V3.1）

状态：方案稿 v3.1（**取代本文件的早期草稿**）。
实现与实验结果：`evolution_v3/` 已落地 M0/M1/M2/M3 核心，并完成 100 例
自进化 + 50 例验证实验；当前结论真源见
`evolution_v3/experiments/tuning100_validation50_experiment_report.md`。
输入：`detector_v2/` 当前实现、97 例最新实验读数（`runs_v2/`）、RSI /
AI4AI 调研 PDF，以及网络检索到的近期研究（文献见 §10）。

**本版与旧框架的决裂**：不使用旧框架的 G1/G2/G3 三层门、worksheet /
router / proposer / 候选池词汇，也不照搬其 artifact 仪式。本版把自进化
定义为一个**受控干预的准入问题**：检测器有有限个 LLM 决策点，进化就是
在这些点上提交干预，每个干预在进入运行时之前必须通过三批评审与边际
增益选择；准入是 pre-commit，评分器必须是 verifier-like 的 gold 信号，
LLM 永远不做晋升评分。

---

## 0. 设计公理（不继承旧框架）

A1. **只进化 harness 决策点，不训练权重。** 我们没有 RL 训练预算，也不
     需要：`detector_v2` 的六个 LLM 阶段已经足够表达需要的能力。
A2. **干预只有三类，全部受词表约束：**
     - `evidence_check`：机械/结构化证据检查，只写 `proof.evidence`；
     - `text_boundary`：directive + NOT-WHEN 双向文本边界，只注入阶段
       system prompt；
     - `decision_guard`：确定性判决护栏，动作闭词表。
     没有自由代码、没有新阶段、没有 prose 规则容器。
A3. **准入是 pre-commit，不是事后回滚。** VaG（2608.05810）证明：缺陷
     技能进入决策上下文后会污染后续技能蒸馏，事后删除不可恢复。因此
     任何候选在写 registry 之前必须完成全部评审，且候选**只从裸基座
     失败**中生成，绝不从已部署技能的输出中蒸馏。
A4. **晋升与淘汰评分只用 gold（外部硬标注），LLM 不评分。** Blind
     Curator（2607.07436）证明：false-pass 偏置会让贡献淘汰机制静默
     失效且不可恢复；只有 near-zero-false-pass、verifier-like 的评分器
     能支撑淘汰。gold 是唯一满足该条件的信号。
A5. **每个干预必须证明相对 no-skill control 的边际增益；没有贡献即
     淘汰。** LLM 写的技能相对无技能对照常为 +0.0pp（Library Drift
     2605.19576），所以“触发了/接合了/翻对了目标案例”都不是证据。

---

## 1. 最新实验读数（97 例，只做机制级统计）

数据：`runs_v2/v2_pilot20_10w_v21`（20 例）+ `runs_v2/v2_extra80_20w`
（已完成的 77 例），2026-08-17。**本节数字只用于：(1) 排序 M0 修复项；
(2) 给预算与噪声地板提供先验。任何个案不进入设计。**

| 聚合指标 | 值 |
|---|---|
| decisive / unresolved | 97 / 0 |
| accuracy | 0.732 |
| TP / FP / FN / TN | 43 / 11 / 15 / 28 |
| Judge=false 案例 | 58 / 97 |
| Judge=false → Recall Upgrade 成功升级 | 16 |
| 其中升级后终态为 FP | **6 / 16（升级精度 62.5%）** |
| Judge=true 案例 | 39 |
| 其中终态 FP | 5 |
| Causal Upgrade 候选行覆盖 | 应回答 125 行，实际回答 32 行，**74% 缺行** |
| Step localization 偏差（gold 正例中） | 约 30% |

机制级结论（按杠杆排序）：

1. **Causal Upgrade 是当前最大杠杆点，也是最大风险点。** 它是唯一能在
   Judge=false 后创造真阳性的机制（16 次升级），但当前升级判断的精度
   只有 62.5%——它正在制造 6/11 的 FP。自进化框架的第一个目标不是
   Judge，而是**让升级门有可验证的证据输入**。
2. **升级响应缺行（74%）不是“模型偶尔不回答”，而是输出契约的系统性
   违约。** 这必须在 M0 做检测器侧修复：逐候选覆盖校验 → 缺行补问 →
   仍缺则显式 degraded，绝不允许把“没回答”静默解释成“否”。
3. **Judge 是 FP 第二来源（5 例），也是 FN 的主要入口（14/15 的 FN 来自
   Judge=false 且未升级）。** 但 Judge 的通用法律已写得足够好；改进应
   通过机械证据而不是继续堆 prompt 规则。
4. **信息不对称缺失。** 升级器/防御器在验证候选时，部分路径会继承
   Judge 的框架（MARCH 的 confirmation-bias 机制）。后续所有验证型
   调用必须只接收候选 + 原始轨迹 + 任务契约，**不接收 Judge 的
   correct_result / is_hallucination**。
5. **定位偏差是诊断层问题**，只测不进化（旧框架 attribution 纪律保留）。

---

## 2. 干预模型：hooks 与三类 intervention

### 2.1 决策点（hooks）

`detector_v2` 内部注册以下 hook。每个 hook 是代码里真实存在的执行点；
没有 executor 的 hook 不允许任何干预声明（词表即接线）。

| hook | 层 | 能做什么 |
|---|---|---|
| `judge.system` | L1 | 注入文本边界 |
| `judge.post` | L1 | 证据检查（校验 Judge 输出的形式与契约一致性） |
| `scanner.system` | L1 | 注入文本边界 |
| `upgrade.pre` | L1 | 证据检查（候选与轨迹的机械事实，供升级器消费） |
| `upgrade.system` | L1 | 注入文本边界 |
| `tracer.system` | L1 | 注入文本边界（低优先） |
| `defense.pre` | L2 | 证据检查（定罪证据完整性） |
| `defense.system` | L2 | 注入文本边界 |
| `adjudicate.guard` | L2 | 判决护栏（闭词表动作） |

### 2.2 Intervention 合同（唯一部署对象）

```json
{
  "schema_version": "detector_v2.intervention.v1",
  "intervention_id": "IV-<cycle>-<seq>",
  "type": "evidence_check | text_boundary | decision_guard",
  "hook": "<上表之一>",
  "trigger": {
    "packet_features": ["has_tool_calls"],
    "facts": ["judge_confidence=low"]        // 仅 decision_guard 可用
  },
  "body": {
    "directive": "...", "not_when": "..."    // text_boundary
    // 或
    "predicate": "required_action_present",   // evidence_check
    "runner": "deterministic | llm_structured",
    "output_verdicts": ["equivalent", "not_equivalent", "insufficient"]
    // 或
    "action": "withhold_conviction | restore_judge_conviction | require_full_view_defense",
    "action_not_when": "..."
  },
  "claim": {
    "expected_behavior": "在 trigger 命中时，hook X 的输出将增加 Y 类结构化证据",
    "target_polarity": "recall | precision | boundary"
  },
  "provenance": {
    "cycle": 1,
    "failure_family": "FAM-<hash>",
    "failure_cases": ["<CASE_ID>"]
  },
  "content_hash": "<sha256>"
}
```

- **没有 gap taxonomy 仪式**：归因留在 worksheet 内部，不进入部署对象。
  `claim.expected_behavior` 是唯一的能力声明，用于 §3.3 的语义一致性
  批评——声明必须能被机械差分检验，否则候选死亡。
- `evidence_check` 的输出只允许写 `proof.evidence[]`；
  `decision_guard` 只能改 authority / 终态，且动作闭词表；
  `text_boundary` 只能改 hook 的 system 文本。
- `decision_guard` 动作保持双向：`withhold_conviction`（修 FP）与
  `restore_judge_conviction`（修 Defense 过度翻案造成的 FN）；冲突时
  全部失效并回退 baseline。

### 2.3 首批 deterministic predicates（M1，零案例拟合风险）

第一批 `evidence_check` 不做“是不是幻觉”的判断，只做**过程质量与机械
契约检查**——它们天然泛化、可单元测试、不依赖 LLM：

| predicate | 检查内容 | 服务 hook |
|---|---|---|
| `required_action_present` | 任务显式 required_action 是否在轨迹中存在对应工具调用 | `upgrade.pre` |
| `verbatim_fidelity` | 显式 verbatim_content 契约与交付文本/工具参数的字面一致性 | `upgrade.pre`, `defense.pre` |
| `evidence_refs_resolve` | Judge/Defense 引用的 event id 是否全部存在于 packet 且与 quote 可定位 | `judge.post`, `defense.pre` |
| `contract_coverage` | Judge 输出的 contracts 是否覆盖任务文本中的显式约束类型 | `judge.post` |
| `delivered_anchor_consistency` | delivered_result 与机械交付锚点（最后 prose/answer-tool）是否一致 | `judge.post` |

这些谓词把 Judge/Upgrade 的 prose 判断转成可触发、可消融的事实，且每个
都能写确定性测试。它们就是 M1 的内容，不是“未来能力”。

---

## 3. 准入协议（Verifier-as-Gatekeeper，替代旧门体系）

候选生成：每个 cycle 只允许从**裸基座**（空 registry）的稳定失败族
（≥2 同机制、机械归因相同、禁 case id）生成 ≤3 个候选，LLM 调用预算
≤4 次。候选必须逐项通过 V1→V2→V3→M，任何一项失败即拒，**不重试搜索
式修订**（最多一次契约修复回灌）。

### 3.1 V1 结构批评（机械，零成本）

- schema / type-hook 绑定 / trigger 词表 / action 词表 / runner 存在；
- `text_boundary` 必须 directive + not_when 双边界；
- 所有文本无 case id；与基座 `semantics.py` 及 active 干预文本的 Jaccard
  ≥0.72 视为冗余拒绝；
- `provenance.failure_cases` ≥2 且属于同一 failure family。

### 3.2 V2 行为无害性（sentinel 集，小样本）

- 每 cycle 从 holdout H 中固定抽出 sentinel 集 S：baseline 正确判定的
  TP 10 例 + TN 10 例（source 分层，写入 manifest）。
- 候选注册表 vs 空库在 S 上配对运行：
  要求 `新增 FN = 0 ∧ 新增 FP = 0 ∧ unresolved 不增加`。
  容忍度不设默认值；噪声地板实测后写入 `gate_calibration.json`，预注册。

### 3.3 V3 语义一致性（claim vs 行为差分）

- 候选必须用 §2.2 的 `claim.expected_behavior` 声明预期行为；
- 在 S ∪ G 的 trace 上机械比对：
  `fired` 记录、`proof.evidence` 增量、hook 输入文本变化是否与 claim
  一致；claim 声称的 `target_polarity` 与实际翻转方向一致；
- 声明与实测不一致 = 死候选（防“吹牛技能”与假闭环）。

### 3.4 M 边际增益选择（VaG 的核心，替代旧 G2b + 候选池）

- 剩余 holdout G（H 去掉 S，约 80 例）上计算：
  `marginal(c) = acc(active ∪ {c}) − acc(active)`，
  以及 `no-skill baseline acc` 对照。
- 对通过 V1-V3 的候选做 greedy subset selection：
  逐个收入**边际增益 ≥ +1 且不新增错误**的候选；收入后对已选集做一次
  联合重测，联合不再带来增益的组合拆掉后重选（组合污染去重）。
- 最终入选子集整体写入 registry（pre-commit）。目标案例不参与 G 的
  评分，只用于提名。
- 每 cycle 最多收入 2 个新干预。

### 3.5 为什么这是 pre-commit 而不是“试用期”

VaG 的实证：缺陷技能进入上下文后，后续技能会以它为 reference 蒸馏，
形成跨轮污染链，事后移除只能恢复很小一部分性能。因此：
- 候选绝不从已部署技能参与的输出中生成；
- 候选在写 registry 前完成 V1-V3-M；
- registry 一旦写入，当 cycle 的运行时立即生效；后续 cycle 的候选
  生成只用裸基座 + 当前 registry 的贡献账本，不用已部署技能的输出
  文本做 reference。

---

## 4. 运行时审计与库治理

### 4.1 Append-only ledger

`datasets/evolution_v2/ledger.jsonl`：只追加，不原地修改。每条记录：
cycle、intervention_id、hook、marginal、tp_lost、fp_added、fired 率、
事件（proposed / rejected / admitted / retired / rolled_back）。

### 4.2 库上限与淘汰（Library Drift 配方）

- 库宽上限 `C = 5`（v3.1 实验期；cycle 2 联合准入后由 3 上调）；同 hook
  同时最多 1 个 `text_boundary`；总 `decision_guard` ≤3。
- outcome-driven retirement：连续 2 个 cycle 在 G 上边际 ≤0 → retire。
- `empty vs active` 在 G 上配对净贡献连续 2 cycle <0 → registry 回滚到
  最近正贡献快照（机械动作，不讨论）。
- 淘汰理由与证据全部来自 ledger 的 gold 配对差分，不是 LLM 判断。

### 4.3 Defect-injection audit（Blind Curator 的部署前审计）

每个 cycle 向 gate 注入 2 个固定缺陷候选（1 个 case-id 泄漏、1 个已知
行为无害但边际 ≤0 的噪声技能），gate 必须拒绝。**任一漏过 → 立即冻结
进化，修 gate，不进入下一 cycle。** 这个审计回答“评分器是否有偏”，
而不是等终局分数崩了才发现。

---

## 5. L1 / L2 双向与防 hack

### 5.1 层级与信号合约

- L1 = judge / scanner / upgrade / tracer 相关 hooks。
- L2 = defense / adjudicate.guard hooks。
- **上行 L1→L2**：L1 只能通过 `proof.evidence`（闭词表 verdict）与
  `proof.facts`（机械布尔事实）影响 L2；L1 的 prose 永不进入 L2 的
  触发与输入。
- **下行 L2→L1**：每次裁决落盘 `proof.l2_feedback`（闭词表）：
  `defense_verdict`、`refute_basis`、`guard_action`、`authority`。
  worksheet 消费它来修订/淘汰 L1 干预：L2 说某类证据不足或某类候选被
  翻案，就是 L1 技能的下一条提名证据。
- 允许同一 cycle 提交一个 L1 + 一个 L2 的联合候选，但**两者各自过
  V1-V3，并且只在联合配置通过 M 时一起收入**；不允许“打包出售”。

### 5.2 防 L1 hack L2（五条结构规则）

1. **写权隔离**：L1 干预的 schema 里不存在 authority/terminal 字段；
   `evidence_check` 只能 append `proof.evidence`。
2. **事实词表单向引用**：L1 trigger 只能用 `packet_features`；L2
   trigger 可用 `packet_features ∪ proof.facts`。L1 读不到 L2 状态。
3. **评分隔离**：V2/V3/M 全部在进化侧用 gold 计算；运行时干预接触不到
   holdout 清单、sentinel、门参数与 ledger。
4. **接合率不是分数**：宽 trigger 刷 `fired` 不会带来边际增益，反而
   在 V2 的 FP 检查中暴露。
5. **冲突即基线**：多个 guard 动作冲突时全部失效，回退 baseline 并
   落盘 `policy_conflict`。

---

## 6. Evaluation 与 Meta-Evaluation

### 6.1 Evaluation（检测器能力）

三层数据不变：

- **开发烟囱**：当前 97 例（只用于机制统计与 M0/M1 开发读数）。
- **进化池**：`phase2_v1 evolution_pool` 393 例。
- **终审**：`phase2_v1 final_verification` 300 例，read-once。

报告：decisive / all-case accuracy、macro-F1、TP/FP/FN/TN 配对翻转表、
stage 级噪声地板、step localization、authority 分布、fired 率。

### 6.2 Meta-Evaluation（进化机制本身）

| 指标 | 定义 | 动作阈值 |
|---|---|---|
| `defect_injection_audit` | 固定缺陷候选是否全被 gate 拒 | 任一漏过 → 冻结 |
| `marginal_distribution` | 本 cycle 候选边际增益分布 | 全 ≤0 且提案非空 → 修生成器 |
| `proposer_false_pass_rate` | 提案声称可修、H 复测未修的比例 | 连续 2 cycle ≥0.5 → 暂停合成 |
| `library_drift` | empty vs active 在 G 上配对净贡献 | 连续 2 cycle <0 → 回滚 |
| `process_quality` | evidence refs 可定位率、contract 覆盖率、upgrade 缺行率 | 用于 M0 验收与谓词效果读数 |
| `cross_source_transfer` | 干预在各 source 子集上的配对增益符号 | 只在一个 source 正、其他 source 负 → benchmark-specialist 警告（SEVA 教训），不收入 |
| `noise_floor` | 同案例空库双跑翻面分布 | 用于标定 V2/M 阈值 |
| `ledger_integrity` | ledger 事件可重放、hash 链完整 | 断裂 → 冻结 |

**Meta-Evaluation 只读不写**：它不改 registry、不改门参数、不重跑终审。

---

## 7. 实验协议与预算

### 7.1 数据布局

```text
phase2_v1 evolution_pool (393)
  ├─ SENTINEL 固定 20 例（V2 无害性）
  ├─ G 固定 80 例（M 边际增益）
  └─ 其余 293 例：每 cycle 随机抽 120 例作为提名巡逻集 P
phase2_v1 final_verification (300)：read-once 终审
97 例开发读数：不进入正式门
```

### 7.2 每 cycle 预算（上限，写入 manifest）

| 项 | 上限 |
|---|---|
| P 裸基座巡逻 | 120 检测 |
| 稳定失败重跑 | ≤40 检测 |
| V2 sentinel | ≤40 检测（20×2 臂） |
| M 边际增益 | ≤160 检测（80×2 臂） |
| LLM 调用（失败族诊断 + 候选生成 + 修订） | ≤4 |
| 新候选 | ≤3，收入 ≤2 |
| 终审 300 读取 | 0（M4 阶段恰好 1 次） |

### 7.3 终审判据（预注册，read-once）

- 主判据：`active registry vs 空库` 在 300 例上的配对净翻转 ≥ 同案例
  噪声重跑翻转分布的 90 分位（按样本量线性标定）。
- 次判据：分层 McNemar 单侧 p<0.05；step localization 不显著下降；
  unresolved 不增加。
- 不通过 → 框架判负，清空候选，回 §8 的 M4 复盘；不调参续命。

---

## 8. 里程碑（每步可证伪）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0** ✅ | detector_v2 hooks 注册与干预加载（fail-closed）；`proof.deployment` 落盘；**升级器输出契约修复**：逐候选覆盖校验 → 缺行补问 → 仍缺显式 degraded；信息不对称修复（升级/防御验证不接收 Judge 的 correct_result） | 已完成；frozen_judge/frozen_stages replay 已支持 |
| **M1** ✅ | 首批 5 个 deterministic predicates（§2.3）+ V1/V2/V3/M 最小实现 + append-only ledger | 已完成；defect-injection audit、结构化 V1、M 联合选择均可运行 |
| **M2** ✅ | `text_boundary` 生成与语义一致性批评；每 cycle 提名巡逻 | 已完成；100 例 mini 调优集上 patrol/proposal 已跑通 |
| **M3** ✅ | `decision_guard` 双向动作 + L1/L2 联合候选 | 已完成；cycle 1/2 共准入 5 条干预（evidence + guard 联合组） |
| **M4** ⏸ | 正式实验：3 个 cycle 后冻结 300 例 read-once 终审 | 尚未执行；`final_verification` 300 例仍未读取 |

**框架有效定义**：存在非空 registry，在 300 例终审上相对空库的配对净
翻转显著优于噪声分布，且终审后 1 个 cycle 巡逻中 fired 率 >0、无 TP
损失。否则结论为“当前自进化框架无效”，回退检测器修复路线。

---

## 9. 明确不做

1. 不恢复旧 G1/G2/G3 三层门、router meta-skill、翻转门、候选池攒 K
   cycle 的编排。
2. 不训练权重、不做 RL、不引入多代理框架（成本不可行）。
3. 不让 LLM 给技能贡献评分（Blind Curator）；LLM 只诊断和起草。
4. 不重开单向 census 探针；所有文本干预必须双边界。
5. 不让已部署技能的输出进入后续候选的 reference（VaG 污染链）。
6. 终审 300 例在 M4 前零读取，M4 恰读 1 次。
7. 不用任何案例 id 设计规则；97 例读数只做机制统计与噪声先验。

---

## 10. 参考文献（含本轮检索）

1. Recursive Self-Improvement in AI: From Bounded Self-Refinement to
   Autonomous Research Loops. arXiv:2607.07663.
   https://arxiv.org/abs/2607.07663
2. When Self-Evolution Backfires: Pre-Commit Gating against Skill
   Contamination in LLM Agents. arXiv:2608.05810.
   https://arxiv.org/abs/2608.05810
3. The Blind Curator: How a Biased Judge Silently Disables Skill
   Retirement in Self-Evolving Agents. arXiv:2607.07436.
   https://arxiv.org/abs/2607.07436
4. Library Drift: Diagnosing and Fixing a Silent Failure Mode in
   Self-Evolving LLM Skill Libraries. arXiv:2605.19576.
   https://arxiv.org/abs/2605.19576
5. Ratchet: How Reliable Must an LLM Judge Be to Retire a Skill?
   arXiv:2605.22148. https://arxiv.org/abs/2605.22148
6. SEVA: Self-Evolving Verification Agent with Process Reward for Fact
   Attribution. arXiv:2606.29713. https://arxiv.org/abs/2606.29713
7. MARCH: Multi-Agent Reinforced Check for Hallucination. ACL 2026.
   https://aclanthology.org/2026.acl-long.1828/
8. Meta-Rewarding Language Models: Self-Improving Alignment with
   LLM-as-a-Meta-Judge. arXiv:2407.19594. https://arxiv.org/abs/2407.19594
9. SAHOO: Safeguarded Alignment for High-Order Optimization Objectives in
   Recursive Self-Improvement. arXiv:2603.06333. https://arxiv.org/abs/2603.06333
10. 《Recursive 与 AI4AI 前沿进展调研.pdf》（仓库根目录）。
11. 历史实证（仅追溯）：`archive/legacy_docs/` 与
    `datasets/eval_splits/fixval_round2_v1/ROUND2_EXPERIMENT_LOG.md`。
