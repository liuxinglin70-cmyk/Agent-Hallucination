# SEHD v6 框架可视化图谱

> 本文档提供 SEHD v6 自进化幻觉检测框架的多角度可视化视图，帮助研究者快速理解系统架构、数据流和关键机制。

---

## 目录

1. [图 1: 系统总体架构](#图-1-系统总体架构)
2. [图 2: PACA-TCL v3.5 检测流水线](#图-2-paca-tcl-v35-检测流水线)
3. [图 3: 双层自进化表面](#图-3-双层自进化表面)
4. [图 4: 单案例进化闭环时序](#图-4-单案例进化闭环时序)
5. [图 5: 确定性失败归因决策树](#图-5-确定性失败归因决策树)
6. [图 6: L1 进化循环状态机](#图-6-l1-进化循环状态机)
7. [图 7: L2 策略进化循环](#图-7-l2-策略进化循环)
8. [图 8: HTC-TES 类型路由](#图-8-htc-tes-类型路由)
9. [图 9: 数据分区与评估协议](#图-9-数据分区与评估协议)
10. [图 10: 关键设计不变量关系](#图-10-关键设计不变量关系)

---

## 图 1: 系统总体架构

```mermaid
flowchart TB
    subgraph L5["🎯 实验协议层 (experiments/)"]
        direction LR
        EXP1[数据分区<br/>dev/probe/holdout]
        EXP2[金丝雀探针]
        EXP3[使用案例账本]
    end

    subgraph L4["🧬 自进化框架层 BCCSE v5"]
        direction TB
        subgraph L1["L1 Provider-Rule 层"]
            M1[candidate_trigger]
            M2[evidence_obligation]
            M3[candidate_to_verifier_route]
            M4[atomic_verifier_instruction]
            M5[clean_defense_boundary]
            M6[causal_link_test]
            M7[earliest_origin_tiebreak]
            M8[abstention_rule]
        end
        subgraph L2["L2 Adjudication-Policy 层"]
            POL[封闭词表策略项<br/>add/modify/remove]
            EVAL[确定性求值器<br/>policy_evaluator]
        end
        ATTR[确定性失败归因器]
        REFL[反思引擎]
        GATE[结构门控]
        FIT[适应度评估]
    end

    subgraph L3["🏷️ 类型条件化层 HTC-TES v3"]
        TC[8类幻觉分类器]
        ROUTE[类型→模块路由]
        TMEM[类型经验记忆]
    end

    subgraph L2D["⚖️ 检测引擎层 PACA-TCL v3.5"]
        H0[H0 基础分析]
        H1[H1 控方立案]
        H2[H2 共识门裁决]
        H3[H3 反事实定位]
        CRS[五维CRS投影]
    end

    subgraph L1I["🔧 基础设施层"]
        PROV[Provider路由<br/>DeepSeek-V4]
        CACHE[共享LLM缓存]
        ENV[环境加载器]
    end

    L5 -->|原始轨迹| L2D
    L2D -->|判决+证据| ATTR
    ATTR -->|L1归因| REFL
    ATTR -->|L2归因| L2
    REFL -->|SkillEdit| GATE
    GATE -->|通过| FIT
    FIT -->|接受| L1
    FIT -->|拒绝| RB[回滚]
    L2 -->|policy_items| L2D
    L1 -->|prompt注入| L2D
    L3 -->|类型路由| REFL
    TMEM -->|经验参考| REFL
    L1I --> L2D
    L1I --> REFL

    style L5 fill:#e1f5fe
    style L4 fill:#f3e5f5
    style L3 fill:#e8f5e8
    style L2D fill:#fff3e0
    style L1I fill:#fafafa
```

---

## 图 2: PACA-TCL v3.5 检测流水线

```mermaid
flowchart LR
    subgraph INPUT["输入"]
        T[原始轨迹<br/>label-free]
    end

    subgraph H0["H0: 基础分析与候选普查"]
        OCSE[OCSE包构建<br/>事件ID分配]
        CENSUS[候选构造枚举<br/>F/Fa/P/S四轴]
    end

    subgraph H1["H1: 控方立案"]
        PROS[因果控方案例构建]
        CERT[错误证书生成<br/>含证据链]
    end

    subgraph H2["H2: 共识门裁决"]
        DEF[对抗性辩护<br/>反证据开脱]
        GATE{共识门<br/>证书有效性}
        APPEAL{上诉?}
        AP_PROS[上诉控方]
        AP_DEF[上诉辩方]
    end

    subgraph H3["H3: 反事实定位"]
        BACK[因果回溯]
        ORIGIN[最早起源裁决]
    end

    subgraph OUTPUT["输出"]
        V1[verified_error]
        V2[verified_clean]
        V3[unresolved]
        CRS[五维CRS投影]
    end

    T --> OCSE --> CENSUS --> PROS --> CERT --> DEF --> GATE
    GATE -->|争议高| APPEAL
    APPEAL -->|是| AP_PROS --> AP_DEF --> GATE
    APPEAL -->|否| H3
    GATE -->|权威有效| H3
    GATE -->|权威无效| V3
    H3 --> BACK --> ORIGIN
    ORIGIN -->|错误确认| V1
    ORIGIN -->|清白确认| V2
    ORIGIN -->|证据不足| V3
    H0 -.-> CRS
    H1 -.-> CRS
    H2 -.-> CRS
    H3 -.-> CRS

    style H0 fill:#e3f2fd
    style H1 fill:#fce4ec
    style H2 fill:#f3e5f5
    style H3 fill:#e8f5e8
    style OUTPUT fill:#fff8e1
```

---

## 图 3: 双层自进化表面

```mermaid
flowchart TB
    subgraph BASE["❄️ 冻结基座 PACA-TCL v3.5"]
        CORE[检测引擎核心<br/>H0-H3流水线]
        COURT[证明法庭<br/>证明合同+共识门]
        CRS[CRS五维投影<br/>权重冻结]
    end

    subgraph L1["🧬 L1 Provider-Rule 层 (可进化)"]
        direction LR
        R1[槽位规则<br/>condition/action/anchor]
        R2[8模块基因组<br/>SkillGenome]
        R3[确定性渲染<br/>When condition, action.]
        R4[Prompt注入<br/>Provider系统提示]
    end

    subgraph L2["⚖️ L2 Adjudication-Policy 层 (可进化)"]
        direction LR
        P1[封闭词表策略项<br/>precondition+action]
        P2[PolicyGenome<br/>≤12项]
        P3[确定性求值器<br/>policy_evaluator]
        P4[法庭钩子<br/>adjudicate_proof_court]
    end

    subgraph CTRL["进化控制"]
        ATTR[确定性归因]
        REFL[反思引擎]
        GATE[结构门控]
        FIT[适应度评估]
    end

    L1 -->|规则注入| BASE
    L2 -->|策略项| BASE
    BASE -->|判决+证据| ATTR
    ATTR -->|L1归属| REFL
    ATTR -->|L2归属| L2
    REFL -->|SkillEdit| GATE
    GATE -->|通过| FIT
    FIT -->|回放验证| BASE
    FIT -->|接受/拒绝| L1

    style BASE fill:#eceff1
    style L1 fill:#e8f5e8
    style L2 fill:#fff3e0
    style CTRL fill:#f3e5f5
```

---

## 图 4: 单案例进化闭环时序

```mermaid
sequenceDiagram
    autonumber
    participant T as 轨迹输入
    participant D as 检测器
    participant A as 归因器
    participant H as HTC-TES
    participant R as 反思引擎
    participant G as 结构门控
    participant F as 适应度评估
    participant M as 经验记忆
    participant C as 检查点

    T->>D: label-free轨迹
    Note over D: H0-H3法庭流水线
    D-->>A: 判决+证据+CRS

    A->>A: 确定性失败分类
    Note over A: 优先级1-5<br/>金标极性门

    alt L1归因
        A->>H: L1 owner信号
        H->>H: 类型分类+路由
        H->>R: 目标模块+Charter
        R->>R: LLM反思(≤5候选)
        R->>R: 去重+critic选择
        R->>G: SkillEdit
        G->>G: 结构验证
        alt 门控通过
            G->>F: 影子基因组
            F->>D: 源案例重检测
            D-->>F: 影子结果
            F->>F: 适应度评估
            F->>D: 金丝雀回放
            alt 接受
                F->>M: 更新经验
                F->>C: 提交基因组
                Note over C: champion更新
            else 拒绝
                F->>F: 回滚影子
            end
        else 门控拒绝
            G->>R: 结构化拒绝反馈
            Note over R: 冷却期/重试
        end
    else L2归因
        A->>R: L2策略提案请求
        R->>G: PolicyEdit
        G->>F: 候选策略基因组
        F->>D: 策略重检测
        D-->>F: 策略评估
        F->>F: 效果门+金标门
        alt 接受
            F->>C: 更新策略基因组
        else 拒绝
            F->>F: 回滚
        end
    else 不可归因
        A->>A: skip_unaddressable
    end

    C-->>T: 下一案例(更新后基因组)
```

---

## 图 5: 确定性失败归因决策树

```mermaid
flowchart TD
    START[检测输出<br/>判决+证据+CRS] --> Q1{Provider工件<br/>缺失/畸形?}

    Q1 -->|是| L1A[L1归因<br/>按artifact类型定owner]
    L1A --> Q1A{金标极性门}
    Q1A -->|FN| FNMOD{owner在<br/>FN模块集?}
    Q1A -->|FP| FPMOD{owner在<br/>FP模块集?}
    Q1A -->|unresolved| ACC[接受归因]
    FNMOD -->|是| ACC
    FNMOD -->|否| DROP[丢弃owner]
    FPMOD -->|是| ACC
    FPMOD -->|否| DROP

    Q1 -->|否| Q2{共识门阻断<br/>非空候选?}
    Q2 -->|是| L2A[L2归因<br/>consensus_gate_policy]

    Q2 -->|否| Q3{空壳complete?<br/>coverage=complete<br/>但证书=0}
    Q3 -->|是| L2B[L2归因<br/>coverage_validity_policy]

    Q3 -->|否| Q4{边界-终态冲突?<br/>boundary=verified_clean<br/>terminal=unresolved}
    Q4 -->|是| L2C[L2归因<br/>boundary_consumption_policy]

    Q4 -->|否| Q5{终态与证书记账<br/>矛盾?}
    Q5 -->|是| DET[detector_layer_out_of_scope]
    Q5 -->|否| NOACT[no_actionable]

    L2A --> L2LOOP[进入L2策略循环]
    L2B --> L2LOOP
    L2C --> L2LOOP
    DET --> TEL[记录遥测]
    NOACT --> TEL
    DROP --> TEL
    ACC --> L1LOOP[进入L1进化循环]

    style L1A fill:#e8f5e8
    style L2A fill:#fff3e0
    style L2B fill:#fff3e0
    style L2C fill:#fff3e0
    style DET fill:#ffebee
    style NOACT fill:#fafafa
```

---

## 图 6: L1 进化循环状态机

```mermaid
stateDiagram-v2
    [*] --> Detected: 案例检测完成

    Detected --> TriggerCheck: should_evolve?
    TriggerCheck --> Stable: 否
    TriggerCheck --> Attributed: 是

    Stable --> CanaryPool: 入稳定池
    CanaryPool --> [*]

    state Attributed {
        [*] --> L1Owner: L1归因
        [*] --> L2Route: L2归因
        [*] --> Skip: 不可归因
        L1Owner --> Reflection: 类型路由
        L2Route --> [*]: 转L2循环
        Skip --> [*]
    }

    Attributed --> Reflection: L1路径

    state Reflection {
        [*] --> Generate: 生成候选(1-5个)
        Generate --> Dedup: 去重
        Dedup --> Critic: critic选择
        Critic --> Candidate: 单一候选
        Candidate --> [*]
    }

    Reflection --> StructuralGate: SkillEdit

    state StructuralGate {
        [*] --> Validate: 结构验证
        Validate --> Pass: 通过
        Validate --> Fail: 拒绝
        Fail --> Cooldown: 模块冷却
        Cooldown --> [*]
        Pass --> [*]
    }

    StructuralGate --> ShadowApply: 通过

    state ShadowApply {
        [*] --> Apply: 应用到影子基因组
        Apply --> SourceReplay: 源案例重检测
        SourceReplay --> Fitness: 适应度评估
        Fitness --> Canary: 金丝雀回放
        Canary --> Decision: 决策
        Decision --> [*]
    }

    ShadowApply --> Commit: 接受
    ShadowApply --> Rollback: 拒绝/无效果/更差

    state Commit {
        [*] --> UpdateGenome: 更新工作基因组
        UpdateGenome --> UpdateMemory: 更新经验记忆
        UpdateMemory --> Checkpoint: 持久化
        Checkpoint --> [*]
    }

    Rollback --> Restore: 恢复父基因组
    Restore --> [*]

    Commit --> NextCase: 下一案例
    NextCase --> Detected
```

---

## 图 7: L2 策略进化循环

```mermaid
flowchart LR
    subgraph PROPOSE["提案阶段"]
        P1[L2归因信号] --> P2[policy_reflection<br/>封闭词表prompt]
        P2 --> P3{响应解析}
        P3 -->|有效| P4[PolicyEdit<br/>add/modify/remove]
        P3 -->|无效| P5[skip_no_policy]
    end

    subgraph VALIDATE["验证阶段"]
        V1[封闭词表检查] --> V2[容量检查≤12]
        V2 --> V3[语义去重]
        V3 --> V4[索引边界检查]
        V4 --> V5[基线不可变检查]
        V5 --> V6[strong/weak保护检查]
    end

    subgraph APPLY["应用阶段"]
        A1[apply_policy_edit] --> A2[候选PolicyGenome<br/>parent_hash链接]
    end

    subgraph REPLAY["回放阶段"]
        R1[源案例重检测<br/>policy_replays] --> R2{效果门}
        R2 -->|终态移动| R3[金标极性门]
        R2 -->|仅覆盖移动| R4[接受<br/>fitness=weak]
        R2 -->|无效果| R5[reject_policy_no_effect]
        R3 -->|score严格上升| R6[接受<br/>fitness=strong]
        R3 -->|否则| R7[reject_policy_fitness]
    end

    subgraph DECISION["决策"]
        D1[accept_policy] --> D2[更新工作基因组]
        D2 --> D3[checkpoint持久化]
        D4[reject] --> D5[回滚<br/>工作基因组不变]
    end

    PROPOSE --> VALIDATE
    VALIDATE --> APPLY
    APPLY --> REPLAY
    REPLAY --> DECISION

    style PROPOSE fill:#e3f2fd
    style VALIDATE fill:#fff3e0
    style APPLY fill:#f3e5f5
    style REPLAY fill:#e8f5e8
    style DECISION fill:#fff8e1
```

---

## 图 8: HTC-TES 类型路由

```mermaid
flowchart TB
    subgraph INPUT["输入信号"]
        SIG[AttributionSignal<br/>+ 检测特征]
    end

    subgraph CLASSIFY["类型分类器"]
        C1[错误类型映射<br/>ErrorType→HallucinationType]
        C2[目标模块映射]
        C3[候选性质分析<br/>claim/action/omission/adoption]
        C4[CRS模式分析<br/>五维异常检测]
        C5[状态转换分析<br/>unresolved+CRS聚合]
    end

    subgraph TYPES["8类幻觉"]
        T1[FACTUAL]
        T2[INSTRUCTIONAL]
        T3[TOOL]
        T4[REASONING]
        T5[OMISSION]
        T6[ADOPTION]
        T7[CONTEXTUAL]
        T8[UNKNOWN]
    end

    subgraph ROUTE["类型→模块路由 (建议性)"]
        R1[FACTUAL→evidence_obligation, atomic_verifier]
        R2[INSTRUCTIONAL→clean_defense, abstention]
        R3[TOOL→c_to_v_route, atomic_verifier]
        R4[REASONING→causal_link, origin_tiebreak]
        R5[OMISSION→candidate_trigger, c_to_v_route]
        R6[ADOPTION→evidence_obligation, clean_defense]
        R7[CONTEXTUAL→abstention, clean_defense]
    end

    subgraph INTERSECT["路由交集"]
        I1[类型路由模块集]
        I2[归因路由模块集<br/>硬边界]
        I3{交集非空?}
        I4[交集模块集]
        I5[回退到归因路由]
    end

    INPUT --> CLASSIFY
    CLASSIFY --> TYPES
    TYPES --> ROUTE
    ROUTE --> I1
    SIG --> I2
    I1 --> I3
    I2 --> I3
    I3 -->|是| I4
    I3 -->|否| I5
    I4 --> OUTPUT[目标模块集<br/>+ 类型策略]
    I5 --> OUTPUT

    style CLASSIFY fill:#e3f2fd
    style TYPES fill:#f3e5f5
    style ROUTE fill:#e8f5e8
    style INTERSECT fill:#fff3e0
```

---

## 图 9: 数据分区与评估协议

```mermaid
flowchart TB
    subgraph DATA["数据集"]
        ALL[全部案例]
    end

    subgraph PARTITION["三层分区"]
        DEV[development_pool<br/>开发池<br/>可重用进化]
        PROBE[gold_probe<br/>探针集<br/>定期方向/回滚门]
        HOLDOUT[final_holdout<br/>最终保留集<br/>密封只读]
    end

    subgraph EVOLVE["进化运行"]
        E1[案例N检测]
        E2[归因+反思]
        E3[源回放]
        E4[金丝雀回放<br/>来自dev稳定案例]
        E5[提交/回滚]
    end

    subgraph EVAL["评估"]
        EV1[三态混淆矩阵]
        EV2[选择性准确率/风险]
        EV3[McNemar检验]
        EV4[Wilson区间]
    end

    subgraph CLAIM["主张资格"]
        C1{final_holdout<br/>评估?}
        C2[champion_eligible<br/>冠军资格]
        C3[development_evidence<br/>仅开发证据]
    end

    ALL --> PARTITION
    DEV --> EVOLVE
    PROBE -.->|定期探针| EVOLVE
    EVOLVE --> EVAL
    HOLDOUT -->|最终评估| EVAL
    EVAL --> CLAIM
    C1 -->|是+授权| C2
    C1 -->|否| C3

    style DEV fill:#e8f5e8
    style PROBE fill:#fff3e0
    style HOLDOUT fill:#ffebee
    style C2 fill:#fff8e1
```

---

## 图 10: 关键设计不变量关系

```mermaid
flowchart TD
    subgraph INV["核心不变量"]
        I1[❄️ 检测基座冻结<br/>CRS权重/法庭规则不变]
        I2[🔒 金标隔离<br/>标签不进规则/策略/反思]
        I3[🚫 失败闭合<br/>缺失权威/解析失败→拒绝]
        I4[🔄 修正闭合<br/>每层可修复/移除自身工件]
        I5[📊 证据驱动门控<br/>无手工调参阈值]
        I6[🔑 Provider纪律<br/>仅DeepSeek-V4路由]
    end

    subgraph MECH["实现机制"]
        M1[L1/L2双层进化表面]
        M2[确定性失败归因器]
        M3[结构门控+金丝雀回放]
        M4[封闭词表注册表]
        M5[影子基因组+回滚]
        M6[使用案例账本]
    end

    subgraph OUT["保障结果"]
        O1[可审计进化]
        O2[可复现结果]
        O3[无标签泄漏]
        O4[无过拟合阈值]
        O5[可回滚错误]
    end

    I1 --> M1
    I2 --> M2
    I2 --> M4
    I3 --> M3
    I4 --> M1
    I4 --> M5
    I5 --> M3
    I5 --> M4
    I6 --> M4

    M1 --> O1
    M2 --> O1
    M3 --> O2
    M4 --> O2
    M2 --> O3
    M3 --> O4
    M5 --> O5
    M6 --> O2

    style I1 fill:#eceff1
    style I2 fill:#ffebee
    style I3 fill:#fff3e0
    style I4 fill:#e8f5e8
    style I5 fill:#e3f2fd
    style I6 fill:#f3e5f5
```

---

## 图例说明

| 颜色 | 含义 |
|------|------|
| 🟦 蓝色 | 输入/分类/分析阶段 |
| 🟪 紫色 | 进化框架核心组件 |
| 🟩 绿色 | 可进化表面/成功路径 |
| 🟨 黄色 | 评估/门控/检查点 |
| 🟥 红色 | 冻结基座/拒绝路径 |
| ⬜ 灰色 | 基础设施/回退路径 |

---

## 使用建议

1. **快速理解**: 从图 1（总体架构）开始，然后看图 4（单案例闭环）了解动态流程。
2. **深入研究**: 图 5（归因决策树）和图 6（L1 状态机）提供了进化机制的详细视图。
3. **实验设计**: 图 9（数据分区）帮助理解评估协议和冠军资格要求。
4. **架构审查**: 图 10（不变量关系）用于验证设计决策是否符合核心原则。

---

*本文档与 `docs/SEHD_V6_ACADEMIC_DOCUMENT.md` 配套使用，共同构成 SEHD v6 框架的完整技术文档。*
