# SEHD-RISE2 — 失败理解驱动的自进化幻觉检测器

SEHD-RISE2 是 SEHD-RISE 的重建工作区（`sehd_rise2/`）。当前形态由两部分组成：

1. **最小冻结检测器**（`sehd_rise2/detector/`）——从零重写的独立检测器：
   census（候选发现）→ verification（证据核验）→ defense（对抗复核）→
   确定性法庭聚合 → fail-closed 权威信封。它是当前一切工作的冻结基座。
2. **Phase 0 失败理解层**（`sehd_rise2/evolution/`）——自进化框架的第一阶段
   基础设施：把检测失败结构化为 **事实 → 假设 → 能力缺口** 三层 Learning Case，
   从真实失败分布建立并**冻结** Capability Gap Taxonomy（词表），并为未来
   Meta Skill 定义诊断协议。它只理解失败，不生成 Skill——生成 Skill 的 Phase 1
   将由数据决定第一个进化目标（capability-driven，不预设 L1）。

100 例真实实验（`runs/random100_20260815_142446`）决定性准确率 76.0%、
FN=12 / FP=12；49 个失败案例的能力归因与 Phase 0 交付见
[SEHD_RISE2_Phase0_失败理解层实施文档.md](SEHD_RISE2_Phase0_失败理解层实施文档.md)。

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
│   ├── provider.py               # provider 传输：直连 HTTPS / 重试 / JSON 修复
│   ├── packet.py                 # 轨迹数据包 + gold 输入防火墙
│   ├── result.py                 # 冻结输出信封 + fail-closed 权威读取
│   ├── config.py / env.py        # 配置解析（env 覆盖优先级）与 .env 加载
│   ├── skills.py                 # 进化接口：load_skill_genome / load_policy_genome
│   ├── evolution_api.py          # 进化接口：Detection Truth 只读投影
│   ├── runs.py                   # 运行记录：每案例过程落盘 + 台账推导统计
│   ├── cli.py / __main__.py      # python -m sehd_rise2.detector
│   └── configs/detector_v1.json  # 三路由最小配置
├── evolution/                    # Phase 0 失败理解层（已落地，见 §Phase 0）
│   ├── gap_taxonomy.py           #   冻结能力缺口词表（单一真源）
│   ├── diagnosis.py              #   机械失败分类 + 诊断协议校验
│   ├── learning_case.py          #   Learning Case schema + 三层校验器
│   └── assemble.py               #   装配 / 校验 / 报告 CLI
├── runs/                         # 运行结果目录（本地产物；布局见 runs/README.md）
└── tools/check_layers.py         # 层级守卫（detector ← evolution 单向）

datasets/learning_cases/v1/       # Phase 0 数据资产（进版本控制）
├── annotations.json              #   人工层单一真源（49 例，已最终复核）
├── cases/*.json                  #   100 个 Learning Case（投影，可重装配）
└── REPORT.md                     #   能力缺口分布报告

tests/                            # 状态机单元测试（284 例全绿）
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

## 学习口径（label-free 设计边界）

- **框架不宣称能发现漏检。** label-free 运行没有任何信号能判定「gold 为 error
  却被判 clean」，因此默认模式下不存在 FN 触发的修复路径。FN 信号的检测能力是
  评测侧（run 树 + gold join）与失败理解层（Learning Case）的职责，不是检测器
  自身的职责。
- **失败理解层只读。** Learning Case 含 gold，是 evaluator 侧产物，任何代码路径
  不得将其传入 `Detector.detect` / `validate_record`；进化侧不 import 检测运行时。

## 下一步

Phase 0 已收尾（最终复核 + 词表冻结，见实施文档 §6）。下一步是 **Phase 1 立项**：
依据 §能力分布选择第一个进化目标（数据支持 policy 优先），并在
`sehd_rise2/evolution/` 中实现对应的确定性可进化面，经过跨案例可验证结局奖励
证明后再谈推广。

## 历史沿革

本仓库由 SEHD-RISE 重建而来：旧框架（`sehd_rise/` 及其测试、设计文档）与
重构前基线 `src/sehd_v6/` 已在 2026-08-15 从仓库移除（git 历史保留，
本地不留残余目录）。当前仓库的检测、实验与文档全部基于 `sehd_rise2/`。

## 许可证

私有研究仓库。
