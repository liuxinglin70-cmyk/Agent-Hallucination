# EvoGuard 实验计划与持续迭代路线设计文档

## 0. 文档目的

本文档整合：

-   SEHD-RISE2 Phase 0 Failure Understanding Layer
-   Phase 1-A Policy Hypothesis Extraction
-   Phase 1-B Offline Policy Replay
-   Phase 1-C Policy Runtime Contract + Certificate Binding
-   当前 GitHub 工程状态
-   最新论文故事线讨论

目标：

将已有工程重构成果整理为 EvoGuard：

> Evidence-Grounded Self-Evolving Hallucination Detection Agent

的完整实验计划。

核心研究问题：

如何让一个幻觉检测器从自身失败中发现能力缺口，形成可执行检测策略，并经过验证后持续增强自身检测能力。

------------------------------------------------------------------------

# 1. 当前系统状态总结

## 1.1 Detector 基线

当前 detector 已经具备完整检测闭环：

    Trajectory
        |
    Packet Construction
        |
    Evidence Production
        |
    Census / Verification / Defense
        |
    Deterministic Adjudication
        |
    Final Decision

当前设计：

-   LLM 负责 evidence production
-   deterministic court 负责最终裁决
-   fail-closed authority boundary

Phase 0 在 AgentHallu 100 例真实实验中：

-   Accuracy: 76.0%
-   FN: 12
-   FP: 12
-   unresolved: 0

说明 detector 已具备稳定基础能力。

------------------------------------------------------------------------

# 2. 已完成实验阶段

## Phase 0: Failure Understanding

目标：

回答：

> Detector 为什么失败？

核心产物：

Learning Case。

失败被抽象为：

    Failure Case

    ↓

    Failure Pattern

    ↓

    Capability Gap

    ↓

    Evolution Target

完成结果：

49 个关键失败案例分析：

-   attribution: 25
-   policy: 14
-   observation: 6
-   verification: 4

关键发现：

FP 全部属于 policy gap：

-   strictness_miscalibration
-   expression_tolerance
-   evidence_weighting

因此第一阶段选择：

L2 Policy Evolution。

------------------------------------------------------------------------

# 3. Phase 1-A: Policy Hypothesis Extraction

目标：

将失败模式转化为策略候选。

不是：

    failure -> skill

而是：

    Failure Case

    ↓

    Mechanism Pattern

    ↓

    Policy Hypothesis

    ↓

    Candidate Policy

产生五条策略：

## L2-P1

建议性要求不承担定罪

## L2-P2

表现层添加不是捏造

## L2-P3

散文重述粒度不是误报

## L2-P4

任务/用户前提优先于外部纠错

## L2-P5

单切片缺席不定罪

设计约束：

禁止：

-   one failure → one skill
-   case-level rule

要求：

policy 必须来自多个案例共同机制。

------------------------------------------------------------------------

# 4. Phase 1-B: Offline Policy Replay

目标：

验证：

policy semantics 是否有效。

完成：

-   replay engine
-   certificate binding
-   policy evaluation

结果：

P2+P3+P4+P5：

-   Accuracy 83%
-   FP 12 → 5
-   FN +0

说明：

策略机制有效。

但是：

不能直接 promotion。

原因：

仍然依赖 oracle binding。

------------------------------------------------------------------------

# 5. Phase 1-C: Runtime Contract + Binding

目标：

回答：

> policy 能否在真实运行环境中自动识别触发条件？

完成：

## Policy Genome

冻结：

    PolicyItem

    - policy_id
    - target_certificate_class
    - precondition
    - exclusions
    - action
    - risk_boundary
    - provenance

## Runtime Binder

输入：

label-free runtime projection。

输出：

    classified

    or

    unclassified

完成真实绑定：

162 张证书。

------------------------------------------------------------------------

# 6. 当前关键实验发现

Oracle:

87.0%

Runtime:

77.0%

Binding agreement:

84%

发现：

policy 本身正确。

瓶颈：

Evidence perception。

即：

    Policy Semantics

            OK

            ↓

    Runtime Trigger Recognition

            FAIL POINT

因此下一阶段不是重新设计 policy。

而是提升 binder。

------------------------------------------------------------------------

# 7. 新论文主线

论文核心：

不是：

"自动生成 skill"

而是：

> 构建一个能够从失败中持续发现能力缺口，并通过可验证策略扩展自身能力的自进化幻觉检测器。

系统：

    Detector

    ↓

    Failure Discovery

    ↓

    Capability Gap Discovery

    ↓

    Policy Evolution

    ↓

    Runtime Binding

    ↓

    Replay Verification

    ↓

    Policy Library

    ↓

    Improved Detector

------------------------------------------------------------------------

# 8. 最终实验设计

AgentHalu 693 条数据划分：

## A. Evolution Pool

作用：

驱动进化。

流程：

    Detector Run

    ↓

    Failure Collection

    ↓

    Capability Analysis

    ↓

    Policy Evolution

------------------------------------------------------------------------

## B. Replay Verification Pool

作用：

验证：

历史失败是否被修复。

指标：

-   FP reduction
-   FN regression
-   TP survival
-   stability

------------------------------------------------------------------------

## C. Frozen Evaluation Set

作用：

最终论文评测。

规则：

-   不参与 evolution
-   不参与 policy selection
-   不参与调参

目标：

证明：

自进化后的 detector 泛化。

------------------------------------------------------------------------

# 9. 下一阶段实验计划清单

## Phase 1-C.5 Binder Calibration（已完成 2026-08-15）

优先级：最高

任务：

### 1. 增加 binder abstention

当前：

unclassified = 0

需要：

允许：

    uncertain

    ↓

    baseline

落地：绑定答案新增必填 confidence 字段；low 置信由 harness 机械降级为
unclassified（不信任模型自觉），弃权从口头鼓励变成结构机制（1-C3 的
0 弃权 → 校准后真实出现并参与 fail-closed）。

### 2. 收紧 premise_conflict

目标：

避免：

错误泛化。

落地：三重正判据——具体值继承自任务前提（要求引用前提原句）+ 定罪权威
为外部知识 + 无引用观察反对；agent 自 authored 的错误推导即使靠外部计算
定罪也排除到 content_contradiction。1-C3 的 12 例 premise_conflict 过绑
（FN 源）在校准后降为 0，FN 回归消除。

### 3. 增加判别式 certificate definition

从：

描述性定义

变成：

边界定义。

落地：七类全部改为 CONVICTS WHEN / NOT WHEN 边界定义；
content_contradiction 降为残余基线类（特定类测试全部失败后才绑定）；
绑定 content 前强制双边对照测试（前提 / 近平价 / 呈现性展开 / 单切片
缺席）。配套校准仪器：replay compare 输出类间混淆矩阵 + 过绑/漏绑方向。

验收：

-   runtime accuracy 接近 oracle
-   FP recovery 提升
-   TP loss = 0

实测（162 证书，oracle 绑定为靶，三轮迭代 + pro 探针）：

-   TP loss = 0：达成（全部 32 配置零 TP 损失，FN 全集 12 = baseline）
-   一致率 84.0% → 92.0%（content 侧精度 140/142）；FP 12 → 10
-   runtime accuracy 接近 oracle：**未达**（78% vs 87%）。v4-pro 定向
    探针仅修复 2/10 漏绑——残余 10 例 FP 全部同向（证书以
    "contradiction" 措辞自证，binder 继承框架；部分验证材料不在
    label-free 输入内）。瓶颈重新定位为**证据表示层**：verification
    LLM 生产 rationale 时知道定罪依据，但该信号在证书表示中被丢弃。
    表示层修复（证书记录 basis 信号）进入 Phase 2 清单，P1-D 冻结
    检测器不动。

------------------------------------------------------------------------

## Phase 1-D Frozen Evaluation

任务：

建立最终测试集。

流程：

    Frozen Cases

    ↓

    Frozen Detector

    ↓

    Runtime Binder

    ↓

    Policy Genome

    ↓

    Final Evaluation

验收：

-   相比 baseline 有稳定提升
-   无明显 regression

------------------------------------------------------------------------

## Phase 2 Full Self-Evolution

循环实验：

    Run Detector

    ↓

    Collect Failure

    ↓

    Analyze Capability Gap

    ↓

    Generate Policy

    ↓

    Validate

    ↓

    Promote

    ↓

    Update Policy Library

    ↓

    Repeat

记录：

-   policy 数量变化
-   policy 生命周期
-   被淘汰策略
-   泛化效果

------------------------------------------------------------------------

# 10. Baseline 与对比实验

## Baseline 1

原始 Detector：

DeepSeek V4 Flash / Pro

回答：

无进化能力时效果。

## Baseline 2

Memory / Reflection 类方法

回答：

普通自改进是否足够。

## Ablation

逐步加入：

    Detector

    +

    Failure Analysis

    +

    Policy Evolution

    +

    Runtime Binder

    +

    Replay Gate

------------------------------------------------------------------------

# 11. 最终论文贡献

## Contribution 1

Failure-driven self-evolution

从失败驱动能力扩展。

## Contribution 2

Executable policy evolution

策略不是 prompt，而是可验证执行对象。

## Contribution 3

Verified capability accumulation

只有通过验证的能力才能进入检测器。

## Contribution 4

Self-evolving hallucination detector

证明检测器可以持续增强，而非固定模型。

------------------------------------------------------------------------

# 12. 当前下一步执行顺序

1.  完成 Binder Calibration
2.  重跑 Runtime Evaluation
3.  固化 Frozen Evaluation Split
4.  完成 Fresh Evaluation
5.  执行完整 self-evolution cycle
6.  整理论文实验表和 ablation

------------------------------------------------------------------------

# 总结

当前 EvoGuard 已经完成：

-   Failure Understanding
-   Policy Discovery
-   Offline Replay
-   Policy Genome
-   Runtime Binding

下一阶段核心：

不是继续生成更多 policy。

而是证明：

> 一个幻觉检测器能否可靠地从失败中获得新的、可泛化的检测能力。
