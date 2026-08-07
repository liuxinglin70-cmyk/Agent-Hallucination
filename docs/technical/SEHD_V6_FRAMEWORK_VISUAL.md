# SEHD v6 自进化框架可视化（第三轮审查后）

> 配套文档：`SEHD_V6_FRAMEWORK_TECHNICAL_REVIEW.md`（详细技术审查）。
> 基线：`main@5cc2a2c` + 第三轮审查修复（G1-G5 已全部落地）；测试 **806 passed**。
> 图例：🟢 已闭环 　🟡 部分/历史缺口（已修复见注）　🔴 未启动。

---

## 1. 三层自进化架构总览

```mermaid
flowchart TB
    subgraph META["🔴 Meta Review 层（P7）authority = evolution_guidance_only"]
        MS[MetaSkillState<br/>guidance / ranking / profile]
        MR[meta_review.py<br/>GateExperience 路 OutcomeValidation<br/>DriftSnapshot]
        ME[MetaEdit<br/>闭词表 5 操作]
        MR -->|生成候选| ME -->|证据门提交 G1| MS
    end

    subgraph GATE["Promotion Gate（P1+P4）硬门 路 证据 路 双维状态"]
        PC[promotion_contract.promote<br/>🟢 单一晋升权威]
        GR[EvidenceGrade<br/>strong/weak/conditional/reject/skip]
        AC[ActivationState<br/>pending/working/deployable/disabled]
        PC --> GR & AC
    end

    subgraph L1["L1 Provider-Rule 层（8 模块 slot-rule）"]
        RE[reflection_engine<br/>因果反思 prompt<br/>🟢 注入 meta_guidance G2]
        SG[StructuralGate<br/>cooldown/convergence/cert]
        SK[SkillGenome<br/>add/modify/remove rule]
        RE --> SG --> SK
    end

    subgraph L2["L2 Adjudication-Policy 层（确定性评估）"]
        PR[policy_reflection<br/>确定性 planner<br/>🟢 promoted intent 候选]
        PE[policy_evaluator<br/>L2-pre + L2-post<br/>🟢 operator 分派]
        PG[PolicyGenome<br/>working/deployable 双视图]
        VR[🟢 VocabularyRegistry<br/>DSL 白名单 + RegistryEdit<br/>🟢 pending→working 晋升 G3]
        PR --> PE --> PG
        VR -.单一权威.-> PE
    end

    META -->|🟢 guidance 注入 G2| L1
    META -->|🟢 ranking 注入 G2| L2
    GATE --> L1 & L2
    L1 -->|prompt 注入| DET[PACA-TCL v3.5<br/>proof court 检测器]
    L2 -->|policy items| DET
```

---

## 2. 端到端数据流（一次 case 的进化闭环）

```mermaid
flowchart LR
    A[原始轨迹] --> B[detect_record<br/>proof court]
    B --> C{step_attributor<br/>should_evolve?}
    C -->|否| D[STABLE<br/>入 stable_canaries<br/>contrast_registry]
    C -->|是| E[failure_classifier<br/>确定性归因]

    E -->|L1| F1[reflection_engine<br/>生成 SkillEdit]
    E -->|L2| F2[policy_reflection<br/>闭词表候选]
    E -->|detector/none| F3[skip_unaddressable]

    F1 --> G1[StructuralGate]
    G1 -->|通过| H1[源 replay + canary<br/>+ contrast]
    H1 -->|assess| I1{FitnessAssessment}
    I1 -->|improvement| J1[shadow batch commit<br/>working genome]
    I1 -->|worse/no_effect| J2[rollback / retry]

    F2 --> G2[validate_policy_edit]
    G2 --> H2[policy_evaluator replay]
    H2 --> I2{gold 极性纪律}
    I2 -->|state_changed| J3[fitness_status=strong]
    I2 -->|coverage_only| J4[fitness_status=weak]

    E -->|no_registered_action| K[registry_reflection<br/>🟢 注册 pending → 证据晋升 G3]

    J1 & J2 & J3 & J4 & K --> L[🟢 GateEventLedger]
    L --> M[🟢 Meta Review 聚合<br/>证据门提交 G1]
    M --> N[经验记忆 meta_review 子域]
```

---

## 3. 晋升契约状态机（P1 + P4）

```mermaid
stateDiagram-v2
    [*] --> HardGate
    HardGate --> Reject : hard reject code<br/>(12 类， profile 不可覆盖)
    HardGate --> Skip : skip/no_edit/halt
    HardGate --> Graded : accept/provisional

    state Graded {
        [*] --> Conditional : provisional<br/>telemetry-only
        [*] --> Weak : coverage-only<br/>local effect<br/>canary 保留
        [*] --> Strong : execution+terminal<br/>+gold|independent)<br/>+non_regression
    }

    Conditional : conditional / pending<br/>🔴 不进 items， 入 pending_queue
    Weak : weak / working<br/>✅ working 视图<br/>🔴 deployable 视图<br/>✅ 可改可删
    Strong : strong / deployable<br/>✅ 双视图<br/>🛡 默认保护
    Reject : reject / 无激活
    Skip : skip / 无激活
    note right of Weak
        canary non_regression
        只是 veto，
        永不单独升级为 strong
    end note
```

### Registry entry 状态机（P3A/P3B）

```mermaid
stateDiagram-v2
    [*] --> baseline : 随 runtime 发布
    [*] --> pending : 首次注册（影子态）
    pending --> registered : 🟢 证据晋升 G3（≥3 独立观测）
    registered --> disabled : disable
    disabled --> registered : enable
    baseline : baseline<br/>✅ active 路 不可删
    pending : pending<br/>🔴 影子态 路 不可执行
    registered : registered(working)<br/>✅ active
    disabled : disabled<br/>🔴 排除
```

---

## 4. L2 双阶段执行（P2 + P9）

```mermaid
flowchart TB
    A[validate / defense / materiality] --> B[apply_consensus_gate]
    B --> C{🟢 L2-pre hook<br/>pre_ledger_policy_items}
    C -->|🟢 引擎已接线 G4| D[surviving set<br/>restore 生效]
    C -->|无 item| E[evaluate_pre_ledger_policies<br/>restore_audited_consensus]
    E -->|8 守卫全过| F[恢复证书回 surviving set<br/>从 consensus_blocked 移除]
    D --> G[occurrence snapshot<br/>earliest-origin 定位]
    F --> G
    G --> H[occurrence ledger<br/>append-only]
    H --> I[base terminal state]
    I --> J{🟢 L2-post hook<br/>policy_items}
    J -->|consume_verified_clean_boundary| K[terminal edge only<br/>🔴 不改 occurrence ledger]
    J -->|downgrade/invalidate| L[effective coverage]
    K & L --> M[final state]
```

### L2-post 的 fail-closed 守卫（consume_verified_clean_boundary）

```text
必须同时满足（缺一则 fail-closed，记 skipped + route_result）：
  boundary_path_state == "verified_clean"
  且 boundary_authority_valid == true
  且 terminal_state == "unresolved"
  且 surviving_error_count == 0
  且 不修改 occurrence ledger
  且 记录 before/after + policy item + authority
```

### L2-pre 的 fail-closed 守卫（restore_audited_consensus）

```text
必须同时满足（缺一则 fail-closed）：
  consensus_gate_applied
  且 certificate_id ∈ blocked set
  且 audit_status == "pass"
  且 causal ∈ {causes_incorrect_result, propagates_to_incorrect_result}
  且 impact ∉ {process_only, process_debt}
  且 defense_status != "dominated"
  且 quarantine_status != "quarantined"
  且 authority valid
```

---

## 5. GateEvent → 反馈 → Meta（P5 + P7）——已闭环

```mermaid
flowchart LR
    A[每个终局 gate 决策] -->|_record_gate_event| B[🟢 GateEventLedger<br/>幂等 路 resume 不重复]
    B --> C{投影}
    C -->|same-source| D[project_same_source_retry<br/>精确 retry_state]
    C -->|cross-case| E[project_cross_case_guidance<br/>10 个模糊闭词表 code]

    D -->|🟢 注入 L1 reflection prompt G2| F1[L1 reflection prompt]
    E -->|🟢 注入 L1/L2 prompt G2| F1
    E -->|唯一聚合方| G[aggregate_gate_experience<br/>提取 feedback_codes]

    B -->|run 末尾| G
    G --> H[generate_meta_edit_candidates]
    H --> I[shadow_candidates]
    I -->|🟢 证据门提交 G1| J[MetaSkillState]
    J --> K[project_meta_review_for_prompt]
    K -->|🟢 注入 G2| F1
    K -->|🟢 注入 G2| F2[L2 policy prompt]
```

---

## 6. 持久化 / 恢复 / 继承 / 回滚

```mermaid
flowchart TB
    subgraph CKPT["checkpoint（每 step persist_progress）"]
        C1[champion_checkpoint.json ✅hash]
        C2[shadow_checkpoint.json ✅hash·故意不恢复]
        C3[evolution_checkpoint.json<br/>schema v5]
        C4[htc_tes_experience_memory.json<br/>v2·meta_review 子域]
    end

    subgraph RESTORE["resume 恢复"]
        R1[case_ids + input_hash<br/>+ config_hash 强校验]
        R2[policy_genome ✅hash<br/>+ 逐项闭词表校验]
        R3[stable genome/policy 🟡无 hash]
        R4[trailing halted 回卷<br/>provisional→rollback_resume]
    end

    subgraph INHERIT["inherit_from 跨 run"]
        I1[evolution_state_manifest.json<br/>sha256 绑定三 artifact<br/>路径逃逸防护]
    end

    subgraph ROLLBACK["同步回滚（probe 失败）"]
        RB1[L1 genome]
        RB2[L2 policy genome]
        RB3[registry]
        RB4[经验记忆]
        RB5[MetaSkillState]
        RB6[canaries + pending queue]
    end

    CKPT --> RESTORE
    I1 -->|新 case 流| CKPT
    ROLLBACK -->|全部恢复同一锚点| CKPT
```

---

## 7. 第三轮审查后达成度热力图

```mermaid
flowchart LR
    P0[P0 冻结基线<br/>🟢 usage ledger 已接线 G5] --> P1[P1 晋升契约<br/>🟢 完整]
    P1 --> P2[P2 安全 L2-post<br/>🟢 完整]
    P2 --> P3A[P3A 版本化 registry<br/>🟢 完整]
    P3A --> P3B[P3B 在线 DSL<br/>🟢 晋升闭环 G3]
    P3B --> P4[P4 分级双视图<br/>🟢 完整]
    P4 --> P5[P5 GateEvent+反馈<br/>🟢 注入 L1/L2 G2]
    P5 --> P6[P6 Meta 持久化<br/>🟢 完整]
    P6 --> P7[P7 Meta 闭环<br/>🟢 证据门提交 G1]
    P7 --> P8[P8 E0 端到端<br/>🟢 离线闭环通过]
    P8 --> P9[P9 L2-pre<br/>🟢 引擎已接线 G4]
    P9 --> P10[P10 连续运行+评测<br/>🔴 未启动]

    style P1 fill:#9f9
    style P2 fill:#9f9
    style P3A fill:#9f9
    style P4 fill:#9f9
    style P6 fill:#9f9
    style P8 fill:#9f9
    style P0 fill:#9f9
    style P3B fill:#9f9
    style P5 fill:#9f9
    style P7 fill:#9f9
    style P9 fill:#9f9
    style P10 fill:#ccc
```

---

## 8. 遗留边界速查

| # | 边界 | 位置 | 状态 |
|---|---|---|---|
| **D1** | L1 action 仍是自然语言注入，执行证据依赖 provider 自报（fail-open） | `module_charter.py` / `skill_integration.py` | 🟡 架构改造方向，未启动 |
| **D2** | 动态词表的策略提议依赖注册时捕获的 intent；无 intent 的 promoted 条目仅可被引用 | `registry_promotion.py` | 🟡 已实现 intent 通道，无 intent 条目仍被动 |
| **D3** | stable genome/policy checkpoint 无 content_hash 校验 | `evolution_runner.py` | 🟡 加固建议 |
| **P10** | 连续运行 + 独立评测 | — | 🔴 未启动 |

> **第三轮结论**：G1-G5 五条断链已全部闭合（见 `EVOLUTION_FIXES_SUMMARY.md`），
> 自进化框架的「检测 → 归因 → 反思 → 回放 → 门控 → 反馈 → 元审查 → 证据门提交」
> 闭环在运行时代码中成立；剩余 D1-D3 为架构改造与加固方向，不阻断闭环。
