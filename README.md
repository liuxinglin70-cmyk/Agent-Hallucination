# SEHD-RISE — 幻觉检测器与自进化框架

SEHD-RISE（Repairability-driven Integrated Self-Evolution，可修复性驱动的一体化自进化）
由一个法庭式幻觉检测器（PACA-TCL v3.5）和一个以可修复性为中心的自进化框架组成。
因果修复层先判定一次检测到的失败是否可修复；只有可修复的失败才进入进化，并经由
L1/L2 两个可进化面共享的统一 RepairPlan（修复计划）协议完成修复——L1 是
Skill 基因组（六个模块、七个确定性抑制/选择操作及一个受限构造操作），L2 是裁决策略基因组（封闭词表策略项，
由证明法庭（proof court）确定性地求值）。每次修复尝试都由五层结果漏斗（outcome
funnel：框架 / 搜索 / 接地 / 执行 / fitness）归类，并携带从投递（dispatch）到跨案例迁移的完整因果归功链。

框架代码位于 `sehd_rise/`（13 层 + 根入口）。重构前基线 `src/sehd_v6/` 仅作为本地、
未被 git 跟踪的历史备份保留（见 `src/README.md`）；被跟踪的树中没有任何内容依赖它。

框架架构与设计契约见
[docs/design/TECHNICAL_DESIGN.md](docs/design/TECHNICAL_DESIGN.md)。

## 核心目标与设计纪律（必读 / Mandatory）

在本仓库中工作的每个 agent——无论涉及框架代码、实验还是文档——都必须先读本节并
遵守。本节是「项目为何存在、决策如何作出」的唯一真源；下游文档只引用它，从不复述。

### 核心目标

造一个**会自己越改越准的幻觉检测器**：自进化的幻觉检测效果能够达到 SOTA。检测器
基座（PACA-TCL v3.5）已冻结；一切改进必须来自两个可进化面（L1 post-court Skill、
L2 裁决策略），且必须由跨案例的可验证结局奖励证明——gold 标签在编辑未参与生成的
案例上测得——而不是仅靠框架内部信号或编辑来源案例自身的重放。每一项框架改动都要
以它对这一目标的贡献来辩护；正确性、可审计性与调用效率的工作是使能项，从来不是
目的本身。

### 设计理念

1. **系统性优于打补丁。** 用结构化的自底向上重设计修根因；绝不堆积特判 `if`、
   手调旋钮或场景映射（见 First-Time Principle：单一真源、不留残渣、本地/部署
   一致）。
2. **证据驱动的门控，不手调阈值。** 接受决策只依赖可观测的回放效应、封闭词表
   契约与 gold 极性——绝不依赖手调数值阈值或 CRS 权重（权重冻结为
   0.35/0.15/0.20/0.20/0.10）。
3. **失败即关闭（fail-closed）纪律。** 权威缺失、输出不可解析或词表违例一律拒绝；
   不确定从不冒充裁决或被接受的编辑。
4. **可进化面对修正封闭。** 每个能产生缺陷产物的层必须也能修复或移除它（L1：
   增/改/删规则；L2：`add_item` / `modify_item` / `remove_item`）。
5. **Gold 隔离。** gold 标签（金标）只用于选择极性与 fitness 归功；绝不进入技能
   内容、策略内容或反思提示词。
6. **Provider 纪律。** 只允许 DeepSeek-V4-Flash / DeepSeek-V4-Pro 路由；凭据值
   绝不进入代码或产物。

### 导师要求（2026-08 讨论）

1. **绝不硬调阈值。** 任何一个手调阈值最终总是错的；优先把经验判断表达为进化出的
   技能（agent 依据积累的经验作判断，而不是依赖一个人工挑选的数字）。
2. **鼓励单一阈值的替代方案。** 多阈值 / 多输出配 best-of-N 选择（测试时扩展，
   test-time scaling）是正当方向；拓宽可选空间，而不是去调一个值。
3. **回归式验证（如 canary 哨兵回归）是可优化的机制**，在值得其成本的地方选择性
   采用；它是护栏，不是正确性权威。
4. **技能效果必须在独立验证集上验证。** 进度汇报必须展示具体的中间实验输出，
   而不只是框架描述。

实验协议必须说明它们如何遵守本节；评审在接受结果之前先检查合规性。

## 目录结构

```text
sehd_rise/                # 框架本体（13 层 + 根入口）
├── platform/             #   env / routes / models / usage / gold / jsonio
├── skills/               #   L1 基因组 / L2 策略基因组 / 规则槽位 / 词表
├── detection/            #   packet / transport / court / policy / injection / detector
├── causal/               #   signals / cut / episode / repairability / plan
├── experience/           #   机制级经验记忆（单一 Experience Truth）
├── typing/               #   HTC-TES 咨询层：classifier / guidance / slices
├── planning/             #   L1/L2 反思、检索、候选、搜索、反馈
├── execution/            #   action / executor / replay / trace
├── evaluation/           #   effect / fitness / canary / gate / promotion
├── meta/                 #   跨案例策略学习器（strategy / review）
├── loop/                 #   统一编排（machine / runner / ledger / …）
├── api/                  #   公共门面：detection / evolution / manifests
├── cli/                  #   python -m sehd_rise <evolve|detect>
├── configs/              #   检测器配置（detector_v3_5.json）
└── __main__.py           #   根入口，分发到 cli

docs/design/              # 框架技术设计（核心文档）
tools/check_layers.py     # 无环层级纪律检查
tests/                    # 状态机单元测试
experiments/              # 本地实验工作区（产物被 git 忽略）
datasets/                 # 原始数据集（仅本地，被 git 忽略）
src/sehd_v6/              # 重构前冻结基线（本地备份，未被 git 跟踪）
```

层级依赖规则是无环的，由 `tools/check_layers.py` 机械强制；规则表见
TECHNICAL_DESIGN.md。

## 用法

### 独立检测器

检测器可以脱离进化独立运行。输入 JSON 必须包含非空的 `history`，且不得包含
`is_hallucination`、`hallucination_step`、`gold_label` 等数据集 gold 字段。

```bash
# 必需凭据。路由级、用途级环境变量可覆盖 provider 默认值；
# 凭据值绝不写入配置文件。
set DEEPSEEK_API_KEY=<secret>

python -m sehd_rise detect \
  --config sehd_rise/configs/detector_v3_5.json \
  --record trajectory.json \
  --case-id example \
  --output-dir detector_output
```

预测结果写入 `detector_output/prediction.json`。同一命令支持批处理模式
（`--records`，配合 `--max-parallel` / `--limit` / `--no-resume`）。
Provider 设置全程遵循一条优先级规则：

- `SEHD_ROUTE_<ROUTE>_*`，例如 `SEHD_ROUTE_DEFENSE_MODEL`
- `SEHD_DETECTOR_*` 或 `SEHD_JUDGE_*`，按路由职责选择
- `SEHD_PROVIDER_*`
- JSON 配置中的值
- DeepSeek 官方端点/模型默认值

支持的后缀有 `ENDPOINT`、`MODEL`、`API_KEY_ENV`、`TEMPERATURE`、`MAX_TOKENS`、
`TRANSPORT_RETRIES`、`TIMEOUT_SECONDS` 与 `TOTAL_TIMEOUT_SECONDS`。进程环境优先于
仓库 `.env`。

基因组部署是显式的：`--genome` / `--policy-genome`（或
`detect_record(..., genome=..., policy_genome=...)`）部署进化出的双层能力，
不引入任何框架级全局状态。

### 独立自进化

进化框架接受原始轨迹（单案例或批量），运行「检测 → 因果分析 → 可修复性分诊 →
RepairPlan → 执行 → 评估」循环，并写出进化后的冠军基因组与机制级经验记忆。

```bash
python -m sehd_rise evolve \
  --records records.jsonl \
  --config sehd_rise/configs/detector_v3_5.json \
  --output-dir evolution_output
```

`records.jsonl` 每行一条记录；每行可以是 `{"case_id": "...", "record": {...}}`，
也可以是内嵌 `case_id` 的记录本体。`case_id -> record` 的 JSON 映射对象同样可用。

常用的预算与续跑参数：

```bash
python -m sehd_rise evolve \
  --records records.jsonl \
  --config sehd_rise/configs/detector_v3_5.json \
  --output-dir evolution_output_2 \
  --reflection-candidates 3 \        # 每案例的独立 mutation 采样数（1..5）
  --max-mutation-attempts 2 \        # 每触发案例的 mutation 重试数（1..8）
  --initial-genome evolution_output/champion_genome.json \
  --initial-policy-genome evolution_output/policy_genome.json \
  --experience-memory evolution_output/htc_tes_experience_memory.json \
  --resume-from evolution_output/evolution_checkpoint.json
```

运行模式（§10 三分法）与 minibatch meta 节奏：

```bash
  --run-mode supervised_evolution|label_free_diagnostic|protected_label_free_adaptation \
  --meta-update-every 5              # 每 5 个 case 刷新一次搜索策略（默认 5；0 = 仅 run 后）
```

写入输出目录的产物：

- `evolution_result.json` — 含五层结果漏斗的审计轨迹
- `champion_genome.json` — 进化出的 L1 技能基因组
- `policy_genome.json` — 进化出的 L2 裁决策略基因组
- `htc_tes_experience_memory.json` — 机制级经验记忆
- `htc_tes_coverage.json` — 类型化覆盖投影
- `evolution_state_manifest.json` — 哈希绑定的状态清单

Provider 路由从仓库根的 `.env` 加载（或进程环境；进程值优先）。凭据值绝不写入
代码或产物。默认 mutation 路由为 DeepSeek 官方 V4-Flash；完整路由解析顺序见
TECHNICAL_DESIGN.md。

## 公共 API

面向应用的稳定导入是 `sehd_rise.api.detection` 与 `sehd_rise.api.evolution`：

```python
from sehd_rise.api.detection import detect_record, load_detector_config
from sehd_rise.api.evolution import run_evolution, load_records
```

层级导入遵循 15 层包结构（`platform`、`skills`、`detection`、`causal`、
`experience`、`learning`、`typing`、`planning`、`execution`、`evaluation`、`meta`、
`loop`、`api`、`cli`）；无环导入纪律由 `tools/check_layers.py` 强制。

## 运行产物

运行产物放在本地 `experiments/` 工作区（被 git 忽略），框架不依赖任何固定系统
路径。框架不缓存 provider 响应：每次调用都直达 HTTPS，重跑即重算。

## 关键设计决策

1. **可修复性先于进化。** `causal/repairability.py` 是唯一的触发权威：只有
   `REPAIRABLE` 的失败进入技能进化；已归因但当前 Skill 语言无法表达的修复进入
   `CAPABILITY_GAP`，绝不伪装成 `NON_EVOLVABLE` 或替代动作；注册表声明有精确能力
   却无法生成 RepairPlan 才是 `UNADDRESSABLE` 框架不变量违例。
2. **一个因果对象，一个 RepairPlan 协议。** 所有下游层消费同一个
   `CausalFailureEpisode` 与规范 `RepairPlan`（失败身份 / 层 / 属主 / 目标边 /
   执行器 / 合法操作 / target domain / grounding query / binding schema / 效应契约 /
   评估契约）；没有哪一层重新发明失败语义。
3. **注册表闭合且实体绑定的 L1 Skill。** `skills/capabilities.py` 是操作、读写面、
   方向、binding domain/字段/基数、executor、fitness postcondition 与负能力的唯一
   真源。规则文本 `When <condition>, <action>.` 只用于持久化/反思；运行时由类型化
   `condition_spec` 返回同一实体上的 `Binding[]`，通过 capability binding contract 后
   才派发确定性操作，不做 aggregate bool 或 first-match 回退。`construct_candidate`
   只能把一个预先确定、无 gold 的 `ClaimAnchor` 交给冻结 constructor；随后只局部续跑
   qualification / focused verifier / defense / proof-court，不重采样 census，也不授予 Skill
   证据、证书或终态裁决权。
4. **封闭词表的 L2 策略，对修正封闭**：策略项把一个封闭前置谓词与一个枚举动作
   配对；运行时语义由确定性的证明法庭求值器（`detection/policy.py`）拥有，
   增/改/删让缺陷策略可修复而不是永久滞留。
5. **五层结果漏斗取代 `skip`。** 每次修复尝试恰好终止于框架 / 搜索 / 接地（grounding）/
   执行 / fitness 五层共 26 种终态之一（`loop/machine.py`）；binding not found /
   ambiguous / domain mismatch 与 construction 类终态都发生在 proposal 验证之后、
   executor 派发之前，归入 grounding 层单独计数，执行层只统计真正派发后的失败——
   框架/搜索空间瓶颈从此不会被压缩进一个 skip 桶，也不会伪装成 executor 失败。
6. **收敛只消费完整的修复尝试。** 预算耗尽、基础设施故障与无效提案都不是学习
   证据；收敛窗口只统计「完整执行且已测效应」的修复在 fitness 层遭到的拒绝。
7. **执行先于归功，效应先于 fitness。** 一个被接受的编辑携带完整链条——投递 →
   目标边移动 → 阻塞谓词清除 → 终态改进 → canary 保持——中间任何一层都不得替代
   下一层的证据。
8. **HTC-TES 是咨询层。** 类型智能（`typing/`）组织经验、检索与评估切片；它从不
   推翻因果属主。
9. **Meta 学策略，不学真相。** meta 层（`meta/`）是五域封闭词表上的跨案例策略
   学习器（搜索先验 / 操作偏好 / 机制排序 / 重试策略 / 候选多样性）；属主、因果
   切点、可修复性与硬门控通道在类型层面不可表示。五域策略的权威形态：
   `search_prior.require_independent_anchor` 是唯一带确定性控制效果的 token——它
   收紧 contrast 邻居选择（邻居的已注册属主必须不同于当前切点属主，否则跳过对比
   回放）。其余四域（operation_preference / mechanism_ranking / retry_strategy /
   candidate_diversity）以及 search_prior 的另外两个 token 仅以
   `authority=evolution_guidance_only` 投影进反思提示词，作为有界、封闭词表的
   引导文本；它们不参与任何确定性决策——候选生成、重试上限、门控、canary、晋升
   都不读它们。
10. **失败即关闭、gold 隔离、冻结基座纪律**，如上文必读章程所述。
11. **实体身份与泛化谓词分离。** 训练期实体按 `target_entity_id` 精确绑定（不是
    靠谓词去「猜」唯一命中）；`condition_spec` 只描述 skill 将来复用时的条件。过宽的
    泛化谓词因此不再把源回放打成 `BINDING_AMBIGUOUS`，而跨案例复用仍按 `condition_spec`
    在新案例 universe 上重新求值，不固化训练期实体 id。
12. **FN 能力缺口是 provider-side 问题，不在 L1 确定性层填补。** 七个未覆盖 FN 家族
    （missing_evidence_*、wrong_qualification、missing_verifier_route、
    wrong_verifier_instruction、missing_certificate_path、coverage_blind_spot）需要
    provider-side / L2 新能力；`fn_capability_coverage_matrix` 记录其
    `required_capability` 与 `blocker` 作为课程输入，绝不新增会跨越权威边界的生成性
    opcode（Skill 不得写证据/证书/verdict）。由此，L1 自进化的诚实定义是**固定
    primitive 词表上的规则特化**（学 condition / anchor 判别器）：`CAPABILITY_GAP`
    被识别后只记录为课程输入（RepairIntent + fn_family + 归档信号），本框架不宣称
    能发明新 detector capability；能力合成管线是明确的后续工作，不是当前行为。

## 下一步正式实验（规划，尚未实施）

以下项目属于**实验层**而非框架：它们只有在框架能正确运行时才有意义，实现必须落在
`experiments/`（通过公共 API/CLI 消费框架），绝不得加入 `sehd_rise/`。此处先记录规划，
作为后续正式实验与试运行的目标清单。

1. **密封留出集 + 公开基线对标。** 建立独立、密封的 holdout（切分脚本只存在于
   `experiments/`），固定口径对标公开 SOTA 基线（AgentHallu 系列、GPT-4o/Claude judge、
   冻结 PACA-TCL v3.5 自身），并同时报告决定性准确率、含 unresolved 的 F1 与
   hallucination-step 定位 F1。
2. **把"泛化"变成选择信号。** 周期性在密封 holdout 的固定子集上重放冠军基因组，用
   跨案例迁移增益做**选择**（不是训练）；`transfer_regression` 从空桶变成真正干活的
   晋升门。
3. **测试时扩展（best-of-N 投票）。** 推理/部署时对同一案例做 N 次 provider 重采样，
   用确定性聚合（终态多数票 + 定位投票 + 一致性阈值弃权）。聚合逻辑可审计、不进学习
   归功。
4. **课程学习 / 主动学习（数据顺序）。** 按幻觉类型或难度切片排序；优先喂给检测器
   最不自信的案例（FP/FN 边界、高置信 unresolved）。
5. **负样本挖掘 / 红队循环。** 定期跑检测器，把"自信出错"的案例定向扩充进化池，并把
   有限 gold 标注预算投到这些边界案例。

> **试运行观测（2026-08-14）**：5 例并发 gold census（3 正例 + 2 负例）全部停在
> framework 层——4 例 `INFRA_FAILURE`（detector 热路径
> `TimeoutError The read operation timed out`）与 1 例正确的 `NON_EVOLVABLE`。根因是
> 检测侧 V4 推理模型在并发放大下、单个 120s 读窗口内产不出完整 JSON，叠加
> `finish_reason=length` 空 body。要拿到干净的 search→execution→fitness 漏斗，需**串行
> 或 2 路以内并发**重跑（或为推理模型配更长读窗口）；这是吞吐预算与模型的匹配问题，
> 不是代理、不是代码回归。见 TECHNICAL_DESIGN「检测热路径读超时」。

## 开发准则

1. **框架内不写实验协议。** 分区、清单、执行授权、数据集切分与评估门控绝不得加入
   `sehd_rise/`；它们属于实验层。
2. **实验放在 `experiments/`。** 协议记录、运行脚本与运行输出留在本地实验工作区
   （被 git 忽略），且只通过上述公共 API/CLI 消费框架。见 `experiments/README.md`。
3. **框架保持独立可调用。** 仅以一条原始轨迹为输入就必须能运行框架；任何实验层
   都是可选消费者，绝不是必需依赖。
4. **共性改进进框架，实验特有行为进实验。** 实验需要新的框架能力时，以通用 API
   的形式加进 `sehd_rise/`，实验特有的策略留在 `experiments/`。
5. **冻结基线只读。** `src/sehd_v6/` 绝不修改；在旧代码中发现的缺陷，通过在
   `sehd_rise/` 中实现正确的语义来修复。

## 许可证

私有研究仓库。
