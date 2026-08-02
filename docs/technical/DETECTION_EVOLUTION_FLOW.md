# 一轮检测与自进化审查流程

本文描述当前 SEHD v6 / PACA-TCL v3.5 / BCCSE v5 / HTC-TES v3 的一次完整运行。重点是数据如何流动、哪些模块被激活、哪些指标被计算，以及什么条件下结果或 edit 可以继续向下游传播。

## 1. 总体边界

```mermaid
flowchart TD
    A[原始 AgentHallu 轨迹] --> B[去除 evaluator-only 字段]
    B --> C[OCSE packet / addressable events]
    C --> D[PACA-TCL base v2]
    D --> E[construct census]
    E --> F[focused prosecution]
    F --> G[defense]
    G --> H{是否触发 appeal}
    H -- 否 --> I[proof adjudication]
    H -- 是 --> J[appeal prosecutor + defender]
    J --> I
    I --> K{authority/runtime gate}
    K -- valid --> L[verified_error / verified_clean / unresolved]
    K -- invalid --> M[阻断权威结论]
    L --> N[attribute_failure]
    N --> O[HTC-TES 类型与模块路由]
    O --> P[DeepSeek v4 flash 反思]
    P --> Q[结构/类型/安全 gate]
    Q --> R[shadow genome]
    R --> S[source replay]
    S --> T[fitness + canary]
    T -- 通过 --> U[champion]
    T -- 失败 --> V[rollback / reject]
```

“检测结果正确”与“进化 edit 合法”是两条不同的授权链。检测器必须先拿到 decision authority；edit 即使结构合法，也必须经过 source replay，必要时还要经过稳定 canary。任何一条链失败都不能修改 champion。

## 2. 检测阶段

### 2.1 输入隔离

`run_online_evolution()` 调用 `_run_detection_with_genome()` 时，输入记录先经过 `_detector_record()`。以下字段只允许 evaluator 使用，不进入 detector、反思 prompt 或 genome：

- `gold_label`
- `is_hallucination`
- `gold_explanation`
- `hallucination_step`
- `hallucination_reason`
- `outer_feedback`

这样可以保证“检测器看到轨迹，评估器看到标签”，而不是让标签泄漏进检测过程。

### 2.2 OCSE packet

轨迹被转换成可寻址的 packet：

- 每个事件有稳定的 `event_id`；
- 保留 source step、agent/tool interaction、输入输出和依赖关系；
- 后续 candidate、证据和责任定位只能引用 packet 中存在的 ID；
- 原始文本被当作证据，不当作系统指令。

这一层的作用是把“模型读到的一段文本”变成可以审计的事件图。

### 2.3 PACA-TCL 基础检测

基础层按以下顺序运行：

1. 候选扫描：从 F / Fa / P / S 四个轴寻找 claim、action、omission、adoption、tool-use 等潜在起点；
2. candidate census：选择最早可寻址 origin，记录依赖事件、候选类型和模块标签；
3. verifier route：将候选送到对应 verifier；
4. atomic verification：逐候选检查规范、违背、agent control、impact 和 counterfactual link；
5. boundary/defense：检查 ordinary failure、environment-caused、authorized action、recovery-before-impact 等边界；
6. propagation：确认 origin → consumption → material impact 是否真实成立。

基础层只产生证据和过程信号，不直接把一个模糊异常升级为最终幻觉结论。

### 2.4 v3.5 construct census 与 focused prosecution

v3.5 会再次建立 construct census，并对有风险的候选做 focused prosecution。两者的作用不同：

- construct census 负责覆盖完整性；
- focused prosecution 负责对候选的因果链做更集中、更严格的证明。

最终会合并基础 prosecution、construct census 和 focused audit。指标包括 candidate 数、prosecution coverage、defense coverage、证书字段完整度、quarantine 数以及责任事件。

### 2.5 defense 与 appeal

defense 不是简单的“没有发现错误”，而是要提供可引用的支持链或边界链。只有 prosecution 和 defense 都满足覆盖条件，proof court 才能把结果当作有权威的确定性判断。

当 appeal calibrator 判定争议足够高或 surviving origins 太多时，会触发：

1. appeal prosecutor 重新构造争议候选的错误证据；
2. appeal defender 看到 appeal prosecution 后提出对抗证据；
3. proof court 重新 adjudicate，并启用 consensus gate；
4. 若仍有多个 surviving origin，可能继续做 counterfactual localization。

当前的安全边界是：必要 appeal 未完成时，结果只能是 `unresolved + decision_authority=invalid`，不能复用 appeal 之前的结论。这是 fail-closed；本次 fresh run 的 SSL EOF 正是在这里被阻断的。

### 2.6 检测输出

检测输出至少包含：

- `state`: `verified_error`、`verified_clean` 或 `unresolved`；
- `runtime_valid`；
- `decision_authority`；
- `authority_failure_reasons`；
- `occurrence_state` 与 recovery/impact ledger；
- `responsible_step`；
- prosecution/defense/appeal 证据；
- provider/cache metadata；
- BCCSE 使用的 CRS 投影和 coverage 诊断字段。

如果 `runtime_valid=false` 或 authority 为 invalid，`resolve_authoritative_state()` 对下游暴露的是 `unresolved`，而不是把 proof 内部的 `verified_error` 当成可以进化的确定性结论。

## 3. 自进化阶段

### 3.1 genome snapshot

每个 evolution case 开始时记录：

- 当前 genome hash；
- generation；
- 当前 module rules；
- stable canary 集合；
- 本轮 budget 和历史 rejected edits。

edit 先应用到 candidate/shadow genome，champion 暂时不变。

### 3.2 self-supervised attribution

`attribute_failure()` 将 detector 输出变成 `AttributionSignal`，包括：

- `error_type`；
- `trigger_source`；
- primary/secondary target module；
- attribution confidence；
- prosecution/defense coverage；
- CRS 五维投影；
- candidate count；
- detection state；
- responsible step 是否可用；
- process diagnostics（`localization_status`、`appeal_status`、
  `focused_quarantined_candidate_count`）。

默认 self-supervised 模式不使用 gold。只有显式启用 `use_gold` 或 `fitness_use_gold` 时，gold 才能进入对应的评估路径。

#### process diagnostics 与 gold 的优先级

detector 会输出一个 label-free 的 `diagnostics` 块，说明**哪个流水线阶段**出了问题
（focused-prosecution quarantine、appeal 失败、localization 无法排歧）。
它只用于确定**根因模块**，绝不改写极性：

| 情况 | 极性来源 | primary module | secondary module |
| --- | --- | --- | --- |
| self-supervised + diagnostic | diagnostics | diagnostics | diagnostics |
| gold FP/FN | **gold** | **gold** | diagnostics |
| gold 判定正确 | **CORRECT，不触发** | — | — |

三条不变式：

1. gold 建立的 FP/FN/localization 极性优先于任何 detector 内部信号；
2. gold 确认正确的 case 不会因为“过程有瑕疵但结论没变”而被制造出 trigger；
3. diagnostics 只能补充 secondary module——把 false positive 归因到
   `earliest_origin_tiebreak` 会把修复送到一个无法纠正错误结论的模块。

阶段优先级按 proof court 的实际执行顺序：appeal > quarantine > localization。

#### runtime-invalid 只作为遥测

detector 运行崩溃时产生 `runtime_invalid_signal()`：`should_evolve` 恒为
`False`，`diagnostic_only=True`。没有权威判决就没有 coverage、没有 CRS，
因此无法归因到任何模块，只记录“当前 genome 在这个 case 上跑不起来”。
异常文本会被归约成有限类别（`provider_timeout`、`provider_auth` 等），
防止原始 runtime 字符串（可能含泄漏的 label）进入 reflection prompt。

### 3.3 HTC-TES 类型路由

HTC-TES 根据 trace 将问题归为 factual、instructional、tool、reasoning、omission、adoption、contextual 或 unknown，并给出类型策略。

路由有两个约束：

1. 类型路由给出优先模块；
2. causal attributor 给出的模块是硬边界。

现在如果两者没有交集，不再静默丢弃候选，而是回退到已经由 causal attributor 限定的模块集合。这样能避免低置信度类型分类导致 `skip_route_conflict`，同时没有允许 edit 越过因果归因边界。

### 3.4 DeepSeek 反思

所有 provider route 统一解析 endpoint、model、credential selector、temperature、max_tokens、retry 和 timeout。当前实验约束使用：

- endpoint：`https://api.deepseek.com`；
- wire model：`deepseek-v4-flash`；
- credential selector：`DEEPSEEK_API_KEY`。

若 route 没有显式配置 `total_timeout_seconds`，系统默认把单次
`timeout_seconds` 作为本次 provider 调用的总预算，避免 retries 把一次调用
扩展成多个完整 timeout 窗口。

反思 prompt 向 DeepSeek 提供：

- 当前目标 module 的规则；
- detection trace；
- attribution signal；
- rejected edit history；
- accepted edit summary；
- capacity guidance；
- HTC-TES type guidance；
- experience memory。

experience memory 里每条记录的 outcome 都是结构化的（封闭词表），而不是自由文本：

- `outcome`：`improved` / `directional` / `no_effect` / `regressed` /
  `rejected` / `not_attempted` / `runtime_invalid`；
- `outcome_stage`：`mutation` / `structural_gate` / `source_replay` /
  `batch_commit` / `detection_runtime`；
- `promotable_evidence`：这条记录是否可以被引用为“确实改善了检测”。
  source replay 和 label-free batch commit 恒为 `False`——由被编辑的
  detector 自己生成的 replay 不能自证正确性；
- `diagnostic_only`：仅遥测，不构成任何证据。

因此 `get_reflection_summary()` 可以按 stage 聚合，告诉反思“**尝试通常卡在哪一阶段**”，
而不只是“失败了”。

LLM 只能返回一个 `no_edit` 或一个单模块 edit。合法 edit 要满足：

- 操作是 `add_rule`、`modify_rule` 或 `remove_rule`；
- target module 位于 attribution/type 允许集合；
- rule 是轨迹可观察的 if-then 局部规则；
- 不包含 case ID、gold、URL、内部 CRS/coverage 字段或跨模块控制语句；
- `expected_effect` 明确预测一个 source replay 可观察的变化。

`expected_effect` 只是反思假设，不是通过凭证。它用于审查 LLM 是否提出了可验证目标；真正是否产生变化仍由 detector replay 决定。

### 3.5 structural/type gate

结构 gate 检查：

- module 是否存在且 active；
- operation 是否允许；
- 模块容量是否已满；
- immutable baseline rule 是否被修改；
- if-then 规则是否过长、条件是否过多、动作是否多步；
- forbidden content；
- feature description firewall；
- type confidence 和 routing provenance；
- semantic duplicate/merge 条件；
- module cooldown、总 edit budget 和 convergence window。

这一层只回答“edit 是否可安全应用”，不回答“edit 是否有效”。

### 3.6 shadow apply 与 source replay

通过结构 gate 后，edit 被应用到 shadow genome。随后必须对同一个 source case 再跑一次完整 detector：

```text
baseline result + baseline signal
        ↓ apply edit to shadow genome
shadow result + shadow signal
        ↓ assess_source_fitness
no_effect / directional_evidence / local_improvement /
strong_improvement / worse
```

fitness 检查包括：

- authority/runtime 是否仍然有效；
- CRS complete 等必要诊断信号是否丢失；
- responsible step 是否被新 edit 丢掉（保持结论但丢失定位同样算 regression）；
- source 是否从 triggered 变成 stable；
- stable case 是否被新 edit 变成 triggered；
- 目标 module local score 是否至少跨过 epsilon；
- 在 gold fitness 模式下，task score 是否朝正确方向变化；
- 是否出现新的 failure trigger。

同一个结果没有发生可测变化时，必须是 `no_effect`，不能因为规则文字“听起来合理”而接受。

#### localization 打分按正确性而非“有没有输出”

`earliest_origin_tiebreak` 的 local score 曾经只看 `responsible_step` 非空，
于是“永远输出一个 origin”的规则也会被记成改进。现在按质量打分：

- 没有 origin → `0.0`；
- 有 gold 且是 error case → 以 gold 的正确性为准（对 `1.0` / 错 `0.0`）；
- 否则用 detector 自己的定位质量：`complete` `1.0` >
  `not_applicable` `0.8` > `failed_temporal_fallback` `0.2`。

注意 `responsible_step_correct` 只在 gold error case 上可信——没有提供
gold steps 时它同样是 `False`，直接当成“定位错误”会把所有 self-supervised
的定位分数清零。

### 3.7 canary、batch commit 与 rollback

source replay 通过后，若存在 stable canary，会对其做抽样 replay。canary 要求：

- before/after authoritative state 相同；
- child runtime valid；
- before/after 都不触发 evolution；
- 不发生 provider/authority 异常。

现在可以通过 `canary_case_ids` 预注册独立 label-free canary，并在 evolution sample 开始前建立稳定锚点。这解决了“5 个 evolution case 全部触发，所以根本没有 stable case 可供 canary”的结构性死路，但没有降低 canary 的非回归要求。

最后：

- gold-assisted `strong_improvement/local_improvement` 可以进入 commit；
- label-free `directional_evidence` 只有在独立 canary 保持稳定时，才能作为 provisional candidate；
- `no_effect`、`worse`、runtime error、canary regression 都回滚；
- label-free 运行永远不自动宣称最终 champion 或 accuracy gain。

## 4. 本次 5/5 触发、0 接受的逐例解释

本次 replay 使用的是已保存的真实 detector 输出，并没有让每个 child genome 重新调用 detector；因此它是“真实轨迹输出的进化回放”，不是 fresh child-detector experiment。

| 案例 | 触发后路径 | 直接原因 | 判断 |
|---|---|---|---|
| BFCL/070 | add_rule → source replay | local score `0.3 → 0.3` | 正确拒绝 no-effect；回放输出未随 genome 改变 |
| BFCL/097 | add_rule → source replay | local score `0.3 → 0.3` | 正确拒绝 no-effect；同上 |
| BFCL/012 | add_rule → source replay | local score `0.3 → 0.3` | 正确拒绝 no-effect；同上 |
| BFCL/022 | type routing | route intersection empty | 机制死路；现已回退到 causal attribution route |
| BFCL/099 | mutation provider | SSL EOF | 传输失败；现有 fail-closed 行为正确，新增超时/计数可观测性 |

因此结论分层如下：

1. 不是三次 source fitness 门槛把有效 edit 误杀，而是回放 detector 返回了与 genome 无关的同一结果；
2. 其中一次是 HTC-TES 路由过硬，已修复为因果归因边界内的 fallback；
3. 其中一次是 provider transport，不是反思内容质量；
4. 真实 fresh run 还受到 appeal defender SSL EOF 影响，导致 authority invalid，不能进入进化；
5. 即使这五例中的 edit 真能关闭 source trigger，若没有独立 stable canary，label-free 模式也只能停留在 telemetry/provisional，不应直接成为 protected champion。现在通过 `canary_case_ids` 显式提供该锚点。

## 5. 当前关键指标解释

- `triggered_cases`：`AttributionSignal.should_evolve=True` 的 evolution cases 数；不是错误标签数量。
- `edits_accepted`：真正进入 champion 的 edit 数；不是 LLM 生成数。
- `edits_rejected`：经过 proposal/结构/replay 后被拒绝的 edit 数；route conflict 和 provider invalid 通常属于 skip，不一定计入该数。
- `source_replays`：candidate genome 在源案例上的 detector replay 次数。
- `canary_cases`：用于非回归检查的 detector 次数。
- `provider_calls`：历史兼容字段，表示 detector-call 计数。
- `detector_calls`：明确的 detector/source/canary/probe 调用数。
- `mutation_calls`：attribution/reflection/merge provider 调用数。
- `mutation_errors`：mutation provider 异常数。
- `mutation_cache_hits`：mutation provider 从全局 cache 命中的次数。
- `total_provider_calls`：`detector_calls + mutation_calls`，用于审计总调用量。
- `authority_valid_rate` / `invalid_count`：post-hoc 指标只对显式 authority 且 runtime-valid 的输出计算；缺失 authority 的旧 artifact 记为 invalid，不计入 TP/TN/FP/FN。
- `champion_generation`：只有 commit 后才增加；候选或 rejected edit 不增加。
- `final_champion_claim`：本框架默认保持 false，最终 holdout 需要外部评估授权。

## 6. 审查时应优先检查的证据

1. `evolution_checkpoint.json`：是否在异常时保留 genome、step、budget 和 rollback 状态；
2. 每个 step 的 `edit_proposal` 与 `expected_effect`：LLM 是否提出了可验证目标；
3. `fitness_check.source_before/source_after`：child 结果是否真的由 child genome 产生；
4. source/canary 的 `genome_hash` 与 detector artifact 路径：排除 replay harness 固定返回 baseline 的假进化；
5. provider route payload：确认 model、temperature、max_tokens 和 timeout 与配置一致；
6. authority failure reasons：区分核心 proof 失败、必要 appeal 失败和辅助 provider 降级；
7. `detector_calls`、`mutation_calls`、`mutation_errors`：不要再把 detector replay 数误认为全部 API 调用数。

实验 harness 的 replay 模式也有明确边界：默认 live 模式会重新运行真实 detector 和 child genome；固定 prediction artifact 只能通过 `FIVE_CASE_REPLAY_MODE=stored_compat` 进入兼容审计，而且缺失显式 authority 时会被阻断。
