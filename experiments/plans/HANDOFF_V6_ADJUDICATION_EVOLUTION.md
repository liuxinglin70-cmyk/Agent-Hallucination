# 交接文档：从 Skill-only 进化到检测器全层可进化（v6 方向）

> 交接时间：2026-08-04 · 交接原因：v5.9 系列补丁式迭代触及结构性天花板，需范式级重构。
> 本文是唯一事实来源；下一会话以本文 + HANDOFF_PROMPT_V6.md 启动。

## 1. 项目现状快照

- 代码：`main` 分支，v5.9e 基线（commit 4b50e8a）之后为 v6 重构系列提交（自
  f78123e 起），未 push。
- 测试：402 个单元测试全部通过。
- Champion genome：af67ab00（含 2 条被接受规则 candidate_trigger /
  evidence_obligation；原产物文件已随仓库清理移除，内容可从 commit 4b50e8a
  的 git 历史查看）。
- 模型约束：仅允许 DeepSeek-V4-Flash / V4-Pro；不触碰任何检测阈值（导师明确要求）。
- 已用案例账本（后续实验必须排除）：`experiments/protocols/used_case_ledger.txt`
  （226 个 case_id，含 validation_100 预留集，已从历史 jsonl 提取并纳入 git
  跟踪）。原始池 `datasets/raw/agenthallu/AgentHallu` 共 693 例。

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

### 5.4 补丁栈清理清单（已完成部分标注）

- `_owner_arbitration` 的 4 个情景通道 → [已删除] 由 `failure_classifier` 取代；
- `sanitize_diagnosis` 的空断言正则、ownership 重映射 → [保留] 它是 LLM 诊断输出的
  结构门而非 owner 猜测，归因已不再依赖它；
- 诊断 prompt 的 hint 可编辑性标注、editable flag → [已删除] 归因输出即允许集合；
- `repair_rule_for_structural_reason` 机械修复 → [保留]（修生成语法，跨层有效）；
- 诊断-干预二级管线 → [保留] 诊断与生成分离结构，归因输出已是（层, 工件, 证据）。

### 5.5 已知检测器层缺陷定性结论（已完成离线复现）

1. boundary_path=verified_clean 但终态 unresolved（实例：BFCL/029，trial_10case_v59c）：
   **结构性策略缺口，非 bug**。boundary finding 只进 provider prompt 上下文
   （proof_engine.py 三处 case_boundary_finding），adjudicate_proof_court 的终态
   聚合无 boundary 消费逻辑。硬修等于硬编码裁决优先级，故归入 L2
   boundary_consumption_policy 进化面（谓词已就绪）。
2. defense coverage=complete 且证书数为 0 的空壳 complete（实例：BFCL/025，
   full50_v59_r2）：**策略问题**。coverage_complete 只看声明值与审计失败数，
   不检查证书非零；归入 L2 coverage_validity_policy 进化面，proof court
   策略钩子已可确定性处置（downgrade/invalidate coverage）。
3. consensus gate 在 appeal 后的阻断：**证据不足以判定 bug**。偏离点已定位：
   adjudicate_proof_court 把 initial 支持集构造为 surviving 中前缀 initial_ 的
   证书，而 apply_consensus_gate 的文档语义是"initial 曾独立支持"（被 defense
   击败的初始证书不再计入）。该收紧可能是有意的抗 FP 设计，语义取舍属策略
   决策：不擅自改门语义，作为 consensus_gate_policy 首批进化候选待实验定夺。

## 6. 纪律与约束（必须延续）

- 任务执行前需用户审批：方案先讨论，批准后再动代码/实验。
- 不硬调阈值；裁决策略进化是结构化决策，不是分数门槛调节（导师意见）。
- 每轮实验案例必须全新（排除账本中全部 case_id），防小样本过拟合。
- 模型仅限 DeepSeek-V4-Flash/Pro；mutation purpose 需纳入非思考模式集合。
- 实验产物目录与脚本按现有惯例放 `experiments/`（gitignored）。

## 7. 下一步清单（v6 实施进度已更新）

已完成（全部测试绿，逐模块 git 提交，账本见 git log f78123e 起）：

- [x] 共享契约 `src/sehd_v6/core/bccse/v6_contracts.py`：层/工件/谓词/动作封闭词表、
      AdjudicationFacts、PolicyItem、FailureAttribution。
- [x] L2 基因组分区 `policy_genome.py`（容量/去重/保护/champion 兼容）。
- [x] 确定性求值器 `policy_evaluator.py`（fail-closed、coverage 只降不升）。
- [x] proof court 策略钩子：`adjudicate_proof_court(policy_items=, boundary_path_state=)`，
      默认惰性，按法庭完整终态规则重算。
- [x] 失败分类器 `failure_classifier.py` + 归因集成：`_owner_arbitration` 4 通道
      补丁栈已删除（净删 158 行），非 L1 失败（L2 与检测器层异常）进
      `evolution_audit["non_l1_failure_attributions"]` 遥测。
- [x] 模块章程 `module_charter.py`（8 模块英文章程，含跨层引导）。
- [x] L1 槽位规则契约 `rule_contract.py`（受控英文 if-then，槽位校验+确定性渲染）。
- [x] 5.5 三项缺陷离线定性（结论见 5.5）。
- [x] 槽位模板落地生成路径：反思 prompt 产出 condition/action/evidence_anchor 槽位，
      规则文本由 render_rule 确定性渲染；结构 gate 切到槽位契约（文本路径仅服务
      remove_rule 与历史 checkpoint 回放）；editable 标注补丁栈已删；章程全文注入
      反思 prompt（取代逐模块 contract 行）。
- [x] L2 策略提案闭环：`policy_reflection.py`（词表动态生成的反思 prompt + 解析）；
      主循环对 L2 归因产出 PolicyEdit → validate/apply → source replay（必须 fired
      且移动可观察输出）→ gold 极性门（分数严格上升，无连续阈值）→ 接受/回滚；
      L2 案例不再进入 L1 变异路径；checkpoint 持久化 policy_genome 可 resume；
      gate_decision 新增 accept_policy / reject_policy_no_effect /
      reject_policy_fitness / skip_no_policy；审计进 evolution_audit["l2_policy_audit"]。
- [x] 检测路径透传：adjudicate_case(policy_items=) 在法庭收敛点用同一证据束
      重裁（final_court_kwargs 单一来源），默认惰性。

待办：

1. 全新 8 例小验证（含 unresolved 僵局形态），验收标准：裁决级触发案例产出
   可 replay 验证的策略提案、L1 no_edit 大类显著下降；案例必须排除
   `used_case_ledger.txt` 全部 case_id（数据集构建脚本需重新编写，旧构建脚本
   已随仓库清理移除）。
2. 通过后重启 50 例正式实验（重新编写构建器，重新排除账本）。
