# 实验日志：第二轮 10 例真实自进化实验（冗余修复后）

- 状态：**已完成**（总耗时 701.3s ≈ 12 分钟；首轮 8819.8s ≈ 2.45h，提速约 12.6×）
- 输出目录：`experiments/runs/evolution_10b/output/`

## 1. 本轮前置修复（相对第一轮实验）

| 缺陷（第一轮观察） | 根因 | 修复 | commit |
|---|---|---|---|
| INFRA_FAILURE×5（4 超时 + 1 reasoning-empty） | (a) V4 推理模型把输出预算花在 reasoning_content 上、content 为空；(b) 传输层每轮固定 120s 窗口 + 无退避预算，慢 provider 连挂 4 次 | (a) `provider_thinking_control` 单一权威：V4 在结构化 JSON 面默认关闭 thinking（第一轮运行中已修）；(b) 渐进超时窗口（growth factor + cap）+ 退避计入默认总预算 + **整案检测级重试一次**（传输层重试耗尽后再给一次完整检测机会） | `009d008`、`af72914` |
| CAPABILITY_GAP×2（construct_origin_evidence / extend_skill_runtime） | 同一根因：gold 正确判决推断出 `no_evolution` 方向，使「gold 正确但仍含过程缺陷」的切割（temporal-fallback 定位 / hollow 覆盖声明）无法编译任何 RepairPlan → 被误标为能力缺口 | `repair_direction`：actionable 切割上 `no_evolution → process_repair`，两个缺口形状均恢复可修复（回归测试钉死） | `1bf9636` |

## 2. 数据集与配置

- 第二组 10 例：50 例 manifest 按 stride=5、offset=1 确定性取样（与第一轮完全不相交）：
  BFCL_001/004/012、Camel_000、Magentic_One_001、Octotools_014（新 split）、
  OpenDeepSearch_003、OpenManus_011/008、SmolAgents_004；6 正 / 4 负。
- 配置同第一轮：`--use-gold --fitness-use-gold --reflection-candidates 2
  --max-mutation-attempts 2`；检测配置已带冗余预算
  （transport_retries 4 + growth 2.0 + cap 480 等）。

## 3. 运行监督（逐案例，运行中追加）

| idx | case | gold | 检测态 | 结果 |
|---|---|---|---|---|
| 0 | BFCL_001 | C | verified_clean | STABLE 正确（clean 正确检出；~2 分钟完成，thinking-off 生效） |
| 1 | BFCL_004 | H | — | **CAPABILITY_GAP：construct_certificate** —— gold-FN 但 candidate 已存在：证据/证书获取无 primitive（`keep_coverage_incomplete` 只能打开覆盖、不能产生 error 判决，声明该形状会在语义上错误）；这正是审查 §二「candidate 已存在但 evidence obligation 错」的诚实边界，记课程输入 |
| 2 | BFCL_012 | C | verified_clean | STABLE 正确 |
| 3 | Camel_000 | H | — | **CAPABILITY_GAP：construct_certificate** —— 同 case 1 家族（candidate 存在但证据链缺失） |
| 4 | Magentic_One_001 | H | verified_error | STABLE 正确（幻觉正确检出） |
| 5 | Octotools_014 | H | — | **CAPABILITY_GAP：construct_certificate** —— 同家族 |
| 6 | OpenDeepSearch_003 | H | verified_error | STABLE 正确 |
| 7 | OpenManus_011 | H | — | **CAPABILITY_GAP：construct_certificate** —— 同家族（第 4 例） |
| 8 | OpenManus_008 | C | verified_error | TRIGGERED（gold FP → evidence_obligation）→ LLM 反思 → 提案被预验证拒绝：`condition_feature_not_bindable_in_domain:coverage:candidate_kind`（模型对 coverage 绑定域写了不可绑定的 candidate_kind 特征；框架 fail-closed + 重试反馈）→ 重试重新检测为 verified_clean → NON_EVOLVABLE |
| 9 | SmolAgents_004 | C | verified_clean | STABLE 正确 |

## 4. 结果分析

### 4.1 五层漏斗

```
framework:  NON_EVOLVABLE 6 | CAPABILITY_GAP 4 | INFRA_FAILURE 0
search/grounding/execution/fitness: 全 0（accepted 0）
```

- **INFRA_FAILURE 5 → 0**：第一轮的全部基础设施失败消失。三层冗余各自生效：
  V4 thinking-off（根除 reasoning-empty 且使检测提速 ~12×）、渐进超时窗口 +
  退避计入预算、整案检测级重试。
- **CAPABILITY_GAP 2 → 4，但构成完全不同**：第一轮的两个缺口形状
  （`construct_origin_evidence` = gold 正确 + temporal-fallback 定位；
  `extend_skill_runtime` = gold 正确 + hollow 覆盖）已由 `repair_direction`
  修复（回归测试证明两者现在编译 REPAIRABLE）。本轮 4 例缺口全部是
  `construct_certificate` 家族——gold-FN 且 candidate 已存在、证据/证书获取
  无 primitive。这正是审查 §二描述的诚实能力边界：现有 op 无法产生 error
  判决所需的证据链，声明新形状会语义错误；不新增 primitive 则无法修复，
  已如实记为课程输入。
- **搜索层唯一一次 mutation**（OpenManus_008）：模型对 coverage 域写了不可
  绑定的 `candidate_kind` 特征。提示词已渲染域限定条件契约
  （`render_condition_spec_contract`，只列出可达域的可绑定特征），框架正确
  fail-closed 并以拒绝理由反馈重试。这是模型提案质量边界，不是框架接线错误；
  重试后重新检测为 clean → NON_EVOLVABLE，收敛记账正确。

### 4.2 结论

1. 两个目标全部达成：基础设施失败清零（含 12.6× 提速），两个可修复的
   能力缺口（gold 正确的过程缺陷）恢复可修复；不可修复的
   `construct_certificate` 家族如实保留为缺口。
2. 剩余 skill yield 瓶颈=搜索层提案质量（10 例中仅 1 例 gold-FP 触发进入
   mutation，且 condition 域特征写错）。框架侧已做到「LLM 只写
   applicability、框架拥有语义」的全部确定性保障（域契约渲染 + 预验证 +
   反馈重试）；提高提案质量属于实验/论文层面的下一步（如增大
   reflection-candidates、域特征示例增强），不动框架语义。
3. 建议下一轮：在更大样本（50 例全量）上运行以观察 REPAIRABLE→execution
   的真实比例；对 holdout 做 base vs L1/L2 对照。

## 5. 根因排查：为什么 10 例没有一例进化（设计边界 vs 机制错误）

逐案归因（来自 `evolution_result.json` 的 episode 快照）：

| 类别 | 案例 | 证据 | 判定 |
|---|---|---|---|
| NON_EVOLVABLE×6 | BFCL_001 / BFCL_012 / SmolAgents_004（gold=clean, 判决 clean）、Magentic_One_001 / OpenDeepSearch_003（gold=error, 判决 error）、OpenManus_008 重试（重检测 clean 正确） | gold 与判决一致 | **正确检测，本来就不该进化**——不是错误 |
| CAPABILITY_GAP×3 | BFCL_004 / Camel_000 / OpenManus_011：gold=error、判决 clean、candidate 已存在 | cut=evidence_obligation/gold_false_negative_clean；episode 合成字段只有 `candidate_trigger/anchor_id`（construct_candidate），owner 是 evidence_obligation → `rejected_frontier_fields: [evidence_obligation:anchor_id]` → 无 plan → GAP(construct_certificate) | **设计边界**：cut.py 注释明示「candidate 存在时 evidence/verifier 拥有缺失证书，repairability 报告不可用的构造 primitive 为 CAPABILITY_GAP」；权威分离禁止 Skill 写证据/证书。审查 §二原文确认这是「能力定义的必然结果」 |
| CAPABILITY_GAP×1 | Octotools_014：gold=error、判决 unresolved | cut=evidence_obligation/provider_artifact_malformed（provider_generation origin），failed fields（target_candidate…）无任何 op target | 同属设计边界（evidence_obligation 只可执行 coverage_state） |
| 真触发×1 | OpenManus_008（gold=clean, 判决 error） | 完整链路 trigger→plan→reflection→mutation→prevalidation→反馈→重试全部执行 | **管线通**；失败点是模型复述框架已知事实：`condition_feature_not_bindable_in_domain:coverage:candidate_kind`。跨轮同类：第一轮 BFCL_009 为 `rule_spec:action_target_required:keep_coverage_incomplete`（漏写框架已知的 `: coverage_state` target） |

结论：**0/10 进化 = 三个因素叠加，无一是管线断裂**——(1) 60% 检测正确（不该进化）；(2) 40% FN 全部落在权威分离的设计边界内（construct_certificate 缺口是设计使然，不是机制错）；(3) 唯一可修复形状（gold-FP）在样本中仅 1 例，其 mutation 提案被模型复述错误杀死。

机制修复（commit `2991fa6`）：唯一 RepairPlan 时框架确定性回填全部已知字段——缺失的 repair_edge_id/target_module/opcode/output_field/action 由 plan 派生，模型「漏写 target」的 action（`keep_coverage_incomplete`）被修复为 `keep_coverage_incomplete: coverage_state`。第一类模型错误从此被框架吸收；第二类（condition 特征本身）是模型学 applicability 的职责，框架已 fail-closed + 反馈重试。

样本层面：两轮 20 例中 candidate-missing FN（construct_candidate 唯一覆盖的 clean→error 形状）出现 0 次——AgentHallu 的 FN 多为 evidence/verifier 失败。要观察到 accepted skill，需分层采样含可修复形状的案例（candidate-missing FN、gold-FP），或对这类案例做定向实验；否则 0-accept 是样本构成的预期结果，不构成「框架不能进化」的证据。
