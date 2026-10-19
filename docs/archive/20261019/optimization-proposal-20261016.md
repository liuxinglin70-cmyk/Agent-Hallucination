# 检测器优化实施方案

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

**日期**: 2026-10-16  
**基于**: 代码审查报告 + 架构分析文档  
**目标**: 提升证据转换效率，减少unknown比例，改善定位精度

---

## 📊 当前瓶颈数据（历史基线）

根据修复前的实验数据：
- **预测使用率**: 43/100例使用模型预测
- **有效比较率**: 8/43（18.6%）非unknown
  - matched: 5例
  - divergent: 3例
  - unknown: 35例（81.4%）
- **净增益**: 检测0、定位0
- **成本增加**: 请求+7.7%、时延+22.7%

**核心问题**: 预测生成了约束，但大部分在比较阶段变成unknown，无法进入证据链

---

## 🎯 优化目标

### 量化目标
1. **有效比较率**: 从18.6%提升到>40%（unknown从35→<26）
2. **候选转换率**: 有效偏离→Recall候选转换率达到80%
3. **净增益**: 在相同成本下，检测+定位综合提升>5个百分点

### 质量目标
- 保持现有测试全部通过
- 不降低正常样本的保护能力
- 清晰的失败原因分类，便于调试

---

## 🔧 方案一：增强约束验证能力（高优先级）

### 问题根因
当前`action_forecast.py:114-121`仅验证引用文本存在：
```python
error = packet_quote_error(prefix, quote, locus=ref, evidence_refs=[ref])
if error:
    raise ValueError("预测引用不属于可见前缀原文")
```

**缺失**：
- 不验证约束是否适用于当前任务
- 不区分引用来源的可信度（工具返回 vs 模型记忆）
- 不检查实体/单位/时间绑定

### 实施方案

#### 1.1 扩展约束Schema（低风险）

**文件**: `detector/action_forecast.py`

**新增字段**:
```python
FORECAST_SCHEMA = "detector.action_forecast.v3"  # 升级版本

# 在_constraints函数中增加验证
row["applicability"] = "established"  # established/conditional/unknown
row["source_credibility"] = "task_requirement"  # 新增字段
# task_requirement / tool_return / external_assertion / model_memory
row["entity_binding"] = None  # 可选：实体名称
row["unit_binding"] = None    # 可选：单位
```

**验证逻辑**:
```python
def _validate_constraint_applicability(constraint, packet, prefix):
    """验证约束是否真正适用"""
    ref = constraint["evidence_ref"]
    
    # 检查来源可信度
    if ref == "question":
        credibility = "task_requirement"
        applicability = "established"
    elif ref.endswith("_tool_01") or "_tool_" in ref:
        # 工具返回需要进一步检查
        tool_obs = _get_tool_observation(packet, ref)
        if observation_failed(tool_obs):
            credibility = "unreliable_source"
            applicability = "conditional"
        else:
            credibility = "tool_return"
            applicability = "established"
    else:
        # 纯内容引用，可能是模型记忆
        credibility = "model_memory"
        applicability = "conditional"
    
    return applicability, credibility
```

**实施难度**: ⭐⭐ (2/5)
- 现有结构支持扩展
- 向后兼容（新字段可选）
- 需要约10个新测试覆盖各类来源

**预期收益**: 减少15-20%的无效约束生成

---

#### 1.2 实现小范围义务核验器（中风险）

**文件**: 新建 `detector/action_obligation.py`

**复用现有能力**:
```python
from detector.predicates import run_predicate
from detector.predicate_dsl import parse_dsl_program

def verify_task_obligation(constraint, packet, task_question):
    """验证约束是否对应真实任务义务"""
    
    # 1. 提取任务中的固定字段要求
    fixed_fields = extract_fixed_fields(task_question)
    
    # 2. 提取单位和数值要求
    numeric_requirements = extract_numeric_requirements(task_question)
    
    # 3. 提取工具使用顺序要求
    tool_sequence = extract_tool_sequence_constraints(task_question)
    
    # 4. 匹配约束到具体义务
    if constraint["requirement"] matches fixed_fields:
        return {"verified": True, "obligation_type": "fixed_field"}
    elif constraint["requirement"] matches numeric_requirements:
        return {"verified": True, "obligation_type": "numeric_constraint"}
    # ... 更多匹配规则
    
    return {"verified": False, "reason": "no_matching_obligation"}
```

**支持的义务类型**（从简单到复杂）:
1. ✅ **固定字段值**：如 "fuelAmount must be 7.93 gallon"
2. ✅ **数值单位**：如 "convert to gallon, keep 2 decimal places"
3. ⚠️ **工具失败恢复**：如 "retry on failure or report honestly"
4. ❌ **复杂前置条件**：需要工具契约（暂不支持）

**实施难度**: ⭐⭐⭐ (3/5)
- 需要设计新的义务提取器
- 需要20-30个测试覆盖各类义务
- 可能与现有predicates有功能重叠

**预期收益**: 进一步减少10-15%的无效约束

---

### 1.3 改进比较失败处理（低风险）

**问题**: 当前32条unknown统一标记为`missing_or_invalid`，无法细分

**文件**: `detector/action_forecast.py:action_comparisons`

**改进**:
```python
# 当前代码
row["validation_state"] = "missing"

# 改进为细分状态
if len(matches) == 0:
    row["validation_state"] = "comparison_missing"
    row["failure_reason"] = "model_did_not_provide_comparison"
elif len(matches) > 1:
    row["validation_state"] = "comparison_duplicate"
    row["failure_reason"] = "multiple_comparisons_for_same_constraint"
elif not isinstance(quote, str) or not quote.strip():
    row["validation_state"] = "quote_missing"
    row["failure_reason"] = "comparison_lacks_required_quote"
elif quote not in actual_action_text:
    row["validation_state"] = "quote_mismatch"
    row["failure_reason"] = "quote_not_found_in_actual_action"
    row["rejected_quote"] = quote[:200]
elif refs not in packet events:
    row["validation_state"] = "reference_invalid"
    row["failure_reason"] = "evidence_refs_out_of_range"
# ... 更多细分
```

**实施难度**: ⭐ (1/5)
- 纯逻辑拆分，不改变现有行为
- 5-10个测试验证分类正确性

**预期收益**: 改善可调试性，为后续优化提供数据支持

---

## 🔧 方案二：完善证据传递链路（中优先级）

### 问题根因
根据架构分析，本轮20个漏检中：
- 10例：Judge阴性且未触发Recall
- 3例：Recall运行但没有候选
- 6例：有候选但未通过Upgrade

**关键瓶颈**: 偏离→Recall候选的转换

### 实施方案

#### 2.1 确保偏离自动进入Recall（中风险）

**文件**: `detector/action_evidence.py`

**当前逻辑**:
```python
def divergence_to_recall_candidate(comparisons, forecast, packet):
    """将达到quorum的偏离转为Recall候选"""
    # 当前实现已存在，但需要验证完整性
```

**改进点**:
1. 确保所有`state=divergent`且`validation_state=valid`的比较都生成候选
2. 候选携带完整的`constraint_id`、`source_event_id`、`next_event_id`
3. 候选的`claim`字段清晰描述偏离内容

**新增验证**:
```python
def test_all_valid_divergences_generate_recall_candidates():
    """确保所有有效偏离都进入候选队列"""
    comparisons = [
        {"state": "divergent", "validation_state": "valid", ...},
        {"state": "divergent", "validation_state": "quote_mismatch", ...},
        {"state": "matched", "validation_state": "valid", ...},
    ]
    candidates = divergence_to_recall_candidate(comparisons, ...)
    # 应该只有第一个进入候选
    assert len(candidates) == 1
    assert candidates[0]["claim"] contains "constraint requirement"
    assert candidates[0]["source_id"] == "constraint_xxx"
```

**实施难度**: ⭐⭐⭐ (3/5)
- 需要理解现有Recall候选生成逻辑
- 需要确保不与其他候选来源冲突
- 需要15-20个测试覆盖各类场景

**预期收益**: 减少3个"有偏离但无候选"的漏检

---

#### 2.2 改进Upgrade的证据门（中风险）

**问题**: 根据案例分析，Upgrade拒绝的主要原因：
- "缺少佐证不能证明事实错误"
- "工具确实返回了该值"
- "没有外部快照"

**文件**: `detector/recaller.py:run_causal_upgrade`

**现有逻辑保持不变**，但增加诊断信息：
```python
def run_causal_upgrade(...):
    # ... 现有逻辑 ...
    
    for candidate in candidates:
        # 记录详细的拒绝原因
        if not content_error_established:
            candidate["upgrade_rejection"] = {
                "reason": "content_error_unestablished",
                "details": "requires_independent_evidence" if ... 
                          else "observation_matches_claim"
            }
        elif not material_to_task:
            candidate["upgrade_rejection"] = {
                "reason": "not_material_to_delivery",
                "details": "error_not_in_delivery_path" if ...
                          else "corrected_before_delivery"
            }
```

**不改变升级逻辑**，只增强诊断能力，为未来优化提供数据。

**实施难度**: ⭐⭐ (2/5)
- 不改变现有判断逻辑
- 仅增加诊断字段
- 5-10个测试验证诊断信息正确性

**预期收益**: 为未来针对性优化Upgrade提供数据基础

---

## 🔧 方案三：细化来源定位（低优先级）

### 问题根因
当前只有单一`responsible_step`，无法区分：
- 错误首次出现的**来源**（origin）
- 助手首次采纳的**采纳点**（adoption）
- 最终出现在答案的**交付点**（delivery）

### 实施方案

#### 3.1 扩展定位结果结构（低风险）

**文件**: `detector/step_tracer.py`

**新增字段**:
```python
localization = {
    "target": "dataset_first_error_event.v1",
    "responsible_step": 2,  # 保持向后兼容
    "origin_step": 2,       # 新增：首次来源
    "adoption_step": 3,     # 新增：首次采纳
    "delivery_step": 4,     # 新增：最终交付
    "lineage_established": True,  # 是否建立完整链路
}
```

**验证逻辑**:
```python
def trace_error_lineage(packet, error_claim, tracer_result):
    """追踪同一错误的完整生命周期"""
    
    # 1. 找到首次来源（可能在工具观察中）
    origin = find_first_occurrence(packet, error_claim)
    
    # 2. 找到首次采纳（助手首次使用该错误值）
    adoption = find_first_assistant_adoption(packet, error_claim, after=origin)
    
    # 3. 找到最终交付（错误值出现在答案中）
    delivery = find_delivery_point(packet, error_claim)
    
    # 4. 验证链路完整性
    if all([origin, adoption, delivery]) and origin <= adoption <= delivery:
        return {"origin": origin, "adoption": adoption, "delivery": delivery,
                "lineage_established": True}
    else:
        return {"origin": origin, "adoption": adoption, "delivery": delivery,
                "lineage_established": False}
```

**实施难度**: ⭐⭐⭐ (3/5)
- 需要实现错误值的跨事件追踪
- 需要处理值变换（如单位转换）
- 需要20-30个测试覆盖各类传播路径

**预期收益**: 
- 提高定位精度（区分来源vs采纳）
- 为演化学习提供更精确的修复目标

---

## 📋 实施优先级与路线图

### Phase 1: 快速改进（1-2天，低风险）

**目标**: 改善诊断能力，不改变核心逻辑

1. ✅ **比较失败细分**（方案1.3）
   - 实施难度: ⭐ (1/5)
   - 预期收益: 改善可调试性
   - 测试需求: 5-10个新测试

2. ✅ **Upgrade拒绝诊断**（方案2.2）
   - 实施难度: ⭐⭐ (2/5)
   - 预期收益: 数据基础
   - 测试需求: 5-10个新测试

**交付物**:
- 更详细的失败原因分类
- 为后续优化提供数据支持
- 全部测试保持通过

---

### Phase 2: 核心优化（3-5天，中风险）

**目标**: 提升有效比较率到40%

3. ✅ **约束适用性验证**（方案1.1）
   - 实施难度: ⭐⭐ (2/5)
   - 预期收益: 减少15-20%无效约束
   - 测试需求: 10个新测试

4. ✅ **偏离到候选转换**（方案2.1）
   - 实施难度: ⭐⭐⭐ (3/5)
   - 预期收益: 减少3个漏检
   - 测试需求: 15-20个新测试

**交付物**:
- `detector.action_forecast.v3` 新schema
- 完整的偏离→候选链路
- 有效比较率提升数据

**验收标准**:
- 全部1240+新增测试通过
- 在回归集上unknown比例<60%（历史81.4%）
- 不增加误报率

---

### Phase 3: 深度优化（5-7天，中高风险）

**目标**: 实现通用义务核验

5. ⚠️ **义务核验器**（方案1.2）
   - 实施难度: ⭐⭐⭐ (3/5)
   - 预期收益: 减少10-15%无效约束
   - 测试需求: 20-30个新测试

6. ⚠️ **来源定位细化**（方案3.1）
   - 实施难度: ⭐⭐⭐ (3/5)
   - 预期收益: 提高定位精度
   - 测试需求: 20-30个新测试

**交付物**:
- 小范围义务核验能力
- 三级定位结构（origin/adoption/delivery）
- 综合效果提升报告

**验收标准**:
- 全部测试通过
- 在回归集上有效比较率>40%
- 在未见数据上验证泛化性

---

## 🧪 验证策略

### 离线验证（每个Phase必须）
1. **单元测试**: 新增功能有完整测试覆盖
2. **回归测试**: 原有1240项测试保持通过
3. **集成测试**: 完整执行链路的端到端验证

### 效果验证（Phase 2/3后）
1. **回归集验证**: 在原100例上测量中间指标
   - 约束生成数量
   - 有效比较率
   - 候选转换率
   - unknown细分统计

2. **对照实验**（可选）:
   - 同源码、同配置的off vs on对比
   - 记录全部中间状态和最终指标
   - 不需要真实模型调用，使用Fake数据

3. **边界测试**: 正常样本保护能力验证
   - 合理替代动作不应误报
   - 诚实失败报告不应定罪
   - 后续修复不应标记为错误

---

## 💰 成本估算

### 开发成本

| Phase | 工作量 | 风险等级 | 测试数量 |
|-------|--------|----------|----------|
| Phase 1 | 1-2天 | 低 | 10-20个 |
| Phase 2 | 3-5天 | 中 | 25-30个 |
| Phase 3 | 5-7天 | 中高 | 40-60个 |
| **总计** | **9-14天** | - | **75-110个** |

### 维护成本
- Schema版本升级：需要更新文档和回放兼容性
- 测试维护：新增测试需要持续维护
- 性能影响：新增验证可能增加5-10%执行时间

### ROI评估
- **Phase 1**: 低成本，改善可观测性，必须做
- **Phase 2**: 中等成本，预期提升20-30%有效率，值得做
- **Phase 3**: 较高成本，需要Phase 2数据支持后决策

---

## ⚠️ 风险与缓解

### 技术风险

1. **Schema升级风险**
   - 风险：v2→v3可能破坏回放兼容性
   - 缓解：保留v2解析器，提供升级路径

2. **性能影响**
   - 风险：新增验证增加计算开销
   - 缓解：只在必要阶段执行，避免重复计算

3. **测试覆盖不足**
   - 风险：新逻辑引入bug
   - 缓解：每个功能点至少3个测试（正常/边界/异常）

### 效果风险

1. **提升不达预期**
   - 风险：优化后unknown仍>60%
   - 缓解：Phase 1先获取数据，Phase 2基于数据调整策略

2. **误报增加**
   - 风险：放宽验证导致误报
   - 缓解：保留严格的Upgrade和Defense门

3. **泛化能力差**
   - 风险：只在回归集有效
   - 缓解：在未见数据上验证，避免过拟合

---

## 🎯 推荐实施策略

### 立即可做（Phase 1）
✅ **强烈推荐**
- 低风险，高回报
- 改善可观测性
- 为后续决策提供数据

### 条件可做（Phase 2）
⚠️ **建议在获得Phase 1数据后评估**
- 中等风险，预期有明确收益
- 需要2-3周迭代验证
- 建议在开发环境先验证效果

### 谨慎评估（Phase 3）
❓ **需要Phase 2证明有效后再决策**
- 较高风险，收益不确定
- 可能需要重新设计部分架构
- 建议作为独立研究项目

---

## 📊 预期效果总结

### 保守估计（Phase 1+2完成）
- **有效比较率**: 18.6% → 35-40%
- **unknown比例**: 81.4% → 60-65%
- **候选转换率**: 未知 → >60%
- **净增益**: 0 → +3~5个百分点
- **成本增加**: +5-10%执行时间

### 乐观估计（Phase 1+2+3完成）
- **有效比较率**: 18.6% → 45-50%
- **unknown比例**: 81.4% → 50-55%
- **候选转换率**: 未知 → >75%
- **净增益**: 0 → +5~8个百分点
- **定位精度**: 细化到origin/adoption/delivery

---

## 📝 结论与建议

### 核心建议
1. ✅ **立即启动Phase 1**：低风险高回报，改善可观测性
2. ⚠️ **评估Phase 2**：基于Phase 1数据决定是否继续
3. ❓ **暂缓Phase 3**：等待Phase 2效果验证

### 关键成功因素
- **增量验证**：每个Phase独立验证效果
- **测试先行**：新功能必须有测试覆盖
- **数据驱动**：基于实际数据调整策略
- **回滚能力**：保持向后兼容，可以回退

### 不建议的做法
- ❌ 同时推进多个Phase（风险叠加）
- ❌ 跳过Phase 1直接做Phase 2（缺少数据基础）
- ❌ 为了指标改变Upgrade/Defense门（破坏保护能力）
- ❌ 基于历史数据过度优化（过拟合风险）

---

**方案制定人**: Claude Opus 5.5  
**制定日期**: 2026-10-16  
**文档版本**: 1.0  
**状态**: 待评审
