# 自进化框架与检测器重设计提案（供审查）

状态：提案，未实施。诊断基于 2026-08-16 的代码与数据实证。

---

## 一、失效模型

结论先行：系统无效不是单一原因，而是**检测器层缺陷（D 系）与自进化框架缺陷
（F 系）的复合**。检测器的四个性质——判决法律与数据集约定耦合、无弃权的
强制二选一、系统性视野压缩、单轮噪声——恰好各自阻断自进化的一条必要前
提；而自进化框架又把动作空间限制在判决后处理。任何单侧修补（调门、换提
示词）都在错误的结构上优化。

### A. 检测器本体的缺陷

#### D1. 判决法律与数据集约定硬编码耦合 —— 跨数据集不一致的直接根源

census / verification / defense 三个全局 system prompt 中写死了 BFCL 风格
的约定规则，例如（`detector/court.py`）：

- census（L40-48）："Tool arguments are matched EXACTLY ... '2.0000'
  where the task requires '2.0' ... is a deviation candidate"；
- verification（L96-105）："an observation of '2.0' echoed as '2.0000' is
  a deviation **even when the task text mentions decimal precision**"；
- defense（L150-158）：同时又写 "micro-differences are normal numerical
  presentation, not hallucinations"（仅限 REPORTED number）。

即：**同一个数值改写，在工具参数里判 error、在报告里判 clean**——这是
BFCL 的基准约定，不是幻觉的普遍法律。检测器在七个源数据集上用同一套
拼贴规则判决，跨源行为必然不一致。

分源实证（两个 100 例 run，p1d = Phase 1-D 冻结 fresh 抽样）：

| 源 | random100 acc | fresh acc | FP 变化 |
|---|---|---|---|
| BFCL | .680 | .500 | 3→6 |
| Camel | .824 | .500 | 2→4 |
| OpenDeepSearch | .889 | .688 | 0→2 |
| Octotools | .778 | .857 | 2→1 |
| SmolAgents | .812 | .833 | 1→2 |
| Magentic_One | .714 | .643 | 3→3 |
| OpenManus | .700 | .643 | 1→3 |
| **总体** | **.760** | **.636** | **12→21** |

同一 run 内跨源方差 .50-.89；跨 run 全面塌缩（FP 翻倍集中在 BFCL /
Camel）。两个抽样本身不同构（gold+ 占比 62% vs 50%，源组成不同），部分
差距来自分布偏移——但 FP 在约定敏感源上翻倍的方向性，指向 D1 而非纯抽
样噪声。

#### D2. 无弃权的强制二选一 —— 不确定性被挤进 clean

判决规则（`court.py::adjudicate`）：census 零候选 → clean；候选无一幸存
→ clean。verification prompt 进一步规定 "Uncertainty is not a reason to
reject: if the evidence cannot excuse the deviation, support it"——**不
确定默认定罪**，defense 再把一部分翻回 clean。整条管线没有"证据不足以
判断"的第三态（`unresolved` 只覆盖传输/契约失败，不覆盖语义不足）。

后果：FN 与 clean 的边界由单轮 provider 噪声决定；错误的责任无法归属
（是没看见、看见了证据不够、还是辩护翻案？判决输出不含此信息）。

#### D3. 发现视野系统性压缩且不可观测 —— 结构性 FN 源

`packet.py` 常量：census 首扫用 index view——**非尾部事件只有 300 字符
预览，工具观察 120 字符**（`INDEX_EVENT_PREVIEW=300`、
`INDEX_TOOL_PREVIEW=120`，尾部仅 8 个事件全量）。

二次扫描（second_pass）仅在**零候选**时触发（`court.py:839-892`），
即：只要首扫在轨迹尾部找到一个候选，中间内容里的幻觉就保持不可见，且
没有任何字段记录这一点——实测两个 run 的 `evidence_coverage.census` 全
部为 "complete"（index 压缩是设计常态，不计 truncated）。真实 agent 轨
迹的中间事件几乎必然超过 300 字符。

这是 FN 的一个结构性来源，且**进化侧看不见**：worksheet 无法区分
"能力缺口"与"根本没看见"。实测规模（两 run 200 例 packet 全量渲染字符
数）：中位数仅 5.6K、98% 的案例 ≤100K 字符、超 500K 的 2 例——对绝大
多数案例，压缩是零收益的纯信息损失，对策为全量优先（A5）。

#### D4. 单轮判决，无自一致性 —— 判决噪声直接进结果

census / verification / defense 各一次调用（温度 0，但无多数投票）。
p1d 相对 random100：census 候选均值 2.36→1.86、最大值 13→7——同一
prompt 在两个批次上发现行为明显漂移；结合 FP 翻倍与 acc 塌缩，劣化主要
发生在"候选→定罪"的裁决环节，正是噪声最敏感的环节。

#### D5. 残渣与死代码

- `_observation_restatement_check`（`court.py:425-504`）：针对
  BFCL/025 单一案例族的确定性增补，实测两个 run 200 例**零触发**
  （LLM 自己的近似措辞先命中了去重条件）——数据集专用补丁，无效且是
  D1 的一部分；
- `_StageAudit` 类**重复定义两次**（`court.py:171` 与 `:186`），第一个
  定义立即被第二个覆盖——未清理的补丁痕迹。

### B. 自进化框架的缺陷

#### F0. 进化从未接到检测器本体（最深根因）

- `detector/skills.py`：genome 是 "OPAQUE payloads ... defers all
  execution semantics"；
- `detector/api.py:54-58`：`detect` 对 genome 只记 hash 进 provenance；
- `detector/court.py:703`：`policy_evaluation` 硬编码空占位。

**"部署一个 genome" = 写文件 + 记 hash**。检测器真实行为在任何 promote
之后一字不变；全部"效果"活在 evaluator 侧的离线回放模拟器里。进化产出
与检测器之间没有执行面。

#### F1. 动作空间单调 —— FN 在代数上不可表达

`evolution/policy.py:144-153`：动作只有 `withhold_conviction` 与
`require_corroborating_content_error`，`replay_case` 只能
`verified_error → verified_clean`。配套制度写死三处：proposal 提示词
"NOT actionable"、cycle 模块注释 "non-actionable context"、worksheet 的
misses 只有 case_id。

#### F2. 进化发生在离错误根源最远的层

FP 根源在 verification/defense（错误支持/辩护失败），FN 根源在
census/verification（D3 视野、证据表示不足）——判决后处理层离两者都最
远。冻结的 `gap_taxonomy.py:78-85` 规划了三条通道
（observation→l1_candidate、verification→evidence_skill、
policy→l2_policy），只落地了 l2_policy。Phase 1-C.5 结论"残余 = 证据表
示层瓶颈（basis 信号进 Phase 2）"从未兑现。

#### F3. 绝对门 + 分布漂移 = 永不晋升

晋升门：零 TP 损失（累积）+ FN(vs baseline)≤1 + 严格改进 + 逐案例归因
剪枝。实证：oracle 32 配置 TP loss=0 → 冻结 P2-P5 → fresh 上幸存证书
**75+27 全部 `content_contradiction`**、P2-P5 靶类曝光 0 → 零触发 → FP
21→21 → NOT promoted。类别键控策略在 random100 上过拟合；fresh FP 的
主体（content 类）恰是策略层不能压的主力定罪类。

#### F4. FN 被循环论证排除

README 学习口径："label-free 运行没有任何信号能判定 FN，因此不存在 FN
触发的修复路径"——用**运行时**限制（正确）推出**进化侧**不可为（错
误）：进化本来就是评测侧 gold 闭环。

### C. 检测器缺陷如何阻断自进化（两半的连接）

1. **双重过拟合环**：三个 prompt 的约定规则在 random100 分布上调出
   （0.76）→ policy 又在同一分布的证书类别上学（类别分布本身是 prompt
   的产物）→ fresh 上类别塌缩（全 content_contradiction）+ acc 塌到
   .64。进化信号源与检测器调参源同分布，学到的只是 prompt 工程的镜像。
2. **信号污染**：FN 实为三种异质来源的混合——视野压缩（D3）、证据表示
   不足（F2）、裁决翻转（D2/D4）——判决输出不含归因信息，worksheet 无
   从分诊，任何修复通道都无从设计。
3. **噪声逼出绝对门**：单轮判决不可重复 → 进化的"改进"无法与噪声区
   分 → 门只能零容忍（F3）→ 永不晋升。门太严不是失误，是检测器噪声逼
   出的防御；正确的修法是降噪声，不是松门。
4. **分布敏感使迁移失效**：约定耦合（D1）使任何在进化池上学到的边界在
   新分布上不可信（P2-P5 零触发）。

---

## 二、推翻与保留

### 推翻（8 项）

| # | 原决定 | 推翻理由 | 替代 |
|---|---|---|---|
| 1 | 数据集约定写死进全局 prompt | D1：跨源不一致的根源 | 任务约定层（§3.1） |
| 2 | 判决强制二选一，无弃权 | D2：噪声进判决 | 三态判决 |
| 3 | 进化只发生在 L2 判决策略层 | F1/F2 | 三通道（§3.2） |
| 4 | genome 是不透明载荷，detector 零执行语义 | F0：没有执行面就没有真闭环 | detector 消费执行面 |
| 5 | 纯离线回放，零 provider 成本 | FN 修复本质是反事实 | 预算化定向在线重放 |
| 6 | 零 TP 损失绝对门 | F3：噪声+漂移下不动点=永不晋升 | 三层门（§3.3） |
| 7 | "FN 是评测侧职责，不可进化" | F4：循环论证 | misses 一级化 + 归因分诊 |
| 8 | 面向单一案例族的确定性 prompt 补丁（BFCL exact-match 段、restatement 增补） | D1/D5：零触发死代码 + 约定泄漏 | 约定层 + 删除 |

### 保留（6 项）

1. **L2 内部单调性**（withhold-only）：判决层只能撤销定罪是好性质；
2. **gold 隔离防火墙**（`EVALUATOR_ONLY_FIELDS`）与 label-free 运行时；
3. **闭卷词汇 + 机械验证纪律**（推广到新通道与新词表）；
4. **library 台账 / genome hash / 冻结 split**（393 进化 + 300 终审，
   forced_in 污染隔离已就绪）；
5. **F1-F6 对照纪律**：条件门控路由、unguided control、决定性复核；
6. **fail-closed 传输/契约处理**（`provider.py`、contract repair、
   批次切分）——工程质量良好，保留。

---

## 三、新架构

总原则：**先修检测器的信号质量，再接进化的执行面；进化动证据生成层，
判决层保持单调。**

```
              ┌──────────────── detector（改造）────────────────┐
              │  census ── 约定层路由（任务特征 → 约定集）        │
              │  verification ── 结构化谓词 + 三态判决            │
              │  defense ── 边缘证书自一致性复查                 │
              │  adjudication ── 单调判决 + FN 归因事实落盘       │
              └──────────────────┬───────────────────────────────┘
                                 │ Detection Truth（含归因）
              ┌──────────────────▼───────────── 评测侧（gold 闭环）─┐
              │ batch → worksheet（misses 一级化+分诊） → 三通道提案  │
              │   → G1 结构门 → G2 统计门 → 候选池（攒 K cycle）      │
              │   → G3 终审（冻结 300 例，唯一晋升权）               │
              │   → genome v{N+1} + 试用期监控 ── 回归即 retire       │
              └──────────────────────────────────────────────────────┘
detector 执行面（显式参数）：census 面 ← 通道 A；verification 面 ← 通道 B；
判决面 ← 通道 C
```

### 3.1 检测器重设计（前置，M0-M1）

**A1. 任务约定层（convention overlay）**。把 D1 的数据集约定从全局
prompt 抽出为显式约定集（闭卷词表，代码常量）：

- `tool_argument_exact_match`：工具参数是精确接口（BFCL 类任务特征：
  存在参数化工具调用且任务给出参数规范）；
- `report_presentation_tolerance`：报告层数值呈现容差；
- 每案例按任务特征**机械路由**适用约定集（不是按源数据集名字——按可
  观察特征，保证对未见源可泛化），路由结果落盘进 Detection Truth。

prompt 只保留通用幻觉法律（对比观察、对比用户陈述、对比任务要求），
所有 exact-match / 容差规则走约定层注入。同时删除
`_observation_restatement_check` 与重复的 `_StageAudit` 定义。

**A2. 三态判决**。verification 的 verdict 闭卷扩为
`supported / rejected / insufficient_evidence`；court 对 insufficient 的
处理契约化：证书进入"低置信"账本，案例终态不变（避免用弃权逃避判
决），但**归因事实落盘**。这直接服务 FN 分诊（A3），并给通道 B 提供
谓词级训练信号。

**A3. FN 归因管道**。miss 案例（gold=true, state=clean）在 Detection
Truth 中必须携带分诊事实：

- `view_degraded`：census 视野压缩发生（见 A5）→ 基础设施问题，不进
  能力通道；
- `census_miss`：视野完整但候选未发现 → 通道 A 输入；
- `evidence_insufficient`：候选被 insufficient 判 → 通道 B 输入；
- `defense_overturn` / `verification_reject`：裁决翻转 → 复查通道
  （A4）与通道 B/C 输入。

worksheet 的 misses 从"只有 case_id 的记录"升级为带归因的一级输入。

**A4. 边缘复查（判决去噪）**。对"单票差"边缘证书（defense 单票 overturn
的 supported 证书、verification 接近对半的批次）做 k=3 自一致性复查，
多数定局。只复查边缘，成本 ≈ 每案例 +0-2 次调用。这是 G2 统计门可以
放宽的前提：判决可重复，改进才可与噪声区分。

**A5. 视野全量化与可观测**。census 首扫默认**全量渲染**（98% 案例
≤100K 字符，provider 上下文容量足够；预算线按实测容量探测设定），
`second_pass` 机制整体删除——首扫即全量，零候选即真 clean。超限案例
（实测 ~2%）降级到 index+tail 分层视图，降级状态如实落盘
（`evidence_coverage.census: degraded` + 压缩率），进化侧必须能看见
"该案例的发现视野受过压缩"。

### 3.2 自进化三通道（对齐 gap_taxonomy 冻结蓝图）

**通道 A：census_probe（observation→l1_candidate，修 FN 召回）**

- 提取：对每个 `census_miss` 案例，一次 provider 调用提取反事实检查义
  务——"哪个可观察特征若被检查会产生会被支持的证书"，类级表述；
- 机械验证：闭卷义务词表、≥2 例同机制支持、禁 case id、必须绑定任务
  形态特征（trigger_pattern）；
- 动作：`census_probe = {obligation_class, trigger_pattern,
  check_directive}`，注入 census prompt 条件段（仅 trigger 匹配时路
  由）；
- 验证（定向在线重放）：miss 案例 + trigger 匹配的 clean 抽样 + TP
  sentinel；同批 unguided control 双跑，增益必须归因探针。

**通道 B：evidence_predicate（verification→evidence_skill，双向）**

- verification 的支持判别从自由文本 rationale 升级为结构化谓词闭卷表
  （初版 8-10 个，对齐冻结 gap_names：`cited_span_present`、
  `derivation_recomputed`、`source_priority_resolved`...），证书携带谓
  词向量；
- 连带收益：binder 从"7 类文本判断"变为"谓词向量→类"机械映射，消解
  binder 漂移与 unclassified 弃权；
- 进化动作：`corroboration_rule`（如"自我报告派生必须重推导才可支
  持"）。新谓词入表是代码审查级操作（词表冻结纪律）。

**通道 C：L2 policy（保留，降级为快速止血）**

现有 withhold / corroborate 原样保留，定位为"已确认类别误报的止血"。
执行面：`court.py` 的 `policy_evaluation` 从空占位变为真实消费。

### 3.3 三层门

| 门 | 内容 | 成本 | 权限 |
|---|---|---|---|
| G1 结构 | 现有 validate 纪律推广到三通道词表 | 零 | 淘汰不合格提案 |
| G2 统计 | 累积证据配对比较：TP 损失 ≤ ceil(2%×TP) 或 McNemar 单侧 p<0.05；通道 A/B 定向重放，通道 C 离线 | 每 cycle ≈ 2×miss 数次调用 | 入候选池 |
| G3 终审 | 冻结 300 例（phase2_v1，与进化池互斥）全管线双跑，配对比较，Pareto 支配或检验显著 | 600 次检测/次（20 并发 ≈ 1 小时级） | **唯一晋升权** |

G3 可攒 K 个 cycle 的候选池一起终审（成本分摊 + 降低多重比较假阳率）。

### 3.4 cycle 编排与防过拟合

- 每 cycle：batch → 三通道提案 → G1 → G2 → 候选池；
- 候选池达阈值或满 K cycle → G3 → 晋升 + 试用期；
- 试用期回归即 retire（library events 已支持）；
- 探针触发绑定任务特征、≥2 例支持、同时激活上限 3（防 prompt 膨胀）；
- G3 的 300 例对进化侧不可见——把"分布漂移"从敌人变成裁判。

---

## 四、与被推翻的旧 L1 生成性 opcode 的本质区别

| 维度 | 旧 L1（已推翻） | 新通道 A |
|---|---|---|
| 注入 | 无条件 prose steering | trigger_pattern 条件路由 |
| 验证 | 无对照、无决定性复核 | unguided control + 定向重放 |
| 词汇 | 开放 prose | 闭卷义务词表 + 机械验证 |
| 门 | 无 | G1/G2/G3 |
| 淘汰 | 无 | 试用期 + retire |

旧 L1 死于假闭环；新通道每个环节都有可证伪复核点。A1 的约定层同理：
它不是把 BFCL 规则换个地方藏，而是把"规则何时适用"从 prompt 耦合变为
显式、可路由、可泛化的声明。

---

## 五、实施切分

| 里程碑 | 内容 | 完成的可证伪标准 |
|---|---|---|
| M0 | A1 约定层 + 删 D5 死代码；A5 视野升级与可观测 | 同一批次重跑，分源 acc 方差收敛（跨源极差缩小）；`evidence_coverage` 能标记压缩 |
| M1 | A2 三态判决 + A3 FN 归因管道；worksheet misses 一级化 | miss 案例的归因事实非空且三类分布可读 |
| M2 | 通道 A 提取器 + 机械门 + 定向重放验证器 + census 执行面；A4 边缘复查 | 一个 census_miss 案例被探针修复的端到端证据（含对照组） |
| M3 | G2 统计门 + G3 终审接线 + 试用期 | 一次完整 G3 裁决记录（晋升或否决均可） |
| M4 | 通道 B 谓词表 + 证书谓词向量 + binder 机械映射 | binder 一致率 / unclassified 率对比基线（92% / 现值） |
| M5 | 通道 C 执行面接入 court.py | `policy_evaluation.fired` 非空且与 evaluator 重放一致 |

M0-M1 是信号质量前置（不做，进化仍学噪声）；M2-M3 是最小可判决闭环；
M4 是 FP/FN 双向收益主体；M5 收尾。

---

## 六、预期效果与可证伪判据

- **M0**：分源 acc 极差（现 .36：.500-.857）显著收敛；若不收敛，D1 的
  约定路由假设错误，需要重新归因跨源方差；
- **通道 A** 打开 FN 修复：census_miss 家族应出现非零回收；两轮 G3 后
  为 0 则通道 A 判失败；
- **通道 B** 打开 content 阻塞的 FP：谓词化后 content 类错误支持应可判
  别；无改善则通道 B 判失败；
- **A4 + G2**：判决重跑一致率（同案例双跑终态一致比例）成为常规度量，
  一致率不升则统计门无意义，回退绝对门；
- **门的健康度**：候选池不再长期空转；若 G2 通过率持续为 0 而 G1 拒绝
  占主导，是提取器问题而非门问题，分开度量。

每个判据都直接检验"进化是否真的作用于检测器本体、信号是否真的比噪声
强"——这是现状与目标之间唯一的本质区别。
