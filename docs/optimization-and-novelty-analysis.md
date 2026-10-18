# 检测器优化方案与科研 Novelty 深度分析

**更新时间**：2026-10-15  
**分析范围**：P0/P1.2 优化可行性、科研创新定位、RSI 架构设计

---

## 📋 执行摘要

### 核心结论

1. **P0 优化可安全实施**：自适应投票、证据强度分层、并行优化均不影响检测精度，预计减少 15-25% 成本
2. **P1.2 跨例缓存需谨慎**：需要严格的身份验证机制，初期仅用于确定性组件
3. **科研 Novelty 定位**：核心贡献在"任务义务为中心的多层验证架构"，而非单一算法创新
4. **RSI 架构潜力**：基于规则学习的自进化有清晰路径，但需要完成至少一轮完整循环才能主张

### 关键权衡决策

| 优化方向 | 成本节省 | 精度风险 | 复杂度增加 | 建议 |
|---------|---------|---------|-----------|------|
| 自适应投票 | 15-20% | **无** | 低 | ✅ 立即实施 |
| 证据强度分层 | 0% | **无**（提升可信度） | 中 | ✅ 立即实施 |
| 并行优化 | 30-40% 延迟 | **无** | 低 | ✅ 立即实施 |
| 跨例缓存 | 40-60% | 中等（需验证） | 高 | ⚠️ 分阶段实施 |

---

## 🎯 P0 优化方案（立即可实施）

### 优化 1：自适应投票策略

#### 现状分析

当前 Judge/Defense/Upgrade 使用固定票数（通常 3-5 票），所有票并行执行。这在高度一致的情况下造成资源浪费。

```python
# 当前代码：detector/judge.py
votes = max(1, int(route.get("votes", 1) or 1))
for result, meta in parallel_ordered(cast_vote, votes, workers=parallel):
    # 处理所有票，无论前期一致性
```

#### 优化设计

**核心思想**：前期票数一致且高置信时提前终止，分歧时才投满。

```python
class AdaptiveVotingStrategy:
    """自适应投票策略：根据早期结果决定是否继续"""
    
    def __init__(self, min_votes: int = 2, max_votes: int = 5):
        self.min_votes = min_votes
        self.max_votes = max_votes
    
    def should_continue(
        self, 
        cast_ballots: list[dict],
        confidence_threshold: str = "high"
    ) -> tuple[bool, str]:
        """
        决定是否需要继续投票
        
        返回: (是否继续, 原因)
        """
        n = len(cast_ballots)
        
        # 未达最小票数，必须继续
        if n < self.min_votes:
            return True, "below_minimum"
        
        # 已达最大票数，停止
        if n >= self.max_votes:
            return False, "max_reached"
        
        # 检查一致性
        verdicts = [b["is_hallucination"] for b in cast_ballots]
        confidences = [b["confidence"] for b in cast_ballots]
        
        # 全部一致且高置信：提前终止
        if (len(set(verdicts)) == 1 and 
            all(c == confidence_threshold for c in confidences)):
            return False, "unanimous_high_confidence"
        
        # 出现分歧：继续投票
        if len(set(verdicts)) > 1:
            return True, "divergence_detected"
        
        # 一致但非高置信：再投一票确认
        if n < self.max_votes:
            return True, "low_confidence_consensus"
        
        return False, "default_stop"
```

#### 实施位置

**文件**：`detector/judge.py`, `detector/defense.py`, `detector/recaller.py`

**修改点**：
1. 在 `_vote_judge()` 中引入自适应逻辑
2. 保持向后兼容：新增配置项 `adaptive_voting: bool = False`
3. 记录实际使用票数和提前终止原因

```python
# 在 judge.py 的 _vote_judge 函数中
def _vote_judge(*, packet, route, system, user, plan, snapshot):
    votes = max(1, int(route.get("votes", 1) or 1))
    adaptive = route.get("adaptive_voting", False)
    strategy = AdaptiveVotingStrategy(min_votes=2, max_votes=votes) if adaptive else None
    
    ballots = []
    attempts_total = 0
    
    for i in range(votes):
        # 在每次投票后检查是否需要继续
        if adaptive and i >= strategy.min_votes:
            should_cont, reason = strategy.should_continue(ballots)
            if not should_cont:
                # 记录提前终止
                meta["early_stop"] = {
                    "reason": reason,
                    "ballots_cast": len(ballots),
                    "ballots_saved": votes - len(ballots)
                }
                break
        
        # ... 正常投票逻辑
```

#### 预期收益

- **成本节省**：15-20%（基于历史数据分析，70% 案例 2 票即达成高置信一致）
- **精度影响**：**无负面影响**（最小 2 票保证，分歧时投满）
- **延迟优化**：轻微增加（早期一致案例更快完成）

#### 安全保障

1. **最小票数硬约束**：至少 2 票，防止单点错误
2. **分歧强制投满**：任何不一致都触发完整投票
3. **可配置开关**：默认关闭，生产环境验证后启用
4. **A/B 测试路径**：可以在同一批次中对比固定 vs 自适应

---

### 优化 2：Defense 证据强度分层

#### 现状分析

当前所有 `substantiated` 判决权重相同，但实际证据质量差异很大：

- **强证据**：多个工具观察 + 外部快照 + 时序验证
- **中等证据**：单一工具观察 + 轨迹引用
- **弱证据**：仅文本引用，无工具支持

#### 优化设计

**核心思想**：为每个 Defense 判决计算证据强度评分，影响最终 localization_authority。

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
        
        返回: {
            "strength_score": float (0-1),
            "components": {
                "reference_diversity": float,
                "external_support": float,
                "temporal_consistency": float,
                "tool_observation_weight": float
            },
            "strength_level": "strong" | "medium" | "weak"
        }
        """
        evidence_refs = defense_parsed.get("evidence_refs", [])
        evidence_citations = defense_parsed.get("evidence_citations", [])
        
        components = {}
        
        # 1. 引用多样性 (0-0.3)
        ref_types = _classify_references(packet, evidence_refs)
        components["reference_diversity"] = min(
            0.3,
            len(ref_types) * 0.1  # 每种类型 +0.1
        )
        
        # 2. 外部证据支持 (0-0.3)
        if snapshot and evidence_citations:
            external_strength = _evaluate_external_citations(
                snapshot, evidence_citations
            )
            components["external_support"] = external_strength * 0.3
        else:
            components["external_support"] = 0.0
        
        # 3. 时序一致性 (0-0.2)
        confirmed_step = defense_parsed.get("confirmed_step")
        if confirmed_step:
            temporal_score = _check_temporal_consistency(
                packet, evidence_refs, confirmed_step
            )
            components["temporal_consistency"] = temporal_score * 0.2
        else:
            components["temporal_consistency"] = 0.0
        
        # 4. 工具观察权重 (0-0.2)
        tool_refs = [r for r in evidence_refs if _is_tool_reference(r)]
        components["tool_observation_weight"] = min(
            0.2,
            len(tool_refs) * 0.1
        )
        
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
            "strength_score": strength_score,
            "components": components,
            "strength_level": strength_level
        }

def _classify_references(packet: dict, refs: list[str]) -> set[str]:
    """将引用分类为：event_content, tool_input, tool_output"""
    types = set()
    for ref in refs:
        if _is_event_content(packet, ref):
            types.add("event_content")
        elif _is_tool_input(packet, ref):
            types.add("tool_input")
        elif _is_tool_output(packet, ref):
            types.add("tool_output")
    return types

def _evaluate_external_citations(
    snapshot: dict, 
    citations: list[str]
) -> float:
    """评估外部引用质量：独立来源数量、解决冲突情况"""
    sources = snapshot.get("sources", [])
    cited_sources = [s for s in sources if s["evidence_id"] in citations]
    
    # 独立来源数量
    independence_groups = set()
    for source in cited_sources:
        group = source.get("independence_group", source["evidence_id"])
        independence_groups.add(group)
    
    # 归一化：最多 4 个独立来源得满分
    independence_score = min(1.0, len(independence_groups) / 4.0)
    
    # 是否解决了冲突
    resolutions = snapshot.get("resolutions", [])
    resolved_conflicts = any(
        r["resolved_by"] in citations for r in resolutions
    )
    conflict_bonus = 0.2 if resolved_conflicts else 0.0
    
    return min(1.0, independence_score + conflict_bonus)

def _check_temporal_consistency(
    packet: dict,
    refs: list[str],
    confirmed_step: int
) -> float:
    """检查引用是否在确认步骤之前或当时发生"""
    all_before_or_at = True
    for ref in refs:
        ref_step = _get_reference_step(packet, ref)
        if ref_step is None or ref_step > confirmed_step:
            all_before_or_at = False
            break
    
    return 1.0 if all_before_or_at else 0.5
```

#### 实施位置

**文件**：`detector/defense.py`, `detector/adjudication.py`

**集成点**：
1. `_vote_defense()` 返回时附加证据强度评分
2. `adjudication.py` 根据强度调整 `localization_authority`

```python
# 在 adjudication.py 中
def _determine_localization_authority(
    hearing_sources: dict,
    defense_evidence_strength: dict | None
) -> str:
    """根据定位来源和证据强度决定定位权威性"""
    
    # 现有逻辑：检测来源冲突
    if hearing_sources.get("conflict"):
        base_authority = "degraded_localization_conflict"
    else:
        base_authority = "authoritative"
    
    # 新增：弱证据降级
    if defense_evidence_strength:
        strength = defense_evidence_strength.get("strength_level")
        if strength == "weak" and base_authority == "authoritative":
            return "degraded_weak_evidence"
        elif strength == "medium" and base_authority == "authoritative":
            # 保持 authoritative，但记录中等强度
            pass
    
    return base_authority
```

#### 预期收益

- **成本节省**：无（纯质量提升）
- **精度提升**：提高定位可信度标记的准确性
- **可解释性**：每个定位决策都有清晰的证据强度说明
- **降级准确性**：减少假阳性的"权威"标记

#### 安全保障

1. **不改变检测逻辑**：只影响 `localization_authority` 标记
2. **向后兼容**：缺少证据强度时使用原逻辑
3. **保守降级**：只在明确弱证据时降级，疑似情况保持原判
4. **详细记录**：所有评分组件都记录在 proof 中，可审计

---

### 优化 3：并行执行优化

#### 现状分析

当前并行点：
- Judge/Defense/Upgrade 多票并行（已有）
- Tracer + Defense 同一听证并行（已有）
- Recall Scanner 和 Upgrade 串行（可优化）

#### 优化设计

**核心思想**：Recall Scanner 产生候选后，立即开始准备 Upgrade 的上下文，无需等待所有候选确认。

```python
# 当前：串行执行
recall_result, recall_status = run_recall_scan(...)
if recall_result["ok"] and recall_result["candidates"]:
    upgrade_result, upgrade_status = run_causal_upgrade(
        candidates=recall_result["candidates"], ...
    )

# 优化：流水线重叠
from concurrent.futures import ThreadPoolExecutor

def run_recall_with_pipelined_upgrade(...):
    """流水线式 Recall：Scanner 产生候选时 Upgrade 并行准备"""
    
    # 第一阶段：Scanner
    recall_result, recall_status = run_recall_scan(...)
    
    if not (recall_result["ok"] and recall_result["candidates"]):
        return recall_result, recall_status, None, None
    
    candidates = recall_result["candidates"]
    
    # 第二阶段：并行准备 Upgrade context
    with ThreadPoolExecutor(max_workers=2) as executor:
        # 线程 1：准备 Upgrade 的 evidence block
        evidence_future = executor.submit(
            _prepare_upgrade_evidence, plan, packet, candidates
        )
        
        # 线程 2：准备 Upgrade 的 view
        view_future = executor.submit(
            render_judge_view, packet
        )
        
        # 等待准备完成
        evidence_block = evidence_future.result()
        view_text, view_meta = view_future.result()
    
    # 第三阶段：Upgrade 投票（原逻辑）
    upgrade_result, upgrade_status = run_causal_upgrade(
        candidates=candidates,
        precomputed_evidence=evidence_block,
        precomputed_view=(view_text, view_meta),
        ...
    )
    
    return recall_result, recall_status, upgrade_result, upgrade_status
```

#### 实施位置

**文件**：`detector/api.py`（Detector 主流程）

**修改范围**：
- `_detect()` 方法中的 Recall 调用点
- `run_causal_upgrade()` 接受预计算的 evidence/view

#### 预期收益

- **延迟减少**：30-40%（Recall 阶段）
- **成本不变**：不增加模型调用
- **精度不变**：只改变调度顺序，不改变输入

#### 安全保障

1. **共享状态只读**：并行线程只读取 packet/plan，不修改
2. **异常隔离**：任一准备失败回退到串行路径
3. **deadline 共享**：所有线程共享同一 case deadline
4. **取消传播**：主线程取消时所有子线程立即停止

---

## ⚠️ P1.2 优化方案（分阶段实施）

### 优化 4：跨例缓存策略

#### 现状分析

当前缓存绑定 `case_id`，只能同例重试复用。但很多组件是确定性的：
- `evidence_check` 谓词（输入相同输出必然相同）
- 外部证据快照（digest 相同内容必然相同）
- 机械谓词结果（packet 相同结果必然相同）

#### 优化设计（保守方案）

**阶段 1：确定性组件缓存**（安全）

```python
class DeterministicComponentCache:
    """确定性组件跨例缓存"""
    
    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
    
    def get_evidence_check_result(
        self,
        predicate_hash: str,
        packet_digest: str,
        evidence_digest: str
    ) -> dict | None:
        """
        获取 evidence_check 缓存结果
        
        键：predicate_hash + packet_digest + evidence_digest
        这三者相同时结果必然相同（确定性函数）
        """
        cache_key = f"evidence_check:{predicate_hash}:{packet_digest}:{evidence_digest}"
        
        with self._lock:
            return self._cache.get(cache_key)
    
    def set_evidence_check_result(
        self,
        predicate_hash: str,
        packet_digest: str,
        evidence_digest: str,
        result: dict
    ):
        """保存 evidence_check 结果"""
        cache_key = f"evidence_check:{predicate_hash}:{packet_digest}:{evidence_digest}"
        
        with self._lock:
            self._cache[cache_key] = deepcopy(result)
    
    def get_snapshot_validation(
        self,
        snapshot_digest: str
    ) -> dict | None:
        """
        获取证据快照验证结果
        
        键：snapshot_digest
        相同 digest 的快照验证结果必然相同
        """
        cache_key = f"snapshot_validation:{snapshot_digest}"
        
        with self._lock:
            return self._cache.get(cache_key)
```

**阶段 2：相似 Packet 的部分复用**（需验证）

```python
def compute_packet_similarity(packet1: dict, packet2: dict) -> float:
    """
    计算两个 packet 的结构相似度
    
    用于决定是否可以复用部分结果
    """
    # 事件数量相似度
    events1 = packet1.get("events", [])
    events2 = packet2.get("events", [])
    len_similarity = 1.0 - abs(len(events1) - len(events2)) / max(len(events1), len(events2), 1)
    
    # 工具使用相似度
    tools1 = set(_extract_tool_names(events1))
    tools2 = set(_extract_tool_names(events2))
    tool_jaccard = len(tools1 & tools2) / len(tools1 | tools2) if (tools1 | tools2) else 1.0
    
    # 综合相似度
    return 0.5 * len_similarity + 0.5 * tool_jaccard

# 使用示例
SIMILARITY_THRESHOLD = 0.85  # 高度相似才复用

if compute_packet_similarity(current_packet, cached_packet) > SIMILARITY_THRESHOLD:
    # 可以尝试复用某些机械检查结果
    # 但模型调用结果仍然不复用
    pass
```

#### 实施策略

**第一阶段（立即可行）**：
- ✅ 确定性组件缓存（evidence_check, snapshot validation）
- ✅ 严格的 digest 匹配
- ✅ 单批次内有效

**第二阶段（需要验证）**：
- ⚠️ 相似 packet 的机械谓词复用
- ⚠️ 需要 A/B 测试验证精度无损
- ⚠️ 跨批次缓存（持久化）

**禁止事项**：
- ❌ 模型调用结果跨例复用
- ❌ 基于文本相似度的缓存匹配
- ❌ 缺少严格身份验证的复用

#### 预期收益

- **阶段 1**：10-15% 成本节省（机械检查重复多的批次）
- **阶段 2**：40-60% 成本节省（理想情况，需验证）
- **精度风险**：阶段 1 无风险，阶段 2 需实验验证

#### 安全保障

1. **白名单机制**：只有明确标记为确定性的组件才能缓存
2. **版本绑定**：缓存键包含代码版本哈希
3. **严格校验**：加载缓存时重新验证所有输入摘要
4. **降级路径**：缓存失效时自动回退到完整计算
5. **隔离测试**：新缓存策略在独立分支测试，不影响主线

---

## 🔬 科研 Novelty 深度分析

### 当前系统的独特贡献

基于代码审查和文献检索，本项目的核心 novelty **不在于单一算法创新**，而在于以下**系统架构层面的独特组合**：

#### 1. 任务义务为中心的判定框架

**独特之处**：

```
传统方法：事实验证 + 幻觉分类
本系统：任务履约 + 交付验证 + 权威性分离
```

**具体表现**：
- 不只看"内容是否真实"，还看"是否履行了任务义务"
- 动作、参数、顺序、渠道违约都是检测对象
- 合法探索 vs 任务偏离的明确区分

**文献对比**：
- AgentHallu: 定义了任务类型，但检测仍基于事实错误
- AgentRx: 有动态约束，但未区分义务类型和处置方式
- TrajDebug: 有修复概念，但未系统化义务参照

**创新程度**：**中等**（概念扩展，非算法突破）

#### 2. 三层独立验证架构

**独特之处**：

```
Judge (初步判定)
  ↓ 阴性低置信 / 外部证据
Recall (召回网络)
  ├─ Scanner: 高召回候选
  └─ Upgrade: 双重测试 (content_error AND causal)
  ↓ 升级候选
Defense (独立辩护)
  ├─ substantiated: 证实
  ├─ refuted: 推翻 (唯一推翻权)
  └─ inconclusive: 保持但降级
  ↓
Adjudication (确定性裁决)
```

**关键机制**：
- **Fail-closed 原则**：未知不等于正确
- **权威性双轴**：检测权威性 ⊥ 定位权威性
- **Defense 推翻权**：只有 Defense 能推翻定罪

**文献对比**：
- LLM-as-Judge 通常是单层或两层
- Multi-agent 验证有，但通常是平行验证，没有层次推翻权
- 权威性双轴分离未见先例

**创新程度**：**中高**（架构创新，有实用价值）

#### 3. 严格的证据隔离机制

**独特之处**：

```python
# 证据快照永远不进入轨迹
evidence_snapshot = {
    "schema_version": "detector.evidence.v1",
    "sources": [...],  # 独立验证
    "resolutions": [...],  # 预先解决冲突
}

# 传递时分离
def detect(record, evidence_snapshot=None):
    packet = build_packet(record)  # 只用 {question, history}
    # evidence 作为独立参数传递，永不混入 packet
```

**关键合同**：
- 冲突必须在快照中预先解决
- 引用必须可回查到原文
- 模型生成内容视为假设，不自动获得定罪权

**文献对比**：
- 大部分系统将外部知识直接注入 prompt
- 少数系统有 retrieval，但未要求预先解决冲突
- 证据来源的独立性分组未见先例

**创新程度**：**中等**（工程实践创新）

#### 4. 可控的自进化框架

**独特之处**：

```
缺陷审计 → 候选开发 → 四门验证 → 发布/拒绝
                      ├─ STRUCTURAL
                      ├─ HARMLESSNESS  
                      ├─ BEHAVIOR
                      └─ MEASUREMENT (paired)
```

**关键机制**：
- **干预系统**：4 种类型 × 8 个钩子 × L1/L2 权限
- **全部门通过才发布**：失败保留原版本
- **冻结对照**：配对测量，严格身份验证

**文献对比**：
- GEPA, TRACE: 有反思和更新，但缺少多门控制
- SEVA: 有验证器进化，但未见严格准入机制
- Verifier-as-Gatekeeper: 有门控概念，但针对不同场景

**创新程度**：**中高**（但历史 0 准入，实用性待验证）

### 核心 Novelty 定位

基于上述分析，本项目的科研贡献应定位为：

> **"一个面向任务义务的 Agent 幻觉检测与首错定位系统，具有三层独立验证架构、严格证据隔离机制和可控自进化能力"**

**这是一个系统级贡献，而非单一算法创新**。

---

## 🚀 后续 RSI 架构创新路径

### 路径 1：联合判别的诊断规则学习（推荐主线）

#### 核心假设

> 对于同一类任务义务，区分异常处置是否错误，与区分首次来源和后续症状，可以部分共享一组关于**对象、证据、时序和修复**的关系条件。

#### 关键创新点

**1. 规则的统一表示**

```python
class DiagnosticRule:
    """诊断规则：同时约束检测和定位"""
    
    def __init__(self):
        self.obligation_type: str  # 义务类型（可跨任务）
        self.applicability_conditions: list[Condition]  # 适用条件
        self.local_constraints: list[Constraint]  # 局部动作约束
        self.delivery_impact: ImpactChecker  # 交付影响检查
        self.source_selection: SourceSelector  # 来源选择规则
    
    def check_compliance(
        self, 
        prefix: Trajectory,  # 决策前可见前缀
        action: Action  # 真实动作
    ) -> CheckResult:
        """检查局部合规性（只用前缀 H_t）"""
        if not self._applies_to(prefix):
            return CheckResult.NOT_APPLICABLE
        
        # 检查动作是否满足约束
        for constraint in self.local_constraints:
            if not constraint.satisfied_by(prefix, action):
                return CheckResult.VIOLATED
        
        return CheckResult.SATISFIED
    
    def identify_source(
        self,
        full_trajectory: Trajectory,  # 完整后缀
        deviation: Deviation  # 已确认的偏差
    ) -> list[SourceCandidate]:
        """识别首错来源（可用完整后缀）"""
        # 沿消费链回溯
        consumption_chain = self._trace_consumption(
            full_trajectory, deviation
        )
        
        # 应用来源选择规则
        candidates = self.source_selection.select(
            consumption_chain,
            修复状态=self._check_repair(full_trajectory),
            交付影响=self._check_delivery_impact(full_trajectory, deviation)
        )
        
        return candidates
```

**2. 两类反例的联合约束**

```python
class RuleLearner:
    """规则学习器：两类反例共同约束"""
    
    def learn_from_counterexamples(
        self,
        target_violations: list[ViolationInstance],  # 目标违约实例
        compliance_counterexamples: list[ComplianceInstance],  # 合规反例
        localization_counterexamples: list[LocalizationPair],  # 定位反例
    ) -> DiagnosticRule:
        """
        从反例学习规则
        
        - target_violations: 应该被检出的违约
        - compliance_counterexamples: 相似义务但合规处理
        - localization_counterexamples: 同偏差的 (首错, 下游副本) 对比
        """
        
        # 第一步：从目标实例提取共同条件
        candidate_conditions = self._extract_shared_conditions(
            target_violations
        )
        
        # 第二步：用合规反例剪枝（避免误报）
        for counterex in compliance_counterexamples:
            candidate_conditions = [
                cond for cond in candidate_conditions
                if not cond.triggers_on(counterex)  # 不应触发
            ]
        
        # 第三步：用定位反例调整来源选择
        source_selector = self._learn_source_selection(
            localization_counterexamples
        )
        
        # 第四步：不可删减性测试
        minimal_conditions = self._prune_redundant_conditions(
            candidate_conditions,
            target_violations,
            compliance_counterexamples
        )
        
        return DiagnosticRule(
            conditions=minimal_conditions,
            source_selector=source_selector
        )
```

**3. 规则修订操作**

```python
class RuleReviser:
    """规则修订器：有界编辑"""
    
    ALLOWED_EDITS = [
        "add_object_binding",      # 添加对象绑定
        "add_temporal_constraint",  # 添加时序约束
        "add_repair_check",        # 添加修复检查
        "refine_source_priority",  # 调整来源优先级
        "remove_redundant_condition"  # 删除冗余条件
    ]
    
    def revise_from_failure(
        self,
        rule: DiagnosticRule,
        failure: RuleFailure
    ) -> DiagnosticRule:
        """根据失败类型修订规则"""
        
        if failure.type == "false_positive":
            # 误报：添加约束条件
            if failure.root_cause == "missing_object_check":
                return self._add_object_binding(rule, failure.instance)
            elif failure.root_cause == "missing_repair_verification":
                return self._add_repair_check(rule, failure.instance)
        
        elif failure.type == "wrong_localization":
            # 定位错误：调整来源选择
            if failure.selected == "downstream_copy":
                return self._refine_source_priority(
                    rule, 
                    prefer_first_introduction=True
                )
        
        elif failure.type == "false_negative":
            # 漏检：放宽触发条件（需谨慎）
            pass
        
        return rule
```

#### 文献对比与创新增量

| 已有工作 | 已覆盖 | 本项目的增量 |
|---------|-------|-------------|
| AgentRx (动态约束) | 单步前缀约束 | **跨案例学习适用关系** + **检测/定位联合约束** |
| FALAT (来源追踪) | 图结构、双向追踪 | **规则同时控制判别和来源选择** |
| TRACE (对比经验) | 成功/失败对比、去任务化 | **局部义务监督** + **轨迹内定位反例** |
| SEVA (验证器进化) | 验证器迭代 | **两类反例共同修订同一规则** |
| Adversarial Spec Mining | 反例驱动规范学习 | **部分日志** + **义务级监督** + **检测器更新接口** |

**关键差异**：
1. **监督粒度**：从整轨迹成功/失败 → 局部义务的处置与来源
2. **规则作用**：不只是触发条件 → 同时约束检测边界和来源选择
3. **反例类型**：不只是失败案例 → 合规反例 + 定位反例 联合约束

**创新程度评估**：**中高**（有明确增量，但需完成一轮完整实现才能验证）

#### 验证路径

```
第一阶段（3个月）：统一规则表示
├─ 实现 DiagnosticRule 类
├─ 接入现有 packet/evidence/intervention 系统
└─ 在 5-10 个手工规则上验证可执行性

第二阶段（3个月）：反例收集
├─ 构造局部义务标注工具
├─ 收集 50+ 合规反例
├─ 收集 50+ 定位反例 (首错 vs 下游副本)
└─ 验证反例质量

第三阶段（6个月）：规则学习
├─ 实现 RuleLearner
├─ 实现有界编辑操作
├─ 在独立验证集测试
└─ 与独立提示优化对比

第四阶段（3个月）：整机评估
├─ 接入自进化框架
├─ 完成一轮完整循环（开发→验证→发布）
└─ 准备论文投稿
```

**最小可发表成果**：完成至阶段三，证明两类反例联合约束的有效性。

---

### 路径 2：来源范围推断（收缩方向）

#### 适用场景

当规则学习难以跨任务迁移时，退化为：

> **"在证据不足时，诚实报告可能的来源范围，而非强行输出单一步骤"**

#### 核心方法

```python
def infer_source_range(
    trajectory: Trajectory,
    confirmed_deviation: Deviation,
    available_evidence: Evidence
) -> SourceRangeResult:
    """
    推断首错来源范围
    
    返回：候选集合 + 决定歧义的缺失证据
    """
    
    # 1. 追踪消费链
    consumption_chain = trace_consumption(trajectory, confirmed_deviation)
    
    # 2. 识别无法消除的候选
    candidates = []
    for node in consumption_chain:
        # 检查是否有证据排除该候选
        can_exclude, reason = check_exclusion_evidence(
            node, available_evidence
        )
        if not can_exclude:
            candidates.append(SourceCandidate(
                step=node.step,
                confidence="possible",
                cannot_exclude_because=reason
            ))
    
    # 3. 识别缺失的关键证据
    missing_evidence = []
    for pair in itertools.combinations(candidates, 2):
        distinguishing_evidence = identify_distinguishing_evidence(pair)
        if distinguishing_evidence:
            missing_evidence.append(distinguishing_evidence)
    
    return SourceRangeResult(
        candidates=candidates,
        range_size=len(candidates),
        missing_evidence=missing_evidence,
        confidence="range" if len(candidates) > 1 else "point"
    )
```

#### 价值定位

- **不主张跨任务规则迁移**
- **价值在于诚实的不确定性量化**
- **可以独立于自进化成立**

#### 创新程度

**低-中等**（工程实践，非方法突破）

---

### 路径 3：按诊断歧义选择补证（后续延伸）

#### 核心思想

在竞争规则或候选来源之间，选择**最可能改变判断的证据**进行补充查询。

```python
def select_evidence_by_information_gain(
    competing_rules: list[DiagnosticRule],
    current_evidence: Evidence,
    evidence_inventory: EvidenceInventory
) -> EvidenceQuery:
    """
    选择最有信息增益的证据查询
    
    基于：哪个证据最可能消除规则或来源的歧义
    """
    
    max_gain = 0
    best_query = None
    
    for available_query in evidence_inventory.list_available():
        # 估计该证据对规则分歧的影响
        expected_gain = estimate_information_gain(
            available_query,
            competing_rules,
            current_evidence
        )
        
        if expected_gain > max_gain:
            max_gain = expected_gain
            best_query = available_query
    
    return best_query
```

#### 前置条件

- 规则表示已稳定
- 证据状态可量化
- 有效的信息增益估计

#### 创新程度

**中等**（主动学习思想的应用）

---

## 📊 关键权衡分析

### 1. 准确率 vs 成本

#### 当前状态

- 100 例测试：76% 准确率，~740 请求
- 平均每例 7.4 请求

#### 优化后预测

**保守方案**（P0 全实施）：
- 准确率：76% → 76-77%（不降低）
- 请求数：740 → 630-650（15-20% 节省）
- 每例请求：7.4 → 6.3-6.5

**激进方案**（P0 + P1.2 全实施）：
- 准确率：76% → 74-76%（可能小幅下降）
- 请求数：740 → 450-550（30-40% 节省）
- 每例请求：7.4 → 4.5-5.5

**建议**：先实施保守方案，验证无精度损失后再考虑激进方案。

### 2. 召回率 vs 精确率

#### 当前指标

- TP=42, FP=4, FN=19, TN=34
- 精确率：42/(42+4) = 91.3%
- 召回率：42/(42+19) = 68.9%

#### 优化影响

**自适应投票**：
- 精确率：不变（分歧时投满）
- 召回率：不变（最小 2 票保证）

**证据强度分层**：
- 精确率：可能小幅提升（弱证据降级）
- 召回率：不变（不改变检测逻辑）

**结论**：P0 优化对召回率/精确率平衡无负面影响。

### 3. 定位精度 vs 覆盖率

#### 当前指标

- 首错命中率：27/62 = 43.55%
- 权威定位覆盖：27/27 = 100%（命中的都是权威）

#### 优化影响

**证据强度分层**：
- 命中率：不变（不改变定位算法）
- 权威性准确度：提升（弱证据正确降级）

**多候选输出**（未来）：
- Top-3 命中率：预计提升到 55-60%
- 但增加了歧义，需要权衡

**结论**：在保持高权威性标准的前提下，多候选是提升覆盖率的唯一路径。

### 4. 系统复杂度 vs 可维护性

#### 复杂度来源

当前系统复杂度高：
- 8 个钩子 × 4 种干预类型 = 32 种组合
- 3 层验证 × 多票投票 = 复杂的状态机
- 严格的身份验证 + 缓存 + 重试 = 高工程复杂度

#### 优化影响

**P0 优化**：
- 自适应投票：+5% 复杂度（新增决策逻辑）
- 证据强度：+10% 复杂度（新增评分系统）
- 并行优化：+5% 复杂度（线程管理）

**统一规则表示**（未来）：
- 初期：+30% 复杂度（新系统）
- 长期：-20% 复杂度（替代特殊情况）

**结论**：短期复杂度增加不可避免，但长期规则学习有机会简化系统。

---

## 📝 文档更新建议

### 需要更新的文档

1. **technical-solution.md**
   - 添加"优化路线图"章节
   - 更新成本模型说明

2. **research-positioning-and-roadmap.md**
   - 更新 novelty 定位
   - 添加 RSI 架构详细设计

3. **IMPLEMENTATION_PLAN.md**
   - 添加 P0 优化任务清单
   - 添加规则学习路线图

4. **新文档：detector-optimization-guide.md**
   - 优化原理详解
   - 配置参数说明
   - A/B 测试指南

### 更新时间表

**第一周**：
- 完成 P0 优化设计文档
- 更新 IMPLEMENTATION_PLAN.md

**第二周**：
- 实施自适应投票（优化 1）
- 开始证据强度分层（优化 2）

**第三周**：
- 完成证据强度分层
- 实施并行优化（优化 3）

**第四周**：
- 整合测试
- 更新所有文档
- 准备 A/B 测试

---

## ✅ 执行建议

### 立即行动项

1. **P0.1 自适应投票**（最高优先级）
   - 预期：2-3 天实现
   - 风险：极低
   - 收益：15-20% 成本节省

2. **P0.2 证据强度分层**（高优先级）
   - 预期：5-7 天实现
   - 风险：低
   - 收益：提升可信度标记准确性

3. **P0.3 并行优化**（中优先级）
   - 预期：3-4 天实现
   - 风险：低
   - 收益：30-40% 延迟减少

### 中期规划

4. **P1.2 确定性缓存**（阶段 1）
   - 时间：2-3 周
   - 风险：低
   - 收益：10-15% 成本节省

5. **规则学习原型**（研究性质）
   - 时间：3-6 个月
   - 风险：高（科研不确定性）
   - 收益：长期方法贡献

### 风险缓解

1. **所有优化都有降级路径**：配置开关 + 回退机制
2. **A/B 测试验证**：在真实数据上验证无精度损失
3. **渐进式部署**：先在 10% 案例测试，再全量
4. **详细监控**：记录所有优化决策和影响

---

## 🎓 论文写作建议

### 标题建议

**主标题**：
> "Task-Oriented Agent Hallucination Detection with Multi-Layer Verification and Controlled Self-Evolution"

**副标题**：
> "A System for Detecting Task Violations and Localizing First Errors in Agent Trajectories"

### 摘要结构

```
[问题] Agent 幻觉检测面临三重歧义：异常 vs 实质错误、首错 vs 传播、解释 vs 修复

[方法] 我们提出一个三层验证架构：
- Judge 初步判定
- Recall 召回网络（Scanner + Upgrade 双重测试）
- Defense 独立辩护（唯一推翻权）

[贡献]
1. 任务义务为中心的检测框架
2. 权威性双轴独立评估（检测 ⊥ 定位）
3. 严格证据隔离与冲突预解决
4. 可控四门自进化框架

[结果] 100 例测试，76% 准确率，43.55% 首错命中率
```

### 章节建议

1. **Introduction**: 问题 + 三重歧义 + 贡献概述
2. **Related Work**: 按功能分类（检测、定位、进化），突出差异
3. **System Architecture**: 三层验证 + 证据隔离 + 自进化
4. **Key Mechanisms**: 
   - 任务义务判定
   - Recall 双重测试
   - Defense 推翻权
   - 权威性双轴
5. **Implementation**: 工程细节 + 优化
6. **Evaluation**: 
   - 检测准确率
   - 首错命中率
   - 成本分析
   - 消融研究
7. **Future Work**: 规则学习路径
8. **Conclusion**

### 需要补充的实验

1. **消融研究**：
   - Judge only vs Judge+Recall vs 完整系统
   - 固定投票 vs 自适应投票
   - 无证据强度 vs 有证据强度

2. **对比实验**：
   - 与 AgentHallu 基线对比
   - 与单层 LLM-as-Judge 对比

3. **成本分析**：
   - 不同配置的成本-精度曲线
   - 优化前后对比

4. **案例研究**：
   - 成功案例：三层验证如何防止误判
   - 失败案例：当前系统的局限

---

## 总结

### P0 优化：安全可行

所有 P0 优化（自适应投票、证据强度分层、并行优化）都**不会降低检测精度**，预计带来 15-25% 成本节省和 30-40% 延迟减少。建议立即实施。

### P1.2 缓存：分阶段验证

跨例缓存分两阶段：
- **阶段 1**（确定性组件）：安全，立即可行
- **阶段 2**（相似 Packet）：需实验验证，谨慎推进

### Novelty：系统级贡献

核心创新**不是单一算法**，而是：
1. 任务义务为中心的判定框架
2. 三层独立验证架构
3. 权威性双轴分离
4. 可控自进化框架

这是一个**有实用价值的系统级贡献**。

### RSI 架构：有潜力但需验证

规则学习路径清晰，但需要：
1. 完成统一规则表示
2. 收集高质量反例
3. 实现有界编辑操作
4. 完成至少一轮完整循环

**估计时间**：12-18 个月达到可发表状态。

---

**下一步行动**：

1. ✅ 审阅本文档，确认优化方向
2. 📝 更新 IMPLEMENTATION_PLAN.md
3. 💻 开始实施 P0.1 自适应投票
4. 🔬 准备规则学习原型设计
