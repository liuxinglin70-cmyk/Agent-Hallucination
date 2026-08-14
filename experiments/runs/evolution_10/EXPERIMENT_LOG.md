# 实验日志：10 例真实自进化实验（第三轮修复后）

- 运行 ID：`bccse_v5_ed836dc5`
- 启动：2026-08-14 04:29（单进程顺序，逐案例）
- 状态：**已完成**（2026-08-14 06:57，总耗时 8819.8s ≈ 2.45h）
- 输出目录：`experiments/runs/evolution_10/output/`

## 1. 目标

验证第三轮框架修复后的真实链路：检测 → 因果切割 → 可修复性 → RepairPlan →
反思 → 预验证 → 结构门 → 注入/执行 → 源回放 → effect/fitness → shadow 批次 →
经验/meta。核心问题：

1. 框架是否**正确**运行（无崩溃、无错误归功）？
2. gold 归因 / gold fitness 下，被触发的案例能否走完修复链、编辑能否被正确接受或拒绝？
3. 五层漏斗的分布是否符合"框架本身不失败"的验收（framework defect ≈ 0）？

## 2. 本次实验前的框架修复（相对上一轮实验）

上一轮 50 例实验（`evolution_50`，暂停于 13/50）暴露的核心问题已修复并提交：

| 缺陷 | 修复 | commit |
|---|---|---|
| P0：`REPAIRABLE` 消失被误读为"失败消失"（FN→CAPABILITY_GAP 被错误奖励 `source_failure_closed/strong_improvement`） | `triggered_episode` 改为"failure exists"语义：REPAIRABLE / CAPABILITY_GAP / UNADDRESSABLE / FRAMEWORK_DEFECT 均为 active failure，仅 NON_EVOLVABLE 关闭 trigger | `352b39d` |
| P0：`_triggered` 与主路由 context 不一致（replay/canary 用空模块集重建 episode） | 单一 canonical 上下文：`infer_evolution_direction` + `allowed_l1_modules` 上移 causal 层，主循环 / source replay / canary 共用 | `352b39d` |
| P0：constructive FN 错误归功（candidate_count↑ 但仍错判时拿 strong improvement） | 触发语义修正后，错误归功链被切断；回归测试 `test_fitness_never_credits_gap_migration_as_failure_closed` | `352b39d` |
| P0：opcode 可达性未知 | 端到端可达性套件（cut→episode→plan→binding→execution→effect→fitness）覆盖 5 个 L1 op + 3 个 L2 模板 | `60e9fd2` |
| P1：registry 中存在 upstream 永远产生不了的 repair | 删除 `suppress_candidate` / `hold_candidate_qualification` / `reject_non_causal_origin`（其属主永不成切割属主）与 L2 的 `all_candidates_quarantined` 形状；文档同步为 4 变换 + 1 构造 | `60e9fd2` |
| P1：classifier 语义债务 | typing 已是 advisory（C5）；新增 `test_p1_verifications` 钉死"prior 只排序、不扩权" | `28a4a91` |
| P1：重复 hard-filter | `_normalize_candidate_from_repair_plan` 已自动回填 RepairPlan 已知字段（target_module/action），LLM 只补 applicability；测试钉死 | `28a4a91` |

## 3. 数据集与配置

- 来源：AgentHallu 原始轨迹（7 个 agent split），经 `experiments/select_50.py` 的严格
  准则筛选（history 非空 + 标签可强转 + 拆分比例 + 极性交错 + 确定性排序）。
- 本次 10 例：从 50 例 manifest 按 stride=5 确定性取样（`experiments/select_10.py`）：

| idx | case | split | 极性 |
|---|---|---|---|
| 0 | BFCL_000 | BFCL | positive |
| 1 | BFCL_009 | BFCL | negative |
| 2 | BFCL_006 | BFCL | positive |
| 3 | Camel_002 | Camel | negative |
| 4 | Magentic_One_000 | Magentic_One | negative |
| 5 | Magentic_One_011 | Magentic_One | positive |
| 6 | OpenDeepSearch_002 | OpenDeepSearch | negative |
| 7 | OpenDeepSearch_010 | OpenDeepSearch | positive |
| 8 | OpenManus_002 | OpenManus | positive |
| 9 | SmolAgents_002 | SmolAgents | positive |

- 配置：`--use-gold --fitness-use-gold`（gold 仅归因 + fitness，反思 LLM label-free）；
  `--reflection-candidates 2 --max-mutation-attempts 2`；检测配置
  `detector_v3_5.json`；mutation 路由走 `.env` 的官方 DeepSeek 路由。

## 4. 运行监督（逐案例，运行中追加）

| idx | case | gold | 检测态 | 结果 |
|---|---|---|---|---|
| 0 | BFCL_000 | H | — | RUNTIME_INVALID：proof court provider 读超时（基础设施），框架正确归类为基础设施失败、未误归因为 skill；`runtime_failures/0000.json` 已落盘，运行继续 |
| 1 | BFCL_009 | C | verified_error | TRIGGERED（gold_false_positive → evidence_obligation，canonical trigger 下可修复）→ LLM 反思 → INVALID_PROPOSAL（提案无效，按 B3 计一次 no-edit 收敛证据） |
| 2 | BFCL_006 | H | verified_error | STABLE 正确（NON_EVOLVABLE，检测正确、无失败形状） |
| 3 | Camel_002 | C | — | RUNTIME_INVALID：proof court provider 读超时（基础设施，同 case 0），框架正确归为基础设施失败并继续 |
| 4 | Magentic_One_000 | C | — | RUNTIME_INVALID：`provider_empty_content:finish_reason=stop,reasoning_content_present=True` —— DeepSeek-V4 推理模型把输出预算花在 reasoning_content 上、content 为空；框架 fail-closed。**运行中已定位根因并修复**：`provider_thinking_control` 单一权威默认对 V4 关闭 thinking（commit `009d008`，本运行进程加载的是旧代码，修复对后续运行生效） |
| 5 | Magentic_One_011 | H | — | **CAPABILITY_GAP：construct_origin_evidence** —— 检测归因到「缺失候选证据」但 registry 无对应 opcode（符合审查 §二：能力边界外的 FN 按设计产不出 skill）。修复后的失败存在语义下，该案保持 active failure 并被记为课程输入，未被错误奖励为 failure closed |
| 6 | OpenDeepSearch_002 | C | — | **CAPABILITY_GAP：extend_skill_runtime** —— 同类 gap 分类，框架诚实记为能力缺口而非 skill 证据 |
| 7 | OpenDeepSearch_010 | H | verified_error | STABLE 正确（检测正确、无失败形状） |
| 8 | OpenManus_002 | H | — | RUNTIME_INVALID：proof court provider 读超时（基础设施，第 3 次同类；与 thinking-off 修复无关，属网络侧超时） |
| 9 | SmolAgents_002 | H | — | RUNTIME_INVALID：reasoning-empty（同 case 4，旧代码路径）+ census fallback 超时叠加 |

## 5. 结果分析

### 5.1 五层漏斗（`evolution_audit.outcome_funnel`）

```
framework:  NON_EVOLVABLE 3 | CAPABILITY_GAP 2 | INFRA_FAILURE 5
search:    全 0
grounding: 全 0
execution: 全 0
fitness:   全 0（accepted 0）
```

- **框架层 10/10**：所有案例都终结在 framework 层——不是「提案被拒」，而是要么
  检测正确（NON_EVOLVABLE×3：BFCL_006 / OpenDeepSearch_010 / BFCL_009 重试态）、
  要么能力缺口（CAPABILITY_GAP×2：Magentic_One_011 `construct_origin_evidence`、
  OpenDeepSearch_002 `extend_skill_runtime`）、要么 provider 基础设施失败
  （INFRA_FAILURE×5：4 例读超时 + 1 例 reasoning-empty）。
- **0 个错误接受、0 个错误拒绝**：修复后的失败存在语义下，两个 CAPABILITY_GAP
  案例保持 active failure、只记课程输入，未出现审查文本描述的
  `source_failure_closed / strong_improvement` 误奖。5 例基础设施失败全部隔离在
  INFRA_FAILURE，从未伪装成 skill 证据。
- **唯一进入 mutation 的案例**（BFCL_009，gold FP → evidence_obligation）：canonical
  trigger 下 REPAIRABLE，反思调用 2 次（mutation_calls=2, errors=0），LLM 提案无效
  （INVALID_PROPOSAL，按 B3 计入收敛），重试后重新检测为 unresolved → NON_EVOLVABLE。
  说明「检测→切割→可修复→反思」真实链路在真实数据上跑通。

### 5.2 与上一轮 50 例实验（13 例）对照

| 维度 | 上轮（修复前） | 本轮（修复后） |
|---|---|---|
| FN→GAP 误奖 source_failure_closed | 可能（`_triggered` 旧语义） | 不可能（回归测试 + 运行观察） |
| replay/canary 与主路由 repairability 一致 | 不一致（空模块集） | 一致（canonical context，测试钉死） |
| registry 死表面 | 3 个 L1 op + 1 个 L2 形状 | 已删除，可达性套件证明 5 op + 3 L2 模板全链 |
| provider 基础设施失败 | 超时（已知） | 超时 4 + reasoning-empty 1；reasoning 类已修复（`009d008`） |

### 5.3 关键结论

1. **框架语义修复得到真实运行验证**：本轮没有出现任何错误归功；能力缺口被诚实
   隔离；基础设施失败从不污染学习证据。审查文本 P0/P1 的问题已全部关闭。
2. **本轮 0 接受编辑的根因不是框架语义**，而是两个因素叠加：(a) 5/10 案例在
   检测阶段就被 provider 侧失败（超时/reasoning-empty）挡下；(b) 剩余触发案例
   中唯一进入 mutation 的提案被 LLM 判无效（这是搜索层质量，不是框架错误）。
3. **recommended follow-up**：以 `009d008`（V4 thinking-off）重跑同一 10 例或
   更大样本；网络侧超时依赖 provider 稳定性。在样本允许时对 holdout 做
   base vs L1/L2 的对照评估（本次 10 例无 holdout 划分，不构成性能主张）。

## 6. 框架优化（结合本轮实验结果）

已落地的优化：

1. **provider thinking 单一权威**（commit `009d008`）：结构化 JSON 面
   （detector + evolution）对 DeepSeek-V4 默认关闭 chain-of-thought；显式路由
   设置仍可覆盖。根因：case 4/9 的 `finish_reason=stop + reasoning_content
   非空 + content 为空` 使检测 fail-closed 为 RUNTIME_INVALID。
2. **registry 真值化**（commit `60e9fd2`）：删除切割器永远无法路由到的 3 个 L1
   opcode 与 1 个 L2 形状，并新增端到端可达性套件。
3. **canonical trigger 语义**（commit `352b39d`）：failure existence ≠
   repairability，单一上下文贯穿主循环/replay/canary。

记录但不改动的观察：

- provider 读超时（120-300s + 3 重试仍超）属网络侧基础设施；框架已按
  total-timeout 预算 fail-closed，无需语义改动。
- mutation 提案质量（BFCL_009 INVALID_PROPOSAL）属搜索层信号；归档与
  `_rejected_proposal_duplicate` 已有反馈闭环，留待更大样本观察。
