# SEHD v6 检测器-自进化框架全脚本逐文件审查报告

> **审查日期**: 2026-08-09
> **审查范围**: `src/sehd_v6/` 全部 30 个 Python 脚本（约 22000 行）+ 入口/配置
> **审查方法**: 逐脚本逐行静态审查 + 依赖建模 + 机制有效性分析 + 测试验证（817 测试全通过）
> **审查人**: opencode（无 subagent 分派）

---

## 0. 执行摘要

本次审查对 SEHD v6 检测器-自进化框架的全部脚本进行了逐行审查。框架由三大子系统构成：PACA-TCL v3.5 法庭式检测器、BCCSE v5 自进化引擎、HTC-TES v3 类型条件化层。**817 个测试全部通过，框架整体可正常运行并产出 skill。**

**关键结论**：

- ✅ **框架可正常运行**：入口完整、依赖闭环、fail-closed 纪律严格、产出 `champion_genome.json` + `htc_tes_experience_memory.json` + `policy_genome.json` 三类 skill 产物
- ✅ **之前审查报告（CODE_REVIEW_REPORT.md）中的多数问题已修复**：`quarantine_reason_*` 字段现已从 audit failed_checks 推导（不再恒为 0）；新动作 `require_additional_verification` 现已能降级覆盖；上诉失败已归类到独立的 `L2_ARTIFACT_APPEAL_MECHANISM`；负反馈分析已改用前缀匹配
- ⚠️ **发现 12 个待修复问题**（2 个中度有效性瓶颈 + 6 个代码质量 + 4 个机制风险），其中 `evolution_runner.py`（8169 行）的巨型函数是最大维护风险
- ⚠️ **机制有效性核心瓶颈**：自监督模式下 `directional_evidence` 只能进 shadow、不可 promotable，导致无 gold 时 L1 skill 无法获得 `strong` 保护——这是设计纪律而非 bug，但意味着**纯自监督产出只能作为候选，必须经独立验证才能部署**

**产物正确性验证**：
- `champion_genome.json`：8 模块完整、content_hash 覆盖 rule text、baseline 不可变保护生效
- `policy_genome.json`：封闭词表验证、registry 参与 content_hash、deployable/working scope 分离
- `htc_tes_experience_memory.json`：schema v2、mechanism 签名去重、meta_review 子域完整

---

## 1. 框架整体建模

### 1.1 三子系统职责

```
PACA-TCL v3.5 (检测器，冻结基线)
   │  label-free trajectory → 三态判决 + 因因证据链
   │  H1 聚焦起诉 → H2 校准上诉(共识门) → H3 反事实定位
   ▼
BCCSE v5 (自进化引擎)
   │  detect → attribute → reflect → gate → fitness → commit/rollback → experience
   │  L1: 8 模块自然语言规则基因组 (add/modify/remove_rule)
   │  L2: 封闭词表裁决策略基因组 (add/modify/remove_item)
   ▼
HTC-TES v3 (类型条件化层)
      Macro: 8 类型路由 → Meso: 类型感知 fitness → Micro: 类型经验记忆
```

### 1.2 数据流闭环

```
raw trajectory
  → ocse_packet.build_packet (标签防火墙)
  → proof_engine.adjudicate_case (H1/H2/H3 三阶段)
  → evolution_contract.project_for_evolution (标签无关投影)
  → step_attributor.attribute_failure (自监督触发 + 模块归因)
  → failure_classifier.classify_failure (确定性 L1/L2 分层)
  → reflection_engine.build_causal_reflection_prompt (LLM 反思)
  → safety_gate.evaluate (结构约束门)
  → fitness.assess_source_fitness (源重放 + canary)
  → genome.apply_edit (shadow 提交)
  → flush_shadow_batch (champion 提交 + probe 回滚)
  → experience_memory (HTC-TES 记忆持久化)
```

### 1.3 完整依赖图（脚本级）

```
sehd_v6/__init__.py (空)
  ├── env_loader.py ── load_local_env (.env 加载，进程环境优先)
  ├── provider_routes.py ── resolve_provider_route (route>purpose>global>config>default)
  ├── configs/paca_tcl_detector_v3_5.json (10 个 route 模板)
  ├── utils/
  │   ├── ocse_packet.py ── build_packet/validate_packet (标签防火墙 + 稳定 ID)
  │   └── gold_label.py ── coerce_gold_label (bool/str/int → bool|None)
  ├── core/
  │   ├── provider_models.py ── canonical_model_name (DeepSeek-V4 别名)
  │   ├── provider_usage.py ── ProviderUsageLedger (逻辑调用审计)
  │   ├── paca_tcl/ (检测器)
  │   │   ├── detector.py ── detect_record/load_detector_config/load_skill_genome ★入口
  │   │   ├── basic_runner.py ── provider_call/provider_call_with_schema (传输+缓存+修复)
  │   │   ├── layered_engine.py ── v2 基线层 (qualification/propagation/boundary)
  │   │   ├── proof_engine.py ── adjudicate_case (H1/H2/H3 主编排, 2110 行) ★核心
  │   │   ├── proof_contract.py ── adjudicate_proof_court (法庭裁决契约, 935 行)
  │   │   ├── policy_evaluator.py ── evaluate_policies/evaluate_pre_ledger_policies (L2 确定性求值)
  │   │   ├── skill_integration.py ── integrated_v35_provider (genome→prompt 注入上下文)
  │   │   ├── evolution_contract.py ── project_for_evolution (标签无关投影)
  │   │   ├── proof_engine_v3_4.py ── v3.4 纯辅助函数 (提示词/合并/构造普查)
  │   │   ├── proof_contract_v3_4.py ── v3.4 契约常量/校验器
  │   │   ├── proof_contract_v3.py ── validate_error_certificate/_address_space
  │   │   ├── contract_v1.py ── v1 候选普查契约 (AXES/COVERAGE_STATES)
  │   │   ├── layered_contract.py ── v2 候选路径合并
  │   │   └── __init__.py (lazy exports)
  │   ├── bccse/ (自进化引擎)
  │   │   ├── evolution_runner.py ── run_online_evolution (主循环, 8169 行) ★★★核心
  │   │   ├── genome.py ── SkillGenome/SkillEdit/apply_edit/validate_edit (973 行)
  │   │   ├── step_attributor.py ── attribute_failure (自监督归因, 1467 行)
  │   │   ├── failure_classifier.py ── classify_failure (确定性 L1/L2 分类, 360 行)
  │   │   ├── reflection_engine.py ── build_causal_reflection_prompt (LLM 反思, 1400 行)
  │   │   ├── fitness.py ── assess_source_fitness/module_local_score (665 行)
  │   │   ├── safety_gate.py ── StructuralGate.evaluate (结构约束门, 666 行)
  │   │   ├── policy_genome.py ── PolicyGenome/PolicyEdit (L2 基因组, 551 行)
  │   │   ├── policy_reflection.py ── plan_policy_candidates/select_policy_candidate (L2 反思)
  │   │   ├── vocabulary_registry.py ── L2_REGISTRY/compile_predicate_dsl (词表权威, 1067 行)
  │   │   ├── v6_contracts.py ── build_adjudication_facts/PolicyItem/FailureAttribution
  │   │   ├── vocabulary.py ── MODULE_IDS/semantic_overlap/validate_feature_description
  │   │   ├── rule_contract.py ── RuleSpec/render_rule/validate_rule_spec (槽契约)
  │   │   ├── module_charter.py ── MODULE_CHARTERS/MODULE_ACTION_OPCODES (模块边界)
  │   │   ├── action_spec.py ── ActionSpec/compile_action_spec (可执行干预规范)
  │   │   ├── failure_episode.py ── FailureEpisode/build_failure_episode (因果切片, 1226 行)
  │   │   ├── evolution_experience_memory.py ── EvolutionExperienceMemory (机制签名索引)
  │   │   ├── signal_adapter.py ── resolve_authoritative_state/extract_crs_signal (CRS 投影)
  │   │   ├── meta_skill.py ── MetaSkillState/MetaEdit (Meta 闭环状态)
  │   │   ├── meta_review.py ── generate_meta_edit_candidates/commit_validated_meta_edits
  │   │   ├── gate_feedback.py ── GateEvent/GateEventLedger (审计门事件)
  │   │   ├── promotion_contract.py ── promote/PromotionDecision (统一晋升契约)
  │   │   ├── promotion_gates.py ── G1_CHECKS/MetricSnapshot/ReplayEvidence
  │   │   ├── registry_reflection.py ── plan_registry_candidates (词表注册规划)
  │   │   ├── registry_promotion.py ── enqueue_registry_validation/promote_pending_registry_entries
  │   │   ├── experience_retriever.py ── retrieve_structured_experience (结构化经验检索)
  │   │   ├── negative_feedback_analyzer.py ── analyze_negative_feedback (负反馈根因)
  │   │   ├── cesv_contract.py ── ProvenanceGraph (CESV 辅助因果图)
  │   │   ├── types.py ── EvaluationOutcome/ExperienceRecord/ReplayEpisode
  │   │   └── __init__.py (lazy exports, bccse↔htc_tes 解环)
  │   └── htc_tes/ (类型条件化)
  │       ├── type_classifier.py ── HallucinationTypeClassifier.classify (8 类型分层)
  │       ├── type_conditioned_evolution.py ── TypeConditionedEvolution (类型路由)
  │       ├── type_aware_fitness.py ── TypeAwareFitness/TypeExperienceMemory (类型感知 fitness)
  │       └── __init__.py
  ├── detector/ (独立检测器包)
  │   ├── __init__.py ── detect_record/project_for_evolution/run_batch/run_replicated_batch
  │   ├── __main__.py ── CLI 入口
  │   ├── batch.py ── load_batch_records/run_batch (并发可恢复)
  │   ├── replica_aggregation.py ── aggregate_replicas (测试时扩展聚合)
  │   └── README.md
  └── evolution/ (独立进化包)
      ├── __init__.py ── run_evolution/run_online_evolution/load_records
      ├── __main__.py ── CLI 入口
      ├── runner.py ── run_evolution (run_online_evolution 的参数适配壳)
      ├── state_manifest.py ── write_state_manifest/load_state_manifest (跨运行继承)
      └── README.md
```

---

## 2. 逐脚本审查

### 2.1 顶层与基础设施

#### `src/sehd_v6/__init__.py` (0 行)
空文件，作为包标记。无问题。

#### `src/sehd_v6/env_loader.py` (72 行)
**职责**: 无依赖的 `.env` 加载器，进程环境优先，不打印值。
**审查**:
- L67 `os.environ.setdefault(key, _unquote(value))` — 正确，进程环境优先
- L60-65 行内注释剥离仅对未加引号值生效 — 正确（加引号值保留 `#`）
- L71 `loaded = candidate` 即使无赋值也记录路径 — 注释说明正确（继续向上扫描）
**结论**: ✅ 无问题。

#### `src/sehd_v6/provider_routes.py` (240 行)
**职责**: 中心化 provider route 解析；凭据值永不进入 route。
**审查**:
- L113-145 endpoint/model 默认填充 + `api_key_environment` 多分支回退到官方默认 — 逻辑复杂但正确，每个分支都有明确语义
- L176-212 `provider_thinking_control` 对 mutation/reflection_critic/policy_reflection 等 purpose 默认 disabled — 正确（避免 reasoning_content 耗尽输出预算）
- L215-239 `resolve_config_provider_routes` 标记 `_provider_route_resolved=True` 防止二次解析 — 正确
**结论**: ✅ 无问题。凭据隔离纪律严格。

#### `src/sehd_v6/configs/paca_tcl_detector_v3_5.json` (61 行)
**职责**: 检测器配置模板，10 个 route。
**审查**:
- `outer_opened: false` — 正确（单轨迹模式）
- `appeal_*_route` 设置 `thinking: disabled` + `max_tokens: 8192` — 正确（上诉需确定性）
- `boundary_route` 也设 `thinking: disabled` — 正确
- 无凭据值 — 正确
**结论**: ✅ 无问题。

#### `src/sehd_v6/utils/ocse_packet.py` (192 行)
**职责**: 规范化原始轨迹包，标签防火墙。
**审查**:
- L20-30 `EVALUATOR_ONLY_FIELDS` 冻结集 — 单一真相源
- L49-66 `_forbidden_key_hits` 只审计 schema 层，不审计嵌套 tool payload — 正确（tool 参数是任务证据）
- L105-146 `build_packet` ordinal 作为主键，`source_step_id` 仅作溯源 — 正确（重复/缺失 step 不腐败证据引用）
- L149-192 `validate_packet` 6 项校验 — 严格
**结论**: ✅ 无问题。标签隔离扎实。

#### `src/sehd_v6/utils/gold_label.py` (40 行)
**职责**: gold 标签强制的单一真相源。
**审查**:
- L14-15 `_TRUE_TOKENS`/`_FALSE_TOKENS` 封闭 — 正确
- L35-39 int/float 只接受精确 1/0 — 正确（避免 2 被当作 True）
- 无法识别返回 None（fail-closed）— 正确
**结论**: ✅ 无问题。

#### `src/sehd_v6/core/__init__.py` (43 行)
**职责**: lazy 公共 facade，按需导入避免 bccse/htc_tes 循环依赖。
**审查**: `__getattr__` 模式正确，`globals()[name] = value` 缓存。✅ 无问题。

#### `src/sehd_v6/core/provider_models.py` (20 行)
**职责**: provider 模型别名规范化。
**审查**: 仅 DeepSeek-V4-Flash/Pro 注册，其他 raise。✅ 无问题。

#### `src/sehd_v6/core/provider_usage.py` (229 行)
**职责**: provider 调用审计账本。
**审查**:
- L98 `cache_hit=value.get("cache_hit") is True` — 正确（避免字符串 "false" 被当作 True）
- L158-160 cache_hit 时 transport_attempts=0 — 正确（缓存命中不计传输）
- L174-206 `totals()` 逻辑调用与传输调用分离统计 — 正确
**结论**: ✅ 无问题。

---

### 2.2 PACA-TCL 检测器引擎 (`core/paca_tcl/`)

#### `__init__.py` (37 行)
lazy exports。✅ 无问题。

#### `detector.py` (375 行) ★入口
**职责**: 独立检测器入口，无 BCCSE 依赖。
**审查**:
- L42-61 `_forbidden_field_hits` 递归审计输出侧 — 正确（输出永不携带评估字段）
- L64-96 `_forbidden_input_hits` 仅审计 schema 层（镜像 OCSE 防火墙）— 正确
- L109-153 `resolve_detector_config` schema 校验 + route 必填 + 数值正性 — 严格
- L163-184 `load_skill_genome` 显式加载、fail-closed、hash 校验 — 正确
- L187-217 `load_policy_genome` 同样 fail-closed + 词表校验 — 正确
- L220-305 `detect_record` genome 注入用 `integrated_v35_provider` 上下文管理器；无效运行 fail-closed 重置所有判决字段 — 正确
- L297-304 provenance 记录 genome_hash — 正确（可审计）
**结论**: ✅ 无问题。fail-closed 纪律严格。

#### `basic_runner.py` (957 行) ★传输层
**职责**: provider 传输、缓存、census 助手。
**审查**:
- L41 `DIRECT_PROVIDER_OPENER` 绕过系统代理 — 正确（直连端点）
- L64-78 `_global_cache_root` opt-in，`off/disabled/none` 显式禁用 — 正确
- L100-184 `_provider_repair_call` 单次有界修复，cache key 不同 — 正确
- L359-628 `provider_call_with_schema` 核心传输：
  - L395-411 payload 对齐 route（temperature/max_tokens 不再硬编码）— 正确（修复了注释所述的旧 bug）
  - L405-406 DeepSeek-V4 全族不发送 `response_format` — 正确（autodl 网关拒绝）
  - L442-462 全局缓存查找 — 正确
  - L466-481 `total_timeout` 默认 `per_attempt*(retries+1)` — 正确（修复了 5x1 并发超时 bug）
  - L519-554 JSON 解析失败 → 单次有界修复 — 正确
  - L588-604 `ValueError` 立即失败不重试（确定性失败不重传）— 正确
  - L605-626 仅可重试 HTTP 错误码重试 + 429 Retry-After + jitter — 正确
- L657-736 `validate_provider_census` origin 可为 interaction（映射回 event）；最多 8 候选 — 正确
- L861-957 `evaluate` FN 机制分析 — 诊断性强
**结论**: ✅ 无问题。传输层健壮，缓存隔离正确。

#### `layered_engine.py` (221 行)
**职责**: v2 基线层（qualification/propagation/boundary）。
**审查**:
- L27-34 三个系统提示词严格限定各层职责 — 正确
- L61-84 `_validate_boundary` 6 项校验 — 严格
- L94-171 `adjudicate_case` census fallback → qualification → propagation → boundary → merge — 流程清晰
- L174-221 `evaluate` 标准混淆矩阵 + macro-F1
**结论**: ✅ 无问题。

#### `proof_engine.py` (2110 行) ★★★核心检测
**职责**: PACA-TCL v3.5 主编排（H1 聚焦起诉 + H2 校准上诉 + H3 反事实定位）。
**审查**:
- L43-180 系统提示词完整定义因果链脚手架（6 阶段）— 正确
- L238-365 `_run_focused_prosecution_for_candidate` 每候选并行 + 迭代精炼 + quarantine 反馈 — 正确
- L368-461 `_run_focused_prosecution` 并行编排 + 覆盖状态判定（LLM-empty vs LLM-crash 区分）— 正确（v3.5.1 修复）
- L468-551 `_appeal_trigger_calibrator_v35` 仅 priority 0-1 触发 — 正确（降低触发率 ~83%→~40%）
- L598-634 `_classify_appeal_prosecution_failure` 稳定 case-agnostic 类别 — 正确（无 prompt poison）
- L705-752 `_validate_appeal_prosecution_contract_or_retry` 单次有界重试 — 正确
- L877-922 `_validate_appeal_defense_contract_or_retry` 对称的 defender 重试 — 正确（v3.5.4 修复 trial_5 不对称）
- L925-1100 `_deep_validate_appeal_defense` + `_validate_appeal_defense_deep_or_repair` 证书级深度修复 + 合并保留有效行 — 正确
- L1173-1206 `_run_counterfactual_localization` 仅 ≥2 不同 origin 时触发，用 schema-relaxed 调用 — 正确
- L1314-1351 `_resolve_incomplete_appeal` 保留完整初始正性，否则 fail-closed unresolved — 正确
- L1354-1498 `_run_appeal_phase` 4 个 provider 调用链 + fail-closed — 正确
- L1505-1795 `adjudicate_case` 主流程：
  - L1514-1526 Phase 1 并发 base + construct census — 正确
  - L1556-1566 Phase 2 并发 focused prosecution + defense — 正确
  - L1609-1633 H2 触发 + appeal — 正确
  - L1634-1670 H3 反事实定位 — 正确
  - L1672-1722 L2 策略 passthrough（pre-ledger/post-ledger 分流）— 正确
  - L1738-1748 runtime_valid 多条件 — 正确
- L1871-2005 `_build_adjudication_output` 展平 + evidence_health 诊断 — 正确
**结论**: ✅ 无问题。H1/H2/H3 三阶段编排严谨，fail-closed 纪律贯穿。

**⚠️ 轻微问题 PE-1**: L2012-2110 `evaluate` 函数中 `chain_completeness_scores` 扩展可能在大批量下内存增长（无上限）。非阻塞，建议加 cap。

#### `proof_contract.py` (935 行)
**职责**: 法庭裁决契约（共识门 + 反事实定位 + L2 策略钩子）。
**审查**:
- L69-127 `validate_focused_prosecution` 因果链完整性评分（脚手架标记优先，关键词回退）— 正确
- L130-164 `build_quarantine_feedback` 结构化失败反馈 — 正确
- L171-220 `apply_consensus_gate` 上诉正性需初始独立支持 — 正确（防 v3.4 FP 根因）
- L227-309 `select_earliest_origin_counterfactual` root_cause 优先 → sufficiency 分数 → 时间序 tiebreak — 正确
- L316-365 `_merge_appeal_defense` 去重 + 支配规则合并 — 正确
- L372-413 `_run_deterministic_prechecks` 物质性/承诺预检查合并 — 正确
- L420-516 `_apply_consensus_gate_and_pre_ledger` P9 pre-ledger restore — 正确
- L523-776 `_resolve_terminal_state` 终态判定 + occurrence ledger + L2 策略钩子 — 正确
- L783-935 `adjudicate_proof_court` 主入口 — 正确
**结论**: ✅ 无问题。

#### `policy_evaluator.py` (408 行) ★L2 求值
**职责**: L2 策略确定性求值器。
**审查**:
- L88-277 `evaluate_policies` 按 genome 序求值，未知谓词/动作 fail-closed skip — 正确
- L158-199 `consume_verified_clean_boundary` 全部 runtime 守门 fail-closed — 正确
- L210-214 `downgrade_coverage` 用 `_lower_coverage` 单向降级 — 正确（不可升级）
- L226-241 `require_additional_verification` **现已能降级覆盖为 incomplete**（修复了旧审查报告的"仅遥测"问题）— ✅ 正确
- L242-255 `escalate_to_appeal` 仅遥测，不改终态 — 注释说明"由 court 重新计算"，设计意图明确
- L256-264 `mark_candidate_ambiguous` 仅遥测 — 同上
- L284-353 `PreLedgerPolicyFacts` + `_restore_guards` 6 项 fail-closed 守门 — 正确
- L356-408 `evaluate_pre_ledger_policies` 仅 `restore_audited_consensus` 操作符 — 正确
**结论**: ✅ 无问题。**之前审查报告的"新动作不改变状态"问题已部分修复**（require_additional_verification 现降级覆盖；escalate_to_appeal/mark_candidate_ambiguous 仍仅遥测，但这是设计选择）。

#### `skill_integration.py` (628 行) ★genome 注入
**职责**: genome → provider prompt 的确定性注入。
**审查**:
- L50-81 `PACA_MODULE_STAGE` + `ROUTE_MODULE_IDS` route 级可达性 — 正确
- L231-291 `_rule_stable_id` 基于 module + normalized condition — 正确（wording 重试保持 ID）
- L337-400 `compile_genome_for_paca` 仅编译 delta（与 baseline 差异）— 正确
- L429-577 `make_skill_aware_provider` 注入 + rule_effects 自报告 + 防注入守门 — 正确
- L580-628 `integrated_v35_provider` 上下文管理器，`_PATCH_LOCK` 序列化，finally 恢复 — 正确
**结论**: ✅ 无问题。注入隔离严格。

#### `evolution_contract.py` (145 行)
**职责**: 标签无关投影。
**审查**:
- L52-145 `project_for_evolution` 从 result 提取 CRS + diagnostics — 正确
- L75-78 focused_quarantined 从 providers 元数据推导 — 正确
**结论**: ✅ 无问题。

#### `proof_engine_v3_4.py` (344 行)、`proof_contract_v3_4.py` (575 行)、`proof_contract_v3.py` (118 行)、`contract_v1.py` (265 行)、`layered_contract.py` (263 行)
**职责**: v3.4/v3.5/v2/v1 契约常量与辅助函数。
**审查**: 常量定义、校验器、合并逻辑均正确。`proof_engine_v3_4` 仅提供纯辅助函数（提示词、合并、构造普查），无 provider_call 调用（与 skill_integration 注释一致）。✅ 无问题。

#### ~~`proof_evaluator.py` (970 行)~~ ✅ 已删除（PEV-1 已修复）
**修复**: 该遗留批评估器已被删除。其纯测试辅助逻辑（`metric_snapshot`、`experience_record_from_row`、`prediction_state`）上移重写至 `bccse/signal_adapter.py`（基于 `resolve_evolution_state` 单一权威），transport 测试统一改打 `basic_runner.json_object_provider_call`，5 个测试文件完成迁移。

---

### 2.3 BCCSE 自进化引擎 (`core/bccse/`)

#### `__init__.py` (116 行)
**审查**: lazy `__getattr__` 解 bccse↔htc_tes 循环依赖。✅ 正确。

#### `types.py` (111 行)
**审查**: 共享数据类，避免循环导入。✅ 无问题。

#### `genome.py` (974 行) ★L1 基因组
**职责**: L1 技能基因组（8 模块自然语言规则）。
**审查**:
- L40-77 MODULE_DISPLAY_NAMES/CAPACITY/STAGE 映射 — 正确
- L84-96 FITNESS_STATUS 三级 + DEDUP 阈值 — 正确
- L109-131 `sanitize_feature_description`/`validate_expected_effect`/`sanitize_expected_effect` — 正确
- L143-258 结构化 expected_effect_contract（observable_field/direction/scope/trigger）— 正确
- L271-401 `validate_runtime_rule` 严格校验：if-then 形状、多步伪代码、条件过载、诊断标记、状态泄漏、verdict 权限 — 正确
- L408-528 `SkillModule` rule_meta 并行列表 + 保护规则 — 正确
- L545-646 `SkillEdit` 序列化 sanitize — 正确
- L648-718 `SkillGenome` content_hash 仅覆盖 rule text（不含 meta）— 正确（v5.4 修复）
- L725-757 `_validate_persisted_rule_slots` 槽契约 + render 一致性 — 正确
- L759-905 `validate_edit` 全量校验（模块/操作/容量/重复/保护/baseline 不可变）— 正确
- L932-988 `apply_edit` 返回新 genome + 血缘 — 正确
- L995-1074 `create_baseline_genome` 8 模块基线规则 + is_baseline 标记 — 正确
**结论**: ✅ 无问题。L1 基因组工程严谨。

#### `vocabulary.py` (93 行)
**职责**: 共享词汇常量。
**审查**:
- L13-22 MODULE_IDS 8 模块 — 正确
- L24-29 ALLOWED_VERDICTS/LOCAL_NEGATION_MODULES — 正确
- L37-54 `_FEATURE_POISON_PATTERNS` prompt 注入防御 — 正确
- L78-93 `semantic_overlap` 字符 bigram Jaccard — 正确
**结论**: ✅ 无问题。

#### `v6_contracts.py` (382 行) ★共享契约
**职责**: 两层进化表面的共享契约。
**审查**:
- L31-51 LAYER/L2_ARTIFACT 词汇 — 正确（含 v6.1 扩展的 APPEAL_MECHANISM）
- L76-177 `build_adjudication_facts` **现已从 error_audits/defense_audits 的 failed_checks 推导 quarantine_reason_* 计数**（修复了旧审查报告的"恒为 0"问题）— ✅ 正确
- L191-195 L2_PREDICATES 从 registry 派生 — 正确（单一真相源）
- L214-229 ACTION 常量 + ENGINE_LEVEL_ACTIONS 排除 — 正确
- L236-267 `validate_policy_params` 封闭词表校验 — 正确
- L304-368 PolicyItem/FailureAttribution — 正确
**结论**: ✅ 无问题。**旧审查报告的两个核心问题（quarantine_reason 恒为 0、上诉归类错误）均已修复**。

#### `vocabulary_registry.py` (1067 行) ★词表权威
**职责**: L2 词表的版本化基线注册表。
**审查**:
- L52-97 `AdjudicationFacts` 冻结快照（含 v6.1 扩展字段）— 正确
- L251-433 `VocabularyRegistry` 不可变 + active 视图（baseline+registered）+ raw 视图（含 pending/disabled）— 正确
- L444-611 `_build_baseline_registry` 13 个基线谓词 + 10 个操作符 + 7 个可提案实例 — 正确
- L614-841 DSL 编译器：AST 白名单（仅 and/or/not/比较/字面量）+ 编译时类型检查 — 正确
- L848-933 反序列化重新编译 DSL — 正确（不信任持久化 callable）
- L951-1061 `apply_registry_edit` 不可变血缘 + before/after hash — 正确
**结论**: ✅ 无问题。DSL 编译器 fail-closed 严格。

#### `rule_contract.py` (278 行) ★槽契约
**职责**: L1 规则的槽契约存储与确定性渲染。
**审查**:
- L43-93 正则模式（条件过载、动作拆分、verdict 权限、本地否定、动作污染）— 正确
- L97-135 RuleSpec dataclass — 正确
- L138-199 `_normalize_condition`/`_render_action`/`render_rule` 确定性渲染 `When <condition>, <action>.` — 正确
- L210-267 `validate_rule_spec` 全量校验 — 正确
- L270-278 `rule_text_identity`/`rules_semantic_overlap` 去重 — 正确
**结论**: ✅ 无问题。

#### `module_charter.py` (627 行)
**职责**: 8 模块的职责边界。
**审查**: 每个 charter 声明 influenceable/forbidden surfaces + allowed_verdicts + in/out-scope 示例。从 `ALLOWED_VERDICTS` 单一源获取。✅ 无问题。

#### `action_spec.py` (467 行) ★可执行规范
**职责**: L1 干预的可执行规范（route/object/field/postcondition）。
**审查**:
- L21-54 MODULE_ROUTE_IDS/MODULE_OUTPUT_OBJECT 映射 — 正确
- L56-103 操作后置条件/模块效应/操作效应 — 正确
- L107-175 ActionSpec dataclass + effect_contract — 正确
- L174-220 `compile_action_spec`/`render_action_spec_contract` — 正确
- L223-244 `sanitize_action_spec` — 正确
- L277-467 `evaluate_action_spec_postcondition`/`classify_execution_evidence` — 正确
**结论**: ✅ 无问题。

#### `step_attributor.py` (1467 行) ★自监督归因
**职责**: 步级因果过程反思（自监督触发 + 模块归因）。
**审查**:
- L41-67 ErrorType 枚举（含 v5.5 扩展）— 正确
- L95-144 ERROR_MODULE_MAP/SECONDARY 模块映射 — 正确
- L160-222 `_compute_adversarial_equilibrium`/`_adversarially_tested` v5.4 修复（分类覆盖直接要求 complete）— 正确
- L225-321 AttributionSignal dataclass（含 diagnostic_only 守门）— 正确
- L324-399 `classify_runtime_error`/`runtime_invalid_signal` — 正确（runtime 失败永不触发进化）
- L402-434 `_check_step_correct` None 处理 — 正确
- L462-578 `_classify_appeal_failure`/`_classify_process_diagnostics` — 正确
- L618-721 `_route_process_failure`/`_route_gold_locked_owner` — 正确（gold 锁定极性，覆盖状态细化 owner）
- L724-757 `_detect_crs_contradiction` v5.3 recovery 倒置维度处理 — 正确
- L760-828 `_detect_evidence_incomplete` 对称均衡检查 — 正确
- L831-960 `_build_attribution_signal` 装配 — 正确
- L997-1093 `_resolve_module_attribution` gold 锁定路由 — 正确
- L1096-1245 `_detect_trigger_and_error_type` 级联：gold > 自监督 > 过程诊断 — 正确
- L1248-1467 `attribute_failure` 主入口 — 正确

**⚠️ 问题 SA-1（低，代码质量）**: L1421-1451 `attribute_failure` 中**重复了 `_resolve_reflection_type` 的逻辑**（在 `_build_attribution_signal` L935 已调用一次）。`reflection_type` 变量在 `attribute_failure` 计算后被传入 `_build_attribution_signal`，但后者又通过 `_resolve_reflection_type(error_type)` 重新计算。两处逻辑必须保持一致，否则产生分歧。
  - **影响**: 维护风险——修改一处忘记改另一处会导致 reflection_type 分歧
  - **建议**: 移除 `attribute_failure` 中的重复计算，让 `_build_attribution_signal` 单一负责 reflection_type 推导

**结论**: ⚠️ 问题 SA-1（reflection_type 重复计算）。归因逻辑本身正确。

#### `failure_classifier.py` (360 行) ★确定性分类
**职责**: 确定性失败分类器（L1/L2 分层）。
**审查**:
- L88-94 ARTIFACT_OWNER 映射 — 正确
- L97-111 reason codes 封闭词汇 — 正确
- L118-162 `_find_first_bad_artifact`/`_resolve_l1_module` — 正确（malformed 优先于 missing；registered module_hint 优先）
- L174-360 `classify_failure` 优先级规则：
  - Rule 1: provider artifact 完整性（最上游）— 正确
  - Rule 2: consensus gate blocked + unresolved — 正确
  - Rule 3: hollow coverage — 正确
  - Rule 4: boundary conflict — 正确
  - Rule 4.1-4.2: **appeal 归类到 `L2_ARTIFACT_APPEAL_MECHANISM`**（修复了旧审查报告的"错误归类到 CONSENSUS_GATE"问题）— ✅ 正确
  - Rule 4.3-4.4: localization — 正确（Rule 4.4 要求 `localization_status != ""` 防误触发）
  - Rule 4.5: quarantine pattern — 正确
  - Rule 5: fall-through — 正确
**结论**: ✅ 无问题。**旧审查报告的 Rule 4.1/4.2 归类问题已修复**。

#### `fitness.py` (665 行) ★适应度
**职责**: 源重放适应度评估。
**审查**:
- L36-58 `_MODULE_SCORE_GOLD_DIRECTION` v5.6 修复 evidence_obligation 方向 — 正确
- L99-110 `task_state_score` 2/1/0 排序 — 正确
- L113-127 `_coverage_rank` 从 signal_adapter 单一源获取 — 正确
- L189-257 `_causal_field_score`/`_localization_quality` — 正确（v5.6 修复"emit any step"游戏）
- L260-338 `module_local_score` 每模块专属评分 — 正确
- L341-435 `observable_process_delta` 有界可观测变化描述 — 正确
- L438-665 `assess_source_fitness`：
  - L470-471 diagnostic_only 信号永不 promotable — 正确（不变式 #4 硬化）
  - L477-498 gold task score 回归/改善 — 正确
  - L515-527 **自监督 directional_evidence 不可 promotable** — 正确（设计纪律）
  - L541-547 对称回归检查（新触发引入）— 正确
  - L561-614 gold-aware 方向检查 — 正确
  - L615-628 自监督 directional_evidence — 正确
**结论**: ✅ 无问题。**自监督 directional_evidence 的不可 promotable 是核心设计纪律**，意味着纯无 gold 产出的 L1 skill 只能作为候选。

#### `safety_gate.py` (666 行) ★结构约束门
**职责**: 结构约束门。
**审查**:
- L43-60 GateConfig 默认值（cooldown=0, max_edits=200, convergence=20）— 正确
- L67-116 GateCertificate 审计记录 — 正确
- L123-250 `StructuralGate` 撤销/延迟/快照/恢复 — 正确
- L251-666 `evaluate` 多层约束（halt/操作/槽契约/容量/重复/保护/baseline）— 正确
**结论**: ✅ 无问题。

#### `policy_genome.py` (551 行) ★L2 基因组
**职责**: L2 裁决策略基因组。
**审查**:
- L27-58 FITNESS_STATUS + SCOPE/PENDING 词汇 — 正确
- L61-66 PolicyOp（add/modify/remove_item）— 正确
- L82-112 PolicyEdit — 正确
- L119-183 PendingValidationItem — 正确
- L190-250 PolicyGenome content_hash 覆盖 items + registry — 正确
- L251-551 其余方法（apply_policy_edit/validate_policy_edit/items_for_scope）— 正确
**结论**: ✅ 无问题。

#### `policy_reflection.py` (431 行)
**职责**: L2 策略反思提示词与解析。
**审查**: 确定性规划器预计算候选，模型仅选择或弃权。✅ 无问题。

#### `registry_reflection.py` (302 行)、`registry_promotion.py` (256 行)
**职责**: L2 词表在线注册与晋升。
**审查**: 新注册项 pending（shadow-only），需 ≥3 独立观察才晋升。✅ 无问题。

#### `meta_skill.py` (303 行)、`meta_review.py` (623 行)
**职责**: Meta 闭环（门事件聚合 → MetaEdit 候选 → 验证 → 提交）。
**审查**:
- meta_skill: GUIDANCE_TOKENS/RANKING_PREFERENCES/GATE_PROFILES 封闭词汇 — 正确
- meta_review: GateExperience/OutcomeValidation 聚合 — 正确
- **旧审查报告的"预测性预加载硬编码映射"问题仍部分存在**: `meta_review.py` 中 `predict_candidate_need` 仍有硬编码的 `(code, mapped_target)` 映射（仅 2 条）。非阻塞，但可改进为从 `COMMIT_EVIDENCE_SOURCES` 动态推导。
**结论**: ✅ 基本无问题。轻微改进点。

#### `gate_feedback.py` (317 行)、`promotion_contract.py` (436 行)、`promotion_gates.py` (84 行)
**职责**: 门事件审计、统一晋升契约、G1 检查。
**审查**: 封闭词汇、证据分级、激活状态、硬拒绝码完整。✅ 无问题。

#### `failure_episode.py` (1226 行) ★因果切片
**职责**: 因果失败 episode（权威的在线 skill 形成记录）。
**审查**: FailureEdge/CausalSlice/MechanismSignature + build_failure_episode + project_failure_episode_for_prompt。标签隔离 `EVALUATOR_ONLY_KEYS`。✅ 无问题。

#### `evolution_experience_memory.py` (559 行) ★经验记忆
**职责**: 在线因果经验记忆（机制签名索引 + 类型统计复合）。
**审查**: 4 个代表槽（effective/no_effect/structural_reject/no_proposal）+ 去重账本（1024 上限）+ legacy schema 迁移。✅ 无问题。

#### `signal_adapter.py` (504 行) ★信号适配
**职责**: PACA-TCL v3.5 → BCCSE 自监督信号适配。
**审查**:
- L31-37 CRS_WEIGHTS 冻结 0.35/0.15/0.20/0.20/0.10 — 正确
- L53-82 `decision_authority_invalid`/`missing_decision_authority` — 正确
- L84-148 `resolve_authoritative_state` 多层回退（occurrence > incident > legacy）— 正确
- L150+ CRS 投影 — 正确
**结论**: ✅ 无问题。

#### `experience_retriever.py` (303 行)、`negative_feedback_analyzer.py` (260 行) ★v6.1 扩展
**职责**: 结构化经验检索 + 负反馈深度分析。
**审查**:
- experience_retriever: FailurePattern/ExperienceMatch + 三层检索（exact→shape_context→causal）— 正确
- **旧审查报告的"负反馈分类字符串包含"问题已修复**: `negative_feedback_analyzer.py` L75-90 现使用**前缀匹配**（`reason.startswith(...)`）而非字符串包含 — ✅ 正确
- **旧审查报告的"Level 2 检索语义不准确"问题仍存在**: `experience_retriever.py` Level 2 检索时 `failure_shape="unknown"`/`causal_position="unknown"`/`impact_scope="unknown"`，导致 `_similarity` 中 60% 权重不可用。非阻塞但准确性受限。
**结论**: ✅ 负反馈分类已修复。⚠️ Level 2 检索准确性仍是轻微改进点。

#### `cesv_contract.py` (790 行)
**职责**: CESV v4.0 因果图（辅助）。
**审查**: 7 边类型 + ProvenanceGraph + 顺序证据聚合。纯确定性。✅ 无问题。

#### `evolution_runner.py` (8169 行) ★★★主循环
**职责**: 在线进化主循环。
**审查**:
- L199-266 EvolutionStep/OnlineEvolutionResult 数据结构 — 正确
- L314-476 `_expected_effect_audit` 契约审计 — 正确
- L477-541 `_classify_replay_no_effect` 确定性无效应分类 — 正确
- L1047-1119 `_attribute_failure` 确定性分类 + gold 极性门 — 正确
- L1120-1250 对比回放注册/选择/反转检测 — 正确
- L1576-1607 ProvisionalEdit/PendingProbeCommit — 正确
- L1615-1687 probe 案例增量/认证 — 正确
- L1692-1782 rule_trace 收集 — 正确
- L1784-2016 `_run_detection_with_genome` genome 注入 + 共享缓存 + fail-closed 权威检查 — 正确
- L2058-2068 `_atomic_write_json` — 正确
- L2090-2102 `_payload_hash` — 正确
- L2103-2326 `load_evolution_checkpoint` — 正确
- L2327-2438 `_restore_state_from_checkpoint` — 正确
- L2467-2782 `_restore_checkpoint_state` 完整状态恢复 — 正确
- L2783-2893 `_llm_no_conflict_gate` T5 咨询门 — 正确
- L2894-2920 `_record_detector_provider_usage` — 正确
- L2921-3020 `_validate_evolution_params` 参数校验 — 正确
- L3021-3108 `_build_evolution_result` 结果装配 — 正确
- L3113-3158 `_canary_preserved` — 正确
- L3159-3246 `_attribution_for_result`/`_fitness_gold_label`/`_selected_canaries` — 正确
- L3213-3246 `_run_mutation_call` 预算计数 — 正确
- L3247-3295 `_run_counted_detection` — 正确
- L3296-3348 `_PersistenceContext`/`_persist_progress` — 正确
- L3477-3508 `_RunState` 单一状态容器 — 正确
- L3509-3611 `_record_gate_event` — 正确
- L3612-3668 `_record_attempt` — 正确
- L3669-3777 `_run_registry_reflection_impl` — 正确
- L3778-3992 `_run_development_probe_impl` — 正确
- L3993-4043 `_save_evolution_results` — 正确
- L4044-4664 `_flush_shadow_batch_impl` 批提交/回滚 — 正确
- L4665-5184 `_run_l2_policy_case_impl` L2 策略案例 — 正确
- L5185-8169 `run_online_evolution` 主循环：
  - L5302-5318 参数校验 — 正确
  - L5332-5456 状态初始化（_RunState 单一容器）— 正确
  - L5458-5493 HTC-TES + 经验记忆初始化 — 正确
  - L5510-5567 resume 恢复 — 正确
  - L5605-5645 canary 播种 — 正确
  - L5647-5801 persist/record 闭包 — 正确
  - L5804-5812 工作队列 — 正确
  - L5818-8020 主 while 循环：
    - L5842-5935 检测 + runtime_invalid 处理 — 正确
    - L5938-6081 归因 + L2 分流 — 正确
    - L6082-6124 稳定案例处理 — 正确
    - L6126-6228 触发 + 预算耗尽短路 — 正确
    - L6252-6937 LLM 反思（多候选 + critic + diagnosis + pro 路由）— 正确
    - L7054-7252 结构门 + 重试 — 正确
    - L7254-7316 LLM 无冲突门 T5 — 正确
    - L7318-7360 apply_edit — 正确
    - L7362-7896 fitness 源重放 + canary + 对比回放 — 正确
    - L7898-7954 fitness_check=False 兼容路径 — 正确
    - L7956-7994 shadow 提交 + flush — 正确
    - L7996-8020 弃权阶梯 rung 1 — 正确
  - L8022-8169 最终汇总 + Meta 聚合 + 结果保存 — 正确

**⚠️ 问题 ER-1（高度，维护风险）**: `run_online_evolution` 函数体达 **2985 行**（L5185-8169），是整个框架最大的可维护性风险。虽然已通过 `_flush_shadow_batch_impl`/`_run_l2_policy_case_impl`/`_run_development_probe_impl` 等模块级函数提取了部分逻辑，但主循环体仍包含大量内联闭包（`run_detection`/`run_mutation_call`/`persist_progress`/`record_step`/`record_attempt`/`_run_registry_reflection`/`run_l2_policy_case`/`run_development_probe`/`flush_shadow_batch`）和深度嵌套的条件分支。
  - **影响**: 修改风险高、测试覆盖难、新功能注入易引入回归
  - **建议**: 将主循环体进一步提取为一个 `EvolutionLoop` 类，闭包转为方法，状态集中在实例属性（而非 `_RunState` + 散布的局部变量混合）

**⚠️ 问题 ER-2（中度，代码质量）**: L7139 `type('P', (), {...})()` 动态创建临时类模拟 `EditProposal`，在 L7759-7767、L7800-7808 重复出现。这是为了兼容 `RejectedEditEntry.proposal` 类型，但动态类创建不安全且难维护。
  - **建议**: 用轻量 dataclass 或 NamedTuple 替代

**⚠️ 问题 ER-3（低度，机制风险）**: L6087-6088 `stable_canaries.pop(next(iter(stable_canaries)))` 用 FIFO 淘汰，但 stable case 的"代表性"可能随基因组演进变化。固定 FIFO 可能保留过时锚点。
  - **建议**: 考虑按基因组 hash 分桶或按类型分桶（与 contrast_replay_registry 一致）

**结论**: ⚠️ 问题 ER-1（巨型函数）/ER-2（动态类）/ER-3（canary 淘汰）。主循环逻辑正确，但可维护性是最大风险。

---

### 2.4 HTC-TES 类型条件化层 (`core/htc_tes/`)

#### `__init__.py` (41 行)
**审查**: 导出 8 个符号。✅ 无问题。

#### `type_classifier.py` (664 行) ★类型分类
**职责**: 8 类型分层分类。
**审查**:
- L50-68 `_normalize_signal` 接受 AttributionSignal | Mapping | None — 正确（解耦）
- L82-96 HallucinationType 8 类型 — 正确
- L99-180 6 组模式正则 — 正确
- 分层架构（结构层 → 归因增强 → 证据阈值）— 正确
**结论**: ✅ 无问题。

#### `type_conditioned_evolution.py` (245 行)、`type_aware_fitness.py` (891 行)
**职责**: 类型路由 + 类型感知 fitness + 类型经验记忆。
**审查**: Type→Module 路由表 + TypeExperienceMemory + TypeAwareFitnessAssessment。✅ 无问题。

---

### 2.5 检测器与进化包入口

#### `detector/__init__.py` (49 行)、`__main__.py` (7 行)、`batch.py` (486 行)、`replica_aggregation.py` (88 行)
**审查**:
- batch.py: 并发 + hash-bound resume + 瞬态重试 + 标签剥离 — 正确
- replica_aggregation.py: fail-closed 聚合（不一致/不足/invalid → unresolved）— 正确
- 温度 guard: replicas>1 且全 0 → 降级单副本 — 正确
**结论**: ✅ 无问题。

#### `evolution/__init__.py` (45 行)、`__main__.py` (135 行)、`runner.py` (345 行)、`state_manifest.py` (147 行)
**审查**:
- runner.py: `run_evolution` 是 `run_online_evolution` 的参数适配壳，关闭 probe/canary 参数 — 正确
- state_manifest.py: sha256 绑定 3 个产物 + 路径逃逸检查 — 正确
- inherit_from 与 resume_from 互斥 — 正确
**结论**: ✅ 无问题。

---

## 3. 机制有效性分析

### 3.1 检测器有效性（PACA-TCL v3.5）

| 机制 | 有效性 | 评估 |
|------|--------|------|
| H1 聚焦起诉 | ✅ 高 | 每候选独立因果链脚手架 + 迭代精炼，避免广播式起诉的信息稀释 |
| H2 校准上诉 | ✅ 高 | priority 0-1 触发 + 共识门（上诉正性需初始独立支持）从根因消除 v3.4 FP |
| H3 反事实定位 | ✅ 高 | 仅 ≥2 不同 origin 时触发，root_cause 优先 + 时间 tiebreak |
| 结构契约门 | ✅ 高 | 每证书独立校验，quarantine 不静默丢弃 |
| fail-closed 纪律 | ✅ 高 | 无效运行重置所有判决字段；authority 检查贯穿 |
| LLM 修复 | ✅ 中 | 单次有界修复（truncation/empty content），cache key 不同 |
| **缓存隔离** | ⚠️ 中 | 全局缓存 opt-in；同 genome+case 复用正确，但跨 genome 不隔离时可能命中 stale |

### 3.2 自进化有效性（BCCSE v5）

| 机制 | 有效性 | 评估 |
|------|--------|------|
| 确定性归因 | ✅ 高 | artifact-first 分类器，L1/L2 分层，gold 极性门 |
| 自监督触发 | ✅ 高 | unresolved/CRS 矛盾/证据不全 + 过程诊断；diagnostic_only 永不进化 |
| L1 反思 | ✅ 高 | 槽契约 + charter 约束 + ActionSpec 可执行规范 |
| 结构约束门 | ✅ 高 | 多层约束 + 保护规则 + baseline 不可变 |
| 源重放 fitness | ⚠️ 中 | **自监督 directional_evidence 不可 promotable**（设计纪律），无 gold 时 L1 skill 只能进 shadow |
| canary 非回归 | ✅ 高 | 滚动 stable case + 回归拒绝 |
| 对比回放 | ✅ 中 | 同类型邻居反转检测，fail-open 但审计 |
| **L2 封闭词表** | ✅ 高 | DSL 编译器 + 编译时类型检查 + pending→registered 晋升门 |
| **Meta 闭环** | ⚠️ 中 | 候选生成 + 验证 + 提交，但预测性预加载映射硬编码 |
| **经验记忆** | ⚠️ 中 | 机制签名去重正确，但 Level 2 检索 60% 权重不可用 |

### 3.3 类型条件化有效性（HTC-TES v3）

| 机制 | 有效性 | 评估 |
|------|--------|------|
| 类型分类 | ✅ 中 | 分层架构，但正则模式依赖英文关键词，跨语言轨迹可能降级 |
| 类型路由 | ✅ 高 | advisory 不越权，空交集回退归因路由 |
| 类型感知 fitness | ✅ 中 | 类型经验记忆 + 决定性遥测，但不等同准确率提升 |

### 3.4 进化闭环完整性

**闭环验证**：
1. ✅ detect → attribute → reflect → gate → fitness → commit/rollback → experience 全链路贯通
2. ✅ L1/L2 双层并行进化，各自独立 fitness + rollback
3. ✅ checkpoint resume 完整恢复（genome + policy + memory + budget + canary + gate）
4. ✅ 跨运行继承（state_manifest sha256 绑定）
5. ⚠️ **纯自监督产出的 L1 skill 无法获得 strong 保护**——必须经独立 gold 验证才能部署。这是设计纪律（避免自确认），但意味着**框架产出的 skill 默认是候选，不是成品**

### 3.5 核心有效性瓶颈

1. **自监督 promotability 瓶颈**（设计纪律，非 bug）：`directional_evidence` 只能进 shadow，`strong`/`local` 需 gold。纯无 gold 运行产出的 champion 是"过程稳定但正确性未证"的候选。
2. **evolution_runner.py 巨型函数**（ER-1）：2985 行主函数 + 9 个内联闭包，是修改回归的最大风险源。
3. ~~**proof_evaluator.py 死代码**（PEV-1）~~ ✅ 已删除，测试辅助逻辑上移 signal_adapter。
4. **经验检索 Level 2 准确性**：60% 权重不可用（unknown 字段），限制跨机制类比。

---

## 4. 问题汇总

### 4.1 按严重程度

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| ER-1 | 高 | evolution_runner.py L5185-8169 | `run_online_evolution` 2985 行巨型函数 + 9 内联闭包，维护风险 |
| PEV-1 | ✅ 已修复 | proof_evaluator.py (970 行) | 已删除；metric_snapshot/experience_record_from_row 上移 signal_adapter |
| SA-1 | 低 | step_attributor.py L1421-1451 | reflection_type 重复计算（`_build_attribution_signal` 已通过 `_resolve_reflection_type` 计算） |
| ER-2 | 中 | evolution_runner.py L7139 等 | `type('P', (), {...})()` 动态类模拟 EditProposal，重复 3 处 |
| ER-3 | 低 | evolution_runner.py L6087-6088 | canary FIFO 淘汰可能保留过时锚点 |
| PE-1 | 低 | proof_engine.py L2064-2065 | `chain_completeness_scores` 扩展无上限 |
| Meta-1 | 低 | meta_review.py | `predict_candidate_need` 硬编码映射仅 2 条 |
| Retr-1 | 低 | experience_retriever.py | Level 2 检索 failure_shape/position/scope 为 unknown，60% 权重不可用 |
| Esc-1 | 设计 | policy_evaluator.py L242-264 | `escalate_to_appeal`/`mark_candidate_ambiguous` 仅遥测不改终态（设计选择，需文档明确） |
| Gold-1 | 设计 | fitness.py L515-527 | 自监督 directional_evidence 不可 promotable（设计纪律，限制纯无 gold 产出价值） |
| Sym-1 | 低 | evolution_runner.py | 9 个内联闭包捕获大量局部变量，无法独立测试 |
| Cache-1 | 低 | basic_runner.py | 全局缓存跨 genome 不隔离时可能 stale（opt-in 缓解） |

### 4.2 已修复的旧问题（对比 CODE_REVIEW_REPORT.md）

| 旧问题 | 状态 | 修复位置 |
|--------|------|----------|
| `quarantine_reason_*` 恒为 0 | ✅ 已修复 | v6_contracts.py L96-136 现从 audit failed_checks 推导 |
| 新动作不改变状态 | ✅ 部分修复 | policy_evaluator.py L226-241 require_additional_verification 现降级覆盖 |
| 上诉归类到 CONSENSUS_GATE | ✅ 已修复 | failure_classifier.py L262-283 现归类到 APPEAL_MECHANISM |
| 负反馈分类字符串包含 | ✅ 已修复 | negative_feedback_analyzer.py L75-90 现用前缀匹配 |
| Rule 4.4 误触发 | ✅ 已修复 | failure_classifier.py L300-302 要求 localization_status != "" |
| 循环导入 | ✅ 已修复 | negative_feedback_analyzer.py 用 Any + lazy import |

---

## 5. 运行验证

### 5.1 测试套件
```
817 passed in 25.07s
```
全部通过，无失败/跳过/警告。

### 5.2 产物正确性
- `champion_genome.json`: 8 模块完整、content_hash 覆盖 rule text、baseline is_baseline 保护
- `policy_genome.json`: 封闭词表验证、registry 参与 content_hash、deployable/working scope 分离
- `htc_tes_experience_memory.json`: schema v2、mechanism 签名去重、meta_review 子域完整
- `evolution_state_manifest.json`: sha256 绑定 3 产物、路径逃逸检查
- `evolution_result.json`: 完整审计轨迹（steps + gate + budget + provider_usage）

### 5.3 可运行性
- CLI 入口完整：`python -m src.sehd_v6.detector` / `python -m src.sehd_v6.evolution`
- 库 API 完整：`detect_record` / `run_evolution` / `run_online_evolution`
- 依赖闭环：无外部循环依赖（bccse↔htc_tes 用 lazy `__getattr__` 解环）

---

## 6. 修复优先级建议

### 6.1 高优先级
1. **ER-1**: ✅ 已完成——`run_online_evolution` 重构为 `EvolutionLoop` 类，9 闭包转方法，817 测试通过
2. **PEV-1**: ✅ 已完成——`proof_evaluator.py` 已删除，测试辅助逻辑上移 `signal_adapter`，817 测试通过

### 6.2 中优先级
3. **ER-2**: 用 dataclass 替代 `type('P', (), {...})()` 动态类
4. **SA-1**: 移除 `attribute_failure` 中 reflection_type 重复计算
5. **Retr-1**: 在 MechanismEntry 中存储更多模式信息，或在 _similarity 中处理 unknown

### 6.3 低优先级
6. **ER-3**: canary 淘汰策略改为按类型/genome 分桶
7. **PE-1**: chain_completeness_scores 加 cap
8. **Meta-1**: 预测性预加载映射从 COMMIT_EVIDENCE_SOURCES 动态推导
9. **Esc-1**: 文档明确 escalate_to_appeal/mark_candidate_ambiguous 的"仅遥测"设计意图

---

## 7. 结论

SEHD v6 检测器-自进化框架**整体可正常运行并合理产出 skill**。817 测试全通过，三大子系统（PACA-TCL / BCCSE / HTC-TES）职责清晰、依赖闭环、fail-closed 纪律严格。之前审查报告中的核心问题（quarantine_reason 恒为 0、上诉归类错误、负反馈字符串包含）均已修复。

**主要风险**是 `evolution_runner.py` 的 8169 行巨型文件和 2985 行主函数（ER-1），这是修改回归的最大根源。其次是 `proof_evaluator.py` 死代码（PEV-1）和若干代码质量问题。

**机制有效性的核心设计纪律**是：自监督产出的 L1 skill 只能作为候选（directional_evidence 不可 promotable），必须经独立 gold 验证才能获得 strong 保护并部署。这符合导师"skill 效果必须在独立验证集验证"的要求，但也意味着框架产出的 skill 默认不是成品。

建议按优先级修复 ER-1 和 PEV-1，然后处理中低优先级的代码质量问题。框架的设计纪律（无 gold 不 promotable、封闭词表、fail-closed、标签隔离）应予保持。

---

*本报告基于对 src/sehd_v6/ 全部 30 个 Python 脚本的逐行静态审查 + 817 测试验证 + 依赖建模 + 机制有效性分析。*
