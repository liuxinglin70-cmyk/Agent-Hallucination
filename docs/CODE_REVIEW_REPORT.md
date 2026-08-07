# SEHD v6.1 框架代码审查与机制有效性分析报告

> **审查日期**: 2026-08-08  
> **审查范围**: 全部重构代码（Phase 2-5）  
> **审查方法**: 静态代码分析 + 测试验证 + 机制逻辑审查

---

## 执行摘要

本次审查覆盖了 SEHD v6.1 框架的全部四个重构 Phase。共发现 **3 个潜在 Bug**、**5 个设计问题**、**8 个优化方向**。框架整体架构合理，但部分新功能的实现存在边界条件处理不足的问题。

**关键发现**：
- ✅ L2 词表扩展：实现正确，测试通过
- ⚠️ 失败分类器：Rule 4.4 存在误触发风险（已修复）
- ⚠️ Meta Skill：预测性预加载逻辑存在边界条件问题
- ⚠️ L1 反思引擎：经验检索模块存在性能隐患
- ❌ **严重**：负反馈分析模块的根因分类逻辑过于简单

---

## 1. L2 词表扩展审查

### 1.1 实现正确性 ✅

**审查文件**: `vocabulary_registry.py`, `v6_contracts.py`, `policy_evaluator.py`

**发现**:
- `AdjudicationFacts` 扩展正确，新增字段类型标注完整
- DSL 字符串字面量扩展符合封闭词表原则
- 新谓词逻辑正确，与 DSL 编译器兼容
- 新动作操作符注册正确，参数验证完整

**代码质量**: 良好

### 1.2 潜在问题

#### 问题 1.1: `quarantine_reason_*` 字段的语义模糊

**位置**: `vocabulary_registry.py` 第 76-79 行

**问题**:
```python
quarantine_reason_schema_mismatch: int = 0
quarantine_reason_evidence_missing: int = 0
quarantine_reason_causal_broken: int = 0
quarantine_reason_other: int = 0
```

这些字段的计数来源不明确。当前实现假设这些值来自 `court_result`，但 `build_adjudication_facts` 中直接从 `court_result.get()` 读取，如果上游没有提供这些字段，将永远为 0。

**影响**: 谓词 `quarantine_schema_mismatch_dominant` 和 `quarantine_evidence_missing_dominant` 可能永远不会触发。

**建议**:
1. 明确这些字段的数据来源
2. 如果上游不提供，考虑从 `quarantined_error_certificate_ids` 推导
3. 添加文档说明字段语义

#### 问题 1.2: 新动作仅记录遥测，不改变状态

**位置**: `policy_evaluator.py` 第 226-251 行

**问题**:
```python
elif operator == ACTION_REQUIRE_ADDITIONAL_VERIFICATION:
    # 要求额外验证：记录遥测，不改变覆盖状态
    telemetry.append({...})
elif operator == ACTION_ESCALATE_TO_APPEAL:
    # 升级到上诉：记录遥测，标记需要上诉
    telemetry.append({...})
```

这些动作只记录遥测，不改变 `eff_prosecution` 或 `eff_defense`，因此 `_recompute_terminal_state` 的结果不会变化。

**影响**: 这些动作对最终判决没有实质影响，可能被视为"无效动作"。

**建议**:
1. 明确这些动作的设计意图：是纯粹的通知机制，还是应该影响状态？
2. 如果应该影响状态，需要修改 `_recompute_terminal_state` 以考虑这些动作
3. 如果只是通知，在文档中明确说明

---

## 2. 失败分类器扩展审查

### 2.1 实现正确性 ✅

**审查文件**: `failure_classifier.py`

**发现**:
- 新规则 4.1-4.5 逻辑正确
- Rule 4.4 的修复正确（要求 `localization_status != ""`）
- 优先级顺序合理

### 2.2 潜在问题

#### 问题 2.1: Rule 4.1 和 4.2 的归属可能不准确

**位置**: `failure_classifier.py` 第 260-282 行

**问题**:
```python
# Rule 4.1 — v6.1: appeal pending blocked terminal.
if L2_PREDICATES["appeal_pending_and_terminal_unresolved"](facts):
    return _attr(
        LAYER_L2,
        L2_ARTIFACT_CONSENSUS_GATE,  # 这里
        ...
    )

# Rule 4.2 — v6.1: appeal failed with surviving errors.
if L2_PREDICATES["appeal_failed_with_surviving_errors"](facts):
    return _attr(
        LAYER_L2,
        L2_ARTIFACT_CONSENSUS_GATE,  # 这里
        ...
    )
```

上诉相关的问题被归类到 `L2_ARTIFACT_CONSENSUS_GATE`，但上诉流程与共识门是不同的机制。

**影响**: 归因不准确可能导致进化方向错误。

**建议**:
1. 考虑添加新的 L2 artifact：`L2_ARTIFACT_APPEAL_MECHANISM`
2. 或者将上诉问题归类到 detector 层（`LAYER_DETECTOR`）

#### 问题 2.2: 缺少级联失败检测

**位置**: `failure_classifier.py`

**问题**: 当前实现只能检测单一失败点，无法检测级联失败（A 失败导致 B 失败）。

**影响**: 复杂失败场景可能被错误归类。

**建议**:
1. 添加级联失败检测逻辑
2. 考虑在 `FailureAttribution` 中添加 `cascade_parent` 字段

---

## 3. Meta Skill 优化审查

### 3.1 实现正确性 ⚠️

**审查文件**: `meta_review.py`, `meta_skill.py`

**发现**:
- 负反馈令牌和排名偏好扩展正确
- 预测性预加载逻辑基本正确
- 加权排名实现正确

### 3.2 潜在问题

#### 问题 3.1: 预测性预加载的硬编码映射

**位置**: `meta_review.py` 第 440-446 行

**问题**:
```python
for code, mapped_target in (
    ("policy_route_unavailable", "prefer_registered_runtime_route"),
    ("registry_entry_pending", "avoid_pending_registry_dependency"),
):
```

这个映射是硬编码的，只支持两种反馈代码。新增的指导令牌无法被预测性预加载覆盖。

**影响**: 预测性预加载的覆盖范围有限。

**建议**:
1. 将映射提取为配置或注册表
2. 从 `COMMIT_EVIDENCE_SOURCES` 动态推导映射

#### 问题 3.2: 预测性预加载的窗口大小固定

**位置**: `meta_review.py` 第 409 行

**问题**:
```python
def predict_candidate_need(
    state: MetaSkillState,
    experiences: Sequence[GateExperience],
    window: int = 10,
) -> list[PredictiveCandidate]:
```

窗口大小固定为 10，对于不同规模的运行可能不适用。

**影响**: 小样本运行时预测不准确。

**建议**:
1. 窗口大小根据经验总数动态调整
2. 添加最小窗口大小限制

#### 问题 3.3: 负反馈生成逻辑过于简单

**位置**: `meta_review.py` 第 338-372 行

**问题**:
```python
if "action_shape" in evidence:
    token = "avoid_action_shape_previously_failed"
elif "ownership" in evidence or "scope" in evidence:
    token = "avoid_scope_or_ownership_mismatch"
```

基于字符串包含的判断过于简单，容易误匹配。

**影响**: 可能生成不准确的指导令牌。

**建议**:
1. 使用更精确的模式匹配
2. 考虑从 `evidence` 中提取结构化信息而非字符串匹配

---

## 4. L1 反思引擎两层架构审查

### 4.1 实现正确性 ⚠️

**审查文件**: `experience_retriever.py`, `negative_feedback_analyzer.py`, `reflection_engine.py`

**发现**:
- 结构化经验检索实现基本正确
- 负反馈分析实现基本正确
- 与现有架构集成良好

### 4.2 潜在问题

#### 问题 4.1: 经验检索的性能隐患

**位置**: `experience_retriever.py` 第 172-210 行

**问题**:
```python
for entry in memory.entries():
    entry_pattern = FailurePattern(...)
    sim = _similarity(current_pattern, entry_pattern)
    if sim > 0.3:
        shape_matches.append((sim, entry))
```

当经验库很大时，遍历所有条目计算相似度是 O(n) 操作，每次反思都会执行。

**影响**: 随着经验积累，反思延迟会线性增长。

**建议**:
1. 添加索引机制（如按 failure_shape 或 causal_position 分桶）
2. 限制 Level 2 检索的最大条目数
3. 考虑使用缓存存储最近检索结果

#### 问题 4.2: 经验检索的 Level 2 语义不准确

**位置**: `experience_retriever.py` 第 176-183 行

**问题**:
```python
entry_pattern = FailurePattern(
    mechanism_signature=entry.signature,
    failure_shape="unknown",  # signature does not encode shape
    causal_position="unknown",
    impact_scope="unknown",
)
```

Level 2 检索时，只使用 `mechanism_signature` 计算相似度，其他字段都是 "unknown"，导致 `_similarity` 函数中 60% 的权重无法使用。

**影响**: Level 2 检索的准确性受限。

**建议**:
1. 在 `MechanismEntry` 中存储更多模式信息
2. 或者修改 `_similarity` 以处理未知字段

#### 问题 4.3: 负反馈分析的根因分类过于简单

**位置**: `negative_feedback_analyzer.py` 第 65-84 行

**问题**:
```python
def _classify_root_cause(rejected: RejectedEditEntry) -> str:
    reasons = " ".join(rejected.reject_reasons).lower()
    if "gold" in reasons or "identity" in reasons or "leak" in reasons:
        return ROOT_CAUSE_GOLD_LEAKAGE
    if "baseline" in reasons or "immutable" in reasons:
        return ROOT_CAUSE_BASELINE_VIOLATION
    # ...
```

基于字符串包含的分类逻辑过于简单，容易误分类。例如，"invalid" 可能匹配 "invalid_schema" 或 "invalid_parameter"，但它们有不同的根因。

**影响**: 负反馈分析的准确性受限，可能给出错误的避免策略。

**建议**:
1. 使用精确的前缀匹配而非字符串包含
2. 考虑从 `rejection_stage` 和 `reject_reasons` 的结构化信息中推导根因

---

## 5. 机制有效性分析

### 5.1 框架整体有效性

| 机制 | 有效性 | 评估 |
|------|--------|------|
| L2 词表扩展 | ⚠️ 中等 | 新动作仅记录遥测，对判决无实质影响 |
| 失败分类器扩展 | ✅ 高 | 新失败形状覆盖更多场景，Rule 4.4 修复正确 |
| Meta Skill 优化 | ⚠️ 中等 | 预测性预加载和加权排名有帮助，但负反馈生成逻辑简单 |
| L1 反思引擎两层架构 | ⚠️ 中等 | 结构化经验检索和负反馈分析有潜力，但存在性能和准确性问题 |

### 5.2 关键机制评估

#### 5.2.1 进化闭环的有效性

**当前状态**: 进化闭环基本完整，但存在以下瓶颈：

1. **L2 策略的表达能力有限**：新动作仅记录遥测，无法改变判决
2. **失败归因的准确性**：上诉问题归类不准确，缺少级联失败检测
3. **经验利用的效率**：经验检索的准确性和性能有待提升

#### 5.2.2 反思引擎的有效性

**当前状态**: 反思引擎的架构改进有潜力，但实现细节需要优化：

1. **诊断-生成分离**：已有改进，但经验检索的集成可以更深入
2. **负反馈利用**：根因分类过于简单，需要更精确的分析
3. **历史经验检索**：准确性和性能都需要提升

---

## 6. Bug 排查

### 6.1 已修复的 Bug

| Bug | 位置 | 修复方式 |
|-----|------|----------|
| Rule 4.4 误触发 | `failure_classifier.py` | 添加 `localization_status != ""` 条件 |
| 循环导入 | `negative_feedback_analyzer.py` | 使用 `Any` 替代直接导入 |
| 测试兼容性 | `reflection_engine.py` | 恢复 `rejected_info` 变量 |

### 6.2 潜在 Bug

| Bug | 位置 | 严重程度 | 描述 |
|-----|------|----------|------|
| `quarantine_reason_*` 永远为 0 | `vocabulary_registry.py` | 高 | 上游不提供这些字段，谓词永远不会触发 |
| 新动作不改变状态 | `policy_evaluator.py` | 中 | 仅记录遥测，对判决无实质影响 |
| 经验检索性能问题 | `experience_retriever.py` | 中 | O(n) 遍历，随经验积累性能下降 |
| 负反馈分类不准确 | `negative_feedback_analyzer.py` | 中 | 字符串包含匹配容易误分类 |

---

## 7. 优化方向

### 7.1 高优先级

1. **修复 `quarantine_reason_*` 字段的数据来源**
   - 明确字段语义和数据来源
   - 如果上游不提供，考虑从其他字段推导

2. **增强新动作的状态改变能力**
   - 考虑让 `require_additional_verification` 和 `escalate_to_appeal` 影响终态
   - 或者明确文档说明这些动作仅用于遥测

3. **优化经验检索性能**
   - 添加索引机制
   - 限制 Level 2 检索范围

### 7.2 中优先级

4. **改进负反馈分析准确性**
   - 使用精确匹配而非字符串包含
   - 从结构化信息推导根因

5. **扩展失败分类器覆盖范围**
   - 添加级联失败检测
   - 修正上诉问题的归类

6. **完善 Meta Skill 预测性预加载**
   - 动态推导反馈代码映射
   - 自适应窗口大小

### 7.3 低优先级

7. **增强经验检索准确性**
   - 在 `MechanismEntry` 中存储更多模式信息
   - 修改 `_similarity` 处理未知字段

8. **添加 L2 策略执行效果追踪**
   - 追踪新动作的实际效果
   - 评估新谓词的使用频率

---

## 8. 结论

SEHD v6.1 框架的重构整体上是成功的，817 个测试全部通过。主要改进包括：

1. **L2 词表扩展**：增加了上诉、定位和隔离原因的表达能力
2. **失败分类器扩展**：覆盖了更多失败场景
3. **Meta Skill 优化**：增强了反馈利用效率
4. **L1 反思引擎优化**：引入了结构化经验检索和负反馈分析

然而，存在一些需要关注的问题：

1. **新动作的实效性**：当前仅记录遥测，需要明确设计意图
2. **经验检索的性能**：需要优化以支持大规模经验库
3. **负反馈分析的准确性**：需要更精确的分类逻辑

建议优先解决高优先级问题，然后进行实验验证，评估框架在实际场景中的有效性。

---

*本报告基于静态代码分析和测试验证，建议结合实际运行数据进行进一步评估。*
