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
coverage 语义。三个尝试从 L2 侧"开闸"或加约束的改动均基于未完全验证的
前提，逐一被测试与日志数据证伪并回退。真正落地的修复只处理了三处纯
机械损耗（词表、空正文、判定复用）。

L1 接受率低的深层根因（为何 5 个 no_effect 案例的编辑改变的是本模块禁止
触碰的字段）指向反思路由质量，属于下一步待调查方向，本报告不臆断。
