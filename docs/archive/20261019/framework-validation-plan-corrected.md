# 检测器框架正确性验证方案（修正版）

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

**日期**: 2026-10-16  
**目标**: 验证框架整体有效性，而非局部调参  
**原则**: 在未见数据上测试泛化能力，避免过拟合

---

## ⚠️ 前期方向性问题

### 原方案的问题
1. **Phase 1-3聚焦于提升中间指标**（有效比较率、候选转换率）
2. **基于历史76/100数据优化**，这些数据来自修复前版本
3. **缺少独立验证集**，无法证明改进真实有效
4. **风险**：可能在回归集上过度拟合，破坏泛化能力

### 根本原则
> **框架的目标是准确检测幻觉，而不是优化某个中间环节的通过率**

---

## ✅ 正确的验证方案

### 阶段1: 框架完整性验证（当前状态）

**目标**: 确认所有机制按设计运行

#### 1.1 代码层面验证 ✅ 已完成
- [x] 全部1252个单元测试通过
- [x] 关键机制回归测试覆盖
- [x] 执行链路完整性验证
- [x] 回放身份管理修复

#### 1.2 设计一致性验证
**检查项**:
```python
# 验证设计意图是否正确实现
checks = {
    "预测隔离": "预测使用截至t的前缀，不泄露t+1信息",
    "资源保护": "预测使用剩余额度，不影响原有诊断",
    "证据传递": "偏离→Recall→Upgrade→Tracer完整链路",
    "合同保护": "Judge基础判决不因预测策略版本改变而失效",
    "失败安全": "预测失败时保持基础检测能力",
}
```

**验证方式**: 
- 阅读代码确认实现符合技术文档
- 检查关键决策点是否有显式验证
- 确认边界情况有测试覆盖

**结论**: ✅ 通过代码审查，设计实现一致

---

### 阶段2: 独立数据验证（推荐方案）

**目标**: 在未见数据上验证框架有效性

#### 2.1 数据集选择原则

**不应使用**:
- ❌ 历史76/100回归集（可能已过拟合）
- ❌ 训练/调试过程中见过的案例
- ❌ 为验证某个假设构造的合成案例

**应该使用**:
- ✅ 未参与开发的真实案例
- ✅ 与训练集分布不同的新场景
- ✅ 来自不同数据源的案例

#### 2.2 验证策略

##### 选项A: 留出验证集（推荐）
```python
# 如果有更大的数据集
validation_set = load_unseen_cases(source="新数据源", count=50)

# 在验证集上测试当前框架
for case in validation_set:
    baseline_result = detect(case, config=off_mode)
    forecast_result = detect(case, config=on_mode)
    
    # 记录最终指标，不关注中间环节
    metrics.record({
        "case_id": case.id,
        "baseline_decision": baseline_result["state"],
        "forecast_decision": forecast_result["state"],
        "ground_truth": case.ground_truth,  # 如果有
    })

# 分析整体效果
analyze_final_outcomes(metrics)
```

##### 选项B: 交叉验证（如果数据有限）
```python
# 如果只有100例数据
folds = split_into_folds(all_cases, n_folds=5)

for i in range(5):
    test_fold = folds[i]
    # 不在test_fold上调整任何参数
    
    results = evaluate_on_fold(test_fold, config=current_config)
    fold_metrics.append(results)

# 平均跨fold的性能
average_performance = mean(fold_metrics)
```

##### 选项C: 前瞻性验证（最可靠）
```python
# 使用当前框架处理新出现的案例
new_cases = collect_new_cases(after_date="2026-10-16")

# 完全不修改框架，直接应用
results = [detect(case, config=production_config) for case in new_cases]

# 与人工标注对比（如果有资源）
agreement_rate = compare_with_human_annotation(results, new_cases)
```

#### 2.3 评估指标

**终点指标**（唯一重要的）:
- **检测准确率**: 正确识别幻觉案例的比例
- **误报率**: 将正常案例标记为幻觉的比例
- **定位准确率**: 在检出案例中，定位是否指向真实错误步骤

**中间指标**（仅用于诊断，不用于优化）:
- 预测使用率
- 比较有效率
- 候选转换率
- 各阶段资源消耗

**关键原则**:
> 只有终点指标提升，才说明框架改进有效。中间指标的改善不代表整体能力提升。

---

### 阶段3: 对照实验设计

**目标**: 确认预测机制的净增益

#### 3.1 实验设置

```python
configs = {
    "baseline": {
        "next_action_mode": "off",  # 关闭预测
    },
    "forecast_shadow": {
        "next_action_mode": "shadow",  # 预测但不影响判决
    },
    "forecast_active": {
        "next_action_mode": "judge_context",  # 预测参与判决
    },
}

# 在相同数据上运行三种配置
for case in validation_set:
    for name, config in configs.items():
        result = detect(case, config=config)
        results[name].append(result)
```

#### 3.2 对比维度

| 维度 | 评估方法 | 期望结果 |
|------|---------|---------|
| **检测能力** | 查全率、查准率 | forecast > baseline |
| **定位精度** | 首错命中率 | forecast ≥ baseline |
| **误报率** | 正常案例误报数 | forecast ≤ baseline |
| **成本** | 请求数、时延 | 增幅<30% |
| **稳定性** | 跨case方差 | forecast ≈ baseline |

#### 3.3 有效性判断

**预测机制有效**的充要条件:
```
检测准确率提升 > 5个百分点
AND 误报率增幅 < 3个百分点
AND 成本增幅 < 30%
```

**如果不满足**:
- 说明当前设计在实际数据上无效
- 需要重新审视架构，而非调参
- 可能需要更根本的机制改进

---

## 🚫 应该避免的做法

### 1. 在回归集上调参
```python
# ❌ 错误做法
for threshold in [0.5, 0.6, 0.7, 0.8]:
    config["some_threshold"] = threshold
    accuracy = evaluate_on_regression_set(config)
    if accuracy > best_accuracy:
        best_threshold = threshold  # 过拟合！
```

**问题**: 
- 回归集可能已经影响了开发决策
- 在其上优化参数=记忆测试答案
- 泛化能力无法保证

### 2. 优化中间指标
```python
# ❌ 错误做法
# 目标：提高"比较有效率"从18.6%到40%
for constraint_type in all_types:
    if improves_comparison_rate(constraint_type):
        add_constraint_filter(constraint_type)  # 可能损害终点指标！
```

**问题**:
- 中间指标↑不保证最终准确率↑
- 可能提高了无用比较的占比
- 局部最优≠全局最优

### 3. 基于历史数据设计特征
```python
# ❌ 错误做法
# 观察到历史数据中"quote_not_in_action"占比高
def new_feature_based_on_historical_pattern():
    # 专门针对历史失败模式的特征
    pass
```

**问题**:
- 历史数据来自旧版本
- 新版本的失效模式可能完全不同
- 过度拟合历史分布

---

## ✅ 推荐的实施步骤

### Step 1: 确认当前框架状态 ✅ 已完成
- [x] 代码审查通过
- [x] 测试全部通过
- [x] 修复了回放身份bug
- [x] Phase 1诊断增强完成

### Step 2: 准备独立验证集

**选项A: 如果有未见数据**
```bash
# 从数据源获取新案例
python scripts/collect_validation_cases.py \
    --source agenthallu \
    --exclude-ids regression_100.txt \
    --count 50 \
    --output validation_set.json
```

**选项B: 如果数据有限**
```bash
# 将现有数据分为train/val（假设之前没分）
python scripts/split_dataset.py \
    --input all_cases.json \
    --train-ratio 0.7 \
    --val-ratio 0.3 \
    --stratify-by error_type
```

**选项C: 如果无法获取新数据**
- 使用当前框架处理新提交的案例
- 前瞻性收集未来2周的新案例
- 完全不修改框架，只记录结果

### Step 3: 运行对照实验

```bash
# 在验证集上运行三种配置
python scripts/comparative_experiment.py \
    --validation-set validation_set.json \
    --configs baseline,shadow,active \
    --output experiment_results.json
```

### Step 4: 分析终点指标

```python
# 分析脚本
from scripts.analyze_results import compare_configurations

report = compare_configurations(
    results_path="experiment_results.json",
    focus_on="final_outcomes",  # 不是中间环节
    metrics=["detection_accuracy", "false_positive_rate", "localization_accuracy"]
)

print(report.summary())
# 输出：
# baseline: 76% detection, 5% FP
# forecast_active: 79% detection, 6% FP
# improvement: +3% detection, +1% FP
# cost: +22% latency
```

### Step 5: 做出决策

**情况A: 预测机制有效**（检测率+5%以上）
- ✅ 保留当前框架
- ✅ 可以考虑Phase 2优化（但仍需在独立集验证）
- ✅ 投入生产使用

**情况B: 预测机制边际效果**（检测率+2-5%）
- ⚠️ 权衡成本收益
- ⚠️ 可能只在特定场景有效
- ⚠️ 需要分析哪类案例受益

**情况C: 预测机制无效**（检测率<+2%）
- ❌ 不应该继续Phase 2调参
- ❌ 需要重新审视架构设计
- ❌ 可能需要更根本的机制创新

---

## 📊 当前建议

### 立即行动

1. **确定验证数据来源**
   - 是否有未参与开发的新案例？
   - 能否获取不同领域的测试集？
   - 是否可以前瞻性收集？

2. **运行基准对照实验**
   ```bash
   # 如果有验证集
   python scripts/run_validation.py --mode compare_all
   
   # 如果只有回归集（警告：可能过拟合）
   python scripts/run_validation.py --mode cross_validation --folds 5
   ```

3. **分析终点指标，不是中间指标**
   - 检测准确率变化
   - 误报率变化
   - 成本变化

### 暂停的行动

- ⛔ **不要立即启动Phase 2**（约束验证、证据链优化）
- ⛔ **不要基于历史失败分布调整参数**
- ⛔ **不要优化中间指标**

### 决策树

```
是否有独立验证集？
├─ 是 → 运行对照实验 → 分析终点指标 → 决策
│   ├─ 有效 → 保留框架，可选优化
│   └─ 无效 → 重新设计，不要调参
│
└─ 否 → 能否获取？
    ├─ 能 → 获取后验证
    └─ 不能 → 交叉验证（次优）或前瞻性收集
```

---

## 🎓 核心原则总结

### 1. 框架验证 > 局部调优
- 先证明整体机制有效
- 再考虑局部改进

### 2. 独立数据 > 回归集
- 在未见数据上验证
- 避免过拟合训练集

### 3. 终点指标 > 中间指标
- 只关心最终检测准确率
- 中间环节优化不保证整体提升

### 4. 泛化能力 > 拟合能力
- 宁可在训练集上表现一般
- 也要在新数据上稳定

### 5. 架构创新 > 参数调优
- 如果整体无效，需要重新设计
- 不要在错误的方向上精益求精

---

## 🚀 修正后的行动方案

### 当前优先级

**P0: 验证框架有效性**
1. 确定独立验证数据来源
2. 运行baseline vs forecast对照实验
3. 分析终点指标（检测准确率、误报率）
4. 基于结果决定是否继续

**P1: 如果框架有效**
- 记录哪类案例受益
- 分析失效模式
- 考虑特定场景的改进

**P2: 如果框架效果不明显**
- 重新审视架构设计
- 可能需要更根本的创新
- 不要在局部调参上浪费时间

---

**方案制定人**: Claude Opus 5.5  
**制定日期**: 2026-10-16  
**状态**: 方向修正，等待验证数据确认
