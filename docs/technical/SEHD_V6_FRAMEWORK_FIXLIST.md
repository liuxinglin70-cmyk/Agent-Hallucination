# SEHD v6 深层代码审查修复清单

> **来源**：2026-08-07 第二轮代码行级审查（4 个并行审查通道 + 逐条人工核实）
> **审查范围**：`src/sehd_v6/` 全部 production 代码（约 29k 行）
> **核实方式**：每条发现均回到源码验证；误报已剔除（如 A5 `if kind else []` 已保护、D6 已有 bool 守卫）
> **状态**：**R01–R29（P0+P1+P2）已全部修复并落地**；P3 清理项（R30–R40）未执行，留待后续。
> **测试基线**：`python -m pytest -q tests` → **761 passed**（738 原有 + 23 新增回归测试 `tests/test_review_fixes_r2.py`）

---

## 0. 汇总

| 级别 | 数量 | 状态 |
|---|---|---|
| **P0 高危** | 3 | ✅ 已修复（R01–R03） |
| **P1 重要** | 7 | ✅ 已修复（R04–R10，R09/R04 有修订见 §7） |
| **P2 加固** | 19 | ✅ 已修复（R11–R29，R24/R25 有说明见 §7） |
| **P3 清理** | 11 | ⬜ 未执行（R30–R40） |
| **合计** | **40** | 29 已修复 / 11 待清理 |

---

## 1. P0 高危（必须修）

### R01 显式配置的部署密钥被静默覆盖
- **位置**：`src/sehd_v6/provider_routes.py:122-127`
- **问题**：第一个分支条件为 `not explicit_key_selector and env.get("DEEPSEEK_API_KEY") and endpoint == 官方`，**缺少 `not selected_key_env` 条件**。当 route 显式配置 `api_key_environment="CORP_KEY"`、环境里恰好存在 `DEEPSEEK_API_KEY` 时，显式配置被覆盖为官方默认密钥。注释自称"Keep an explicitly configured deployment credential fail-closed"，此分支恰好违反该意图（L128-129 的 elif 才是正确语义）。
- **影响**：部署专用凭据被静默替换；密钥选择不可审计；与 fail-closed 设计矛盾。
- **修复**：第一分支追加 `and not selected_key_env`，或直接删除该分支由 elif 承担。
- **验证**：`tests/test_provider_route_env.py` 补用例：route 显式 `api_key_environment` + 环境有 `DEEPSEEK_API_KEY` → 断言保留显式值。

### R02 `revoke_structural_accept` 延迟恢复 streak 导致提前/推迟收敛
- **位置**：`src/sehd_v6/core/bccse/safety_gate.py:201-202`
- **问题**：用证书在"接受时"的快照 `prior_consecutive_no_edit` **整体覆盖**当前 streak。该恢复只有在 revoke 紧随 accept 时才精确；而 runner 的 **probe 回滚**（`evolution_runner.py:4193-4195`）在累积了多个 case 之后才 revoke（`pending_probe_commits` 跨 case 累积）：
  - `prior` 高（如 19）且中间只过 2 个 case → 恢复 19 + runner 再 `record_no_edit()` = 20 ≥ 窗口 → **提前收敛停机**；
  - `prior` 低且中间累积了 no-edit → 恢复抹掉中间计数 → 收敛被推迟。
- **影响**：probe 回滚场景下收敛判定失真（提前停机丢队列 / 推迟停机多烧预算）。
- **修复**：`revoke_structural_accept` 不再碰 streak（runner 已负责在 revoke 后精确 `record_no_edit()` 一次）；为 probe 回滚引入独立的 streak 快照/恢复 API（`gate.snapshot_convergence()` / `restore_convergence()`），probe 失败时恢复**probe 前**的快照而非逐证书覆盖。`defer_structural_reject`（L214-215，即时调用）保持原样。
- **验证**：`tests/test_graded_gate.py` 补用例：accept → 多个 no-edit → 延迟 revoke → streak 不被抹掉；probe 回滚恢复 probe 前快照。

### R03 critic 选择 `int(bool)` 绕过 fail-closed
- **位置**：`src/sehd_v6/core/bccse/reflection_engine.py:760-763`
- **问题**：`index = int(critic_response.get("selected_index"))`。Python `int(True)==1`、`int(False)==0`，critic 若返回 `"selected_index": false/true` 会静默选中 index 0/1 而不是 fail-closed。
- **影响**：违反"fail-closed 选择"契约，一个畸形响应可绕过拒绝路径直接选中候选。
- **修复**：先 `isinstance(value, bool)` 拒绝，再 `isinstance(value, int)`；仅接受 int（或数字字符串经严格转换）。
- **验证**：`tests/test_feedback_loop.py` / `tests/test_reflection_history.py` 补 `selected_index=true/false` 用例断言 fail-closed。

---

## 2. P1 重要（应该修）

### R04 `candidate_trigger`（direction=None）负 delta 恒判 worse，gold-clean 修复被拒
- **位置**：`src/sehd_v6/core/bccse/fitness.py:660-668`（配合 L36-37 方向表、L282-285 模块分）
- **问题**：`_MODULE_SCORE_GOLD_DIRECTION["candidate_trigger"] = None`（注释"score direction is neutral"），但 `delta < 0` 在 L660-668 **无条件 worse**。典型反例：gold-clean 案例、task 分未变，一次正确修复把多余候选 1→0（`module_local_score` 1.0→0.0）→ 被判 worse 拒绝；gold 可用时本该裁定"移除候选是修复"。
- **影响**：candidate_trigger 模块的正确"减候选"修复被系统性拒绝，L1 进化在该模块被卡死。
- **修复**：对 `direction=None` 的模块，负 delta 不再无条件 worse：gold 可用时基于 `task_state_score` 变化判定（task 不变 → no_effect；task 改善 → local_improvement）；无 gold 时保持现有保守行为（负 delta → no_effect 而非 worse，避免自监督方向性误判）。
- **验证**：`tests/test_evolution_hardening.py` 中 fitness 相关用例补"clean 案例移除候选"场景。

### R05 gold 路径 `trigger_source` 被无条件覆盖为 `EVIDENCE_INCOMPLETE`
- **位置**：`src/sehd_v6/core/bccse/step_attributor.py:989-996`
- **问题**：该块条件为 `has_gold and error_type != CORRECT and candidate_count == 0 and gold is True`，**无条件把 trigger_source 覆盖为 EVIDENCE_INCOMPLETE**（自监督触发器）。它运行在 gold 路径（Trigger 3 `GOLD_MISMATCH`/`LOCALIZATION_ERROR`）**之后**：gold=True、predicted==True、`step_correct=False` 的定位错误在 census 为空时被降级为 `CANDIDATE_MISS`，`LOCALIZATION_ERROR` 分支永不执行。
- **影响**：触发源与错误类型语义矛盾；gold 确认的定位错误被静默改写为候选缺失；审计与证据摘要失真。
- **修复**：仅当 `trigger_source == TriggerSource.NONE`（或明确非 gold 触发）时才允许覆盖；gold 路径保留 `GOLD_MISMATCH`；或将该块移入 cascade 并加互斥。
- **验证**：`tests/test_evolution_hardening.py` 定位相关用例补"gold + step_mismatch + census 空"场景，断言 error_type 保持 LOCALIZATION_ERROR。

### R06 `_slot_for` 未覆盖 `rollback_batch`，最差回滚结果对记忆检索不可见
- **位置**：`src/sehd_v6/core/bccse/evolution_experience_memory.py:90-107`
- **问题**：`rollback_batch`（runner `flush_shadow_batch` 拒绝分支，`structural_result="accepted"` 且 worse 时无 `no_effect_reason`）不满足任何分类 → `_slot_for` 返回 None → **整条 attempt 不进任何代表槽**，只有计数。等价的单源 `reject_fitness`（reject 前缀）进 no_effect 槽。
- **影响**：批量路径中最差的"worse 回滚"对 `retrieve()` 完全不可见，反思 LLM 永远看不到这类负面样本。
- **修复**：`rollback_batch`/`rollback_probe`/`rollback_resume` 并入 no_effect（或 structural_reject）判定；同时调整判定顺序让 `structural_result` 优先于 no_proposal 列表（消除 `no_edit`+`rejected` 组合错分隐患）。
- **验证**：`tests/test_evolution_experience_memory.py` 补 `rollback_batch` 槽位用例。

### R07 deep repair 整体替换合法辩护证书
- **位置**：`src/sehd_v6/core/paca_tcl/proof_engine.py:1054-1059`
- **问题**：deep repair 的响应 `repair_response` 若全部通过校验则 **`return repair_response`**，整体替换原 `appeal_defense`；而 repair prompt 只要求"为无效候选补证书"。原响应中通过校验的合法辩护**无合并逻辑**。且 court 对 `appeal_defense` 只校验 coverage=complete + 每张证书通过，**不校验 target_candidate_id 集合是否覆盖 required**（`proof_contract.py:391-394`）。
- **影响**：合法辩护被静默丢弃 → 防御池变弱 → verified_clean 可能翻转为 verified_error（FP 增加）。
- **修复**：修复响应与原始 valid_rows 合并后再进法院；或 court 对 appeal_defense 增加"target 集合 == required"强校验。
- **验证**：`tests/test_proof_court_policy_hook.py` / detector 测试补"部分无效证书 + 合法证书保留"场景。

### R08 `metric_snapshot` abstention 分母不对称
- **位置**：`src/sehd_v6/core/paca_tcl/proof_evaluator.py:608,614,623`
- **问题**：混淆计数排除 abstention（predicted None 不进任何桶），但分母 `gold_positive`/`gold_negative` **包含** abstention 行 → 弃权不对称压低 `hallucination_recall` / `clean_recall` / `clean_fpr`，与 `basic_runner.py` 声明的"abstentions excluded from binary confusion metrics"语义不一致。
- **影响**：评测指标被弃权率扭曲；进化/晋升判断可能据此误判基因组优劣。
- **修复**：分母统一为有判定行的计数（tp+fn / tn+fp），或明确声明"recall 含弃权惩罚"并统一两处语义。
- **验证**：`tests/test_framework_hardening_r1.py` 补 abstention 场景断言 recall/clean_fpr 语义一致。

### R09 共识门与隔离/process_only 触发自相矛盾（appeal 结构性必死）
- **位置**：`src/sehd_v6/core/paca_tcl/proof_engine.py:506-516`（触发）+ `proof_contract.py:198-215`（共识门）
- **问题**：priority 0/1 触发候选（all_quarantined / process_only / unresolved 因果）在 initial prosecution 里**必然没有** admissible 因果证书（全隔离候选返回空证书列表；状态不达标）→ 共识门对它们的 appeal 证书**结构性必拦截**。v3.5.2 注释"remain appealable via the calibrated trigger"不成立。
- **影响**：这类候选的 appeal 调用是纯浪费的 provider 开销；"给第二次机会"的设计意图落空（fail-closed 方向，不产生错误判定）。
- **修复**：共识门对"初始无证书的隔离候选"豁免（或触发时携带初始审计标记使 gate 放行）；至少应在 `_appeal_trigger_calibrator_v35` 中剔除这两类必死触发。
- **验证**：`tests/test_sehd_v6_core_contracts.py` 补"all_quarantined 候选 + appeal + consensus gate"场景，断言不产生无意义 appeal 调用或能正确放行。

### R10 无 CRS 信号的 unresolved case 被系统性判为 CONTEXTUAL
- **位置**：`src/sehd_v6/core/htc_tes/type_classifier.py:346-365`
- **问题**：`AttributionSignal.crs_aggregate` 默认 0.0（无信号），`isinstance(0.0, (int,float))` → `crs_agg=0.0` → `< 0.2` → 每个无 CRS 的 unresolved case 被加 `CONTEXTUAL + state_coverage` 证据。且两个来源（signal.crs_aggregate 高=异常；evidential_scores 平均低=异常）极性相反却共用同一阈值。
- **影响**：类型判定系统性偏差（污染 evidence/support_families、confidence 归一化）。
- **修复**：区分"无 CRS 信号"（None）与真实低分；对两个来源分别使用与各自极性匹配的阈值。
- **验证**：`tests/test_causal_baselines.py` / htc_tes 测试补"无 CRS 信号 unresolved"场景，断言不产生 state_coverage 证据。

---

## 3. P2 加固（防御性/边缘）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| R11 | `evolution_runner.py:4500-4509` | 接受路径 `assess_edit(detection_after=source_audits[idx].get("shadow_result", {}))` 无 try 保护，`shadow_result` 缺失/空 dict 时 `resolve_evolution_state({})` 抛 ValueError → 整轮 run 崩溃（拒绝路径有 try，接受路径没有，不对称） | 接受路径前置校验 shadow_result 非空且状态合法，或包 try 记 runtime_errors |
| R12 | `genome.py:376` | `condition = normalized.split("时", 1)[0]`：中文词内"时"（如"时间戳缺失时"）把 condition 截成"当"→ action 错位、`multiple_actions`/`condition_overload` 检查失真 | 改用 `re.split(r"时[，,]", …)` 或先匹配句读分界 |
| R13 | `proof_evaluator.py:585` | `parse_gold_label(gold)` 无 try/except（对比 L458-461 `_row_correct` 有），脏 gold（"maybe"）整批评估崩溃 | 与 `_row_correct` 一致容错跳过 |
| R14 | `step_attributor.py:519-523` | `_APPEAL_INCOMPLETE_STATUSES` 含 `"incomplete_fail_closed"`，但引擎实际发射 `"incomplete_fail_closed_unresolved"`（proof_engine.py:1313），词汇已漂移（当前被 error 分支掩盖） | 前缀匹配或补全精确值 |
| R15 | `step_attributor.py:421` | `row.get("gold_steps", row.get("hallucination_step"))`：gold_steps 键存在但值为 None 时回退不生效（Python 语义），回退是死逻辑 | 先取 raw 再判 None 回退 |
| R16 | `type_classifier.py:265,488` | `for axis in (row.get("axes") or [])`：axes 为非序列（int/str）时 TypeError | isinstance Sequence 防护或降级 UNKNOWN |
| R17 | `failure_episode.py:980` | `evidence.get("missing_fields") or ()` 若为字符串会被 `_sorted_dedup` 按字符展开成集合，污染机制签名 | 先 isinstance(list/tuple/set) 守卫 |
| R18 | `failure_episode.py:619,638-639` | `signal.trigger_source.value` / `signal.error_type.value` 裸取（同文件其他处用 getattr 防御） | 统一 getattr(..., "value", "") |
| R19 | `genome.py:846-854` | `validate_edit` 副作用：把调用方 `SkillEdit.operation` 从 SkillOp 静默改写为 str | 校验用局部变量，不落回入参 |
| R20 | `evolution_runner.py:3676-3677` | L2 `finish()` 直接写 `gate._consecutive_no_edit = 0; gate._converged = False` 私有字段，绕过公开 API，与 R02 的 streak 语义修改冲突 | 增加 `gate.reset_convergence()` 公开调用或证书驱动 |
| R21 | `failure_episode.py:695-699` | `_candidate_rows` 无 `prosecution`/`defense` 旧键回退（v3.5 输出无旧键，属一致性加固） | 补回退保持与 fitness.py 一致 |
| R22 | `signal_adapter.py:114-121` | 顶层 legacy state token（如 `verified_hallucination`）直接 raise ValueError，重载旧数据崩溃 | legacy map 归一化或 fail-closed 返回 unresolved |
| R23 | `reflection_engine.py:469-474` | rejected 编辑 `proposal.content[:100]` 原文回显进下轮 prompt，未再 sanitize | echo 前再过特征校验/剥离命中片段 |
| R24 | `step_attributor.py:1246-1251` | `step_mismatch` 的 `gold_step_count` 进 `evidence_summary`（恒进 prompt），泄漏 gold 标注存在性元数据 | 门控到 gold_reflection 或仅保留 predicted |
| R25 | `step_attributor.py:862` | `bool(row["gold_label"])`（有 L860-861 的 isinstance 守卫，低危；字符串 gold 会 raise 而非翻转，属 fail-closed） | 统一走 `coerce_gold_label` 单一事实源 |
| R26 | `skill_integration.py:481-514` | 进化规则文本原样拼接进 system prompt，无运行时过滤（下游 schema 校验 fail-closed，但注入面无防线） | 注入点加白名单/黑名单（拒绝 system/role 指令前缀） |
| R27 | `evolution/__main__.py` | 无 `--pro-mutation-route` 参数，`run_evolution` 已支持 `pro_mutation_route`，CLI 用户无法触发 Pro 升级 | 补 CLI 开关 |
| R28 | `detector/batch.py:228-237` | 失败仅写 failure.json，无进程内重试（瞬态超时/429 直接失败，需人工重跑整批） | 对可重试错误做有限次退避重试 |
| R29 | `env_loader.py:39-58` | (a) 找到的第一个 .env（可能为空）遮蔽上层根 .env；(b) `KEY=VALUE # comment` 行内注释进入 value | 跳过空文件/合并；剥离行内注释 |

---

## 4. P3 清理（死代码/诊断）

| # | 位置 | 内容 |
|---|---|---|
| R30 | `curriculum.py` 整个文件（98 行） | `order_evolution_cases`/`get_case_priority_label` 零调用；且 L66-68 的 gold 类型检查与 `parse_gold_label` 支持字符串自相矛盾（若接线必崩） |
| R31 | `step_attributor.py:1333-1423` | `build_attribution_llm_prompt`/`parse_attribution_llm_response` 零调用（LLM 增强归因已退役）；且 JSON 提取 `re.search(r"\{[^{}]*\}")` 无法匹配嵌套对象 |
| R32 | `step_attributor.py:1002-1017` | `UNRESOLVED_UNKNOWN` 细化块为纯 no-op（自赋值/pass） |
| R33 | `fitness.py:642-650`（及 61-64） | `_score_direction_matches_gold` 的 no_effect 分支不可达（前置条件已全覆盖） |
| R34 | `proof_engine.py:1803-1818` | 诊断指标 `focused_prosecution_explicit_no_error_count` 统计的是"成功候选"而非"显式无错误"；`coverage_error` 被计入 provider_error_count，语义失实（仅诊断） |
| R35 | `proof_engine.py:604,777-778` | `_classify_appeal_*_failure` 的 error 分支永不触发（retry 只在校验非空响应后调用），但 retry prompt 声明了这些分类——prompt 与实现不一致 |
| R36 | `skill_integration.py:406-420` | `route_stage_map` 零调用 |
| R37 | `type_conditioned_evolution.py` | `create_edit_proposal`/`intersect_allowed_modules`/`get_target_modules` 等约 300 行零调用（类型"路由"承诺与实际行为——仅提示文本——不符）；`create_edit_proposal` L370-372 有未校验的 target_module 回退 |
| R38 | `provider_usage.py` 整个文件（227 行） | `ProviderUsageLedger` 零 production 调用方（G5 悬置）——接线到 `run_mutation_call`/`run_detection` 或显式标注实验性 |
| R39 | `proof_contract_v3.py:194` `adjudicate_proof_court` | 零引用（含测试）死代码；`proof_engine_v3_4.py`/`contract_v1.py` 的 adjudicate/evaluate 仅测试引用（遗留） |
| R40 | `proof_engine.py:397 vs 431` | candidate_id 缺失时 fallback 不一致（"unknown" vs ""）→ 该候选状态漏计（当前 validate_provider_census 保证 candidate_id 必填，不可达） |

---

## 5. 误报剔除记录（避免后续重复排查）
| 原发现 | 位置 | 剔除原因 |
|---|---|---|
| `_select_candidate` 空 kind 回退读 defense 污染 | `failure_episode.py:767` | 实际代码 `rows = _candidate_rows(detection_result, kind) if kind else []`，空 kind 被短路为 []，走 census 回退（L785）不读 defense；仅"未知非空 kind 读 defense"残留（并入 R21 一致性项） |
| `bool(row["gold_label"])` 字符串翻转 | `step_attributor.py:862` | L860-861 已有 `isinstance(row["gold_label"], bool)` 守卫，非 bool 直接 raise（fail-closed），不会翻转（降级为 R25 统一事实源） |
| `_APPEAL_INCOMPLETE_STATUSES` 漏检 | `step_attributor.py:519` | 当前被 `_classify_appeal_failure` 的 error 分支掩盖不触发，但词汇已漂移 → 保留为 R14 加固 |
| contrast registry 双权威 / 缓存 key 不稳定 / JSON repair 重传 | 多处 | 已核实无问题：缓存 key 确定、repair 单次有界、retry 预算正确、v2 层仅作证据源无裁决双轨 |

---

## 6. 建议修复顺序

```text
第一批（P0，立即）：R01 密钥覆盖 · R02 streak 恢复 · R03 int(bool)
第二批（P1，机制质量）：R04 中性方向负 delta · R05 trigger 覆盖 · R06 slot 缺口
                      R07 deep repair · R08 metric 分母 · R09 共识门矛盾 · R10 CRS 信号
第三批（P2，防御）：R11-R29（其中 R11/R12/R14 优先）
第四批（P3，清理）：R30-R40（与功能修复分批提交，不混入）
```

**纪律**：每批独立提交；修复必须附回归测试；P0/P1 修复后全量 `python -m pytest -q tests` 必须保持 738+ 通过；行为变化需在提交说明中注明（尤其 R02 涉及收敛语义、R04 涉及 fitness 判定）。

---

## 7. 修复执行记录（2026-08-07）

### 已修复并落地（R01–R29）

| # | 修复内容 | 回归测试 |
|---|---|---|
| R01 | `provider_routes.py` 首分支追加 `not selected_key_env` | `test_r01_*`（显式密钥保留 / 无选择器用默认） |
| R02 | `safety_gate.py` 新增 `snapshot_convergence`/`restore_convergence`；`revoke_structural_accept` 增加 `restore_streak=False` 参数；`evolution_runner.py` probe 回滚改用快照恢复（不再逐证书覆盖 streak，删除回滚循环内 `record_no_edit`） | `test_r02_delayed_revoke_keeps_streak` / `test_r02_immediate_revoke_still_restores_priors` |
| R03 | `reflection_engine.select_intervention_candidate` 拒绝 bool `selected_index` | `test_r03_bool_selected_index_fails_closed` |
| R04 | `fitness.py`：中性方向模块（direction=None）负 delta 且 task 分未动 → `no_effect`（audited，不再是 worse 硬否决）。**修订**：初版"task 改善 → local_improvement"不可达（gold task 变化已在 L489-508 前置返回），改为无 harm 证据时 no_effect | `test_r04_neutral_module_negative_delta_is_no_effect` |
| R05 | `step_attributor.py`：candidate-miss 增强仅在 `trigger_source == NONE` 时覆盖，gold 定位错误保留 | `test_r05_gold_localization_error_survives_candidate_miss_override` |
| R06 | `_slot_for` 增加 `decision.startswith("rollback")` → no_effect 槽 | `test_r06_rollback_batch_slots_as_no_effect` |
| R07 | `proof_engine.py` deep repair 返回**合并**响应（原 valid_rows + repair_rows，target 重叠时 repair 优先），不再整体替换丢弃合法辩护 | —（依赖现有 detector 集成覆盖） |
| R08+R13 | `proof_evaluator.metric_snapshot`：recall/clean_recall/clean_fpr/responsible_step_accuracy 分母改为**有判定行**计数；`parse_gold_label` 加 try/except 跳过脏 gold | `test_r08_abstention_does_not_deflate_recall` |
| R09 | `_appeal_trigger_calibrator_v35`：仅剔除 process_only/unresolved 因果的必死触发。**修订**：all_quarantined/隔离初始证书**保留触发**（设计意图：appeal 是隔离证据的审计重审面，共识门豁免留作后续 P9 增强），因为现有测试锁定该行为 | —（原 `test_quarantine_only_..._triggers_appeal` 保持通过） |
| R10 | `type_classifier`：仅当 signal 有 `crs_scores` 时才信任 `crs_aggregate`（默认 0.0 不再被当真实低分）；无信号时走 evidential-scores 来源并按各自极性判定 | `test_r10_no_crs_signal_produces_no_state_coverage_evidence` |
| R11 | `evolution_runner.py` 接受路径 `assess_edit` 包 try/except（记 `runtime_errors`，typed_assessment=None 时不写审计） | — |
| R12 | `genome.py`：`当…时` 句式用正则 `时(?=[，,。；;：:\s]|$)` 定界，词内"时"不再截断 condition | `test_r12_*`（词内"时"通过 / 正常句式通过） |
| R14 | `_classify_appeal_failure` 状态判定改前缀匹配（`incomplete_fail_closed` 匹配引擎实际发射的 `incomplete_fail_closed_unresolved`） | `test_r14_appeal_status_prefix_match` |
| R15 | `_check_step_correct`：`gold_steps=None` 时正确回退 `hallucination_step`（原来 get 默认值死逻辑） | `test_r15_none_gold_steps_falls_back` |
| R16 | `type_classifier` 新增 `_as_string_sequence`，axes 非序列不再 TypeError/字符展开 | — |
| R17 | `failure_episode`：`missing_fields` 非序列归一化为空元组 | — |
| R18 | `failure_episode`：`trigger_source.value`/`error_type.value` 改 `_enum_value` 防御 | — |
| R19 | `genome.validate_edit` 不再改写调用方 `edit.operation`（副作用消除） | `test_r19_validate_edit_keeps_operation_type` |
| R20 | L2 `finish()` 直接写 `gate._consecutive_no_edit`/`_converged` 改为 `gate.reset_convergence()` | — |
| R21 | `_candidate_rows` 增加 `prosecution`/`defense` 旧键回退 | `test_r21_legacy_keys_fall_back` |
| R22 | `resolve_evolution_state` 顶层 legacy state token 经 `_LEGACY_INCIDENT_STATE_MAP` 归一化，不再 raise | `test_r22_legacy_top_level_state_normalizes` |
| R23 | rejected 规则回显提取为模块级 `_sanitize_rejected_content_echo`，抑制角色改写片段 | `test_r23_rejected_echo_suppresses_role_override` |
| R24 | **恢复原行为**（测试锁定 `gold_step_count` 允许披露，`test_evidence_summary_does_not_leak_gold_polarity_or_step_values` 明确断言 count 存在）；注释补充"count 允许、值禁止"的边界 | — |
| R25 | **保持现状**：`bool(row["gold_label"])` 前已有 `isinstance` 守卫（非 bool 直接 raise，fail-closed 特性而非 bug），不修改 | — |
| R26 | `skill_integration` 注入点过滤角色改写指令前缀（ignore previous/system prompt/you are now 等），命中即跳过该规则 | — |
| R27 | `evolution/__main__.py` 新增 `--pro-mutation-route` CLI 开关（对齐 `run_evolution` 已支持的参数） | — |
| R28 | `detector/batch.py` 新增 `_detect_with_transient_retry`（2 次退避重试 timeout/429/connection；确定性失败不重试） | `test_r28_batch_retries_transient_failure` / `test_r28_batch_does_not_retry_deterministic_failure` |
| R29 | `env_loader.py`：剥离未加引号值的行内注释；空 .env 不再遮蔽上层（持续扫描直到找到有内容的候选） | `test_r29_inline_comment_stripped` / `test_r29_empty_env_does_not_shadow_parent` |

### 未执行（P3 清理，R30–R40）

死代码/诊断指标/未接线模块（curriculum.py、step_attributor LLM 归因残留、fitness 不可达分支、proof_engine 诊断指标语义、type_conditioned_evolution 未接线函数、provider_usage 接线、旧版本 contract 死代码等）。建议与功能修复分批独立提交，不混入。**G1/G2/G3/G4/G5 五个机制缺口不在本清单 40 项内（属于上一轮文档 `SEHD_V6_FRAMEWORK_TECHNICAL_REVIEW.md` §7 的框架级缺口），仍待框架级改造。**
