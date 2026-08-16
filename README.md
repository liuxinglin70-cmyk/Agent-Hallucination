# SEHD-RISE2 — 失败理解驱动的自进化幻觉检测器

SEHD-RISE2 是 SEHD-RISE 的重建工作区（`sehd_rise2/`）。当前形态由两部分组成：

1. **最小冻结检测器**（`sehd_rise2/detector/`）——从零重写的独立检测器：
   census（候选发现）→ verification（证据核验）→ defense（对抗复核）→
   确定性法庭聚合 → fail-closed 权威信封；判决约定层按机械包特征路由
   注入三阶段。它是当前一切工作的冻结基座。
2. **自进化框架**（`sehd_rise2/evolution/`）——失败理解（Learning Case +
   能力缺口词表）、L2 策略层与离线/运行时重放、门控进化循环、约定进化
   （复现过滤 → 提取 → G1 → 冻结前缀配对 G2 → 部署三阶段执行面）。

当前阶段是**修复-验证迭代**（fix-val）：每轮随机重抽 100 例验证有效性，
过程真源见 [FIX_VALIDATE_ITERATION_PLAN.md](FIX_VALIDATE_ITERATION_PLAN.md)。
Round 1（fresh 100 基线 acc 0.66）与 Round 1.5（噪声地板重诊断 +
进化机制修复）已落地；核心实证——判决噪声地板（同 100 例双跑翻面
24%、单轮 FP/FN 集合约一半不可再现）是此前「修复一直没用」的根源，
已由稳定性仪器（`tools/run_stability.py`）与配对重放门纳入测量。

## 核心目标与设计纪律（必读 / Mandatory）

在本仓库中工作的每个 agent——无论涉及框架代码、实验还是文档——都必须先读本节并
遵守。本节是「项目为何存在、决策如何作出」的唯一真源；下游文档只引用它，从不复述。

### 核心目标

造一个**会自己越改越准的幻觉检测器**：自进化的幻觉检测效果能够达到 SOTA。
检测器基座已冻结；一切改进必须来自可进化面（L1 候选发现扩展 / L2 决策边界 /
verification 证据扩展），且必须由跨案例的可验证结局奖励证明——gold 标签在
编辑未参与生成的案例上测得——而不是仅靠框架内部信号。失败不能直接映射到 Skill：
必须经过失败理解层（Failure Understanding Layer）转换成 Capability Gap 之后，
由数据决定进化方向。

### 设计理念

1. **系统性优于零散修补。** 用结构化的自底向上重设计修根因；绝不堆积特判 `if`、
   手调旋钮或场景映射（见 First-Time Principle：单一真源、不留残渣、本地/部署
   一致）。
2. **证据驱动的门控，不手调阈值。** 接受决策只依赖可观测的回放效应、封闭词表
   契约与 gold 极性。
3. **失败即关闭（fail-closed）纪律。** 权威缺失、输出不可解析或词表违例一律拒绝；
   不确定从不冒充裁决或被接受的编辑。
4. **Gold 隔离。** gold 标签只属于评测侧（Learning Case、eval_gold.json）；
   绝不进入检测器输入、提示词或产物（`packet.EVALUATOR_ONLY_FIELDS` 输入防火墙
   机械强制）。
5. **Provider 纪律。** 只允许 DeepSeek-V4-Flash / DeepSeek-V4-Pro 路由；凭据值
   绝不进入代码或产物。
6. **能力发现先于能力合成。** 不知道缺什么之前不生成 Skill；词表冻结后，
   任何词表外能力缺口都必须先走人工裁决。

### 导师要求（2026-08 讨论）

1. **绝不硬调阈值。** 任何一个手调阈值最终总是错的；优先把经验判断表达为进化出的
   技能。
2. **鼓励单一阈值的替代方案。** 多阈值 / 多输出配 best-of-N 选择（测试时扩展，
   test-time scaling）是正当方向；拓宽可选空间，而不是去调一个值。
3. **回归式验证（如 canary 哨兵回归）是可优化的机制**，在值得其成本的地方选择性
   采用；它是护栏，不是正确性权威。
4. **技能效果必须在独立验证集上验证。** 进度汇报必须展示具体的中间实验输出，
   而不只是框架描述。

实验协议必须说明它们如何遵守本节；评审在接受结果之前先检查合规性。

## 目录结构

```text
sehd_rise2/
├── detector/                     # 最小冻结检测器（可独立运行）
│   ├── api.py                    # 唯一 Detector API：Detector.detect / detect()
│   ├── court.py                  # 最小法庭：三阶段 provider + 确定性裁决
│   ├── conventions.py            # 判决约定层：闭卷词表 + 包特征路由 + 双向边界
│   ├── provider.py               # provider 传输：直连 HTTPS / 重试 / JSON 修复
│   ├── packet.py                 # 轨迹数据包 + gold 输入防火墙 + 分阶段视图
│   ├── result.py                 # 冻结输出信封 + fail-closed 权威读取
│   ├── config.py / env.py        # 配置解析（env 覆盖优先级）与 .env 加载
│   ├── skills.py                 # 进化接口：load_skill_genome / load_policy_genome
│   ├── evolution_api.py          # 进化接口：Detection Truth 只读投影
│   ├── runs.py                   # 运行记录：双指纹 + 每案例过程落盘 + 台账推导统计
│   ├── cli.py / __main__.py      # python -m sehd_rise2.detector
│   └── configs/detector_v1.json  # 三路由最小配置
├── evolution/                    # 自进化框架
│   ├── gap_taxonomy.py           #   冻结能力缺口词表（单一真源）
│   ├── diagnosis.py              #   机械失败分类 + 诊断协议校验
│   ├── learning_case.py          #   Learning Case schema + 三层校验器
│   ├── assemble.py               #   装配 / 校验 / 报告 CLI
│   ├── policy.py                 #   L2 策略层：词表 + 候选集 + genome 构建
│   ├── replay.py                 #   离线重放：绑定校验 + 裁决重放 + oracle/runtime 对比
│   ├── binder.py                 #   运行时证书类绑定器（label-free 感知阶段）
│   ├── proposal.py / cycle.py    #   L2 提案生成 + 门控进化循环（累积重放门 + 台账）
│   └── convention_evolution.py   #   约定进化：复现过滤 → 提取 → G1 → 配对 G2
├── runs/                         # 运行结果目录（本地产物；布局见 runs/README.md）
└── tools/check_layers.py         # 层级守卫（detector ← evolution 单向）

tools/                            # 仓库级测量/审计工具
├── run_stability.py              #   双 run 判决稳定性：一致率、FP/FN Jaccard、
│                                 #   census 集稳定性、翻面阶段解剖
├── audit_packet_ids.py           #   run 树 packet/证据引用完整性审计
├── build_eval_split.py           #   评测抽样（分层不变量 + manifest）
└── build_phase2_split.py         #   Phase 2 进化/验证 split

datasets/learning_cases/v1/       # Phase 0 数据资产（进版本控制）
├── annotations.json              #   人工层单一真源（49 例，已最终复核）
├── cases/*.json                  #   100 个 Learning Case（投影，可重装配）
└── REPORT.md                     #   能力缺口分布报告

datasets/policy_candidates/v1/    # Phase 1-A/B/C 数据资产（进版本控制）
├── policy_candidates.json        #   L2 策略候选集（5 条 replayed + NS-1 弃权发现）
├── certificate_bindings.json     #   oracle 证书类绑定（162 条，label-free 纪律）
├── runtime_bindings.json         #   运行时绑定器产物（machine_generated）
├── policy_genome.json            #   P2-P5 冻结 genome（load_policy_genome 可加载）
├── replay_result.json / REPORT.md            # oracle 重放结果与报告
├── runtime_replay_result.json / RUNTIME_REPLAY_REPORT.md   # 对比结果

datasets/eval_splits/             # 冻结评测 split 与轮 manifest（进版本控制）

tests/                            # 契约单元测试（140 例全绿）
experiments/                      # 本地实验工作区（产物被 git 忽略）
```

## 用法

### 独立检测器

```bash
# 必需凭据；凭据值绝不写入配置文件。
set DEEPSEEK_API_KEY=<secret>

python -m sehd_rise2.detector --record trajectory.json --output-dir out
```

- 输入 JSON 必须包含非空 `history`，且不得包含 `is_hallucination`、
  `hallucination_step`、`gold_label` 等 gold 字段（检测入口拒绝）；
- `--config` 缺省使用随包 `configs/detector_v1.json`；
- 输出 `out/prediction.json`（冻结信封 `sehd2.detection_result.v1`）；
- 基因组部署是显式的：`--genome` / `--policy-genome` 部署进化出的双层能力，
  不引入框架级全局状态。

Provider 路由解析顺序：`SEHD_ROUTE_<ROUTE>_*` → `SEHD_DETECTOR_*` →
`SEHD_PROVIDER_*` → JSON 配置 → DeepSeek 官方端点/模型默认值；进程环境优先于
仓库 `.env`。

### 稳定性测量（噪声地板仪器）

```bash
python tools/run_stability.py runs/<run_a> runs/<run_b> [--out report.json]
```

任意两个 run 的判决一致率、FP/FN 集合 Jaccard、census 候选集稳定性与
翻面阶段解剖。任何「修复前后对比」都必须先对照这个噪声地板再归因。

### 约定进化循环（fix-val Round 2+）

```bash
python -m sehd_rise2.evolution.convention_evolution extract \
    --run runs/<id> --out conventions_raw.json   # 内建失败复现过滤
python -m sehd_rise2.evolution.convention_evolution gate \
    --raw conventions_raw.json --out conventions_gated.json
python -m sehd_rise2.evolution.convention_evolution genome \
    --conventions conventions_gated.json --out convention_genome.json
python -m sehd_rise2.evolution.convention_evolution validate \
    --run runs/<id> --conventions conventions_gated.json --out-dir <dir>
```

`validate` 是冻结 census 前缀的配对重放：每案例一次 unguided 全量滚动
冻结候选集，guided 臂只重跑裁决阶段并注入约定——配对差异归因于约定；
发现层伤害须两次全量重放都定罪才算。部署 =
`Detector.detect(..., evolved_conventions=genome)`（三阶段 prompt 注入）。

### Phase 0 学习案例装配（离线复盘）

```bash
python -m sehd_rise2.evolution.assemble assemble \
    --run runs/random100_20260815_142446 \
    --dataset datasets/raw/agenthallu/AgentHallu \
    --annotations datasets/learning_cases/v1/annotations.json \
    --out datasets/learning_cases/v1
python -m sehd_rise2.evolution.assemble validate --dir datasets/learning_cases/v1/cases
python -m sehd_rise2.evolution.assemble report \
    --dir datasets/learning_cases/v1/cases --out datasets/learning_cases/v1/REPORT.md
```

装配是投影：检测事实真源 = run 树，gold 真源 = 数据集原始文件，人工层真源 =
`annotations.json`。修改标注后重跑 assemble，不手改 `cases/` 投影。

## 公共 API

```python
from sehd_rise2.detector.api import Detector, detect
from sehd_rise2.detector.evolution_api import project_for_evolution
from sehd_rise2.detector.skills import load_skill_genome, load_policy_genome
```

层级导入纪律（`detector` 无外部依赖、`evolution → detector` 单向）由
`python sehd_rise2/tools/check_layers.py` 机械强制。

## Phase 0 失败理解层（交付摘要，2026-08-15）

产出一套"失败理解基础设施"，由三部分组成：

1. **Learning Case Dataset**：100 例结构化（机器装配 + 49 例人工标注），每例为
   事实（`failure_analysis`，机械推导可审计覆写）→ 假设（`failure_hypothesis`）
   → 能力（`capability_analysis`，词表约束）三层；
2. **Capability Gap Taxonomy（已冻结）**：observation / verification / policy /
   attribution（仅诊断）/ infrastructural / no_gap 六类；`gap_allowed` 是唯一
   门函数，冻结后拒绝一切词表外取值；
3. **Meta Skill Diagnosis Protocol**：`sehd2.capability_diagnosis.v1`（无
   confidence，携带 `diagnosis_source`），离线 gold 对齐版为 Phase 0 基准，
   在线版必须消费 label-free 视图。

### 能力分布（49 例，标注经最终复核）

| 类别 | 数量 | 细目 |
|---|---|---|
| attribution（仅诊断） | 25 | locus_vs_manifestation 22、tie_ambiguity 3 |
| policy | 14 | strictness_miscalibration 8、expression_tolerance 3、adversarial_defense_overturn 2、evidence_weighting 1 |
| observation | 6 | unsupported_claim_grounding 4、missing_required_action 2 |
| verification | 4 | excuse_based_rejection、source_credibility_conflict、unverified_derivation、exactness_boundary |

**关键实证**：FN 12 例机械阶段（census2/verification4/defense6）经语义分析后
（census6/verification4/defense2）——"失败发生的位置 ≠ 能力缺失的位置"；
12 FP 全部为 policy 类（判决边界过严）。

**Phase 1 优先级信号**（capability-driven，供立项决策）：
policy（L2 决策边界）14 例 > observation（L1 候选发现）6 例 > verification
（evidence skill）4 例；attribution 是测量层改进，不生成能力。

## Phase 1-A L2 策略候选集（交付摘要，2026-08-15）

Phase 1 立项为 **L2 决策边界进化**（评审裁决：12 FP 全部 policy 类，且可在
100 例 run 树上零 provider 成本离线重放）。P1-A 把 12 个 policy 类 FP 提取为
第一批 **L2 Policy Candidate Set**，保持评审要求的三层结构
（failure case → policy hypothesis → candidate policy item）：

- **5 条策略、12/12 FP 全覆盖**，机制聚类横切 Phase 0 的 gap name：
  L2-P1 建议性要求不承担定罪（5 例）、L2-P2 表现层添加≠捏造（2 例）、
  L2-P3 散文重述粒度≠误报（2 例）、L2-P4 任务/用户前提优先于外部纠错（2 例）、
  L2-P5 单切片缺席不定罪（2 例）；
- **防案例级规则化的机械约束**：trigger 是封闭证书类词表（`policy.py` 单一真源），
  校验器拒绝单案例策略与含 case id 的 trigger 定义；
- **FN/TP 风险显性化**：每条策略强制声明 `fn_risk`、`do_not_apply` 反例
  （BFCL/027、Camel/053、BFCL/104 等）与 `tp_must_survive` 证据；
  动作词表含 `require_corroborating_content_error`（Camel/039 反例驱动）。

上限（全部触发且零回归）FP 12→0、决定性准确率 76%→88%——是上限不是承诺。
候选集 `review_status: first_pass`，复核通过后进入 P1-B 离线重放。
交付详情见 [EvoGuard 实验计划与迭代路线文档](EvoGuard_experiment_plan_and_iteration_roadmap.md)。

## Phase 1-B 离线策略重放（交付摘要，2026-08-15）

`sehd_rise2/evolution/replay.py` 在 100 例 run 树上**只重放确定性裁决**
（三阶段 provider 输出原样读取，零 provider 调用），fail-closed：绑定不全、
类出词表、baseline 无法复现记录状态一律拒绝重放。策略通过**证书类绑定层**
起作用：162 张幸存证书逐一绑定六类之一（label-free 工作表纪律 + basis 审计
+ gold 极性暴露表）。32 个配置（baseline / 5 单策略 / 26 组合 / 全集）结果：

- **P2+P3+P4+P5 零 TP 损失**：FP 12→5、准确率 76%→83%——通过评审最低
  晋升门（acc ≥ baseline + FP 明显降 + FN +0）；
- **全集 86%（FP 12→0）但 FN +2**：OpenManus/048（gold=clean）与
  067/072（gold=error）的定罪证书机制完全同构（terminate 不交付已提取
  答案），证书层不可分——P1 的 delivery-form 子机制需精化后才能全集部署；
- **corroboration 动作验证有效**：Camel/039、Camel/070、Octotools/019 的
  requirement_compliance 证书被同案例 content 证书佐证，定罪存活；
- **全部 32 配置 must-survive 违例 = 0**（P1-A 声明的四例 TP 哨兵全存活）。

候选集保持 `first_pass`（评审纪律：重放不等于晋升）。P2-P5 四类零 gold-error
证书的过拟合张力已写入报告——部署前须先有可复现绑定器。
交付详情见 [EvoGuard 实验计划与迭代路线文档](EvoGuard_experiment_plan_and_iteration_roadmap.md)。

## Phase 1-C 策略运行时契约 + 证书绑定（交付摘要，2026-08-15）

评审最终裁决落地 + L2 从离线分析工具走向运行时能力的三步：

1. **裁决落地**：证书类词表新增 `delivery_form` **弃权类**（不挂策略）；
   NS-1 不可分发现入册（048 gold-clean vs 067/072 gold-error 证书层同构，
   分离需要 L1 证据能力而非更细规则）；P1 触发定义剔除 delivery-channel。
   零已知回归原则下 oracle 重放：**精化 P1 与 P2-P5 全部零 TP 损失，
   全集 87.0%（FP 12→1，FN +0）**。
2. **C1 Policy Runtime Contract**：`build_policy_genome` 把过门策略冻结为
   `sehd2.policy_genome.v1`（动作词表封闭且仅含 withhold /
   require_corroborating，绝不改写裁决）；`policy_genome.json`
   （P2-P5）经检测器侧 `load_policy_genome` 无适配加载（跨层测试证明）。
3. **C2/C3 感知闭环**：`binder.py` 运行时绑定器——输入契约 8 字段封闭
   allowlist + 三重 label-free 机械扫描 + **unclassified 弃权**（不确定
   → 策略不触发 → baseline 行为）；`replay.py compare` 输出 oracle vs
   runtime 的 agreement / unclassified rate / 逐证书分歧 / 逐配置
   decision delta。**实测（v4-flash，162 证书）**：绑定一致率 84.0%，
   但 runtime 全集仅 77%（oracle 87%）——oracle 的 11 例 FP 修复只回收
   2 例（难例系统性偏向 content），premise_conflict 过宽损失 1 例 TP，
   unclassified 0 使用（从不弃权）。**结论：语义已验证、感知是瓶颈**，
   binder 校准是 P1-D 前的迭代清单；正面信号是 binder 独立复现了
   NS-1 弃权簇（048/067 → delivery_form）。
4. Meta Skill 新输出类型 `non_separable_under_current_representation`
   已由 NS-1 首次行使——不是所有能力缺口都必须产出策略，
   「当前证据表示下不可分 → 弃权」是合法且更健康的进化结论。

交付详情见 [EvoGuard 实验计划与迭代路线文档](EvoGuard_experiment_plan_and_iteration_roadmap.md)。

## Phase 1-C.5 Binder Calibration（交付摘要，2026-08-15）

三项校准任务全部落地，交付形态是**可迭代的校准闭环**而非一次性调参：

1. **判别式证书类定义**（`policy.py::CERTIFICATE_CLASSES`）：七个类从描述性
   prose 改为边界定义——每类 `CONVICTS WHEN`（必须通过的正判据）+
   `NOT WHEN`（易混邻类重定向）；`content_contradiction` 降为**残余基线类**
   （声明序末位，仅在所有特定类测试失败后绑定）。premise_conflict 收紧为
   三重正判据：具体值**继承自任务前提**（要求引用前提原句）+ 定罪权威为
   外部知识 + 无引用观察反对；agent 自 authored 的错误推导（即使靠外部
   计算定罪）明确排除到 content_contradiction。
2. **弃权结构化**（`binder.py`）：绑定答案新增必填 `confidence` 字段，
   `normalize_binding` 将 low 置信**机械降级**为 unclassified（harness 强制，
   不信任模型自觉）；提示词改为有序判别流程——特定类先行、content 残余、
   绑 content 前强制双边对照测试（前提 / 近平价 / 呈现性展开 / 单切片缺席）。
3. **校准读出仪器**（`replay.py compare`）：类间混淆矩阵（oracle × runtime）
   + 过绑/漏绑方向计数，每轮迭代的分歧簇直接定位下一个定义修正点。

**三轮校准迭代 + 一次模型扩容探针的实证轨迹**（162 证书，oracle 绑定为靶）：

- 一致率 84.0%（1-C3 基线）→ 81.5% → 88.9% → **92.0%**；
  content 侧精度 140/142，premise_conflict 过绑（1-C3 的 FN 源）降为
  **0**，FN 回归消除（全集 12 = baseline），**全部 32 配置 TP 损失 = 0**；
- 中间迭代的混淆矩阵捕获并修复了两次回归（requirement_compliance 过绑
  19 例：错误的动作条款 + content 证明门槛过高），证明仪器本身是必要的；
- **v4-pro 探针**（8 个 FP 侧漏绑案例定向重绑）只修复 2/10——模型扩容
  无法关闭缺口。

**结论与残余**：runtime 全集 78%（oracle 87%），FP 12→10。剩余 10 例 FP
全部同向：证书自身以 "contradiction" 措辞断言偏差，flash 与 pro 均继承该
框架；部分案例（如码→名展开的正确性）所需的验证材料不在 label-free 输入
表示内。**瓶颈已从「binder 推理」重新定位为「证据表示」**——verification
LLM 生产 rationale 时知道自己的定罪依据（对照措辞 / 外部知识 / 单切片
缺席），但该信号在证书表示中被丢弃。表示层修复（证书生产时记录 basis
信号）属检测器侧变更，冻结检测器的 P1-D 不动它；进入 Phase 2 进化清单。

## 修复-验证迭代（fix-val，当前阶段）

Phase 0 → Phase 1-D 之后进入持续迭代：每轮随机重抽 100 例（60 进化 +
40 验证），轮内「重抽 → 轮基线（附双跑稳定性测量）→ 修复 → 进化 →
配对验证 → 反馈归因」。完整协议、门与判据的真源是
[FIX_VALIDATE_ITERATION_PLAN.md](FIX_VALIDATE_ITERATION_PLAN.md)。

已落地的关键实证与修复：

- **Round 1**（commit 88392b0）：约定层抽取（prompt 去 BFCL 硬编码）、
  census 视野全量化、证据窗口 ±2、三态判决 + FN 归因管道；通道 A
  （census 探针）两轮 G2 后被证伪删除，重设计为约定进化。
- **Round 1.5**（本轮）：重诊断确立**判决噪声地板**——同 100 例双跑
  翻面 24%、FP/FN 集合 Jaccard 0.39/0.48（单轮错误普查约一半不可
  再现）、纯 test-retest（temp=0）翻面 10-30%，census 候选集不稳定
  （Jaccard 0.66）是最大噪声注入点。自进化「前后无差异」的三个机制
  bug（提取学噪声 / G2 非配对 / 执行面缺 2/3）全部修复：
  evolved 约定注入全部三阶段 prompt、`frozen_census` 冻结前缀配对
  重放、worksheet 失败复现过滤、`detector_fingerprint` 覆盖判决面
  源码、`tools/run_stability.py` 稳定性仪器。

## 学习口径（label-free 设计边界）

- **框架不宣称能发现漏检。** label-free 运行没有任何信号能判定「gold 为 error
  却被判 clean」，因此默认模式下不存在 FN 触发的修复路径。FN 信号的检测能力是
  评测侧（run 树 + gold join）与失败理解层（Learning Case）的职责，不是检测器
  自身的职责。
- **失败理解层只读。** Learning Case 含 gold，是 evaluator 侧产物，任何代码路径
  不得将其传入 `Detector.detect` / `validate_record`；进化侧不 import 检测运行时。

## 下一步

Round 2 起步序列（协议见 FIX_VALIDATE_ITERATION_PLAN.md §零·五）：
重抽 100 → 双跑轮基线附 `run_stability` 报告 → extract（内建复现
过滤）→ G1 → 冻结前缀配对 G2 → 部署 → 40 例 fresh 配对验证（判据
相对噪声地板解释）。

待用户拍板的成本决策：在线判决稳定性方案——判决级 k=3 多数投票
（成本 ×3，按 churn 20% 推算翻面率降至 ~10%）或裁决阶段换非 flash
模型；两者都改变预算结构，选定后才并入 Round 2。

## 历史沿革

本仓库由 SEHD-RISE 重建而来：旧框架（`sehd_rise/` 及其测试、设计文档）与
重构前基线 `src/sehd_v6/` 已在 2026-08-15 从仓库移除（git 历史保留，
本地不留残余目录）。当前仓库的检测、实验与文档全部基于 `sehd_rise2/`。

## 许可证

私有研究仓库。
