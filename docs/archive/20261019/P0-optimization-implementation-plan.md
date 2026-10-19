# P0 优化实施计划

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

**创建时间**：2026-10-15  
**状态**：待审核  
**目标**：在不降低检测精度的前提下，实现 15-25% 成本节省和 30-40% 延迟减少

---

## 总览

### 优化项目

| ID | 优化名称 | 预期收益 | 风险等级 | 实施时间 | 优先级 |
|----|---------|---------|---------|---------|--------|
| P0.1 | 自适应投票策略 | 15-20% 成本节省 | **极低** | 2-3 天 | 🔴 最高 |
| P0.2 | Defense 证据强度分层 | 提升可信度标记 | **低** | 5-7 天 | 🟠 高 |
| P0.3 | 并行执行优化 | 30-40% 延迟减少 | **低** | 3-4 天 | 🟡 中 |

### 总体时间表

```
Week 1          Week 2          Week 3          Week 4
|----P0.1----|
        |------P0.2------|
                |--P0.3--|
                        |---整合测试---|
```

---

## P0.1 自适应投票策略

### 目标

在高度一致的情况下提前终止投票，避免资源浪费。

### 核心设计

```python
class AdaptiveVotingStrategy:
    """自适应投票策略"""
    
    def __init__(self, min_votes: int = 2, max_votes: int = 5):
        """
        参数:
            min_votes: 最小票数（硬约束）
            max_votes: 最大票数（原配置值）
        """
        self.min_votes = min_votes
        self.max_votes = max_votes
    
    def should_continue(
        self, 
        cast_ballots: list[dict],
        confidence_threshold: str = "high"
    ) -> tuple[bool, str]:
        """
        决定是否继续投票
        
        返回: (是否继续, 原因代码)
        
        提前终止条件:
        1. 达到 min_votes
        2. 所有票一致
        3. 所有票都是 high confidence
        
        强制继续条件:
        1. 未达 min_votes
        2. 出现分歧（不同的 is_hallucination）
        3. 一致但非 high confidence
        """
        n = len(cast_ballots)
        
        # 未达最小票数，必须继续
        if n < self.min_votes:
            return True, "below_minimum"
        
        # 已达最大票数，停止
        if n >= self.max_votes:
            return False, "max_reached"
        
        # 提取判决和置信度
        verdicts = [b["is_hallucination"] for b in cast_ballots]
        confidences = [b["confidence"] for b in cast_ballots]
        
        # 检查一致性
        all_same_verdict = len(set(verdicts)) == 1
        all_high_confidence = all(c == confidence_threshold for c in confidences)
        
        # 完美一致：提前终止
        if all_same_verdict and all_high_confidence:
            return False, "unanimous_high_confidence"
        
        # 出现分歧：必须投满
        if not all_same_verdict:
            return True, "divergence_detected"
        
        # 一致但低置信：再投一票
        if n < self.max_votes:
            return True, "low_confidence_consensus"
        
        return False, "default_stop"
```

### 实施步骤

#### 第一步：创建策略类（0.5 天）

**文件**：`detector/adaptive_voting.py`（新建）

**内容**：
- `AdaptiveVotingStrategy` 类
- 单元测试覆盖所有分支

**验收**：
- ✅ 所有单元测试通过
- ✅ 代码覆盖率 100%

#### 第二步：集成到 Judge（1 天）

**文件**：`detector/judge.py`

**修改位置**：`_vote_judge()` 函数

```python
def _vote_judge(
    *,
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    system: str,
    user: str,
    plan: InterventionPlan | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Judge 投票 - 支持自适应策略"""
    
    votes = max(1, int(route.get("votes", 1) or 1))
    
    # 新增：检查是否启用自适应投票
    adaptive_enabled = route.get("adaptive_voting", False)
    if adaptive_enabled:
        strategy = AdaptiveVotingStrategy(
            min_votes=route.get("adaptive_min_votes", 2),
            max_votes=votes
        )
    else:
        strategy = None
    
    ballots: list[dict[str, Any]] = []
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    failures: list[dict[str, Any]] = []
    ballots_usage: dict[str, Any] = {}
    
    def cast_at(_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
        return _cast_vote(
            packet=packet, route=route, system=system, user=user,
            plan=plan, snapshot=snapshot
        )
    
    # 投票循环
    for i in range(votes):
        # 自适应检查
        if strategy and i >= strategy.min_votes:
            should_cont, reason = strategy.should_continue(ballots)
            if not should_cont:
                # 记录提前终止
                early_stop_info = {
                    "enabled": True,
                    "reason": reason,
                    "ballots_cast": len(ballots),
                    "ballots_saved": votes - len(ballots),
                    "cost_saving_pct": (votes - len(ballots)) / votes * 100
                }
                break
        
        # 投票（原逻辑）
        result, status = cast_at(i)
        total_attempts += int(status.get("attempts") or 0)
        warnings.extend(status.get("warnings") or [])
        
        # ... 处理投票结果（原逻辑不变）
    
    # 在 meta 中记录自适应信息
    if strategy:
        sample_meta["adaptive_voting"] = early_stop_info
    
    # ... 后续逻辑不变
```

**验收**：
- ✅ 现有测试全部通过（向后兼容）
- ✅ 新增 10+ 个自适应场景测试
- ✅ 手工测试 5 个真实案例，验证提前终止逻辑

#### 第三步：集成到 Defense 和 Upgrade（0.5 天）

**文件**：`detector/defense.py`, `detector/recaller.py`

**修改**：与 Judge 类似的集成模式

**验收**：
- ✅ 所有现有测试通过
- ✅ 新增自适应测试覆盖

#### 第四步：配置和文档（0.5 天）

**配置文件**：更新默认配置示例

```json
{
  "judge_route": {
    "votes": 5,
    "adaptive_voting": false,  // 默认关闭，验证后启用
    "adaptive_min_votes": 2
  },
  "defense_route": {
    "votes": 3,
    "adaptive_voting": false,
    "adaptive_min_votes": 2
  }
}
```

**文档更新**：
- `docs/configuration-guide.md`：添加自适应投票说明
- `docs/optimization-and-novelty-analysis.md`：链接到实现

**验收**：
- ✅ 配置文件格式正确
- ✅ 文档清晰易懂

#### 第五步：A/B 测试（1 天）

**测试方案**：
1. 选择 50 个历史案例
2. 分别用固定投票和自适应投票运行
3. 对比：
   - 检测准确率（必须一致）
   - 首错命中率（必须一致）
   - 平均请求数（期望减少 15-20%）
   - 平均耗时（可能略增，但不超过 5%）

**验收标准**：
- ✅ 准确率差异 < 1%
- ✅ 请求数减少 >= 10%
- ✅ 无异常终止或错误

---

## P0.2 Defense 证据强度分层

### 目标

为 Defense 判决标注证据强度，提升定位权威性标记的准确性。

### 核心设计

```python
class EvidenceStrengthScorer:
    """Defense 证据强度评分器"""
    
    @staticmethod
    def score_defense_evidence(
        defense_parsed: dict,
        packet: dict,
        snapshot: dict | None
    ) -> dict:
        """
        计算 Defense 证据强度
        
        评分维度（总分 1.0）:
        1. 引用多样性 (0-0.3)
           - event_content: +0.1
           - tool_input: +0.1
           - tool_output: +0.1
        
        2. 外部证据支持 (0-0.3)
           - 独立来源数量（最多 4 个得满分）
           - 解决了冲突：+0.2
        
        3. 时序一致性 (0-0.2)
           - 所有引用在确认步骤之前或当时：1.0
           - 有引用在之后：0.5
        
        4. 工具观察权重 (0-0.2)
           - 每个工具观察引用：+0.1（最多 2 个）
        
        评级:
        - strong: >= 0.7
        - medium: 0.4-0.7
        - weak: < 0.4
        """
        evidence_refs = defense_parsed.get("evidence_refs", [])
        evidence_citations = defense_parsed.get("evidence_citations", [])
        
        components = {}
        
        # 1. 引用多样性
        ref_types = _classify_references(packet, evidence_refs)
        components["reference_diversity"] = min(0.3, len(ref_types) * 0.1)
        
        # 2. 外部证据支持
        if snapshot and evidence_citations:
            components["external_support"] = (
                _evaluate_external_citations(snapshot, evidence_citations) * 0.3
            )
        else:
            components["external_support"] = 0.0
        
        # 3. 时序一致性
        confirmed_step = defense_parsed.get("confirmed_step")
        if confirmed_step:
            components["temporal_consistency"] = (
                _check_temporal_consistency(packet, evidence_refs, confirmed_step) * 0.2
            )
        else:
            components["temporal_consistency"] = 0.0
        
        # 4. 工具观察权重
        tool_refs = [r for r in evidence_refs if _is_tool_reference(packet, r)]
        components["tool_observation_weight"] = min(0.2, len(tool_refs) * 0.1)
        
        # 总分
        strength_score = sum(components.values())
        
        # 分级
        if strength_score >= 0.7:
            strength_level = "strong"
        elif strength_score >= 0.4:
            strength_level = "medium"
        else:
            strength_level = "weak"
        
        return {
            "strength_score": round(strength_score, 3),
            "components": {k: round(v, 3) for k, v in components.items()},
            "strength_level": strength_level,
            "schema_version": "detector.evidence_strength.v1"
        }

def _classify_references(packet: dict, refs: list[str]) -> set[str]:
    """将引用分类"""
    types = set()
    for ref in refs:
        # 检查引用类型
        if _is_event_content_ref(packet, ref):
            types.add("event_content")
        elif _is_tool_input_ref(packet, ref):
            types.add("tool_input")
        elif _is_tool_output_ref(packet, ref):
            types.add("tool_output")
    return types

def _is_event_content_ref(packet: dict, ref: str) -> bool:
    """判断是否为事件内容引用"""
    # 实现：检查 ref 是否为 event_id 格式
    events = packet.get("events", [])
    for event in events:
        if event.get("event_id") == ref:
            return True
    return False

def _is_tool_input_ref(packet: dict, ref: str) -> bool:
    """判断是否为工具输入引用"""
    # 实现：检查 ref 是否为 tool_id 且指向输入
    # 格式可能是 "event_id:tool_id" 或 "tool_id"
    return "tool_" in ref and "input" in ref.lower()

def _is_tool_output_ref(packet: dict, ref: str) -> bool:
    """判断是否为工具输出引用"""
    return "tool_" in ref and "output" in ref.lower()

def _is_tool_reference(packet: dict, ref: str) -> bool:
    """判断是否为任何工具引用"""
    return _is_tool_input_ref(packet, ref) or _is_tool_output_ref(packet, ref)

def _evaluate_external_citations(snapshot: dict, citations: list[str]) -> float:
    """评估外部引用质量"""
    sources = snapshot.get("sources", [])
    cited_sources = [s for s in sources if s["evidence_id"] in citations]
    
    if not cited_sources:
        return 0.0
    
    # 独立来源数量
    independence_groups = set()
    for source in cited_sources:
        group = source.get("independence_group", source["evidence_id"])
        independence_groups.add(group)
    
    independence_score = min(1.0, len(independence_groups) / 4.0)
    
    # 冲突解决加分
    resolutions = snapshot.get("resolutions", [])
    resolved_conflicts = any(r["resolved_by"] in citations for r in resolutions)
    conflict_bonus = 0.2 if resolved_conflicts else 0.0
    
    return min(1.0, independence_score + conflict_bonus)

def _check_temporal_consistency(
    packet: dict, 
    refs: list[str], 
    confirmed_step: int
) -> float:
    """检查时序一致性"""
    for ref in refs:
        ref_step = _get_reference_step(packet, ref)
        if ref_step is None:
            continue
        if ref_step > confirmed_step:
            return 0.5  # 有引用在确认步骤之后
    return 1.0  # 所有引用都在之前或当时

def _get_reference_step(packet: dict, ref: str) -> int | None:
    """获取引用对应的步骤号"""
    # 实现：从 packet 中查找 ref 对应的步骤
    events = packet.get("events", [])
    for i, event in enumerate(events, start=1):
        if event.get("event_id") == ref:
            return i
        # 检查工具引用
        for tool in event.get("tools", []):
            if tool.get("tool_id") == ref:
                return i
    return None
```

### 实施步骤

#### 第一步：创建评分器（2 天）

**文件**：`detector/evidence_strength.py`（新建）

**内容**：
- `EvidenceStrengthScorer` 类
- 所有辅助函数
- 完整的单元测试

**验收**：
- ✅ 20+ 个单元测试覆盖所有评分维度
- ✅ 边界情况测试（空引用、缺失快照等）

#### 第二步：集成到 Defense（2 天）

**文件**：`detector/defense.py`

**修改位置**：`_vote_defense()` 函数

```python
def _vote_defense(...) -> tuple[dict[str, Any], dict[str, Any]]:
    """Defense 投票 - 附加证据强度评分"""
    
    # ... 原投票逻辑 ...
    
    # 新增：计算证据强度
    if result.get("ok") and result.get("parsed"):
        from detector.evidence_strength import EvidenceStrengthScorer
        
        strength = EvidenceStrengthScorer.score_defense_evidence(
            defense_parsed=result["parsed"],
            packet=packet,
            snapshot=snapshot
        )
        
        result["evidence_strength"] = strength
        status["evidence_strength_level"] = strength["strength_level"]
    
    return result, status
```

**验收**：
- ✅ 所有现有测试通过
- ✅ 新增 15+ 个证据强度测试
- ✅ 手工验证 10 个真实案例的评分合理性

#### 第三步：集成到 Adjudication（1 天）

**文件**：`detector/adjudication.py`

**修改**：根据证据强度调整 `localization_authority`

```python
def _determine_localization_authority(
    hearing_sources: dict,
    defense_result: dict | None
) -> str:
    """决定定位权威性"""
    
    # 原逻辑：检测来源冲突
    if hearing_sources.get("conflict"):
        base_authority = "degraded_localization_conflict"
    else:
        base_authority = "authoritative"
    
    # 新增：弱证据降级
    if defense_result and "evidence_strength" in defense_result:
        strength = defense_result["evidence_strength"]
        strength_level = strength.get("strength_level")
        
        if strength_level == "weak" and base_authority == "authoritative":
            return "degraded_weak_evidence"
        # medium 保持原权威性，但记录强度
    
    return base_authority
```

**验收**：
- ✅ 降级逻辑正确
- ✅ 向后兼容（无强度信息时使用原逻辑）
- ✅ 测试覆盖所有组合

#### 第四步：更新结果 Schema（0.5 天）

**文件**：`detector/result.py`

**修改**：在 `detection_result` 中添加证据强度字段

```python
# 在 adjudication 结果中添加
{
    "localization_authority": "authoritative" | "degraded_localization_conflict" | "degraded_weak_evidence",
    "evidence_strength": {  # 新增
        "strength_score": 0.75,
        "strength_level": "strong",
        "components": {...},
        "schema_version": "detector.evidence_strength.v1"
    }
}
```

**验收**：
- ✅ Schema 验证通过
- ✅ 旧结果仍可解析（向后兼容）

#### 第五步：验证和文档（1 天）

**验证**：
1. 运行 30 个历史案例
2. 人工审查证据强度评分是否合理
3. 检查是否有不当降级

**文档**：
- 更新 `docs/technical-solution.md`：添加证据强度说明
- 创建 `docs/evidence-strength-guide.md`：详细评分规则

**验收**：
- ✅ 90% 以上案例评分合理
- ✅ 无不当降级（原 authoritative 不应变 weak）
- ✅ 文档完整

---

## P0.3 并行执行优化

### 目标

通过流水线重叠减少 Recall 阶段的等待时间。

### 核心设计

**当前流程**（串行）：
```
Scanner 完成 → 等待 → Upgrade 开始准备 → Upgrade 投票
```

**优化流程**（流水线）：
```
Scanner 完成 → 立即准备 Upgrade context (并行)
              ├─ 准备 evidence block
              └─ 准备 view
              ↓
            Upgrade 投票（使用预计算结果）
```

### 实施步骤

#### 第一步：创建流水线函数（1 天）

**文件**：`detector/recall_pipeline.py`（新建）

```python
"""Recall 流水线优化"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping
from detector.packet import render_judge_view
from detector.interventions import InterventionPlan


class RecallPipeline:
    """Recall 流水线：Scanner 和 Upgrade 准备重叠"""
    
    @staticmethod
    def run_with_overlap(
        *,
        packet: Mapping[str, Any],
        config: Mapping[str, Any],
        plan: InterventionPlan | None,
        snapshot: Mapping[str, Any] | None,
    ) -> tuple[
        dict[str, Any],  # recall_result
        dict[str, Any],  # recall_status
        dict[str, Any] | None,  # upgrade_result
        dict[str, Any] | None,  # upgrade_status
    ]:
        """
        运行 Recall，Scanner 完成后立即准备 Upgrade context
        """
        from detector.recaller import run_recall_scan, run_causal_upgrade
        
        # 第一阶段：Scanner
        recall_result, recall_status = run_recall_scan(
            packet=packet,
            config=config,
            plan=plan,
            snapshot=snapshot
        )
        
        # 检查是否有候选
        if not (recall_result.get("ok") and recall_result.get("candidates")):
            return recall_result, recall_status, None, None
        
        candidates = recall_result["candidates"]
        
        # 第二阶段：并行准备 Upgrade 所需资源
        try:
            precomputed = RecallPipeline._prepare_upgrade_context(
                packet=packet,
                candidates=candidates,
                plan=plan,
                snapshot=snapshot
            )
        except Exception as e:
            # 准备失败，回退到串行
            precomputed = None
        
        # 第三阶段：Upgrade 投票
        upgrade_result, upgrade_status = run_causal_upgrade(
            packet=packet,
            candidates=candidates,
            config=config,
            plan=plan,
            snapshot=snapshot,
            precomputed_context=precomputed  # 新增参数
        )
        
        return recall_result, recall_status, upgrade_result, upgrade_status
    
    @staticmethod
    def _prepare_upgrade_context(
        *,
        packet: Mapping[str, Any],
        candidates: list[dict],
        plan: InterventionPlan | None,
        snapshot: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """
        并行准备 Upgrade context
        
        并行任务:
        1. 准备 evidence block
        2. 渲染 judge view
        
        返回: {
            "evidence_block": str,
            "view_text": str,
            "view_meta": dict
        }
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交任务
            futures = {
                "evidence": executor.submit(
                    RecallPipeline._prepare_evidence_block,
                    plan, packet, candidates
                ),
                "view": executor.submit(
                    render_judge_view, packet
                )
            }
            
            # 收集结果
            for key, future in futures.items():
                try:
                    if key == "evidence":
                        results["evidence_block"] = future.result()
                    elif key == "view":
                        view_text, view_meta = future.result()
                        results["view_text"] = view_text
                        results["view_meta"] = view_meta
                except Exception as e:
                    # 任一任务失败，整体失败
                    raise RuntimeError(f"Upgrade context preparation failed: {e}")
        
        return results
    
    @staticmethod
    def _prepare_evidence_block(
        plan: InterventionPlan | None,
        packet: Mapping[str, Any],
        candidates: list[dict]
    ) -> str:
        """准备 evidence block"""
        from detector.packet import packet_features
        
        if not plan:
            return ""
        
        evidence = plan.evidence_for(
            "upgrade.pre",
            packet,
            {"candidates": candidates}
        )
        
        if not evidence:
            return ""
        
        lines = [
            "\nMECHANICAL PRE-CHECKS (facts from the trajectory/contract, "
            "not a verdict):"
        ]
        for item in evidence:
            lines.append(f"- {item['predicate']}: {item['verdict']} ({item['detail']})")
        
        return "\n".join(lines)
```

#### 第二步：修改 run_causal_upgrade（1 天）

**文件**：`detector/recaller.py`

**修改**：接受可选的预计算 context

```python
def run_causal_upgrade(
    *,
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
    snapshot: Mapping[str, Any] | None = None,
    precomputed_context: dict[str, Any] | None = None,  # 新增参数
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Causal Upgrade - 支持预计算 context
    
    参数:
        precomputed_context: 可选的预计算结果，包含:
            - evidence_block: str
            - view_text: str
            - view_meta: dict
    """
    plan = plan or empty_plan()
    
    if not candidates:
        return {
            "ok": True, "upgrades": [], "upgraded_candidate": None,
            "upgraded_candidates": [],
        }, {
            "stage": "causal_upgrade", "state": "not_needed",
            "attempts": 0, "view": "not_rendered", "warnings": [],
        }
    
    route = dict(config["judge_route"])
    route["_route_name"] = "causal_upgrade"
    
    # 使用预计算 evidence 或现场计算
    if precomputed_context and "evidence_block" in precomputed_context:
        evidence_block = precomputed_context["evidence_block"]
    else:
        # 原逻辑
        evidence = plan.evidence_for("upgrade.pre", packet, {"candidates": candidates})
        evidence_block = _format_evidence_block(evidence)  # 提取为函数
    
    # 使用预计算 view 或现场渲染
    if precomputed_context and "view_text" in precomputed_context:
        view_text = precomputed_context["view_text"]
        view_meta = precomputed_context["view_meta"]
    else:
        # 原逻辑
        view_text, view_meta = render_judge_view(packet)
    
    # ... 后续逻辑不变 ...
```

**验收**：
- ✅ 向后兼容（无 precomputed_context 时正常工作）
- ✅ 预计算结果被正确使用
- ✅ 所有测试通过

#### 第三步：集成到主流程（0.5 天）

**文件**：`detector/api.py`

**修改**：在 `_detect()` 中使用流水线

```python
from detector.recall_pipeline import RecallPipeline

def _detect(...):
    # ... Judge 逻辑 ...
    
    # Recall 阶段
    if recall_decision == "needed":
        # 使用流水线优化
        recall_result, recall_status, upgrade_result, upgrade_status = (
            RecallPipeline.run_with_overlap(
                packet=packet,
                config=config,
                plan=plan,
                snapshot=snapshot
            )
        )
    else:
        recall_result = None
        upgrade_result = None
    
    # ... 后续逻辑不变 ...
```

**验收**：
- ✅ 端到端测试通过
- ✅ 延迟确实减少

#### 第四步：性能测试（1 天）

**测试方案**：
1. 选择 20 个需要 Recall 的案例
2. 对比串行 vs 流水线的耗时

**预期结果**：
- Recall 阶段延迟减少 30-40%
- 总体案例延迟减少 10-15%

**验收**：
- ✅ 延迟减少达标
- ✅ 无并发错误
- ✅ 结果完全一致

---

## 整合测试（Week 4）

### 测试范围

1. **功能测试**：所有单元测试和集成测试通过
2. **回归测试**：运行完整 100 例测试集
3. **性能测试**：验证成本和延迟优化
4. **稳定性测试**：连续运行 500 例无崩溃

### 验收标准

| 指标 | 基线 | 优化目标 | 实际要求 |
|-----|------|---------|---------|
| 检测准确率 | 76% | ≥ 76% | 差异 < 1% |
| 首错命中率 | 43.55% | ≥ 43.55% | 差异 < 2% |
| 平均请求数 | 7.4 | ≤ 6.5 | 减少 ≥ 10% |
| Recall 延迟 | 基线 | 减少 30-40% | 减少 ≥ 25% |
| 崩溃率 | 0% | 0% | 0 崩溃 |

### 测试计划

#### Day 1-2：功能和回归测试
- 运行所有单元测试（2500+ 个）
- 运行完整 100 例测试集
- 对比基线结果

#### Day 3：性能测试
- 成本分析：统计请求数减少
- 延迟分析：对比各阶段耗时
- 生成性能报告

#### Day 4：稳定性测试
- 连续运行 500 例
- 监控内存泄漏
- 检查并发问题

#### Day 5：修复和文档
- 修复发现的问题
- 更新所有文档
- 准备发布

---

## 配置管理

### 默认配置

```json
{
  "judge_route": {
    "model": "claude-opus-5-5",
    "votes": 5,
    "vote_concurrency": 5,
    "adaptive_voting": false,  // P0.1: 验证后启用
    "adaptive_min_votes": 2
  },
  "defense_route": {
    "model": "claude-opus-5-5",
    "votes": 3,
    "vote_concurrency": 3,
    "adaptive_voting": false,
    "adaptive_min_votes": 2,
    "evidence_strength_scoring": true  // P0.2: 默认启用
  },
  "recall_route": {
    "model": "claude-opus-5-5",
    "votes": 1,
    "use_pipeline": true  // P0.3: 默认启用
  }
}
```

### 分阶段启用

**第一阶段**（Week 1-3）：
- `adaptive_voting`: false（开发和测试）
- `evidence_strength_scoring`: true（无风险）
- `use_pipeline`: true（无风险）

**第二阶段**（Week 4）：
- 整合测试通过后，启用 `adaptive_voting`
- 观察 1 周，无问题则正式启用

---

## 风险和缓解

### 风险矩阵

| 风险 | 可能性 | 影响 | 缓解措施 |
|-----|--------|------|---------|
| 自适应投票误判 | 低 | 高 | 最小 2 票硬约束 + 分歧必投满 |
| 证据强度评分错误 | 低 | 中 | 只影响标记，不改检测逻辑 |
| 并行竞态条件 | 中 | 高 | 只读共享状态 + 异常回退 |
| 性能未达预期 | 中 | 低 | 逐项测试，未达标则延迟启用 |
| 引入新 bug | 低 | 高 | 严格测试 + 分阶段部署 |

### 回退策略

**每个优化都有独立开关**：
```python
# 可以随时关闭任一优化
config["judge_route"]["adaptive_voting"] = False
config["defense_route"]["evidence_strength_scoring"] = False
config["recall_route"]["use_pipeline"] = False
```

**金丝雀部署**：
1. 先在 10% 案例测试
2. 无问题扩展到 50%
3. 观察 1 周后全量

---

## 成功标准

### 必须达成（P0）

- ✅ 检测准确率不降低（差异 < 1%）
- ✅ 首错命中率不降低（差异 < 2%）
- ✅ 无新增崩溃或严重 bug
- ✅ 所有现有测试通过

### 期望达成（P1）

- ✅ 请求数减少 ≥ 15%
- ✅ Recall 延迟减少 ≥ 30%
- ✅ 代码覆盖率 ≥ 90%

### 加分项（P2）

- ✅ 请求数减少 ≥ 20%
- ✅ 提前终止率 ≥ 60%
- ✅ 证据强度评分人工审核通过率 ≥ 95%

---

## 后续工作

### P1 优化（需验证）

1. **P1.1 基于特征的 Recall 触发**
   - 预测哪些案例需要 Recall
   - 可能提升 3-5% 准确率

2. **P1.2 跨例缓存（阶段 1）**
   - 确定性组件缓存
   - 预计 10-15% 成本节省

3. **P1.3 多候选定位输出**
   - 诚实报告不确定性
   - 可能提升首错命中率到 55-60%

### P2 研究项目（长期）

1. **规则学习框架**
   - 统一诊断规则表示
   - 两类反例联合约束
   - 时间：12-18 个月

2. **主动证据选择**
   - 按信息增益选择补证
   - 时间：6-9 个月

---

## 联系和支持

**负责人**：[待指定]  
**代码审查**：[待指定]  
**测试负责**：[待指定]

**文档**：
- 技术细节：`docs/optimization-and-novelty-analysis.md`
- 配置指南：`docs/configuration-guide.md`（待创建）
- API 文档：`docs/detector-api.md`（待更新）

---

**最后更新**：2026-10-15  
**下次审查**：Week 4 整合测试后
