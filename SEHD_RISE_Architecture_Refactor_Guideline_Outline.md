# SEHD-RISE 框架重构指导提纲

## Repairability-driven Integrated Self-Evolution
### 以可修复性为核心的一体化多层自进化重构

> **项目名称：SEHD-RISE**
>
> **全称：Repairability-driven Integrated Self-Evolution**
>
> **中文名称：以可修复性为核心的一体化多层自进化重构**
>
> **定位：** 对 SEHD-v6 的自进化控制平面进行架构级重构，在保留 Proof-Court Detection、L1/L2/Meta Skill、HTC-TES 和机制级经验迁移等核心 novelty 的前提下，建立以 Repairability、Causal RepairPlan 和显式因果信用为中心的统一闭环。
>
> **文档性质：** 本文档仅作为架构重构的指导提纲，定义问题、建议方向和目标形态；不包含具体代码修改方案、函数级实施步骤、迁移顺序或补丁设计。

---

# 0. SEHD-RISE 的核心目标

SEHD-RISE 不是对 SEHD-v6 的“简化版重写”，也不是减少研究机制。

本次重构的目标是：

> **保留检测层和多层 Skill 的研究复杂度，同时显著降低 Self-Evolution Control Plane 的重复权威、状态复制和语义耦合。**

核心目标包括：

1. 保留 SEHD 的 Proof-Court hallucination detection 体系。
2. 保留 L1 Skill、L2 Skill、Meta Skill 三层自进化结构。
3. 保留 HTC-TES 的 hallucination-type-aware learning。
4. 保留机制级经验迁移与跨案例 Skill generalization。
5. 将 Repairability 变成是否进入自进化的核心判断。
6. 将 failure attribution、repair ownership 和 executable repair surface 收敛到统一的 causal representation。
7. 将“框架无法修复”与“模型没有找到有效 Skill”严格区分。
8. 建立能够解释每一次 Skill 成败的完整 causal credit chain。
9. 为后续小规模逐案例实验、ablation、transfer evaluation 和 SOTA 验证提供清晰的实验基础。

---

# 1. 当前框架存在的主要问题

## 1.1 Evolution Trigger 与 Repairability 分裂

当前框架中：

```text
是否需要进化
```

与：

```text
是否存在合法、可执行、可观测的修复路径
```

并不是同一个机制决定。

当前可能出现：

```text
AttributionSignal.should_evolve = True
        ↓
Failure Classifier
        ↓
FailureEpisode
        ↓
Repair Frontier
        ↓
UnaddressableFailure
```

### 核心问题

框架可能先认为：

> “这是一个需要学习的问题。”

之后又认为：

> “当前 Skill 系统没有办法表示或执行这个修复。”

说明 Evolution Trigger 与 Repairability Authority 不统一。

### 主要后果

- 产生大量 triggered-but-unaddressable cases。
- `skip_unaddressable_failure` 成为正常业务路径。
- 难以判断失败来自模型还是框架。
- 污染 no-edit / convergence 统计。

---

## 1.2 `UnaddressableFailure` 的语义层级错误

UnaddressableFailure 表示的是：

> 一个已经被判定为需要进化的 failure，无法被现有 Skill / Action / Repair vocabulary 编译成合法修复。

这属于：

```text
framework representational / ownership / repairability failure
```

而不是：

```text
learning search exhausted
```

### 当前风险

如果 Unaddressable 被作为普通 no-edit：

```text
框架不会修
```

可能被解释为：

```text
已经收敛，没有值得学习的内容
```

这是状态机语义上的错误。

---

## 1.3 根因判断存在多个 competing authorities

当前框架中，对同一个 failure 进行解释和路由的机制包括：

- Step Attribution
- Failure Classifier
- Gold polarity logic
- FailureEpisode
- Repair Frontier
- HTC-TES Type Classifier
- Type-conditioned routing
- L1 planner
- L2 planner
- Meta guidance

这些机制都可能重新解释：

- failure type
- owner
- target module
- repair layer
- evolution direction

### 主要问题

系统没有形成严格的：

```text
one causal fact → one authority
```

而更接近：

```text
one causal fact
→ multiple projections
→ multiple reinterpretations
→ multiple route decisions
```

### 风险

- owner 被覆盖
- route 被重写
- vocabulary 不匹配
- empty intersection
- silent skip
- dead path
- production chain 不可达

---

## 1.4 Detector Fault 与 Skill Fault 的边界仍可能被打穿

框架已经开始区分：

- provider semantic failure
- validator fault
- transport / parse fault
- route / config fault

这是正确方向。

但架构层仍存在一种危险：

```text
Detector 自身协议 / contract / instrumentation 缺陷
        ↓
被进化系统当成 Skill deficiency
        ↓
Genome 学会补偿 detector bug
```

### 风险

长期 Skill 可能积累：

> 为当前框架缺陷服务的补偿规则

而不是：

> 可跨案例迁移的 hallucination detection experience

---

## 1.5 Failure Attribution 尚未完全等价于 Terminal Causal Cut

当前已经有 terminal basis、blocking facts、repair frontier 等结构。

但仍需要警惕：

```text
first malformed artifact
```

被等价为：

```text
true causal blocker
```

### 核心问题

异常 artifact 只能证明：

> “这里有问题。”

不能自动证明：

> “这里就是导致当前错误 terminal state 的最小因果切面。”

### 风险

```text
wrong artifact
→ wrong owner
→ wrong RepairPlan
→ no effect
→ reject / skip
```

---

## 1.6 HTC-TES 同时承担类型理解和关键路由

HTC-TES 对以下能力有明显价值：

- hallucination taxonomy
- experience organization
- transfer retrieval
- benchmark analysis
- type-conditioned guidance

但如果同时决定 runtime target modules，则会形成第二套 routing truth。

### 风险

当 type classification 与 causal attribution 不一致时：

```text
真实 causal owner
        ↓
被 type routing 再次过滤或重排
```

导致不必要的 dead-end。

---

## 1.7 L1 与 L2 的编排逻辑重复度较高

L1 和 L2 都分别具有：

- failure handling
- candidate planning
- generation / selection
- execution
- gate
- replay
- effect assessment
- memory
- promotion

### 核心问题

L1/L2 的研究对象可以不同，但不应该拥有两套完全独立的 self-evolution orchestration semantics。

### 风险

- registry 不同步
- effect definition 不一致
- route vocabulary 不一致
- replay semantics 不一致
- audit 不一致
- integration bug 增多

---

## 1.8 Meta Skill 的系统复杂度大于当前直接纠错能力

Meta Skill 当前主要调节：

- guidance
- ranking
- search preference
- gate profile

本质上属于：

```text
search / learning strategy optimization
```

而不是：

```text
root-cause correction authority
```

### 问题

当基础闭环仍有：

- Unaddressable
- wrong owner
- executor failure
- effect mismatch
- route mismatch

时，Meta 无法从根本上解决这些问题。

但 Meta 本身又引入：

- 独立 state
- evidence accumulation
- commit
- rollback
- persistence
- prompt influence

增加控制平面复杂度。

---

## 1.9 `skip` 状态掩盖了真实故障漏斗

当前类似 `skip_*` 的结果可能覆盖很多不同原因：

- failure 不可寻址
- planner 无候选
- route 不存在
- budget exhausted
- provider failure
- parser failure
- proposal invalid
- runtime precondition 不满足

### 问题

这些结果对架构优化的含义完全不同。

将其压缩为 skip 会使：

```text
实验结果
```

难以反推：

```text
真正的 framework bottleneck
```

---

## 1.10 Convergence 可能混入非学习事件

真正能够作为 convergence evidence 的事件应该是：

> 框架拥有明确 RepairPlan，也成功完成了有效搜索和执行，但连续无法获得可接受改善。

不应该包括：

- unaddressable
- provider unavailable
- parser failure
- budget exhausted
- executor failure
- framework routing failure

否则 convergence 反映的是：

```text
system availability
```

而不是：

```text
learning saturation
```

---

## 1.11 同一语义在多个结构中重复表达

同一个 failure 可能依次被表示为：

```text
Detector Result
→ AttributionSignal
→ FailureAttribution
→ FailureEpisode
→ Repair Frontier
→ Runtime Observation
→ ActionSpec
→ GateEvent
→ Experience
→ Meta Review
```

### 风险

状态投影越多：

- 信息越容易丢失
- vocabulary 越容易漂移
- schema 越容易失配
- source of truth 越难确认

---

## 1.12 当前主要风险已经从单模块错误转向系统集成错误

近期框架历史问题存在明显共同模式：

```text
模块 A 单独正确
模块 B 单独正确
模块 C 单独正确

但：

A → B → C

生产路径不可达或语义不一致
```

### 典型系统性风险

- mechanism 已实现但没有真正进入 production path
- state 被写入但下游没有消费
- registry 已进化但 executor 没有使用
- feedback 已生成但 Meta 看不到真实原因
- edit 执行了但 causal credit 使用错误 observable
- provider 重采样造成伪 execution / 伪 effect

这说明当前最重要的问题是：

> **Semantic Integration Complexity**

---

# 2. 对应问题的建议解决方向

> 本节只定义架构方向，不指导具体实施。

---

## 2.1 建立单一 Repairability Authority

未来建议不再使用：

```text
should_evolve
```

作为独立的最终触发权威。

目标关系应是：

```text
Detection Defect
        ↓
Causal Repairability Analysis
        ↓
┌────────────────┬──────────────────┐
│                │                  │
Non-Evolvable   Repairable      Framework Defect
                   │
                   ▼
               Evolution
```

只有 Repairable failure 才进入 Skill evolution。

---

## 2.2 将 Unaddressable 定义为 Framework Invariant Violation

目标语义：

```text
如果系统已经宣布某 failure 属于 evolvable surface，
那么就必须存在合法 RepairPlan。
```

否则属于：

```text
UNADDRESSABLE_FRAMEWORK_DEFECT
```

不属于：

```text
NO_EDIT
```

---

## 2.3 建立统一的 Causal Failure Object

建议所有下游模块围绕一个统一的 causal representation 工作。

该 representation 在概念上至少包含：

- terminal state
- terminal basis
- blocking predicate
- causal failure edge
- failure owner
- Skill layer
- repairable surface
- legal repair operations
- observable effect
- repairability status

下游不再重新发明 failure semantics。

---

## 2.4 将根因定位改为 Terminal Causal Cut

目标不是：

```text
find bad artifact
```

而是：

```text
find minimal repairable cause preventing correct terminal state
```

建议将：

- malformed artifact
- missing artifact
- quarantine
- coverage anomaly
- appeal anomaly

作为构造 causal cut 的证据。

而不是直接当作最终 owner。

---

## 2.5 建立严格的 Detector / Evolution Ownership Boundary

建议明确：

### Detector Boundary

负责：

- schema
- validator
- parser
- transport
- route / configuration
- deterministic hard contracts
- inference repair

### Evolution Boundary

只负责：

- provider semantic behavior
- evidence generation behavior
- verification behavior
- causal/localization behavior
- adjudication policy behavior

---

## 2.6 HTC-TES 改为 Advisory Type Intelligence

HTC-TES 保留，但主要用于：

- experience indexing
- retrieval
- mechanism clustering
- type-specific guidance
- evaluation slicing
- transfer analysis

不再拥有最终 causal owner 的否决权。

---

## 2.7 L1 / L2 共享统一 RepairPlan Protocol

建议保留两层 Skill，但使用统一高层抽象：

```text
RepairPlan
├── failure identity
├── layer
├── owner
├── target causal edge
├── executor
├── legal operations
├── effect contract
└── evaluation contract
```

### L1

```text
Executor = Provider Behavior / Instruction Executor
```

### L2

```text
Executor = Adjudication Policy Executor
```

这样保留多层 novelty，同时减少重复 orchestration。

---

## 2.8 Meta Skill 重新定义为 Cross-Case Strategy Learner

Meta Skill 的目标应明确为：

```text
从多次已验证的 RepairPlan outcome 中
学习未来搜索与选择策略
```

Meta 负责：

- search prior
- operation preference
- mechanism ranking
- retry strategy
- candidate diversity strategy

Meta 不负责：

- 当前 failure owner
- 当前 causal cut
- repairability
- correctness authority
- hard gate relaxation

---

## 2.9 建立真正的 Framework Failure Funnel

建议未来 outcome 不再以 skip 为核心分类。

应按照故障发生的位置进行区分。

### Framework Layer

```text
NON_EVOLVABLE
UNADDRESSABLE
ROUTING_FAILURE
SCHEMA_FAILURE
INFRA_FAILURE
BUDGET_EXHAUSTED
```

### Search Layer

```text
NO_HYPOTHESIS
INVALID_PROPOSAL
DUPLICATE_PROPOSAL
```

### Execution Layer

```text
ACTION_NOT_DISPATCHED
PROVIDER_NOT_COMPLIED
TARGET_EDGE_UNCHANGED
BLOCKER_NOT_CLEARED
```

### Fitness Layer

```text
SOURCE_NOT_IMPROVED
CANARY_REGRESSION
TRANSFER_REGRESSION
ACCEPTED
```

---

## 2.10 重构 Convergence Definition

Convergence 应只消费：

```text
complete repair attempts
```

即：

```text
RepairPlan valid
→ proposal valid
→ action executed
→ causal effect measured
→ no acceptable improvement
```

其他系统错误不能成为 convergence evidence。

---

## 2.11 减少状态复制，明确 Source of Truth

建议核心事实的 authority 关系为：

```text
Detection Truth      → Detector Result / Terminal Basis
Failure Truth        → Causal Repair Episode
Repair Truth         → RepairPlan
Execution Truth      → ExecutionTrace
Effect Truth         → CausalEffectRecord
Fitness Truth        → EvaluationRecord
Experience Truth     → Mechanism Experience Memory
Meta Truth           → Meta Skill State
```

其他结构只允许作为 projection。

---

## 2.12 强制保留完整 Causal Credit Chain

未来一个 Skill 从提出到接受应能够完整解释：

```text
Hypothesis
→ RepairPlan selected
→ Action dispatched
→ Executor/provider complied
→ Target causal edge changed
→ Blocking predicate cleared
→ Terminal behavior changed
→ Task score improved
→ Canary/transfer preserved
→ Skill committed
```

任何中间层都不能替代下一层的证据。

---

# 3. SEHD-RISE 重构后的目标框架

## 3.1 总体框架

```text
                     ┌───────────────────────┐
                     │      Trajectory       │
                     └──────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │  PACA-TCL Proof Court │
                    │                        │
                    │ Evidence Construction  │
                    │ Prosecution / Defense │
                    │ Consensus / Appeal    │
                    │ Localization          │
                    │ Terminal Basis        │
                    └───────────┬────────────┘
                                │
                                ▼
                 ┌─────────────────────────────────┐
                 │ SEHD-RISE Causal Repair Layer  │
                 │                                 │
                 │ Terminal Causal Cut             │
                 │ Failure Ownership               │
                 │ Repairability                   │
                 │ Canonical RepairPlan            │
                 └──────────────┬──────────────────┘
                                │
                     ┌──────────┴──────────┐
                     │                     │
                     ▼                     ▼
              NON-EVOLVABLE         REPAIRABLE FAILURE
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │ Multi-Layer Skill   │
                                │ Selection           │
                                └──────────┬──────────┘
                                           │
                   ┌───────────────────────┼────────────────────────┐
                   │                       │                        │
                   ▼                       ▼                        ▼
            ┌─────────────┐         ┌─────────────┐         ┌──────────────┐
            │   L1 Skill  │         │   L2 Skill  │         │  Meta Skill  │
            │             │         │             │         │              │
            │ Provider    │         │ Adjudication│         │ Cross-case   │
            │ Behavior    │         │ Policy      │         │ Strategy     │
            └──────┬──────┘         └──────┬──────┘         └──────┬───────┘
                   │                       │                        │
                   └───────────────┬───────┘                        │
                                   ▼                                │
                         ┌─────────────────────┐                     │
                         │  Repair Execution   │◄────────────────────┘
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │  Execution Trace    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Causal Effect Check │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Fitness / Canary /  │
                         │ Transfer Evaluation │
                         └──────────┬──────────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                         ▼                     ▼
                      REJECTED              ACCEPTED
                                                │
                                                ▼
                                  ┌────────────────────────┐
                                  │ Mechanism Experience   │
                                  │ Memory                 │
                                  └────────────┬───────────┘
                                               │
                                               ▼
                                       Meta Skill Update
```

---

# 4. 多层 Skill 的保留与重新定位

## 4.1 L1 Skill — Provider-Level Detection Skill

### Novelty 定位

L1 继续代表：

> detector 在 provider semantic behavior 层面的自进化。

包括：

- candidate generation
- evidence obligation
- verification instruction
- routing behavior
- causal relation detection
- localization behavior
- clean / error boundary behavior

### 研究意义

L1 是：

```text
Model Behavior Evolution
```

而不是单纯 prompt optimization。

---

## 4.2 L2 Skill — Adjudication-Level Policy Skill

### Novelty 定位

L2 继续代表：

> detector 在结构化 evidence 已经形成以后，对 proof adjudication 逻辑的自进化。

包括：

- consensus
- coverage
- boundary consumption
- appeal policy
- adjudication policy
- policy vocabulary

### 研究意义

L2 是：

```text
Decision Policy Evolution
```

与 L1 的 provider behavior evolution 构成明显的双层机制。

---

## 4.3 Meta Skill — Cross-Case Evolution Strategy Skill

### Novelty 定位

Meta Skill 继续作为第三层：

> 通过跨案例和跨 RepairPlan 的经验，学习如何更高效地进行未来自进化。

主要作用：

- hypothesis search prior
- operation ranking
- retry preference
- mechanism preference
- candidate diversity
- experience reuse
- cost-aware search

### 研究意义

Meta 是：

```text
Evolution Strategy Evolution
```

因此多层结构形成：

```text
L1: Detection Behavior Evolution
L2: Decision Policy Evolution
Meta: Evolution Strategy Evolution
```

这是 SEHD-RISE 应保留并强化的核心 novelty。

---

# 5. HTC-TES 在 SEHD-RISE 中的定位

HTC-TES 继续保留。

但建议从：

```text
runtime causal routing authority
```

转向：

```text
type-aware intelligence layer
```

主要服务：

- hallucination type tagging
- experience clustering
- retrieval
- mechanism transfer
- type-conditioned reflection prior
- benchmark stratification
- cross-type generalization analysis

关系应为：

```text
Causal RepairPlan
       +
HTC-TES Type Information
       ↓
Better Search / Retrieval / Transfer
```

而不是：

```text
HTC Type
       ↓
Override Causal Owner
```

---

# 6. 重构后的单向权威关系

SEHD-RISE 应保持：

```text
Detector
   ↓
提供 runtime facts

Causal Repair Layer
   ↓
唯一决定 failure owner / repairability / RepairPlan

L1 / L2 Skill Layer
   ↓
执行 RepairPlan

Execution & Causal Effect Layer
   ↓
证明修复是否真正生效

Fitness / Transfer Layer
   ↓
决定是否值得保留

Experience Memory
   ↓
积累机制经验

Meta Skill
   ↓
优化未来搜索策略
```

应避免：

```text
HTC → 覆盖 causal owner
Meta → 改写 RepairPlan
Gold → 重新创造 runtime owner
L2 reject → fallback 猜测 L1
Reflection → 自己定义 execution semantics
```

---

# 7. SEHD-RISE 的目标状态机

```text
DETECTED
   │
   ▼
CAUSAL_FAILURE_ANALYZED
   │
   ├── NON_EVOLVABLE
   │
   ├── FRAMEWORK_DEFECT
   │
   └── REPAIRABLE
          │
          ▼
     REPAIR_PLANNED
          │
          ▼
     HYPOTHESIS_SEARCH
          │
          ├── NO_HYPOTHESIS
          │
          ▼
     PROPOSAL_VALIDATED
          │
          ▼
       EXECUTED
          │
          ├── EXECUTION_FAILURE
          │
          ▼
     TARGET_EDGE_CHANGED
          │
          ├── NO_CAUSAL_EFFECT
          │
          ▼
      BLOCKER_CLEARED
          │
          ▼
      FITNESS_EVALUATED
          │
     ┌────┴────┐
     │         │
 REJECTED   ACCEPTED
               │
               ▼
       EXPERIENCE_LEARNED
               │
               ▼
          META_UPDATED
```

---

# 8. SEHD-RISE 必须保留的研究 Novelty

## 8.1 Proof-Court Hallucination Detection

继续保留：

- prosecution
- defense
- certificate
- consensus
- appeal
- causal localization
- terminal basis
- fail-closed verdict

---

## 8.2 Explicit Causal Self-Evolution

SEHD-RISE 不应退化为：

```text
改 prompt
→ final label 变好
→ accepted
```

而应保持：

```text
RepairPlan
→ Action Execution
→ Causal Edge Movement
→ Blocking Predicate Closure
→ Detector Improvement
→ Generalization Validation
```

---

## 8.3 Multi-Layer Skill Evolution

必须保留：

```text
L1 Skill
L2 Skill
Meta Skill
```

但从：

```text
三个相互竞争的控制器
```

转为：

```text
三个共享同一 causal contract 的 evolution layers
```

---

## 8.4 Type-Aware Skill Learning

HTC-TES 继续支持：

- type-aware experience
- type-aware retrieval
- type-aware analysis
- type-aware generalization

---

## 8.5 Mechanism-Level Experience Transfer

Experience 应描述：

```text
什么 failure mechanism
在什么条件下
使用什么 Skill layer
执行什么 repair
产生什么 causal effect
是否实现 transfer
```

而不仅是：

```text
某条 rule 成功过
```

---

## 8.6 Explicit Skill Causal Credit

Skill credit 必须绑定：

```text
target edge
execution
effect
fitness
transfer
```

避免：

```text
provider resampling
proxy observable
random verdict movement
```

获得错误信用。

---

# 9. 重构后的核心故障漏斗

建议 SEHD-RISE 重点监控：

```text
Detected Failure
       ↓
Repairable Failure
       ↓
RepairPlan Compiled
       ↓
Hypothesis Generated
       ↓
Proposal Valid
       ↓
Action Executed
       ↓
Target Edge Changed
       ↓
Blocking Predicate Cleared
       ↓
Source Improved
       ↓
Canary Preserved
       ↓
Cross-Case Transfer
       ↓
Skill Accepted
```

每一步都应该有独立统计。

---

# 10. SEHD-RISE 的核心评价指标

## 10.1 Repairability Rate

```text
可生成合法 RepairPlan 的 failure
/
所有应属于 evolvable surface 的 failure
```

---

## 10.2 Unaddressable Framework Failure Rate

应被作为框架质量指标单独监控。

理想目标：

```text
≈ 0
```

---

## 10.3 Execution Rate

```text
真实执行 repair
/
合法 RepairPlan
```

---

## 10.4 Causal Effect Rate

```text
target edge changed
/
executed repairs
```

---

## 10.5 Blocker Closure Rate

```text
blocking predicate cleared
/
target edge changed
```

---

## 10.6 Source Improvement Rate

```text
source detector improved
/
blocker cleared
```

---

## 10.7 Transfer Gain

```text
evolved Skill 在独立案例上的真实增益
```

这是最终 Skill 是否有价值的核心指标之一。

---

## 10.8 Framework Failure Rate

单独统计：

- unaddressable
- authority conflict
- route mismatch
- schema mismatch
- registry mismatch
- state desynchronization
- execution infrastructure failure

不能与 learning reject 混合。

---

# 11. SEHD-RISE 的总体设计原则

## Principle 1

**Complex Detector, Simple Evolution Control Plane**

检测层可以复杂。

自进化控制面必须可验证。

---

## Principle 2

**One Fact, One Authority**

一个事实可以有多个 projection。

但只能有一个 source of truth。

---

## Principle 3

**Repairability Before Evolution**

不是：

```text
Trigger first
→ Repairability later
```

而是：

```text
Repairability established
→ Evolution starts
```

---

## Principle 4

**Multi-Layer Skills, One Causal Contract**

L1、L2、Meta 的创新性来自不同 learning surface。

而不是不同 failure semantics。

---

## Principle 5

**Meta Learns Strategy, Not Truth**

Meta Skill 优化：

```text
how to search
```

而不是重新决定：

```text
what is causally broken
```

---

## Principle 6

**Type Information Organizes Experience, Not Causal Truth**

HTC-TES 增强经验学习。

不覆盖 causal RepairPlan。

---

## Principle 7

**Framework Failure ≠ Learning Failure**

必须在所有实验结果中严格分离。

---

## Principle 8

**Execution Before Credit**

一个 Skill 没有真正执行，就不能获得任何 causal credit。

---

## Principle 9

**Causal Effect Before Fitness**

一个 Skill 即使 final label 偶然改善，如果没有改变目标 causal edge，也不能证明该 Skill 有效。

---

## Principle 10

**Generalization Before Strong Skill Claim**

Source-case improvement 只能证明 local repair。

跨案例 transfer 才能支持真正的 Skill claim。

---

# 12. SEHD-RISE 的最终研究形态

SEHD-RISE 最终应形成：

```text
Trajectory
→ Proof-Court Detection
→ Terminal Causal Failure
→ Repairability
→ Canonical RepairPlan
→ L1 / L2 Skill Execution
→ Causal Effect Verification
→ Fitness / Transfer
→ Mechanism Experience
→ Meta Strategy Learning
→ Better Future Evolution
```

最终保留的整体创新结构为：

```text
Proof-Court Detector
+
Causal Repairability Layer
+
L1 Provider-Level Skill
+
L2 Adjudication-Level Skill
+
HTC-TES Type-Aware Experience
+
Meta Skill Strategy Evolution
+
Explicit Causal Credit
+
Mechanism-Level Cross-Case Transfer
```

---

# 13. SEHD-RISE 的核心研究主张

重构后的框架不应该通过减少机制来获得稳定性。

而应该通过：

```text
减少重复 authority
减少重复状态解释
减少重复 routing
减少语义 adapter
强化 causal contract
强化 Repairability
强化 execution evidence
```

来获得稳定性。

SEHD-RISE 的目标可以概括为：

> **保留 SEHD 的多层智能与研究创新，但让整个自进化过程由一条可解释、可执行、可验证的因果修复链统一起来。**

这也是 SEHD-RISE 与普通：

- Reflexion
- prompt self-refinement
- memory augmentation
- rule tuning

之间最重要的区别。

SEHD-RISE 的最终目标不是：

> “让模型更会反思。”

而是：

> **让 hallucination detector 能够识别自身可修复的机制缺陷，在明确的多层 Skill 空间中执行修复，并通过显式 causal credit 与跨案例 transfer 证明自身确实发生了有效自进化。**
