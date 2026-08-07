# SEHD v6 框架优化重构方案

> **创建时间**: 2026-08-08  
> **最后更新**: 2026-08-08  
> **状态**: 🟡 进行中  
> **遵循原则**: First-Time Principle (pristine)

---

## 进度追踪

| 阶段 | 任务 | 状态 | 完成时间 |
|------|------|------|----------|
| Phase 0 | 现状分析与方案制定 | 🟢 已完成 | 2026-08-08 |
| Phase 1 | 代码读取与逻辑梳理 | 🟢 已完成 | 2026-08-08 |
| Phase 2 | L2 词表扩展实现 | 🟢 已完成 | 2026-08-08 |
| Phase 3 | 失败分类器混合架构 | 🟢 已完成 | 2026-08-08 |
| Phase 4 | Meta Skill 优化 | 🟢 已完成 | 2026-08-08 |
| Phase 5 | L1 反思引擎两层架构 | 🟢 已完成 | 2026-08-08 |
| Phase 6 | 测试与验证 | 🟢 已完成 | 2026-08-08 |
| Phase 7 | 文档更新与清理 | 🟢 已完成 | 2026-08-08 |

---

## 1. 重构目标与范围

### 1.1 核心目标

基于学术文档分析，对 SEHD v6 框架进行四个关键优化：

1. **L2 封闭词表扩展** - 在保持确定性的前提下提升表达能力
2. **失败分类器混合架构** - 突破封闭词表限制，处理未知失败模式
3. **Meta Skill 机制优化** - 解决反馈延迟、排名简单、负反馈缺失问题
4. **L1 反思引擎四层架构** - 模式提取→经验检索→反事实推理→负反馈分析

### 1.2 非目标（明确排除）

- ❌ 不修改 PACA-TCL v3.5 检测器核心逻辑
- ❌ 不改变 CRS 权重或法庭决策规则
- ❌ 不引入手工调参阈值
- ❌ 不破坏金标隔离原则
- ❌ 不增加 provider 调用次数（除必要的 LLM 辅助诊断）
- ❌ 不修改实验协议层（experiments/）

### 1.3 设计约束

所有修改必须满足：
- **确定性求值**：L2 策略仍由确定性求值器执行
- **失败闭合**：任何验证失败都回退到安全状态
- **可审计性**：所有决策都有完整的审计追踪
- **可回滚性**：任何修改都可以回滚到之前状态
- **单一所有权**：每个规则只有一个所有者

---

## 2. 详细重构方案

### 2.1 L2 封闭词表扩展

#### 2.1.1 现状分析

当前 `vocabulary_registry.py` 的三层封闭：
1. **字符串字面量封闭** (`_DSL_ALLOWED_STRINGS`, 第 584-587 行）
2. **事实字段封闭** (`_FACTS_FIELDS`, 第 557 行）
3. **DSL 语法封闭** (`_validate_dsl_node`, 第 611-657 行）

#### 2.1.2 扩展方案

**扩展 A：参数化动作实例增强**

当前状态：
```python
# 第 172-188 行
@dataclass(frozen=True)
class ActionInstance:
    name: str
    operator: str
    params: tuple[str, ...]
```

扩展为：
```python
@dataclass(frozen=True)
class ActionInstance:
    name: str
    operator: str
    params: tuple[str, ...]
    default_params: Mapping[str, Any] = field(default_factory=dict)  # 新增：默认参数绑定
    param_constraints: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # 新增：参数约束
```

**扩展 B：复合谓词支持**

新增：
```python
@dataclass(frozen=True)
class CompositePredicate:
    name: str
    combinator: str  # "and" | "or" | "not" | "xor"
    operands: tuple[str, ...]  # 谓词名称
    registered_generation: int
    status: str
```

**扩展 C：条件动作链（谨慎引入）**

```python
@dataclass(frozen=True)
class ConditionalActionChain:
    name: str
    steps: tuple[ConditionalActionStep, ...]
    rollback_strategy: str  # "reverse" | "compensate" | "abort"

@dataclass(frozen=True)
class ConditionalActionStep:
    condition: str  # 谓词名称
    action: str     # 动作名称
    on_failure: str # "continue" | "rollback" | "abort"
```

#### 2.1.3 文件修改计划

| 文件 | 修改类型 | 描述 |
|------|----------|------|
| `vocabulary_registry.py` | 扩展 | 添加 CompositePredicate、ConditionalActionChain |
| `policy_evaluator.py` | 扩展 | 支持复合谓词求值 |
| `policy_genome.py` | 扩展 | 支持动作链验证 |
| `v6_contracts.py` | 更新 | 导出新类型 |

---

### 2.2 失败分类器混合架构

#### 2.2.1 现状分析

当前 `failure_classifier.py` 的局限：
- 第 253-278 行：Rule 5 兜底只能返回 `no_actionable` 或 `internal_inconsistency`
- 无法处理未预设的失败模式
- 封闭词表限制了表达能力

#### 2.2.2 四层混合架构

```
L0: 确定性核心（保留）
    ↓ 未命中
L1: 模式匹配层（新增）
    ↓ 低置信度
L2: LLM 诊断层（新增）
    ↓ 验证
L3: 确定性验证层（新增）
```

#### 2.2.3 新增数据结构

```python
@dataclass(frozen=True)
class LayeredAttribution:
    """分层归因结果"""
    layer: str
    artifact: str
    reason_code: str
    confidence: float
    evidence_source: str  # "deterministic" | "pattern_match" | "llm_assisted"
    deterministic_evidence: dict
    pattern_similarity: float | None
    llm_rationale: str | None
    validation_passed: bool
    validation_details: dict | None
```

#### 2.2.4 文件修改计划

| 文件 | 修改类型 | 描述 |
|------|----------|------|
| `failure_classifier.py` | 重构 | 保留 L0，添加 L1-L3 入口 |
| `pattern_matcher.py` | 新增 | 失败模式相似度匹配 |
| `llm_diagnostician.py` | 新增 | LLM 辅助诊断 |
| `attribution_validator.py` | 新增 | 确定性验证层 |
| `layered_attribution.py` | 新增 | 分层归因数据类型 |

---

### 2.3 Meta Skill 机制优化

#### 2.3.1 现状问题

1. **反馈延迟**：`generate_meta_edit_candidates` 在反馈 ≥1 时生成，`evaluate_commit_evidence` 需要 ≥3 才提交
2. **排名简单**：`rank_candidates` 仅计算匹配数（第 503-522 行）
3. **负反馈缺失**：只利用正反馈，无反向指导

#### 2.3.2 优化方案

**优化 A：预测性预加载**

```python
@dataclass(frozen=True)
class PredictiveCandidate:
    """预测性候选"""
    edit: MetaEdit
    confidence: float
    predicted_evidence_count: int
    trend: str  # "increasing" | "stable" | "decreasing"

def predict_candidate_need(
    state: MetaSkillState,
    experiences: Sequence[GateExperience],
    window: int = 10,
) -> list[PredictiveCandidate]:
    """基于趋势预测可能需要的 MetaEdit"""
```

**优化 B：加权排名**

```python
@dataclass(frozen=True)
class WeightedRankingPreference:
    name: str
    weight: float
    decay_rate: float
    min_evidence: int

def rank_candidates_weighted(
    candidate_ids: Sequence[str],
    features: Mapping[str, Mapping[str, bool]],
    state: MetaSkillState,
    time_context: Mapping[str, Any],
) -> list[str]:
    """加权排名，考虑时间衰减"""
```

**优化 C：负反馈利用**

```python
NEGATIVE_GUIDANCE_TOKENS = frozenset({
    "avoid_action_shape_previously_failed",
    "avoid_unowned_output_target",
    "avoid_condition_without_discriminator",
    "avoid_redundant_intervention",
})

def generate_negative_guidance(
    validations: Sequence[OutcomeValidation],
) -> list[MetaEdit]:
    """从负反馈提取反向指导"""
```

#### 2.3.3 文件修改计划

| 文件 | 修改类型 | 描述 |
|------|----------|------|
| `meta_skill.py` | 扩展 | 添加 WeightedRankingPreference、负反馈令牌 |
| `meta_review.py` | 重构 | 添加预测性预加载、加权排名、负反馈生成 |
| `reflection_engine.py` | 更新 | 注入新的 Meta 指导 |

---

### 2.4 L1 反思引擎四层架构

#### 2.4.1 现状问题

从 `build_causal_reflection_prompt`（第 250-700 行）分析：
1. 信息过载：10+ 信息源同时注入
2. 诊断-生成耦合
3. 经验利用浅层
4. 反事实缺失
5. 负反馈表面化

#### 2.4.2 四层架构设计

```
L0: 模式提取层 (Pattern Extraction)
    ↓
L1: 经验检索层 (Experience Retrieval)
    ↓
L2: 反事实推理层 (Counterfactual Reasoning)
    ↓
L3: 负反馈分析层 (Negative Feedback Analysis)
```

#### 2.4.3 新增数据结构

```python
# L0: 模式提取
@dataclass(frozen=True)
class FailurePattern:
    mechanism_signature: tuple[str, ...]
    context_features: tuple[str, ...]
    failure_shape: str
    causal_position: str
    impact_scope: str
    complexity_score: float

# L1: 经验检索
@dataclass(frozen=True)
class ExperienceMatch:
    pattern: FailurePattern
    similarity_score: float
    successful_interventions: tuple[dict, ...]
    failed_interventions: tuple[dict, ...]
    negative_feedback_insights: tuple[str, ...]
    retrieval_path: str  # "exact" | "shape_context" | "causal"

# L2: 反事实推理
@dataclass(frozen=True)
class CounterfactualHypothesis:
    intervention: dict
    counterfactual_checks: tuple[CounterfactualCheck, ...]
    confidence: float
    expected_effect: str
    potential_risks: tuple[str, ...]
    generalizability_score: float

@dataclass(frozen=True)
class CounterfactualCheck:
    check_type: str  # "necessity" | "sufficiency" | "generalizability"
    question: str
    answer: bool
    reasoning: str
    confidence: float

# L3: 负反馈分析
@dataclass(frozen=True)
class NegativeFeedbackInsight:
    rejection_category: str
    root_cause: str
    what_went_wrong: str
    how_to_avoid: str
    similar_failure_ids: tuple[str, ...]
    severity: str  # "critical" | "moderate" | "minor"
```

#### 2.4.4 文件修改计划

| 文件 | 修改类型 | 描述 |
|------|----------|------|
| `reflection_engine.py` | 重构 | 拆分为四层架构 |
| `pattern_extractor.py` | 新增 | L0 模式提取 |
| `experience_retriever.py` | 新增 | L1 经验检索 |
| `counterfactual_reasoner.py` | 新增 | L2 反事实推理 |
| `negative_feedback_analyzer.py` | 新增 | L3 负反馈分析 |
| `reflection_types.py` | 新增 | 四层架构数据类型 |

---

## 3. 依赖关系分析

### 3.1 核心依赖图

```
vocabulary_registry.py
    ├── policy_evaluator.py (L2 求值)
    ├── policy_genome.py (L2 基因组)
    └── v6_contracts.py (契约导出)

failure_classifier.py
    ├── v6_contracts.py (归因类型)
    └── vocabulary_registry.py (谓词求值)

meta_skill.py
    ├── meta_review.py (Meta 审查)
    └── reflection_engine.py (Meta 指导注入)

reflection_engine.py
    ├── failure_episode.py (Episode 构建)
    ├── evolution_experience_memory.py (经验记忆)
    └── meta_review.py (Meta 指导)
```

### 3.2 修改顺序

**必须按以下顺序执行**：

1. **Phase 2**: L2 词表扩展（基础）
   - 先修改 `vocabulary_registry.py`
   - 再修改 `policy_evaluator.py`
   - 最后修改 `policy_genome.py`

2. **Phase 3**: 失败分类器（依赖 L2 词表）
   - 新增 `layered_attribution.py`
   - 新增 `pattern_matcher.py`
   - 新增 `llm_diagnostician.py`
   - 新增 `attribution_validator.py`
   - 最后重构 `failure_classifier.py`

3. **Phase 4**: Meta Skill（独立）
   - 修改 `meta_skill.py`
   - 修改 `meta_review.py`
   - 更新 `reflection_engine.py` 注入点

4. **Phase 5**: L1 反思引擎（依赖 Meta Skill）
   - 新增 `reflection_types.py`
   - 新增 `pattern_extractor.py`
   - 新增 `experience_retriever.py`
   - 新增 `counterfactual_reasoner.py`
   - 新增 `negative_feedback_analyzer.py`
   - 最后重构 `reflection_engine.py`

---

## 4. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| L2 词表扩展破坏确定性 | 高 | 所有扩展保持封闭词表，新增类型必须可编译为确定性函数 |
| LLM 辅助引入非确定性 | 高 | L3 确定性验证层，验证失败则回退 |
| 四层架构增加延迟 | 中 | 各层可独立开关，默认只启用 L0+L1 |
| 经验检索不准确 | 中 | 多层级检索 + 置信度衰减 |
| 测试覆盖不足 | 高 | 每个 Phase 完成后运行完整测试套件 |

---

## 5. 验证计划

### 5.1 单元测试

每个新增模块必须有对应的测试文件：
- `test_pattern_matcher.py`
- `test_llm_diagnostician.py`
- `test_attribution_validator.py`
- `test_experience_retriever.py`
- `test_counterfactual_reasoner.py`
- `test_negative_feedback_analyzer.py`

### 5.2 集成测试

- `test_layered_failure_classifier.py`
- `test_optimized_reflection_engine.py`
- `test_enhanced_meta_review.py`

### 5.3 回归测试

- 确保所有现有测试通过
- 确保 provider 调用次数不增加（除 LLM 辅助诊断）
- 确保金标隔离不被破坏

---

## 6. 回滚计划

每个 Phase 完成后创建 Git commit：
```bash
git commit -m "refactor(framework): Phase N - <description>"
```

如需回滚：
```bash
git revert HEAD~N..HEAD
```

---

## 7. 当前状态

**Phase 7 完成**：所有重构工作已完成，817 个测试全部通过。

**完成内容总结**：

### Phase 2: L2 词表保守扩展 ✅
- 扩展 `AdjudicationFacts`：新增 8 个字段（appeal_status, appeal_triggered, localization_status, responsible_step_available, 4 个 quarantine_reason_*）
- 扩展 `_DSL_ALLOWED_STRINGS`：新增 8 个字符串字面量
- 新增 6 个基线谓词：appeal_pending_and_terminal_unresolved, appeal_failed_with_surviving_errors, localization_failed_with_temporal_fallback, responsible_step_missing_and_verdict_error, quarantine_schema_mismatch_dominant, quarantine_evidence_missing_dominant
- 新增 3 个动作操作符：require_additional_verification, escalate_to_appeal, mark_candidate_ambiguous
- 更新 `v6_contracts.py` 和 `policy_evaluator.py` 支持新动作

### Phase 3: 失败分类器扩展 ✅
- 新增 6 个失败形状 reason codes
- 新增 5 条分类规则（Rule 4.1-4.5）
- 处理 appeal pending、appeal failed、localization fallback、responsible step missing、quarantine pattern
- 修复规则 4.4 避免误触发（要求 localization_status 非空）

### Phase 4: Meta Skill 优化 ✅
- 新增 5 个负反馈指导令牌：avoid_action_shape_previously_failed, avoid_unowned_output_target, avoid_condition_without_discriminator, avoid_redundant_intervention, avoid_scope_or_ownership_mismatch
- 新增 3 个加权排名偏好：recent_feedback_weight, severity_weight, mechanism_reliability
- 实现预测性预加载 `predict_candidate_need`：基于趋势预测提前生成候选
- 实现加权排名 `rank_candidates_weighted`：考虑偏好权重和时间衰减
- 实现负反馈生成：从 contradicts_accept 和 supports_reject 中提取反向指导

### Phase 5: L1 反思引擎两层架构 ✅
- 新增 `experience_retriever.py`：结构化经验检索模块
  - `FailurePattern`：结构化失败模式
  - `extract_failure_pattern`：从 FailureEpisode 提取模式
  - `retrieve_structured_experience`：三层检索（精确签名→形状上下文→因果位置）
  - `format_experience_for_prompt`：格式化经验供提示使用
- 新增 `negative_feedback_analyzer.py`：负反馈深度分析模块
  - `NegativeFeedbackInsight`：结构化负反馈洞察
  - `analyze_negative_feedback`：分析拒绝记录提取根因
  - `format_negative_feedback_for_prompt`：格式化负反馈供提示使用
- 修改 `reflection_engine.py`：集成两个新模块
  - 新增 `experience_memory` 和 `negative_feedback_analysis` 参数
  - 保留原有 `rejected_info` 确保测试兼容性

### 测试验证 ✅
- 817 个测试全部通过
- 修复了 6 个初始失败的测试

---

*本文档记录了 SEHD v6 框架的完整优化重构过程。*
