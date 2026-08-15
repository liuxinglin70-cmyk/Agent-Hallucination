# SEHD-RISE2 能力进化框架技术设计文档

## ------基于 AgentHallu 100 例实验结果的重新分析与优化方案

------------------------------------------------------------------------

# 1. 文档目的

本文档基于：

1.  当前 SEHD-RISE2 detector 架构；
2.  最新 AgentHallu 100 例真实实验结果；
3.  前期 Self-Evolution Framework 设计讨论；

重新审查并优化整个自进化框架。

本文不讨论如何继续堆叠规则，也不讨论简单 prompt 优化。

核心问题：

> 当前 detector 已经具备较强 hallucination detection
> 能力，那么下一阶段如何让系统自动发现自身能力缺口，并通过可验证方式扩展检测能力？

最终目标：

构建：

> 一个能够从失败案例中学习，并持续扩展 verification capability
> 的自进化检测系统。

------------------------------------------------------------------------

# 2. 当前 Detector 状态重新评估

## 2.1 当前 detector 架构

当前系统：

    Trajectory
    
    ↓
    
    Packet Construction
    
    ↓
    
    Evidence Production
    
        |
        |-- Census
        |
        |-- Verification
        |
        |-- Defense
    
    ↓
    
    Deterministic Adjudication
    
    ↓
    
    Final Decision

检测器采用：

-   LLM 负责 evidence production；
-   deterministic court 负责最终裁决；
-   fail-closed authority boundary。

实验报告显示当前检测器：

-   Accuracy: 76.0%
-   Hallucination Recall: 80.6%
-   Clean Recall: 68.4%
-   FN: 12
-   FP: 12
-   unresolved: 0

说明：

当前 detector 已经具备完整检测闭环。

因此：

下一阶段不是替换 detector，而是扩展 detector 的能力空间。

------------------------------------------------------------------------

# 3. 实验结果揭示的问题

100 例案例说明：

错误并不是单一原因。

如果直接：

    failure → skill

会产生严重问题：

-   一个 case 一个 skill；
-   规则爆炸；
-   无法泛化；
-   退化为 prompt engineering。

因此需要引入：

    Failure
    
    ↓
    
    Failure Pattern
    
    ↓
    
    Capability Gap
    
    ↓
    
    Evolution Target

------------------------------------------------------------------------

# 4. Capability Gap 分类体系

根据实验结果，将 detector 失败分为四类。

------------------------------------------------------------------------

# 4.1 Observation Gap

## 定义

Detector 没有观察到错误。

表现：

-   census 没有产生 candidate；
-   错误没有进入 evidence pipeline。

典型案例：

## 缺失工具调用

Agent：

    用户要求执行动作
    
    ↓
    
    Agent直接回答

Detector：

无法发现：

missing required action。

## 错误参数

例如：

工具返回：

    2.0

后续参数：

    2.0000

这种需要特殊观察能力。

实验中 BFCL/025 通过确定性候选增强成功恢复。

因此：

Observation Gap 是第一阶段最适合进化的方向。

对应：

# L1 Candidate Discovery Skill

------------------------------------------------------------------------

# 4.2 Evidence Validation Gap

## 定义

Candidate 已经产生。

但是 verification 无法正确判断。

流程：

    candidate
    
    ↓
    
    verification
    
    ↓
    
    错误拒绝

实验中的典型问题：

-   情有可原解释；
-   外部事实无法验证；
-   领域知识不足。

这说明：

未来需要：

# Verification Evidence Skill

但是：

当前阶段不应该立即开放。

原因：

Verification 是当前 detector 稳定核心。

过早修改会破坏：

-   证据链；
-   attribution；
-   evaluation。

------------------------------------------------------------------------

# 4.3 Decision Policy Gap

## 定义

Evidence 正确。

但是最终裁决错误。

主要表现：

FP。

实验发现：

部分 FP 来自：

-   过度字面合规；
-   将建议当成硬约束；
-   表述差异误判。

对应：

# L2 Policy Evolution

未来负责：

调整：

    evidence
    
    ↓
    
    decision boundary

而不是改变 evidence production。

------------------------------------------------------------------------

# 4.4 Attribution Gap

## 定义

Detector 判断：

"发生错误"。

但是无法找到：

错误真正产生的位置。

实验中大量：

    detected step
    
    !=
    
    root cause step

原因：

当前 locus 规则偏向：

错误显现位置。

例如：

step10 输出错误。

但是：

真正问题：

step3 接受错误输入。

因此需要：

# Causal Attribution Capability

------------------------------------------------------------------------

# 5. Self-Evolution 总体架构重新设计

新的架构：

                     Detector
    
                        |
    
                  Failure Cases
    
                        |
    
                    Meta Skill
    
                        |
    
            Capability Gap Discovery
    
                        |
    
            Skill Specification
    
                        |
    
                  Skill Candidate
    
                        |
    
                  Replay Evaluation
    
                        |
    
                   Promotion
    
                        |
    
                 Skill Registry

------------------------------------------------------------------------

# 6. Skill 重新定义

## 6.1 Skill 不应该是什么

Skill 不应该是：

-   prompt fragment；
-   单条规则；
-   单案例修复；
-   detector override。

否则：

系统会退化：

    failure
    
    ↓
    
    prompt patch
    
    ↓
    
    新的失败

------------------------------------------------------------------------

## 6.2 Skill 新定义

Skill：

> Detector 的可验证能力扩展模块。

目标：

扩展：

    Evidence Generation Capability

而不是直接生成：

    hallucination=true

------------------------------------------------------------------------

# 7. L1 Skill 第一阶段设计

虽然完整体系中 Skill 可以扩展：

-   Candidate；
-   Verification；
-   Attribution；

但是第一阶段必须限制范围。

第一阶段：

    L1 Skill = Candidate Discovery Extension

------------------------------------------------------------------------

## 架构

    Packet
    
    ↓
    
    Existing Census
    
    +
    
    L1 Candidate Skills
    
    ↓
    
    Candidate Set
    
    ↓
    
    Verification
    
    ↓
    
    Defense
    
    ↓
    
    Adjudication

------------------------------------------------------------------------

## 原因

### 1. 保持 detector 冻结

避免重新进入框架重构。

### 2. 明确收益来源

如果性能提升：

可以明确归因：

Skill 提升 candidate recall。

### 3. 降低工程复杂度

不用立即解决：

-   多证据融合；
-   verification 权重；
-   policy 学习。

------------------------------------------------------------------------

# 8. Evidence Skill Contract

Evidence Skill Contract 是：

Skill 接入 detector 的统一协议。

它定义：

Skill 能做什么。

不能做什么。

------------------------------------------------------------------------

# 8.1 Identity Contract

Skill 必须包含：

-   skill_id
-   version
-   provenance
-   capability description

目的：

保证：

可追踪。

------------------------------------------------------------------------

# 8.2 Capability Declaration

Skill 必须声明：

解决：

哪个 capability gap。

例如：

    MissingToolCallCapability

而不是：

    Fix BFCL/067

------------------------------------------------------------------------

# 8.3 Input Contract

Skill 输入：

只能来自：

Detector Projection。

禁止：

访问：

-   gold；
-   evaluator-only information；
-   hidden truth。

------------------------------------------------------------------------

# 8.4 Applicability Contract

Skill 必须声明：

适用范围。

例如：

Tool Skill：

    存在tool interaction

才执行。

------------------------------------------------------------------------

# 8.5 Output Contract

第一阶段：

输出：

Candidate Proposal。

包括：

-   candidate id
-   claim
-   location
-   evidence reference
-   violation pattern

禁止：

输出：

-   final verdict；
-   certificate；
-   court decision。

------------------------------------------------------------------------

# 8.6 Authority Contract

Skill：

可以：

增加候选。

不能：

修改：

-   verification；
-   defense；
-   adjudication。

------------------------------------------------------------------------

# 8.7 Failure Contract

Skill失败：

必须：

    discard output
    
    continue baseline detector

------------------------------------------------------------------------

# 9. Meta Skill 设计

Meta Skill 是整个系统核心。

它不是：

Skill Generator。

它是：

> Failure Understanding and Capability Evolution Controller

------------------------------------------------------------------------

# 10. Meta Skill 功能

## 10.1 Failure Observation

输入：

-   trajectory；
-   packet；
-   candidates；
-   certificates；
-   decision；
-   evaluation feedback。

生成：

Learning Case。

------------------------------------------------------------------------

## 10.2 Failure Pattern Mining

寻找：

多个失败之间共同原因。

禁止：

    one failure → one skill

------------------------------------------------------------------------

## 10.3 Capability Gap Classification

输出：

    Observation Gap
    
    Verification Gap
    
    Policy Gap
    
    Attribution Gap
    
    No Gap

------------------------------------------------------------------------

## 10.4 Skill Specification Generation

Meta Skill 不直接修改 detector。

输出：

Skill Specification：

包括：

-   target capability；
-   applicability；
-   expected evidence；
-   evaluation metric。

------------------------------------------------------------------------

## 10.5 Skill Promotion

Promotion 必须属于 Meta Skill。

评价：

## Capability Improvement

是否提升目标能力。

## Side Effect

是否增加：

-   FP；
-   regression。

## Generalization

是否跨案例。

## Stability

是否重复有效。

------------------------------------------------------------------------

# 11. 后续工程路线

## Phase 1

冻结 detector。

------------------------------------------------------------------------

## Phase 2

建立 Learning Case 数据结构。

------------------------------------------------------------------------

## Phase 3

实现 Meta Skill Failure Analysis。

------------------------------------------------------------------------

## Phase 4

冻结 Evidence Skill Contract。

------------------------------------------------------------------------

## Phase 5

实现第一个 Candidate Discovery Skill。

------------------------------------------------------------------------

## Phase 6

实验：

baseline detector

vs

baseline + skill

------------------------------------------------------------------------

## Phase 7

逐步开放：

-   Verification Skill；
-   L2 Policy Evolution；
-   Attribution Skill。

------------------------------------------------------------------------

# 12. 研究贡献定位

不应该包装：

"自动生成 hallucination detector skill"。

容易被认为：

prompt engineering。

更合理：

# Failure-Driven Capability Evolution for Evidence-Based LLM Verification

核心贡献：

1.  从失败中发现验证系统缺失能力；
2.  将 Skill 定义为 evidence capability extension；
3.  使用 Meta Skill 管理能力发现和生命周期；
4.  实现验证系统持续能力扩展。

------------------------------------------------------------------------

# 13. 最终原则

    Detector
    
    负责稳定检测。


    Skill
    
    负责扩展能力。


    Meta Skill
    
    负责理解失败并驱动进化。

最终目标：

不是生成更多规则。

而是：

> 构建一个能够发现自身能力边界，并通过可验证能力扩展突破边界的自进化验证系统。
