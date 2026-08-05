# SEHD v6 进化框架修复汇总

本文档记录针对 `evolve_50_20260806` 实验暴露问题实际落地的修复，以及经审查后**未落地**的改动及其原因，避免后续会话在错误前提下重复推导。

## 已实施的修复

### F1: expected_direction 词表归一化（`genome.py`）

**问题**：模型在同一次反思调用里可能产出过去式 `decreased`（与
`observable_process_delta` 的 token 一致），而契约词表只认原形 `decrease`。
`evolve_50` 中 3 次 `invalid_mutation` 因 `expected_effect_contract_invalid_direction:decreased`
被纯机械地拒绝。

**修复**：新增 `normalize_expected_direction` 作为单一归一化入口，
`validate_expected_effect_contract` 与 `sanitize_expected_effect_contract`
两处校验都经过它。别名映射覆盖 `increased/increases → increase`、
`decreased/decreases → decrease` 等。非法值仍被拒。

**验证**：`decreased` 通过并归一为 `decrease`，`sideways` 仍被拒。

### F2: 空正文走既有修复路径（`paca_tcl/basic_runner.py`）

**问题**：6 次 `provider_empty_content: finish_reason=length,
reasoning_content_present=True` —— 推理耗尽输出预算、正文为空。既有修复
路径只处理截断 JSON，空正文直接抛错、不走修复。

**修复**：空正文与截断 JSON 共用同一 `JSON_RECOVERY` 修复路径，修复提示
语明确两种失败形式。失败仍 fail-closed。

### F3: condition_overload 判定单一事实来源（`rule_contract.py`）

**问题**：`condition_overload` 的正则在 `validate_rule_spec` 内联，无法被
预检等其它阶段复用。

**修复**：抽出 `condition_is_overloaded` 谓词，`validate_rule_spec` 经它
判定。这是单一事实来源重构，不改行为。

### F4: gold 监督下 task score 先于诊断守卫判定（`fitness.py`）

**问题**：`assess_source_fitness` 先无条件检查 `responsible_step_lost` 与
`required_diagnostic_signal_lost`，之后才计算 gold task score。对
`verified_error → unresolved` 这类"错误定罪退让"的纠偏，会先以
`responsible_step_lost` 拒绝——即使它把任务分从 0 提到 1。`BFCL/088`
正是因此被误拒，且与 HTC-TES 已确立的 "wrong verdict → unresolved 是
partial progress"（commit `b7bbcac`）语义分叉。

**修复**：gold 可用时先算 task score：`task_after < task_before` 直接拒，
`task_after > task_before` 直接接受为 improvement，`task_after ==
task_before` 才继续跑 `responsible_step_lost` / CRS 丢失 / module-local
检查。self-supervised（gold 为 None）路径顺序不变。

**验证**：`verified_error → unresolved`（gold=clean）判为
`local_improvement` 且不再带 `responsible_step_lost`；gold=error 的同迁移
仍判 `worse(gold_assisted_task_score_regressed)`；"verdict 不变且丢失
responsible step" 仍判 `worse(responsible_step_lost)`。

### F5: action target 必须是 provider schema 注册字段（`module_charter.py`）

**问题**：所谓"闭 opcode"只约束变异端 JSON（opcode 名 + 非空 target），
target 不校验是否落在 provider 实际 schema 上。`abstention_rule` 的
`shape_decisive_standard` 在 boundary schema 中无任何落点；`Magentic_One/072`
用 `trajectory_event_reference`、`BFCL/024` 用 `counterfactual` 这类
schema 外字段，provider 没有可执行槽位。5 个 no_effect 中 3 个（`BFCL/073`、
`OpenManus/095`、`SmolAgents/014`）都是 `abstention_rule/shape_decisive_standard`。

**修复**：
- 新增 `MODULE_ACTION_TARGETS` 注册表：module → 其注入 route 的
  provider-schema 输出字段集合，作为 target 词表唯一事实来源。
- `validate_action_surface` 拒绝注册表外 target
  （`action_target_outside_schema:<target>`）。
- 删除无 schema 落点的 `shape_decisive_standard` opcode，abstention_rule
  只剩 `emit_boundary_finding`。
- `render_action_opcode_text` 把每模块的合法 target 字段注入反思 prompt，
  引导模型产出 schema 真实的字段名。
- `counterfactual_evidence_refs` 注册为合法 target：它是对焦控方 schema 中
  `benchmark_causal_evidence_refs`（counterfactual 因果链证据）的命名，
  已接受的 champion 规则依赖它，保持已知有效的干预路径可表达。

**验证**：`require_evidence_field: impact_evidence_refs` 通过，
`require_evidence_field: trajectory_event_reference` 以
`action_target_outside_schema` 拒绝，`shape_decisive_standard` 以
`action_opcode_unowned` 拒绝。

### F6: LLM conflict gate 从 hard veto 降级为 advisory（`evolution_runner.py`）

**问题**：conflict gate 是 fail-closed 的 LLM 调用，且 prompt 自述
"complementarity 不是 conflict"，但对"在一般规则上做更具体细化"的 ADD
规则频繁误判为冲突。`evolve_50` 中 17 次 gate 评估 4 次（23.5%）在
source replay 之前被丢弃，且多为"要求 counterfactual 证据/隔离证书"这类
与基线动作方向一致的细化。

**修复**：conflict 不再硬拒。冲突结果记入
`reflection_audit["llm_conflict_gate"]`（随所有 fitness_check 落盘），
`conflict_gate_passed` 保持 False 从而继续阻断 T6 self-supervised
provisional 接受；编辑照常走 source replay，由 gold task score、canary
regression、module-local fitness 这些证据型守卫判定是否接受。

**验证**：冲突被标记（`CONFLICT_FLAGGED`）时编辑仍可被 gold fitness 接受；
T6 provisional 仍需 conflict gate pass 才能成立。

## 审查后回退的改动（未落地）

以下改动在实施后经详细审查与全量测试（438 通过）被判定为**误诊**并回退，
记录回退原因。

### R1: L2 accept 后强制回落 L1（已回退）

**原假设**：22/36 触发案例被 L2 accept 短路、占用 61% 预算，champion 与
baseline 几乎无差异。

**审查发现**：L1 路径在另外 20 个触发案例上实际运行过（retry_invalid 8 +
reject_fitness 4 + retry_fitness_no_effect 4 + reject_conflict 4），只产出
1 条接受。真正的瓶颈是 **L1 接受率低**（1/20），不是 L2 吞预算。改动会
让每个 L2 accept 后都额外消耗一次 L1 反思调用，在已由裁决层修复的案例上
浪费预算，且无法解决接受率低的根因。

**回退**：恢复 `if l2_accepted or l2_stable_source: continue` 短路。

### R2: module_local_score 对齐 charter 输出面（已回退）

**原假设**：abstention_rule / evidence_obligation / atomic_verifier_instruction
打分读取不拥有的字段，导致编辑改变输出却被判 delta=0.0。

**审查发现**：
- 5 个 no_effect 案例的观测 delta 大多在 `prosecution_certificates`
  （abstention_rule charter 禁止触碰的字段），而非该模块自己的输出面。
  `BFCL/024` 与 `SmolAgents/014` 的 `observable_deltas` 为空 —— 编辑
  确实未产生任何可观测变化，no_effect 判定正确。
- 改动破坏了 13 条守护历史正确行为的契约测试：`min()` 防单边证明、
  gold 极性方向、隔离证书防刷分（quarantine gaming）、B2 偏置修复。
- 这些测试是 B1/B2/B8/v5.4/v5.6 历次修 bug 沉淀的行为规范，改动推翻
  了它们却没有修好观察到的失败。

**回退**：`fitness.py` 恢复原版。

### R3: L2 降级需 L1 侧证据支撑（已回退）

**原假设**：coverage 降级是单调减少，接受降级而对应侧无 L1 规则会留下
无人补充的证据缺口。

**审查发现**：hollow-defense 案例中，policy 纠正的是**虚假**的完整防御
声明（coverage=complete 但 0 有效证书）。invalidate 它是修复错误而非
制造缺口。改动会阻止这类合法的裁决层纠正——首个案例（尚无 L1 进化规则）
的 policy 纠正会被误拒。覆盖率损失的真实机制（两条降级 policy 被接受且
从未被 L1 反制）应通过提升 L1 接受率解决，而非在 L2 侧加阻断。

**回退**：移除 `_unsupported_downgrade_side` 门控与其辅助函数。

## 审查结论

本轮审查的核心教训：`evolve_50` 的瓶颈是 **L1 进化接受率低**（20 次 L1
尝试仅 1 次接受），根因在反思质量与 fitness 判定，而不在 L2 预算占用或
coverage 语义。三个从 L2 侧"开闸"或加约束的改动均基于未完全验证的前提，
逐一被测试与日志数据证伪并回退。

L1 接受率低的深层根因已定位为三段契约未闭合：

1. **fitness 判定顺序**：gold task score 的计算晚于
   `responsible_step_lost` / CRS 丢失守卫，导致真实的方向改善被误拒
   （F4）。
2. **action 契约无执行落点**：target 词表不绑定 provider schema，
   `shape_decisive_standard` 等 opcode 在 provider 输出中没有对应字段，
   规则注入后无可执行动作（F5）。
3. **conflict gate 在证据前误杀**：弱 LLM 把互补细化判为冲突，把本应
   replay 验证的提案提前丢弃（F6）。
4. **owner 路由不精确**：`unresolved → abstention_rule` 占启发式主导位，
   且 error certificate 一律映射 evidence_obligation（F7/F8）。
5. **rule_trace 语义失真**：静态编译列表被当作执行证据（F9）。

前两项叠加解释了 5 个 no_effect 案例中 3 个（abstention_rule 指向无
schema 落点的动作）、以及错误 owner 编辑改变错误模块输出面的现象。

### 本轮后续修复（F7-F9，均已落地）

**F7：确定性 artifact owner 提到 allowed 路由首位 + 起诉证据缺口投影**
（`evolution_runner.py`）
- `attribution_owner` 存在时置于 `attribution_route` 首位，启发式 primary
  降为受约束 fallback；type intersection 后同样提升 artifact owner。
- 新增投影规则：unresolved + prosecution coverage incomplete + 零存活
  error certificate + 有候选 → `error_certificate missing` →
  evidence_obligation，打破 unresolved→abstention 默认。Hollow complete
  defense 保持归 L2，避免遮蔽既有 L2 路由。

**F8：error certificate 畸形/缺失拆分 owner**（`failure_classifier.py`）
- 已生成但字段畸形（malformed）的 error certificate → 
  `atomic_verifier_instruction`（证书质量面）；未生成（missing）→
  `evidence_obligation`（证据义务面）。
- 投影第 6 条 audit 失败证书标记为 malformed，保留 failed fields 作证据。

**F9：rule_trace 拆分 compiled / injected**（`skill_integration.py` +
`evolution_runner.py`）
- provider wrapper 在 meta 中记录 `bccse_injected_rule_ids`（该 route
  实际注入的规则 id）。
- 检测结果新增 `compiled_rule_trace`（genome 静态列表）与
  `injected_rule_trace`（按 provider call 聚合、去重）。no_effect 归因可
  区分"规则从未注入"与"注入但 provider 忽略"。

**F10：slot-contract 结构化拒绝重试归一**（`evolution_runner.py`）
- `_structural_rejection_retryable` / `_structural_reject_category` /
  `_structural_hard_constraint` 此前只认 `runtime_rule_contract:` 前缀，
  `rule_spec_contract:condition_overload` 这类槽位契约拒绝既不可重试也
  拿不到硬约束文本。统一两个前缀，并把
  `action_target_outside_schema` 加入可重试类别与约束词表。

### 遗留方向修复（F11-F12，均已落地）

**F11：防御侧证据缺口投影**（`evolution_runner.py`）
- 新增与起诉侧对称的投影：unresolved + defense coverage incomplete +
  零有效 defense certificate + 有候选 → `defense_certificate missing` →
  `clean_defense_boundary`。Hollow complete prosecution 保持归 L2，与
  F7 的 hollow-defense 守卫对称。

**F12：provider 执行效果分层（applied_rule_ids 闭环）**
（`skill_integration.py` + `evolution_runner.py`）
- `_compile_module_delta` 在每条注入规则文本内联稳定 rule id
  （`【module】[id] rule`），并在注入 fragment 末尾指示 provider：若实际
  应用了某条进化规则，在 JSON 顶层输出 `applied_rule_ids`。
- `make_skill_aware_provider` 把 `applied_rule_ids` 从 provider 输出中剥离
  进 meta（`bccse_applied_rule_ids`），下游 schema 校验只看到基础契约。
- 检测结果新增 `applied_rule_trace`（route → provider 自报应用的规则 id），
  与 `compiled_rule_trace` / `injected_rule_trace` 三层闭环。no_effect 归因
  现可区分：未注入 / 已注入但未应用 / 已应用但无 observable 变化。

## 遗留方向（本报告未改动）

- **provider 条件匹配的最终确认**：`applied_rule_ids` 是 provider 自报的
  best-effort 证据（fail-open，不参与判定）。当 provider 未上报时，
  `applied_rule_trace` 为空，只能回到 `injected_rule_trace` + observable
  delta 推断。若需强闭合，需要 provider 在输出中增加每个
  certificate/candidate 与所应用规则的字段级绑定（rule-level execution
  effect），目前通过 `applied_rule_ids` 提供了最小闭环。
