# SEHD-RISE2 V2 架构重构方案：统一 Evolution Artifact 层

决策链：`SEHD_RISE2_ARCHITECTURE_DIAGNOSIS.md`（断点 B1-B6）→ **本文**
（架构决策）→ `FIX_VALIDATE_ITERATION_PLAN.md`（过程协议）。

**V2 的唯一验收目标：自进化闭环生效**——

> failure → capability gap → evolution artifact → **运行时执行** →
> 相对噪声地板可测的净增益（冻结 300 例配对终审）。

输入：统一 Evolution Artifact 架构提案（2026-08-16）+ 仓库 HEAD
`4cd560c` 实证。提案方向采纳，三处按仓库实证收敛（§一）。

---

## 一、对统一提案的采纳与收敛

### 采纳（提案的核心增量）

1. **统一 Evolution Artifact Contract**：进化的输出只有一个对象，携带
   显式 `execution_point`——没有接线执行点的产物结构上不可发布
   （直接封死 B1「skill genome 双死」这类失效）。
2. **`detect(artifacts=…)` 单参数**：取代 `genome / policy_genome /
   evolved_conventions` 三个并行入口。
3. **G2a 行为门**：任何 artifact 在宣称能力增益前，必须先机械证明
   「运行时行为确实改变了」——这是对 N3（执行面缺失直到被测量才暴露）
   的制度化防御。
4. **门的顺序：runtime proof → capability proof**。
5. **删除 skill genome**（诊断 M-A），论文叙事的「experience acquired
   capability」由 artifact 的 provenance（failure_cases → 已部署能力）
   承担，不需要 Skill JSON。
6. **gap 成为承重结构**：failure 先归 gap，gap 决定 artifact 类型。

### 收敛（与提案不同的三处，均以仓库实证为据）

1. **intervention 不是自由形式，按 kind 分型、闭卷词表**。通道 A 的
   证伪结论（单向放大器 + 开放 prose = 假闭环，盈亏平衡需 >2.75 实测
   0.5）决定了：prompt 型 artifact 强制双向边界（directive + NOT-WHEN
   排除），裁决型 artifact 动作限 `POLY_ACTIONS` 闭卷。提案里的
   `candidate generation operator`（census 自由生成算子）**不开放**——
   B4（census_miss 门死）在通道 B 提供可测证据形态前不重开。
2. **convention 机制不推倒重来**。它是唯一已闭环资产，Round 1.5 刚配
   好仪器（复现过滤 + 冻结 census 配对重放 + 发现层伤害守卫）。V2 把
   convention 条目**原样升级为 prompt_directive 型 artifact 实例**
   （字段不变），管线复用。
3. **gap 词表不新造**。`evolution/gap_taxonomy.py` 已冻结
   `GAP_CATEGORIES` 且自带 `CATEGORY_EVOLUTION_TARGET` 映射
   （observation→l1_candidate、verification→evidence_skill、
   policy→l2_policy）——它就是 Capability Gap Contract，V2 把它从
   标注词表升级为 artifact 合成的机械路由。

---

## 二、保留 / 删除 / 新核心抽象

### 保留

| 资产 | 理由 |
|---|---|
| court / conventions / packet 冻结基座 | 判决面稳定是噪声地板测量的前提 |
| convention_evolution 全管线（复现过滤、G1、配对 G2） | 唯一闭环 + 最新仪器 |
| replay / cycle 的累积重放门 + library 台账 | 累积证据纪律与 culprit/idle 剪枝 |
| gap_taxonomy 冻结词表 | 升格为 artifact 类型路由 |
| binder 闭卷感知语义（unclassified 弃权） | 运行时类绑定的实现基础 |
| run_stability / detector_fingerprint | 噪声地板与冻结语义的仪器 |
| 冻结 300 例验证集（`89c9a90`） | M4 实验侧终审（不进自进化循环） |

### 删除

| 对象 | 位置 | 断点 |
|---|---|---|
| skill genome 全接口（加载器、参数、导出、测试） | `detector/skills.py`、`api.py`、`cli.py`、`evolution_api.py` | B1 |
| `genome / policy_genome / evolved_conventions` 三参数 | `api.detect` / `cli.py` | 入口分裂 |
| policy genome / convention genome 两个独立 schema | `policy.py` / `convention_evolution.py` | 被 artifact registry 取代 |

### 新核心抽象（唯一新增）

**EvolutionArtifact（`sehd2.evolution_artifact.v1`）+ detector 侧
artifact 执行面 + registry**。不加其它模块；执行点词表与裁决动作词表
归位 detector（层级守卫方向不变：detector 不 import evolution）。

---

## 三、Evolution Artifact Contract

```json
{
  "schema_version": "sehd2.evolution_artifact.v1",
  "artifact_id": "ART-<cycle>-<seq>",
  "version": "v1",
  "kind": "prompt_directive | adjudication_policy | evidence_predicate",
  "execution_point": "stage_prompts | adjudication | verification_predicates",
  "gap": {
    "category": "<gap_taxonomy 类>",
    "name": "<冻结 gap 名>",
    "gap_type": "recall | precision | boundary",
    "failure_signature": "<该机制失配的可观察签名，类级表述>",
    "affected_stage": "census | verification | defense | adjudication",
    "expected_capability": "<修复后应具备的检测能力，一句话>"
  },
  "trigger": {
    "packet_features": ["<闭卷特征>"],
    "certificate_class": "<CERTIFICATE_CLASSES 闭卷>"
  },
  "intervention": {},
  "capability_delta": {
    "before_capability": "<部署前做不到什么，类级>",
    "after_capability": "<部署后能做到什么，类级>"
  },
  "provenance": {
    "cycle": 0, "worksheet": "<path>",
    "failure_cases": ["<case_id>"], "gap_evidence": []
  },
  "content_hash": "<sha256>"
}
```

**Gap 是一等对象，Artifact 是它的可执行投影**。链路必须是
`failure → mechanism gap → artifact`——跳过 gap 直接 failure →
artifact 产出的必然是 case-specific patch。Gap 对象五个字段的责任
分工：`category/name` 由冻结词表（`gap_taxonomy.py`）裁定成员资格；
`gap_type` 声明修复方向（recall=找回漏检、precision=消除误伤、
boundary=移动判决边界），G2b 按它选配对臂判据；`failure_signature`
是机制签名（G1 校验 ≥2 失败案例共享同一签名，且禁 case id）；
`affected_stage` 必须与 `execution_point` 一致（G1 机械校验）；
`expected_capability` 是 M4 实验的可证伪预测——终审增益必须落在声
明的能力面上，否则 artifact 判负。**`capability_delta` 是防退化条款**：
artifact 不是 `if trigger: add prompt` 的规则容器，而是把
`before_capability`（如「无法发现时间依赖下的证据矛盾」）变为
`after_capability`（「能发现……」）的能力增量；评审与论文叙事以
delta 是否成立为准，不以规则条数为准。

**kind → intervention → execution_point 的机械绑定**（G1 拒绝一切
表外组合）：

| kind | intervention（闭卷） | execution_point | 触发 | 对应当前产物 |
|---|---|---|---|---|
| `prompt_directive` | `{directive, exclusion}` 双向边界缺一不可 | `stage_prompts`（census/verification/defense 三阶段，N3 语义） | packet_features 全命中 | convention 条目原样映射 |
| `adjudication_policy` | `{action ∈ withhold_conviction \| require_corroborating_content_error, exclusions}` | `adjudication`（确定性聚合内） | certificate_class 绑定命中 | policy item 原样映射 |
| `evidence_predicate` | 谓词规则（M4 定） | `verification_predicates` | 谓词向量 | 无（通道 B，预留） |

**gap → kind 的机械路由**（G1 检查，来源 `CATEGORY_EVOLUTION_TARGET`）：

| gap category | 允许的 kind | 备注 |
|---|---|---|
| observation（census_miss 族） | 无可部署 kind | B4：记录归因，等 evidence_predicate；不重开单向 census 算子 |
| verification | prompt_directive（裁决层可测部分）；evidence_predicate（M4） | excuse_based_rejection 等三机制是谓词化首要输入 |
| policy | prompt_directive + adjudication_policy | FP 与 defense_overturn 双向 |
| attribution / infrastructural / no_gap | 诊断类，不产 artifact | 词表已声明 |

**registry**：一个 JSON 文件 = 已部署 artifact 集（带 content_hash），
配 library 台账（born/amended/retired，沿用 cycle.py 既有模式）。
试用期回归即 retire。

---

## 四、Runtime Execution Plane（detect 的统一入口）

```python
detect(record, case_id=..., artifacts: Sequence[Mapping] = (),
       frozen_census=None)
```

court 内部路由（全部闭卷）：

```
artifacts
  ├─ prompt_directive（trigger: packet_features 全命中）
  │     └─ render 双向边界块 → census/verification/defense 三阶段
  │        system prompt（复用 render_evolved_block 语义）
  │        proof.active_artifacts 落盘
  └─ adjudication_policy
        ├─ 防段完成后：surviving 证书（supported ∧ upheld）逐个闭卷
        │  类绑定（binder 语义内联：字段白名单 + unclassified 弃权；
        │  仅当部署了裁决型 artifact 才跑，零部署零成本）
        └─ adjudicate 消费：withhold / corroborate 作用于 error_certs
           的选择；proof.policy_evaluation.fired 落盘
```

**单一实现纪律**：离线重放（replay/cycle）与运行时消费调用同一个
adjudicate 语义函数——运行时传实时绑定，离线传录存绑定，不存在两套
规则。这是消除 B2「运行时/离线语义漂移」的结构保证。

**词表归位**（层级守卫不破）：`CERTIFICATE_CLASSES`、`POLY_ACTIONS`、
artifact kind/execution_point 词表定义点落到 detector 侧；
`evolution/policy.py` 反向导入。`detector/skills.py` 整体删除，其
hash 校验职责并入 detector 侧 artifact 加载校验。

---

## 五、Evolution Loop（failure → gap → artifact → gates → registry）

```
batch run tree + gold（评测侧）
  ▼ 稳定性测量（run_stability，噪声地板进轮 manifest）
  ▼ worksheet（复现过滤；misses 带归因一级化）
  ▼ failure → gap（gap_taxonomy 闭卷；attribution/no_gap 分流诊断）
  ▼ gap → artifact 合成（每 gap 类一次 provider 调用；
    prompt_directive 沿用 convention 提取器语义，
    adjudication_policy 沿用 proposal 提取器语义）
  ▼ G1 结构门（artifact schema + kind/execution 机械绑定 + gap 路由
    合法性 + ≥2 例同机制 + 禁 case id + 双向边界/闭卷动作）
  ▼ G2a 行为门（硬门：接合证据 + 配对行为差分）
  ▼ G2b 配对统计门（累积证据；冻结 census 配对重放；容差读自
    gate_calibration 单一真源）——晋升权在此
  ▼ registry + library 台账 → detect(artifacts=…) 即刻生效
  ▼ 试用期回归即 retire
```

冻结验证集不进本循环（2026-08-16 用户裁决）：其全管线双跑终审是
M4 完整实验的一次性实验侧验收，与自进化 machinery 分离。

---

## 六、统一 Promotion Gate

| 门 | 内容 | 成本 | 权限 |
|---|---|---|---|
| G1 结构 | schema / kind-execution 绑定 / gap 路由 / 词表 / ≥2 例同机制 / 禁泄漏 | 零 | 淘汰 |
| **G2a 行为** | (i) 接合证据：目标案例配对臂上 `active_artifacts` / `policy_evaluation.fired` 非空；(ii) 行为差分：配对臂下 ≥1 目标终态或证书链改变 | 复用 G2b 的配对臂，近零边际 | **硬门**：不接合或不改变行为 = 死产物，禁入 G2b |
| G2b 配对统计 | 冻结 census 配对重放；sentinel 损失 / clean 定罪 / 发现层伤害 / 最小净增益全部读自 `datasets/calibration/gate_calibration.json` 单一真源（默认绝对零容忍；重标定只来自轮基线稳定性双跑，绝不来自冻结验证集） | 每 cycle ≈ 2×目标数次调用 | **晋升权**：过门即写 registry、即刻部署 |

顺序即提案要求：**runtime proof（G2a）→ capability proof（G2b）**。
G2a 把「artifact 真的进了执行路径」从约定变成可机械检验的发布条件。
冻结验证集的全管线双跑终审属于 **M4 完整实验的实验侧验收**（一次性、
离线、不进循环）；自进化循环内的晋升权在 G2b，试用期回归由后续
cycle 的累积重放自动 retire。

---

## 七、如何保证 artifact 一定进入 execution path（结构保证清单）

1. **词表即接线**：kind/execution_point 的合法值 = court/adjudicate
   代码里实际存在的执行点枚举；G1 对照该枚举校验，无执行点的 artifact
   无法通过（B1 失效模式结构性消灭）。
2. **G2a 硬门**：接合证据 + 行为差分不过，禁入统计门。
3. **Detection Truth 落盘**：每次检测记录 `active_artifacts` 与
   `policy_evaluation.fired`——部署后是否持续接合可被任意 run 复查。
4. **单一实现**：离线重放与运行时共用同一 adjudicate 语义，门测的
   就是部署的。
5. **registry 即部署**：晋升写 registry，`detect(artifacts=registry)`
   是唯一部署形态；不存在绕过执行面的「部署」动作。
6. **试用期**：每轮 batch 复查已部署 artifact 的接合率与回归，
   回归即 retire（library events）。

---

## 八、模块接口变化表

| 文件 | 变化 |
|---|---|
| `detector/api.py` | `detect(artifacts=…)`；删 `genome/policy_genome/evolved_conventions` 参数与 provenance 三键 → `artifacts_hash` + `active_artifacts` |
| `detector/skills.py` | **删除**；hash/闭卷校验并入 detector 侧 artifact 加载 |
| `detector/artifacts.py`（新，词表+校验+渲染路由） | kind/execution_point 词表单一真源；加载 fail-closed |
| `detector/court.py` | prompt_directive 渲染（复用既有 renderer）；裁决型绑定步 + adjudicate 消费；fired/active 落盘 |
| `detector/conventions.py` | 手工约定不动（基座，非进化产物）；`CERTIFICATE_CLASSES/POLY_ACTIONS` 定义归位至此或 artifacts.py |
| `evolution/convention_evolution.py` | 输出 schema 改为 prompt_directive artifact；复现过滤/G1/配对 G2 机制不动 |
| `evolution/policy.py` | 词表改为反向导入；genome 构建输出 adjudication_policy artifact |
| `evolution/proposal.py` | worksheet 增加 gap 列；提案输出按 gap 路由分型 |
| `evolution/cycle.py` / `replay.py` | 重放改为调用 detector 侧同一 adjudicate 语义；台账扩为 artifact registry |
| `detector/cli.py` / `evolution_api.py` | `--artifacts` 单入口；导出面对齐 |
| `tools/check_layers.py` | 规则不变 |

---

## 九、里程碑与可证伪验收

| 里程碑 | 内容 | 可证伪判据 |
|---|---|---|
| **M0** | 本方案冻结（含 §十两项用户裁决） | 用户批准 |
| **M1** | 删 skill genome + `detect(artifacts=…)` 统一签名 + 词表归位 | 全仓库无 genome 三参数符号；tests / check_layers / pristine-scan 三绿；20 例冒烟与基线逐案例终态一致（重构不改行为） |
| **M2** | 裁决型执行面：运行时绑定 + adjudicate 消费 + 单一实现 | `policy_evaluation.fired` 非空，且离线重放与运行时在全部累积 run tree 上逐案例一致（B2 关闭） |
| **M3** | G2a 行为硬门 + G2b 校准单一真源（gate_calibration.json，默认绝对零容忍） | 一个故意构造的死 artifact 被 G2a 拒绝；容差变更改变 cycle 判决（门自身可证伪）——均已由测试锁定 |
| **M4** | 实验：Round 2 协议（evolve → G1→G2a→G2b → 部署 → 冻结验证集一次性终审，实验侧） | 净翻转分布 vs 同案例纯重跑翻面分布可区分 → 框架生效；不可区分 → 如实判负并回 §一收敛点复盘 |

**「框架生效」的最终定义**：≥1 个 artifact 走完 G1→G2a→G2b 部署后，
在实验侧终审中产生相对噪声地板可区分的净增益，且部署后接合率持续为
正。

---

## 十、已裁决与开放项

1. **已裁决（2026-08-16，用户拍板）：裁决阶段换非 flash 模型**。
   verification / defense 路由模型升级为非 flash 级（配置变更，
   detector_fingerprint 随之改变——与历史 run 的比较须以新指纹重跑
   基线）。G2b 统计参数标定解锁：M3 前先以新模型跑稳定性测量
   （run_stability 双跑），按实测翻面率定 TP 损失容差 / McNemar 阈。
   k=3 多数投票不采用。
2. **census 执行点重开**（B4）：V2 明确不重开单向 census 算子；
   evidence_predicate（通道 B / M4 后续）是唯一候选路径，另行立项。
3. **已裁决（2026-08-16，用户拍板）：冻结验证集不进自进化循环**。
   自进化 machinery 的晋升权 = G2b（累积配对证据）；冻结验证集的
   全管线双跑终审只作为 M4 完整实验的一次性实验侧验收。进化侧代码
   不得 import/读取该 split。
