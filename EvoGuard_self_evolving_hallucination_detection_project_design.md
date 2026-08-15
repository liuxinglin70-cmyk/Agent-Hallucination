# EvoGuard 项目总设计文档

## Evidence-Grounded Self-Evolving Hallucination Detection Agent

------------------------------------------------------------------------

# 1. 项目名称与定位

## 项目名称

**EvoGuard**

全称：

**EvoGuard: Evidence-Grounded Self-Evolving Hallucination Detection
Agent**

中文：

**基于证据驱动的自进化幻觉检测 Agent**

------------------------------------------------------------------------

# 2. 核心研究问题

当前大语言模型幻觉检测方法主要依赖：

-   更强的基础模型能力
-   人工设计检测规则
-   静态 prompt 或固定检测流程

这些方法的问题：

-   检测器无法理解自身失败原因
-   无法从历史错误中积累能力
-   无法形成可验证的新检测能力

EvoGuard 研究的问题：

> 一个幻觉检测 Agent
> 是否能够从自身检测失败中发现可泛化证据，自动形成可执行检测策略，并通过验证后持续增强自身幻觉检测能力？

因此，本项目的核心不是训练一个新的分类模型，而是构建：

> 一个能够从失败中持续进化的幻觉检测器。

------------------------------------------------------------------------

# 3. 论文核心故事线

核心叙事：

## 幻觉检测器不是一次性模型，而应该具备持续学习能力。

传统流程：

    输入
     |
    固定 Detector
     |
    输出结果

失败之后：

    失败
     |
    人工分析
     |
    人工修改规则

EvoGuard：

    输入
     |
    Hallucination Detector
     |
    Failure Discovery
     |
    Evidence Extraction
     |
    Policy Evolution
     |
    Runtime Verification
     |
    Policy Promotion
     |
    Improved Detector

核心思想：

> Detector 不直接学习参数，而通过持续演化可执行检测策略获得能力提升。

------------------------------------------------------------------------

# 4. 核心贡献

## Contribution 1

## Failure-Grounded Self-Evolution

提出一种失败驱动的幻觉检测器自进化机制。

检测器首先运行：

    Detector
     |
    Prediction
     |
    Failure Cases

失败案例成为进化来源。

------------------------------------------------------------------------

## Contribution 2

## Evidence-Grounded Executable Policy Evolution

失败不会直接生成规则。

流程：

    Failure
     |
    Evidence
     |
    Candidate Pattern
     |
    Executable Policy

Policy 是结构化、可执行对象，而不是自然语言描述。

------------------------------------------------------------------------

## Contribution 3

## Verified Policy Accumulation

提出策略晋升机制：

    Candidate Policy
    
    ↓
    
    Runtime Binding
    
    ↓
    
    Replay Verification
    
    ↓
    
    Policy Library

只有经过验证的策略才能成为检测器长期能力。

------------------------------------------------------------------------

# 5. 系统总体架构

                      Hallucination Detection Agent
    
                                |
                                v
    
                         Failure Discovery
    
                                |
                                v
    
                        Evidence Discovery
    
                                |
                                v
    
                        Policy Evolution
    
                                |
                                v
    
                        Meta Governance
    
                                |
                                v
    
                        Executable Policy
    
                                |
                                v
    
                        Runtime Binder
    
                                |
                                v
    
                        Replay Verification
    
                                |
                                v
    
                        Policy Library Update
    
                                |
                                v
    
                     Self-Evolving Detector

------------------------------------------------------------------------

# 6. 三个核心组件

# 6.1 Evidence Discovery Layer

职责：

发现检测失败背后的证据结构。

输入：

-   detector prediction
-   gold label
-   question
-   response
-   context

输出：

结构化失败证据：

    {
     failure_type,
     evidence,
     trigger_condition,
     boundary_condition
    }

注意：

Evidence Layer 本身不是主要进化对象。

它负责：

"发现为什么失败"

------------------------------------------------------------------------

# 6.2 Executable Policy Layer

这是主要进化对象。

Policy 来源：

    Failure Case
    
    ↓
    
    Evidence Pattern
    
    ↓
    
    Candidate Policy
    
    ↓
    
    Executable Policy Genome

Policy 示例：

    Policy:
    
    premise_conflict


    Trigger:
    
    Response contradicts explicitly established premise


    Action:
    
    Require corroborating evidence

Policy 不直接修改标签。

它提供检测约束。

------------------------------------------------------------------------

# 6.3 Meta Governance Layer

职责：

管理 policy evolution。

包括：

-   candidate 筛选
-   policy 边界调整
-   防止过拟合
-   policy 合并与淘汰

例如：

错误策略：

    任何冲突文本都触发 premise_conflict

Meta 调整：

    只有明确 premise 被建立，
    且 response contradicts premise 时触发

------------------------------------------------------------------------

# 7. Policy 生命周期

    Failure
    
    ↓
    
    Evidence Extraction
    
    ↓
    
    Candidate Generation
    
    ↓
    
    Meta Evaluation
    
    ↓
    
    Executable Policy
    
    ↓
    
    Runtime Binding
    
    ↓
    
    Replay Validation
    
    ↓
    
    Promotion
    
    ↓
    
    Policy Library

------------------------------------------------------------------------

# 8. Runtime Binding

Policy 本身不直接运行。

需要 Binder。

运行时：

    New Input
    
    ↓
    
    Detector
    
    ↓
    
    Policy Library
    
    ↓
    
    Binder
    
    ↓
    
    Trigger Decision
    
    ↓
    
    Policy Action
    
    ↓
    
    Final Detection Result

Binder 判断：

当前输入是否满足 policy 触发条件。

------------------------------------------------------------------------

# 9. 实验设计

数据集：

Agent-Halu

693 条数据。

不采用传统 train/test 表述。

采用：

## Evolution Pool

用于驱动自进化。

流程：

    Detector Run
    
    ↓
    
    Failure Collection
    
    ↓
    
    Evidence Analysis
    
    ↓
    
    Policy Evolution

------------------------------------------------------------------------

## Replay Verification Pool

用于验证：

新 policy 是否修复历史失败。

指标：

-   error reduction
-   regression
-   stability

------------------------------------------------------------------------

## Frozen Evaluation Set

最终评测。

要求：

-   不参与 evolution
-   不参与 policy selection
-   不参与调参

目的：

验证：

自进化后的幻觉检测器是否获得泛化提升。

------------------------------------------------------------------------

# 10. 当前实验进展

已完成：

## P1-B

Policy 语义验证。

## P1-C

完成：

-   Policy Runtime Contract
-   Policy Genome
-   Certificate Binding
-   Runtime Replay

结果：

Oracle:

87%

Runtime:

77%

Binding agreement:

84%

主要发现：

Policy 设计有效。

当前瓶颈：

Evidence 到 Runtime Trigger 的感知能力。

------------------------------------------------------------------------

# 11. 后续路线

## Phase 1-C.5

Binder Calibration

目标：

提升：

-   trigger precision
-   runtime agreement
-   abstention ability

------------------------------------------------------------------------

## Phase 1-D

Frozen Evaluation

验证：

进化后的 detector 是否超过初始 detector。

------------------------------------------------------------------------

## Phase 2

Full Self-Evolution

循环：

    Detect
    
    ↓
    
    Fail
    
    ↓
    
    Explain
    
    ↓
    
    Generate Policy
    
    ↓
    
    Verify
    
    ↓
    
    Promote
    
    ↓
    
    Improve Detector

------------------------------------------------------------------------

# 12. 最终目标

EvoGuard 希望证明：

幻觉检测器不应该只是一个固定模型。

未来可靠 Agent 应该能够：

    发现自己的失败
    
    ↓
    
    理解失败原因
    
    ↓
    
    形成检测知识
    
    ↓
    
    验证知识有效性
    
    ↓
    
    持续增强自身能力

最终目标：

构建一个真正能够自我进化的幻觉检测 Agent。
