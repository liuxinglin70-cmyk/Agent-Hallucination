# 检测器与自进化框架：修复-优化-验证迭代任务文档

状态：**Round 1.5 底层重诊断完成——主导失效模式是判决噪声地板，
不是机制缺陷**；稳定性测量、配对重放门与三阶段执行面已落地
（2026-08-16，见 §零·五）。Round 2 从「跑通 evolve → G2 → 部署 →
配对验证」起步。
范围：**持续迭代地修复、优化检测器与自进化框架，每轮在随机重抽的
100 例数据集上验证有效性，按验证反馈驱动下一轮**。不做完整实验、
不追求 SOTA 数字。

配套文档：`SELF_EVOLUTION_REDESIGN_PROPOSAL.md`（完整论证与代码/数据
证据）。本文档自包含问题全景与修复方向。

---

## 零、Round 1 落地记录与设计修订（2026-08-16）

### 已落地的检测器修复（B1-B4 全部）

- **约定层**（`detector/conventions.py`）：三个全局 prompt 的 BFCL 硬
  编码全部抽出为闭卷约定集，按机械包特征路由注入三阶段；约定拆为
  `tool_argument_exact_match`（参数精确）、`observation_restatement_
  fidelity`（参数复述精确）、`report_presentation_tolerance`（报告
  复述容差）三条，双向边界。
- **视野全量化**：census 默认全量渲染（400K/200K/100K 预算），超限
  降级到 index+tail 且 `degraded` 如实落盘；second_pass 机制删除；
  死代码（`_observation_restatement_check`、重复 `_StageAudit`）删除。
- **证据扩展权**：窗口 ±1 → ±2。
- **三态判决**：verification 增 `insufficient_evidence`；cited 观察
  物理超预算时该候选机械弃权（不再是 unresolved 整案报废）；
  `miss_triage_facts` 分诊事实落盘。
- **FN 归因管道**：`attribute_miss` 机械分诊 view_degraded /
  census_miss / evidence_insufficient / defense_overturn /
  verification_reject；worksheet misses 一级化带归因。

**Round 1 基线（fresh 100 例，20 并发）**：acc 0.66，TP 42 / FP 16 /
FN 18 / TN 24，0 unresolved。FN 归因（进化集 13 例）：defense_overturn
5 / census_miss 4 / verification_reject 4。

### 通道 A 证伪与重设计（本轮最重要的实证结论）

两轮 G2 定向重放均拒绝，证据链：

1. **归因修正前的假信号**：4 个 "sentinel lost" 全部是 provider 瞬时
   失败（unresolved），不是探针损害——修复 transport retry 后消失；
2. **去污染后的真实信号**：修复 1/4 目标、误伤 2 个 clean——若部署
   净效果 +1 TP / +2 FP，负收益；
3. **结构性根因**：收益池（census_miss）每轮仅 ~4 例且只占 FN 31%，
   风险池（宽 trigger 命中的 clean）11+ 例；盈亏平衡需修复率/误伤率
   > 2.75，实测 0.5；
4. **动作形态根因**：探针词表只有"何时检查"没有"何时不算"——单向
   放大器必然在 clean 上溢出。对照：手工约定层（带双向边界）实测
   FP 19→16 零误伤。

**重设计**：删除 `evolution/probe.py` 整个通道，新建
`evolution/convention_evolution.py`——进化的动作单位 = **带 NOT-WHEN
排除边界的判断约定**，与手工约定同形态；输入同时覆盖 misses（带归因
+gold 错误描述）与 wrongful convictions（带定罪证书链）；G1 强制双向
边界 + ≥2 例同机制；G2 **逐约定单独重放**（干净归因），部署判据 =
目标修复/翻案 ≥1 ∧ 零 sentinel 损失 ∧ 零新误伤。执行面：
`Detector.detect(evolved_conventions=...)` → 三阶段 prompt 注入。

### 判决去噪的正确解与错误解（实证）

- k=3 defense 多数投票：**错误解**。defense 翻转是系统性偏差（标准
  错位）不是方差，投票固化偏差（FN 15→18）；已回退单票。
- transport retry（provider 瞬时失败重跑）：**正确解**。假翻转清零。
- defense 辩护标准收紧（"any legitimate reading" → "证据实际支持的
  解读"）：针对 defense_overturn 族（FN 38%）的根因修复。定向重放
  （真实 baseline 态 × 收紧后态）：+2 miss 恢复（含 1 例 census_miss
  连带恢复）/ −1 TP 丢失（文本锚定的边缘辩护案例）/ FP 对照 12/12
  不变——净正保留。TP 丢失的边缘案例与未恢复 miss 正是约定进化的
  提取输入。

---

## 零·五、Round 1.5 底层重诊断与机制修复（2026-08-16）

用户裁决「修复一直没用、之前的诊断有问题」成立。全部结论以磁盘
run 数据重算为准（`tools/run_stability.py` 已把测量固化成仪器）。

### 实证测量（历史 run 免费重算）

| 测量 | 数值 | 含义 |
|---|---|---|
| 同 100 例两次运行判决翻面 | 24/100 | 主导信号是 run 间方差 |
| FP / FN 集合跨 run Jaccard | 0.39 / 0.48 | 每轮错误普查约一半不可再现 |
| 纯 test-retest（同代码同配置，temp=0） | 10-30% 翻面 | 根源是 provider 非确定性（flash 级） |
| census 候选集跨 run Jaccard | 均值 0.66，仅 31/89 案例完全一致 | 最大噪声注入点在发现层 |
| 翻面解剖（24 例） | census 集 17、defense 12、verification 5（可叠合） | 稳定化预算应花在 census/defense |
| config_fingerprint 覆盖面 | 重构前后同指纹 | 指纹不覆盖 prompt/代码面，跨 run 比较失真 |

推论：**每轮 ~35 例错误质量中，仅 ~17 例（共享 FP 7 + 共享 FN 10）
是可学习的系统性信号，其余是抽样噪声**。「40 例净翻转 ≥5」的验证
判据泡在噪声地板里（纯噪声重跑即可达 ~±5），进化有没有真效应在
这个设计下不可测。

### 自进化「前后没有区别」的三个机制 bug（全部代码证实）

1. **N1 提取输入是噪声**：worksheet 直接取单轮 misses/定罪名单，
   其中约一半不会在重跑中再现——提取器在学习抽样噪声；
2. **N2 G2 门不是配对的**：`repaired` 只看 guided 臂定罪、不看
   unguided 同跑是否自己翻回（纯噪声恢复计入「修复」）；
   sentinel/clean 零容忍又在 20% 噪声上随机否决——门是掷硬币；
3. **N3 执行面缺 2/3**：evolved 约定只注入 census prompt（三个
   system 模板只有 census 带 `{evolved_block}` 占位符），而 69% 的
   miss 在 defense/verification 层、wrongful-conviction 方向约定在
   裁决层无任何作用点——部署 genome 后裁决行为不可能改变。

### 已落地修复（140 tests 全绿，pristine 扫描干净）

- **三阶段执行面**（`court.py`）：`{evolved_block}` 进入
  verification 与 defense system prompt——约定真正作用于判决层；
- **冻结 census 配对重放**（`run_court(frozen_census=...)` +
  `Detector.detect(frozen_census=...)`）：一次 unguided 全量滚动
  冻结候选集，guided 臂只重跑裁决阶段——guided-vs-unguided 差异
  归因于约定而非 census 重采样；冒烟实测单案例 165s→91s（更便宜）；
- **worksheet 可再现性过滤**（`confirm_failures`）：提取前对每个
  miss/定罪做一次 unguided 复跑，只有再现的失败才进 worksheet；
  复现率本身作为噪声分数落盘；
- **配对 G2 门**（`validate_conventions` 重写）：`repaired` 配对
  化（unguided=clean ∧ guided=error）；新增发现层伤害守卫
  （clean 池 guided 全量 ×2 都定罪才算伤害——伤害须再现，收益须
  配对）；census_miss 目标的发现层修复记录但不作为部署判据
  （通道 A 证伪教训）；自恢复目标单列不冒功；
- **detector_fingerprint**（`runs.py`）：config + 判决面源码
  （court/conventions/packet/provider）联合哈希，run.json 双指纹
  落盘；冻结语义从 illusory 变为真实；
- **稳定性仪器**（`tools/run_stability.py`）：任意两 run 的判决
  一致率、FP/FN Jaccard、census 集稳定性、翻面阶段解剖。

### Round 2 协议修订

1. **轮基线必附稳定性测量**：同 100 例（或子样本）双跑一次，
   `run_stability` 报告进轮 manifest——修复是否降低噪声地板成为
   一级信号；
2. **进化验证全部走配对重放**（冻结前缀）；40 例 fresh 读取保留，
   但判据相对噪声地板解释（配对差异 vs 同案例纯重跑翻面的分布）；
3. **在线判决稳定性是待裁决的成本决策**：判决级 k=3 多数投票
   （成本 ×3，按 churn 20% 推算翻面率降至 ~10%）或裁决阶段换
   非 flash 模型——两者都改变预算结构，须用户拍板后才进 Round 2；
4. 提取-验证循环照 §二 执行，extract 现内建 confirm 步骤。

---

## 一、问题全景与修复方向

### 1.1 检测器的问题

**P1. 判决法律与数据集约定硬编码耦合 —— 跨数据集性能不一致的直接根源。**
census / verification / defense 三个全局 prompt 里写死了 BFCL 风格规则
（"2.0 被写成 2.0000 是 deviation，即使任务文本提到精度"），defense 又同
时写"报告层数值微差不要推翻"。同一个行为在不同源上判决相反。
实测：random100 → fresh100 总体 acc .760 → .636，FP 12→21 翻倍集中在
BFCL（3→6）与 Camel（2→4）；同一 run 内七源方差 .50-.89。
**修复方向：约定层**——把约定从 prompt 抽出为闭卷约定集，按任务特征
（非源名）机械路由；prompt 只留通用法律。附带删除死代码
（`_observation_restatement_check` 零触发、重复 `_StageAudit`、恒真
`defense_outcome`）。

**P2. 发现视野被无谓压缩 —— 最大的 FN 结构性来源。**
census 首扫对非尾部事件只给 300 字符预览、工具参数 120 字符、工具观察
**只显示字符数**；>8 事件的轨迹中间内容实质不可读，且
`evidence_coverage` 永远报 complete（进化侧看不见盲区）。
实测 packet 全量渲染：**中位 5.6K 字符、98% 案例 ≤100K、仅 2% >500K**
——压缩对绝大多数案例是零收益的纯信息损失。
**修复方向：视野全量化**（provider 上下文容量足够，R0 容量探测定预算
线）——首扫默认全量渲染，second_pass 机制删除，分层视图仅作超限案例
降级路径且降级状态如实落盘。

**P3. 证据选择权全在发现层，裁决层无扩展权 —— FP 与 FN 共同的深层机制。**
`render_evidence_view` 只渲染 census 声明的 `locus + evidence_refs` ±1
邻居：引用漏了"能证明偏差的观察"→ FN；漏了"能反驳的观察"→ FP。
verification 不能请求更多证据。
**修复方向：证据扩展权**——窗口 ±1 → ±2、cited 工具观察全文、
per-event 截断随 P2 放宽；深度按全量化后残余实例数定。

**P4. 无弃权的强制二选一，错误责任不可归属。**
census 零候选 → clean；候选全灭 → clean；verification 被规定"不确定不
是拒绝的理由"（不确定默认定罪）。FN 与 clean 的边界由单轮噪声决定，
判决输出不含"为什么错"。
**修复方向：三态判决 + FN 归因管道**——verification 增
`insufficient_evidence`（终态不变、归因落盘）；miss 案例强制分诊
view_degraded / census_miss / evidence_insufficient / defense_overturn，
作为进化通道的一级输入。

**P5. 判决噪声与绑定层输入缺陷。**
单轮无自一致性（两 run 的 census 候选均值 2.36→1.86 漂移，劣化集中在
裁决环节）；binder 输入投影**不含工具观察**（unclassified 率高是输入
设计问题）；binder resume 不失效 stale 绑定；全链单 provider 依赖（进化
效果与 provider 漂移不可分）。
**修复方向**：边缘证书（单票差）k=3 自一致性复查；binder 投影补观察
+ 词表/投影变更时显式失效旧绑定；环境指纹冻结（模型 pin，漂移即重基
线）。

### 1.2 自进化框架的问题

**E1. 进化从未接到检测器本体。** genome 对 detector 是不透明载荷
（`api.detect` 只记 hash，`court.py` 的 `policy_evaluation` 是空占位）
——"部署"= 写文件，全部效果活在 evaluator 侧回放模拟器里。
**修复方向：三个执行面**——census 面（探针条件路由注入）、判决面
（policy 真实消费）、verification 面（谓词规则）。

**E2. 动作空间单调，FN 在代数上不可表达。** 仅有的两个动作都只能撤销定
罪；misses 被三处制度写死为"不可行动"。
**修复方向：通道 A（census_probe 检查义务库）**——从 miss 提取反事实
检查义务，条件路由注入，unguided control 对照 + 定向在线重放验证。

**E3. 进化层级错位。** FP/FN 的根源都在证据生成层，进化却只发生在离根
源最远的判决后处理层。冻结的 gap_taxonomy 蓝图本就规划了三条通道
（observation / verification / policy），只落地了 policy 一条；
"basis 信号进 Phase 2" 的既定结论从未兑现。
**修复方向：三通道对齐蓝图**——A（census）、B（verification 谓词化，
binder 变机械映射）、C（L2 policy 保留降级为止血）。

**E4. 绝对门 + 分布漂移 = 永不晋升。** 零 TP 损失 + FN≤1 + 逐案例归因剪
枝；实测 fresh 上幸存证书全部 content_contradiction、部署策略靶类曝光
0、零触发、NOT promoted。门太严实质是检测器噪声（P5）逼出的防御。
**修复方向：G1 结构门 + G2 统计门**（§五），预注册容差。

**E5. 无 Meta 治理 + FN 被循环论证排除。** INTERFACES.md 规划的 meta/
（跨案例策略学习器）从未实现，门、台账、冲突规则内联在 cycle.py 剪枝
循环里；"label-free 运行时无法判 FN 所以 FN 不可进化"用运行时限制推
进化侧不可为（进化本来就是评测侧 gold 闭环）。
**修复方向：Meta 层**（§六）+ misses 一级化。

### 1.3 修复项 → 验证信号映射

| 修复项 | 验证修好的信号（轮内可测） |
|---|---|
| P1 约定层 | 分源 acc 极差收敛（基线 .36） |
| P2 全量化 | FN 归因中 view_degraded 家族消失/显性化 |
| P3 证据扩展 | evidence_insufficient 家族缩小 |
| P4 三态+归因 | 每个 miss 携带非空归因 |
| P5 去噪/绑定 | 重跑判决一致率上升；unclassified 率下降 |
| E1-E3 通道 | 目标 FN/FP 案例实修（含对照组） |
| E4 门 | 候选池不再空转；G1/G2 拒收可归因 |
| E5 Meta | 冲突/振荡/熔断负面场景机械测试通过 |

---

## 二、迭代循环定义

循环不是线性计划：**轮数不固定，由反馈驱动**。每轮七步：

```
┌─ 每轮 ─────────────────────────────────────────────────────────┐
│ ① 数据重抽       新 100 例（60 进化 + 40 验证），分层随机        │
│ ② 轮基线锚定     当前框架 + 当前 genome 在新 100 例上 run        │
│                  （+ binder + gold join + FN 归因）              │
│ ③ 修复           从 backlog 选取修复项执行（代码级，带回归测试）  │
│ ④ 优化           进化机制推进：修复后的检测器在新进化集 60 上      │
│                  提取动作 → G1 → G2 → 候选 genome（Meta 治理）   │
│ ⑤ 轮内验证       进化集上试错自由；两个检查点：修复后、进化后     │
│ ⑥ 验证集读取     本轮 40 例上对比「基线 vs 修复+进化后」，        │
│                  预注册判据，每轮仅此一次                         │
│ ⑦ 反馈归因       失败模式归类 → 更新 backlog → 继续/终止决策      │
└──────────────────────────────────────────────────────────────────┘
```

**首轮特殊性**：首轮在 ② 之前先完成前置锚定（B0：指纹冻结、容量探测、
backlog 中 P1/P2/P4 等核心修复项），因为没有信号质量的修复，后续轮的
进化验证都在学噪声。

**轮内成本**：两个 100 例 run（基线 + 修复/进化后）+ 定向重放（≤2×miss
数）+ binder。20 并发下每 run 约 1 小时级。

**贡献分解**：验证集读取裁决的是"本轮修复+优化的整体效果"；修复与进化
各自的贡献用进化集 60 上的两个检查点分解，验证集不重复读取。

---

## 三、数据协议：每轮随机重抽

### 3.1 重抽规则

- **总体**：从未进入任何 run 的 fresh 案例（AgentHallu 693 − 污染 union
  ≈584；可用池以脚本从 manifest 与 `runs/*/run.json` 机械推导，不手挑）；
- **每轮**：随机重抽 100 = 60 进化 + 40 验证，分层规则每轮不变——按源
  数据集比例配额 × gold 极性 1:1；种子与完整清单写入当轮 manifest
  （`datasets/eval_splits/fixval_round{n}_v1/`）；
- **不变量**：分层规则、极性平衡、与污染 run 的隔离，逐轮机械校验。

### 3.2 重抽如何防过拟合（这是设计结构，不只是纪律）

- 框架的记忆（探针库 / genome / Meta 账本）**跨轮累积**，数据**每轮换
  新**——一个动作只有连续多轮在不同抽样上都带来改善才被认定为真泛
  化；只在单轮改善的，大概率是当轮抽样噪声或对当轮进化集的过拟合，
  由 Meta 试用期（§六 M4）跨轮监控并淘汰；
- 验证集每轮 1 次读取、判据预注册、**反馈只改下一轮任务不改本轮参数**
  ——验证集永远不会变成调参集，因为它下轮就换掉了；
- 允许案例跨轮换角色（上轮验证、本轮进化），全程记录案例级台账（每案
  例的轮次、角色、结果），供事后审计。

### 3.3 跨轮统计设计（重抽带来的必修课）

- **可比量是轮内配对改善**：每轮 40 例上"基线 vs 修复+进化后"的逐案例
  翻转。不同轮的总体 acc 数字不可直接比较（数据不同）；
- **跨轮池化**：多轮的配对翻转做符号检验 / 分层 McNemar（轮为层），
  轮数 ≥3 后功效可达中等效应；
- **漂移监控**：每轮基线 run 的指标构成框架漂移序列——修复不应使基线
  退化（退化 = 修复引入回归，触发 Meta 熔断逻辑的框架级版）。

### 3.4 读取与轮数预算

- 验证读取每轮恰 1 次（步骤⑥），全局以轮数封顶：**默认 ≤6 轮**；
- 超出预算强制终止并报告（说明当前框架状态与已确证的结论）。

---

## 四、修复 backlog（供每轮步骤③④选取；含依赖）

| # | 修复项 | 对应问题 | 依赖 |
|---|---|---|---|
| B0 | 环境指纹冻结 + gateway 容量探测 + 重抽脚本与 manifest | P5/D12 | — |
| B1 | 约定层抽取 + prompt 去硬编码 + 删死代码 | P1 | B0 |
| B2 | 视野全量化（默认全量、second_pass 删除、超限降级可观测） | P2 | B0（容量线） |
| B3 | 证据扩展权（±2 窗口、观察全文、截断放宽） | P3 | B2 |
| B4 | 三态判决 + FN 归因管道 + misses 一级化 | P4/E5 | — |
| B5 | binder 投影补观察 + stale 失效 + 边缘复查（k=3） | P5 | — |
| B6 | census 执行面（探针条件路由，并发 ≤3） | E1 | B4（归因） |
| B7 | 判决执行面（policy_evaluation 真实消费） | E1 | — |
| B8 | 通道 A 全链（义务提取器 + 定向重放 + unguided control） | E2/E3 | B2,B4,B6 |
| B9 | G1/G2 门实现 | E4 | B0（容差预注册） |
| B10 | Meta 层 `evolution/meta.py`（§六） | E5 | B9 |
| B11 | 通道 B 谓词化 + binder 机械映射 | E3 | B3,B5（后置，视前几轮反馈） |

首轮建议取 B0-B4（+B5 若余量）；B8 依赖的信号质量修复就位后，第二轮
起通道与门/Meta 进入常规循环。

---

## 五、门的设计（结构门 + 统计门，由 Meta 层执行）

### G1 结构门（零成本，机械）

闭卷词表（义务/谓词/证书类/动作）+ ≥2 例同机制支持 + 禁 case id + 必须
声明 trigger_pattern 与风险边界。现有 `validate_proposals` 纪律的推广，
无自由裁量。

### G2 统计门（当轮进化集上 replay 一次，三个合取条件）

1. **无倒塌**：acc ≥ 轮基线 − 2 例；TP ≥ 轮基线 − 1；FN 增量 ≤ 2；
2. **目标解决**：动作声明要修复的 FP/FN 案例，实修比例 ≥ 1/2；
3. **指标提升**：净 acc 或 F1 严格 > 轮基线。

在线通道（A/B）的 replay = 定向重放受影响案例；离线通道（C）= 确定性
判决重放。**replay 一次即裁决**，不做搜索式多次重放（防在进化集上变相
调参）。容差预注册于轮 manifest，当轮不可调整；调整本身是下一轮任务并
重锚基线。G2 的基线锚定当轮基线 run，不随 cycle 浮动。

---

## 六、Meta 层（`evolution/meta.py`，防优化循环与冲突的单一真源）

六条机制，cycle 只消费不维护：

- **M1 动作账本**：每动作登记 target、方向（suppress/augment）、通道、
  状态（candidate/probation/active/retired/banned）、归因案例集、效果
  记录；与 genome hash 链双向一致；
- **M2 方向独占**：同一 target 同轮至多部署一个方向——通道 C 压定罪与
  通道 A/B 增定罪不得同目标并存，违反在 G1 拒收（冲突动作不进候选池，
  不是运行时调和）；
- **M3 冷却与禁忌**：retire 后 2 轮内同形动作（同 target+方向+trigger
  语义）禁止重提；同形 born→retired 达 2 次进永久禁忌表并上报；
- **M4 试用期与回滚**：晋升动作先 probation 一轮，下轮新抽进化/验证集
  上回归即 retire（带原因）；genome 级熔断——连续 2 轮配对改善为负，
  自动回滚到上一个 active genome 并上报；
- **M5 预算**：每轮探针激活 ≤3、动作总数 ≤5、proposal 调用 ≤3、在线
  重放 ≤2×miss 数、验证读取恰 1 次；超预算候选顺延；
- **M6 帧稳定**：sentinel 集 = 当轮基线 run 中 convicted 的 gold-error
  案例（每轮重锚），不是"当前 genome 下 convicted"的漂移集合。

---

## 七、反馈协议（验证读取 → 下一轮）

| 验证反馈模式 | 归因假设 | 映射的下一轮动作 |
|---|---|---|
| 新 FP 家族 | 约定路由过宽 / 探针过触发 | 收紧 trigger 或 retire 探针（走 M3 冷却） |
| 新 FN 家族 | 证据扩展不足 / 归因管道漏类 | B3 深化；不新增通道 |
| 进化集改善、验证集不动 | 当轮进化集过拟合 | 依赖重抽结构：下轮自动换样验证；连续 2 轮如此触发 M4 |
| 无任何变化 | 提取器空转或门过严 | 分开度量：G1 拒收主导=提取器问题；G2 拒收主导=机制问题 |
| 倒塌（超容差） | 通道冲突或探针互斥破坏 | M4 熔断回滚；冲突对进 M2 复查 |
| 轮基线退化 | 修复引入回归 | 回退该修复项，重跑轮基线 |

每次读取在当轮 manifest 留下：判据、genome 指纹、逐案例翻转表、归因
结论、backlog 变更。

---

## 八、判据与终止

### 每轮判据（步骤⑥，预注册）

- **主判据（配对）**：40 例上净翻转（FP↓+FN↓ − 反向）≥5 例为"显著改
 善"；1-4 例为"弱改善"；≤0 为"无改善/倒退"；
- **副判据**：FN 归因无 unknown；Meta 账本无未裁决冲突；轮基线不劣于
  前轮基线的池化区间（漂移监控）。

### 全程判据（轮数 ≥3 后的池化评估）

- 跨轮分层符号检验 p<0.05 且方向一致 → **框架有效成立**；
- 池化无效应且各轮弱改善分散 → 现方案证伪，进入重新设计（不是调参
  续命）。

### 终止条件（任一触发）

1. 轮数达 6（读取预算用尽）；
2. 全程判据已获明确结论（成立或证伪）；
3. 同一失败模式连续 2 轮无法归因——诊断模型不完备，停止烧预算，回到
   深挖（而不是继续迭代）。

### 明确不做的事

- 不跑 393/300 完整实验（本轮迭代通过后的下一阶段）；
- 不以任何验证读取结果修改 prompt / 词表 / 阈值 / 容差（只改下轮任务）；
- 不引入新 provider（单 provider 漂移分离是后续课题，本轮只冻结指纹）。
