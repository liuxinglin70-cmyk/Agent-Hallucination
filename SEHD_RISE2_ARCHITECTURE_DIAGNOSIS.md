# SEHD-RISE2 架构重构诊断图

输入：外部架构审查报告（2026-08-16）+ 仓库 HEAD `4cd560c` 逐文件核实。
每条主张的裁决以磁盘代码为准，全部锚点（`file:line`）已在 HEAD 复核。

文档分工：`SELF_EVOLUTION_REDESIGN_PROPOSAL.md` 定目标架构；
`FIX_VALIDATE_ITERATION_PLAN.md` 定过程协议；本文定位于三件事——
当前真实状态机、理想状态机、两者之间的全部断路点，以及**不加功能
模块**的最小修改清单。

---

## 一、总判断

**「自进化没有增益」的架构根因成立：进化 → 检测器执行的桥只剩一条
活的，另两条产物链一条双死、一条半死。**

| 进化产物 | 生产者 | 部署执行面 | 状态 |
|---|---|---|---|
| convention genome | `evolution/convention_evolution.py` | `detect(evolved_conventions=…)` → 三阶段 prompt 注入（`court.py:669-683`） | **唯一闭环** |
| policy genome | `evolution/policy.py:423` + `cycle.py` | `detect(policy_genome=…)` 只记 hash（`api.py:74-79`）；动作消费只存在于离线重放（`replay.py`） | 半死：有生产、无执行 |
| skill genome | **全仓库零生产者**（grep 证实，仅 `skills.py` 的加载器） | OPAQUE 载荷，provenance 记 hash（`skills.py:2-7`） | 双死接口 |

系统当前实际行为 = **判决约定校准器**（prompt 注入）+ **离线裁决重放
校准器**（L2 policy 确定性重聚合，零 provider 调用，`replay.py:1-9`）。
这与外部审查「实际实现的是判决规则校准系统」的结论一致；「L1 无执行
槽」一处需修正（§五-6）：**槽在，门死**。

背景约束（Round 1.5 实测，迭代计划 §零·五）：判决翻面 24/100、FP/FN
集合跨 run Jaccard 0.39/0.48——每轮 ~35 例错误里仅 ~17 例是可学习的
系统性信号。任何门与任何修复判据都必须相对这个噪声地板解释。

---

## 二、当前真实状态机（HEAD 实测）

### 2.1 运行时检测面（生产路径）

```
record（label-free；gold 字段被 packet 输入防火墙拒绝）
  │
  ▼
build_packet ──► run_court(packet, config,
                           evolved_conventions, frozen_census)   court.py:640
  │
  │  手工约定   packet_features → route_conventions → convention_block
  │             （conventions.py:36-118，闭卷词表 + 机械路由）
  │  进化约定   render_evolved_block：trigger 全命中才注入；
  │             directive 与 NOT-WHEN 排除缺一不路由
  │             （conventions.py:229-251）→ census / verification /
  │             defense 三个 system prompt 全部携带  court.py:669-683
  │  frozen_census  跳过 census 阶段，只重跑裁决（配对重放锚）
  │             （court.py:750-757）
  ▼
census ──► verification(按证据分批) ──► defense(分批)     provider 三阶段
  │        每阶段契约修复重问 ×2；任一 provider/契约失败 → unresolved
  ▼
adjudicate  确定性闭卷聚合（court.py:421-602）
  │         verified_error / verified_clean / unresolved
  │         miss_triage_facts 分诊落盘（census 候选数/弃权/翻转/视野降级）
  ▼
冻结信封 result（state / decision_authority / provenance / stage_audit）

genome        ──┐
policy_genome ──┴──► provenance 记 content_hash（api.py:74-79）
                      ——零执行语义，README 明文定位「不透明载荷」
```

### 2.2 进化链 1：convention evolution（唯一回生产的闭环）

```
run tree + eval_gold
  ▼ confirm_failures            convention_evolution.py:261-305
    每个失败一次 unguided 复跑，只有再现的 miss / 定罪进 worksheet
    （噪声地板对策：单轮普查约一半不可再现）
  ▼ extract_conventions         一次 provider 调用
    输入含 misses（FN 归因 + gold 错误描述）与 wrongful convictions
    （定罪证书链）——两个方向都教约定
  ▼ G1 gate_conventions         机械门（:350-422）
    闭卷 trigger 词表 / ≥2 例同机制 / 强制 NOT-WHEN / 禁 case id
  ▼ build_convention_genome ──► G2 validate_conventions   逐约定单独
    unguided 全量滚动冻结 census → guided 只重跑裁决阶段
    部署判据（:726-735）：配对修复或翻案 ≥1 ∧ 零 sentinel 配对损失
    ∧ 零 clean 配对定罪 ∧ 零再现发现层伤害 ∧ 配对净 > 0
    census_miss 目标的发现层修复：记录、永不决定性（:15-19, :683-688）
  ▼ convention genome（deployable 子集）
  ▼ detect(evolved_conventions=…)  → 三阶段 prompt      ✅ 进生产
```

### 2.3 进化链 2：L2 policy cycle（离线闭环，终点不回生产）

```
batch run tree + runtime bindings
  ▼ build_worksheet             proposal.py:155-208
    wrongful convictions（幸存证书链）/ tp sentinels
    / recorded_misses ——仅记录：proposal.py:57-59 明文
    「policy layer can only withhold convictions, never create them」
  ▼ generate_proposals          一次 provider 调用 + validate_proposals 机械门
  ▼ 累积重放门                   cycle.py:409-503
    adjudicate = replay_case 确定性重聚合（replay.py，零 provider 调用，
    读录存的 provider 输出重算终态）
    dominates（cycle.py:436-448）：零 TP 损失 ∧ 新增 FN≤1（对基线）
      ∧（FP 严格下降 ∨ 恢复现 genome 正在丢的 TP）
    culprit 剪枝（TP 损失归因到提案）+ idle 剪枝（无归因收益即删）
  ▼ genome_v{N+1} + library 台账（born/amended/retired events）
  ╳ 终点：生产 detect() 从不消费——policy_genome 参数只记 hash；
    detector 侧不存在 policy_evaluation（grep 证实）
```

### 2.4 进化链 3：skill genome（双死接口）

```
（无生产者）──► skills.py 校验 modules 对象 + hash ──► provenance 记 hash
接口存在于 api.detect / cli --genome / evolution_api 导出，全链路零语义
```

---

## 三、理想状态机（重设计提案 §3，通道 A 已按 Round 1 实证修订）

```
        ┌───────── detector（冻结基座 + 三个显式执行面）─────────┐
        │ census prompt     ← 约定层（手工 + evolved，双向边界路由）│
        │ verification      ← 谓词化证据（通道 B：corroboration_rule）│
        │ defense prompt    ← 约定层                                   │
        │ adjudicate        ← L2 policy 执行面（通道 C：withhold/     │
        │                     corroborate，policy_evaluation 落盘）    │
        └────────────┬──────────────────────────────────┘
                     │ Detection Truth（含 miss 归因 + fired 记录）
   ┌─────────────────▼────────────── 评测侧 gold 闭环 ──────────────┐
   │ batch → 稳定性测量（run_stability，噪声地板一级信号）            │
   │   → worksheet（复现过滤 + misses 带归因一级化）                  │
   │   → 提案（miss 方向双向边界约定 / 谓词规则 / L2 止血）→ G1 结构门 │
   │   → G2 配对统计门（冻结 census 配对重放；TP 损失容差或 McNemar）  │
   │     → 候选池（攒 K cycle）                                       │
   │   → G3 终审（冻结 300 例全管线双跑，唯一晋升权）→ genome v{N+1}  │
   │   → 试用期：回归即 retire（library events）                       │
   └──────────────────────────────────────────────────────┘
```

与当前的差距映射：

| 理想组件 | 当前状态 | 缺口 |
|---|---|---|
| census 执行面（约定注入） | ✅ 已落地（含 N3 修复：三阶段全注入） | — |
| 裁决层执行面（policy） | ❌ court 无 policy_evaluation | 断路点 B2 |
| 谓词化证据（通道 B） | ❌ 证书仍是自由文本 rationale | 断路点 B5 |
| FN 方向动作 | ⚠️ 仅约定通道可修裁决层 FN；census_miss 门死 | 断路点 B3/B4 |
| G2 统计化 + G3 终审 | ❌ 绝对零损门 + 无终审 | 断路点 B6 |
| 三通道统一 cycle 编排 | ❌ 两条链各自独立 CLI、genome 家族分裂 | 并入 B2 修复 |

---

## 四、断路点清单

| # | 断点 | 位置与证据 | 后果 | 严重度 | 对应修复 |
|---|---|---|---|---|---|
| B1 | skill genome 双死接口 | 无生产者（grep）；`skills.py:2-7` OPAQUE；`api.py:74-79` 只记 hash | 论文叙事里的「Skill 学习」在代码中不存在任何实例；接口本身是残渣 | ★★★★★ | M-A |
| B2 | policy genome 无运行时执行面 | detector 侧无 policy_evaluation（grep）；消费仅在 `replay.py` 离线重放；两条进化链 genome 家族分裂 | L2 进化的终点永远不改变检测行为；运行时/离线两套语义必然漂移 | ★★★★★ | M-B（提案 M5） |
| B3 | L2 动作空间单调，FN 代数不可表达 | `policy.py:144-153` 仅 withhold/corroborate 两动作；`proposal.py:57-59`、`cycle.py:25-26` 明文把 misses 定为 non-actionable | policy 通道结构上只能减 FP；FN 只能靠约定通道修裁决层子集 | ★★★★★ | M-D（通道 B）是根治；M-B 先接通止血面 |
| B4 | census_miss FN 门死 | 收益池仅占 FN ~31%（Round 1 实测 4/13）；通道 A 两轮 G2 证伪（修复 1/误伤 2，盈亏平衡需 >2.75 实测 0.5）；`convention_evolution.py:15-19,683-688` 把发现层修复定为非决定性 | 发现层（candidate recall）的真增益无门可走——这是与「detector SOTA」目标之间最大的能力空洞 | ★★★★ | 无最小修复；须待通道 B 谓词化提供可测证据形态（§七） |
| B5 | 证书证据是自由文本 | verification rationale 无结构；类绑定靠 binder 文本判断（一致率 92%，pro 探针 2/10 触发漂移） | 通道 B 无法表达；binder 漂移直接进 replay 门；unclassified 弃权丢信号 | ★★★★ | M-D（提案 M4） |
| B6 | G2 绝对零损门 + 无终审 | `convention_evolution.py:726-735` 零容忍条件；`cycle.py:436-448` 严格支配；无 G3 | Round 1.5 配对化已消 census 采样噪声，但裁决层 provider 噪声（defense 12/24 翻面）仍在零容忍门上随机否决/放行；晋升缺唯一权威判据 | ★★★ | M-C（提案 M3） |

---

## 五、外部审查主张核实表

| 审查主张 | 裁决 | 核实与修正 |
|---|---|---|
| P0：Skill Genome 是死资产，detector 零执行语义 | **成立** | `api.py:74-79`、`skills.py:2-7`、README「不透明载荷」。且比审查所述更重：**连生产者都没有**。注意其定位是设计决策（`evolution_api.py:13-18` 权威分割）而非实现疏漏——但「defer 到未来框架」至今未兑现，期票即残渣 |
| P1：L2 policy 只能减 FP，方向错位 | **成立** | 三处封死：动作词表（`policy.py:144-153`）、提案提示词（`proposal.py:57-59`）、cycle 文档（`cycle.py:25-26`）。FN 在该通道代数上不可表达（=提案 F1） |
| P2：三个可进化面 L1❌ / L2 partial / verification partial | **部分成立** | L1 并非无接线：进化约定注入 census prompt（`court.py:669-683`，N3 修复后三阶段全注入）。准确形态是「槽在、门死」（B4）。verification 的 partial 准确指谓词化未做（B5） |
| P3：Skill contract 只有 schema/hash，无 behavioral intervention | **成立** | 但最小修复取向是**删除**（M-A）而非补全——补全执行语义 = 通道 A/B 的工作，不应挂在死接口上重演通道 A 的假闭环 |
| §五：Gate 只优化 FP reduction，碰不到 FN | **部分成立** | convention 通道的 `repaired`（frozen census 下 clean→error）**是** FN 修复且 gate-decisive——可修裁决层 FN（defense_overturn / verification_reject / evidence_insufficient，Round 1 中占 FN 9/13）。不可达的是 census_miss（B4）。cycle 通道的严格增益条款（FP↓ ∨ 恢复自伤 TP）则完全如审查所述 |
| §六：census miss 没有 execution slot | **需修正** | 槽存在（census prompt 注入面）；被门排除（`convention_evolution.py:15-19`）。且「candidate generation skill」形态（单向检查义务）已被通道 A 两轮实证证伪——修这个门不能靠重新打开单向义务，须换证据形态（通道 B） |
| §七：convention evolution 是唯一有效模块，接近论文级 | **认可** | Round 1.5 后它还叠加了复现过滤 + 冻结 census 配对重放 + 发现层伤害守卫——正是针对噪声地板的仪器化。该模块是当前的资产，不是重构对象 |
| §十：现状撑不起 self-evolving claim | **成立** | 唯一闭环是 prompt 约定注入。要支撑 claim 需要 M-B（执行面）+ M-C（G3 决定性验证）落地，且增益须相对噪声地板可测 |

---

## 六、最小修改列表（不加功能模块，按执行顺序）

### M-R2 先行：跑通约定闭环的真效应验证（零代码）

Round 1.5 机制已全部落地（140 tests），未跑的只有端到端实证：evolve →
G2 → 部署 → 冻结 300 例验证集配对比较，净翻转相对纯重跑翻面分布
（噪声地板）解释。这是迭代计划 Round 2 的既定起点，也是 M-A/M-B/M-C
所有改动的测量基线。前置用户裁决见 §七。

### M-A 删除 skill genome 双死接口（B1）

- 改动面：`detector/skills.py` 删 skill 分支与 `load_skill_genome`；
  `api.py` / `cli.py` / `evolution_api.py` 删 `genome` 参数与导出；
  对应测试删除。
- 验收：tests 全绿、`tools/check_layers.py` 过、pristine-scan 干净。
- 通道 B 落地时按谓词向量重新定义能力描述符，不在死接口上续写。

### M-B 接通通道 C：policy genome 运行时执行面（B2，提案 M5）

- 词表单一真源归位：`CERTIFICATE_CLASSES` / `POLICY_ACTIONS` 的
  定义点落到 detector 侧（层级守卫方向不变：detector 不得 import
  evolution），evolution 反向导入。
- 运行时类绑定：surviving 证书（supported ∧ upheld）在 adjudicate
  前做一次闭卷分类（沿用 binder 分类语义；`unclassified` → 策略不
  点火，fail-closed 等于基线行为）。
- 动作消费：`adjudicate` 按 items 应用 withhold / corroborate，
  `policy_evaluation.fired` 进 Detection Truth。
- 单一实现：`replay.py` / `cycle.py` 的离线重放改为复用同一 adjudicate
  语义，消除运行时/离线两套规则。
- 验收（提案 M5 判据）：`policy_evaluation.fired` 非空，且与 evaluator
  侧重放在全部累积 run tree 上逐案例一致。

### M-C 三层门接线（B6，提案 M3）

- G2 统计化：绝对零损改为「TP 损失 ≤ ceil(2%×TP) 或 McNemar 单侧
  p<0.05」，参数以 Round 2 实测噪声地板校准；配对重放结构保持。
- 候选池：G2 通过进池，攒 K cycle。
- G3 终审：冻结 300 例验证集（`89c9a90` 已落盘 split）全管线双跑
  配对比较，唯一晋升权；试用期回归即 retire。
- 验收：一次完整 G3 裁决记录（晋升或否决均可）。

### M-D 证书谓词化（B5，提案 M4）——超出最小范围，列后

谓词闭卷表 + 证书谓词向量 + binder 机械映射。这是 FP/FN 双向收益的
主体与 census_miss 门重开的前提，但依赖 M-B/M-C 先稳定，且动作单位
变化须重新过 G1 词表纪律。不进本次最小修改。

---

## 七、开放决策（须用户拍板，迭代计划 Round 2 协议第 3 条）

在线判决稳定性的成本结构，二选一：

1. 裁决阶段 k=3 多数投票：成本 ×3，按 churn 20% 推算翻面率降至
   ~10%（注意：defense 层的 k=3 已被实证为错误解——投票固化系统性
   偏差；此处指判决级稳定性复查，形态须先设计）；
2. 裁决阶段换非 flash 模型：改变预算结构。

两者都动预算，未经裁决不进 Round 2。该项直接决定 M-C 统计门的参数
能否标定。
