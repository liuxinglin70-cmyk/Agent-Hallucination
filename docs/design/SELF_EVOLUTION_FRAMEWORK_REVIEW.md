# 检测器自进化框架 Review 与优化方案

## 1. Review 目标

本报告围绕一个核心目标审查当前 SEHD v6：自进化流程是否能够真实产出可执行、可继承、可复用的 detection skill，并最终改善幻觉检测效果。

审查重点如下：

1. 自进化主流程是否真正闭环，而不是只生成文本或修改 artifact。
2. L1 provider rule 与 L2 adjudication policy 是否都能改变真实检测行为。
3. 反思引擎的输入、输出和候选搜索是否适配 DeepSeek-V4-Flash / DeepSeek-V4-Pro。
4. 经验记忆是否能把 replay 结果转化为下一轮更有效的生成信息。
5. 当前测试和实验材料能证明什么，不能证明什么。

本报告不把无 gold 的 provisional L1 规则进入工作 genome、coverage-only L2 policy 进入策略 genome 视为问题。这两条探索性继承路径是允许的。审查关注的是它们能否形成真实能力，而不是继续收紧接受门槛。

## 2. 仓库结构与主流程

当前仓库的核心模块边界清晰：

| 模块 | 位置 | 职责 |
| --- | --- | --- |
| Standalone detector | `src/sehd_v6/detector/` | 单轨迹 PACA-TCL v3.5 检测入口 |
| Evolution public API | `src/sehd_v6/evolution/` | 自进化公开入口、记录加载、配置解析 |
| PACA-TCL engine | `src/sehd_v6/core/paca_tcl/` | 候选发现、控辩证据、proof court、定位 |
| BCCSE engine | `src/sehd_v6/core/bccse/` | 归因、反思、规则/策略 genome、fitness、回滚 |
| HTC-TES | `src/sehd_v6/core/htc_tes/` | 类型分类、类型经验记忆、类型遥测 |
| Experiment layer | `experiments/` | 实验计划、运行脚本、案例账本和本地结果 |

实际 L1 流程为：

```text
trajectory
  -> detector(current L1 genome + current L2 policy)
  -> self-supervised trigger
  -> type classification
  -> deterministic failure classification as a later owner supplement
  -> reflection candidates
  -> critic selection
  -> structural gate
  -> apply edit to candidate genome
  -> full detector source replay
  -> optional canary / contrast replay
  -> commit into working champion or rollback
  -> experience memory
```

实际 L2 流程为：

```text
L2-attributed detection
  -> one policy reflection call
  -> closed-vocabulary parse and validation
  -> candidate policy genome
  -> proof-court replay with the same evidence bundle
  -> observable state/coverage movement check
  -> accept into working policy genome or rollback
```

## 3. 总体判断

### 3.1 已经成立的能力

当前框架不是“生成一条规则后假设它有效”的伪进化。L1 edit 会进入真实 detector provider prompt，source replay 会用 child genome 重新执行完整检测路径：

- `skill_integration.py:367-422` 按具体 provider route 注入对应 module 的规则。
- `skill_integration.py:425-473` 在进程锁内替换真实 provider binding，退出后恢复。
- `evolution_runner.py:4969-5054` 对 candidate genome 执行真实 source replay，再计算变化。
- provider cache key 包含完整 system/user body，genome 变化会改变注入 prompt 和 request hash，不会把 baseline 响应误当作 child 响应。

L2 policy 也不是由 LLM 在运行时解释。policy item 由确定性 evaluator 执行，proof court 使用完整裁决规则重新计算终态：

- `policy_evaluator.py:82-181` 机械执行 predicate/action。
- `proof_contract.py:651-685` 在 court convergence point 应用 policy，并按完整 court 条件重算终态。
- `evolution_runner.py:2868-2977` 用 candidate policy genome replay 后再接受或回滚。

以下工程约束也基本合理：

- detector 输入递归剥离 evaluator-only 字段。
- invalid authority 和 runtime-invalid 不进入 mutation/fitness。
- edit 先作用于 candidate/shadow genome，不直接污染 working genome。
- checkpoint、genome hash、policy hash 和 replay 计数可审计。
- L1/L2 都支持 add/modify/remove，进化面具备纠错能力。
- 423 项仓库测试全部通过。

### 3.2 尚未成立的能力

当前代码证明了“框架能够执行进化 wiring”，但尚未证明“v6 能稳定产出跨案例有效 skill”。主要原因不是门槛过严，而是干预空间与失败空间没有完全对齐，反思候选搜索也会提前丢失可执行候选。

总体结论：

| 判断项 | 结论 |
| --- | --- |
| L1 edit 是否真实进入 detector | 是 |
| L1 source replay 是否真实运行 child genome | 是 |
| L2 policy 是否真实进入 proof court | 是 |
| L2 是否覆盖声明的三个失败 artifact | 否，目前主要能处理 coverage validity |
| 两层能力是否能统一跨轮继承和独立部署 | 否，L2 继承和 detector API 未闭合 |
| 反思候选是否充分利用结构校验和 replay 预算 | 否，critic 过早裁剪候选 |
| 经验记忆是否能准确指导下一次可执行干预 | 部分成立，但索引和反馈信息不足 |
| 当前证据是否证明检测效果提升 | 否，只有历史摘要，没有当前 v6 可复核结果 artifact |

## 4. 问题清单

### P0-1：L2 声明的失败空间大于实际可执行动作空间

这是当前最直接阻碍 L2 产出有效 skill 的问题。

L2 声明拥有三个 artifact：

- `consensus_gate_policy`
- `coverage_validity_policy`
- `boundary_consumption_policy`

L2 prompt 从 `L2_ACTIONS` 暴露五种 action，见 `v6_contracts.py:208-223` 和 `policy_reflection.py:68-85`：

- `downgrade_coverage`
- `invalidate_coverage_claim`
- `reclassify_candidate_state`
- `require_route_replay`
- `emit_telemetry_only`

但实际 evolution runner 在 replay 前拒绝全部 engine-level action：

```text
reclassify_candidate_state
require_route_replay
```

对应代码为 `evolution_runner.py:2842-2849`。

剩余 action 的实际能力如下：

| Action | 能否改变 detector | 适用范围 |
| --- | --- | --- |
| `downgrade_coverage` | 能 | 空壳 coverage 或错误 complete 声明 |
| `invalidate_coverage_claim` | 能 | 空壳 coverage 或错误 complete 声明 |
| `emit_telemetry_only` | 不能 | replay 必然 no-effect |

这意味着：

- `coverage_validity_policy` 有真实可执行修复面。
- `consensus_gate_policy` 通常没有能解除阻断或重建证据的 action。降低 coverage 往往只会保持 `unresolved`。
- `boundary_consumption_policy` 没有“消费 boundary finding”或等价动作。降低 coverage 也不能把已存在的 `verified_clean` boundary 正确送入 terminal edge。
- prompt 会诱导模型提出 runner 明确不支持的 action，浪费唯一一次 L2 reflection call。

因此 L2 闭环在代码结构上存在，但还不是覆盖三个 artifact 的完整闭环。

### P0-2：L2 学到的能力不能自然进入下一轮新案例或独立 detector

L1 和 L2 的继承边界不对称：

- public evolution API 支持 `initial_genome`，见 `evolution/runner.py:131-132`、`235`。
- public evolution API 没有 `initial_policy_genome`。
- L2 policy 只能通过绑定原 `evolution_case_ids` 的 checkpoint resume 恢复，见 `evolution_runner.py:1881-1890`、`2262`。
- standalone detector 的 `detect_record()` 只接受 L1 `genome`，不接受 policy genome，见 `paca_tcl/detector.py:148-169`。
- CLI 也只有 `--genome`，没有 policy 输入，见 `paca_tcl/detector.py:224-250`。

运行结束虽然写出 `policy_genome.json`，见 `evolution_runner.py:5574-5578`，但这个 artifact 没有对应的公共加载和执行路径。

结果是：L2 policy 可以在当前 `run_online_evolution()` 进程内影响后续案例，但不能像 L1 skill 一样作为下一轮全新数据的初始能力，也不能通过 standalone detector 部署验证。这会截断真正的跨轮自进化。

### P0-3：确定性 failure classifier 不是实际触发与路由的单一权威

设计文档把 `failure_classifier.classify_failure()` 描述为失败层和 owner 的确定性权威，但实际流程仍由旧 `step_attributor.attribute_failure()` 先决定：

- 是否触发进化。
- primary/secondary module。
- error type。
- type classifier 的主要输入。

只有 `signal.should_evolve=True` 后，runner 才调用 `_attribute_failure()` 和 `classify_failure()`，见 `evolution_runner.py:3774-3899`。

而 `_attribute_failure()` 构造给 classifier 的 `provider_artifacts` 只覆盖：

- quarantine error/defense certificate。
- gold FN + omission 时人工构造的 missing candidate。

对应代码为 `evolution_runner.py:705-743`。

这与 `failure_classifier.py:21-37` 声明的通用 provider artifact 输入契约并不等价。当前 detector result 并没有形成完整的 provider artifact integrity projection，classifier 无法直接看到所有 missing/malformed candidate、route record、certificate field。

后果如下：

- 一部分真正的 L1 artifact failure 仍依赖旧启发式路由。
- 一部分 L2 failure 只有在旧 trigger 先触发后才有机会进入 L2。
- HTC-TES type 和旧 module map 仍在决定候选空间，确定性 classifier 只是后置补充。
- 文档宣称的“artifact-first attribution”尚未真正落到主控制流。

这不是简单的文档偏差。错误 owner 会让反思生成一个 provider 无法执行的 edit，最终表现为 `no_edit` 或 `source_no_effect`，直接降低 skill 产出率。

### P1-1：L1 skill 的“规则已注入”与“规则实际被 provider 执行”不可区分

当前 source replay 能观察：

- state 变化。
- trigger 变化。
- coverage 变化。
- candidate/certificate/quarantine 数量变化。
- responsible step 和 localization 变化。

但 detector 没有输出 rule-level execution trace，例如：

- 哪条 evolved rule 被哪个 provider 收到。
- rule condition 是否匹配。
- provider 是否按 rule 改变了哪个输出字段。
- rule 已执行但下游 court 抵消了效果，还是 provider 直接忽略了规则。

当前只能通过 `bccse_injected_modules` 和最终 observable delta 间接推断。于是 `source_no_effect` 混合了至少四种原因：

1. condition 在源案例上根本不成立。
2. condition 成立，但 provider 忽略了自然语言规则。
3. provider 改变了输出，但改变被 schema normalization/quarantine 消除。
4. provider 输出已移动，但 downstream proof court 对终态不敏感。

这四类失败需要完全不同的下一步 edit。当前 experience memory 只收到“没有 observable delta”，反思无法学习究竟该改 condition、action、module 还是 L2 policy。

### P1-2：L1 action 仍是弱约束自然语言，artifact-to-behavior 耦合不足

L1 已从整句自由文本改成 `condition/action/evidence_anchor` slots，这是正确方向。但 action 仍是自由英文，只通过关键词判断是否大致属于 module surface，见 `module_charter.py:295-370`。

这可以阻止明显越权，却不能保证 provider 具体执行：

- `require evidence`
- `populate evidence field`
- `keep coverage incomplete`
- `emit certificate`

这些动作在不同 provider schema 中对应不同字段和不同执行语义。当前 action 没有 opcode、目标字段、对象引用和 postcondition，模型生成的规则可能“语义合理但执行不明确”。历史交接材料也记录了 14 个提案中 8 个 no-effect/worse，见 `experiments/plans/HANDOFF_V6_ADJUDICATION_EVOLUTION.md:19-33`。

### P1-3：critic 在结构校验前淘汰候选，未选候选永久丢失

当前多候选流程为：

```text
生成 N 个候选
  -> 基础 envelope 去重
  -> LLM critic 选择 1 个
  -> 只对该候选做完整解析、结构 gate 和 source replay
```

对应代码为 `evolution_runner.py:4127-4189`。

问题在于 critic 看到的是尚未通过以下检查的候选：

- RuleSpec contract。
- target module action surface。
- expected effect contract。
- baseline immutability。
- capacity、semantic overlap、rule index。

如果 critic 选中的候选随后结构失败或 replay no-effect，其他候选不会进入同轮验证。一次候选扩展实际只增加了“让同一模型投票”的成本，没有充分扩大可执行搜索宽度。

这直接违背 test-time scaling 的目标。更有效的方式应是先确定性预校验所有候选，再让 critic 排序合法候选，最后在既定 replay 预算内顺序尝试。

### P1-4：反思输出协议对 `diagnosis` 的定义自相矛盾

system prompt 要求 edit object 包含 `diagnosis`，见 `reflection_engine.py:572-577`。多候选示例给出的值是：

```json
"diagnosis": {}
```

见 `reflection_engine.py:746-752`。

runner 的解析语义却是：

- `diagnosis` 缺失：允许。
- `diagnosis` 存在且为 `{}`：拒绝。
- `diagnosis` 存在时必须有 `failed_output`、`owner_module`、`existing_rule_gap`。

见 `evolution_runner.py:4385-4443`。

prompt 没有完整定义这三个字段，但 parser 会据此拒绝。DeepSeek 在 strict JSON 输出中很容易照示例返回空对象，导致本来可执行的 rule 变成 `invalid_causal_diagnosis`。

### P1-5：诊断和生成的二阶段结构只在 no-edit 后触发，且 binding 未完全执行

当前流程先让 primary reflection 同时完成：

- 诊断失败。
- owner 判断。
- 规则生成。
- expected effect 预测。

只有所有 primary candidate 都返回 no-edit，才启动 diagnosis-only call，见 `evolution_runner.py:4191-4285`。

这会产生两个问题：

1. 已生成但 owner 错误或 action 不可执行的候选不会得到 diagnosis stage 帮助。
2. targeted prompt 虽声明必须保持 diagnosis owner，但 parser 只检查输出 module 是否在整个 allowed set 内，没有强制等于 diagnosis owner，见 `reflection_engine.py:646-653` 和 `evolution_runner.py:4289-4302`。

二阶段结构已经出现，但还没有成为稳定的数据契约。

### P1-6：L2 policy reflection 没有使用与其他 mutation call 一致的 DeepSeek thinking 配置

`provider_thinking_control()` 对以下 purpose 默认关闭 DeepSeek V4 thinking：

- `mutation`
- `reflection_critic`
- `reflection_diagnosis`

见 `provider_routes.py:166-194`。

L2 使用的 purpose 是 `policy_reflection`，见 `evolution_runner.py:2823-2829`，不在集合内。

因此在未显式配置时：

- L1 reflection 默认 non-thinking。
- L2 reflection 默认可能启用 thinking。

仓库已经记录过 DeepSeek V4 把 token 消耗在 `reasoning_content` 并返回空 `content` 的问题，见 `evolution/runner.py:34-44`。L2 prompt 的输出只是一个很小的闭词表 JSON，不需要长 reasoning；当前配置反而增加空响应和截断概率。

### P1-7：反思 prompt 输入过载且包含不相关 charter

每次 L1 reflection 都注入所有八个 module charter，见 `reflection_engine.py:557-560`，即使实际 allowed module 只有一到三个。

同时输入还包括：

- 最多 6000 字符原始 trajectory evidence。
- candidates、prosecution、defense、adjudication、CRS 摘要。
- structured runtime observation。
- 当前 skill。
- accepted/rejected history。
- type guidance 和 experience summary。
- 大段全局规则说明。

DeepSeek-V4-Flash 在 non-thinking strict JSON 模式下适合完成边界清晰、schema 简洁的生成任务，不适合在一个超长 prompt 中重新判断全部架构职责。当前 prompt 同时要求“不要重新路由 owner”和“从所有 charter 中找 owner”，会增加 no_edit、错误 owner 和 schema 遗漏。

### P2-1：HTC-TES 在控制流中的作用与设计描述不一致

技术设计称：

```text
editable_modules = type_route(type) ∩ attribution_route(signal)
```

空集时应 `skip_route_conflict`，见 `docs/design/TECHNICAL_DESIGN.md:123-133`。

实际代码在空交集时回退到 attribution route，见 `type_conditioned_evolution.py:264-305`。实际行为更有利于避免 type classifier 阻塞有效 edit，但意味着 type route 不是 hard safety constraint。

同时 type classifier 本身使用多组固定权重和阈值，见 `type_classifier.py:251-257`，而其结果又大量来自 `target_module -> type` 映射，见 `type_classifier.py:463-475`。这使“type 决定 module”和“module 反推 type”存在循环。

既然实际代码已把 type 降级为 advisory，推荐明确把 HTC-TES 定位为：

- 经验组织标签。
- 检索和多样性标签。
- 实验遥测维度。

不再让它参与关键 owner 决策。owner 应完全由 failure artifact 决定。

### P2-2：经验记忆的索引不能精确区分可执行失败原因

`TypeExperienceMemory` 主要按以下字段去重：

- hallucination type。
- evolution direction。
- module。
- feature description。
- success boolean。

见 `type_aware_fitness.py:117-139`。

重复时只增加 `observation_count`，保留原 metadata。这样会把以下尝试折叠到同一条经验：

- 相同 feature 但不同 operation。
- 相同 feature 但不同 expected field。
- provider 未执行与 downstream 无影响。
- structural reject 与 source no-effect。

另外 L2 rejected history 只保存 `decision + reason`，没有保存被拒 proposal 的 predicate/action/params 语义签名，见 `evolution_runner.py:2764-2771`。下一轮 policy reflection 可能重复提出同一无效策略。

### P2-3：机械 condition 简化可能改变 skill 语义

当 slot condition conjunction 过多时，runner 会截取部分 condition 后重新验证，见 `evolution_runner.py:4337-4365`。

这种处理能提高结构通过率，但会扩大或改变规则触发范围。它可能让 source replay 产生 movement，却不再是模型原本提出的判别条件。对于最终要跨案例复用的 skill，机械改写语义比一次 schema reject 更危险。

推荐只做无语义变化的 normalization。涉及删除条件的修改应回到生成阶段，由模型在明确保留核心 discriminant 的约束下重写。

### P2-4：文档与真实执行边界存在漂移

除 HTC-TES route 外，还有以下边界描述不一致：

- `module_charter.py:265-284` 把 `abstention_rule` 描述为 final adjudication edge。
- `reflection_engine.py:171-178` 又说明它只能塑造 boundary finding，不能直接改 proof court verdict。
- `skill_integration.py:100-105` 显示它只注入 `boundary_route`，实际 final verdict 仍由确定性 court 决定。

这种漂移会诱导反思模型提出实际不可执行的 final-edge rule，也会让后续开发者继续修 prompt 而不是修真实控制面。

## 5. 反思引擎专项设计建议

## 5.1 设计原则

反思引擎不应再次判断整个 detector 是否正确。它只需要完成一个受限任务：根据已经确定的 failure artifact，提出一个能改变该 artifact 输出的可执行干预。

建议把反思拆成三个明确对象：

```text
FailureObservation
  -> InterventionCandidate[]
  -> ReplayOutcome[]
```

正确性仍由 gold 或独立验证集评估；反思只负责提高可执行 skill 的产生率。

## 5.2 建议输入：FailureObservation

```json
{
  "layer": "l1_provider_rule | l2_adjudication_policy",
  "artifact": "owner module or policy artifact",
  "reason_code": "closed vocabulary reason",
  "source_evidence_refs": ["addressable event/certificate ids"],
  "observed_output": {
    "field": "field name",
    "value": "current value",
    "problem": "missing | malformed | hollow | blocked | unconsumed"
  },
  "executable_surface": {
    "route": "provider route or proof-court hook",
    "owned_fields": ["closed field names"],
    "legal_actions": ["closed action names"]
  },
  "current_artifact": {
    "relevant_rules_or_policy_items": []
  },
  "execution_history": {
    "similar_successes": [],
    "similar_failures": []
  }
}
```

输入约束：

- owner 由 deterministic planner 提供，不再让反思模型重新选择 layer。
- 只注入 owner 对应 charter，不注入八个 charter。
- 只注入与 failure field 有关的 trajectory/certificate evidence slice。
- 原始轨迹仍标记为 untrusted evidence。
- 不输入 gold、case identity 或最终答案解释。
- 不重复输入同一事实的自由文本、CRS 和 structured observation 三种版本。

## 5.3 建议输出：InterventionCandidate

L1 建议从“自由 action 文本”升级为“自然语言 condition + 封闭 action opcode”：

```json
{
  "operation": "add_rule | modify_rule | remove_rule",
  "target_module": "evidence_obligation",
  "rule_index": null,
  "condition": "one trajectory-observable discriminator",
  "action": {
    "opcode": "require_evidence_field",
    "object": "error_certificate",
    "field": "impact_evidence_refs",
    "on_missing": "keep_incomplete"
  },
  "evidence_anchor": "failure signature",
  "expected_delta": {
    "field": "causal_field_coverage",
    "direction": "increase"
  },
  "rationale": "short causal explanation"
}
```

L2 继续使用完全封闭结构，但只向模型暴露 runner 当前能够 replay 的 action：

```json
{
  "operation": "add_item | modify_item | remove_item",
  "item_index": null,
  "precondition": "closed predicate",
  "action": "closed executable action",
  "params": {},
  "expected_delta": {
    "field": "terminal_state | effective_coverage | route_output",
    "direction": "shift"
  }
}
```

`diagnosis` 不再作为可选且未定义的嵌套对象。诊断事实属于 `FailureObservation`，候选只表达干预。

## 5.4 建议候选搜索流程

```text
1. Deterministic planner 固定 layer、artifact、owner、legal actions
2. Flash 生成多个 intervention candidates
3. 对全部候选做 deterministic schema/ownership/capacity/duplicate 预校验
4. 丢弃非法候选，但保留每个 reject reason
5. critic 只对合法候选排序，不做最终淘汰
6. 按排序在既有 replay budget 内顺序尝试
7. 第一个产生目标 observable movement 且无已知 regression 的候选进入工作 genome
8. 所有候选和 replay outcome 写入 experience memory
```

这不是放宽门槛。它只是把 test-time scaling 的计算真正用于搜索可执行 skill，而不是在验证前让同一个模型丢掉候选。

## 5.5 DeepSeek 模型适配

### DeepSeek-V4-Flash

适合：

- 有明确 owner 和 legal action 的 JSON candidate generation。
- 封闭词表 L2 candidate 排序。
- 根据结构化 replay feedback 做一次定向重写。

不适合：

- 同时阅读八个 module charter 并重新推导架构 owner。
- 在超长原始 trace 上完成诊断、路由、生成、critic 和 correctness 判断。
- thinking mode 下输出很小的 strict JSON。

建议：

- mutation、diagnosis、critic、policy reflection 统一显式使用 non-thinking。
- 每次只给一个 artifact 的 compact observation。
- 一个 prompt 只定义一个 JSON schema，不保留 legacy schema 描述。
- system/user 全部使用同一种主语言，规则 slot 继续使用英文。

### DeepSeek-V4-Pro

适合：

- Flash 已经产生 `no_edit` 或连续产生可执行 no-effect 时，对因果 discriminant 做更深入诊断。
- 对 source case、一个相似支持案例和一个反例进行对比，形成可泛化 condition。

建议只在明确的失败类别触发 Pro，而不是让所有案例默认使用更昂贵模型。触发依据应是 categorical outcome，例如 `all_candidates_invalid`、`all_replays_no_effect`，而不是调一个连续分数阈值。

## 6. 拟采用的目标架构

### 6.1 统一 DetectorGenome

把 L1 和 L2 作为一个可部署能力包：

```json
{
  "schema_version": "sehd.detector_genome.v1",
  "provider_rule_genome": {},
  "adjudication_policy_genome": {},
  "content_hash": "...",
  "lineage": {}
}
```

统一后：

- evolution 输入使用 `initial_detector_genome`。
- detector 输入使用 `detector_genome`。
- source/canary/probe/standalone detector 都运行完全相同的两层能力。
- 下一轮新案例可以继承 L1+L2，而不是只继承 L1。
- artifact hash 能表示完整 detector behavior，不再需要分别追踪两个未绑定 identity。

### 6.2 Artifact-first EvolutionPlanner

把当前控制流调整为：

```text
authoritative detection result
  -> build complete provider artifact integrity projection
  -> deterministic classify_failure
  -> if actionable: produce FailureObservation
  -> if L1: reflect on one owner surface
  -> if L2: enumerate/rank legal policy interventions
  -> if no_actionable: telemetry only
```

旧 `step_attributor` 可以继续生成 trigger diagnostics 和 fitness telemetry，但不再拥有 layer/module routing authority。

### 6.3 完成 L2 artifact-to-action 矩阵

建议明确实现以下对应关系：

| Failure artifact | 可执行 intervention |
| --- | --- |
| `coverage_validity_policy` | downgrade/invalidate hollow coverage claim |
| `boundary_consumption_policy` | deterministic consume/reconcile boundary finding under closed preconditions |
| `consensus_gate_policy` | execute route replay or deterministic reconsideration based on addressable initial/appeal evidence |

其中 `require_route_replay` 已存在于 vocabulary，但 runner 明确拒绝。优先选择以下一种最终语义并完整实现，不保留“prompt 可见但 runtime 不可执行”的半状态：

1. 真正实现 route replay，重新获得可验证证据。
2. 从 vocabulary 和 prompt 删除该 action，直到 runtime 支持。

对 closed-vocabulary L2，不建议让 LLM自由创造 predicate/action 组合。空间很小，可以由 deterministic planner 枚举所有与 artifact 匹配的 legal edits，LLM 只负责排序或返回 no-proposal。这样能从根本上消除无效 action 和漏字段。

### 6.4 增加 rule execution trace

每个 evolved L1 rule 应有稳定 `rule_id`。对应 provider 输出增加审计字段：

```json
{
  "rule_applications": [
    {
      "rule_id": "...",
      "condition_matched": true,
      "action_applied": true,
      "affected_fields": ["impact_evidence_refs"]
    }
  ]
}
```

这个字段只用于 evolution telemetry，不参与 proof court verdict。

有了该 trace，no-effect 可以确定性拆分为：

- `condition_not_matched`
- `provider_did_not_apply_action`
- `output_changed_then_quarantined`
- `provider_output_changed_but_court_invariant`
- `expected_delta_observed`

下一轮反思才能知道该改规则的哪一部分。

### 6.5 重构经验记忆索引

经验检索主键建议调整为：

```text
layer + artifact + reason_code + target_module + feature_signature
```

HTC-TES hallucination type 作为辅助标签保留，不再作为首要索引。

每条经验保存：

- 完整 intervention semantic signature。
- operation/action opcode。
- structural result。
- rule application result。
- observable deltas。
- downstream court effect。
- accepted/rejected/provisional 状态。
- observation count。

重复经验应合并计数，但必须更新 outcome 分布，不能只保留第一次 metadata。

### 6.6 用对比案例生成可泛化 condition

当前反思主要基于一个 source case。建议在已有检测结果中检索：

- 一个相同 artifact/reason 的近邻案例。
- 一个 owner 相同但不应触发该 action 的稳定反例。

反思输入不需要完整复制两个轨迹，只提供对比 feature 和 addressable evidence slice。模型任务变成：找出 source/positive 共有、negative 不具备的 trajectory-observable discriminator。

这比要求模型在 rationale 中声明“generalizable”更能产生真正可复用 skill。

## 7. 实施优先级

### 第一阶段：打通能力闭环

1. 统一 L1/L2 persistence、load 和 detector execution boundary。
2. 让 public evolution 可以从完整两层 genome 开始新一轮。
3. 让 standalone detector 可以显式加载完整两层 genome。
4. 对齐 policy prompt 与 runtime executable actions。
5. 为 boundary consumption 和 consensus gate 提供真实可执行 intervention，或暂时从 actionable artifact 中移除不能修复的项。

第一阶段完成后，L2 才是可以跨轮积累的真实 skill surface。

### 第二阶段：提高 skill 产出率

1. 建立完整 provider artifact integrity projection。
2. 让 deterministic failure classifier 成为 layer/artifact/owner 的单一权威。
3. L1 action 改为 semi-structured opcode。
4. 候选先全部预校验，再 critic 排序，再按预算 replay。
5. 统一 diagnosis/intervention schema，删除矛盾的可选字段。
6. 增加 rule execution trace。

### 第三阶段：提高 skill 泛化能力

1. 经验记忆按 artifact/reason/intervention signature 检索。
2. 引入支持案例与稳定反例的对比生成。
3. 用 Pro 处理 Flash 的 categorical no-edit/no-effect failure，不默认扩大所有调用成本。
4. HTC-TES 固定为经验标签和分析维度，不再进入 owner critical path。

### 第四阶段：清理单一事实来源

1. 统一 `abstention_rule` 的真实执行职责描述。
2. 统一 type route 的 advisory 定位。
3. 删除 prompt 可见但 runtime 不支持的 L2 action。
4. 删除被新 `FailureObservation -> InterventionCandidate` 契约取代的旧诊断/owner 重复逻辑。

## 8. 验证方案

验证重点不是继续调整阈值，而是观察自进化漏斗中每一步是否真实产生能力。

### 8.1 必须记录的产出指标

| 指标 | 回答的问题 |
| --- | --- |
| actionable attribution count | 触发案例中有多少落到真实可编辑 artifact |
| valid candidate count | 模型生成是否满足可执行 schema |
| executable candidate count | 候选 action 是否被 runtime 支持 |
| rule condition matched count | L1 rule 是否在 source/后续案例真实触发 |
| provider action applied count | provider 是否执行了 evolved action |
| observable movement count | edit 是否真实移动 owned output |
| court movement count | output movement是否传到终态或有效 coverage |
| inherited reuse count | 已接受 skill 是否在后续非 source 案例再次触发并产生预期效果 |
| no-effect reason distribution | 产出失败发生在 condition、provider、schema 还是 court |
| validation transition table | 独立验证集上 baseline -> evolved 的正确/错误迁移 |

### 8.2 建议实验顺序

1. Contract wiring：分别选择 L1 artifact、coverage validity、boundary consumption、consensus gate 形态，确认每类都有真实 executable candidate。
2. Fresh small run：使用账本外全新案例，保存完整 `evolution_result.json`、完整 detector genome 和 replay artifacts。
3. Cross-run inheritance：把第一轮完整两层 genome 加载到另一批全新案例，证明 L2 与 L1 都被继承。
4. Reflection ablation：比较当前 critic-first 与“prevalidate-all + ranked replay”的候选有效率和 observable movement。
5. Control-surface ablation：比较 free-text action 与 structured opcode 的 rule application 和 no-effect 分布。
6. Independent validation：冻结完整两层 genome，在独立 validation set 上做 baseline/evolved paired evaluation。

### 8.3 结果解释边界

- source replay movement 证明 skill 可执行，不单独证明正确。
- inherited reuse 证明 skill 不只记住 source case，不单独证明整体准确率提升。
- stable canary 证明已知行为未明显退化，不单独证明新 verdict 正确。
- 只有独立 validation 的 paired result 才能回答“幻觉检测效果是否提高”。

## 9. 测试与证据边界

本次执行：

```text
python -m pytest tests
423 passed
```

测试覆盖了：

- detector/evolution API contract。
- label firewall 和 authority fail-closed。
- L1 source replay、canary、checkpoint 和 resume。
- L2 policy parse、genome、evaluator、proof-court hook 和 rollback。
- reflection candidate、diagnosis fallback 和 mutation budget。

但大量 evolution 测试使用 mock detector/provider，通过 `genome.generation` 或人工 policy payload 直接模拟状态变化。这能证明 orchestration 正确，不能证明真实 DeepSeek provider 会遵循 evolved rule。

仓库当前没有保留可复核的 `experiments/**/evolution_result.json` 或 v6 champion run artifact。历史交接文档记录过两条能改变 source detection 的 L1 rule，但 artifact 已清理，且发生在 L2 完整闭环之前。因此当前最准确的证据结论是：

```text
框架 wiring 已通过单元测试；L1 曾有真实 source movement 记录；
当前 v6 两层框架尚未通过可复核的跨轮、跨案例、独立验证实验。
```

## 10. 最终建议

当前不应优先继续增加 gate、阈值或 prompt 特例。最优先的系统性工作是：

1. 把 L1 和 L2 合成一个可继承、可部署的 detector genome。
2. 让 failure artifact 与 executable intervention 一一对应，尤其补齐 boundary consumption 和 consensus gate。
3. 让确定性 artifact classifier 真正成为反思前的控制权威。
4. 把 L1 action 从弱自然语言升级为 provider-schema 对齐的半结构化动作。
5. 增加 rule execution trace，结束对 `no_effect` 原因的猜测。
6. 让多个候选先通过确定性预校验，再排序并利用 replay 预算搜索有效 skill。
7. 用 artifact/replay outcome 组织经验，而不是主要依赖 type/module 循环映射。

完成这些调整后，自进化流程才会从“可以运行并偶尔得到 movement”转变为“能够系统搜索、解释失败、跨轮继承并复用有效 skill”。
