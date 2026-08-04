# 交接文档：从 Skill-only 进化到检测器全层可进化（v6 方向）

> 交接时间：2026-08-04 · 交接原因：v5.9 系列补丁式迭代触及结构性天花板，需范式级重构。
> 本文是唯一事实来源；下一会话以本文 + HANDOFF_PROMPT_V6.md 启动。

## 1. 项目现状快照

- 代码：`main` 分支，本地领先 origin/main 12 个 commit（含 v5.9a–e 系列），未 push。
- 测试：301 个单元测试全部通过。
- Champion genome：`af67ab00`（`experiments/protocols/trial_10case_v59c_output/champion_genome.json`），含 2 条被接受规则（candidate_trigger / evidence_obligation）。
- 模型约束：仅允许 DeepSeek-V4-Flash / V4-Pro；不触碰任何检测阈值（导师明确要求）。
- 已用案例账本（后续实验必须排除）：`experiments/protocols/` 下全部 `*.jsonl`（210 个 case_id，含 validation_100 预留集）。原始池 `datasets/raw/agenthallu/AgentHallu` 共 693 例。

## 2. 数据证据：六轮运行全漏斗（R1–R6，约 50 步）

| 结局 | 数量 |
|---|---|
| skip_no_edit | 19 |
| reject（fitness worse / no_effect / conflict / replay 崩溃） | 4 / 4 / 2 / 2 |
| accept_batch | **2** |
| skip_stable / runtime_invalid | 12 / 2 |

关键分布：

- 触发案例初始状态：**unresolved 25/36（69%）**，verified_clean 6，verified_error 5。
- no_edit 案例初始状态：**unresolved 12/19（63%）**。
- 14 个非接受提案中 8 个 no-effect/worse：提案即使路由正确，也大多无法移动检测行为。

## 3. 根因分析（为什么一直在迭代）

### 根因 1：触发面与可编辑面的结构性错配（核心矛盾）

unresolved 判决由**确定性 proof court** 产生（`src/sehd_v6/core/paca_tcl/contract_v1.py` 的
`adjudicate_proof_court`：consensus gate、materiality/commitment 检查、coverage 聚合）；
而当前 genome 的 8 个模块只能通过自然语言规则塑造各 provider 的局部输出。
实测的 unresolved 失败形态——consensus_gate_blocked、boundary_path 已 verified_clean
但终态 unresolved、defense 空壳 complete——**没有任何 provider 规则能修复**。

v5.8–v5.9e 的迭代轨迹是在给这类"不可拥有"的失败反复找 owner：仲裁从 2 通道膨胀到
4 通道情景式 if-else。把不可修复的失败路由给某个模块，结局必然是 no_edit 或
replay no-effect——这不是路由没调好，是这类失败不属于当前可编辑面。

### 根因 2：控制面太弱

一条自然语言规则只是 provider prompt 的一段文本；provider 行为由整个 prompt +
结构化 schema 驱动。14 个提案 8 个无效/有害证明："规则文本 → provider 行为"
耦合太弱，无法可靠"改变检测结果"。

### 根因 3：迭代过程退化成补丁链

诊断管线上叠着 5 层特殊处理（ownership 重映射、空断言正则、hint 可编辑性标注、
editable flag、机械修复），每层都是回应上一轮的"LLM 又困惑于 X"。单个补丁都合理，
合起来是熵增：每轮修表面症状，不修结构，所以永远有下一轮。

## 4. 核心问题的回答：裁决级失败能否自进化？

**能，但必须扩展"可进化工件"的定义——自进化不应局限于 Skill（provider 规则）。**

关键架构事实（已核实）：

1. proof court 是**确定性契约层**，其裁决由策略参数驱动：
   `apply_consensus_gate_flag`、materiality/commitment 检查、
   boundary finding（case_boundary_finding → NEUTRAL_EVIDENCE）的消费方式。
   裁决级僵局正是这些策略的产物。
2. `detect_record(adjudicator=...)` 已有裁决器注入点；
   `skill_integration` 已实现 genome → provider 的注入机制。
3. abstention_rule 模块本来就是"裁决边缘"进化的雏形：它的规则通过 boundary finding
   进入 proof court。这证明裁决层可以有受控的进化通道。

**结论：新增"裁决策略基因组"（adjudication policy genome）作为第二进化层。**
与 provider 规则的本质区别：策略工件由**确定性求值器**机械执行（不是 LLM 解释
prompt 文本），"工件 → 行为"耦合是机械保证的，直接解决根因 2。

## 5. v6 设计要点

### 5.1 两层可进化面

| 层 | 工件 | 执行方式 | 对应失败类别 |
|---|---|---|---|
| L1 provider 规则（现有 8 模块） | 自然语言 if-then 规则 | 注入 provider prompt | artifact 级：某 provider 缺失/畸形证书、候选、证据字段 |
| L2 裁决策略基因组（新增） | 封闭词表的结构化策略项 | proof court 确定性求值 | 裁决级：consensus gate 僵局、空壳 complete、boundary 与终态不一致 |

### 5.2 触发归因：从失败形态直接分类，废除情景式仲裁

归因只回答一个问题：**失败发生在哪一层、哪个工件**：

- 某 provider 输出缺失/畸形 → L1，owner = 该工件所属模块（diagnostic_gap 已是这个形状，删除覆盖它的静态 error-type 映射与仲裁通道堆栈）。
- consensus_gate_blocked 非空 → L2，工件 = consensus gate 策略。
- defense/prosecution coverage=complete 但证书数为 0（空壳 complete）→ L2，工件 = coverage 有效性策略。
- boundary_path 状态与终态矛盾 → L2，工件 = boundary 消费策略。
- 都不满足 → `detector_layer_out_of_scope`（真·检测器 bug）或 `no_actionable`，只留遥测，不进反思。

### 5.3 L2 策略工件的表示（关键设计约束）

- **封闭词表**：每个策略项 = （可观察前提条件, 枚举动作之一），条件只引用
  proof court 输入中已有的可寻址事实（证书计数、coverage 状态、blocked ids）；
- **确定性求值**：proof court 在契约求值时应用策略项集合，语义由代码固定，
  LLM 只负责"提出"，不负责"解释执行"；
- **同一套门控**：结构 gate（词表/容量）→ source replay（策略项必须改变
  裁决输出）→ canary → gold fitness。门控层不变，只是工件类型扩展；
- **可逆性**：策略项集合构成独立 genome 分区，回滚/champion 机制复用现有实现。

### 5.4 补丁栈清理清单（重构时删除或吸收）

- `_owner_arbitration` 的 4 个情景通道（process_channel / verdict_evidence_gap /
  unresolved_balance 及其变体）→ 由 5.2 的统一失败分类取代；
- `sanitize_diagnosis` 的空断言正则、ownership 重映射 → L1 归因直接来自工件，
  诊断不再需要猜 owner；
- 诊断 prompt 的 hint 可编辑性标注、editable flag → 归因输出即是允许集合；
- `repair_rule_for_structural_reason` 机械修复 → 保留（它修的是生成语法，跨层有效）；
- 诊断-干预二级管线 → 保留其"诊断与生成分离"结构，但诊断输出改为
  （失败层, 工件, 证据引用）三元组，不再是"owner 猜测"。

### 5.5 已知检测器层缺陷（C 类：修代码，不进进化）

1. boundary_path=verified_clean 但终态 unresolved 的聚合不一致；
2. defense coverage=complete 且证书数为 0 的"空壳 complete"语义；
3. consensus gate 在 appeal 后对 initial vs combined prosecution 的阻断条件过严
   （多例 unresolved 的直接来源）。
   这三项应先做缺陷确认（离线复现），确认是 bug 则直接修 proof court 代码；
   它们是策略问题则归入 L2 进化面。逐项判定，不预设。

## 6. 纪律与约束（必须延续）

- 任务执行前需用户审批：方案先讨论，批准后再动代码/实验。
- 不硬调阈值；裁决策略进化是结构化决策，不是分数门槛调节（导师意见）。
- 每轮实验案例必须全新（排除账本中全部 case_id），防小样本过拟合。
- 模型仅限 DeepSeek-V4-Flash/Pro；mutation purpose 需纳入非思考模式集合。
- 实验产物目录与脚本按现有惯例放 `experiments/`（gitignored）。

## 7. 下一步清单（供下一会话排期）

1. 与用户确认 5.1–5.3 的 L2 设计（策略词表的初版枚举）。
2. 对 5.5 三项缺陷做离线复现与定性（bug vs 策略问题）。
3. 实现 L2 最小闭环：策略 genome 分区 + proof court 求值钩子 + 失败分类归因。
4. 拆除 5.4 补丁栈，归因路径单一化。
5. 离线桩测试覆盖：失败分类、策略求值、门控复用。
6. 全新 8 例小验证（含 unresolved 僵局形态），验收标准：裁决级触发案例
   产出可 replay 验证的策略提案，且 no_edit 大类显著下降。
7. 通过后重启 50 例正式实验（数据集构建器 `build_full50_dataset.py` 可复用，
   注意重新排除账本）。
