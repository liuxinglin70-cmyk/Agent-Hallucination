# 检测器之上的 RSI 框架：项目建模、失效诊断、文献边界与设计

更新：2026-10-17。本文面向后续框架搭建，不修改生产代码。

阅读顺序：§1 项目建模 → §2 现有进化为何失败 → §3 文献与 novelty 边界 → §4 框架设计 → §5 代码接入点 → §6 科研反思与可证伪主张 → §7 实施与验收 → **§8 底座结构的再发现（第二轮迭代，含全量实测）** → **§9 总纲（结论与 novelty 定位）**。

若只读两节：§8（这个底座到底哪里可以改进，有数据）与 §9（应该怎么定位，不要过度声称）。§3–§7 是这两节之前的推理过程，其中 §4.5、§3.3(B)、§3.4 已被 §8.5 与 §3.5 修正。

文中的证据等级分四种，混用会让后续决策失真：

- **代码事实**：在当前 checkout 中可直接读到，附 `file:line`。
- **实测事实**：本轮（2026-10-17）用仓库自身函数在 693 例语料上跑出的数字，附统计口径；复现脚本见 §8.1 与 §8.4。
- **历史事实**：来自 `docs/archive/self-evolution-audit-20260928.md` 记录的三轮运行，只对那次运行成立。
- **文献事实**：本次 2026-10-17 检索核实过的内容；未在本轮检索中核实的不写成结论。
- **推断**：本次分析提出，尚无可执行证据。

§8 是对 §3–§7 的**修正**，不是补充。它基于全量实测发现：排序层（`top_k=3` + 静态 8 档优先级）才是当前可达精度的主要约束，而 §4 设计的规则层位于其下游；`verified` 层在当前语料上无供给，导致 §4.5 的 `CE_l` 不成立。阅读 §4 时应同时读 §8，冲突处以 §8 为准。

一个必须先说清的更正：审计报告（§4.1.2、§4.2.1）记录的若干接口缺陷，在当前代码中**已经部分修复**。§2.2 逐条对照。后续设计不能建立在过期诊断上。

---

## 1. 项目建模

### 1.1 研究对象的三层结构

这个仓库不是"一个幻觉分类器"，而是三层耦合系统。把层次分清楚是后续所有设计的前提。

```
层 3  诊断规则层  r = (ω, g, c, m, π)        进化作用于这一层
        ↑ 编译为
层 2  干预注册表  intervention v1            部署状态 R_t
        ↑ 在运行时被读取
层 1  五阶段流水线 judge → recall → upgrade → tracer → defense → adjudicate
        ↑ 读
层 0  输入投影   record {question, history} + evidence snapshot
```

层 0 是唯一不可进化的部分：`detector/dataset.py:19` 的 `project_record` 只保留 `{question, history}`，gold 全部留在评估侧（`gold_fields`）。这条边界是整个框架可信的前提，任何 RSI 设计都不得触碰。

层 1 的调用图由 `detector/api.py:215 _detect` 固定编排，是"执行图"，当前不可改写（`evolution/capability_surface.py:145` 明确 `execution_graph_rewrite: False`）。

层 2 是进化真正写入的地方。四类干预 × 十个 hook × 四个 runner，构成一个封闭但非平凡的可执行空间：

| 维度 | 取值 | 位置 |
| --- | --- | --- |
| 干预类型 | `evidence_check` `text_boundary` `decision_guard` `recall_trigger` | `interventions.py:50` |
| hook | `judge.system` `judge.post` `recall.trigger` `scanner.system` `upgrade.pre` `upgrade.system` `tracer.system` `defense.pre` `defense.system` `adjudicate.guard` | `interventions.py:54` |
| runner | `deterministic` `typed_dsl` `typed_semantic` `sandboxed_component` | `interventions.py:285` |
| guard 动作 | `withhold_conviction` `restore_judge_conviction` `require_full_view_defense` `convict_mechanical_violation` | `interventions.py:75` |
| 目标极性 | `recall` `precision` `boundary` `resolution` | `interventions.py:82` |
| 目标种类 | `detection` `localization` `joint` `resolution` | `interventions.py:91` |

层 3 目前**没有显式表示**。这是本次分析最重要的结构判断：`evolution` 直接操作层 2 的干预字典，中间缺了一层"诊断规则"的语义对象。§2 会说明这正是失败的机制根因之一。

### 1.2 检测器的判定语义：四态而非二态

这是设计 RSI 时必须尊重的第二个结构事实。一次检测输出三个正交维度（`detector/result.py:12`）：

| 维度 | 取值 | 含义 |
| --- | --- | --- |
| 终态 `state` | `verified_error` / `verified_clean` / `unresolved` | 未决 ≠ clean |
| 判定权威 `decision_authority` | `authoritative` / `degraded_judge_only` / `invalid` | 只有 authoritative 进正式预测 |
| 定位权威 `localization_authority` | `authoritative` / `degraded_localization_conflict` | 与判定权威独立计量 |

定位目标是 `dataset_first_error_event.v1`（`detector/localization.py:5`），单位是轨迹事件位置而非原始 step id（`localization.py:31` 的 `gold_step_mapping` 处理映射，无法唯一映射时返回 `ambiguous`/`unmapped` 且不进分母）。

含义对 RSI 很硬：**任何以"对不对"为标签的反馈都丢掉了两个维度**。"未决"和"判错"是不同事件——前者是仪器故障，后者是算法错误。把二者合并成一个 loss，进化器会去修复仪器而不是修复规则。这正是三轮运行失败的直接形态（§2.1）。

### 1.3 可用于学习的信号：逐案、逐阶段、可对齐

检测器产出的 `proof` 是一个异常丰富的诊断载体，逐项列出可用的监督信号及其可信度：

| 信号 | 位置 | 逐案可得 | 能否作监督 |
| --- | --- | --- | --- |
| Judge 多票 ballots 与分票 | `judge.py:194` 返回 `provider.vote` | 是 | 是（不确定性信号） |
| Recall 候选列表 | `proof["recall_candidates"]` | 是 | 是 |
| Causal upgrade 逐候选行 | `proof["causal_upgrade_rows"]` | 是 | 是 |
| Tracer 定位步与引用 | `proof["step_tracer"]` | 是 | 是（与 gold_step 对齐后） |
| Defense 裁决与理由 | `proof["defense"]` | 是 | 是 |
| 部署事件（哪条干预真的触发） | `proof["deployment"]` | 是 | 是（行为证据） |
| 因果诊断 `profile/candidates/graph/audit` | `causal_audit.py:315` | 是 | 部分（多数为 hypothesis） |
| 风险上下文 `risk_context.v1` | `risk_context.py` | 是 | 仅 hypothesis |
| 语义轨迹 | `semantic_trace.py` | 是 | `claim_equivalence=unestablished` |
| 下一步预测与比较 | `proof["action_forecast"]` | 是 | 43/100 有预测，仅 8 条非 unknown |
| 定位冲突标记 | `result.py:36` | 是 | 是 |
| 阶段健康 | `stage_status` | 是 | **是（必须用于排除）** |

这张表是 RSI 框架的原料清单。它说明一件关键的事：**这个检测器已经产出了可以做结构化信用分配的全部信息，缺的是把这些信息组织成学习信号的那一层。** 现有 `evolution` 只消费了其中很小一部分（family 前缀 + judge 合同 + 谓词信号），大量信息在 `proof` 里被产出后丢弃。

### 1.4 现有进化机制的真实形状

`evolution/engine.py:3442 _run_cycle` 的实际阶段序列：

```
sentinel coverage 预检 → audit（缺陷注入自检）→ patrol（18-120 例裸底座巡检）
→ prepare_families（机制归纳 + 正常邻居 + 能力计划）
→ 起草（LLM proposer 或 development 循环）
→ STRUCTURAL → HARMLESSNESS → BEHAVIOR →（M）→ combination → 准入
```

三个数字决定了循环的实际容量，必须记住：

- `budget.llm_calls = 4`（`evolution/config.py` 默认），`max_proposals = 3`。
- `development_adapter.py:38,84` 两处硬编码 `max_proposals=1`。
- `predicate_dsl.py:41 MAX_RULES = 16`、`MAX_CONDITIONS = 4`。

也就是说：一个机制每轮最多拿到 1 个候选、整个周期最多 3-4 次模型调用。这不是"迭代优化"，这是"每轮抽一次样"。历史运行 C3 的 `budget_exhausted` 正是这个结构的直接后果。

### 1.5 评估口径：三个目标不能相加

`evolution/metrics.py` 已经把三个目标分开实现了，这是很好但被低估的基础设施：

- `paired_delta`（:425）：检测的配对混淆增量，含 `new_errors`、`unresolved_added`、`health_regressed`、`decisive_fixed/broken`。
- `paired_localization_delta`（:1260）：分母是冻结批次本身而非两臂都判出的子集，因此"少检出"不能伪装成"定位变好"；`joint_broken` 是定位保护量。
- `target_outcome`（:293）与 `objective_marginal`（:365）：按 detection/localization/joint/resolution 分派。

`paired_localization_delta` 里这句注释值得完整保留：**"a miss must not be able to leave the denominator, otherwise a candidate could look like it improved localization by detecting less."** 这已经是正确的实验设计，后续框架必须复用而不是重写。

---

## 2. 现有进化为什么失败：机制级诊断

三轮 7 个开发版本、4 个正式候选、0 准入。审计报告给了 9 条根因排序。本节不复述，做两件事：**在当前代码上重新核验哪些仍然成立**，以及**指出审计没说到的那一层**。

### 2.1 已被修复的（不能再当作待办）

| 审计条目 | 当前代码事实 | 结论 |
| --- | --- | --- |
| §4.2.1 父候选在提示边界丢失 | `development_adapter.py:63-76` 现在传入 `parent_program` 与 `development_diagnosis`；`proposer.py:568-570` 序列化这两个字段 | **已修复** |
| §4.1.2 `_deviation_for` 把"有义务"当"已违规" | `mechanisms.py:453-456` 现在要求 `verdict in _VIOLATION_VERDICTS` 才返回该 deviation | **已收窄** |
| §3.2 `noise_floor=deterministic_replay` 过强 | `engine.py:3858-3866` 现在检查 `LiveDetectorBackend` 下非 guard hook 存在即标记 provider stage | **已修复** |
| §4.1.3 混合族身份依赖第一位成员 | `mechanisms.py:738-740` 现在 `unique = sorted(set(signatures))` 后取 `unique[0]` | **已修复** |
| §4.1.2 合同标签直接升为 confirmed | `mechanisms.py:762-772` 现在合同一致的族为 `hypothesis/contract_only`，只有成员带 `predicate.*` 绑定时才 `confirmed/multi_case` | **已修复** |
| §7.5 一个 `passed` 混合多种语义 | 未改。`gates.behavior_gate` 仍是单一 dict | 仍成立 |

这张表本身就是一条结论：**审计报告写于 2026-09-28，代码在其后改过。任何基于该审计的后续设计都必须先做这一步复核，否则会去修已经不存在的问题，并漏掉真正现存的问题。**

### 2.2 仍然成立且更严重的

**(a) 修订提示仍然看不到反例内容。**

`development_adapter.py:73-81` 的 `case_evidence` 每条只有：

```json
{"member": "normal_2", "role": "normal", "baseline_prediction": false,
 "active_prediction": true, "improved": false, "regressed": true, "fired": true}
```

这是**布尔位，不是反例**。模型看到的仍然是"normal_2 坏了"，而不是"Camel/048 里工具观察说 Louis Laguerre，回答写 James Thornhill，你的规则把这段链当成了决定性证据"。proposer 的 `rejected feedback` 里 `reason` 还经过 `redact_case_ids`（`proposer.py:573`），连文本都被抹掉。

这是整个链条上最贵的一处浪费：**LLM 被用来修改规则，却不被告知规则错在哪个具体关系上。**

**(b) 联合单元仍被结构性截断。**

`development_adapter.py:33-35`：

```python
for candidate in result.candidates:
    candidate["intervention_id"] = identifier
    candidate.pop("content_hash", None)
```

`proposer.propose(..., max_proposals=1)` 只返回一条，然后所有返回项被赋成同一个 ID。而 proposer 的规则明确要求 FP 路径必须成对起草（`proposer.py:179-186`：`evidence_check` + `withhold_conviction`，单独一条"can never fire and is rejected before the gate"）。

这不是调参问题，是**表达能力被接口吃掉**：一类合法的修复形状在开发单位里根本无法表示。审计条目 7 完全成立。

**(c) 机制身份已稳定，但义务绑定仍从合同反推。**

身份顺序问题已修（`mechanisms.py:738-740`），不再列为待办。

但 `mechanisms.py:413-420` 仍然：

```python
declared = {_norm(item) for item in (member or {}).get("contracts") or ()}
for obligation in _OBLIGATION_PRECEDENCE[1:]:
    if obligation in declared:
        kind = obligation
        bindings.append("judge.contracts")
```

任务义务来自 **Judge 自己声明的合同清单**。这引入一个循环：检测器的读法被当成任务事实，再交给进化器去"修复"这个读法。`status` 层已经诚实降级为 `hypothesis/contract_only`（`mechanisms.py:762-772`），所以它不再伪装成 confirmed，但**归因仍然建立在这个循环上**：`repair_requirements` 按 `obligation` 查表，若义务读错，修复动作也读错。

另外 `mechanisms.py:308` 的 `_VIOLATION_VERDICTS` 把 `unresolved`、`uncovered`、`inconsistent`、`missing` 与 `violated` 一并算作违规。语义上"无法核验"被当成"违规"，这是系统性误报源，且在 `status` 降级之后依然存在。规则对象要求四态（§4.2），就是为了把这个混淆从数据结构层面消除。

**(d) 搜索空间没有被真正使用。**

`capability_surface.py:121` 报告 `coarse_cartesian_program_space`，但实际候选几乎全是 `text_boundary` + 单个 `recall_trigger`（历史 7 个版本）。`typed_dsl` 已被注册为 `TERMINAL_CONVICTION`（`interventions.py:320`），是唯一既能观察又能定罪的机械通道，却几乎没有被使用。**可用的最强通道处于闲置状态。**

**(e) 预算账本嵌套。** `development.max_provider_calls` 与 `proposer.budget.llm_calls` 不是同一本账，C3 的实际停止原因是后者。这条仍成立。

**(f) 仪器故障被记成候选失败，且本轮新发现。**

`gates.py:130 arm_has_infrastructure_failure` 写得很清楚：

> "A gate rejection caused by a missing label, a non-authoritative terminal, a runtime-invalid row or a failed stage is not evidence that the gate logic rejected the candidate."

但它**没有被候选测量路径调用**。它只服务缺陷注入审计（`engine._audit`）。候选臂走的是 `_run_unit` → `paired_delta` → 门。

后果可以精确推导。一个案例若是 `degraded_judge_only`，`prediction_state` 返回 `None`（`result.py:70-73`）；`paired_delta` 把它计入 `unresolved_added`（`metrics.py:491-495`）；`meets_m` 在 `unresolved_added > 0` 时直接失败（`gates.py:1235-1236`）。**因此一个 provider 故障就把候选判为不合格，而候选的内容与这个故障无关。**

历史运行 `Magentic_One/049` 的 scanner 截断与 upgrade `no_quorum` 正是这条路径。审计把根因 1 写成"测量链不稳定"，但机制上的准确描述是：**测量故障没有独立的通路，只能伪装成算法失败。** 这是本轮复核里最需要优先修的一条，因为它把仪器问题系统性地翻译成了对算法的否证。

### 2.3 审计没说到的那一层：缺一层显式规则表示

上面五条都是现象。它们的共同原因是层 3 缺失（§1.1）：**进化器直接编辑干预字典，没有一个"这条规则在说什么"的对象**。

后果是具体的：

1. 没有 `ω`（义务实例绑定），所以"任务要求确认修改后状态"和"任务要求用指定工具"在数据结构上是同一个东西——都是 `member.contracts` 里的一个字符串。
2. 没有 `c` 的四态（`not_applicable / satisfied / violated / unknown`），所以"规则不适用"和"规则判它没问题"无法区分。`mechanisms.py:308` 的 `_VIOLATION_VERDICTS` 把 `unresolved`/`uncovered`/`inconsistent`/`missing` 都算违规——语义上"无法核验"被当成了"违规"，这是系统性误报源。同类问题在测量侧更严重：`arm_has_infrastructure_failure` 已经把"不可测"从"被拒"里分出来了，但这条分界线没有走出审计模块（§2.2(f)）。
3. 没有 `m`（材料性链接）与 `π`（来源选择）的分离，所以检测与定位共用一条命题。而 `adjudication.py` 从结构上就把两者分开了（`LOCALIZATION_AUTHORITY_*` 与 `AUTHORITY_*` 独立）。**检测器的判定语义比进化器的表示更细。**

第 3 点是本文最重要的判断：**现有进化的表达粒度低于检测器的语义粒度，因此它的每一次修改都在丢信息。** 加多少轮、放宽多少门都改不了这一点。

### 2.4 一条更根本的怀疑

审计已指出：`OpenDeepSearch/086` 与 `OpenManus/073` 的正确事实不在 history 里，`judge.py:225` 明确要求"Do not use any information that is not in the question or the trajectory"。对这类案例：

- 轨迹内自洽检测器**在信息论上不可能**判对；
- 任何在轨迹内改提示、加规则的候选最多只能靠猜；
- 而"猜对"在配对评估里会被记成 TP。

这意味着历史三轮的一部分失败不是算法失败，是**任务可识别性失败**。RSI 框架必须有显式的可识别性分类，否则会把"没证据"的案例当负例喂给优化器。

---

## 3. 文献与 novelty 边界

本轮（2026-10-17）检索了四组工作。结论先给：**"反例驱动的 agent 规则演化"和"带预测-证伪契约的 harness 自进化"都已被占满，不能作为本文的方法贡献。** 剩下的空间比预想的窄，但确实有一个这个仓库独占的位置。

### 3.1 已核实的近邻工作

| 工作 | 机制 | 对本项目的约束 |
| --- | --- | --- |
| **AgentSpec**（arXiv 2503.18666，ICSE 2026） | DSL，规则 = trigger + predicate + enforcement；并用 o1 自动生成规则，报告 95.56% precision / 70.96% recall | 可执行约束形式**不能认领**。"模型生成规则 + 报 precision/recall"也已存在 |
| **AutoSpec**（arXiv 2606.24245） | 四阶段闭环：反例挖掘（FP/FN）→ ILP 引导谓词学习 → 候选生成与选择（AddException / AddDisjunct 等算子）→ 验证接受；即 ILP 引导的 CEGIS | **最直接的竞争者**。"反例驱动规则精化"作为一个 idea 已被占满。其消融还显示去掉 ILP 引导后 F1 显著下降，说明搜索引导本身有价值 |
| **AHE**（arXiv 2604.25850） | 三个可观测性支柱；每条 harness 编辑附自声明预测，下一轮用任务级结果证伪；10 轮把 Terminal-Bench 2 从 69.7% 提到 77.0% | **"预测-证伪契约"不能认领**。其消融还给出一个关键结论：增益来自 tools / middleware / memory，**不来自 system prompt** |
| **AHE 类 blog 分析** | 明确把"无预测的 prompt 循环"列为反模式：overfit benchmark、增益不迁移、无法归因 | 说明 `text_boundary` 是弱通道，历史候选的形态本身就不利 |
| **SEVA**（ICML 2026 FAGEN workshop） | 结构化事实核验 + 过程奖励；Verify-Reflect-Probe-Refine 四轮自进化。**关键负结果**：每轮产出的是 benchmark specialist 而非 generalist（同模型 HaluEval +15pp，TruthfulQA −10~−14pp，4× 数据下仍持续） | 这是本项目最需要防的失效模式。任何"检测器自进化"的主张都必须含跨族迁移实验 |
| **MASC**（ACL 2026 Findings） | 历史条件的下一步重建 + 原型先验，做无监督逐步错误检测；Who&When 上 AUC-ROC +7.8% | 逐步错误检测本身不新。它是"检测"不是"改进检测器" |
| **Trajel**（arXiv 2605.24219） | 五类轨迹级幻觉分类法 + 专家标注；发现高二分类准确率仍漏掉最细的类型 | 与本项目的任务定义直接重叠；也支持"二分类指标不足以刻画诊断质量" |
| **Selective prediction / conformal risk control** 系（El-Yaniv & Wiener；Geifman & El-Yaniv；SCoRE 2603.24704；guardrail 认证 2609.22048） | 固定风险下的覆盖最大化、分组联合认证、有限样本保证 | **"把可靠性阈值做成统计保证"是成熟方法**，不能当新方法。可当工程层引用 |
| **Coverage 相关理论**（如 Self-Evolving Agent Harness, arXiv 2607.00871 的 anytime-valid certificates；Harness RSI 的 retention bound） | 对自修改施加 anytime-valid 统计门并提供可审计证书 | "用统计门控制自进化"已被系统化。本项目的门控不是新意所在 |

### 3.2 被占满的两个方向

1. **反例驱动的规则精化**（AutoSpec、DICE、CEGIS 传统）。任何以"从 FP/FN 反例学规则"为核心卖点的表述，都会被认为是 AutoSpec 的变体。
2. **带预测/证伪契约的自进化**（AHE、anytime-valid certificates、Harness RSI）。"每条修改附预测再证伪"已经是 2026 年的标准做法。

本仓库的历史运行在两个方向上都不占优：它的"预测"是事后归纳的 reason 字符串，它的"反例"是布尔位。

### 3.3 仍然空着的三件事

检索没找到直接对应。这三件恰好都能由这个仓库的既有结构支撑，而不是硬凑。

**(A) 反例的来源是检测器自身的阶段分歧，而非外部金标。**

AutoSpec 的反例来自带标签语料上的错分。这里的检测器每案产出 judge 票型、recall 候选、upgrade 逐行、tracer 定位、defense 裁决、定位冲突标记（§1.3）。这些内部量之间的**不一致**本身构成一个反例源：例如 judge 判阴性而 upgrade 有候选、tracer 与 defense 的步号方向相反、定位权威降级。这类反例是**无标签可得**的，且直接指向"哪一层该改"。

这不是"用置信度做主动学习"——分歧的双方是不同阶段的独立读法，不是同一模型的多次采样。

**(B) 两类反例同时约束检测与定位，且共用一条规则。**

研究问题可陈述为：给定义务实例 ω，合规反例（相似义务下的合法处置）约束"何时可以判错"，定位反例（同一偏差的已核验首次来源 vs 下游副本）约束"位置选在哪"。两类反例约束的是**同一条关系规则**的不同条件位（c/m 与 π）。

AutoSpec 只有一类反例（安全性上的 FP/FN），其规则也不表达来源选择。MASC / Trajel 做定位，不做规则学习。

**(C) 被改进对象包含"归因算子"本身。**

现有全部工作改进的是被评估系统（harness、verifier 的策略、规则集）。这里可以再上一层：把"失败签名 → 修复类别"的映射本身作为学习对象，并用 **admissions-per-proposal** 与 **counterexamples-per-admission** 度量它是否变好。

AHE 的 prediction 是**每次编辑**的；把"归因表"当作可学习参数、并按"同等预算下产出更多可测候选"来评分，检索未找到对应。这构成一个真正的二阶主张，且可证伪。

### 3.4 诚实的定位表述

建议方法贡献写作两条，且只在证据支持时保留第二条：

1. **一项以义务实例为单位、由检测器内部阶段分歧生成反例的诊断规则表示**，同一条规则的条件位分别受合规反例与定位反例约束，从而在检测与定位之间共享可检查的语义。不是新搜索算法，是新的监督组织方式。
2. **归因算子的受控自进化**：在冻结评估契约下学习"失败签名 → 修复类别"的映射，以同等预算下可测候选产出率度量，并与冻结归因基线对照。

如果 (2) 只有微弱效果，就按 (1) 写成可解释检测定位方法，把 (2) 降为工程经验。

**不能写**的：把"动态约束 + 来源追踪 + 自进化"三件原语的组合当创新；把门控设计当创新；把"检测器能改进自己"当已验证结论。

### 3.5 第二轮的 novelty 重估（在 §8 的实测之后）

§3.3 的三条是在读代码后提出的，当时没有实测。§8 做完全量测量后，必须修正其中的 (B)，并给 (C) 增加一个前置条件。

**(B) 需要降级为"待定"。** 它依赖 `CE_l` 有供给，而 §8.5 实测 `verified` 层在当前语料上生产者为 0（唯一生产者 `arithmetic_check` 要求 calculator 工具，全量出现 0 次）。降级不是放弃，是把它从"已成立的结构性差异"改成"**需要先造出可核验首次引入的第二个生产者**才能成立的结构性差异"。这本身是一个可发表的能力缺口陈述，但不能作为已有优势来声称。

**(C) 需要加一条前置。** §8.8 实测：443 个案例只有 35 种不同的风险信号模式，98.9% 的案例存在并列排序键，基于现有信号的学习型排序在跨族评估下**低于**静态基线（33.9% vs 38.1%）。这意味着"归因算子可学习"这条主张的前提——**存在足够丰富的签名空间**——尚未被证实，反而被初步否定。诚实写法是把 (C) 表述为：**先度量签名空间的多样性，再判断归因是否可学**；并把这个否定结果作为第一轮证据报告。

**新增一条候选贡献 (D)，来自本轮测量。**

排序层（`top_k=3` + 8 档静态优先级）切断 34.8% 的金标位置，而容量约束 `max_projection_events=16` 从未生效。检测器的 LLM 听证阶段实际是在**一个由静态整数表筛过的 3 个候选里做选择**，且该表把 49% 的事件钉在同一档。§8.8 进一步证明：在这个特征空间里，任何基于现有信号的重新排序都无法跨族泛化。

这给出一个与 AutoSpec / AHE / AgentScope 都不同的研究问题，且是关于**验证器本身的结构**而非规则内容：

> 一个多阶段验证系统的诊断能力，有多少被它自己的**候选供给函数**所限制？

可测形式是：固定判定层、只改读取宽度与排序，测量端到端定位精度的变化。如果变化显著，说明"更强的判定规则"在这一部分案例上从未有机会——这是对当前一片 RSI / self-improving verifier 工作（都在改判定侧）的一个直接质疑，且有本仓库的现成实验条件。

这条的 novelty 风险是它可能被视为"ablation study"而非方法贡献。对策是把它做成**可报告的诊断量**（候选召回率、并列熵、特征多样性指数），而不是一次性消融——即给这类系统一个"先量供给、再谈改进"的评估协议。

### 3.6 更新后的定位建议

在 §3.4 的两条之外，把 (D) 作为第一条，因为它有本轮实测支撑而 §3.4 的两条没有：

1. **(D) 候选供给的诊断与评估协议**——已有实测数据，成本最低，风险是"像消融"。
2. **(A) 阶段分歧作为反例源**——结构性差异清楚，尚无实验。
3. **(C) 归因算子的受控自进化**——二阶主张，但 §8.8 已给出反向前提。

**(B) 暂时不写**，等 `verified` 层有了第二个生产者。

---

## 4. 框架设计

### 4.1 设计被丢弃的第一版（迭代记录）

第一版设计把问题当作"更好的搜索"。做法是：扩大候选空间（放开 `max_proposals`、增加轮数、允许 `typed_dsl` 与 `text_boundary` 混合搜索），用贝叶斯优化在候选上采样，以联合指标为适应度。

自我反驳后放弃，三条理由：

1. **它复现了已经失败的结构。** 历史三轮的失败不是搜索不够，是搜索的目标函数（`behavior_gate` 的极性判定）与证据（布尔位反馈）都错了。在错误目标上做更好的优化，只会更快收敛到错误解。
2. **它与 AHE / AutoSpec 正面竞争且无优势。** 它们有权重的模型、有更强的 harness 动作空间、有更大的语料。同样的搜索叙事不可能赢。
3. **它不可解释。** 一个由 BO 选出的候选说不出"为什么是这个条件"，与本文要求的科研意义冲突。

第二版改为：**先把学习单位从"干预"下沉到"义务实例上的规则"，再让反例从检测器内部生成，最后才谈搜索。** 搜索不是方法，是执行细节。

### 4.2 规则对象

```
r = (ω, g, c, m, π, θ)

ω  obligation     任务义务实例：绑定 question 原文片段、对象、所需结果、适用时点
g  guard          适用条件：typed DSL 条件合取（FIELD_TYPES × ALL_OPERATORS）
c  constraint     局部约束四态：not_applicable | satisfied | violated | unknown
m  materiality    该偏差与最终交付的关联条件：同对象/同义务/消费链/修复状态
π  provenance     来源选择规则：在观察到的依赖图上选首次引入事件，非固定步数前移
θ  reliability    该规则的条件精度估计（按义务类别与 packet 特征分层）
```

与检测器语义的对应关系是这套设计成立的关键：

| 规则位 | 检测器中的落点 | 权限 |
| --- | --- | --- |
| `ω`、`g`、`c` | `evidence_check` @ `judge.post` / `upgrade.pre` / `defense.pre`，runner=`typed_dsl` | 可获定罪权（经校准） |
| `m` | 同上，或 `decision_guard` 消费其事实 | 同上 |
| `π` | `tracer.system` 的上下文，或新增 `tracer.post`（需扩展，见 §5.4） | **不得**获定罪权 |
| `θ` | 不进入运行时，只决定权威等级 | 治理层 |

`π` 不得定罪这一点是从 `adjudication.py` 读出来的：定位权威与判定权威独立，`LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT` 只降定位。让一条来源规则获得判定权会破坏这个正交性。

### 4.3 三个嵌套循环

```
┌─ L_macro（策略层，跨轮，天级）────────────────────────────────┐
│  学习：预算分配、测-改决策、接受策略                           │
│  度量：admissions/proposal、counterexamples/admission         │
│                                                               │
│  ┌─ L_meso（归因层，每轮，小时级）───────────────────────┐   │
│  │  学习：σ: 失败签名 → 修复类别                          │   │
│  │  产物：机制假设 + 可达修复动作 + 需补能力               │   │
│  │                                                        │   │
│  │  ┌─ L_micro（规则层，每次迭代，分钟级，无 LLM）────┐  │   │
│  │  │  CEGIS：给定 σ，在 typed DSL 上归纳 r          │  │   │
│  │  │  两类反例 → 有界编辑 → 必要性删除 → 保留见证    │  │   │
│  │  └────────────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

三层分开的理由：它们的**成本量级差三个数量级**，混在一个循环里就是历史失败的直接原因（一次 L1 回放要跑整个五阶段流水线 ~800 秒）。L_micro 是纯函数执行，可以跑上千次迭代；L_meso 需要少量真实测量；L_macro 只在轮结束时更新。

#### L_micro：规则层 CEGIS

```
输入：σ（机制假设）、目标案例集合 T、合规反例集合 N、定位反例集合 L
输出：规则 r、见证 w(r)、不可删减性证书

1  初始化  r₀ = 从 σ 的共享条件生成的最弱规则
2  repeat
3      CE_c ← { n ∈ N : r(n) 判为 violated }          # 合规反例：过宽
4      CE_l ← { (l_earlier, l_later) ∈ L : r 选错来源 } # 定位反例：来源错
5      if CE_c ≠ ∅:
6          对每个 n，提取导致误判的条件位，试删除或加排除条件
7          保留能消除该反例的最小编辑；记录 witness = "删掉它会混淆 n"
8      elif CE_l ≠ ∅:
9          修改 π：增加对象/时序/修复条件
10         记录 witness = "删掉它会选到副本 l_later"
11     elif ∃ t ∈ T : r(t) ≠ violated:
12         从 t 的失败签名提取缺失条件，试增加条件（受 MAX_CONDITIONS=4 限制）
13     else:
14         break
15 until 预算耗尽
16 对每个保留条件做必要性测试：单独删除后重跑，若判别/定位结果不变则删除该条件
```

三条设计约束：

- **每步只做一种编辑**（删除 / 加合取 / 加排除 / 改 π）。这样每一步都能归因到具体反例。
- **必要性测试是解释性的来源。** 保留下来的每个条件都必须能回答"删掉它会混淆哪一种情况"。这是 §3.4 第一条主张的可检查形式。
- **不使用案例 ID 或 gold。** `development.py:222` 的 `no_case_id` 检查已经存在，直接复用；新增条件只能引用 `FIELD_TYPES` 中的 packet 视图。

#### L_meso：归因层

现有 `mechanisms.py:340 operation_for(stage, failure_mode)` 是**静态查表**，`_OPERATION_BY_FAILURE` 写死。这是"归因不可进化"的根源：三轮里同样的 stage/mode 永远得到同样的修复动作。

改为可学习的估计：

```
σ 的键 = (stage, failure_mode, obligation_kind, evidence_state, localization_state)
σ 的值 = 修复类别上的分布，参数用 Beta/Dirichlet 后验
修复类别 = {narrow_guard, add_conjunct, add_exception, move_source_rule,
            require_evidence, expand_capability, abstain_measurement}
初始化 = 现有静态表（保持向后兼容）
更新   = L_micro 的结果（哪类编辑真的消除了反例）
选择   = Thompson sampling，替代当前"前两个机制优先"的 FIFO
```

关键的新增类别是 **`abstain_measurement`**：当检测到 `evidence_state=missing`（§2.4 的可识别性问题）或 `stage_status` 不健康时，归因输出"测量待修"而不是"算法待改"。历史三轮缺的正是这一项——`Magentic_One/049` 的 `truncated_output` 与 `no_quorum` 被当作候选的失败，浪费了 3/4 的正式预算。

#### L_macro：策略层

只学三件事，每轮结束更新一次：

1. **预算分配**：给 `revise`（改规则）还是 `remeasure`（重测同一候选）还是 `collect`（补证据）。历史运行的不对称是：不可测候选被丢弃而不是重测。
2. **接受策略**：`mechanism_supported` 与 `deployment_utility` 分开记录（审计 §7.5 的建议），二者不一致时进入 `secondary_hypothesis` 队列而不是直接拒绝。
3. **探索配额**：保证每轮至少有一个"未探索过的义务类别"进入候选，防止 FIFO 饿死。

### 4.4 权威等级作为可学习属性

当前 runner 的 `terminal_authority` 是**按类型固定**的：`deterministic` / `typed_dsl` → `TERMINAL_CONVICTION`，`typed_semantic` / `sandboxed_component` → `TERMINAL_EVIDENCE_ONLY`（`interventions.py:285-390`）。构造器还硬性拒绝 learned runner 拥有定罪权（`interventions.py:227`）。

这个设计解决了"模型权重换了判决就变"，但它把**同一类型内部的可靠性差异**抹平了：一条在三个案例上都不出错的 `typed_dsl` 规则与一条在 400 个案例上零误报的规则，拿到同样的定罪权。

改为三层：

| 等级 | 获得条件 | 判定权 |
| --- | --- | --- |
| `evidence_only` | 默认 | 无 |
| `conviction_calibrated` | 在分层校准集上，规则触发样本的 precision 下界 > 1−α（Clopper–Pearson），且覆盖 ≥ n_min | 有 |
| `revoked` | 上线后监测到误报，或触发分布漂移 | 撤销 |

`θ` 就是这个 precision 估计，按 `(obligation_kind, packet_feature_set)` 分层。这里必须诚实说明：**统计机器是现成的**（§3.1 的 conformal / selective prediction 一整套），本项目的贡献是把它**接到规则层**并报告风险-覆盖前沿，不是发明新的界。

现存的 `conviction_calibration.py` 已经在做类似的事（源码指纹 + 语料核验 + 零误报门），本次是把它从"零误报的硬门"升级为"分层估计的软门 + 前沿报告"，并让前沿本身成为可报告的科研产物。

### 4.5 反例的构造

三类反例，来源与用途各不相同：

**CE_c 合规反例（约束 c 与 m 的过宽）**

- 来源：`gold=false` 且检测器 `prediction=false` 且 `stage_status` 全健康的案例（这是"检测器自己认为正常、金标也认为正常"的双重确认，比单靠 gold 强）。
- 匹配条件：**同义务类别 + 同风险前提**，不是文本相似度。复用并改造 `causal_failures.matching_action_normal_neighbors`（:40）——它已经在做"同前缀风险 + 合规下一事件"的匹配，但只覆盖工具失败这一类。
- 用途：迫使规则绑定对象、时序、修复证据。对应审计 §3.4 的 Camel/048 案例。

**CE_l 定位反例（约束 π）**

- 来源：同一偏差的**已核验首次来源** vs **下游副本**。现有 `causal_failures.py:184` 已经在计算 `classification ∈ {early, late, aligned}`，条件很严（要求 `materiality=verified` 且 `first_introduction=verified_declared_result` 且 `counterfactual=verified_symbolic`）。
- 用途：阻止规则把"最早出现的失败观察"选成首错。这是审计 §3.4 里"C3 把 ask_human 的文本当成决定性观察"的直接对策。

**CE_m 测量反例（约束 L_macro，不进规则学习）**

- 来源：`stage_status` 非 ok、`decision_authority != authoritative`、`noise_floor` 未测。
- 用途：把这类案例从 L_micro 的输入里剔除，并把"测量待修"作为一等状态报告。**这是唯一直接针对历史失败根因 1 的设计。**

### 4.6 与已有基础设施的复用关系

这套设计不是另起一套系统。逐项对应：

| 新框架组件 | 复用的现有实现 | 需要新增的 |
| --- | --- | --- |
| 规则对象的 `g`、`c` | `predicate_dsl.py` 全套（`validate_predicate_program`、`run_predicate_program`、`MAX_*`） | 无 |
| 规则绑定与执行 | `interventions.py` `RUNNER_REGISTRY`、`_validate_body` | `θ` 字段 |
| 反例集合 CE_c | `causal_failures.matching_action_normal_neighbors`、`worksheet` 的邻居逻辑 | 一般化到非工具义务 |
| 反例集合 CE_l | `causal_failures.build_causal_failure_record` 的 `classification` | 从"记录"改为"用于约束 π" |
| 反例集合 CE_m | `engine._unmeasurable_cases`、`metrics.failed_stage_state` | 贯通到 L_macro |
| 局部回放 | `evolution/sandbox.py`、`backends.py` 的 `LiveDetectorBackend` | 一个只跑规则不跑五阶段的快路径 |
| 配对评估 | `metrics.paired_delta`、`paired_localization_delta` | `target_outcome` 的双目标读法 |
| 数据划分 | `splits.py` 的 `EvolutionSplit` + `FinalHoldout`（读一次即封存，:174） | 义务族隔离划分 |
| 经验存储 | `memory.py`（三类）、`archive.py`（候选 + 可复用组件） | 归因表本身 |
| 冻结身份 | `detector/identity.py`、`build_execution_identity`、`replay_inputs.v1` | 无 |

`FinalHoldout` 的读一次机制（`splits.py:197` 检查 marker 存在即拒绝）已经是正确的研究卫生实践，直接继承。

---

## 5. 代码接入点

按改动代价从小到大。每项都标出最小可验收标准。

### 5.1 已可做，无需新接口（P0）

| 改动 | 位置 | 验收 |
| --- | --- | --- |
| 修复 `max_proposals=1` 与 ID 塌缩 | `development_adapter.py:33-35,38,84` | 一个 `evidence_check` + `decision_guard` 的联合单元能从起草走到冻结，两条 entry 各持独立 ID |
| 反例带真实文本窗口 | `development_adapter.py:73-81` 的 `case_evidence` 增加事件原文摘录（按 `evidence_view` 的有界通道），保留别名 | 提示中出现反例的事件文本，且不含 case ID |
| 测量失败不进规则学习 | `engine._run_cycle` 的 family 准备处，用 `_unmeasurable_cases` 预过滤 | `no_quorum` / `truncated_output` 案例不出现在 `member_summaries` |
| 测量故障不冒充算法失败 | `engine._run_unit` 与 `metrics.paired_delta`：把 `gates.py:130 arm_has_infrastructure_failure` 的判据上移到候选行 | 非权威行输出 `measurement_pending`，不计入 `unresolved_added`（§2.2(f)） |
| 义务绑定脱离 judge 合同 | `mechanisms.py:413-420` | `bindings` 不再单独出现 `judge.contracts`；改为从 question 原文绑定的独立字段 |

### 5.2 需要小改接口（P1）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 规则级快路径回放 | 新增 `evolution/rule_replay.py` | 只执行 `evidence_check` 与 `decision_guard`，不跑 judge/recall/upgrade。这是 L_micro 能跑上千次的前提 |
| 归因表 | `mechanisms.operation_for` 改为读可注入的估计器 | 保留静态表作默认，行为完全向后兼容 |
| `θ` 与权威分层 | `interventions.RunnerSpec` 增加 `reliability` 字段；`conviction_verdicts_for` 读取 | learned runner 仍不得定罪（保留 `:227` 的拒绝） |
| 双目标门 | `gates.behavior_gate` 增加 `mechanism_supported` / `deployment_utility` 两个输出 | 不改变现有 `passed` 语义，新增字段供 L_macro 读 |

### 5.3 需要扩展检测器（P2，谨慎）

`π` 目前只能通过 `tracer.system` 的文本提示间接影响定位（`interventions.py:1887 prompt_block`）。要让来源选择规则**可执行**，需要一个能在 tracer 输出后检查位置的 hook。

不建议直接加 `tracer.post` guard。更保守的做法是复用现有 `evidence_check` @ `defense.pre`：把"候选来源是否满足对象的同一性"作为一条语义证据写进去，由 defense 消费。这样不引入新权限，但表达力受限（只能间接影响）。

是否值得扩展执行图是独立决策，需要单独授权。`capability_surface.py:198-205` 已经把 `typed execution-graph artifacts` 列为未满足前提。

### 5.4 明确不做

- 不改 `adjudication.py` 的终态语义。
- 不让任何学习组件获得 `decision_authority`。
- 不改 `FinalHoldout` 的读一次规则。
- 不在评估侧读取 gold 进入运行时（`dataset.project_record` 的边界不动）。

---

## 6. 科研反思

### 6.1 对本文设计的四条反驳

前四条针对 §4 的设计；针对 §8–§9 的第五条见 §9.5。

**反驳 1：这不过是 AutoSpec 换了个对象。**

部分成立。搜索形式（反例 → 有界编辑 → 验证）确实相同。差异必须落在三处可测的地方，否则应当承认是应用工作：(i) 反例来源是阶段分歧而非错分标签；(ii) 两类反例约束不同条件位；(iii) 归因算子本身可学习。如果实验显示这三处都没有带来差异，贡献收缩为"在幻觉检测任务上应用了 CEGIS"，这仍然可以发表，但档次不同。

**反驳 2：SEVA 已经证明这类自进化会退化成 benchmark specialist。**

这是最强的反证。SEVA 在同一模型上报告 HaluEval +15pp / TruthfulQA −10~−14pp，并在 4× 数据下持续存在。

本文设计有一个结构上的应对：规则绑定义务类别 ω 而非案例表面特征，且 `no_case_id` 是硬检查。但**这不构成证据**。必须在实验设计里写成显式检验：按义务族隔离开发与评估，报告跨族迁移率。如果迁移率为零，就应当报告这个负结果——它与 SEVA 一致，是关于"诊断规则能否迁移"的有价值发现。

**反驳 3：样本量撑不起结论。**

693 例，本轮实测其中 `hallucination_step` 可映射为 `source_step` 的金标阳性为 **443** 例（其余 250 例为金标阴性、`unannotated`），全部可做定位评估。历史定位命中数字必须分口径引用，不能混用：`docs/experiment-log.md:9` 的 `114/419 = 27.21%` 是 2026-09-27 的 **653 例 G0 运行**结果，分母是那一批可评估阳性，不是本次 693 例语料。两者相差一代语料与一次检测器版本，直接比较会得到错误的基线。

在这个量级上，任何"方法论上一个点"的主张都无法被统计区分：443 例上单点差异的标准误约 2.3pp，端到端精度差 1–2pp 在统计上与零无异。这正是历史运行反复在同一批 100 例上看到"净增益 0"却无法判定原因的一部分原因。

对策：把主张拆成两级。一级是**机制级**指标（反例是否被消除、条件是否可删减、归因表是否收敛、**金标是否进入读取窗口**），这些在几十到几百例上就能稳定测；二级是**端到端**指标，只在封存集上报告一次，不做中途选择性读取。这比反复在同一批 100 例上优化（当前实践）在方法上更干净。

**反驳 4：可解释性主张可能是空的。**

"每个保留条件都有反例见证"是可检查的，但可检查不等于忠实。规则可能是在事后拼凑一个能通过删除测试的条件集，而真实判别依据是别的。

对策：加忠实性检验。仓库已有正确的落点——`evolution/metamorphic.py` 的 §15.2 变换集（`METAMORPHIC_CLASSES`，见 `interventions.py:176`：`case_id_rename`、`source_rename`、`irrelevant_tool_call`、`neighbor_surface_token`、`semantic_equivalence`、`localization_event_move`），当前用于 runner 准入。把它扩展到**规则级**：对一条规则声明为必要的条件，做针对性的语义等价扰动与无关片段扰动，看判别是否按声明保持不变。

这才是把"可解释"从"有见证"推进到"忠实"的路径。注意它证明的是"判别依赖这条证据"，不证明"该证据在真实环境中有因果责任"——这个区别必须保留，否则会把遮蔽检验当成因果证明。

### 6.2 可证伪主张（按检验顺序）

| # | 主张 | 最有区分力的检验 | 否定结果 |
| --- | --- | --- | --- |
| H1 | 两类反例约束同一规则，比分别优化检测/定位更有效 | 同等模型、同等候选数、同等预算：共享条件 vs 两套独立提示 | 独立提示等效 → 收缩为组合改进 |
| H2 | 两类反例互补 | 无反例 / 仅 CE_c / 仅 CE_l / 两者，另设等量随机邻居 | 随机邻居同效，或组合只是用召回换精度 |
| H3 | 归因可学习 | L_meso vs 冻结静态表，度量 admissions/proposal 与 counterexamples/admission | 无差异 → 二阶主张不成立，回到一阶 |
| H4 | 权威分层有效 | 报告风险-覆盖前沿；检验前沿单调性与固定风险下的覆盖增益 | 前沿不单调 → `θ` 估计不可靠，退回固定规则 |
| H5 | 规则可迁移 | 义务族隔离的开发/评估；报告跨族有效触发率与误报 | 只在原族触发 → 报告为负结果，与 SEVA 对照 |
| H6 | 读取宽度而非规则表达力是当前定位瓶颈 | `top_k` 扫描 {1,2,3,4,6,8}，端到端定位精度的配对变化（§8.9 读数 0） | 精度不随 k 上升 → 宽度不是瓶颈，§8 的重构作废，回到 §4 |

H3 是本文独有的主张，也是风险最高的一条。如果只能做一件事，做 H3 的对照——它最便宜（不需要端到端回放），且无论正负都有信息。

H6 是第二轮迭代新增的，且**必须先于 H1–H5 检验**：本轮已测出金标覆盖率随 k 单调上升（65.2%→88.9%，k=3→6），但端到端是否受益完全未知。如果 H6 为假（读完也没用），那么 §8.2 的重构不成立，本文应回到 §4 的规则层叙事；如果 H6 为真，§4 的规则层在 34.8% 的案例上从未有过机会，历史"算法无效"的结论需要重新解释。

另外两条来自 §8.8 的否定结果已经可以直接写进当前记录，不需要新实验：

- **H2 的预期下调**：两类反例互补的前提是 `CE_l` 有供给，而 §8.5 实测 `verified` 层为零。在 `CE_l` 改用金标代理之前，H2 的"仅 CE_c / 仅 CE_l"对照中后者是空臂。
- **H3 的风险上调**：如果归因表学习的是"失败签名 → 修复类别"，而排序层实测的 35 种信号模式已经饱和，那么归因表可用的区分特征同样贫乏。H3 需要先证明存在足够的签名多样性，否则它会在一个 35 类空间里学出一张查找表。

### 6.3 一条必须先回答的问题

在设计任何新机制之前，有一个实验应该先做，因为它决定后续方向：

**当前瓶颈到底是搜索还是测量？**

历史三轮的证据是：4 个正式候选中 3 个不可评估。如果把这个比例降到接近 0（只做测量修复，不改算法），准入数会不会从 0 变成非 0？

这个实验很便宜：冻结现有候选内容，用修好的测量（匹配后缀的空干预对照 + 固定重测）重跑正式门。如果准入仍为 0，说明瓶颈在算法/表达力，RSI 值得投入；如果准入变成 1-2，说明当前最缺的是仪器，先前的"算法无效"结论是错的。

**在这个实验有结果之前，不应该投入 L_micro 的完整实现。** 这是本文给后续工作的第一条建议，也是最容易被跳过的一条。

### 6.4 关于"RSI"这个词

严格意义的递归自我改进是系统改写自己的改进器。当前框架能做的：

- 能改：规则集、归因表的参数、预算调度。
- 不能改：执行图、评估器、数据划分、gold。
- 明确不可：权重更新（`capability_surface.py:159` `model_weight_update: False`）。

因此准确表述是**受控的自指改进（controlled self-referential improvement）**，而不是开放式 RSI。论文写作时用后者会被直接质疑；用前者并经得起检查。`capability_surface.py:56` 的 `open_ended_self_evolution: False` 已经是诚实的自我标注，应当保持。

---

## 7. 实施与验收

### 7.1 阶段划分

| 阶段 | 内容 | 前置 | 验收（可检查，非效果） |
| --- | --- | --- | --- |
| P0 | 测量故障独立通路（§2.2(f)）：把 `arm_has_infrastructure_failure` 的判据上移到候选测量 | 无 | `degraded_judge_only` 案例产生 `measurement_pending` 而不计入 `unresolved_added`；历史 4 候选的正式结果可按新口径重读 |
| P0 | 测量仪器修复 + 固定候选重测（§6.3 的实验） | 无 | 4 个历史候选中有 ≥1 个得到可测终态；不可测原因逐条归因 |
| P0 | 联合单元与反例文本修复（§5.1） | 无 | 纯函数探针：联合单元从起草到冻结不丢失；提示含反例原文 |
| P1 | 规则对象与 L_micro（§4.2、4.3） | P0 | 在已知机械案例（如字面算术）上，规则从最弱起点收敛到可解释条件集；每条件有反例见证 |
| P1 | CE_m 贯通 | P0 | 不健康案例不出现在规则学习的输入中 |
| P2 | L_meso 归因表（§4.3） | P1 | H3 对照完成并报告 |
| P2 | `θ` 与风险-覆盖前沿（§4.4） | P1 | 前沿单调；固定风险下覆盖增益可测 |
| P3 | H1/H2/H5 对照 | P2 | 按 §6.2 报告，含负结果 |

### 7.2 最小的"能证明框架成立"的实验

不是跑满三轮。是这一个：

在义务族隔离的划分上，固定模型、固定候选数、固定预算，比较三臂：

- **A 臂**：现有 pipeline（`text_boundary` + 极性门）。
- **B 臂**：L_micro 规则归纳 + 两类反例，但归因表冻结。
- **C 臂**：B + L_meso。

观察：可测候选率、准入的规则数、每条准入规则的反例见证数、封闭义务族上的联合指标、跨族迁移率、成本。

A→B 测 H1/H2，B→C 测 H3。如果 B ≈ A 且 C ≈ B，整个框架不成立，应当如实报告。

### 7.3 记录要求

沿用 `docs/experiment-log.md` §6 的规则，并新增三项：

1. 每条准入规则必须附：反例见证、必要性证书、`θ` 估计及分层依据、源码与配置身份。
2. 每次不可测事件必须附：失败阶段、失败类型、是否重测、重测结果。
3. 每条被拒绝的候选必须保留其真实执行身份（`archive.py` 已有，需确保 L_micro 的中间迭代也进入归档，否则"为什么这个条件没被保留"无法复查）。

---

## 8. 底座结构的再发现：排序层、验证空洞与四个先测读数

本节是第二轮迭代。§3–§7 的设计是从"检测器缺一层规则表示"推出来的；本节换了一个起点——**不先假设该学什么，而是先测底座的哪些部件真的在学习、哪些从未被使用**。测完之后的结论与原设计有实质差异，其中一条（§8.5）直接修正 §4.5。

所有数字都是本轮在 `datasets/raw/agenthallu/AgentHallu` 全量 693 例上实测的，用仓库自己的函数（`build_packet`、`build_step_risk_profile`、`select_causal_candidates`、`gold_step_mapping`、`predict_action_boundary`），未改动任何代码。

### 8.1 四个实测事实

**事实 1：排序只有一个 8 档整数，且近半数事件挤在同一档。**

全量 5,269 个事件的风险信号分布：

| 信号 | 计数 | 优先级 |
| --- | --- | --- |
| `unverified_observation` | 3,423 | 2 |
| `unverified_action` | 3,387 | 2 |
| `unsupported_claim` | 2,583 | 3 |
| `observed_tool_failure` | 215 | 1 |
| `external_guidance` | 112 | 1 |
| `verified_deviation` | **0** | 0 |

`unsupported_claim` 一项占全部事件的 49.0%，且全部落在同一个优先级 3。`select_causal_candidates` 的排序键是 `(priority, -verified_count, position)`（`step_uncertainty.py:175-179`）——一个三元素元组，其中第二项在本语料上恒为 0。

**事实 2：排序键的第二项是死代码。**

`verified_deviation` 的唯一生产者是 `arithmetic_check`（`causal_contracts.py:147`），它要求 `tool_name == "calculator"`。全量语料里的 calculator 调用数为 **0**。因此 `verified` 这个 `verification_status` 取值在 `by_event` 行中出现 **0 次**，排序键退化为 `(priority, position)`——即"先按 8 档整数，同档按谁先出现"。

**事实 3：top_k=3 的覆盖率有 21.4 个点的现成空间。**

在 443 个金标阳性案例（`hallucination_step` 全部映射为 `source_step`）上，按当前排序取前 k 个事件，金标位置的命中率：

| k | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 覆盖率 | 38.1% | 49.4% | 65.2% | 77.0% | 82.4% | 88.9% | 94.8% | 97.1% |

对照组：永远选第一个事件 25.3%，永远选最后一个 5.0%。

同一批案例里，**63.4%（281/443）存在两个以上事件并列最高优先级**，47.2%（209/443）有三个以上，31.8%（141/443）有四个以上——即三分之一的案例里，前 3 名的取舍完全发生在并列组内部。把"位置序 top-3"与"当前排序 top-3"逐例比对，17.8%（79/443）完全相同；在有并列的案例中，同档内唯一可用的次序就是位置。在"金标确实位于并列组内"的 200 例中，金标最早 24.9%、最晚 15.3%、居中 31.0%。如果只把并列组内的顺序换成"金标优先"（其余保持位置序），top-1 从 38.1% 升到 67.5%，top-3 从 65.2% 升到 86.7%——**+29.3pp / +21.4pp**。这个上界用到了金标，因此不可实现；它只说明"排序"这个自由度值多少。

**事实 4：`scorer_artifact` 是一条已经铺好、从未通电的线。**

`causal_audit.scorer_artifact` 在配置里是合法字段（`config.py:81`），加载时校验存在性（`config.py:212-220`），其内容摘要进入 `scorer_digest`（`step_uncertainty.py:132-135`）、`build_execution_identity` 的 `artifacts.scorer`（`identity.py:47`）、回放审计（`replay.py:57`）、后端运行时身份（`backends.py:123`）。也就是说，**"这次排序由哪个产物产生"这件事，框架从头到尾都会记录**。但全仓没有一处代码把它**解析成打分器**：所有引用只做两件事——校验路径存在、计算内容摘要。`SCORER` 常量（`causal_contracts.py:22`）写死为 `semantic_proxy.v2` 加那张 8 项优先级表，与 artifact 内容无关。

### 8.2 重构：可学习的对象是"读哪些事件"，不是"判什么"

四件事拼起来给出一个与 §4 不同的重心。

检测器的权威路径是这样走的：事件进来 → 打上 1~3 档优先级 → 排序 → **取前 3 个**（`top_k: 3`）→ 交给 tracer / defense 这些 LLM 阶段去选和判。

全量 693 例的 `select_causal_candidates` 选中规模**恒为 3**，`omitted_event_ids` 与 `unresolved_event_ids` 全部为空，`state` 全部是 `selected`。原因是结构性的：`top_k=3` 先把候选截到 3 个，`max_projection_events: 16` 的容量检查发生在它之后（`step_uncertainty.py:181-183`），永远看不到第 4 个。轨迹最长 43、中位 6，即便有 28 例超过 16 个事件，风险画像的 `event_limit` 是 `max_projection_events × max_dependency_depth = 128`（`:43`），仍然不触发。

因此：**在这套配置下，唯一真正起作用的读取宽度限制就是 `top_k=3` 这个常量，容量参数从未生效。**

这是 §8.1 事实 3 那个覆盖率数字的机制解释：被丢掉的 34.8% 不是因为容量不足，而是因为排序在第 4 位之后就不再被读取。

于是最贵的那一环（LLM 听证）实际在做的是：**在一组由静态整数表排好序的 3 个候选里做选择**。表把 49% 的事件钉在同一档，同档内按谁先出现。而被选中的概率分布是：金标在前 3 里的机会 65.2%，剩下的 34.8% 里中位排名是第 6。

这解释了一件事，这件事在 §2 和审计里都只被描述成"测量失败"或"门控过严"：**一条规则即使完全正确，如果它需要的事件从没进过 top-3，它在正式测量里的表现就是零增益**。历史 4 个候选中 3 个 `not_evaluated`、1 个 `rejected`，其中 `D001` 的直接表现就是 `fired_rate=0`。规则没被触发，可以是规则错，也可以是它瞄准的位置根本没被读到。这两种情况在现有记录里无法区分——因为没有任何指标记录"金标是否进入了候选集"。

这也是为什么 §4.4 的权威分层和 §4.3 的规则归纳都不够：它们改善的是"给定读到的证据，判得对不对"。而当 34.8% 的案例里正确答案压根没进读取窗口时，判定层的所有改进在这部分案例上恒等于零。

**修正后的判断：自我改进的第一个对象是读取预算的分配函数（排序），第二个才是判定规则。** 这不是把问题简化，而是把它挪到了唯一同时满足三个条件的量上：

- 有监督：定位金标（443 例）是逐事件的位置标签，不需要新标注；
- 无权限风险：排序不产生 `decision_authority` 也不产生 `localization_authority`，与 §4.2 的正交性完全兼容——它只是决定"谁被看到"，不决定"谁对"；
- 可离线千次迭代：排序是纯函数，`SIGNAL_PRIORITY` 进 `scorer_digest`，换一个打分器就换一个可追责的身份。

### 8.3 困惑度：从"决策信号"改写为"排序先验"

这里必须先把一个常见的做法排除掉。

**为什么困惑度不能当判定信号。** 本检测器的判定权威由校准授予，不由分数幅度授予：`conviction_verdicts_for` 走 `_calibrated_conviction_verdicts`，内置谓词必须在 gold-clean 上零误报才保留定罪权（`interventions.py:810-817`）；`interventions.py:227` 更是硬拒绝 learned runner 持有定罪权。在这种语义下，一个连续的困惑度分数**没有任何合法入口进入 `state`**。硬接进去就是绕过校准，与 §4.4 直接冲突。文献里 semantic entropy 一类工作把不确定性当分类阈值用，那是二分类器的语境，搬过来会破坏这套权限模型。

**为什么它能当排序先验。** 排序不需要权威，只需要一个在全序上能打破并列的量。而底座现在缺的正是这个：8 档整数在 63.4% 的案例上并列，并列内只能靠位置。困惑度提供的恰恰是"同样被打上 priority 3 的 2,583 个事件里，哪一个更异常"。

**这个槽位是预留好的，不是新造的。** 三条独立的证据：

1. `causal_audit.score_source` 的合法取值里已经有 `teacher_forced_ppl`（`config.py:194`）。
2. 它在 `step_uncertainty.py:42` 被显式判为 `unsupported`，`health` 里写着固定原因 `teacher_forced_backend_unavailable`——即"后端没接"，不是"这个方向被否决"。
3. `risk_context.py:31` 在 `score_source != "semantic_proxy"` 时直接返回 `None`，同一处 `payload["score_source"]` 写死为 `"semantic_proxy"`（`:82`）。

换句话说，底座对困惑度的态度是**"已登记、已拒绝、保留入口"**，而不是"从未考虑"。补上这个后端是填空，不是改架构。

**正确的契约（四条，缺一不可）：**

- **因果前缀**：只能对 `events[:t]` 做 teacher-forced 打分，绝不能看见 `t+1` 及以后。这条纪律底座里已经有正确先例——`predict_action_boundary` 的注释明确写着"Use only observations available through t; never generate t+1 observations"（`next_action.py:82`）。
- **零决策权**：分数只影响 `top_k` 的成员与顺序，不写进 `proof`，不参与 `adjudicate`。
- **身份化**：模型/版本/超参写进 `scorer_artifact`，摘要自动进入 `scorer_digest` 与执行身份。冻结回放会因为 `scorer_artifact` 变化而拒绝复用旧阶段——这是想要的行为。
- **零额外 provider 请求**：teacher-forced 打分是对**已记录的文本**做本地前向，不调用生成接口。这与审计里 forecast 那 +7.7% 请求 / +22.7% 耗时的成本结构完全不同。

**它可能买到多少。** 上界是 §8.1 的 +21.4pp（top-3）。但要诚实：困惑度与文本长度、体裁强相关，用绝对分数会把长模板的 observation 一律排到前面。必须做**轨迹内归一化**——把每个事件的分数转成它在本轨迹内的百分位或 z 分数，再做跨轨迹比较。如果不做这一步，这个方法在审稿时会被直接判为伪增益。可报告的是覆盖曲线（top-k 对 k），不是端到端精度。

**与文献的区别必须写清楚。** 这不是"用困惑度检测幻觉"，而是**在固定读取预算下用困惑度分配读取机会**。前者是分类问题，需要一个阈值和一次判定；后者是资源分配问题，需要的是一个序。前者在本底座的权限模型里没有入口，后者有。

#### 8.3.1 对 RSI 循环本身的推进：测量分辨率

上面说的是困惑度对**定位**的用处。它对**循环**还有一个更根本的用处，这一条比排序更重要。

**底座现在的反馈是离散的。** 一个案例的终态是 `state ∈ {verified_error, verified_clean, unresolved}`，再叠加两个权威字段。对一个候选的评估就是"在这批案例上，配对计数变了吗"（`metrics.paired_delta`）。在 443 个可定位案例上，**一个案例的翻转约等于 0.23pp**；要区分两个候选，需要它们造成若干例翻转的差异。

这带来一个循环层面的硬限制：**内层循环无法区分"两个候选都好"与"两个候选都无效"**。历史运行里 4 个候选 0 准入，其中 3 个 `not_evaluated`、1 个 `rejected` 且只有 FP 减 1——在 0.23pp 的分辨率下，这些结果与"候选没有做出任何可测改变"是无法区分的。

仓库自己承认了这个缺口。`evolution/calibration.py:9-18` 明确拒绝计算 Brier、log loss、AUROC、校准曲线：

> Those need a numeric score per case; computing them over an ordinal label would mean inventing the numbers and reporting the invention as a measurement. `CONTINUOUS_METRICS_UNAVAILABLE` records that gap in the report itself.

**这句话是本设计的直接依据。** 框架不是"不需要连续信号"，是"当前没有合法的连续信号，因此拒绝伪造"。困惑度正是缺失的那个量：它对每个事件给出一个实数，而不是一个四态标签。

于是困惑度对 RSI 的贡献可以准确表述为**提高测量分辨率**，具体有三个可用点：

1. **L_micro 的适应度函数**（§4.3 留空的位置）。CEGIS 用反例消除做约束，但当两个规则消除了同等数量的反例时，它无法选择。可以加一个连续判据：**优先保留那些触发事件困惑度更高（更异常）的规则**——即"它抓到的确实是罕见模式，而不是模板文本"。这个计算在纯函数层面完成，毫秒级，不需要任何流水线回放。这是 L_micro 能真正"跑上千次"的关键。
2. **测量的分层预算**。按案例的困惑度裕度排序，优先测量"接近翻转"的案例（分歧边缘），跳过"怎么改都不会动"的案例。这把有限的重测预算集中在有信息的地方。
3. **候选的预筛**。在两个候选都要付端到端成本时，先用离线困惑度差排序，避免把预算花在显然是噪声的候选上。

**必须同时写下的三条限制**，否则这一节会被误读为"困惑度能提升精度"：

- 困惑度度量的是**模型的惊讶程度**，不是**事实的对错**。它只能排序"哪里更值得看"，不能判断"那里错没错"。
- 它与文本长度、模板化程度强相关，必须做轨迹内归一化（§8.3）。
- 它不进入任何权威字段。§8.3 的四条契约（因果前缀、零决策权、身份化、零额外请求）对它全部适用。

因此正确的表述是：**困惑度不提高判定精度，它提高循环的分辨率**。这两件事在论文里必须分开写——前者可以做端到端实验并被 SEVA 那类负结果打中，后者是机制层的改进，只需要证明"同样的预算下，能产出更多可测候选"（即 §3.3(C) 的 admissions-per-proposal 口径）。

### 8.4 下一步推测：从"检测信号"改写为"可检查性掩码"

本轮把 `predict_action_boundary` 在全量上跑了一遍，结果推翻了一个设计假定：

| 指标 | 实测值 |
| --- | --- |
| 可构造边界的事件 | 209 / 5,269 = **4.0%** |
| 至少有一个边界的轨迹 | 139 / 693 = 20.1% |
| `divergent`（成功声明而无观察到的恢复） | **0** |
| `unknown` | 95（其中 `possible_fallback_unverified` 72、`unrelated_or_unrecognized_action` 23） |
| `compliant` | 114（`observed_retry_or_repair` 88、`honest_failure_report` 26） |

按 agent 家族分布极不均衡：BFCL 一家的边界占 143/209（68%），OpenDeepSearch 全族只有 1 个。

**结论一：它不能当反例源。** 唯一能表达"违规"的状态 `divergent` 在全量 693 例上出现 0 次。§4.5 和审计都指望这条通道供给反例，但它的产出上限是 0。审计里记录的三个 `divergent` 来自模型预测路径（100 例中 43 个接受预测、8 个有效比较），与这条确定性路径是两回事，不能互相援引。

**结论二：它解释了 forecast 的净零结果——但归因要分两层，不能混。**

这里有一个容易被混淆的对照，必须分开：

| 通道 | 可打分目标供给 | 实测代价 |
| --- | --- | --- |
| 确定性边界（`predict_action_boundary`） | 209 个事件 / 139 轨迹 = 20.1% | 0 请求 |
| 模型预测目标（`forecast_targets`） | **3,828 个候选 / 679 轨迹 = 98.0%** | 每案若干次 provider 请求 |

**供给层不是瓶颈。** 模型预测路径的靶标覆盖 98.0% 的轨迹，远超确定性通道。历史运行的损失发生在它下游：100 例中 23 例 `budget_exhausted`（预算预留给 3×votes，见 `judge.py:375-380` 的注释与 §审计 P0-1），43 例接受预测，而代表票里只有 8/43 得到有效比较（81.4% 为 `unknown`）。

**因此净零结果有两个独立原因，都必须写下来：**

1. **比较层失效（可修）**：引文逐字匹配过严、前缀只保留 6 个事件、时序判断门槛过高，导致 81% 的比较返回 `unknown`。这是实现缺陷，审计的 P0-1/P0-2 判断正确。
2. **确定性通道供给为零（不可修，只能换路）**：`divergent` 在 693 例上出现 0 次。任何指望"零成本本地比较"供给反例的设计，上限就是 0。

**一个此前未被记录的对照：模型预测目标的金标覆盖率其实高于 top-3。** 在 443 个金标阳性案例上，比较"金标位置是否落在 `forecast_targets` 的靶标集合内"与"金标位置是否落在 `top_k=3` 内"：

| 家族 | n | 金标在预测靶标内 | 金标在 top-3 内 |
| --- | --- | --- | --- |
| BFCL | 103 | **89.3%** | 53.4% |
| Camel | 57 | **91.2%** | 73.7% |
| Octotools | 30 | **86.7%** | 80.0% |
| OpenDeepSearch | 58 | **72.4%** | 67.2% |
| SmolAgents | 57 | **56.1%** | 49.1% |
| Magentic_One | 54 | 33.3% | **55.6%** |
| OpenManus | 84 | 46.4% | **84.5%** |
| **池化** | **443** | **67.9%** | **65.2%** |

池化只差 2.7pp，但**结构完全不同**：预测靶标在 BFCL 上覆盖 89.3%，而 top-3 只有 53.4%——这是 35.9pp 的差距，且恰好落在错误最多的家族上。反过来 OpenManus 上 top-3 明显更好。

这意味着"下一步预测"的价值不是零，而是**它看到了 top-3 看不到的位置**。历史运行没有把这份覆盖转成精度，问题出在比较层（81% unknown）而不是供给层。

**但要立刻给出反面约束**：预测靶标是**模型生成的候选**，覆盖到位置不等于覆盖到正确判断。BFCL 的 89.3% 覆盖对应的是"模型觉得值得检查下一步"，仍然需要 43 例才接受预测、8 例有效比较来兑现。所以这张表支持的是"**拓宽读取宽度**"（§8.9 读数 0 的另一个理由），不是"重启动预测机制"。

两者叠加的解释是：**便宜的通道没有供给，有供给的通道太贵且比较失败。** 这意味着"下一步预测"这个方向的价值不在当前形态里——要么把它做成零成本且能产出 `divergent` 的确定性判据（需要新的可检查模式，例如检索结果的逐字覆盖），要么承认它只能作为 §8.4 后半段的掩码使用，或者把它降级为**候选扩展器**（只在 top-3 未覆盖时补充位置，不承担判断）。

**结论三：它有一个真实用途，但不是检测——是可检查性掩码与成本模型。**

这里需要区分**两种可检查性**，它们的量级差 17 倍，混用会得出相反的结论：

| 可检查性层级 | 判定方式 | 覆盖（金标阳性 443 例） | 单次成本 |
| --- | --- | --- | --- |
| **L1 确定性可检查** | `predict_action_boundary` + `compare_next_action`，纯函数比对记录 | 事件级 4.0%（209/5269）；轨迹级 20.1% | 0 请求 |
| **L2 预测靶标可检查** | `forecast_targets` 选出值得检查的位置，需模型比较 | 金标位置落入 67.9% | 每案若干请求 |
| **L3 不可本地检查** | 需要完整五阶段流水线 | 余额 | L1 回放 ~800 秒/例 |

只有 L1 能在无模型的情况下判定对错，因此只有它能支撑 §4.3 的 L_micro"上千次迭代"。**L2 覆盖率高 17 倍，但每次检查都要付费且已证明 81% 的会比较为 `unknown`。**

- 落在 L1 的事件上，一条规则可以被**局部证伪**——只看下一事件的记录就能判对错。这类规则进 L_micro。
- 落在 L2 上的规则可以"先筛后判"：用预测靶标扩展候选集，再由现有模型阶段判定。这类进 L_micro→L_meso 混合通道。
- 落在 L3 的规则本地无法判定，必须端到端测量，成本高一个数量级。

现在的框架对这三类一视同仁，全部走 L1 回放。用一张分层的可检查性掩码把一部分规则路由到纯函数通道，是把 L_micro"能跑上千次"这件事变得**有依据**而不只是**有愿望**。而且 L1 掩码本身是确定性的、零请求、可冻结——它不会引入新的测量噪声。

**边界必须写明：** L1 的 4.0% 太薄，不能当主机制；L2 的 67.9% 看起来厚，但它依赖模型且比较层尚未修好。任何把下一步预测当作**核心判定信号**的方案，用这张表就能判死；把它当作**路由元数据或候选扩展器**，则 L2 的比例说明它确实有可用空间。

顺带一个可验证的预测：如果 §8.9 读数 0 显示加宽 top_k 有效，那么 L2 与 L3 的边界会向内移动，L2 的边际价值下降；如果加宽无效，说明问题不在位置而在判定，L2 作为候选扩展器的价值反而上升。这两个结果互相排斥，因此读数 0 必须先做。

### 8.5 验证空洞：`verified` 层没有供给（修正 §4.5）

这是本轮最重要的一条，它直接改了原设计的可执行性。

`causal_failures.build_causal_failure_record` 要给出 `evidence_state="verified"` 并停到 `aligned/late/early`，需要同时满足（`:168-176`）：`materiality == "verified"`、`first_introduction == "verified_declared_result"`、`counterfactual.status == "verified_symbolic"`、`localization.origin_status == "verified_local_result_origin"`。这条链的起点是 `bind_arithmetic_claim`（`causal_contracts.py:166`），而它要求：`source == "judge"`、三个数字可解析、`quote == delivered`、且能在 packet 里找到**唯一**一个 `arithmetic_check` 状态为 `deviation` 的 calculator 交互。

本语料的 calculator 调用数是 **0**。

因此：**这条"已验证首次引入"的通道在当前语料上几乎不可能触发，`CE_l` 定位反例的供给接近于零。** §4.5 把 `causal_failures.py:184` 的 `classification` 当作 `CE_l` 的来源，这个假定不成立。同理，`verified_deviation` 信号为 0 意味着 §8.1 的排序键第二项永远不起作用，也意味着"verified 优先"这条排除策略在本语料上没有区分力。

三个可选处置，必须选一个并写进实验设计，不能含糊：

1. **扩生产**：为 `verified` 层增加非算术的生产者（例如结构化工具返回值与声明的字面比对、检索结果与引用的逐字覆盖）。这是把 §5.3 的"扩展检测器"从可选项变成前置项。
2. **降目标**：承认本语料上不存在"已核验首次引入"，`CE_l` 改用**代理监督**——金标 `hallucination_step`（443 例）直接作为定位标签，明确标注这是数据集标注而非运行时核验。这在方法上更弱，但可用。
3. **报告为不可用**：把 `CE_l` 声明为当前语料不可供给的反例类，只做 `CE_c` 与 `CE_m`。

我的建议是 2 → 1 的顺序：先用金标代理把 L_micro 跑通并产出可解释规则（便宜、能出结果），同时把 1 作为独立的能力缺口项登记进 `evolution/gaps.py`——它的 `GAP_REASONS` 里已经有 `missing_representation` 与 `missing_localization_operator` 两个现成分类，不需要新造词汇。

### 8.6 权威不对称：学习出来的规则比手写规则更容易定罪

这一条是我在读 `conviction_verdict_table` 时发现的，它是个方向相反的激励。

- **内置谓词**：必须通过校准记录（零误报、源码指纹、语料指纹、现场重测），否则 `_calibrated_conviction_verdicts` 返回空集，绑定它的 guard 被抑制（`interventions.py:755-758`）。
- **演化出的 `typed_dsl` 程序**：`custom_conviction_verdicts_for(programs)` 直接取程序自己声明的 `conviction_verdicts`，不做任何测量（`interventions.py:771-778`）。表构造处有一句注释解释了理由——"its authority comes from the design contract ... and its admissions are governed by the metamorphic gate"（`:772-775`）。
- **分发处**：`conviction_verdicts_for` 只在 `not runner or runner == "deterministic" or predicate in PREDICATES` 时走校准（`:810`）。演化程序的 runner 是 `typed_dsl`、名字不在 `PREDICATES` 里，于是**直接返回声明值**。

也就是说，同一个检测器里：手写的解析器要 250 个干净案例零误报才能定罪，而自进化刚生成、从未在任何数据上跑过的程序，凭声明就能定罪。`metamorphic` 门检查的是**不变性**（换 case id、加无关工具调用时判定是否不变），不是**准确性**——它防不住一条稳定地判错所有案例的规则。

**这是 RSI 的激励方向问题，不是实现 bug。** 循环被奖励去产出"更容易获得权威"的东西，而那条路恰好是不需要测量的那条。历史三轮没有滥用它（因为准入数为 0，还没走到这一步），但这个口子一旦打开，框架会系统性地偏向未经验证的规则。

修正很小，且与 §4.4 的 `θ` 是同一件事：**让 `typed_dsl` 程序也走测量，只是测量集不同**——内置谓词用全语料 gold-clean，演化程序用开发划分（`splits.py` 的 `pool`/`gain`，不能碰 `validation`/`final`）。测出来的就是 `θ`。这不需要改权限模型，只需要把 `_calibrated_conviction_verdicts` 的适用范围从"内置谓词"扩到"任何要求定罪权的程序"。

### 8.7 发散：还有哪些接口是空的

按"可测性 / 成本"排序，供后续取舍。前四条有代码或数据支撑，后两条是推断。

| # | 接口 | 现状 | 为什么可能是机会 | 代价 |
| --- | --- | --- | --- | --- |
| 1 | `top_k` 宽度 | 常量 3，且是所有 693 例里唯一生效的宽度限制 | 覆盖率 65.2% → 88.9%（k=6）；`select_causal_candidates` 的选中规模恒为 3、从未截断（§8.2），说明放开它不会被下游容量挡住 | 一个配置值。但 `build_risk_context` 有字节上限 `min(6000, max_added_prompt_tokens_per_case // 2)`（`risk_context.py:94`），超限从尾部逐个弹候选、只剩一个时返回 `None`。拓宽 top_k 是否真的进入提示，**必须实测而不是假定** |
| 2 | 排序键 | `(8 档整数, 恒为 0 的分量, 位置)` | 见 §8.1 事实 1–2：63.4% 案例并列最高优先级；但 §8.8 实测学习型加权排序在跨族下不如静态基线 | 一个纯函数。**已有否定结果，不建议直接投入** |
| 3 | 位置先验 | 无 | 金标位置中位数为 3、轨迹中位长 6，即"错误通常出现在中段"；这是零成本、无标签的弱先验 | 零。可作为排序的第二个特征，也可单独作为对照臂，用来证明学习确实超过了"猜中间" |
| 4 | 权威测量范围 | 只覆盖内置谓词 | 见 §8.6，激励方向反了 | 复用 `conviction_calibration` 的测量循环，换数据集 |
| 5 | 风险信号词表 | 8 项，2 项占 49% | 一个只有 8 档、且两档吞掉一半事件的词表，其信息容量接近饱和。扩词表比改排序更深，但需要新的确定性检测器 | 高，属于 §5.3 的扩展决策 |
| 6 | 家族 × 类别结构 | 强混杂 | BFCL 的 103 个错误全部是 Tool-Use；OpenManus 的 84 个里 42 个是 Human-Interaction。这既是分层的机会，也是 H5 迁移检验的直接威胁 | 零（分析），但会限制可达结论 |

第 6 条值得单独说一句：**义务类别与 agent 家族高度共线**。BFCL 164 例中 103 个错误全是 Tool-Use Hallucination。这意味着任何"按义务类别分层学到的规则"都可能只是在学"BFCL 长什么样"。`no_case_id` 检查挡得住显式的案例 ID，挡不住这种结构性的家族指纹。跨族迁移实验因此不是可选项，是唯一能区分"学到了义务"与"记住了家族"的检验。

### 8.8 排序可学习性的实测上界：三个交叉验证，一个否定结果

前面几节把排序当作最可能的改进对象。这一节报告我实际做了三次留一交叉验证，**结论是否定性的**，必须写在设计前面。

**实验设置**：443 个金标阳性案例。所有数值均为 **leave-one-family-out**（留一 agent 家族）的折外结果，即任何一次评估所用的参数都没有见过被评估家族的数据。基线是被测的静态排序 `(priority, -verified, position)`。

**控制臂（零学习，必须有）**：

| 排序规则 | top-1 | top-3 |
| --- | --- | --- |
| 永远选最早事件 | 25.3% | 62.8% |
| 位置中段先验（无信号） | 16.9% | 56.7% |
| 永远选最晚事件 | 5.0% | 49.0% |
| **当前静态排序** | **38.1%** | **65.2%** |

静态排序已经显著好于任何"猜位置"的规则（top-1 +12.8pp，top-3 +2.4pp），说明那 8 档优先级确实携带信息。但也注意 top-3 只比"选最早"高 2.4pp——**在读取宽度这个维度上，当前排序几乎没有超过最朴素的位置策略**。

**实验一：信号提升率加权（learned lift）**。从训练折估计每个信号的 P(事件位于金标 | 该信号存在)，以基准率为参照做收缩，取事件所含信号的最大提升率为权重。

| 结果 | top-1 | top-3 |
| --- | --- | --- |
| 静态（全量） | 38.1% | 65.2% |
| 学习型（折外 pooled） | **33.9%** | **63.9%** |

**跨族迁移失败。** 逐族看：BFCL 从 36.9 掉到 10.7，其余六个家族**完全不变**（不是变差，是排序结果逐字相同）。这说明在 35 种信号模式里，加权没有产生任何新的区分；唯一发生变化的家族，变化方向是错的。

**实验二：位置分箱先验作为并列消解器**。从训练折估计相对位置十分位的命中率，仅在优先级相同时使用。

| 结果 | top-1 | top-3 |
| --- | --- | --- |
| 静态（折外 pooled） | 38.1% | 65.2% |
| + 位置先验 | 38.1% | **68.4%** |

逐族：Octotools 80.0→93.3、OpenDeepSearch 67.2→74.1、SmolAgents 49.1→56.1、OpenManus 84.5→85.7、Camel 不变；**BFCL 53.4→66.0 上升，但 Magentic_One 55.6→33.3 崩塌**，两者抵消后池化只剩 +3.2pp。

Magentic_One 的崩塌有明确原因：它的中位轨迹长 8、金标中位位置 3，而 Octotools/OpenDeepSearch/OpenManus 是 4 和 2。相对分箱在长轨迹上把"中段"摊薄，金标反而落到低分箱。这不是实现 bug，是**用相对位置做先验本身就依赖轨迹长度分布**。

**实验三：为什么两者都失败——信息不足，不是估计不足。**

全量 443 个案例只有 **35 种不同的信号模式**；其中 155 例（35.0%）共享同一个模式 `(('unsupported_claim',), ('unverified_action','unverified_observation'))`。而 **98.9%（438/443）的案例存在重复排序键**。

这在数学上封死了学习空间：当 98.9% 的案例里所有候选都落在同一档优先级、且第二分量恒为 0，排序只能退化为位置序。任何"学习排序权重"的方法，可动的自由度只有"同档内如何排"；而当同档内没有可区分的特征时，它学到的只能是训练集的位置分布——实验二恰恰证明了这一点，它学到的就是位置分布，因此必然在位置分布不同的家族上失败。

**本节的结论，按可靠性排序**：

1. **确定**：`top_k=3` 切断 34.8% 的金标位置，而容量参数 `max_projection_events` 在此配置下从未生效（§8.2）。**加宽读取窗口是无需学习、可立即测量的改动**（§8.9 读数 0）。
2. **确定**：排序的并列结构（98.9%）意味着"更好的排序算法"不是主要杠杆，**"给同档事件提供可区分特征"才是**。当前 8 档词表把 49% 的事件钉在同一档（§8.1 事实 1）。
3. **否定**：基于现有风险信号的加权排序在跨族评估下**不优于**静态基线。任何把它写进方法贡献的方案，会在第一轮跨族实验中被推翻。
4. **部分**：位置先验在 top-3 上有 +3.2pp 的池化增益，但代价是单族 −22.3pp 的崩塌。若要使用，必须按轨迹长度分层或按家族校准——那就不再是"零成本先验"。

这个否定结果本身就是 §3.3(C) 那个二阶主张的第一条证据：**归因与排序的可学习性必须被度量，而不能被假定**。它同时给出 §4.3 的一个约束——L_micro 的搜索空间需要新的可区分特征，而不只是新的权重；否则 CEGIS 会在同一个 35 种模式的空间里空转。

### 8.8.1 一个可能被误读的观察

§8.1 的逐信号提升率看起来很有说服力，必须单列警告：

| 信号 | 声明优先级 | P(金标 \| 该信号) | 提升 |
| --- | --- | --- | --- |
| `external_guidance` | 1 | 0.830 | **6.21×** |
| `observed_tool_failure` | 1 | 0.257 | 1.92× |
| `unverified_action` | 2 | 0.163 | 1.22× |
| `unverified_observation` | 2 | 0.129 | 0.97× |
| `unsupported_claim` | 3 | 0.122 | 0.91× |

基准率 443/3315 = 0.134。表面读法是"优先级排错了：`external_guidance` 该排第一，`unsupported_claim` 根本不携带定位信息"。

**这个读法是错的，原因是家族混杂。** `external_guidance` 在全语料共 112 次出现，**且 112 次全部来自 Camel（57）与 OpenManus（55）两个家族**——其余五个家族一次都没有。在 443 个金标阳性案例内部，该信号共 88 次（Camel 38、OpenManus 50），其中 73 次恰好落在金标位置。`external_guidance` 的识别靠 `_human_guidance_channel`（`step_uncertainty.py:25`），它匹配 `ask_human` 之类的工具名，而只有这两个家族使用人类交互工具。

所以这个 6.21× 不是"人类交互信号能定位幻觉"，而是"**`external_guidance` 只存在于两个人类交互家族的轨迹里，而这类轨迹的幻觉恰好发生在问人那一步**"。它的 6.21× 是"在一个本来就只有这两个家族的子集里，事件落在金标位置的条件概率"——不含任何跨族信息。

§8.8 实验一已经给出了这个判断的实证：把 Camel 与 OpenManus 留出后，用其余家族估计的权重在这两个家族上没有带来任何增益。**混杂在留一交叉验证下会立刻坍缩。**

这条观察的真正用途不是改权重，而是**指出一个数据采集层面的问题**：当前 8 档信号词表在两个人类交互家族上极度集中，在 BFCL 上几乎只剩 `unverified_action/observation`。词表的覆盖不均，比词表的排序错误更根本。它也是 §8.7 结论 2 的另一个说法。

### 8.9 修正后的最小实验序列

§7.2 的三臂实验（A/B/C）成本是三轮 L1 回放量级。基于本节的测量，可以把它前面插三个**不涉及任何规则学习**的读数，它们更便宜且决定后续一切：

**读数 0（半天，零新代码）：加宽读取窗口。** 把 `top_k` 从 3 扫到 `{1,2,3,4,6,8}`，记录每个 k 的金标覆盖率（已测：38.1/49.4/65.2/77.0/88.9/94.8）与**端到端**定位精度。这一步回答：**瓶颈是读取宽度还是判定？**

必须在两种读法之间做出判断，不能含糊：
- 若 k=6 时端到端定位精度显著上升且成本可接受 → 历史失败的主因不是规则表达力，是窗口太窄，§4 的整个叙事要改写；
- 若端到端精度不动（很可能，因为 §8.1 已测到 `build_risk_context` 有字节上限会弹掉尾部候选）→ 判定层确实是瓶颈，§4 的设计值得投入。

两种结果都有信息，这正是它值得先做的原因。**必须实测拓宽后候选是否真的进入了提示**：`risk_context.py:94` 的上限是 `min(6000, max_added_prompt_tokens_per_case // 2)`，超限时从尾部逐个弹出候选（`:95-99`），只剩一个时直接返回 `None`。当前配置下该上限为 6000 字节，因此 width 扫描必须同时记录每个 k 的**实际候选数与截断状态**，否则会得到一个"改了 top_k 但提示没变"的假结果。

**读数 1（一天，零新代码）：零学习下界。** 用"永远最早"（top-1 25.3% / top-3 62.8%）、"逆位置序"（32.1% / 67.3%）、"位置中段先验"（16.9% / 56.7%）三个臂重跑同一批案例。任何后续方法必须显著超过它们，尤其是逆位置序这个**反直觉但很强的下界**。

**读数 2（一天，已有本轮数据）：排序可学习性。** 用 leave-one-family-out 复现 §8.8 的三个实验。本轮已经得到否定结论（学习型加权 33.9% < 静态 38.1%），这一步的作用是把它变成可复现的基线，并确认在更大的特征空间下是否仍然否定。**如果仍然否定，就不要在排序上继续投入，直接进入 §7.2 的 A/B/C。**

**读数 3（两到三天）：困惑度作为并列消解器。** 这是 §8.3 的可执行形式，协议必须写死，否则会被做成一堆无法比较的结果。

1. **范围**：只做离线打分，不接运行时。对 693 例的每个事件位置 t，用 teacher-forced 方式计算 `content`（及 `tool_interactions` 的 `arguments` / `observation`）在 `events[:t]` 条件下的负对数似然均值。零 provider 生成请求。
2. **归一化**：绝对 PPL 与文本长度、体裁强相关，必须转成**轨迹内相对量**——同一轨迹内所有事件的 PPL 取 z 分数或百分位。不做这一步，实验结论无效（§8.3 已写明）。
3. **插入方式**：只作为并列消解器，排序键改为 `(priority, -z_ppl, position)`。**不改优先级本身**，这样能单独测出"同档内选谁"的贡献。
4. **评估**：leave-one-family-out，报告 top-1 / top-3 / top-6，与三个基线对照——静态排序（38.1/65.2/88.9）、位置先验（38.1/68.4/—）、金标 oracle 上界（67.5/86.7/—）。
5. **判定标准（先写死，避免事后解释）**：只有在 top-3 上**优于位置先验的 68.4%** 且在至少 5/7 的家族上不退步，才算通过。仅仅优于静态排序不算通过——位置先验已经做到了 +3.2pp，而它零成本。
6. **可能的失败模式与对应读数**：若 top-1 上升而 top-3 不变，说明困惑度只在高置信事件上有用，可退化为"高置信优先、其余按位置"；若某一家族崩塌，先查该家族的轨迹长度分布（BFCL 中位 10，Octotools 中位 4），按长度分箱再试一次；若全线不如位置先验，则结论是**困惑度在本语料上不携带定位信息**，应记入负结果并停止该方向。

这条读数的价值在于它把"困惑度能不能推进 RSI"从主张变成了一个可以在三天内得到否定答案的实验。考虑到 §8.8 中三个基于风险信号的尝试全部失败，**负面结果的概率并不低**，应按此预期安排预算。

**然后**才进入 §7.2 的 A/B/C。

四个读数加起来小于一次完整进化周期，却能把"该改什么"从推断变成测量。**如果只能做一件事，做读数 0。** 它比 §6.3 建议的"固定候选重测"更便宜，而且回答的是同一个问题的前半段——历史失败的 3/4 `not_evaluated` 里，有多少其实是因为候选的靶事件从未进入读取窗口。

同时，§8.8 的否定结果应当被写进正在进行的实验记录，而不是等到 §7.2 之后：它已经改变了 §4.3 中 L_micro 的可达空间（需要新特征，而不只是新权重），也改变了 §6.2 中 H1/H2 的预期。

---

## 9. 总纲：这个底座的长处在哪，novelty 应该落在哪

§8 的测量完成后，可以给出一个比 §3 更收敛的判断。这一节是全文的结论，写作时可以直接作为定位段落。

### 9.1 这个检测器真正稀缺的东西

绝大多数 agent 幻觉检测工作输出一个二值判断。这个底座输出的是**一份带权限标注的诊断档案**：四态终态 × 两个正交权威字段 × 逐阶段健康状态 × 定位冲突类型 × 定位来源通道 × 逐事件风险信号 × 可零成本复核的下一步比对。全量 693 例上，这套档案是完整的、可回放的、每个字段都有身份摘要的。

文献里没有第二套系统同时具备这四件事：**权威分级**（哪些结论可信、哪些只是假设）、**正交的双目标输出**（判定与定位各自独立降级）、**可核验的定位**（`source_channels` 逐字回溯到事件或工具交互）、**冻结回放**（`scorer_artifact` 变更即拒绝复用旧阶段）。

这套东西的价值不在"更准"，而在**它让"系统在哪里不知道"变成可读的**。`unresolved ≠ clean` 这一条不变量，在这个底座里是代码事实（`result.py:78-83` 的 `prediction_state` 返回 `None`），而不是事后解释。

**所以 novelty 的第一个落点不是新的检测机制，而是：目前没有任何方法在"知道自己不知道"这个层次上组织自进化。** 现有工作（AutoSpec 的 CEGIS、AHE 的预测证伪、anytime-valid 的统计门）都在优化"给定标签后的正确率"，共同假设是存在一个二值或标量反馈。这个底座的反馈是**多维且带权限的**，用标量去压缩它就浪费了它唯一的稀缺性。

### 9.2 三个层次的信号，正确用法各不相同

本轮把三类"不确定性"拆开实测，结论是它们不能互相替代：

| 信号 | 它在测什么 | 正确用途 | 错误用途（会被审稿打中） |
| --- | --- | --- | --- |
| **四态 + 权威字段** | 系统对某个案例**有没有资格下结论** | 组织反例与路由（§4.5 CE_m）、区分"没测到"与"判错了" | 当作置信度做加权投票 |
| **困惑度**（待接） | 模型对某段文本**有多惊讶** | 排序先验（§8.3）、测量分辨率（§8.3.1）、L_micro 适应度 | 当作事实置信度、直接预测幻觉 |
| **下一步预测** | 某条声明**能否被记录证据当场否证** | 可检查性掩码（§8.4）、候选扩展 | 当作违规检测器（实测 `divergent` = 0） |

这三条的分工不是设计偏好，是 §8 的实测逼出来的：

- 四态语义的价值在于**它不是连续的**。硬把它变成分数（例如把 `degraded_judge_only` 记为 0.5）会丢掉"这是权限问题不是把握问题"这个信息，而 §4.4 的 `θ` 恰恰要建立在权限之上。
- 困惑度是唯一能提供**高分辨率连续序**的量，但它在因果上离"对错"最远。`evolution/calibration.py` 拒绝伪造连续指标，正是承认了这个空位（§8.3.1 引用的原文）。
- 下一步预测最接近"可判定"，但实测供给为零。它的价值是**告诉循环哪里可以便宜地工作**，不是工作本身。

一句话：**权威字段管"能不能信"，困惑度管"先看哪里"，下一步预测管"哪里能便宜地验证"。** 三者正交，任何两个的合并都会破坏一个。

### 9.3 novelty 的三条候选，按证据强度排序

| 排序 | 主张 | 现有证据 | 主要风险 |
| --- | --- | --- | --- |
| **1** | **候选供给是自进化验证器的隐藏瓶颈**：在固定判定层的前提下，读取宽度与排序决定了一条规则是否"有机会"被测量；诊断量是候选召回率、并列熵、特征多样性指数 | 本轮全部实测（§8.1–§8.2、§8.8）：`top_k=3` 切断 34.8%，98.9% 并列，35 种模式，学习型排序跨族失败 | 可能被视为 ablation 而非方法。对策：做成评估协议而非一次性消融 |
| **2** | **多维权威反馈下的自进化**：用四态 + 权威字段把"失败"拆成可测得多类，使循环能区分"判错 / 未测到 / 读不到"并路由到不同修复 | 代码事实（`result.py`、`adjudication.py`、`conviction_calibration.py`）+ 历史审计的 3/4 `not_evaluated` 均可由此重读 | 需要证明拆分确实改变了循环行为（H3 的对照）；§8.8 显示特征空间贫乏，这是直接威胁 |
| **3** | **含义：困惑度作为循环的测量分辨率** | `evolution/calibration.py` 显式承认该缺口；§8.3.1 三个可用点 | 尚未实测。必须先在 §8.9 读数 3 上取得正结果 |

**第 1 条现在是唯一有完整实测支撑的主张，应作为主线。** 第 2 条是原设计（§4）的核心，仍然成立但需要实验；它的可信度因为 §8.8 的否定结果而下降。第 3 条是纯设想，必须等读数 3。

### 9.4 如果只写一句话

> 我们不是在造更强的幻觉检测器，而是给"检测器如何改进自己"换一种反馈结构：把二值的对错，拆成带权限的多种"不知道"，并用读取预算的可测量性去约束改进过程本身。

这句话的每一个分句都对应上文的可检查内容：验收据 §9.3 表；它不声称精度提升（§6.4），不声称开放式 RSI（§6.4），也不声称困惑度有效（§8.9 读数 3 未做）。

### 9.5 与 §3.6/§6 的关系

本节结论**取代** §3.6 的排序：那里把 (D) 排第一、§3.4 的两条在后，是因为当时 (D) 只有结构性论证；现在 (D) 升级为"候选供给瓶颈"，有了全量实测。§6.2 的 H1–H6 全部保留，但检验顺序应当调整——**H6（宽度）先于 H1–H5**，因为它最便宜且能一票否决 §8 的重构。

§6.1 的四条反驳不变，需要补充第五条：

**反驳 5：你的核心发现是"窗口太窄"，这是一个工程参数，不是科学问题。**

这条反驳有力。"把 `top_k` 从 3 改到 6"本身确实不是科学贡献。可辩护的部分在于**评估协议**：现有全部 agent 自进化工作都在报告端到端精度，而没有人报告"候选是否进入了判定层的视野"。当 34.8% 的金标位置从未被读取时，端到端精度是**一个混合了两个不同失败模式的量**。把它拆开（是读不到，还是读到了判错）是测量方法的改进，而测量方法的改进在实证领域是正当贡献——但必须用实验证明这个拆分改变了结论，而不只是增加了一个指标。这需要 §7.2 的 A/B/C 在**同一批案例上分读两种失败**，并证明二者对候选优劣的排序不同。

## 附录 A：关键代码坐标

| 主题 | 位置 |
| --- | --- |
| 检测编排 | `detector/api.py:215` `_detect` |
| 阴性强制召回 | `detector/api.py:58` `_judge_negative_needs_recall` |
| 结果信封 | `detector/result.py:12` `build_detection_output` |
| 判定语义 | `detector/adjudication.py:289` `adjudicate` |
| 定位目标 | `detector/localization.py:5`、`:18` |
| 干预 schema | `detector/interventions.py:45-136` |
| runner 声明 | `detector/interventions.py:285` `RUNNER_REGISTRY` |
| body 校验 | `detector/interventions.py:1004` `_validate_body` |
| 提示注入 | `detector/interventions.py:1887` `prompt_block` |
| DSL 约束 | `detector/predicate_dsl.py:37-48` |
| 内置谓词 | `detector/predicates.py:46` `PREDICATE_VERDICTS` |
| 因果诊断 | `detector/causal_audit.py:315` `build_causal_diagnostics` |
| 输入投影 | `detector/dataset.py:19` `project_record` |
| 周期主循环 | `evolution/engine.py:3442` `_run_cycle` |
| 机制归纳 | `evolution/mechanisms.py:388` `obligation_for`、`:431` `_deviation_for`、`:340` `operation_for` |
| 开发 L0/L1 | `evolution/development.py:168`、`:352` |
| 开发适配器 | `evolution/development_adapter.py:33-35,38,63-81,84` |
| 四门 | `evolution/gates.py:238`、`:533`、`:692`、`:1194` |
| 配对指标 | `evolution/metrics.py:425`、`:1260` |
| 失败类型化 | `evolution/causal_failures.py:90`、`:184` |
| 能力规划 | `evolution/capability_planning.py:36-100` |
| 可进化空间自述 | `evolution/capability_surface.py:35` |
| 数据划分 | `evolution/splits.py:154`、`:174` |
| 预算默认 | `evolution/config.py` `DEFAULTS` |

§8 新增引用的坐标：

| 主题 | 位置 |
| --- | --- |
| 信号优先级表（8 档静态整数） | `detector/causal_contracts.py:17-22` `SIGNAL_PRIORITY`、`SCORER` |
| 候选排序键 | `detector/step_uncertainty.py:175-179` `(priority, -verified, position)` |
| 风险画像 `score_status` | `detector/step_uncertainty.py:112-122` |
| `teacher_forced_ppl` 的登记与拒绝 | `detector/config.py:194`、`detector/step_uncertainty.py:42`、`detector/risk_context.py:31,82` |
| `scorer_artifact` 的四处身份引用 | `detector/config.py:81,212-220`、`detector/step_uncertainty.py:132-135`、`detector/identity.py:47`、`detector/replay.py:57`、`evolution/backends.py:123` |
| 下一步预测的因果前缀约束 | `detector/next_action.py:82` |
| 下一步预测的边界判据 | `detector/next_action.py:55-66`、`:99-142` |
| `verified` 的唯一生产者 | `detector/causal_contracts.py:147` `arithmetic_check` |
| `verified` 的绑定入口 | `detector/causal_contracts.py:166` `bind_arithmetic_claim` |
| 因果失败记录的三重前置条件 | `evolution/causal_failures.py:168-176` |
| 内置谓词的校准门 | `detector/interventions.py:718-737` `_calibrated_conviction_verdicts` |
| 演化程序绕过校准 | `detector/interventions.py:771-778`、`:810` 分支 |
| 校准准入条件（只看假正例） | `detector/conviction_calibration.py:436-440` |
| 风险上下文字节上限 | `detector/risk_context.py:94` |
| 能力缺口分类词表 | `evolution/gaps.py:47-58` `GAP_REASONS` |

§8 的实测均可离线复现。脚本见 `scripts/probe_candidate_ordering.py`（本轮新增，遵循 `scripts/predicate_corpus_probe.py` 的约定：无 provider、无网络、gold 只用于打分不进 packet）：

```
python scripts/probe_candidate_ordering.py            # 摘要
python scripts/probe_candidate_ordering.py --out probe.json   # 完整结果
```

它输出的顶部覆盖曲线、四个零学习基线、并列结构、两种下一步预测边界供给、预测靶标供给、逐信号提升率、以及三个 leave-one-family-out 排序对照，与本节各表逐一对应。**重跑时若数字与本文不符，以脚本为准**——本文写于 2026-10-17，语料或 `causal_audit` 策略变化都会移动这些数。

一处必须注意的口径差异：`next_action` 有两读法。**reachable**（209 个边界 / 139 轨迹 / 4.0% 事件）遍历全部事件，回答"确定性审计若能检查全部位置，供应有多少"；**routed**（199 / 139）只走 `top_k` 选中的 3 个事件，回答"运行时实际交给了它什么"。两者相差 10 个边界，说明现有路由确实丢掉了一小部分可零成本检查的位置——这本身就是一个可立即修的损失，量级不大但方向明确。

## 附录 B：本次核实的文献

本轮检索核实：AgentSpec（arXiv 2503.18666，ICSE 2026）、AutoSpec（arXiv 2606.24245）、AHE（arXiv 2604.25850）、SEVA（ICML 2026 FAGEN workshop）、MASC（ACL 2026 Findings 1168）、Trajel（arXiv 2605.24219）、selective prediction 与 conformal risk control 的若干 2026 工作（SCoRE 2603.24704、guardrail 认证 2609.22048、anytime-valid 2607.00871）。

`docs/research-positioning-and-roadmap.md` §5 的 13 条来源本轮未逐条复核，引用时按该文档原样归属。

CEGIS / SyGuS / DICE 的谱系、Dawid–Skene 式的标注者可靠性建模、Chow 的拒识分析属于领域基础，本文按基础文献引用，未在本轮检索中复核具体数值。

## 附录 C：与既有文档的关系

- 本文替代 `docs/research-positioning-and-roadmap.md` §3–§4 的机制设想作为**执行层设计**，但研究定位与 novelty 的最终表述仍以该文档为准，直到实验给出证据。
- 本文对 `docs/archive/self-evolution-audit-20260928.md` 的根因排序做了代码复核，结论是根因 1、2、7 仍成立且优先级最高，根因 5 已部分修复。
- 本文不修改 `docs/technical-solution.md` 的任何合同描述；§5 的改动若实施，须同步更新该文档。
