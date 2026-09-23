# 检测口径修复 + 开跑前排查（2026-09-23）

## 这一轮做了什么

用户在看完 §32 的底层审查后定了三件事：

1. **693 例本身就是验证数据集**，不必再要独立 holdout——暴露问题不重要，要先在它上面确定效果。
2. **解决两个遗留问题**：检测口径（§32.7 那三条）、预算与运维。
3. **做一轮开跑前的排查与问题检测**，确保能真跑。

本轮交付：检测口径三条问题的解决方案与落地、693 例全量测量能力与成本模型、一个可复用的开跑前
readiness 检查器。**未运行任何真实模型服务**，未调用任何 provider，未改任何门限数值。

**读数**：`1043 passed`（上轮 917），`scripts/verify.py` 15 项全 PASS。开跑前检查：baseline 与
evolution 两个阶段均 **READY（0 blockers）**。

## 1. 必须先说的一条：693 例上目前没有任何真实读数

排查时实测：**整个仓库的归档结果只覆盖 10 个唯一案例**（8 个判定幻觉 + 2 个干净）。
之前所有"142 行定罪""98/142 = 69% 定位准确率"都是同一批案例重复约 30 次的读数
（`reports/rsi_pilot_20260919/` 的 300 个文件里只有 10 个不同案例）。

这不是缺陷，是"至今没测过"。**它把预算问题从次要变成了决定性的**：693 例跑一次在 `workers=8`
下估计要 **21–28 小时**（见 §3），而自进化一轮要评估多次。所以本轮的结论顺序是：
先在 693 例上拿到基线读数，再谈自进化。

## 2. 检测口径三条问题的解决

### 2.1 机械定罪谓词的误报 → 校准护栏 + 抽取修复

**问题**：`required_action_present` 等谓词若给出 `violated`，会经 `convict_mechanical_violation`
**零 LLM、不过 defense、直接记 authoritative 定罪**。在 250 个 gold-clean 案例上实测误报
**7 例（2.8%）**，机制是文本框匹配（`_ACTION_MARKERS` 含 `"use the"`，从
"Use the complete name with article if any." 里抽出 `complete name`）。

**修法（两层，都必要）**：

1. **校准护栏（通用）**：一个谓词的 `violated` 要能授权定罪，必须**先通过干净集零误报校准**。
   校准记录落盘在 `detector/calibration/conviction_predicates.json`，含谓词名、clean 误报数、
   error 命中数、**语料指纹**与**谓词源码依赖指纹**。运行期比对指纹，不匹配即视为未校准。
   未通过校准的谓词**保留 evidence 产出但吊销定罪权**（guard 被抑制，案件回落 judge/defense 路径）。
   源码指纹是**导出的可达依赖指纹**而非整文件哈希：改谓词读的常量会移动它，改同文件另一个
   谓词的 pattern 不会。
2. **抽取修复（让它通过校准，而不是绕过）**：要求"动词 + 显式工具语义对象"双条件、
   排除疑问句与回答格式要求。

**实测结果**：

| 谓词 | clean 误报 | error 命中 |
|---|---|---|
| `required_action_present` | **7 → 0** | 32 → 3 |
| `final_delivery_present` | **3 → 0** | 24 → **0** |
| `verbatim_fidelity` | 0 → 0（未改） | 0 → 0 |

**必须如实说明的两点**（代理主动报出，我认可其诚实）：

- `required_action_present` 丢失的 29 例 error 命中，逐例审计后**没有一例同时满足"detail 事实
  成立"且"属于所声称的失效族"**——8 例的 detail 陈述与轨迹不符、13 例的 movement 目标根本不是
  地点（`close the ticket`、`and need`）。即那些**本来就不是真阳性**。修后 3 例全部属于
  `Missing Required Call` 族。
- `final_delivery_present` 在本语料上**已无任何 error 命中**，价值只剩"确认交付通道存在"。
  这是它在本语料上的真实能力上限。**是否保留它作为 convicting 谓词由用户决定**（当前保留，
  因为它不再产生误报，且它在别的框架形态上可能有价值）。

**覆盖面的诚实结论**：`required_action_present` 现在只覆盖 `Missing Required Call` 族 32 例中的
3 例。要覆盖整族需要**新谓词**（显式工具名抽取 + 调用缺失判定），属于口径扩张，本轮未做。

### 2.2 定位的责任步性质 → 分列，不再混读

**问题**：142 个定罪行的责任步中，**117 行指向自身散文为空、只有工具调用的"动作步"**，
25 行指向有散文的"内容步"，而指标不区分二者——72.6% 在两种口径间不可比。

**修法**：`detector/packet.py::step_kind()` 只读**事件结构**（`content` 非空 → 内容步；
空而有 `tool_interactions` → 动作步；都空 → 空步；步号越界 → `None` 不猜），
在生产定位读数的位置分列报告 `by_step_kind`。**既有分子分母与 `not_regressed` 判据逐字未变**。

**我独立核实了这一条**（重新实现对 142 行的重建，结果完全一致）：

| 量 | 读数 |
|---|---|
| 动作步 | **117 例，gold 命中 98 = 83.8%** |
| 内容步 | **25 例，gold 命中 0 = 0.0%** |

**这揭出了混读平均值掩盖的事实**：整体 69% 几乎全由动作步撑起，**内容步 0/25**。
用户要的"在 693 例上确定效果"必须按这两种步分开看。

### 2.3 tracer/defense 冲突与单元级不稳定

**冲突**：实测 3/142 的行**报告两个不同责任步却仍记 authoritative**（`BFCL/153` tracer 说 14、
final 说 10；`BFCL/008` tracer 说 3、final 说 1）。修法：**只对真正的矛盾降级**——
代理先量后定，发现"tracer 与 defense 的 `confirmed_step` 不一致"里 25 例其实是 tracer
正常履职（`LOCALIZATION_LAW` 要的就是"最早"，tracer 报更早的步 25/25、0 次更晚），
行内自洽；真正的矛盾只有 envelope 的 `proof.step_tracer` 与采纳步不符这一类，恰好就是那 3 例。
降级作用在**新增的 `localization_authority` 轴**，`decision_authority` 逐字不动
（动它会改变已成立的判定，且 `localization_behavior_gate` 实测完全不读该轴）。

**单元级不稳定**：`BFCL/008`/`024` 在同一配置下 8 次重复判定分裂为 6/2——**单例不能作为
"已修复"的证据**，而框架的四门有以单例计分的成分。而 `_run_cycle` **早就算了**
`reroll_noise`（同配置两次 roll 的差 = 噪声底），只是从未传给任何门（`reroll_control` 形参
在所有调用点都是默认 `None`）。修法：

- 噪声底接入报告（`instrument.noise_floor`），**三态可区分**：`measured`（真测了）/
  `deterministic_replay`（确定性 hook，已知为零）/ `not_measured`（只有一条 roll，未知）。
  把"没人测"报成 0 等于宣称"测过且仪器静止"。
- **只在不稳定案例上有增益的候选不放行**，且这种拒绝记为 `not_evaluated` 而非"已证伪"
  （不拉黑机制）。
- **伤害类判据仍按全量计数，绝不被噪声折抵**——否则候选可以把"自己弄坏的案例"洗成仪器抖动。

## 3. 693 例全量测量能力与成本模型

**测量入口**：`scripts/run_baseline_693.py`（复用 `detector/runner.py::run_batch`，支持断点续跑）。
离线 dry-run 实测：693 例全部执行、原样再跑 1.6 s 且 `cases re-run: 0`（真跳过）、
7 个来源分列齐全、**零 provider 调用**。

**顺带修掉一个会污染结果的缺陷**：`run_batch` 的跳过逻辑原只做"文件存在 + case_id 相符"。
若 `run.json` 被删而 `cases/*/result.json` 仍在，旧结果会被当作已完成跳过，**同时新写的 manifest
把当前配置指纹盖上那些它从未产生的行**——一整轮读数归属错误的运行。现已改为拒绝、并校验
信封 schema、并提供显式 `--retry-unresolved`。

**成本模型**（`scripts/cost_model_693.py`，标定自 38 条逐例实测、23 个唯一案例）：

| workers | work 下界 | 模拟 p50 | 效率修正 | 长尾下界 |
|---|---|---|---|---|
| 1 | 170.0 h | 170.5 h | **215.3 h** | 90.7 h |
| 4 | 42.7 h | 43.3 h | **54.6 h** | 23.4 h |
| 8 | 21.3 h | 22.2 h | **28.0 h** | 12.5 h |
| 16 | 10.8 h | 11.7 h | **14.8 h** | 6.3 h |

- **在 W≥4 时决定下界的是总工作量，不是那条 94 分钟的尾巴**（尾巴只在慢案例数 > workers 时咬人）。
- Token：34 例实测 mean 26.9k/例 → **693 例外推 ≈ 18.6M tokens**。
- **三个诚实的警告**：① 样本只有 23/693 个唯一案例，且全部来自框架按难度挑出的 patrol/audit 集，
  work 下界应读作"良顺语料的地板"；② 四条只存在于 README 散文里的读数占了样本总分钟的 61%，
  不信任它们时 W=8 的估计从 21 h 降到 **9.4 h**——这个摆动本身是结论的一部分；
  ③ **provider 限流只能真跑才知道**，若端点限流，所有估计都会偏低。

## 4. 开跑前排查

新增 `scripts/preflight_real_run.py`：回答一个问题——**"现在开跑，它的读数会不会等于它声称的东西"**。
每一检查都对应本仓库已经真实发生过的一次事故。不调用 provider、不写运行目录。

实测两个阶段（配置 `evolution/configs/selfevo_real_dev2_20260923.json`）：

| 检查 | baseline | evolution |
|---|---|---|
| corpus（693 例对账、路径折叠冲突） | OK | OK |
| labels（443 正 / 250 负 / 443 带 gold step） | OK | OK |
| split / gates_satisfiable | 不适用 | OK（sentinel 3+3 ≥ 地板 2/2） |
| timeouts | WARN ratio=0.75 | WARN ratio=0.75 |
| budget | 不适用 | OK（一轮约 92 次新鲜评估） |
| credentials | OK（`.env` 中有，**不读值**） | OK |
| work_root / path_length | OK | OK |
| calibration（3 谓词已校准） | OK | OK |
| runner_admission（4 runner 全准入） | OK | OK |
| cost | WARN 21–28 h | WARN |
| **结论** | **READY（0 blockers）** | **READY（0 blockers）** |

**顺带查到的一个真实隐患**：`selfevo_real_20260923.json`（§30 用的那份配置）里
**单例超时 3h > 整轮预算 2h**——一个病态案例就能吃光整轮预算，而这正是 §31.4 运行 1
"审计吃掉 89.4 分钟、巡逻 16 例里 10 例未决"的成因。检查器现在把它判为 **BLOCKER**。
dev2 配置（4h 预算）是 ratio=0.75 的 WARN。

**"693 例就是验证集"带来的口径变化**：不需要 holdout，但**每轮测量都会消耗案例**——
这张表上跑得越多，"从未被任何测量看过"的案例就越少。所以基线必须在自进化之前做，
否则自进化的收益无法与"案例被反复看过"区分。这一点写进了本报告的结论，不是代码约束。

## 5. 未做的事（汇总）

- **未运行真实服务**；未调用任何 provider。全部读数来自离线测试、mock 与归档重放。
- **未在 693 例上产生任何真实检测读数**——本轮交付的是"能测"，不是"测了"。
- **未改任何门限数值**；未改划分；未创建 holdout（用户已明确不需要）。
- **未扩充机械谓词的覆盖面**（`required_action_present` 仍只覆盖 `Missing Required Call`
  32 例中的 3 例）。整族覆盖需要新谓词，是口径扩张，需先决定。
- **未修晋升路径的噪声底**（`promotion.py` 未接 noise floor）。其控制臂来自缓存重放，
  是否适用需单独判定。
- **未实现 8 次重复的 dispersion 面板**。cycle 只取两条 roll，只能**发现**成对翻转的案例；
  "八次里只翻一次"的案例仍可能被当稳定案例计分。`repeated_roll_disagreement` 已实现但
  未接入运行路径，需要一次预注册的重复面板（round6 对 008/024 做过）。
- **未标定真实 provider 下的噪声底**——代码现在会正确测量并报告它，但那个数字只能由真实运行产生。
- **未给 `work_root` 加锁**（运维约束保留：同一 work_root 只允许一个 run）。

## 6. 建议的运行顺序

1. **先跑基线**：`python scripts/run_baseline_693.py --workers 8 --run-id baseline693 --runs-root artifacts/baseline/runs`
   （需凭据；21–28 h 量级；支持中断续跑）。产出逐例结果 + 按来源/按 gold/按步性质分列的指标 + 耗时分布。
2. **读基线**：确认 TP/FP/FN/TN、定位分列（动作步 vs 内容步）、`degraded` 与未决数、
   以及**实测噪声底**（首轮第 1 件事就是看这个数）。
3. **再跑自进化**：`python -m evolution full-self-evolution`（或按 §31 的 cycle 路径），
   开跑前用 `scripts/preflight_real_run.py --phase evolution` 确认 READY，并遵守"同一 work_root
   只允许一个 run"。

## 7. 证据等级

**本报告说明**：检测口径的三条问题都有了解法与实测数字；693 例的测量能力已建成并离线验证；
开跑前的 15 项检查在两个阶段都通过。

**本报告不说明**：检测器在 693 例上的准确率、定位准确率、自进化收益、或任何泛化结论。
本轮**零真实调用**，全部数字来自离线复现与归档重放。
