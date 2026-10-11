# 疑惑度引导的双向因果轨迹审计：技术优化设计

> **版本**：v0.1 研究/实施设计，2026-10-08。**状态：拟议，未实现、未开展新模型实验，不代表已经取得检测或自进化收益。**  
> **代码基线**：`liuxinglin70-cmyk/Agent-Hallucination`，`main@0fd2c71a2255d80b6800c8fdfc7d058e7207192e`。  
> **参考**：[核心技术文档](technical-solution.md)、[G0 基线对比](detector-vs-agenthallu-baseline-20260927.md)、[自进化失效分析](self_evolution_failure_analysis.md)、[实验结果索引](results/README.md)。
>
> 本文是后续编码的可执行设计依据；当前功能合同、实验历史和正式结果分别以代码、实验日志及已归档原始报告为准。历史优化方案 v2 包含后来被裁撤的实现，不可直接作为当前可调用 API。

## 0. 核心回答与研究问题

**可以由疑惑度定位反哺幻觉检测，但不能用高疑惑度直接定罪。** 目标是将 **定位侧产生的结构化异常和因果证据** 反馈给原有检测器，使一次检测支持两条路径：

1. **Judge 已判阳性**：在指控的传播链上寻找“首次引入错误”的步骤，帮助 Step Tracer 减少锚在工具使用/最终交付位置的晚判；同时将修复/冲突信息交给原有 Defense 复核，降低由已纠正错误产生的误报风险。
2. **Judge 已判阴性**：使用低成本风险画像发现可能遗漏的任务违约、事实冲突、虚假完成等 **可核查偏差**，在预算/校准条件下触发现有 `run_recall_scan → run_causal_upgrade → run_hearing → adjudicate`；没有可核查偏差时保留阴性与不确定状态。

下一阶段围绕两项贡献开展实验：**长轨迹局部筛查 + 因果归因**；**因果诊断驱动的受控自进化**。前者须先被证实有效，后者不得借模型自我解释替代独立验证。

## 1. 事实基线、误差方向与边界

- 2026-09-27 归档：653/653 例请求；651 权威、2 降级；权威二分类 Precision 0.8954、Macro-F1 0.7476、Accuracy 0.7527；全部 419 个金标阳性中精确责任步命中 114，准确率 0.2721。**Precision 不是 Accuracy。**
- 历史三轮自进化：3/3 轮完成、7 个开发版本、4 个通过 L1 冻结、3 个正式 `not_evaluated`、1 个 `rejected`、0 准入；不能将 0 准入等同于 0 局部改善。
- 用基线归档逐例 `rows` 核对：291 个权威 TP 中，正确步 114、晚 1 步 87、晚 2 步 28、晚 3 步 26、晚至少 4 步 30、早判 6（合计 291）；包含 1 个降级终局 TP 则为 292，其中晚至少 4 步 31、合计晚判 172/292=58.9%。文档早前的“其他晚判 37”与逐例不符，需校正。
- 重点是 Human-Interaction（定位 0.0423）和 Retrieval（0.1067），同时保留 Tool-Use、Reasoning 的正常邻居。晚判只是观察到的误差现象，**不证明**每个晚判都应该强制前移；多错误轨迹和基准标注选择必须独立审查。
- 原始 693 例在历史开发过程中已有暴露。2026-09-28 的 554/139 划分是**内部对照计划**，不能声称独立未见泛化。现有 Git 仓库不跟踪 `datasets/raw/` 与完整 `artifacts/`。

## 2. 代码级调用链与改动点

### 2.1 现有生产链

`detector/api.py::Detector.detect/_detect` → `validate_record/build_packet` → `compile_runtime_plan` → `run_judge` →（按需）`run_recall_scan` / `run_causal_upgrade` → 对每项正向听证 `run_step_tracer + run_defense` → `adjudicate` → `build_detection_output`。

- `detector/api.py::_judge_negative_needs_recall` 对高置信度一致阴性可跳过 Recall；`recall_forced` 接收 `plan.recall_triggered` 或有效 Evidence Snapshot；`negative_needs_recall` 决定是否真正进入补漏链。
- `detector/step_tracer.py::run_step_tracer` 当前使用 `render_judge_view(packet)` 整体轨迹及单个听证指控，检查合法步号、引用和文本合同；“引用合法”不等于“最早因果根因真实”。
- `detector/adjudication.py::_localization_record` 已记录 Tracer / Defense / 指控 / 采纳责任步间的冲突；`detector/result.py` 单独保存 `decision_authority` 与 `localization_authority`。
- `detector/packet.py::project_event_window` 已能提取只读局部窗口；`detector/interventions.py::_semantic_result` 存在 scoped semantic evidence-only 路径，不能把它当成允许直接定罪的通用局部模型。
- `evolution/worksheet.py::stable_localization_families`、`evolution/proposer.py::_localization_candidates`、`evolution/metrics.py::paired_localization_delta` 和 `evolution/promotion.py` 为定位修复、配对计分和正式准入提供现有基础。

### 2.2 目标数据流（拟议）

`Packet → StepRiskProfile（只读） → Evidence-backed CausalCandidates → CausalAudit（传播/受控反事实）`

- **正向分支**：`CausalAudit → run_step_tracer(causal_context=...) → 原 run_defense → adjudicate`。
- **阴性分支**：`CausalAudit 中有可核查的具体偏差 + 触发策略允许 → negative_needs_recall → run_recall_scan → run_causal_upgrade → run_hearing → adjudicate`。
- **自进化分支**：`CausalAudit + 已冻结评估差分 → worksheet / mechanisms → capability_planning / proposer → development(L0/L1) → gates / promotion → registry / memory`。

**所有新数据默认是 `evidence_only` 或 `hypothesis`；不得新增一个绕过 Defense/Adjudication 的“疑惑度直接定罪”捷径。**

## 3. 疑惑度：两个可区分的评分源

### 3.1 真正的逐步语言困惑度（可选后端）

对第 t 个 assistant 内容/动作序列的 token，在固定模型 φ 下定义：

`u_t = -(1/N_t) * Σ_j log p_φ(a[t,j] | task, historical_prefix, a[t,<j])`；`PPL_t = exp(u_t)`。

必须是对**已观察到的 Agent 步骤**进行条件似然评价，并冻结 scorer 模型、tokenizer、动作序列化方式和最大上下文。当前 `detector/provider.py::_provider_call` 仅发送兼容 Chat Completions 的生成请求，**没有**输入步骤 teacher-forced likelihood 的现成接口。生成的判定 JSON 的 logprob 不等于原始步骤 PPL。对不支持此能力的 provider 必须输出 `unsupported`，不得用 prompt 自评分冒充 PPL。

### 3.2 结构化语义疑惑度（首期实现）

首期采用可追溯的风险信号：`contract_deviation`（固定目标/参数/次序违约）、`observation_conflict`（工具观察不一致）、`unsupported_claim`（未核实事实）、`action_precondition`（前提不满足）、`propagation`（后续传播）。信号来源可复用 `detector/predicates.py`、`detector/predicate_dsl.py`、`detector/evidence.py`、`detector/packet.py` 的现有输出与新增加的因果边。

`risk_t = f(normalized_nll_t?, contract_t, observation_t, unsupported_t, dependency_t)`，其中 `f` 必须在开发集预设、记录版本，并以召回与校准实验验证。**它不是幻觉概率，不能设一个未经验证的“分数 > 0.8 就定罪”规则。**

步骤类型不同（自然语言、工具参数、纯观察）不能混用原始 PPL；工具观察本身不是 assistant 责任步骤。未评分、工具数据缺失与截断均输出显式 `unknown`，不能充作零风险。

## 4. 长轨迹候选筛查与后续步骤推演

1. **快审**：按风险排序选 Top-K，强制保留已有 Judge/Recall/Tracer 指控及其有证据的上游依赖祖先；不应只取空间上相邻的窗口。
2. **慢审**：结合任务合同、候选前缀、局部事件窗口、上游来源、真实后续步骤，核验哪一步**首次**引入材料性错误；区分首次引入、后续引用、交付暴露、已修复和独立第二错误。
3. **真实后缀追踪**：只分析轨迹中实际出现的工具观察及依赖链，可以提供“下游用到了上游错误值”的可审计关联，但这仍不等于已经证明反事实因果效应。
4. **反事实推演**：尝试最小修正候选步骤，推测有限后续动作。只有算术/合同/可重放环境的验证结果可以标为 `verified`；没有真实执行器时生成的后续行为必须标 `model_hypothesis`，不得伪造工具返回、状态改变或已验证的事实。
5. **预算失败**：Top-K 漏掉真根因、依赖链不完整、相互矛盾的外部观察、长轨迹截断等情况应返回 `inconclusive` 和原因，而不是强制改判。

在 `detector/packet.py::project_event_window` 之上增加“依赖闭包”的只读投影，不改变原 `event_###`、`source_step_id` 的含义。闭包无法成立时，不调用昂贵慢模型或明确降级，不默默吞掉更早事件。

### 4.1 两条反哺规则（不能互换）

| 初判 | 允许的审计输出 | 进入原系统的方式 | 禁止 |
|---|---|---|---|
| 阳性 | 早期错误候选、已纠正标记、冲突证据 | 引导 Tracer；将可证实的纠正/冲突证据提供 Defense 并保持其独立复核 | 仅因低/高 PPL 将阳性自动清除或改步 |
| 阴性 | 具体未核查偏差、定位线索、可信事件引用 | 经校准策略设置 `recall_forced`，仍由 Scanner/Upgrade 提案和验证 | 高 `risk_t` 直接设置 `is_hallucination=true` |
| 未决 | 可观察性/缺失/预算原因 | 记录诊断，后续获取冻结证据或重测同一版本 | 以模拟步骤冒充权威测量 |

对强一致阴性可新增 “`causal_recall_triggered`” 作为 `negative_needs_recall` 的**一个条件**，但只对有明确偏差陈述且满足预算、校准与权限限制的案例触发。避免每例无条件运行昂贵深度审计，使现有快路径失效。

### 4.2 建议的只读诊断合同

`StepRiskProfile v1`：`packet_hash`、`scorer_id/digest`、`available_modalities`、`by_event[{event_id,step_kind,risk_signals,risk_score?,score_status,evidence_refs}]`、`selection_policy_version`、`selected_event_ids`、`cost`、`health`。

`CausalAudit v1`：`packet_hash`、`candidate_event_id`、`alleged_deviation`、`grounding_kind`、`evidence_refs`、`observed_dependencies[{from,to,relation}]`、`error_role∈{introduction,propagation,delivery,corrected,unknown}`、`counterfactual{kind,status,assumption,limited_prediction}`、`claim_status∈{supported,refuted,inconclusive}`、`scope/health/cost`。

每个字段有上限、闭合枚举与引用合法性检查；没有来源的模型意见只能是建议，不得标 `supported`。**诊断对象不包含任何 gold/标注责任步**。候选的生产者、打分权重、投影规则和模型版本都需冻结 hash，以免伪造配对增益。

## 5. 真实代码改造清单（分 PR，必须接通生产入口）

| 顺序 | 改造入口 | 改造内容 | 验收 |
|---|---|---|---|
| P0 | `docs/detector-vs-agenthallu-baseline-20260927.md`、`scripts/` 中统计检查 | 修正 292 个已检出事件的偏移加总，审计历史模型版本/样本角色；固定开发数据 | 指标一致、分母可复算，旧基线不被改写 |
| P1 | 新 `detector/step_uncertainty.py`、`detector/causal_graph.py`、`detector/causal_audit.py`，现有 `packet.py` | 风险源、依赖闭包、只读 shadow 审计；假说与证据分级 | shadow 模式保持旧判定逐案完全不变；Recall@K、覆盖与成本有记录 |
| P2 | `detector/step_tracer.py::run_step_tracer`、`detector/api.py::run_hearing` | 用 `causal_context` 引导对最早因果步骤的审查；原 `Defense` 原样保留 | 定位收益独立于额外 token；多错误/纠正路径不反向伤害 |
| P3 | `detector/api.py::negative_needs_recall`、`recaller.py`、`adjudication.py` | 对阴性中的已核查异常按策略触发原 Recall/Upgrade/Defense；原裁决权限不变 | FN 改善及 FP 伤害同时记录；非触发案例与禁用新功能时旧路径不变 |
| P4 | `evolution/worksheet.py`、`mechanisms.py`、`proposer.py`、`development.py`、`memory.py`、`metrics.py` | 将候选-传播-反例机制及失败类型反馈至已有 L0/L1、正式双指标配对与审计记录 | 版本身份清晰，真实 hook 可触发，候选保护无新增伤害；新版本真实净收益可复查 |

**建议先 P1→P2 验证定位，P3 再研究检测反哺，P4 才谈有效自进化**；避免在没有改善证据前恢复大量独立但无调用方的 v2 组件。

### 5.1 具体调用与兼容性陷阱

- `Detector._detect` 在 `run_hearing(positive)` 内调用 Tracer；若加 `causal_context`，必须调整 Tracer 入参/提示与缓存身份，保留 `positive` 只针对同一指控的约束。不能在同一次冻结听证中复用**输入已改变**的旧 tracer。
- 阴性补漏插入 `recall_forced/negative_needs_recall` 时，确保当开关 `off`、证据不可用或预算耗尽时，旧逻辑不变；`run_recall_net` 只需执行一次，保留其当前 on-demand 语义。
- `Detector._detect` 的 `snapshot` 由 `validate_snapshot/touches_packet` 审核；新模块仅引用快照中的真实 E#，不能把假设检索结果变成 Evidence Snapshot；Judge 不接收独立外部证据时不能声称它核查了相应事实。
- `detector/adjudication.py` 已区分 `decision_authority` / `localization_authority`，以及 `localization_conflict`。新增证据不得绕过合法听证或自行篡改顶层 `state` / `responsible_step`；如果未来允许它参与最终采纳，需新版本的证明和冲突合同。
- `evolution/backends.py` / `runner.py` 的 `frozen_stages`、`requires_fresh_evaluation`、`detector_env_fingerprint`、`source_digest`、`cache_tag` 必须纳入 scorer、审计策略、模型与外部证据摘要。改变 live 路由不得以冻结前缀掩盖目标干预。校验缺失时 fail closed。
- 现有 `typed_semantic`、`sandboxed_component` 等 Runner 的能力/权限绑定保持不变：模型审计结果仅为证据，不能作为机械违规自动获得 terminal authority。
- 先做固定算法与 prompt 版本的研究对照。若要让进化器**学习 scorer 权重或部署新 Python 代码**，需要新的候选工件类型、执行环境、哈希、回放与准入协议；目前的 `tracer.system` 文本干预并不等同于能热更新任意代码。

## 6. 自进化反馈：从“错误摘要”变成“可检验修复操作”

现有 `worksheet.py::stable_localization_families` 区分错步，`localization_repair_viability` 避免将“标签选了另一真实错误”误当机械缺陷，`proposer.py::_localization_candidates` 能提出 `tracer.system` 文本边界；`metrics.py::paired_localization_delta` 不会以少检测阳性来伪造定位提升。应延续这套规则。

新 `CausalFailureRecord v1`（开发侧）建议记录：`failure_kind∈{missed_detection, false_positive, late_attribution, early_attribution, ambiguous_multi_error, evidence_missing, runtime_unmeasurable}`、`observed_deviation`、`earliest_supported_event_id?`、`dependent_event_ids`、`counterevidence_refs`、`mechanism_hypothesis`、`required_capability`、`normal_neighbors`、`next_action`。其中历史 gold 只用于开发侧错误分类及候选筛选，**不得传入部署端 scorer/tracer/guard 或正式验证数据的提案提示**。

调度建议：
- 缺世界事实 → 补齐同来源冻结证据，再比较双臂；
- 定位偏晚且证据支持 → 提案 `tracer.system` 首次引入/传播区分；
- 多错误或已纠正 → 先人工/规则消歧，再加入边界反例；
- provider 截断/no_quorum → **同冻结候选重测**，不推断“候选无效”；
- 只有次级 Precision 收益 → 记录独立假说，不追认原 Recall 声明；
- 提案伤害正常邻居 → 提供完整父候选和受损逐例 evidence，再尝试限定边界。

继续执行原来的 L0/L1、Structural、Harmlessness、Behavior 和 M / localization 目标门，并保持候选可追溯、零容忍规定的保护项；**准入数增加不是优化目标**。诊断、修订与重新测试用同一冻结的目标机制与预算核对。

## 7. 实验/消融、判定指标与停止规则

| 组别 | 目标 | 必须比较 |
|---|---|---|
| G0 原检测器 | 冻结基线 | Judge/Recall/Tracer/Defense 完整路径 |
| G1 等预算局部复审 | 排除“只是多花 token” | 与新方案相近的调用/延迟 |
| G2 仅风险排序+局部 Tracer | 候选发现效果 | Recall@1/3/5、Exact-Step |
| G3 G2+真实后缀依赖 | 因果传播效果 | 偏移分布、已纠正误报 |
| G4 G3+受限反事实审查 | 额外效益 | Verified vs hypothesis 分层、性能/成本 |
| G5 G4+阴性反馈 Recall | 是否真的减少漏检 | FN/FP/Authority/费用配对 delta |
| G6 G5+机制级 RSI | 是否产生跨轮增益 | 版本冻结、L1/正式可测、双指标净收益 |

在 G2 内单独比较 PPL、语义风险代理、组合与无疑惑度机械特征。统一 scorer、tokenizer、模型、样本、来源快照和预算条件。任何引入外部取证的 A/B 必须冻结相同证据；任何新增真正模拟动作的验证另列实验因素。

指标至少包括：**全部金标阳性（含漏检）的 Exact-Step 命中率**；正确检出阳性条件下的定位率；Rec@K；偏移分布；判定 Precision/Recall/Macro-F1/Accuracy；权威/未决/截断；各源与各长度段（≤5、6–10、≥11）的效果；tokens/latency P50/P90；进化候选触发、L1、可测、准入与保护损伤。**不能用缩小阳性分母提高定位率。**

实验步骤：
1. 离线契约与引用校验、合成多错误/纠正/工具失败案例；`pytest` 和旧快照兼容。
2. 先以历史暴露样本做开发与错误诊断，不把同样例当“独立泛化”；
3. 预注册 554/139 的内部比较，避免反复查看 139 的结果来调参；正式论文需要另建独立来源的新案例集；
4. 固定模型配置、实验计划、最大额外调用、Top-K、horizon、停止规则和缓存身份；先做 A/A 空干预重复测试；
5. 发现不可测、候选偏差无证据、运行健康下降时输出对应状态，重测固定候选或中止；不得把失败当 0 收益或者通过降低门限掩盖。

**第一轮落地完成定义**：有一个通过生产路径调用的 `CausalAudit`，可复现候选证据并保留旧检测；一个禁用时逐案与 G0 一致的 feature flag；一个能证明 Recall/定位实际增益或明确证明无效的冻结配对实验。没有这三项，不将设计记为“交付完成”。

## 8. 组会决策点

1. 首先使用“结构化语义疑惑度 + 真实后缀依赖”，还是同时建设本地似然评分后端？
2. 优先在 P2 做 **定位主终点**，还是提前引入 P3 阴性 Recall 反馈？建议先定位以免混淆效应。
3. 是否批准额外的人工双标注/歧义审查和新的独立轨迹采样？这是论文因果归因可信度的关键。

组会一页版见：[疑惑度反哺检测方案组会汇报](../doc/group-meetings/group-meeting-uncertainty-causal-feedback-20261008.md)。
