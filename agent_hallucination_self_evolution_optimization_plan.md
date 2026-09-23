# Agent-Hallucination 自进化框架优化实施方案

> 用途：指导 code agent 对当前仓库进行系统性代码优化，使自进化框架从“能够运行候选生成与门控流程”升级为“能够稳定发现真实失败机制、产生可执行修复、局部迭代、正式验证、部署并继承有效能力”的真实自进化系统。
>
> 适用仓库：`liuxinglin70-cmyk/Agent-Hallucination`
>
> 基于当前已核验代码、实验日志与 JEV / System One 调研结果整理。

---

## 0. 总结结论

当前框架的主要问题不是闭环不存在，而是闭环中的几个核心对象尚未对齐：

1. **失败家族按执行路径聚类多、按真实错误机制聚类少**，导致 proposer 面对的是“路径相似但修复机制不同”的案例集合。
2. **可生成候选 ≠ 具备修复能力**。当前 actionability 判断更多检查是否存在可用 hook / intervention route，而不是检查当前系统是否拥有完成修复所需的信息、表示、执行器和控制权。
3. **部署演化阶段缺少完整的候选内循环修订**。ResearchRun 中已经存在 draft → evaluate → revise，但 EvolutionEngine 的正式演化仍偏向“一轮一个新候选 + 下一轮再提案”。
4. **候选针对的机制与 gain set 之间缺乏机制级可测性对齐**。存在“有 recall opportunity，但没有当前机制的 repair opportunity”的情况。
5. **验证过于昂贵且噪声较大**，导致大量明显无效候选也进入昂贵的完整检测路径；同时单例翻转难以区分真实收益与 provider 随机性。
6. **没有被正式晋升的有效修改，就无法形成真正的程序性经验积累**，Memory / Registry 设施存在但没有足够的成功父代可以继承。
7. **JEV 的真正价值不是直接替换 Judge，而是提供一种工程范式：将开放式生成、结构化决策、概率化测量和正式验证分离。**

因此，本方案的核心路线是：

> **把 EvolutionEngine 从“候选生成器 + 评估器”升级为“机制诊断器 + 能力规划器 + 候选开发器 + 分层验证器 + 正式晋升器 + 可继承组件库”。**

最终的真实自进化闭环应当是：

```text
Patrol failures
    ↓
Mechanism diagnosis
    ↓
Capability requirement analysis
    ↓
Repair task construction
    ↓
Candidate development loop
  draft → local test → diagnose → revise
    ↓
Candidate freeze
    ↓
Mechanism-aligned promotion protocol
    ↓
Formal gates
    ↓
Commit active registry + reusable procedure/component
    ↓
Next generation inherits deployed capability
```

---

# 1. 优化目标与非目标

## 1.1 核心目标

优化后的框架必须能够做到：

### G1. 发现真实可迁移失败机制

不能只知道“Judge 错了”“Recall 没触发”“Tracer 选错步”，而要形成可操作的机制描述：

- 哪个任务义务被违反；
- 实际交付与预期关系在哪里不一致；
- 哪条事实或结构证据支持该判断；
- 检测器的哪个阶段为什么没有捕获它；
- 什么类型的修复操作才有可能解决它。

### G2. 判断系统是否真正具备修复能力

必须区分：

- candidate 可以生成；
- intervention 可以触发；
- intervention 可以改变中间状态；
- intervention 实际具备完成目标修复的能力。

### G3. 对候选进行开发而不是一次性猜测

一个候选失败后，应能够：

- 读取局部失败证据；
- 修订同一父候选；
- 对修订后的版本重新进行低成本开发测试；
- 在正式评估集冻结前完成开发。

### G4. 机制与评估机会严格对齐

正式评估前必须确认：

- gain set 中存在当前机制的可修复错误；
- sentinel 中存在当前能力可能破坏的正确案例；
- 机制外测试可用于检查副作用。

### G5. 晋升后的改动能够被下一代真实继承

被晋升的内容必须成为下一轮：

- active registry 的一部分；
- proposer 可检索的有效 procedure；
- 可重用 component / executor；
- 明确的版本父代。

---

## 1.2 非目标

本次优化不应：

- 通过放松 HARMLESSNESS 或允许新错误来制造“自进化成功”；
- 用 LLM 或 JEV 的预测结果直接替代正式 promotion score；
- 用同一批已观察正式结果反复搜索候选直到通过；
- 将 hosted Jev 直接作为不可冻结的终局判定器；
- 依赖 case id、gold label、特定答案常量生成修复规则；
- 把纯概率分数改善解释成检测能力或定位能力真实提升。

---

# 2. 当前架构中的关键问题

## 2.1 Failure family 表示过度依赖执行路径

当前 `evolution/worksheet.py::_mechanism_signature()` 主要记录：

- judge 是否正向；
- recall / upgrade / tracer / defense 的 stage state；
- recall path；
- upgrade 是否正向；
- defense verdict；
- positive source。

该签名非常适合回答：

> “检测流程在哪里出了问题？”

但不足以回答：

> “Agent 实际犯了哪种错误？同一修复为什么应该同时适用于这些案例？”

### 风险

路径相同但机制不同的案例会被合并，导致：

- proposer 生成过宽规则；
- recall trigger 在正常案例上误触发；
- tracer prompt 对不属于同一定位机制的案例失效；
- family unanimity 无法代表真正的机制一致性。

---

## 2.2 Actionability 将“存在 route”误认为“具备能力”

当前 `classify_family_actionability()` 只要存在某种合法 route，例如：

- `recall_trigger`
- `recall_directive`
- `upgrade_directive`
- `evolved_predicate`
- `tracer_directive`

就倾向于把 family 标记为 actionable。

但以下三件事必须分开：

```text
Generatable → Triggerable → Repair-capable
```

例如 Attempt 5 已经证明：

- tracer directive 能生成；
- hook 能触发；
- reasoning 确实变化；
- 最终 responsible_step 仍然没有修复。

说明“可触发”不能推出“可修复”。

---

## 2.3 Candidate development loop 不完整

`evolution/research.py` 已经拥有：

```text
draft → evaluate → screening verdict → revise → re-evaluate
```

但 `EvolutionEngine` 中的部署型演化主要是：

```text
cycle N: propose → gate → reject
cycle N+1: proposer reads rejection feedback → propose another candidate
```

这意味着候选失败后的修订过程：

- 缺少连续 parent-child 版本关系；
- 缺少针对具体失败案例的局部测试；
- 缺少“修复误报边界后再次测试”的明确开发阶段；
- 正式门控承担了过多候选开发工作。

---

## 2.4 Gain opportunity 不是 mechanism opportunity

当前 `gain_opportunity()` 基本按：

- recall：baseline FN 数量；
- precision：baseline FP 数量。

进行统计。

但一个针对 A 机制的候选，不能因为 gain set 中存在任意 FN 就被认为可测量。

必须改成：

```text
当前 candidate targeting mechanism M
        ↓
formal gain set 中是否存在 mechanism M 的 baseline failure？
```

---

## 2.5 评估成本过高

真实运行已经显示：

- 单个 case 可能非常慢；
- provider reasoning 波动明显；
- candidate 数量虽不多，但完整 paired run 成本高；
- 很多 candidate 在进入完整验证前就可以通过局部检查证明无效。

因此需要建立 L0/L1/L2 分层验证。

---

## 2.6 成功经验无法积累

当前：

- procedural memory 基本为空；
- improvement memory 基本为空；
- gap memory 基本为空；
- active registry 在最近真实 run 中仍为空。

如果所有候选都止步于正式门控，则系统没有“成功父代”，下一轮无法真正建立在上一代改进之上。

需要建立：

- candidate archive；
- reusable development component；
- promoted procedure；
- active version lineage。

---

# 3. 目标架构

建议将 evolution 系统划分为 7 个明确阶段。

## Stage A — Failure Discovery

来源：patrol / bootstrap / validation observations。

输出：原始失败实例。

现有模块可继续使用：

- `stable_failure_families()`
- `diagnose_failure_families()`

但只能作为初始粗筛。

---

## Stage B — Mechanism Diagnosis

新增正式对象：

```python
FailureMechanismV1
```

建议结构：

```python
{
  "schema_version": "evolution.failure_mechanism.v1",
  "mechanism_id": "...",
  "family_id": "...",
  "objective": "detection|localization|joint",

  "task_obligation": {
    "kind": "exact_delivery|tool_requirement|factual_claim|completion_claim|...",
    "subject": "...",
    "expected_relation": "..."
  },

  "observed_deviation": {
    "kind": "mismatch|omission|unsupported_claim|false_completion|...",
    "actual": "...",
    "expected": "..."
  },

  "evidence_bindings": [
    {
      "event_id": "...",
      "field": "...",
      "role": "obligation|actual_delivery|tool_result|claim_support"
    }
  ],

  "detector_failure": {
    "stage": "judge|recall|upgrade|tracer|defense|adjudication",
    "failure_mode": "...",
    "path_signature": {...}
  },

  "repair_hypothesis": {
    "operation": "compare_fields|retrieve_evidence|rewrite_prompt|new_executor|...",
    "required_inputs": [...],
    "required_capabilities": [...],
    "forbidden_shortcuts": [...]
  },

  "member_cases": [...],
  "normal_neighbors": [...],
  "status": "hypothesis|confirmed|ambiguous",
  "confidence_basis": "structural|multi_case|human_review|..."
}
```

### 机制确认标准

一个 mechanism 至少满足以下之一才允许进入 candidate development：

1. ≥2 个案例共享同一可验证 repair operation；
2. 1 个案例但存在强结构证据，且明确标记为 `single_case_research_only`；
3. localization 类问题通过 viability check，确认 gold step 具有可归责性且不是 annotation-choice ambiguity。

---

## Stage C — Capability Planning

新增对象：

```python
CapabilityRequirementV1
```

用于回答：当前系统“缺什么能力”，而不是“有哪些 intervention type”。

建议分类：

```text
information_missing
representation_missing
semantic_judgment_missing
control_flow_missing
executor_missing
localization_attribution_missing
prompt_only
already_supported
```

示例：

```python
{
  "mechanism_id": "M-001",
  "required_capability": "semantic_judgment_missing",
  "required_inputs_available": True,
  "current_routes": ["typed_dsl", "text_boundary"],
  "current_routes_sufficient": False,
  "evidence": [
    "typed_dsl cannot compare semantic equivalence",
    "tracer directive fired but responsible_step unchanged in controlled runs"
  ],
  "recommended_action_class": "new_executor"
}
```

### 核心原则

只有当：

```text
required_inputs_available == True
AND
recommended_action_class can be executed
```

才进入正式 development。

否则写入 `gaps.jsonl`。

---

# 4. Phase 1：重构 Failure Mechanism 建模

## 4.1 修改文件

重点：

- `evolution/worksheet.py`
- `evolution/gaps.py`
- 新增 `evolution/mechanisms.py`
- 新增对应 tests

---

## 4.2 新增 `evolution/mechanisms.py`

建议包含：

```python
@dataclass(frozen=True)
class FailureMechanism:
    ...


def infer_mechanism(...):
    ...


def mechanism_key(...):
    ...


def mechanism_compatible(a, b) -> bool:
    ...


def repair_operation_signature(mechanism) -> tuple:
    ...
```

### 不允许

- mechanism key 中包含 case id；
- mechanism key 中包含 gold step 常量；
- 直接复制 dataset annotation 成为 runtime rule；
- 用 source 名称作为主要区分字段。

### 允许

- 使用 projected trajectory 中的任务义务类别；
- 使用 delivery / tool / evidence 关系；
- 使用 detector path 作为次级特征；
- 使用现有 `mechanism_signature` 作为 detector failure 子结构。

---

## 4.3 修改 `stable_failure_families()`

当前 family 作为 coarse cluster 保留，但后续再执行：

```python
coarse_family
   ↓
mechanism extraction
   ↓
mechanism-compatible subgrouping
```

最终 proposer 输入不再只给：

```text
FAM-fn-judge_false_no_recall_candidates
```

而应给：

```text
Mechanism M-xxx
Task obligation: exact tool-argument delivery
Observed deviation: unauthorized text inserted into quoted argument
Detector miss: judge negative + no recall candidate
Repair operation: compare expected literal span against actual tool argument
```

---

## 4.4 验收测试

必须添加测试：

1. 两个 path signature 相同但 repair operation 不同的案例，不能被归为同一 mechanism。
2. 两个来源不同但具有相同 repair operation 的案例，可以归为同一 mechanism。
3. case id 替换后 mechanism key 不变。
4. source name 替换后 mechanism key 不应改变，除非 source 对 task contract 语义有必要影响。
5. localization ambiguity case 不能进入 confirmed mechanism。

---

# 5. Phase 2：实现真实 Capability Gap 分析

## 5.1 修改 `classify_family_actionability()`

不要删除现有 route enumeration，但在其上增加一层：

```python
analyze_capability_requirement(mechanism, capability_surface)
```

返回：

```python
{
  "classification": "actionable|capability_gap|research_only",
  "required_capability": "...",
  "current_routes": [...],
  "sufficient_routes": [...],
  "insufficient_routes": [...],
  "evidence": [...]
}
```

---

## 5.2 新增 route falsification memory

同一个 route 若满足：

- hook 实际触发；
- provider 成功；
- candidate 改变了中间行为；
- 经过至少一次有针对性的 revision；
- 目标 outcome 仍无改善；

则记录：

```python
RouteFailureEvidence
```

示例：

```python
{
  "mechanism_id": "M-loc-001",
  "route": "tracer_directive",
  "status": "insufficient_for_mechanism",
  "evidence": {
    "hook_fired": 10,
    "output_changed": 7,
    "target_outcome_changed": 0
  }
}
```

下一轮 capability planner 必须消费该记录，避免继续将同一 route 视为默认可修复方案。

---

## 5.3 修改 `gaps.py`

当前 gap 只有“没有 route”时容易形成。

应增加：

```text
route_exists_but_insufficient
missing_information
missing_representation
missing_executor
missing_localization_operator
```

这样 `gaps.jsonl` 才能成为真实能力面历史，而不是永远接近空集。

---

# 6. Phase 3：加入 Candidate Development Loop

这是整个方案最关键的代码改造。

## 6.1 新增模块

建议：

```text
evolution/development.py
```

定义：

```python
class CandidateDeveloper:
    def develop(mechanism, capability_plan, budget, ...):
        ...
```

开发流程：

```text
Draft v1
  ↓
L0 structural/local tests
  ↓
L1 targeted development cases
  ↓
Failure diagnosis
  ↓
Revise parent candidate
  ↓
Repeat until budget or success
  ↓
Freeze best version
```

---

## 6.2 Candidate version schema

建议统一记录：

```python
{
  "candidate_id": "...",
  "version_id": "...",
  "parent_version_id": "...",
  "mechanism_id": "...",
  "action_class": "...",
  "candidate": {...},
  "development_evidence": {...},
  "revision_reason": [...],
  "status": "draft|failed_l0|failed_l1|ready_for_freeze|discarded"
}
```

---

## 6.3 复用 ResearchRun 逻辑

不要重新实现一套完全独立的 revision engine。

优先抽取 `evolution/research.py` 中以下能力：

- draft route；
- evaluate version；
- screening verdict；
- revision packet；
- parent candidate + diagnosis；
- version persistence。

重构成可复用组件，例如：

```text
evolution/candidate_versions.py
evolution/development.py
```

`ResearchRun` 和 `EvolutionEngine` 共用同一套版本管理。

---

## 6.4 Development budget

新增配置：

```json
"development": {
  "max_versions_per_mechanism": 4,
  "max_provider_calls": 6,
  "max_l1_cases": 12,
  "require_normal_neighbors": 2,
  "allow_action_class_change": true
}
```

正式 promotion budget 与 development budget 必须分离。

---

# 7. Phase 4：实现 L0 / L1 / L2 分层验证

## 7.1 L0 — Deterministic Development Checks

目标：在消耗 provider budget 前淘汰明显无效 candidate。

检查：

- schema 合法；
- structured identity 不冲突；
- hook 可达；
- predicate program 可执行；
- candidate 目标 mechanism 与 declared target_kind 一致；
- candidate 确实改变了目标 stage 的配置 / execution plan；
- 不使用 case id / gold / forbidden constants；
- 正常近邻上的确定性 predicate 不误触发；
- 新 executor 可加载、artifact digest 完整。

输出：

```python
DevelopmentCheckResult
```

---

## 7.2 L1 — Targeted Partial Replay

只运行被 candidate 影响的 stage 与必要后继。

### 示例

#### 修改 `tracer.system`

冻结：

- judge
- recall
- upgrade

重跑：

- tracer
- defense
- adjudication

#### 修改 `recall.trigger`

不得冻结 recall 及其所有后继。

#### 修改 evidence executor

必须重跑 executor 影响的 hook 以及依赖该 evidence 的 guard / adjudication。

---

## 7.3 建立 Hook Dependency Map

新增：

```python
HOOK_DEPENDENCY_GRAPH = {
  "judge.system": ["judge", "recall", "upgrade", "tracer", "defense", "adjudication"],
  "judge.post": ["recall", "upgrade", "tracer", "defense", "adjudication"],
  "recall.trigger": ["recall", "upgrade", "tracer", "defense", "adjudication"],
  "upgrade.pre": ["upgrade", "tracer", "defense", "adjudication"],
  "tracer.system": ["tracer", "defense", "adjudication"],
  "defense.system": ["defense", "adjudication"],
  "adjudicate.guard": ["adjudication"]
}
```

由 L1 runner 根据 candidate type 自动决定最小重跑边界。

---

## 7.4 L1 判定标准

候选只有满足以下条件才进入 freeze：

1. 至少一个 target development case 发生目标改善；
2. development neighbor 不出现新错误；
3. hook 实际触发；
4. candidate 的目标行为发生可观测变化；
5. 若目标为 localization，则必须 responsible step / event 实际改善，不能只以 reasoning 变化计分；
6. provider / runtime health 无退化。

---

## 7.5 L2 — Formal Promotion

继续沿用现有正式门：

- STRUCTURAL
- HARMLESSNESS
- BEHAVIOR
- M

但 L2 只接收已经冻结的 candidate artifact。

任何 L2 失败都不能在同一个 frozen protocol 上继续调 candidate。

候选修改后必须创建新的 candidate version 和新的正式 protocol。

---

# 8. Phase 5：Mechanism-Aligned Promotion Protocol

## 8.1 新增 `mechanism_opportunity`

当前 `gain_opportunity()` 应保留作为粗粒度统计。

新增：

```python
def mechanism_opportunity(
    baseline_rows,
    mechanism,
    mechanism_membership,
) -> dict:
    ...
```

返回：

```python
{
  "mechanism_id": "...",
  "target_cases": 3,
  "repairable_errors": 2,
  "already_correct": 1,
  "unresolved": 0,
  "evaluable": True
}
```

---

## 8.2 Promotion split 需要 3 类集合

### A. Mechanism Gain Set

必须包含：

- baseline 错误；
- 已确认属于当前 mechanism；
- candidate development 未使用的案例。

### B. Protection Set

包含：

- baseline 正确 TP；
- baseline 正确 TN；
- 若是 localization candidate，还要包含 baseline 正确定位案例。

### C. Mechanism-external Challenge Set

包含相似表面特征但不同机制的案例，用于验证规则是否过宽。

---

## 8.3 数据泄漏纪律

当前 693 例已全部处于开发暴露范围。

因此：

- 693 例可以用于开发、机制发现、内部 comparative evidence；
- 不能将其结果表述为独立泛化证明；
- 最终 generalized self-evolution claim 需要新建未暴露 holdout。

建议新增：

```text
datasets/protocols/final_holdout_v1.json
```

由框架只读一次，不能参与 proposer / revision。

---

# 9. Phase 6：成功经验与组件继承

## 9.1 区分三层存储

### 1. Candidate Archive

所有开发候选，包括失败版本。

用途：

- 避免重复搜索；
- 记录路线为何失败；
- 支持 parent-child lineage。

### 2. Reusable Development Components

尚未晋升为终局行为，但局部已证明有价值的组件，例如：

- extractor；
- evidence formatter；
- parser；
- typed semantic executor；
- localization helper。

这些组件不能直接写 Active Registry，但可被新候选复用。

### 3. Promoted Procedures / Active Registry

只有正式通过 promotion 的行为进入。

---

## 9.2 程序性经验 schema

建议：

```python
{
  "procedure_id": "...",
  "mechanism_signature": "...",
  "capability_requirement": "...",
  "action_class": "...",
  "artifact_ids": [...],
  "promotion_evidence": {...},
  "known_non_applicable": [...],
  "parent_generation": "..."
}
```

下一轮 proposer 首先检索 mechanism-compatible procedures。

---

# 10. Phase 7：扩展 Action Space，而不是只修改 prompt

当前 capability surface 已经明确：

- new hook：缺失；
- sandboxed code generation：缺失；
- model weight update：缺失；
- graph rewrite：缺失。

如果最终目标是真正开放式自进化，只靠：

- text boundary
- DSL predicate
- recall trigger
- decision guard

是不够的。

建议按风险从低到高开放能力。

---

## 10.1 Level A — Typed semantic executor

优先级最高。

新增 runner：

```text
runner = "typed_semantic"
```

输入：

- 现有 26 字段 typed view；
- 经过明确选择的局部文本字段；
- 不允许读取 gold / case id。

输出：

```python
{
  "verdict": "support|violation|insufficient",
  "probabilities": {...},
  "evidence_refs": [...],
  "artifact_id": "..."
}
```

初始只能用于 `evidence_check`，不能直接单独 terminal-write。

---

## 10.2 Level B — Sandboxed local code component

支持 code agent 生成小型、受约束 Python 组件：

- 纯函数；
- 输入 schema 固定；
- 禁止网络；
- 禁止文件系统任意访问；
- 禁止 import 非白名单包；
- CPU / 时间 / 内存有限制；
- 必须通过 property + metamorphic tests。

适合：

- 字段解析；
- 参数绑定比较；
- 结构化 contract checker；
- event dependency extraction。

---

## 10.3 Level C — New Hook / Graph Rewrite

最后再做。

必须引入明确的：

- graph schema；
- allowed mutation set；
- static validation；
- execution trace diff；
- rollback。

不应在 P0-P2 阶段过早开放。

---

# 11. JEV / System One 思想的正确接入方式

## 11.1 不推荐的接入方式

不要：

1. 把 hosted Jev 直接放入 authoritative terminal path；
2. 使用 Jev 预测 promotion 是否会通过，然后跳过正式评估；
3. 直接把 `confidence` 当成 `P(correct)`；
4. 在未冻结模型版本的情况下写入 promotion evidence；
5. 在当前 693 暴露案例上训练后再声称泛化。

---

## 11.2 推荐用途 A：Capability Router

输入：机制与当前 capability surface。

输出有限选项：

```text
prompt_only
predicate
recall_control
semantic_executor
local_code_component
new_evidence_source
localization_operator
capability_gap
```

它的作用是：

- 帮助选择下一种修复工具；
- 避免重复使用已经实测无效的 action class；
- 将开放式 proposal 问题变成部分结构化决策。

它不能决定 promotion。

---

## 11.3 推荐用途 B：Typed Semantic Decision Executor

JEV 类模型最大的潜在价值是：

> 对有限语义关系进行快速、类型化、概率化判断。

适合问题：

- 任务义务是否要求特定工具动作？
- 完成声明是否被观察结果支持？
- 两个交付字段是否语义等价？
- 某个 claim 是否有对应 evidence？

初始必须作为辅助 evidence，不应直接定罪。

---

## 11.4 推荐用途 C：测量与资源调度

可以探索：

```text
低风险 → 停止
中风险 → recall
高风险 → deeper evidence validation
```

但前提是风险概率经过：

- held-out calibration；
- 分来源评估；
- accuracy-at-coverage；
- Brier / log loss；
- AUROC；
- selective risk；
- bootstrap CI。

不能只报告 ECE。

---

# 12. 学习型 Runner 的基础设施修复

若引入 JEV-like / learned executor，必须先完成以下代码修复。

## 12.1 Artifact immutability

当前 `source_digest()` 只覆盖顶层 `.py` 文件。

必须新增：

```python
artifact_digest(path)
artifact_manifest(...)
```

Promotion protocol 需冻结：

- source digest；
- model artifact digest；
- tokenizer / config digest；
- calibration artifact digest。

### 回归测试

替换权重文件后，旧 frozen protocol 必须拒绝加载。

---

## 12.2 structured_key 修复

当前非 `typed_dsl` runner 的 `program_hash` 可能为空。

新增 runner 后 structured identity 必须包含：

```text
runner
artifact_id / digest
predicate / task schema
hook
input feature set
```

两个不同模型不能被错误判成同一个 intervention。

---

## 12.3 Calibration gate

学习型 executor 不能只通过 family unanimity 就获得 conviction 权限。

至少要求：

- held-out accuracy；
- false positive rate；
- false negative rate；
- calibration summary；
- per-source grouped result；
- abstention behavior。

初期建议 learned executor 只输出 evidence，不允许独立 terminal conviction。

---

# 13. 概率测量改造

## 13.1 目的

概率读数用于：

- 诊断模型置信与错误之间的关系；
- 资源调度；
- 辅助统计分析。

不能替代 binary / localization 正式收益。

---

## 13.2 新增离线校准审计

建议新模块：

```text
evolution/calibration.py
scripts/audit_calibration.py
```

报告：

- judge confidence level accuracy；
- per-source accuracy；
- Brier；
- log loss；
- AUROC；
- calibration curve；
- bootstrap CI；
- accuracy-at-coverage；
- unresolved / abstention behavior。

如果当前只有 high/medium/low，先建立离散 reliability table，不要假装其为连续概率。

---

# 14. 代码改造建议清单

## 14.1 `evolution/worksheet.py`

需要：

- 保留 coarse family；
- 调用 mechanism extractor；
- family → mechanism subgroup；
- actionability 改为 capability planning；
- localization viability 成为 mechanism confirmation 前置条件。

---

## 14.2 `evolution/gaps.py`

需要：

- 支持 capability gap reason taxonomy；
- 记录 route insufficient；
- 记录 repeated failure；
- 允许 proposer 检索历史 gap。

---

## 14.3 `evolution/proposer.py`

输入新增：

```text
mechanism
capability_requirement
previous_candidate_versions
route_failure_evidence
reusable_components
normal_neighbors
```

输出候选必须显式声明：

```text
target_mechanism_id
action_class
expected_behavior_change
required_inputs
formal_success_condition
```

---

## 14.4 `evolution/research.py`

重构可复用：

- version graph；
- revision packet；
- failure diagnosis；
- screening。

避免 ResearchRun 和 EvolutionEngine 存在两套不一致的 candidate revision 逻辑。

---

## 14.5 `evolution/engine.py`

插入：

```text
mechanism diagnosis
↓
capability planning
↓
development loop
↓
freeze
↓
formal promotion
```

不要再让每个 cycle 一开始就直接 proposal → promotion。

---

## 14.6 `evolution/metrics.py`

新增：

- mechanism_opportunity；
- mechanism_repair_delta；
- action_class_effect；
- development target delta；
- localization target delta；
- calibration auxiliary metrics。

---

## 14.7 `evolution/gates.py`

正式 gate 保持严格。

新增：

- learned executor structural checks；
- artifact identity checks；
- mechanism alignment checks；
- localization protection；
- learned evidence source calibration requirement。

---

## 14.8 `evolution/backends.py`

新增：

- partial replay plan；
- hook dependency based rerun；
- artifact digest；
- cached development run；
- exact candidate version identity。

---

## 14.9 `detector/interventions.py`

为未来新 runner 提供显式 registry，而不是继续 `if/elif/else` 隐式闭合。

建议：

```python
RUNNER_REGISTRY = {
    "deterministic": ...,
    "typed_dsl": ...,
    "typed_semantic": ...,
}
```

每个 runner 声明：

- supported hooks；
- input schema；
- output verdict set；
- deterministic / learned；
- artifact identity requirement；
- terminal authority policy。

---

# 15. 测试策略

## 15.1 Unit tests

必须覆盖：

- mechanism key；
- capability classification；
- route falsification；
- candidate lineage；
- partial replay dependency；
- artifact digest；
- structured_key learned runner；
- mechanism opportunity；
- promotion leakage prevention。

---

## 15.2 Metamorphic tests

建议：

1. case id 改名，机制与候选行为不变；
2. source 名改名，不应改变 mechanism；
3. 轨迹中插入无关工具调用，不应改变特定 evidence checker；
4. 正常近邻中加入相同表面 token，不应误触发规则；
5. 对 expected / actual 字段做语义等价改写，typed semantic checker 应保持相同判断；
6. localization case 中移动无关 event，不应改变 root-cause relationship。

---

## 15.3 Integration tests

模拟完整流程：

```text
patrol finds family
→ mechanism extracted
→ capability gap classified
→ candidate drafted
→ L0 pass
→ L1 target improvement
→ candidate frozen
→ L2 pass
→ registry updated
→ next cycle proposer sees procedure
```

必须有至少一个 synthetic golden path 测试真正产生 active registry 非空。

---

## 15.4 Negative integration tests

至少覆盖：

- candidate L1 有 target improvement 但 neighbor regression → 不得 freeze；
- L2 protection regression → 不得 promote；
- route fired but no target change → 记录 insufficient route；
- learned artifact changed after protocol freeze → 拒绝评估；
- formal gain set 无 mechanism opportunity → `not_evaluated`，不能判 failure；
- candidate 读取 gold / case id → structural reject。

---

# 16. 实验路线

## E0 — Mechanism model dry-run

不改 detector 行为。

目标：

- 对历史失败重建 mechanism；
- 检查同路径不同机制是否能分开；
- 检查同机制跨来源是否能合并。

验收：

- 人工抽查 20–30 个 failure；
- ≥80% mechanism grouping 可解释；
- 不依赖 case id / gold。

---

## E1 — Candidate development loop

选择一个已有强证据机制。

目标：

- 至少产生 2 个 parent-child candidate version；
- rejection 能驱动具体修订；
- L1 能清晰解释为什么 v2 比 v1 好。

此阶段不要求正式 promotion。

---

## E2 — 首次真实 promotion

选择满足：

- ≥2 confirmed mechanism cases；
- ≥1 未用于 development 的 formal gain case；
- ≥2 TP + ≥2 TN protection；
- 有 mechanism-external challenge。

目标：

- 产生至少 1 个正式通过 candidate；
- active registry 非空；
- next generation 可继承。

如果没有 candidate 通过，也必须能够明确回答：

```text
是 mechanism 不可修？
是 action class 能力不足？
是 candidate 设计失败？
是 formal gain 无机会？
是 harmlessness 失败？
```

不能再只得到笼统的“0 promoted”。

---

## E3 — Multi-generation inheritance

至少运行 3 generation。

验证：

1. G1 晋升能力在 G2 baseline 中真实存在；
2. G2 proposer 可检索 G1 procedure；
3. G2 不重复提出相同机制；
4. G2 可在 G1 上继续增加另一个能力；
5. G3 运行中 G1/G2 均未回退。

这才是“真实自进化”的核心证明。

---

# 17. 自进化成功的正式定义

不要再用“流程跑了三轮”作为主要成功标准。

建议定义：

## Generation-level success

一个 generation 成功，当且仅当：

1. 存在 confirmed mechanism；
2. 生成 candidate development lineage；
3. candidate 通过 L1；
4. candidate 冻结；
5. candidate 通过正式 promotion gates；
6. active registry / artifact store 更新；
7. 下一 generation baseline 加载该能力。

## Multi-generation self-evolution success

至少满足：

```text
G0 < G1 < G2
```

其中“<”必须通过：

- 新机制的真实修复；
- 无超限保护退化；
- 已晋升能力继续存在；
- 下一代确实基于上一代版本运行。

不能用：

- proposer 输出变化；
- reasoning token 增加；
- hook firing；
- confidence 提升；
- 某个中间 state 变化

单独作为 self-evolution success。

---

# 18. 建议实施顺序

## P0 — 必须先做

### P0.1 Mechanism representation

- 新增 `mechanisms.py`
- coarse family → mechanism subgroup
- 增加 mechanism schema 和测试

### P0.2 Capability planning

- route 存在 ≠ actionable
- gaps taxonomy 扩展
- route insufficient history

### P0.3 Candidate development loop

- 抽取 ResearchRun revision 能力
- parent-child candidate lineage
- L0/L1 开发测试

### P0.4 Mechanism-aligned opportunity

- mechanism gain opportunity
- formal split alignment

完成以上四项之后，再运行一次真实 self-evolution。

---

## P1 — 提高效率和正确性

### P1.1 Partial replay

### P1.2 Candidate archive / component store

### P1.3 Artifact digest

### P1.4 structured_key fix

### P1.5 calibration audit

---

## P2 — 扩大能力空间

### P2.1 Typed semantic runner

### P2.2 Sandboxed local code executor

### P2.3 Property / metamorphic test gate

---

## P3 — 开放式演化

只有在 P0-P2 成功以后再考虑：

- new hook generation；
- graph rewrite；
- weight update；
- learned policy routing；
- richer multi-agent improver。

---

# 19. 给 Code Agent 的执行规则

Code agent 在实施时应遵守：

1. **不要一次性重写整个 evolution 包。** 每个 phase 单独提交，保持现有 528+ 测试基线持续通过。
2. 所有新 schema 必须 versioned。
3. 任何 formal gate 行为变更必须带 regression test。
4. 不得删除现有冻结、账本、authority、runtime health 约束。
5. 不得为了让候选通过而降低 harmlessness。
6. development 与 promotion 数据使用必须隔离。
7. 新 runner 必须显式注册，不能继续依赖隐式 else 分支。
8. learned artifact 必须有 digest。
9. 每个正式 candidate 必须绑定 mechanism id。
10. 每次 candidate rejection 必须产生机器可读 failure reason。
11. 每个 route 如果触发但无目标作用，要形成 negative capability evidence。
12. 新的 evaluation metric 必须标明是：
   - promotion-authoritative；
   - diagnostic-only；
   - calibration-only。
13. 所有 JEV-like 模块初期只能作为辅助 decision / evidence source，不得直接成为独立 terminal authority。
14. 每个 phase 完成后运行：
   - unit tests；
   - integration tests；
   - audit；
   - dataset integrity；
   - mock evolution run。

---

# 20. 推荐的第一批具体代码任务

如果要立即开工，建议按以下顺序创建 issue / task：

### Task 1 — Introduce FailureMechanismV1

新增 `evolution/mechanisms.py`，将 detector path 与 task-level error mechanism 分离。

### Task 2 — Mechanism subgrouping

修改 `worksheet.py`，粗 family 后执行 mechanism-compatible regrouping。

### Task 3 — CapabilityRequirementV1

新增 capability requirement taxonomy，并改造 `classify_family_actionability()`。

### Task 4 — Route insufficiency memory

当 candidate hook fired 且行为变化但 target outcome 不变时记录 `route_insufficient`。

### Task 5 — Candidate version graph

从 `research.py` 抽取 parent-child revision 设施。

### Task 6 — CandidateDeveloper

新增 draft → L0 → L1 → revise loop。

### Task 7 — Hook dependency partial replay

为 development 阶段减少完整 rerun。

### Task 8 — mechanism_opportunity

正式 promotion 前确认目标机制具有实际 gain opportunity。

### Task 9 — mechanism-aligned promotion manifest

冻结 target mechanism / gain / protection / challenge set。

### Task 10 — promoted procedure inheritance

确保通过 candidate 写入 active registry 后，下一 generation baseline 与 proposer memory 均使用新版本。

### Task 11 — artifact digest + learned runner-safe identity

为未来 semantic runner 铺平基础设施。

### Task 12 — calibration audit

离线分析现有 judge confidence，但先保持 diagnostic-only。

---

# 21. 最终验收 Checklist

完成优化后，应能回答以下问题，且每项都有代码或报告证据：

- [ ] 每个失败家族是否被进一步映射为 task-level failure mechanism？
- [ ] 是否能够识别“route 存在但能力不足”？
- [ ] 是否有 candidate parent-child revision lineage？
- [ ] candidate 是否先经过低成本开发测试再进入 promotion？
- [ ] gain set 是否确认包含当前 mechanism 的 repair opportunity？
- [ ] protection 是否覆盖 TP / TN / localization 正确案例？
- [ ] candidate 是否绑定不可变 artifact / source identity？
- [ ] promotion 失败是否能返回具体的、可下一步行动的原因？
- [ ] 至少一个 candidate 是否真实进入 Active Registry？
- [ ] 下一 generation 是否真实继承该 registry？
- [ ] 下一 generation proposer 是否能够检索上一代 procedure？
- [ ] 是否至少连续两代产生新增能力且未破坏先前能力？
- [ ] learned / JEV-like 模块是否仍受正式 evidence 和 promotion gate 约束？
- [ ] 是否明确区分检测提升、定位提升、概率校准提升与成本优化？
- [ ] 是否存在独立 holdout 用于最终泛化结论？

---

# 22. 核心设计原则

最后建议 code agent 始终围绕以下五条原则实施：

### 原则一：先修“机制识别”，再修“候选搜索”

如果 failure family 本身混合多个机制，增加 proposal 数量只会更快地产生错误候选。

### 原则二：先证明 action class 有修复能力，再扩大搜索

prompt、DSL、semantic executor、local code component 是不同能力等级，不能互相替代。

### 原则三：development 可以反复试，promotion 必须冻结

这能同时满足工程效率与实验可信度。

### 原则四：概率是测量工具，不是事实证明

JEV-like 概率可以帮助路由、校准和统计，但不能绕过真实 outcome 与 evidence。

### 原则五：自进化的本质是“成功能力被下一代继承”

如果没有 promoted capability 成为下一 generation 的 baseline，那么无论跑多少 cycle，都只能称为自动实验框架，而不是已经证明的真实自进化。

---

## 建议最终目标状态

当框架完成上述改造后，系统应该具备如下闭环：

```text
发现失败
→ 形成真实机制
→ 判断能力缺口
→ 选择修改工具
→ 生成候选
→ 局部执行与修订
→ 冻结候选
→ 机制对齐的正式评估
→ 无副作用则晋升
→ 记录可复用 procedure / component
→ 下一代在新能力上继续发现并修复新的机制
```

这套闭环一旦能够连续运行两到三个 generation，并且每一代都通过真实、冻结、可复核的 promotion evidence 保持前代能力并新增至少一个能力，就可以比较有力地说明：

> 当前系统已经从“自动化候选测试框架”跨越到“具备真实能力积累的自进化框架”。


---

# 23. 执行流程：从当前代码基线出发的落地步骤

> 本节生成于 2026-09-22，基线为 commit `e9003de`（工作树仅本方案文档未跟踪）。所有行号指该提交。
> 与前三节的关系：§18 定优先级，§20 列任务，**本节给出每一步改哪个文件的哪个函数、用什么测试证明、什么条件才算这一步结束**。

## 23.0 使用方式

1. **开工前先做 §23.1 的核对。** 文档 §2 描述的是写作时的代码状态；此后提交已落地其中一部分（定位目标闭环、机制画像二次分组、结构化身份、读一次终审守卫、保护集修复计量）。不核对就直接实现会重复造轮子，甚至改坏已有正确行为。
2. **一次只推进一个工作包，独立提交。** 每个工作包结束时 528 项测试必须继续通过（实测 `528 passed in 44.28s`），并附新增回归测试。
3. **阶段门先于实验。** P0 四项全部完成前不运行真实自进化；实验只验证已冻结的协议，不用实验结果反过来调代码。

---

## 23.1 第一步：代码现状核对

### 23.1.1 文档条目 → 代码事实对照

| 文档条目 | 应存在的对象 | 当前代码事实 | 状态 | 位置（commit `e9003de`） |
|---|---|---|---|---|
| §2.1 失败家族表示 | 机制签名 | `_mechanism_signature` 仅记录 judge/recall/upgrade/tracer/defense 路径；已有 `diagnose_failure_families` 产出 `member_summaries`/`predicate_signals`/`shared_literals`/`typed_samples`，以及 `refine_failure_families` 按谓词画像二次分组 | **部分** | `evolution/worksheet.py:171`、`:812`、`:936` |
| Task 1 `FailureMechanismV1` | 任务级机制对象 | 不存在（无 `evolution/mechanisms.py`） | **缺失** | — |
| §2.2 actionability | 能力判定 | `classify_family_actionability` 枚举 routes（仅 `mechanical_conviction` 额外要求家族内一致违规） | **部分** | `worksheet.py:719-809` |
| Task 3 `CapabilityRequirementV1` | 能力需求分类 | 不存在 | **缺失** | — |
| §2.3 候选开发循环 | draft→测试→修订 | Engine 为单轮 `propose`(2013) → 门(2113-2186)，候选失败即终态；`ResearchRun` 已有 draft→evaluate→revise→screening，但 `engine.py` 对 `research` 零引用，`set_research_packet` 在正常 cycle 中不可达 | **缺失** | `engine.py:1985-2019`、`:2113`；`research.py:113` |
| §2.4 机制机会 | 机制级 gain opportunity | `gain_opportunity` 按极性计数，`localization_opportunity` 按定位计数；机制级判定不存在 | **部分** | `metrics.py:225`、`:246`；`engine.py:819-887` |
| §2.5 分层验证 | L0/L1/L2 | 只有 SPEC 说明的免费 STRUCTURAL 门 + 正式四门；无确定性预检、无定向重放 | **部分** | `gates.py:222`；`engine.py:2113` |
| §2.6 经验积累 | 程序性经验可继承 | 写入（`record_episode`/`record_procedure`）与检索（`recall_experience`）已接线；但 `record_improvement` 在 engine 中导入后从未调用，且 `active.json` 当前 0 条，程序性记忆为空 | **部分** | `engine.py:1027`、`:1042`；`memory.py:216`；`artifacts/evolution/registry/active.json` |
| Task 4 route 反证记忆 | route insufficient | 不存在（全仓 `route_insufficient|hook_fired|fired_but` 零命中）；原料齐备：`fired_rate`、行为事件（`text_injected`/`recall_triggered`/`guard_fired`/`evidence_increment_rows`） | **缺失** | `metrics.py:510`、`:533`；`gates.py:638-688` |
| Task 8 `mechanism_opportunity` | 机制级机会函数 | 不存在；`_measurable_families` 已在极性/定位层实现"无机会 → `not_evaluated`" | **部分** | `engine.py:819`、`metrics.py:225/:246` |
| Task 9 机制对齐晋升 | 协议含机制/挑战集 | `freeze_protocol` 有 roles/`development_cases`/gold/`baseline_roll`/`coverage_floor`，**无 mechanism 概念、无挑战集** | **部分** | `promotion.py:303-470`、`:543` |
| Task 7 hook 依赖重放 | 依赖图 + 部分重放 | 不存在 | **缺失** | — |
| §12.1 artifact 不可变 | digest/manifest | `file_digest` 已存在；`source_digest` 为非递归 `glob("*.py")`，只覆盖顶层模块；无模型/分词器/校准产物 digest | **部分** | `backends.py:64-85`；`harness.py:118-125` |
| §12.2 structured identity | 覆盖 runner 与产物 | 三类确定性类型已含 `type/hook/features/predicate/runner/program_hash`；`text_boundary` 按设计无 key；无 artifact_id 字段（尚无 learned runner） | **基本完成** | `contracts.py:142-219` |
| §12.3 校准 | 校准审计 | 不存在（无 `evolution/calibration.py`） | **缺失** | — |
| §14.9 runner registry | 显式注册表 | 不存在；派发是两处 if/else（未知 runner 会在 `_validate_body` 抛错，行为安全但不可扩展） | **缺失** | `detector/interventions.py:899-908`、`:1075-1084`、`:465-470` |
| §8.3 独立 holdout | 读一次终审集 | `FinalHoldout` 读一次守卫 + `python -m evolution final` 已实现；但默认配置 `split_manifest=None`，未配置任何终审清单 | **部分** | `splits.py:183-291`；`evolution/configs/development.json` |
| §10.1-10.3 能力扩展 | 语义执行器/沙箱/图改写 | 全不存在；能力面自述 `open_ended_self_evolution: false` | **缺失** | `evolution/capability_surface.py:47`、`:89-118` |

### 23.1.2 已经落地、本方案**不应重复实现**的部分

| 能力 | 落点 | 对应文档条目 |
|---|---|---|
| 定位目标全链路：`FAM-loc-*`、`target_kind`、家族前缀⇔目标类型强制、定位行为门与 M 判据、联合成功计量（漏报不离分母） | `worksheet.py:464/:542/:385/:326`、`gates.py:738-860`、`metrics.py:412`、`engine.py:1538` | §4.3、§7.4 |
| 定位可修复性判定（`structurally_impossible` vs `needs_review`） | `worksheet.py:385`、`:451` | 下一轮方案 §3.1 |
| 保护集"改善与退化分开记录、永不相加"（`net` 恒为 `null`） | `metrics.py:280` | §5.2 |
| 结构化身份（含 runner 与 program hash） | `contracts.py:142` | §12.2 |
| 不可测量 ≠ 失败：`not_evaluated` / `skipped_unmeasured` / `promotion_unmeasurable` | `engine.py:225`、`:1319-1352`、`:786` | §15.4 |
| 冻结协议 + 复用四门 + 版本发布 + 账本 + registry 快照/回滚/退役 | `promotion.py`、`registry.py`、`engine.py:2866-3024` | §7.5、§9 |
| 读一次终审守卫 | `splits.py:183`、`cli.py:90` | §8.3 |

**已核对、无需改动**：`registry.check_limits` 的默认值虽与框架配置不同，但 `engine.py:2272` 与 `:2301` 均传入 `library_cfg=self.config.get("library")`，默认值不会生效。

### 23.1.3 结论

§2 的六条问题里 0 条被完整解决、4 条部分覆盖、2 条原样存在；§3–§13 的目标架构中，机制建模、能力规划、开发循环、机制对齐机会、route 反证、部分重放、存档继承、校准、能力扩展均**未开始**。因此本节以下流程是真实工作量，不是复述。

---

## 23.2 红线（每个工作包都要复核）

| 红线 | 现有强制点 | 新增代码必须遵守 |
|---|---|---|
| 不得泄漏 case id / gold 进候选 | `contracts.py:131`（`structural_gate:254` 调用）；promotion 预检 `promotion.py:896` | 机制 key 与候选生成路径同样适用 |
| 不得放宽任何门限换准入 | `gates.py` 四个门 + 定位门 | 新加的是**开发期**判据，不是门限替代 |
| development 与 promotion 数据/预算隔离 | `freeze_protocol` 的 `development_cases` 校验 | 开发案例不得进入机制增益集 |
| 新 runner 必须显式声明能力 | 无（待建） | 声明 supported hooks / verdict set / 是否 learned / 是否可 terminal-write |
| learned 产物必须有 digest | 无（待建） | 权重、分词器、配置、校准产物全部纳入 |
| 每个正式候选绑定机制 id | 无（待建） | `build_candidate` 与 `freeze_protocol` 双向校验 |
| 每次拒绝产生机器可读原因 | `engine.py:2234-2269` | 新增失败态必须落账本，不得只写日志 |
| route 触发但无目标效果要记负向证据 | 无（待建） | 见 §23.3 P0.2 |
| 新指标必须标注权威性 | 报告中的 `instrument`/`evidence` 分块 | 标注 `promotion-authoritative` / `diagnostic-only` / `calibration-only` |
| 每个工作包跑固定验证 | `scripts/verify.py` | 见 §23.8 |

---

## 23.3 P0 执行流程（先做，做完才允许真实运行）

### P0.1 机制表示（Task 1 + Task 2）

**目标**：粗家族 → 机制子组，使 proposer 收到的是"任务义务 + 观测偏差 + 检测器失败 + 修复操作"四段式描述，而不是只有路径签名。

**步骤**

1. 新增 `evolution/mechanisms.py`：
   - schema `evolution.failure_mechanism.v1`，字段按 Stage B（`mechanism_id` / `family_id` / `objective` / `task_obligation` / `observed_deviation` / `evidence_bindings` / `detector_failure` / `repair_hypothesis` / `member_cases` / `normal_neighbors` / `status` / `confidence_basis`）。
   - 导出 `infer_mechanism(...)`、`mechanism_key(...)`、`mechanism_compatible(a, b)`、`repair_operation_signature(...)`。
2. **输入白名单**（决定 key 是否合法）：仅允许现有行的 `result.proof`、`stage_status`、`_mechanism_signature`（作为 `detector_failure.path_signature` 子结构）、`diagnose_failure_families` 已算出的 `predicate_signals`/`member_summaries`/`typed_samples`/`shared_literals`、`_contract_kinds`。**禁止** case id、gold、gold step、source 名、具体答案常量进入 key。
3. 在 `worksheet.py` 增加 `mechanism_subgroups(families)`：输入为 `refine_failure_families` 的输出，输出为带 `mechanism_id`（形如 `M-<sha(repair_operation_signature)>`）的子组，并保留粗家族 id 作为前缀以便兼容既有门。
4. 接线（不改门）：
   - `engine.py:1930`（diagnose）→ `:1935`（refine）→ **新增 mechanism_subgroups** → `:1936`（split_actionable_families）。
   - `worksheet_report`（`worksheet.py:637`）增加 `mechanisms` 块。
   - proposer 消费端：`proposer.py` 的五处家族模板（`_template_batch:1066`、`_recall_trigger_candidate:1142`、`_missing_search_candidates:1190`、`_fp_candidates:1268`、`_localization_candidates:1340`）改为优先读 mechanism 描述，字段缺失时回落到 family 描述（保证 `--mini` 与既有测试不变）。
5. 测试 `evolution/tests/test_mechanisms.py`：
   - §4.4 五条验收（同路径不同修复操作不合并；不同来源同修复操作可合并；改 case id 后 key 不变；改 source 名后 key 不变；定位歧义案例不得进入 confirmed）。
   - §15.2 变形测试第 1、2、4 条。

**判据（E0）**：不动检测器行为的前提下，对历史失败重建机制；人工抽查 20–30 个失败，≥80% 分组可解释；测试证明 key 与 case id / gold / source 无关。

**产物**：`reports/<date>_mechanism_dryrun/`（分组表 + 抽查记录 + 复算脚本，标 `diagnostic-only`）。

**提交边界**：只新增模块与接线 + 测试，不改任何门、不改 proposer 输出契约。

---

### P0.2 能力规划与 route 反证记忆（Task 3 + Task 4）

**目标**：把"存在 route"降级为枚举结果，把"是否具备修复能力"独立判定；并让"触发过但没用"成为可检索的负向证据。

**步骤**

1. 新增 `evolution/capability_planning.py`：`analyze_capability_requirement(mechanism, capability_surface)`，返回 §5.1 的 `classification / required_capability / current_routes / sufficient_routes / insufficient_routes / evidence`（可选 `recommended_action_class`）。taxonomy 用 §5.1 八类（`information_missing` / `representation_missing` / `semantic_judgment_missing` / `control_flow_missing` / `executor_missing` / `localization_attribution_missing` / `prompt_only` / `already_supported`）。
2. **保留** `classify_family_actionability`（`worksheet.py:719`）作为 route 枚举，由新函数调用；`split_actionable_families`（`engine.py:357`）改为消费新分类，`capability_gap` 与 `research_only` 分流到已有 gap 记录路径。
3. route 反证记忆：新增 `evolution/route_failures.py`，schema `evolution.route_failure.v1`，落盘 `harness_dir/route_failures.jsonl`（与 `gap_store` 同目录，`engine.py:513`）。写入条件**同时**满足：
   - hook 实际触发：`fired_rate > 0`（`metrics.py:510`）；
   - provider/runtime 健康：无 `provider_error`/`degraded`（复用 `arm_has_infrastructure_failure`，`gates.py:119`）；
   - 中间行为变化：`evidence_increment_rows > 0` 或对应 `text_injected`/`recall_triggered`/`guard_fired` 事件 > 0（与 `gates.py:638-688` 同源）；
   - 经过至少一次有针对性的 revision（依赖 P0.3 的版本图）；
   - 目标 outcome 未改善（目标机制计分未变）。
   接入点：`engine.py:2234-2269`（拒绝处理）与 `promotion.py::PromotionRun.run`（`:944` 起）的终态；账本加 `route_failure` 事件。
4. 消费端：capability planner **必须**读取 `load_gap_state`（`gaps.py:136`）与 route 反证记录；同一 route 在同一机制上连续 ≥2 次 insufficient 后不得再出现在 `sufficient_routes`。
5. `gaps.py` 扩展 reason 取值：`route_exists_but_insufficient` / `missing_information` / `missing_representation` / `missing_executor` / `missing_localization_operator`（`gap_record:43`、`gap_identity:37`，保持旧记录可读）。
6. 测试 `test_capability_planning.py`、`test_route_failures.py`：负例"hook fired 但无目标变化"→ 记录 insufficient（对应 §15.4 第三条）；连续 insufficient 后 planner 不再推荐该 route。

**判据**：能力面报告（`capability_surface_report()`）与 planner 输出一致；真实运行后 `gaps.jsonl` 不再接近空集。

**提交边界**：新增判定与记录，不改变任何门的判据。

---

### P0.3 候选开发循环（Task 5 + Task 6 + Task 7 最小版）

**目标**：把"一次性提案"变成"同一父候选的多版本开发"，且开发成本显著低于正式晋升。

**步骤**

1. 抽取版本图：新增 `evolution/candidate_versions.py`，把 `research.py` 的 `_new_version_id`(707)、`_load_existing_versions`(718)、`_record_version`(740)、`_version_identity`(525)、`_find_reusable_results`(537) 提升为可复用组件；`research.py` 改为调用它，**行为与磁盘格式不变**（`reports/rsi_pilot_20260919/` 下的归档必须仍可读，`test_research.py` 1080 行全绿）。
2. 新增 `evolution/development.py::CandidateDeveloper`：`develop(mechanism, capability_plan, parent_versions, budget)`，流程 Draft → L0 → L1 → 失败诊断 → 修订 → 循环至预算/成功 → Freeze；版本记录 schema 按 §6.2（`candidate_id` / `version_id` / `parent_version_id` / `mechanism_id` / `action_class` / `candidate` / `development_evidence` / `revision_reason` / `status`）。
3. **L0 确定性检查**（零 provider 成本）：schema 合法（`validate_intervention`，`interventions.py:623`）、`structured_key` 不冲突（`contracts.py:142`）、hook 可达（复用 `gates.py:425-437` 的逻辑）、无 case id 泄漏（`contracts.py:131`）、不引用 gold、正常近邻上确定性谓词不误触发、目标 hook 确实改变执行计划。
4. **L1 定向重放 + 最小 hook 依赖图——顺序修正**：§7.3 的依赖图是实现 L1 的**前置条件**，不能留到 P1。理由：`requires_fresh_evaluation`（`gates.py:48`）只把 `judge.system` 与 `require_full_view_defense` 视为 fresh，其余 hook 走冻结 judge 回放（`promotion.py:740`/`:798` 的 `baseline_results` 注入）。若不先声明"改了哪个 hook 要重跑到哪一层"，L1 只能退化为整臂重跑，开发成本等于晋升成本，P0 的意义消失。因此本工作包内先落 §7.3 的 7 条最小依赖图；扩展与缓存策略留给 P1.1。
5. 开发预算：`evolution/config.py` 的 `DEFAULTS`(20-114) 增加 `development` 块（§6.4 五键），`evolution/configs/development.json` 同步；provider 调用单独计数，账本记 `development_budget` 事件，与 promotion 预算分离。
6. 入口：新增 CLI `develop`（`evolution/cli.py` 加 `cmd_develop`，`evolution/__main__.py` 注册），用法 `python -m evolution develop --mechanism M-xxx [--parent v003] [--dry-run]`；cycle 内可选择启用（`development.enabled`，默认 false），启用时报告须记录本轮走的是开发路径还是单轮路径。
7. 测试：lineage 贯穿（parent → child 可追溯）、预算上限生效、L0 各拒绝分支、**L1 有目标改善但邻居退化 → 不得 freeze**（§15.4 第一条）。

**判据（E1）**：同一机制至少 2 个 parent-child 版本；拒绝理由能驱动具体修订；L1 能说明 v2 为何优于 v1。**不要求正式晋升。**

**提交边界**：development 只产出候选与开发证据，写 `work_root/development/`，不写 registry、不改冻结协议。

---

### P0.4 机制对齐的机会判定与晋升清单（Task 8 + Task 9 + Task 10 的 schema 部分）

**步骤**

1. `evolution/metrics.py` 新增 `mechanism_opportunity(baseline_rows, mechanism, membership)`，返回 §8.1 结构（`target_cases`/`repairable_errors`/`already_correct`/`unresolved`/`evaluable`）；保留 `gain_opportunity`/`localization_opportunity` 作粗粒度仪器。
2. 接线：`_measurable_families`（`engine.py:819-887`）在现有极性/定位判定之上加机制层判定；无机制机会沿用既有语义 `status="not_evaluated"`，**不新增失败态**。
3. `promotion.py::freeze_protocol`(303-470) 增加键：`mechanism_id`、`mechanism_gain_cases`、`challenge_cases`（机制外相似案例）；校验 `development_cases` 与机制增益集不相交；`load_protocol`(543) 增加对应一致性复核。
4. 三类集合按 §8.2 构造：机制增益集（baseline 错误 ∧ 属该机制 ∧ 未用于开发）、保护集（baseline 正确 TP/TN ∧ 定位正确案例）、机制外挑战集。
5. 新增 `promotion_diagnosis` 块（§16 E2 的五问），把"未晋升"映射到机器可读原因：机制不可修 / action class 能力不足 / candidate 设计失败 / formal gain 无机会 / harmlessness 失败。落在 `engine.py:225` 的 `terminal_classification` 之后，与 `gate_rejection_is_unevaluable`(`:192`) 的语义保持一致。
6. 测试：`mechanism_opportunity` 计数正确；形式增益集无机制机会 → `not_evaluated`；`challenge_cases` 混入开发案例 → 冻结被拒。

**判据**：冻结协议能在**运行前**回答"这批数据里有多少属于本机制的可修复错误"。

---

## 23.4 检查点 A：P0 完成后的首次真实运行（对应 §16 E2）

**入口条件**：P0.1–P0.4 全部通过；`python scripts/verify.py` 全绿；账本校验通过。

**选件标准**：≥2 confirmed mechanism cases；≥1 未用于开发的 formal gain case；≥2 TP + ≥2 TN 保护；存在机制外挑战集。

**命令序列（真实服务）**

```bash
python -m evolution preview --cycle 1 --mini          # 1. 看机制与能力规划输出
python -m evolution develop --mechanism M-xxx         # 2. 开发到冻结候选
python -m evolution freeze-protocol ...               # 3. 在任候选输出之前冻结机制对齐批次
python -m evolution promote ...                       # 4. 四门 + 定位门
python -m evolution verify-ledger                     # 5. 账本哈希链
python -m evolution governance --cycle 1              # 6. 归因、退役与回滚核算
python -m evolution registry                          # 7. 检查 active registry
```

**退出条件**：终态机器可读地回答五问之一（不允许多数情况下只得到"0 promoted"）。无论成败归档到 `reports/<date>_promotion_mechanism/`，含只读复核脚本与逐例 `elapsed_seconds`。

---

## 23.5 P1 执行流程（效率与正确性）

| 包 | 内容 | 落点 |
|---|---|---|
| P1.1 | 部分重放扩展：`partial_replay_plan(candidate, active_registry)`、按 hook 依赖决定 rerun 边界、按 `(source_digest, registry_key, case, boundary)` 键控缓存 | `evolution/backends.py`（`source_digest:73`、`registry_key:93` 即键素材） |
| P1.2 | 候选存档 + 可复用组件库：`work_root/candidates/`（含失败版本与失败原因）、`work_root/components/`（extractor/formatter/parser/semantic executor/localization helper，各带 manifest 与 digest）；proposer 输入增加"可复用组件"段 | 新增 `evolution/archive.py`；`proposer.set_memory_context`(`:313`/`:1007`) |
| P1.3 | artifact digest：`artifact_digest(path)` / `artifact_manifest(...)`，覆盖权重/分词器/配置/校准产物；`source_digest` 由 `glob("*.py")` 改 `rglob("*.py")` 覆盖子包（**注意：此改动会失效既有缓存，属预期**）；冻结协议纳入 digest | `backends.py:64-85`、`promotion.py:303` |
| P1.4 | `structured_key` 扩展：未知 runner 返回含 `runner/artifact_id/artifact_digest/input feature set` 的键而非 `None`，两个不同模型不得判为同一 intervention | `contracts.py:142` |
| P1.5 | 校准审计：当前只有 `judge_confidence` 三档（`interventions.py:87-117`），先出**离散 reliability table**（分级准确率、per-source、abstention），连续指标（Brier/log loss/AUROC/accuracy-at-coverage/bootstrap CI）待有概率输出后启用；全部标 `calibration-only` | 新增 `evolution/calibration.py`、`scripts/audit_calibration.py` |

**回归要求**：P1.3 必须带"替换权重后旧冻结协议拒绝加载"的测试；P1.4 必须带"两个不同 artifact 不被判同一"的测试。

---

## 23.6 P2 执行流程（扩大能力空间）

1. **显式 runner registry + typed_semantic**：`detector/interventions.py` 新增 `RUNNER_REGISTRY`，每个 runner 声明 supported hooks / input schema / output verdict set / deterministic|learned / artifact identity requirement / terminal authority policy；把两处 if/else 派发（`:899-908`、`:1075-1084`）改为查表。新增 `typed_semantic`：初期只允许 `evidence_check`，只输出证据，**不得独立 terminal conviction**；identity 必须被 P1.3/P1.4 覆盖。
2. **Level B 沙箱组件**：纯函数、固定输入 schema、禁网、禁任意文件系统访问、导入白名单、CPU/时间/内存上限，必须通过 property + metamorphic 测试。
3. **把 §15.2 的变形测试固化为新 runner 的准入 gate**（case id 改名、source 改名、无关工具调用插入、近邻同 token、语义等价改写、无关 event 移动）。

**前置**：P1.3 + P1.4 完成；否则 learned runner 没有可信身份，无法进入正式门。

---

## 23.7 P3 前置条件（不在本轮开工）

仅在 P0–P2 成功后考虑：new hook 生成、图改写、权重更新、学习型策略路由。动手前必须先有：graph schema、allowed mutation set、static validation、execution trace diff、rollback。勾选清单即 `capability_surface.py:112-118` 的 `required_for_open_ended_evolution`。

---

## 23.8 每个工作包的固定验证流程（不可省略）

```bash
python -m pytest -q                                          # 528 → 不得下降
python scripts/verify.py                                     # 九项检查（数据完整性/pytest/两个 CLI/capability-audit/两个 mock smoke/准入与审计/源码一致性）
python -m evolution capability-audit                         # 能力面自述必须与实际派发一致
python -m evolution verify-ledger                            # 账本哈希链
python -m evolution cycle --cycle 1 --backend mock --mini    # 离线一次完整 cycle
python -m evolution experiment --mini --backend mock         # 离线多轮
```

- 以上全部离线、零 provider 成本；mock 后端写临时根目录，不触碰真实 registry 与原始数据（`scripts/verify.py` 已用 `tempfile` 保证）。
- **真实服务命令**（`cycle` / `experiment` / `preview` / `promote` / `full-self-evolution` / `final`）只在阶段门通过后执行，报告须记录时间、并发、模型、配置 hash 与源码 digest。
- 归档：`reports/<date>_<workpackage>/`，并标注证据等级（工程闭环通过 / 机制收益成立 / 无结论）——三者不得混用。

---

## 23.9 任务清单（对 §20 的修订版）

| # | 任务 | 现状 | 依赖 | 验收测试 |
|---|---|---|---|---|
| 1 | `FailureMechanismV1` | 新增 | — | `test_mechanisms.py`（§4.4 五条 + 变形） |
| 2 | 机制子组接线 | 部分（`refine_failure_families` 已在） | 1 | 同上 |
| 3 | `CapabilityRequirementV1` | 新增 | 1 | `test_capability_planning.py` |
| 4 | route 反证记忆 | 新增 | 3、5 | `test_route_failures.py` |
| 5 | 候选版本图 | 抽取（`research.py` 已有） | — | `test_research.py` 全绿 + 新增 lineage 测试 |
| 6 | `CandidateDeveloper`（L0/L1） | 新增 | 5、7(最小) | `test_development.py` |
| 7 | hook 依赖重放 | 最小版进 P0.3，扩展版 P1.1 | — | `test_backends.py` |
| 8 | `mechanism_opportunity` | 新增 | 1 | 指标测试 |
| 9 | 机制对齐晋升清单 + `promotion_diagnosis` | 扩展 `freeze_protocol` | 8 | `test_promotion.py` |
| 10 | 晋升经验继承 | 部分（写入/检索已接线；schema 缺 mechanism/artifact 字段） | 9 | 合成 golden path：registry 非空 → 下一代 proposer 可检索 |
| 11 | artifact digest + learned identity | 部分（`file_digest` 已在） | — | 替换权重 → 旧协议拒绝 |
| 12 | 校准审计 | 新增 | — | `scripts/audit_calibration.py` 产出离散表 |

---

## 23.10 进度追踪

每个工作包完成后在本表登记，写清 commit 与验证读数，作为 §21 验收清单的证据索引。

| 工作包 | commit | pytest | verify.py | 归档报告 |
|---|---|---|---|---|
| P0.1 机制表示 | `ffed49e`（补交） | 551 passed | 全 PASS（exit 0） | [reports/mechanism_20260922](reports/mechanism_20260922/README.md) |
| P0.2 能力规划 + route 反证 | `ffed49e`（补交） | 591 passed | 全 PASS（exit 0） | [reports/capability_planning_20260922](reports/capability_planning_20260922/README.md) |
| P0.3 开发循环 | `ffed49e`（补交） | 616 passed | 全 PASS（exit 0） | [reports/development_20260922](reports/development_20260922/README.md) |
| P0.4 机制对齐机会 | `ffed49e`（补交） | 656 passed | 全 PASS（exit 0） | [reports/promotion_alignment_20260922](reports/promotion_alignment_20260922/README.md) |
| §24 复审与查缺补漏 | `ffed49e`（补交） | 675 passed | 全 PASS（exit 0） | 见 §24 |
| P1.1 部分重放边界 | `ffed49e`（补交） | 680 passed | 全 PASS（exit 0） | [reports/p1_replay_artifacts_20260922](reports/p1_replay_artifacts_20260922/README.md) |
| P1.3 artifact digest | `ffed49e`（补交） | 680 passed | 全 PASS（exit 0） | [reports/p1_replay_artifacts_20260922](reports/p1_replay_artifacts_20260922/README.md) |
| P1.4 可执行身份 | `ffed49e`（补交） | 715 passed | 全 PASS（exit 0） | [reports/p1_identity_calibration_archive_20260922](reports/p1_identity_calibration_archive_20260922/README.md) |
| P1.5 校准审计 | `ffed49e`（补交） | 715 passed | 全 PASS（exit 0） | [reports/p1_identity_calibration_archive_20260922](reports/p1_identity_calibration_archive_20260922/README.md) |
| P1.2 候选存档与组件库 | `ffed49e`（补交） | 715 passed | 全 PASS（exit 0） | [reports/p1_identity_calibration_archive_20260922](reports/p1_identity_calibration_archive_20260922/README.md) |
| —— **P1 完成，五项全部落地** —— | | | | 见 §25、§26 |
| 检查点 A 首次真实运行 | 未开始 | — | — | 前置风险见 §26.5 |
| P2.1 runner registry + typed_semantic | `ffed49e`（补交） | 730 passed | 全 PASS（exit 0） | [reports/p2_runner_registry_20260922](reports/p2_runner_registry_20260922/README.md) |
| P2.3 变形测试 gate | `b61eff7` | 772 passed | 全 PASS（十项） | [reports/p2_metamorphic_20260923](reports/p2_metamorphic_20260923/README.md) |
| P2.2 Level B 沙箱 | `c7121e7` | 813 passed | 全 PASS（十项） | [reports/p2_sandbox_20260923](reports/p2_sandbox_20260923/README.md) |
| §28.6 存档接线 | `95e0d01` | 825 passed | 全 PASS（十项） | [reports/archive_wiring_20260923](reports/archive_wiring_20260923/README.md) |
| 小规模验证实验 | 见 §29 | 825 passed | — | [reports/validation_20260923](reports/validation_20260923/README.md) |
| 真实自进化实验 | 见 §30（0 准入） | — | — | [reports/real_evolution_20260923](reports/real_evolution_20260923/README.md) |
| 开发循环接入 cycle（§23.3.6） | `09cee47` | 841 passed | 全 PASS（十二项） | [reports/cycle_development_wiring_20260923](reports/cycle_development_wiring_20260923/README.md) |
| 开发循环真实运行 1 | 见 §31 | 843 passed | 全 PASS（十二项） | [reports/real_dev_20260923/run1_README.md](reports/real_dev_20260923/run1_README.md) |
| **开发循环真实运行 2（真实数据首次跑通开发阶段）** | 见 §31 | 843 passed | 全 PASS（十二项） | [reports/real_dev_20260923/run2_README.md](reports/real_dev_20260923/run2_README.md) |
| **底层深度审查：测量完整性（19 个活缺陷）** | 见 §32 | **917 passed** | 全 PASS（十五项） | [reports/deep_audit_20260923](reports/deep_audit_20260923/README.md) |
| P3 开放式演化 | 未开始（需 P0–P2 成功） | | | |

**P0/P1 状态小结（2026-09-22）**：P0 四项 + P1 五项全部落地，715 项测试通过，`scripts/verify.py` 七项全 PASS。
两处**已知的未接线**（不是缺陷，是待定的设计决策）：候选存档与组件库尚无运行路径写入，缺"由谁在何时归档"的决定。
**检查点 A 的前置风险**：`route_failures.jsonl` 与带 `mechanism_id` 的真实运行产物至今只存在于测试与 mock 中，
P0.2/P0.4 的读数在真实数据上从未验证；且独立 holdout（§8.3）未创建。运行前须先确认这两点。

### P0.1 完成记录（2026-09-22）

**交付物**：`evolution/mechanisms.py`（新增）、`evolution/worksheet.py::mechanism_subgroups`/`excluded_failure_cases`/`mechanism_report`、engine 的 cycle 与 preview 两条路径接线、proposer 消费端字段与 `mechanism_prompt_view`、`evolution/tests/test_mechanisms.py`（22 项）+ `test_engine_smoke.py` 两条接线回归。

**判据（E0）**：`reports/mechanism_20260922/` 的干跑报告显示，6 个历史失败案例建成 2 个机制、案例账目闭合（4 可提案 + 2 显式排除）；同路径不同机制可分（义务/偏差/修复操作不同）、同机制跨来源可合并（`SmolAgents` + `BFCL`）；key 与 case id、gold、source 无关（有改名不变性与 gold 字段不变性测试）。

**过程中修掉四个真实缺陷**：① `refine_failure_families` 静默丢弃案例（新增 `excluded` 逐例报出）；② 子组共用家族 id 会互相覆盖（改为独立 id + 保留粗前缀）；③ 误报机制被合同类型拆碎（义务仅在偏差非 `none` 时参与修复签名）；④ preview 报告不携带机制（而 preview 正是方案指定用于核对机制的入口）。

**未做**：未改任何门、未改 proposer 输出契约、未运行真实服务。`mechanism_opportunity`、能力规划、开发循环均属后续工作包。

### P0.2 完成记录（2026-09-22）

**交付物**：`evolution/capability_planning.py`、`evolution/route_failures.py`（均新增）、`mechanisms.repair_requirements()` 公开访问器、`gates.behavior_gate` 机器可读计数、`gaps.py` reason 分类法与身份剥离、engine 两侧接线（读反证→规划；写反证+gaps）、测试 38 项（`test_capability_planning.py` 18、`test_route_failures.py` 20）与 2 项端到端 cycle 测试。

**判据**：判分三层（fit / reach / express）分开，端到端 cycle 中"触发但不修好"被真实写入 `evolution.route_failure.v1`；两轮观测后该路线从 `sufficient_routes` 移出并降级 `research_only`；七类非反证情形各有测试拒绝记录。

**修掉两个真实缺陷**：① **fit 层缺失**——"召回从未运行"机制被观察类路线判为可修，理由是字段可读，但观察不能启动召回；② **极性不匹配分支不带触发读数**——而那正是"触发但无收益"的落点，导致反证永远无法成立。

**未做**：未改门判据（`behavior_gate` 仅新增报数字段）、未运行真实服务、未改 proposer 契约。反证记录是诊断输入，不参与准入。

### P0.3 完成记录（2026-09-22）

**交付物**：`evolution/candidate_versions.py`（版本图抽取，research 改为委托，磁盘格式与 22 项研究测试不变）、`evolution/development.py`（`CandidateDeveloper` + L0/L1 + `DevelopmentBudget`）、`evolution/backends.py` 的 `HOOK_DEPENDENCY_GRAPH`/`partial_replay_plan`（10 个 hook → 可达阶段）、`engine.develop_rows`（L1 配对测量）、`development` 配置块、`python -m evolution develop` 命令、`test_development.py` 25 项。

**判据**：干跑给出两种对照——mock 不修目标时 L1 拒绝、无版本冻结；mock 修目标时 L1 通过并冻结 `v001`（机制 `M-17aad6d46f7a`、route `recall_trigger`）。§15.4 第一条负例（目标改善但近邻退化 → 不得 freeze）有专项测试。

**修掉三个真实缺陷**：① **机制阶段名与运行时词汇不一致**——`mechanisms.py` 写 `recall`/`tracer`，运行时是 `recall_scanner`/`step_tracer`，导致 L0 可达性检查把每个召回类机制都判为"无 hook 可达"，开发循环无法启动（影响 P0.1/P0.2 的阶段读数）；② preview 报告丢弃案例列表，开发循环拿到空案例集；③ L1 误用"阶段状态变化"作判据（非计划要求，且 mock 后端不上报阶段状态），改为计划第 3/4 条的"候选确实触发过"。

**未做**：未改任何门判据、未运行真实服务、开发循环不写 registry 不判准入。`mechanism_opportunity` 与机制对齐晋升清单属 P0.4。

### P0.4 完成记录（2026-09-22）

**交付物**：`evolution/metrics.py::mechanism_opportunity`（机制级机会，按评测方成员证据计数）、`evolution/promotion_alignment.py`（新增：v2 协议构造/复核、候选绑定、开发目录导入、机器可读诊断）、`promotion.py` 的 v2 冻结与运行分支（`_run_aligned`）、engine 的机制预检（`_mechanism_opportunity`，未绑定时逐字保留旧语义）、CLI 的 `--mechanism/--membership/--challenge/--from-development/--version`、测试 40 项（`test_mechanism_opportunity.py` 14 + `test_promotion_alignment.py` 26）。

**判据**：干跑覆盖五种终态——机制修好且保护/挑战干净 → `promoted`；增益集无可修机会 → `promotion_unmeasurable` 且**只跑 control 臂**；保护集退化 → `protection_regression`；机制外挑战退化 → `challenge_regression`；对同一冻结协议二次尝试 → 阻断（`protocol already consumed`）。冻结时即拒绝的构造缺陷共 8 类，各有测试。

**关键设计**：成员关系只能由评测方绑定提供，绝不从 case id / 来源 / 同极性推断（有改名不变性测试）；收益与保护/挑战退化不相加抵消；四门在 v2 中不可裁剪；`bind_candidate()` 只允许冻结前绑定，运行期不存在事后绑定路径。

**未做**：未运行真实服务；脚本化替身只用于让五种终态可复现，本记录不构成检测能力或泛化结论。独立 holdout（§8.3）仍未创建。

## 检查点 A 入口条件（P0 已完成，尚未运行）

P0 四项全部落地，真实运行的入口条件已具备：

- 机制表示：`M-*` 从任务义务/观测偏差/失败阶段/修复操作推断，与 case id、gold、来源无关；
- 能力规划与反证：三层判定（fit/reach/express）+ 已证伪路线记忆，`gaps.jsonl` 与 `route_failures.jsonl` 有内容可查；
- 开发循环：L0/L1 + 版本 lineage + `freeze()` 单一出口，开发预算与晋升预算分离；
- 机制对齐晋升：v2 协议冻结机制、成员证据、候选哈希与基线 registry，按三类集合计分。

运行前仍需按 §23.4 准备：确认存在 ≥2 confirmed 机制案例、≥1 未用于开发的正式增益案例、≥2 TP + ≥2 TN 保护案例与机制外挑战集；并按 §8.3 决定独立 holdout 是否本轮创建。**本轮未运行真实服务，也未创建 holdout。**

**整体成功判据仍是 §17**：`G0 < G1 < G2` 必须以"新机制真实修复 + 无超限保护退化 + 已晋升能力继续存在 + 下一代确实基于上一代版本运行"证明；`proposer` 输出变化、reasoning token 增加、hook 触发、confidence 提升、某个中间 state 变化，都不单独构成自进化成功。

---

# 24. 复审与查缺补漏（2026-09-22）

对 P0.1–P0.4 的落地做一次独立复审：三路并行审计（开发循环 / 晋升对齐 / 引擎与 CLI 接线），每条高危结论都由**可执行复现**确认后才动手。本节只记录"计划要求 vs 代码事实"的差距，以及本轮修掉了什么。

## 24.1 修掉的真实缺陷

按严重度排列，全部有回归测试。

| # | 缺陷 | 证据 | 修法 | 测试 |
|---|---|---|---|---|
| 1 | **preview 的 bootstrap 分支必然崩溃**——`_extend_patrol(patrol_cases=...)` 传了方法不接受的参数名（签名是 `patrol_rows`），而无家族时的预览正是唯一走这条路的场景 | 复现：`TypeError: got an unexpected keyword argument 'patrol_cases'` | 改回 `patrol_rows=` | `test_preview_bootstrap_path_runs_when_the_patrol_finds_no_family` |
| 2 | **L1 定位判据是"步骤变了"而非"步骤对了"**——把一个错步骤换成另一个错步骤也算改善，gold 从来没被读过 | 复现：baseline step=1、candidate step=9、gold=1 → `passed=True` | 目标改善要求"命中标注步骤且 baseline 未命中"；无标注时不得声称改善；`_case_regressed` 同步 | `test_l1_localization_refuses_a_differently_wrong_step`、`test_l1_localization_needs_the_annotation_to_claim_an_improvement` |
| 3 | **声明了但读不到的机制绑定被当成"未绑定"**——`_mechanism_opportunity` 先判空 dict 再看错误字段，而读取失败恰好把两个 dict 都留空，于是"要求按机制计分"退化为静默回退，报告里连 `evaluable: False` 都没有 | 复现：绑定路径不存在 → 返回 `None` | 错误优先于空值判断 | `test_declared_but_unreadable_mechanism_binding_is_not_a_silent_fallback` |
| 4 | **两个可达 reason code 不在诊断表里**——metric 的 `mechanism_invalid` / `mechanism_measurement_unresolved` 直接进 `diagnosis()`，落到 `unclassified`（"没记录原因"），而实际上记录了 | 复现：`diagnosis("mechanism_invalid")["category"] == "unclassified"` | 新增 `REASON_CATEGORIES` 单一来源，覆盖 metric 词表与旧路径词表 | `test_every_reachable_reason_code_has_a_category` |
| 5 | **旧路径把"不可测/被阻断"记成"候选设计失败"**——`promotion_unmeasurable`、`promotion_blocked_preflight` 都映射到 `candidate_no_effect`，与模块自己的不变式"不可测不等于被证伪"直接冲突，而 v1 是默认路径 | 复现：三条不同终态得同一个 code | 新增 `LEGACY_STATE_REASONS`，五种终态各归其位 | `test_unmeasurable_is_never_recorded_as_candidate_design_failure` |
| 6 | **同一批次可在新路径重新冻结再计分**——per-path 的 `.attempt.json` 换个路径就绕过，`frozen_at` 一变 `protocol_hash` 也变，§7.5"不得在同一冻结协议上重试"落空 | 复现：同一 batch 两次冻结都成功，字段只差 `frozen_at`/`reason` | 冻结写 `protocol_frozen` 账本事件，按 (batch_hash, candidate_hash, mechanism) 全局拒绝重冻结 | `test_refreezing_the_same_batch_at_a_new_path_is_refused` |
| 7 | **`develop` 没有出口**——只有 `CandidateDeveloper.freeze()` 会写 `frozen` 状态，而 `cmd_develop` 从不调用它，于是 CLI 产出的版本永远被 `candidate_from_development` 拒绝（"must be frozen"） | 复现：`cmd_develop` 函数体内无 `.freeze(` 调用 | `cmd_develop` 在选定版本后调用 `freeze()` 并在报告中给出 `frozen` 块 | 端到端复现（见 24.3） |
| 8 | **L1 的近邻检查在生产中恒为空**——CLI 传 `neighbor_cases=[]`，且机制根本没有近邻集合 | 复现：`develop` 的 L1 只测目标 | 新增 `worksheet.attach_normal_neighbors()`，从巡逻中"基线已判对"的案例给每个机制挂近邻，两侧调用 | `test_mechanisms_carry_normal_neighbours_from_the_patrol` |
| 9 | **开发预算两项形同虚设**——`max_provider_calls` 的计数器从无调用者（`spend_provider` 零引用），`max_l1_cases` 只按 1× 计费而实际跑两条臂 | 复现：`spend_provider` 全仓仅定义处出现 | 每次起草计一次 provider 调用；L1 按"用例×2 臂"计费；`_replay_cost()` 同时用于准入判断 | `test_l1_case_budget_charges_both_arms`、`test_l1_provider_budget_stops_the_loop` |
| 10 | **`normal_neighbors_clean` 吞掉异常**——谓词 runner 抛错被 `continue` 跳过，于是"检查没跑"被报告为 `passed=True` | 复现：runner 全抛错时该检查通过 | 异常收集进 detail 并判失败；`check_l0` 的 schema 检查改为记录任何异常而非只捕 `ValueError` | `test_l0_reports_a_neighbour_runner_that_raises`、`test_l0_records_an_unexpected_validator_error_instead_of_raising` |
| 11 | **`develop_rows` 收到 `split` 却不用**——开发测量可以落在 sentinel/gain 上，把正式评测用例变成开发暴露，直接违反 §19.6 | 复现：传 gain case 不报错 | 开发测量命中正式集合即拒绝 | `test_development_never_measures_on_formal_gain_cases` |
| 12 | **`--dry-run` 会调用真实服务**——标志判断在 `preview_cycle` 之后，dry run 先跑了最贵的一次巡逻（实测挂起 5 分钟并留下缓存） | 实测：`develop --dry-run` 超时，且写盘 | dry run 强制 mock 后端，优先读已保存的 preview 报告，报告注明 `preview_source` / `provider_calls: 0` | 见 24.3 |
| 13 | **CLI 角色集合重叠被静默吞掉**——`{**benefit, **protection, **challenge}` 让后写的赢，§8.2 的"三类互斥"只剩文档 | 复现：同一 case 出现在两个 flag 里不报错 | 检测冲突并 `SystemExit`，不产出协议 | `test_cli_refuses_a_case_declared_in_two_role_sets` |
| 14 | **`verify_alignment` 不重算 freeze 时的前置条件**——localization 的 gold-step 要求与 `baseline_registry_hash` 只在构造时检查，只调 `verify_alignment` 的消费者会接受 `build_alignment` 会拒绝的协议 | 复现：抹掉 gold_steps 重算哈希后仍通过 | 两条件在 `verify_alignment` 中重新推导 | `test_verify_alignment_rederives_the_localization_precondition`、`test_verify_alignment_requires_the_baseline_registry_identity` |
| 15 | **`--case-id` / `--workers` / `meta --mini` 是死 flag** | AST：无读取点 | 三者均接入真实行为（收窄目标集并校验成员、写入 backend workers、过滤 mini 协议报告） | 手工验证 |
| 16 | **死代码与重复实现**：`gain_opportunity_for`（零引用别名）、`development.mechanism_routes`（零引用）、`VERSION_RESULTS`（与 `candidate_versions.VERSION_STATUSES` 重复）、`engine` 里重复赋值两次的 `worksheet["route_failures"]`、`research.py` 里被本地定义遮蔽的 `canonical_hash`/`case_file_name` 导入、8 个未使用导入 | AST + grep | 全部移除；`research.py` 的遮蔽改为直接复用导入 | 全量测试保持通过 |

## 24.2 有意保留、不算缺陷的部分

- **`_mechanism_opportunity` 未绑定时返回 `None`**：这是 §8.1 要求的"无成员证据就不得推断机制"的落点，未绑定运行必须逐字保留旧极性语义。本轮只修了"声明了但读不到"这一种情形。
- **两类 capability surface 报告同时存在**：`evolution/capability_surface.py`（`…v2`，回答"当前运行时能创造什么"）与 `capability_planning.capability_surface_report()`（`…v1`，回答"某个修复需要什么能力"）。名字相近但问题不同，都有活调用者，未合并。
- **`max_provider_calls` 的单位是"调用"不是"用例"**：L1 的成本由 `max_l1_cases` 单独封顶，provider 计数管的是修订轮数，两者不重复计费。
- **独立 holdout（§8.3）仍未创建**：属于检查点 A 的决策项，不是代码缺陷。

## 24.3 默认配置的参数修正

`development.max_l1_cases` 从计划的 `12` 改为 `24`，理由写在 `config.py` 注释里：计划写这个数时开发循环还没有按"两条臂"计费。修好计费后，一个带 4 个近邻的 4 案例机制一次重放就要 16 次用例评估，`12` 会让循环在第一次重放前就停下。这不是放宽门限——它只是让预算数字与实际成本同单位。

## 24.4 验证

- `pytest`：**675 passed**（较本轮开始时的 656 增加 19 项回归测试，全部对应 24.1 与 24.5-2 的条目）；
- `scripts/verify.py`：七项全 PASS（`dataset_integrity` / `pytest` / `detector_cli` / `evolution_cli` / `capability_audit` / `smoke` / `capability_smoke`）；
- 四份归档复现脚本（`mechanism_20260922` / `capability_planning_20260922` / `development_20260922` / `promotion_alignment_20260922`）全部重跑，读数与结论不变：机制账目闭合、五种晋升终态语义未变；
- 端到端离线复现（mock）：`develop` → `ready_for_freeze` → `freeze()` → `candidate_from_development` 返回 `evolution.candidate.v2`，`development_cases` 8 例，预算记账 `provider_calls=1 / l1_cases=16`；
- **本轮仍未运行真实服务，仍未创建 holdout。** 期间两次误触真实后端的进程已终止，由其产生的 `artifacts/` 缓存已清理。

## 24.5 仍未解决（留给后续工作包）

1. **检查点 A 的入口条件未验证**：需要真实数据确认 ≥2 confirmed 机制案例、≥1 未用于开发的正式增益案例、≥2 TP + ≥2 TN 保护案例与机制外挑战集是否齐备。
2. ~~**引擎侧机制绑定仍无配置入口**~~（本轮已补）：`cycle` / `experiment` / `governance` / `preview` 新增 `--mechanism-binding` + `--membership-binding`，两者必须同时给出，否则拒绝启动——成员证据只能由评测方提供，不得由框架从 case id / 来源 / 极性推断。仍然**没有**任何 `evolution/configs/*.json` 预设该绑定，这是有意的：绑定是一次运行的评测方输入，不是随包默认值。
3. **P0.4 的两问仍是 `None`**：`mechanism_unrepairable` 与 `action_class_insufficient` 在单批次内无法回答——它们需要跨批次/跨 action class 的证据，属于 P2 的能力空间扩展，不应硬编码进本次判定。
4. **P1 各项未动**：部分重放缓存、候选归档与组件库、artifact digest、`structured_key` 扩展、校准审计。
5. **revision 证据未接入**：`route_failures` 支持 `revisions_observed` 作为独立准入依据，但 `engine.record_route_failures` 从不上报它，所以该分支目前只能靠"重复观测 ≥2"触发。

---

# 25. P1 执行记录（2026-09-22）

本轮做 **P1.1（部分重放）** 与 **P1.3（artifact digest）**。P1.2 / P1.4 / P1.5 未开工。
每项都在动手前先确认"计划要求 vs 代码事实"，修掉的缺口与保留的语义分别列在下面。

## 25.1 P1.1 部分重放边界

**核对结论**：`partial_replay_plan` 是死代码——除归档复现脚本外零调用者。真正的重放边界由
`LiveDetectorBackend._frozen_stages` 内部一组硬编码 hook 名决定（`"scanner.system" not in hooks`
之类）。也就是说 §7.3 的依赖图只是**声明**，运行时用的是另一张表，两者不会因为对方改动而失败。

**落地**：

1. `REPLAY_STAGES`——阶段顺序的唯一拼写，图、边界函数与报告共用。
2. `replay_boundary(hooks)`——返回 `rerun_stages` / `unknown_hooks` / `boundary_key`。
   **未知 hook 保守取全流程**：未声明的 hook 无法证明可达范围为空，假设为零会冻结它
   其实能影响的阶段（这正是 §23.2 "新 runner 必须显式声明能力" 的运行时对应物）。
3. **缓存键**：结果文件名带 `boundary_key`，使边界逻辑扩展后旧缓存不为新边界作答。
   仅在 registry key **非内容派生**时附加——内容派生的 key 已经蕴含 hook 集合
   （`content_hash` 覆盖 entries），无条件附加只会挤占文件名预算并触发 Windows 路径上限。
   `_content_derived_key()` 是这条判断的显式落点，有注释说明为什么两种情形不同。
4. `partial_replay_plan` 增加 `boundary_key` 字段；两处 reporting 随之可见。
5. **开发循环报告 `replay_boundary`**：让"开发比重跑整臂便宜"成为可核对的事实，而不是假设。

**没有改门限**：边界只回答"重跑多少"。`requires_fresh_evaluation` 与
`detector.api._FROZEN_STAGE_HOOKS`（fail-closed 拒绝重放的表）逐字保留。三张表的关系
现在有测试钉住：

- `HOOK_DEPENDENCY_GRAPH`（本包）＝ hook 能影响哪些阶段；
- `_FROZEN_STAGE_HOOKS`（detector）＝ 哪些 hook 使冻结阶段失效、必须拒绝重放；
- `_frozen_stages`（运行时）＝ 单次重放实际复用了什么。

不变量是**包含关系**：能被 hook 作废的冻结阶段，必然在该 hook 的影响范围内，因此图必须
声明它。`test_graph_reach_covers_every_hook_the_detector_refuses_to_replay_around`
逐 hook 校验。写这条测试时先写错过一版——它断言"图上可达的阶段不得出现在冻结信封里"，
跑出 `judge.post → recall_scanner` 的分歧。查证后确认**分歧是测试理解错了**：
`judge.post` 确实影响 recall（它写入 facts，`_matches` 会读），但冻结一个**已结算**
阶段仍然成立，因为该阶段的值就是同一批输入产生的。两者的差别是"可达"与"必须重跑"，
不是矛盾。

## 25.2 P1.3 artifact 身份

**落地**：

- `artifact_digest(path)`：文件或目录的内容摘要。路径不存在时**拒绝**，不返回 `"missing"`
  哨兵——哨兵会和真值一样参与哈希，"权重被换成空" 正是这样通过校验的。
- `artifact_manifest(*paths)`：`schema_version` + 逐件摘要 + 覆盖两者的 manifest 摘要。
  空 manifest 合法，表示"该 runner 不声明外部 artifact"，与"声明了但读不到"是两句不同的话。
- `source_digest()`：`glob("*.py")` → `rglob("*.py")`，覆盖子包；**排除 `tests/`**。
  测试文件不参与任何一次检测，让它的改动失效所有缓存、拒绝所有冻结协议，会把摘要的
  权威花在它并不描述的东西上。这正是 P1 要求里"此改动会失效既有缓存，属预期"的落地
  ——只是失效范围限定在真正的执行语义上。
- `freeze_protocol(..., artifact_paths=[...])` 冻结 manifest；
  `load_protocol` 与 `_run_aligned` 在测量前逐字节复核（`_verify_artifact_manifest`）。
- CLI `freeze-protocol --artifact`（可重复），端到端已验证写入正确。

**§12.1 要求的回归**（`test_promotion.py`）：替换权重 → 拒绝加载；删除权重 → 拒绝加载；
不声明 artifact → manifest 为 `{}` 且摘要非空。三条都有专项测试。

## 25.3 验证

- `pytest -q`：**680 passed**（§24 结束时 675，本轮 +5：3 项边界 + 2 项 artifact）；
- `scripts/verify.py`：七项全 PASS；
- 新增归档复现：`reports/p1_replay_artifacts_20260922/`（`README.md` + 只读复现脚本 +
  `replay_and_artifacts.json`），脚本输出显示 tracer 类候选只需重跑 2/6 阶段、
  未知 hook 取全流程、替换权重被拒。
- **未运行真实服务，未创建 holdout。**

## 25.4 仍未解决

1. **P1.2 候选存档 / 组件库**：`work_root/candidates/`、`work_root/components/` 未建；
   proposer 输入无"可复用组件"段。§9 的三层存储目前只有第三层（Active Registry）真实存在。
2. **P1.4 `structured_key` 扩展**：未知 runner 仍返回 `None`，即两个不同模型可以被判为
   同一 intervention。P2 的 typed_semantic runner 落地前必须补上。
3. **P1.5 校准审计**：`evolution/calibration.py`、`scripts/audit_calibration.py` 未建。
4. **`boundary_key` 的缓存隔离范围**：非内容派生 registry key 才带边界键。当前
   `make_registry` 总是写 `content_hash`，所以实际路径都有隔离；但绕过 `make_registry`
   手写 registry 时会落到该分支，这一点已注释在代码里。
5. **检查点 A 仍未运行**：入口条件（≥2 confirmed 机制案例等）需要真实数据核对，
   独立 holdout（§8.3）未创建。

---

# 26. P1 收尾执行记录（2026-09-22）

本轮完成 **P1.4 / P1.5 / P1.2**，P1 全部包已落地（P1.1 与 P1.3 见 §25）。

## 26.1 P1.4 可执行身份（§12.2）

**核对**：`structured_key` 对非 `typed_dsl` runner 的 `program_hash` 恒为空串，且 artifact
的路径与摘要完全不进键。实证反例：两个同 predicate、同 runner 名、绑定不同模型的条目产生
**逐字节相同**的键，会被 STRUCTURAL 去重判为"与已部署重复"。

**关键判断**：这条缺陷**今天不可达**——没有任何 learned runner 被注册（那是 P2.1），
`validate_intervention` 会先在 schema 层拒绝未知 runner。所以本包做的是**纵深防御**，
测试按此如实断言，另用 monkeypatch 模拟 P2.1 注册后检查会真正生效。把"潜伏"写成"已修复
活缺陷"会是虚报。

**落地**：`DETERMINISTIC_RUNNERS`；`artifact{id,digest}` 进键（路径刻意排除）；
`identity_incompleteness()` 报出缺失原因——**"无法判断是否相同"不得当作"不同"通过**；
三处接线（STRUCTURAL 门、冗余比较、L0）。确定性条目的**等价关系不变**：键元组多了一位，
但它们之间仍和改动前一样判等（键不落盘，非兼容性破坏）。

## 26.2 P1.5 校准审计（§13.2）

**落地**：`evolution/calibration.py` + `scripts/audit_calibration.py`。

只出**离散 reliability table**，并把连续指标**明确标注为未实现并给出原因**：`judge.confidence`
是无数字尺度的有序级别，算 Brier/AUROC 等于编造映射。原因是报告的一部分，读者看到的是
"为何没有"而不是"好像漏了"。

三个刻意的判断：**弃权是独立结局**（并入"错"会让谨慎的系统显得失准）；**空分母是 `None` 不是
`0.0`**；**单调性作为发现报告而不强制**（非单调说明该级别不可用作排序，抹平它就毁了审计目的）。

对真实归档 30 例：high 准确率 0.80 / 覆盖 0.93，medium 0.50（含 2 例弃权），单调成立。

## 26.3 P1.2 候选存档与可复用组件（§9）

**落地**：`evolution/archive.py`，两个存储，与 Active Registry 三层分离。

- **候选存档**：保留**失败版本与失败原因**，按 `(mechanism_id, candidate_hash)` 去重，
  `repeated_failure_reasons` 按候选计数。
- **可复用组件**：本框架里可复用的单元是**校验过的 typed-DSL 程序**——唯一同时具备稳定摘要、
  封闭词表、无自由代码、且有两个消费者的对象。prose directive 刻意不算组件。
  组件层**无法晋升**：不导出任何通往 registry 的路径，有测试断言。
- **接线**：`proposer.set_components()` 只作上下文；引擎按机制所需的**能力**检索注入。

**修掉两个自己写出的缺陷**（都由测试或端到端验证抓出）：
1. 引擎初版按 actionability 的 `classification`（`actionable`）检索，而组件按能力面词表
   （`control_flow`）登记——两个词表共用一个字段名，检索**永远匹配不到**；
2. `record_component` 只按程序摘要去重，同一程序换 `kind` 会静默返回旧记录，
   使调用方刚声明的角色**不可检索**。

## 26.4 验证

`pytest`：**715 passed**（§25 结束时 680，+35）。`scripts/verify.py` 七项全 PASS。
新增归档：`reports/p1_identity_calibration_archive_20260922/`。**未运行真实服务，未创建 holdout。**

## 26.5 P1 完成后的状态与下一步

**P1 五项全部落地**，但有两处**存储可用而未接线到运行路径**，必须说清楚：

1. **候选存档没有任何运行在写**：`archive_candidate` / `ingest_development_run` 已可用且有测试，
   但 `develop` / `cycle` 尚未调用。缺的是"由谁在何时归档"的设计决定（开发循环末？cycle 末？），
   不是实现能力。
2. **组件库只由显式调用写入**：尚无"某候选的 predicate 自动登记为组件"的路径。

这两条不构成缺陷——它们是没有隐含默认值的接线决策——但不补上，§9 的存储就只是空仓库。

**下一步仍建议 P2**，顺序：

1. **P2.1 `RUNNER_REGISTRY` + `typed_semantic`**：这是 P1.4 那套 identity 检查从"纵深防御"
   变成"把守者"的时刻，也是**唯一能把上述两个存储真正用起来**的一步（learned runner 才需要
   artifact 摘要与组件复用）。前置 P1.3/P1.4 已就绪。
2. **P2.3 变形测试作为准入 gate**：§15.2 的六类变形（case id 改名、source 改名、无关工具调用
   插入、近邻同 token、语义等价改写、无关 event 移动）固化为新 runner 的门。
3. **P2.2 Level B 沙箱组件**：规模最大，放最后。

**检查点 A 仍建议排在 P2.1 之后**：它需要你决定 holdout 策略与服务预算，且我核查发现
`route_failures.jsonl` 与带 `mechanism_id` 的真实运行产物至今只存在于测试与 mock 中——
P0.2/P0.4 的读数在真实数据上尚未验证过。这一点在跑之前必须知道。

---

# 27. P2.1 执行记录：显式 runner registry 与 typed_semantic（2026-09-22）

## 27.1 修掉的缺口

runner 空间此前是**隐式 if/else**：除 `typed_dsl` 外的一切都落到内置谓词表，两处派发点
各写一遍同样的分支。这种形状的特定失效模式是——**未注册的 runner 名会静默执行另一个执行器**，
而"没有声明"与"用默认"在代码里无法区分。§23.2 的红线"新 runner 必须显式声明能力"因此
此前只有文档，没有落点。

## 27.2 落地

`RUNNER_REGISTRY`：每个执行器声明 supported hooks / intervention types / input schema /
deterministic / learned / requires_artifact / `terminal_authority`。两处派发改查表，
**未注册 runner 是错误而不是回退**；声明在 `validate_intervention` 中被强制；
`RunnerSpec` 构造即拒绝"learned 且持终局权威"。

`typed_semantic` 新增，权威级别 `evidence_only`：仅 `evidence_check`，仅三个非控制流 hook，
**刻意排除 `recall.trigger`**——不得定罪的执行器也不得决定何时启动召回网络。
裁决词表 `supported`/`contradicted`/`abstained`，**弃权是一等结局**。
必须绑定 `artifact{id,digest}`，且运行时会与执行器**实际加载**的 artifact 比对。

## 27.3 本记录最重要的一条

**"evidence-only" 由声明与结构双重保证，而结构那一半才是决定性的。**

语义裁决会被**记录**（L1 行为门与人都能看到），但它的谓词**不进入封闭 fact 词表**
（`convict_mechanical_violation` 只接受内置或 typed_dsl 谓词的定罪裁决），所以任何 guard
都无法消费它。这意味着即使声明被误改、或未来有人在别处放宽检查，
**语义裁决仍然到不了终局判决**。验证脚本把这条单独演示并归档。

这是刻意设计的冗余：声明说明意图，结构使其无法绕开。

## 27.4 附带修正（三处，都是"新能力到达不了"的问题）

1. `evidence_check` 分支在 runner 已注册但谓词未定义时误报"no registered executor"，
   会把调用方引向错误方向；现已按实际原因分派消息，因为 proposer 的修复循环读这段文本。
2. `make_intervention(..., artifact=...)`：构造器此前无法表达带 artifact 的条目，
   而它**构造时即校验**，所以 P2.1 的 runner 会无法从草拟路径到达——校验器接受、
   构造器表达不出的能力等于不可用。
3. `capability_surface_report()` 新增 `executable_inventory.runners`，并区分
   `name_a_learned_executor`（真）与 `generate_a_learned_executor`（假）：
   框架能**绑定**一个注入的执行器，但它**不自带**。把后者报成真会声称一个不存在的能力。

## 27.5 验证

`pytest`：**730 passed**（§26 结束时 715，+15）。`scripts/verify.py` 七项全 PASS。
`python -m evolution capability-audit` 现列出三个 runner 及其权威级别。
归档：`reports/p2_runner_registry_20260922/`（README + 只读复现脚本 + JSON）。
**未运行真实服务，未创建 holdout。**

## 27.6 必须说清楚的一条：这是契约，不是能力

**没有任何真实语义执行器存在。** `typed_semantic` 是一个可绑定的契约——它保证"一旦绑上，
约束会被执行"，但不提供任何判断能力。要用它必须自行实现 `SemanticExecutor` 子类并提供
artifact 摘要。

因此 P2.1 的完成**不等于**框架获得了语义判断能力，只是获得了**安全地接纳**这种能力的通道。
这一点也写在归档 README 的第一条"未做"里。

## 27.7 下一步

**建议 P2.3（变形测试 gate）而非 P2.2**，理由：§23.6 把变形测试列为新 runner 的准入条件，
而 P2.1 恰好打开了一类新 runner 的入口。在**真实执行器接入之前**把门立好，比之后补更省力
——否则第一个真实执行器会成为事实上的标准。§15.2 的六类变形（case id 改名、source 改名、
无关工具调用插入、近邻同 token、语义等价改写、无关 event 移动）可直接固化为该 runner 的
准入测试集。

P2.2（Level B 沙箱）规模最大且与"允许生成任意代码"相邻，应放最后，且需要 §23.7 的
前置条件讨论。

**检查点 A 仍排在 P2 之后**，且仍有两个未决前提：holdout 策略与服务预算需要你决定；
`route_failures.jsonl` 与带 `mechanism_id` 的真实运行产物至今只存在于测试与 mock 中。

---

# 28. 交接说明（2026-09-23，供新会话使用）

本节是**自包含**的：新会话不需要读取任何先前对话，只需读本文件 + 仓库。

## 28.1 项目与工作目录

- 仓库：`E:\agent hallucination\agent hallucination`（Windows / Git Bash）
- 本文件：`agent_hallucination_self_evolution_optimization_plan.md`（仓库根，**未纳入 git**）
- 当前基线：**841 passed**，`python scripts/verify.py` 十二项全 PASS
- 运行测试：`python -m pytest -q`（约 65 秒）

> **本节已过期过一次。** §28.3 的"剩余任务"中，P2.3 与 P2.2 已由 §29 完成、§28.6 的存档接线
> 已由 §29.3 完成、检查点 A 的首次真实运行已由 §30 完成、开发循环接入 cycle 已由 §31 完成。
> **先读 §23.10 的进度表与最后一节执行记录，再决定做什么**，不要按 §28.3 的清单开工。

## 28.2 已完成（P0 + P1 + P2.1）

| 阶段 | 工作包 | 归档证据 |
|---|---|---|
| P0.1 | 失败机制表示（`evolution/mechanisms.py`） | `reports/mechanism_20260922/` |
| P0.2 | 能力规划 + route 反证（`capability_planning.py`、`route_failures.py`） | `reports/capability_planning_20260922/` |
| P0.3 | 候选开发循环（`development.py`、`candidate_versions.py`） | `reports/development_20260922/` |
| P0.4 | 机制对齐晋升（`promotion_alignment.py`、`metrics.mechanism_opportunity`） | `reports/promotion_alignment_20260922/` |
| §24 | 独立复审与 16 项缺陷修复 | §24 |
| P1.1 | 部分重放边界（`backends.replay_boundary`、缓存键） | `reports/p1_replay_artifacts_20260922/` |
| P1.3 | artifact digest（`artifact_digest`/`artifact_manifest`） | `reports/p1_replay_artifacts_20260922/` |
| P1.4 | 可执行身份（`structured_key` + `identity_incompleteness`） | `reports/p1_identity_calibration_archive_20260922/` |
| P1.5 | 校准审计（`evolution/calibration.py`、`scripts/audit_calibration.py`） | 同上 |
| P1.2 | 候选存档 + 组件库（`evolution/archive.py`） | 同上 |
| P2.1 | runner registry + `typed_semantic`（`detector/interventions.py`） | `reports/p2_runner_registry_20260922/` |

## 28.3 剩余任务

### P2.3 变形测试 gate（建议先做，§15.2 六类）

把 §15.2 的六类变形固化为**新 runner 的准入 gate**：

1. case id 改名 → 机制与候选行为不变；
2. source 名改名 → 不改变 mechanism；
3. 轨迹插入无关工具调用 → 不改变特定 evidence checker；
4. 正常近邻加入相同表面 token → 不误触发规则；
5. expected/actual 做语义等价改写 → typed semantic checker 保持相同判断；
6. localization case 移动无关 event → 不改变 root-cause relationship。

**为什么先做**：P2.1 刚打开一类新 runner 的入口。在**真实执行器接入之前**立门，比之后补省力——否则第一个真实执行器会成为事实上
的标准。建议落点：新增 `evolution/metamorphic.py`（生成变形对 + 判定）+ `evolution/tests/test_metamorphic.py`，
并在 `validate_intervention` 或 admission 路径接入对新 runner 的准入。

### P2.2 Level B 沙箱（规模最大，放最后）

纯函数、固定输入 schema、禁网、禁任意文件系统访问、导入白名单、CPU/时间/内存上限，
必须通过 property + metamorphic 测试。**动手前需讨论 §23.7 的前置条件**：该能力与
"允许生成任意代码"相邻，需要 graph schema、allowed mutation set、static validation、
execution trace diff、rollback 的既有讨论。

### 检查点 A（首次真实运行，§23.4）

**两个未决前提，必须由用户决定，不要自行假设：**

1. **holdout 策略**：`datasets/protocols/final_holdout_v1.json` 不存在；693 例已全部处于开发暴露范围。
   本轮是否创建独立 holdout？
2. **真实服务预算**：需要多少调用/时长。

**且必须知道的事实**：`route_failures.jsonl` 与带 `mechanism_id` 的真实运行产物
**至今只存在于测试与 mock 中**——P0.2 的路线反证、P0.4 的机制级读数在真实数据上从未验证过。
运行前先核对 §23.4 的入口条件（≥2 confirmed 机制案例、≥1 未用于开发的正式增益案例、
≥2 TP + ≥2 TN 保护案例、机制外挑战集）。

### P3 开放式演化（未开工，前置未满足）

new hook 生成、图改写、权重更新、学习型策略路由。**不在本轮范围**。前置是
`capability_surface.py` 的 `required_for_open_ended_evolution` 清单。

## 28.4 不可违反的红线（§1.2 与 §19，全程有效）

1. 不得通过放宽 HARMLESSNESS 或允许新错误来制造"自进化成功"；
2. 不得用 LLM 或 JEV 预测替代正式晋升分数；
3. 不得在已观察的正式结果上反复搜索候选；
4. 不得将 hosted Jev 作为不可冻结的终局权威；
5. 不得将 case id、gold label 或特定答案常量用于生成修复规则；
6. 不得将纯概率分数改善解释为检测或定位能力的真实提升；
7. **development 与 promotion 数据/预算必须隔离**；
8. **learned artifact 必须有 digest**；learned runner **不得持终局定罪权威**；
9. 每个正式候选必须绑定 mechanism id；每次拒绝必须产生机器可读原因；
10. 新指标必须标注 `promotion-authoritative` / `diagnostic-only` / `calibration-only`。

## 28.5 每个工作包的固定验证流程（§23.8，不可省略）

1. `python -m pytest -q` 全绿；
2. `python scripts/verify.py` 七项全 PASS；
3. 相关归档复现脚本重跑，读数与结论不变；
4. 新增归档报告到 `reports/<date>_<topic>/`（README + 只读复现脚本）；
5. 更新本文件 §23.10 进度表 + 新增一节完成记录；
6. **不运行真实服务**（除非用户明确批准检查点 A）；
7. 结束后清理 `artifacts/` 中由本次运行产生的临时产物。

## 28.6 已知的"存储可用但未接线"

这两条不是缺陷，是**待定的设计决策**，接线前需明确由谁调用：

1. **候选存档无运行路径写入**：`archive.archive_candidate` / `ingest_development_run` 可用且有测试，
   但 `develop` / `cycle` 未调用。缺"开发循环末还是 cycle 末归档"的决定。
2. **组件库只由显式调用写入**：尚无"某候选的 predicate 自动登记为组件"的路径。

## 28.7 工作方式约定（用户在本轮中反复确认过的）

- 一个工作包一个提交，保持测试基线持续通过；
- 修改前先核对"计划要求 vs 代码事实"，**不要相信计划文档描述的代码状态**（它比仓库旧）；
- 任何高危结论先**可执行复现**再动手；区分"活缺陷"与"潜伏缺陷"，不要虚报；
- 归档报告要写清**未做什么**，不只是做了什么。

---

# 29. P2.3 / P2.2 / §28.6 执行记录与验证实验（2026-09-23）

本节记录本轮三个工作包与两个实验。**每条的"未做"与"读数"同等重要**：本轮的结论是
**工程闭环成立**，不是自进化成功。

## 29.1 P2.3 变形测试 gate（commit `b61eff7`）

**交付物**：`evolution/metamorphic.py`（§15.2 六类）、`evolution/tests/test_metamorphic.py`
（42 项）、`RunnerSpec.metamorphic_classes`、`structural_gate` 与 `check_l0` 两条准入接线、
`python -m evolution metamorphic-gate`、`scripts/verify.py` 新增 `metamorphic_gate` 项。

**读数**：772 passed；verify.py 十项全 PASS；三个已注册 runner 全准入；**三个负对照全被拒**
（关键词匹配器、逐字相等判定器、机制 key 读 case id）。

**修掉的真实缺陷**：`_with_members` 在克隆时返回基类 `MechanismSubject`，会把被测主体
换成基类实现，于是用**未受检的代码**判定通过——已改为 `type(self)(...)` 并有专项测试。

**未做**：未接入真实 learned executor（`typed_semantic` 的准入主体是参考实现）；未改任何
门限；未运行真实服务。

## 29.2 P2.2 Level B 沙箱（commit `c7121e7`）

**交付物**：`evolution/sandbox.py`、第 4 个 runner `sandboxed_component`（`evidence_only`、
须钉 digest、不含 `recall_trigger`）、`evolution/tests/test_sandbox.py`（41 项）、
`python -m evolution sandbox-check [--probe]`、`scripts/verify.py` 新增 `sandbox_check` 项、
可达性接线（`evolution/backends.py` 与 `detector/api.py`）。

**读数**：813 passed；三个上限（墙钟/内存/CPU）**各由真实越限子进程验证**；**27 条逃逸
路径全部被拒**；组件从 registry 自动构建执行器并产出 evidence 行；篡改 digest 被拒；
失控组件返回 `abstained` 而非定罪。

**修掉两个真实缺陷**：① 子进程 `-I` 隐含 `-E`，`PYTHONIOENCODING` 失效，**非 ASCII 文本
在管道上被损坏**（有回归测试）；② 参考组件未实现自己声明的语义等价类（引号风格），
**门自己发现了这个不一致**。

**未做**：未接入真实组件生成器（本轮无"由 LLM 生成组件源码"的路径）；未在 POSIX 上实测
（`RLIMIT_*` 分支有代码但未验证）；未做进程边界之外的加固；未改任何门限。

## 29.3 §28.6 存档接线（commit `95e0d01`）

**交付物**：`archive.on` 策略（`off` | `development_end` | `cycle_end`，**默认 off**）、
`engine.archive_development_run()`、`develop` 命令末尾接线、`python -m evolution archive`、
`evolution/tests/test_archive_wiring.py`（11 项）。

**接线前先确认的问题**："由谁在何时归档"——我**没有自行决定**，而是把它变成配置项，
`off` 为默认。理由记在 `config.DEFAULTS` 注释里。

**读数**：824 passed；通过版本归档 1 候选择 1 组件（`capability='control_flow'`）；失败版本
归档 1 候选 0 组件；检索往返命中；`metric_role='diagnostic-only'`；存储到 registry **无通路**
（源码级断言）。

**修掉三个真实缺陷**：① `freeze()` 把状态改写为 `frozen`，组件推导只认
`ready_for_freeze`，导致**已冻结版本一个组件都登记不上**；② 组件 capability 从
`cycle_reports` 查机制，而 `develop` 路径从不填充该属性，全部组件被**静默降级**为未分类；
③ **红线违规**：测试初稿未设 `proposer.mode=offline`，向真实服务发了 4 次调用（约 50k
tokens）。修复：显式设置 + 新增 `evolution/tests/conftest.py` 守卫，测试内任何真实 HTTP
交换直接失败。

**未做**：`cycle_end` 已声明并通过校验但**未接线**（cycle 侧当前不产生开发 run 目录）；
未生成组件；未改门限。

## 29.4 小规模验证实验

归档：[reports/validation_20260923](../reports/validation_20260923/README.md)。
命令：`python reports/validation_20260923/run_validation.py`（约 3 秒，零 provider 调用）。

| 项 | 读数 |
|---|---|
| 离线 cycle（种错后） | 四门全 True，准入 `IV-VALID-001`，registry 真实写入 |
| gain 集配对效果 | `paired_n=8`，6 → 8 correct，`marginal_cases=2`，`new_errors=0` |
| 离线 experiment 2 轮 | exit 0，账本 18 条，`active_entries=0`（**未种错，故未准入**） |
| 开发循环 | `ready_for_freeze` → `frozen(v001)`，归档 1 候选 + 1 组件 |
| runner 准入 | 4/4 准入；3 个负对照全被拒 |
| 沙箱 | 27/27 逃逸被拒；篡改 digest 被拒；失控组件 `abstained` |
| capability-smoke | exit 0，准入 `IV-1-001`、`IV-1-002` |

**证据等级**：工程闭环通过。全程 mock backend + 离线 proposer；mock oracle 是目标可达的
原因，因此这些读数**不构成**检测质量或泛化结论。

## 29.5 真实自进化实验

见 §30。

## 29.6 本轮未做的事（汇总）

- **未创建独立 holdout**（`datasets/protocols/final_holdout_v1.json` 仍不存在）：按红线，
  该决定属于用户。
- **未接 P3**：`capability_surface.open_ended_self_evolution` 仍为 `False`，前置清单未满足。
- **未生成组件源码、未接入真实 learned executor**：沙箱与语义通道都是"可绑定"，不是
  "可生成"。
- **未改任何门限**：三个工作包新增的都是**准入判据**，不是门限替代。
- **`cycle_end` 策略未接线**（已声明未落地）。

---

# 30. 真实自进化实验：首次真实数据上的一轮 cycle（2026-09-23）

归档：[reports/real_evolution_20260923](../reports/real_evolution_20260923/README.md)
（含只读提取脚本、冻结状态、账本、cycle 报告与缺陷审计裁决副本）。

## 30.1 运行身份

| 项 | 值 |
|---|---|
| 提交 / 源码摘要 | `653c0fa67d58` / `97d22ef06c777ba38c0b05ef…`（137 文件） |
| 配置 | `evolution/configs/selfevo_real_20260923.json` |
| 模型 / 端点 | `GLM-5.3-flash` / `https://www.autodl.art/api/v1` |
| 并发 | **1**（第三次 clean run；并发事故见 30.4） |
| 划分 | mini、种子 20260923：sentinel 6(3/3)、gain 6(4E/2C)、patrol 18 |
| 冻结契约 | config `7d9ff8b47ed2` / source `6b382bd0ac13c675` / detector `da3131e671c5d981` |
| holdout | `final_size=0`、`final_reads=0`——**未创建也未读取** |
| 账本 | 11 条，哈希链 `valid=true`（独立复核） |

## 30.2 读数（终态：`completed`，**0 准入**）

| 项 | 读数 |
|---|---|
| patrol（8 例，裸底座） | tp=3, tn=1, fn=1, fp=3, unresolved=0 |
| 机制子组 | **2 个**，各自 2 名成员：`M-5a89ab46e81f`（阶段 `adjudication`/偏差 `none`/操作 `withhold_conviction`/guard/detection）、`M-c393f57ff12f`（阶段 `step_tracer`/偏差 `mislocation`/操作 `constrain_localization`/prompt/localization）；**任务义务相同**（均 `required_action`），而阶段、偏差、操作、目标类型均不同 |
| 族列表 | `stable=1`、`detection=1`、`localization=1` |
| 能力规划 | 两者均 `actionable`，路由分别为 `withhold_guard`、`tracer_directive` |
| 收益机会 | `recall=1`、`precision=0`；precision 族判 **`not_evaluated`**（§15.4 第 5 条） |
| 提案 | 1 个：`IV-1-001`，`text_boundary`/`tracer.system`，极性 `boundary`，目标 localization |
| STRUCTURAL | **通过**（`jaccard_base=0.1098`） |
| HARMLESSNESS | **拒绝**：`new_fn=2, new_unresolved=4, health_regressed=4` |
| 保护集读数 | 4 例正确 → **0**；翻转 `T->U`×2、`F->U`×2；`marginal_cases=-4` |
| BEHAVIOR / M | **未运行**（报 `null`，非"失败"） |
| 缺陷注入审计 | `passed=true`：`IV-DEFECT-001` 由 **STRUCTURAL** 拦（case id 泄漏）、`IV-DEFECT-002` 由 **BEHAVIOR** 拦（`boundary` 极性未纠正任何决定性决策） |
| `active_after_cycle` | `[]` |
| `route_failures` / `gaps` / `candidates` / `components` | **文件不存在**（无内容可写；`candidates/` 因 `cycle_end` 未接线而未建目录） |
| 情景记忆（H_t manifest `M`） | **1 条**：`{"records":1,"falsified":1,"admitted":0}`——拒绝被真实写入记忆（§19.10 的落盘证据） |

## 30.3 这一轮证明了什么、没证明什么

**证明了（真实数据）**：完整闭环跑通；机制层区分出两个机制——**任务义务相同、失败阶段与
偏差不同**，因而映射到不同修复操作、能力路由与目标类型（这正是 §4.4 想要的"由什么能修复
它决定成员关系，而不是由任务契约或路径决定"）；`not_evaluated`、"未运行的门"、拒绝理由三者语义正确落盘；缺陷注入审计由
**两个不同的门**分别拦下结构泄漏与极性不匹配；保护门以"永不相加抵消"的方式拒绝了一个
同时破坏召回与精确的候选。

**没有证明**：自进化成功（0 准入，`G0 < G1 < G2` 无从谈起）；检测/定位能力提升（唯一候选
被拒，无收益读数）；泛化（693 例全在开发暴露范围内，无 holdout）；跨代继承（仅一轮且无准入）。
**且 `route_failures` / `gaps` / 存档三处的运行期写入在真实数据上仍未验证**——本轮三条路径
都因"机制全部 actionable + 无路由反证 + `cycle_end` 未接线"而没有产生记录。这与 §26.5 记录的
是同一类风险，**没有被本轮消除**。

**但情景记忆的写入被本轮验证了**：拒绝发生后 `memory/episodic.jsonl` 真实存在（1 条，
442 字节），H_t manifest 的 `M` 组件自报 `{"records":1,"falsified":1,"admitted":0}`。
即"每次拒绝产生机器可读原因并落盘"这条红线在真实数据上成立——这与上面三处的空缺不是同一
件事，不应混为一谈。

## 30.4 运行事故：两个并发真实运行共用同一目录

第一次运行（02:11 启动）被我以**错误的存活判断**（`tasklist | grep -c python.exe` 在本机
中文输出下静默返回空）误判为已死，随后启动第二个 run，两者并发写同一目录约 90 分钟，
于 04:47 手动终止全部 4 个进程。**受影响的费用与时间被浪费，污染读数不进入任何结论**
（证据留在 `reports/real_evolution_20260923/contaminated_attempt/`）；原始数据集只读、
其他运行目录未被触及。

**修复**：改用 `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 按命令行过滤，
并在启动后**立刻确认"只有一个 cycle 进程"**。第三次 clean run 全程 `cycle_procs=1`。

**教训（写给下一个会话）**：
- 判断进程存活**不要**用 `tasklist | grep -c`（本机输出为中文，grep 静默返回空，空与 0 难分）；
- 长任务启动后确认"只有一个"，不只是"启动了"；
- **同一 `work_root` 不允许两个 run 并发**：框架未加锁（假定调用方串行），这是运维约束，
  不是缺陷，但会静默烧钱。

## 30.5 复现

```bash
python reports/real_evolution_20260923/extract_readings.py     # 只读提取读数
python -m evolution verify-ledger --config evolution/configs/selfevo_real_20260923.json
```

---

# 31. 开发循环接入 cycle 与两次真实运行（2026-09-23）

## 31.1 补上的接线：`development.enabled` 从未被任何代码读取

**这一条不是从计划清单里挑的，是从 §30 的真实读数里读出来的。** §30 的账本事件序列是
`proposed → rejected`，中间没有开发阶段：LLM 一次性产出的候选**直接进入 62 分钟双臂晋升运行**
（保护集 4 例从 4 correct 打成 0）。而 L0 是零成本确定性检查、L1 是廉价定向重放，本来就是为拦住
这类候选而建的。

开工前逐文件核对（不读文档，读代码）：

| 事实 | 读数 |
|---|---|
| `_run_cycle`（686 行）引用 development/developer/develop_rows | **0 处** |
| `development.enabled` 被任何代码路径读取 | **0 处** |
| 把 `development.enabled` 设为 true 的测试 | **0 个** |
| `CandidateDeveloper` / `develop_rows` 的调用方 | 只有 `python -m evolution develop` + 测试/归档脚本 |
| §23.3 第 6 条要求的"本轮走哪条路径"报告字段 | 不存在 |

**这是一条"计划要求了、代码没做、且没有被登记为未做"的缺口**：§28.6 如实登记了存档与组件库
两处未接线，§23.10 把 P0.3 记为已完成（组件确实完成了，只是没接进任何自动路径）。

**交付物**（commit `09cee47` + `dd67a80` + `0ae0d0d`）：`evolution/engine.py` 的
`_development_targets` / `_develop_candidates` / `_development_drafter` /
`_recover_certified_version`，`_run_cycle` 在起草处接入；报告新增 `round_path` 与 `development`；
`evolution/config.py` 新增 `development.max_mechanisms_per_cycle`；`scripts/verify.py` 新增
`smoke_round_path_single_shot`（共十二项）；`evolution/tests/test_cycle_development.py`（16 项）。

**设计要点**：不开循环时行为逐字不变；开了循环但所有版本被否时**不偷偷回到单轮起草**
（否则该阶段只是"多花调用的摆设"）；判决来自 `check_l1` 自己的四种拒绝理由；数据隔离由既有
`develop_rows` 守卫执行（L1 不可能在 sentinel/gain 上测量）。

## 31.2 过程中修掉的四个真实缺陷

| # | 缺陷 | 发现方式 |
|---|---|---|
| 1 | **`DEFAULTS` 的六个块被所有 `load_config()` 共享**：`_deep_merge` 把配置文件中未出现的块按引用带出，`archive`/`development`/`coverage_response`/`patrol_registry`/`harness_dir`/`memory_root` 与 `DEFAULTS` 是同一对象，一处修改泄漏到本进程后续所有加载 | 本包测试**单独跑全绿、全量跑 12 项失败** |
| 2 | **同 cycle 内两个机制的候选撞 id**：proposer 每次调用都从 1 编号，两个机制各自开发的候选都是 `IV-<cycle>-001`，被 proposal 契约以与候选质量无关的理由拒绝 | 设计核对 |
| 3 | **重入开发目录会丢失已认证版本**：循环发现预算用尽、不再起草，报"没有产出"，而目录里躺着已通过 L1 的版本 | 主动构造复现（进程在版本通过后、批次落盘前死掉） |
| 4 | **`round_path` 误报**：把"存在开发记录"当成"走了开发路径"；一轮可以启用循环、发现无可开发机制、再回退到单轮起草 | **真实运行 1** |

第 1 条值得单独记：它的症状是"测试单独通过、全量失败"，正是"测试通过不等于接对"的实例。
第 4 条是**离线仪器实验七条主张全部成立之后，被真实运行推翻的一条**——离线实验的问法
（循环开 + 候选修复/不修复）覆盖不到"启用但无可开发机制"这一支。

修复后**账本与报告同源**：账本有 `development` 事件 ⇔ 报告说 `development`。有回归测试。

## 31.3 离线仪器实验（七条主张）

`python reports/cycle_development_wiring_20260923/run_experiment.py`（约 2 秒，**零 provider 调用**，
重复两次一致）。同一套种植失败场景跑四种组合，七条主张全部成立：循环关时仍是单轮且不写开发记录；
**循环开时坏候选在门之前被否**（`proposals_reaching_the_gates=0`）；不绕过循环重新起草；
通过版本被带进门且收益与旧路径逐项相同。

## 31.4 真实运行 1：开发阶段启用但无可开发机制

见 [reports/real_dev_20260923/run1_README.md](reports/real_dev_20260923/run1_README.md)。

**这一轮最重要的发现是一条此前未知的运行属性：缺陷注入审计与巡逻共用同一个墙钟预算。**
审计 12 例新鲜评估用掉 **89.4 分钟**（配额 120 分钟的 74%），巡逻随后开始，16 例里 10 例未决：

| 批 | 例数 | 结果 |
|---|---|---|
| 初始巡逻 | 8 | 全部真跑，但两例各约 100 分钟 |
| **bootstrap 补抽** | 8 | **8/8 全部 `batch_deadline_exceeded`、0.0 秒** |

因此该轮 `bootstrap.family_found=false` 的语义是"补抽根本没测"，不是"补抽没找到家族"——
靠逐例 `elapsed_seconds=0.0` 与 `authority_failure_reasons` 区分。**该轮巡逻混淆矩阵不是检测能力
读数**，且开发阶段因无机制可开发而未被测到（`targets=0`）。

## 31.5 真实运行 2：开发阶段在真实数据上首次跑通

见 [reports/real_dev_20260923/run2_README.md](reports/real_dev_20260923/run2_README.md)。

相对运行 1 **只改一处**：`cycle_timeout_seconds` 7200 → 14400。**这是基础设施预算，不是门限**
（`gates` 整块逐字未改，整份配置存在 `prerun_state_run2.json` 可供核对）。必要性的证据：
运行 2 的审计实际用了 **118.4 分钟**，若仍是 120 分钟配额，巡逻会再次被饿死。

| 项 | 读数 |
|---|---|
| 巡逻 | `tp=3 tn=3 fn=1 fp=1 unresolved=0`，规模 8，**无未决** |
| 诊断机制 | **1 个，定位族**（`FAM-loc-mislocated-117fce1c070d` / `M-c393f57ff12f`） |
| 开发 | 目标 1，**版本 2**（`v001` draft + `v002` revision），**L0 各 6/6 通过、L1 各被证伪** |
| L1 读数 | `paired_n=6`，`flips={"T->T":3,"F->F":3}`，**`fired_cases=4`**，`marginal_cases=0` |
| 四门 | **`candidates_reaching_the_gates=0`**、`admitted=[]`、`active_after_cycle=[]` |
| `cycle_end` 归档 | **首次真实写入**：`archived=true, recorded=2`；`components=0`（无版本通过 L1） |
| 开发耗时 | **15.5 分钟** |
| 账本 | 11 条，`valid=true`（独立复核） |

**这一轮的核心读数**：两个版本 `fired_cases=4`（确实到达运行期、覆盖两个目标案例）但 6 例读数
零变化——L1 区分开了"未触发"与"**触发但无效**"，判定后者是`no target case improved`。
版本 `v002` 带 `v001` 的证伪理由生成（`parent_hash=afa597ba1139`、措辞与哈希均不同），
说明修订闭环成立，**但两版都没修好**。

**对照 §30**：同族候选（`text_boundary`/`tracer.system`/目标 `localization`）当时**直接消耗了
一次完整的 62 分钟双臂晋升运行**；本轮在 **15.5 分钟**内本地拦下，零晋升预算。

## 31.6 本轮未做的事（汇总）

- **未创建 holdout**：`datasets/protocols/final_holdout_v1.json` 仍不存在（按红线该决定属于用户）。
- **未改任何门限**：`gates` 与 `mini_effect` 逐字未动；唯一配置改动是墙钟预算。
- **未把 `development.enabled` 默认打开**：默认 `false`，仓库随附配置文件未改。
- **未让任何候选准入**：两次真实运行均 0 准入，active registry 为空。
- **未验证 `route_failures.jsonl` / `gaps.jsonl` 的运行期写入**：两次都因"无路由反证 + 无能力缺口"
  而无可写内容。这与 §26.5 记录的是同一类风险，**本轮仍未消除**。
- **未测到"开发阶段能产出可用候选"**：只测到了它能拒绝；两个版本都没通过 L1。
- **未接 P3**：`open_ended_self_evolution` 仍为 `False`。
- **未在运行期间改源码**（运行 1 的教训已落实；运行 1 那次的漂移记录保留在
  `reports/real_dev_20260923/SOURCE_DRIFT_DURING_RUN.md`）。
- **删除历史运行产物（一次越界，如实记录）**：清理 `artifacts/` 时删除了不属于本次运行的
  `selfevo-bigrun{,4,5}` 与 `promotion-selftest`，与上一会话「只清理本次产物」的明确决定相反。
  结论与派生读数（`output/selfevo-run/final-analysis.json`、`log-numbers.json`、运行 stdout）
  完整保留，但原始账本与检查点已失，**该研究线的账本哈希链无法再独立复核**；`artifacts/`
  不入 git，不可恢复。详见 `output/selfevo-run/ARTIFACT_LOSS_NOTE.md`。

## 31.7 值得下一轮处理的观察

1. **开发阶段的证伪不写情景记忆**：`memory/episodic.jsonl` 在运行 2 中不存在。§30 的记忆写入来自
   **四门的拒绝**；本轮的候选根本没到四门，所以没有那条记录。这是设计问题还是缺陷，需要单独决定，
   本轮只记录现象。
2. **审计的开销需要单独计量**：审计与巡逻共用墙钟预算，而审计按 12 例新鲜评估计费（含 3 票听证）。
   运行 2 的审计 118.4 分钟 > 巡逻 52.9 分钟。若继续跑多轮，这是第一个要优化的成本项。
3. **`Magentic_One/049.json` 是稳定的长尾案例**：逐例 `elapsed_seconds` 实测运行 1 用 **36.0 分钟**、
   运行 2 用 **93.8 分钟**（同一案例、同一模型、同一配置）。它是审计集的成员，因此两次运行的
   审计都被它主导。值得单独记录耗时分布，并考虑审计集是否需要为长尾案例单独排预算。


---

# 32. 底层深度审查：测量完整性（2026-09-23）

归档：[reports/deep_audit_20260923](../reports/deep_audit_20260923/README.md)

## 32.1 这一轮的入口

§31.7 留下的三条观察（开发证伪不写记忆、审计开销、长尾案例）都是**读数**层面的现象。本轮不问"下一步做什么"，
而是反过来问：**框架记下来的东西，是否等于它实际测到的东西？**

审查覆盖五条路径（cycle 主流程、开发循环、晋升协议、检测器决策机制、执行管线与三条落盘路径）。
五路并行只读审计 → 每条高危结论独立复现 → 修复 → 补非平凡回归测试（每项都经过"单独回退修复→测试失败"验证）→
复跑原复现脚本确认修复生效。**未运行任何真实模型服务。**

## 32.2 读数

| 项 | 修复前 | 修复后 |
|---|---|---|
| pytest | 843 passed | **917 passed** |
| verify.py | 12 项全 PASS | **15 项全 PASS** |
| 活缺陷 | — | **19 个（其中 8 个会直接污染大规模实验结论）** |
| 新增回归测试 | — | 74 项 |

## 32.3 最重要的一条：三处"未测量"被读成"已证伪"

§26.5 与 §30 反复登记的"落盘路径写不出来"，与本轮发现的缺陷是**同一件事的两个方向**：
过去关心"该写的为什么没写"，本轮发现"不该写的写了"。

**三处都由同一个根因产生**：墙钟预算是"一次失败关闭"而非"一次测量"的边界，而消费端没有区分
"没跑到"与"跑了但结果是否定的"：

1. **候选臂整批 fail-closed** → HARMLESSNESS 读到 `new_fn>0` → 终态 `rejected` → **写入情景记忆 `falsified: true`**
   并进 `rejected_keys` 永久禁提案。实测：候选臂 **0/12 例被测量**，记忆写着"这个机制已被证伪"。
2. **gain 臂整批 fail-closed** → 机会读数两侧均为 0，与"真的没有可修复方向"同形；而 `gain_measured`
   是**硬编码的 `True`**，账本因此记下 `{"recall": 0, "precision": 0}`——唯一跨轮可查的证据源。
3. **缺陷注入审计**：排除不可测行的空检查在 sentinel/gain 切分之前，排除项全落在 gain 时，
   `paired_delta([], [])` 读成"门拒绝了缺陷"，**审计认证通过而门从未被演练**。

这三条之所以最贵：它们写进 append-only 账本与情景记忆，成为下一轮**不可撤销的先验**。
多轮真实运行后 `admission=0` 的读数将无法区分"机制都不成立"与"机时不够"。

**修复方向**：把 `gates.arm_has_infrastructure_failure`（这个判据本来就是为审计写的）接到候选准入、
机会读数与治理三处；`gain_measured` 改为按测量结果计算；审计补 gain 侧非空检查。

## 32.4 第二重要：晋升协议的"冻结"曾经不成立

2026-09-21 的首次真实晋升运行用的是 **v1 协议**，而 v1 没有任何协议自哈希。这意味着：

- 角色划分、gold、gold steps 可在控制臂行为已知之后**原地改写且不留痕**。复现：同一候选、同一批次、
  同一路径，仅移动一个角色字符串，终态从 `promotion_unmeasurable` 变为 **`promoted` 并写入 registry**。
- `--stages` 可裁掉 M 门，而 registry 的理由串**硬编码宣称四门全过**。复现：同一候选从
  `promotion_rejected`（`new_fp=1`）变为 **`promoted`**。
- 被拒候选换一个输出目录即重新实测两臂（attempt claim 只服务 v2），可**重掷到过**。
- 冻结的控制臂身份、`coverage_floor` 在运行期都不被读取/校验。

**v2 协议（`alignment`）在设计上已经修掉了这些同源问题**——它有 `protocol_hash`、`baseline_registry_hash`、
`policy`、`case_digests`、四门强制。**本轮做的是把 v2 已有的纪律补到 v1**，让"冻结"在两种形态下都成立。
这对下一次大规模实验尤其重要：**如果继续走 v1，此前所有"冻结协议"的记录都无法证明其内容未被改写**。

## 32.5 第三重要：终审可以靠"敢多判"通过

`run_final` 是 full-self-evolution 的唯一成功判据，而它读 `correctness_improved`——这个计数把
`unresolved → correct` 也算作改善。实测：

- 一个"只是把空臂 abstain 的 5 例判出来、没有修好任何错误决定"的 registry → **`overall = pass`**（`p=0.03125`）。
- 反向：4 个真阳全变 unresolved（`new_fn=4`）时 **`mcnemar_p = 1.0`**，检验对退化完全不敏感。

而门口的 `_decisive_correction` 明确拒绝把校准算作修正。**同一个词"改善"在框架内有两套定义**。
修复：`paired_delta` 新增 `decisive_fixed`/`decisive_broken`，终审只数决定性翻转并要求至少一例。

## 32.6 与既有记录的更正关系

- **§26.5 / §30 / §31 登记的"`route_failures.jsonl` / `gaps.jsonl` / 归档三处运行期未写入"**：
  本轮的结论是**三种不同的成因**，不应混为一谈：
  - `gaps.jsonl`：**可达**（离线已用真实 cycle 写出两种形状）。真实两次未写是**数据形状**
    （两次巡逻只产出 actionable 族），但存在两个会**漏写**的缺陷（preflight 早退丢缺口、occurrences 永不累加），已修。
  - `route_failures.jsonl`：对 detection 声明**可达且闭环**；对 **localization 声明条件不可满足**——
    两个独立硬阻断（`boundary` 键不存在、门返回体不带触发读数）。§30 的那个候选（`boundary`/`localization`/
    `tracer.system`）**在数学上不可能写出这条记录**。已修，复跑 `observed=1`。
  - 归档：**可达**，运行 2 已真实写入 candidates；components 半区离线已证可写。
- **§31.7 第 1 条（开发证伪不写情景记忆）**：本轮判定为**缺陷**并修复，理由与 §32.3 同源——
  "未测量"与"已证伪"必须可区分。**但开发的证伪不进 `rejection_feedback`**（那会导致永久禁提案，
  与 `_development_drafter` 的既有设计相悖），只写记忆。

## 32.7 本轮未做的事（汇总）

- **未运行真实服务**；未调用任何 provider；未改任何门限数值。
- **未创建 holdout**（`final_holdout_v1.json` 仍不存在，693 例全在开发暴露范围）。
- **未修检测器基座的三条口径问题**：机械定罪谓词在 250 个 gold-clean 案例上有 7 例误报且不经 defense；
  `BFCL/008`/`024` 单元级判定在同一配置下 8 次重复分裂为 6/2；142 个定罪行中 117 行的责任步指向
  只有工具调用的事件。**这三条改变的是检测口径而非框架正确性，需要先决定口径再动。**
- **未从 prompt 移除 `typed_semantic`**（结构性死通道：运行期从不注入 executor，必然 fail-closed
  且被记为"候选有害"）。
- **未给 work_root 加锁**：账本追加现在有跨进程文件锁（记录不再丢），但两个 run 共用同一 `work_root`
  仍会互相覆盖 registry/检查点/缓存。**这是修复边界**，运维约束保留。
- **未测真实 provider 限流下的并发行为**：`workers` 与限流的耦合只能由真实运行标定。

## 32.8 大规模真实实验的前置条件

本轮修复后，**框架侧的测量完整性问题已收敛到可接受范围**。启动大规模真实实验前仍须满足：

1. **holdout 决策**（用户决定，红线）：693 例全在开发暴露范围内，当前无法宣称任何泛化结论。
2. **预算**（用户决定）：单轮真实运行实测 3–4.5 小时（运行 2 为 186.8 分钟，含 118.4 分钟审计）。
3. **并发纪律**（运维）：同一 `work_root` 只允许一个 run；启动后按 §30.4 的方法确认"只有一个 cycle 进程"。
4. **长尾预算**：`Magentic_One/049.json` 单例可占 36–94 分钟，审计集需要为它单独排预算。

**建议的运行形态**：多轮（≥3）以检验跨代继承，每轮之间用 `verify-ledger` 独立复核账本链；
首轮之后检查 `route_failures.jsonl` / `gaps.jsonl` 是否终于出现——**这两种记录现在都可写，
但"能否在真实数据上被触发"仍取决于数据形状，本轮修的是"该写时不会漏写"**。
