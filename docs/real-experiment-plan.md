# 大规模真实实验方案：验证自进化的实际效果

状态：**方案（未执行）**。本文件在任何真实读数产生之前写成，用于冻结测量口径。
写入日期：2026-09-23。对应代码基线 HEAD `4a70491`，`pytest --collect-only` = **1043 项**。

执行完成后，本方案的记录按约定并入 `agent_hallucination_self_evolution_optimization_plan.md`
（新增一节 + §23.10 进度表一行），本文件保留为口径依据。

---

## 0. 结论先行

现在缺的不是实验次数，而是**三个从未被测量过的量**：

| 缺的量 | 事实 | 证据 |
|---|---|---|
| **G0 基线** | 693 例上**零真实读数**；全仓库归档只覆盖 **10 个唯一案例**，且全部来自按难度挑出的 patrol/audit 池 | §33.2；`reports/calibration_and_preflight_20260923/README.md` |
| **噪声底** | temperature 0 下仍有分裂；该数字从未实测 | §33.3.3、§33.6；`BFCL/008`/`024` 八次重复 6/2 |
| **部署后读数** | active registry 每一轮真实运行结束时都是**空的**，因此"已部署的基座"从未在语料上被测量过 | §30 `active_after_cycle=[]`；§31 run1/run2 均 0 准入；`artifacts/evolution/registry/active.json` 为空 |

由此推出本方案的排序：**先量噪声底 → 再量 G0 → 再跑多代（争取准入）→ 最后配对测效果**。
反过来做（先跑多代）会得到与 §30/§31 相同的读数：终态 `completed`、准入 0，且无法区分
"机制不可修"与"机时不够"。

**最重要的一条设计约束**：真实运行至今 **6 次，准入 0 次**（§30 一拒；§31 run1 无可用机制；
§31 run2 两个版本都被 L1 证伪；2026-09-16 两轮 3 候选全拒；2026-09-21 晋升 `promotion_unmeasurable`）。
所以本实验的**主终点必须建立在"准入确实发生"之上**；若准入仍为 0，实验的合法交付物是
§16 E2 的五问机器可读诊断，而不是"自进化有效"。方案据此把"能否区分机制不可修 vs 仪器测不到"
写成前置条件（见 §7）。

---

## 1. 当前事实（实测，非文档转述）

开跑前状态由本会话实际跑出：

```
python scripts/preflight_real_run.py --config evolution/configs/selfevo_real_dev2_20260923.json --phase baseline
  → phase=baseline  blockers=0  warnings=2  READY
python scripts/preflight_real_run.py --config evolution/configs/selfevo_real_dev2_20260923.json --phase evolution
  → phase=evolution blockers=0  warnings=3  READY
python -m pytest -q --collect-only → 1043 tests collected
```

警告项即本实验的三个主要风险，逐条保留：

1. `timeouts`：`case_timeout=10800s`(3.0h) / `cycle_timeout=14400s`(4.0h)，ratio=0.75 ——
   一个病态案例可吃掉整轮。
2. `long_tail`：实测最慢案例 **108.0 分钟**（`Magentic_One/036` 命中 900s provider 超时），
   超过整轮预算的 40%，**批级 fail-closed 仍可能发生**。
3. `cost`：全语料一遍 W=8 工作下界 **21.3h** / 模拟 p50 **22.2h** / 效率修正 **28.0h** /
   长尾下界 12.5h，≈ **18,628,941 tokens**。

成本的诚实边界（`reports/cost_model_693.json` 自带的三条警告，必须在预算决策时一起读）：

- 样本仅 **23 个唯一案例**，且**全部来自按难度挑出的 patrol/audit 集** —— 对全语料的外推方向未知。
- 四条散文读数占样本总分钟的 **61%**；剔除后 W=8 工作下界从 21.3h 降到 **9.4h**。
- **限流只能真跑才知道**（§32.7 登记为未测）；`workers` 与限流的耦合从未标定。

即：**单遍语料的真实成本在 9.4h 与 28h 之间，本方案用"可截断的分层顺序"（§3.2）吸收这个不确定性。**

---

## 2. 预注册：本实验要回答的问题

**P1（主终点）** 在预注册测量集的前缀 P 上，部署了自进化产出 registry 的基座，是否比空 registry
的基座在同一批案例上产生更多**决定性修复**，即 `decisive_fixed > decisive_broken`？

- 用 `decisive_fixed`/`decisive_broken` 而不是正确率差，理由：§32.5 已证明
  `correctness_improved` 把 `unresolved → correct` 的**校准**算作改善，能让一个"只把空臂弃权的 5 例判出来"
  的 registry 得到 `overall = pass`（`p=0.03125`）。同一份修复把终审改为只数决定性翻转并要求至少一例。
- **P1 不成立即"自进化效果未成立"**，与"流程跑了 N 轮"无关（§17 明令）。

**P2（主终点，与 P1 并列）** 若准入仍为 0，机器可读的五问诊断给出的原因是哪一条：
机制不可修 / action class 能力不足 / candidate 设计失败 / formal gain 无机会 / harmlessness 失败（§16 E2、§23.3 P0.4）？
**要求：五问的区分必须是"测出来的"，不能是"没跑到"的伪装** —— 这正是 §32.3 修掉的三处
"未测量被读成已证伪"，其修复判据（`gates.arm_has_infrastructure_failure`、`gain_measured` 按测量结果计算、
审计 gain 侧非空检查）必须在本次运行的报告里被逐条引用为证据。

**P3（诊断，前置）** 真实 provider 下的噪声底：哪些案例在两次独立 roll 之间翻转？
**翻转的案例在 P1 中一律不计分**，单列报告。

**P4（诊断）** 定位改善是否只出现在动作步上。§33.4 在归档定罪上实测
**动作步 98/117 = 83.8%、内容步 0/25 = 0.0%**，混读的 69% 几乎全由动作步撑起。
本次要求在**新运行**上重测这个分列，而不是引用旧读数（§33.6 已声明旧读数是描述而非估计）。

---

## 3. 设计

### 3.1 四个读数、三个对照

| 读数 | 臂 | 案例集 | 用途 |
|---|---|---|---|
| **NF** 噪声底 | 空 registry，**独立 roll ×3** | 40 例面板（§3.4） | 判定稳定案例集；给出真实翻转率 |
| **A** G0 基线 | 空 registry | 前缀 P | 自进化的比较基准 |
| **B** 部署后 | 已部署 active registry | 同一前缀 P | P1 的处理臂 |
| **C** 逐轮 cycle | 框架自身 | mini pool（S+G+patrol） | 准入是否发生（P2） |

A 与 B 必须是**同一份案例清单、同一 `workers`、同一 `detector_config`**，唯一变量是
`interventions`。`run_batch` 的 `run.json` 会写入 `interventions_hash`，所以两遍不能共用 `run_id`
（清单不同会被拒绝，这是 §33.4 修掉的"旧结果被当作已完成跳过、新 manifest 却把当前配置指纹
盖上它从未产生的行"那条缺陷的正面用法）。

### 3.2 预注册测量集与顺序（本方案的核心设计）

**排除面**：mini split 的 40 例（S 6 + G 6 + patrol_pool 18 + validation 10）**不进测量集** ——
它们是自进化循环自身的暴露面，落进测量集会让 P1 无法与"这些案例被循环看过"区分（§33.2 的口径约束）。

**测量集**：693 − 40 = **653 例**。

**顺序**：按来源**轮转**（round-robin），来源内用固定种子乱序，写成
`datasets/protocols/measurement_v1.json` 的 `cases` 键。

为什么必须轮转：`detector/runner.py:247` 用 `executor.submit(run, case_id) for case_id in cases`
**按清单顺序提交**。若按来源聚簇（默认清单就是 BFCL → Camel → Magentic_One → … → SmolAgents），
中途停下就等于"只测了前几个来源 + 快的案例"（慢例完成晚），是一个**由时间截断引入的选择偏置**。
轮转顺序使**任意前缀都是近似分层的样本**，于是"跑多久"变成运行期预算决定，而不是设计期必须猜准的量。

**冻结时点**：`measurement_v1.json` 必须在任何真实读数产生之前写完并提交。
此后不得增删案例；规模由"跑到哪个前缀"决定，不由"挑哪些案例"决定。

### 3.3 首终点与分析规则（预注册，先写后跑）

1. **首终点**：前缀 P 上的 `decisive_fixed` 与 `decisive_broken` 计数（P1）。
2. **只在稳定案例上计分**：由 NF 面板判定的不稳定案例**单列，不计入 P1 分子分母**。
3. **定位分列**：`step_localization.by_step_kind` 的动作步/内容步分开读，**禁止取平均**
   （既有分子分母与 `not_regressed` 判据逐字不变，§33.3）。
4. **未决与降级单列**：`unresolved`、`degraded`、`degraded_disagreeing`、`no_terminal_decision`
   各自成列。**"没跑到"不得被读成"结果是否定的"**（§32.3）。
5. **不设人为的"必须通过"阈值**。本实验交付的是读数与判定，不是"跑通了"。NF 是读数不是门 ——
   没有"翻转率必须低于 X%"这种阈值；它只决定哪些案例有资格进入 P1。
6. **配对统计只做描述 + 现成检验**。仓库全文（§13）没有 p 值/功效设计，概率只作测量工具
   （§22 原则四）。本次不引入新的显著性门槛；`paired_delta` 的既有字段（§21 的 `mcnemar_p`）
   只作附录读数，**不承担"通过/不通过"的裁决**，裁决用 `decisive_fixed > decisive_broken` 这个
   计数事实（§32.5 的修复口径）。

### 3.4 NF 面板的构成

**40 例 = S(6) + G(6) + patrol_pool(18) + 测量顺序前 10 例**，同一空 registry，**3 次独立 roll**。

- 选 S+G 的理由：门槛判据（HARMLESSNESS 的 `protected_tp≥2 / protected_tn≥2`、
  `new_fn/new_fp/new_unresolved`）**只在这 12 例上读**。若这 12 例里有不稳定的，
  每一次候选判定都在噪声上做决定，这与"测出翻转率"的目的直接对齐。
- 选前 10 例的理由：让 NF 面板同时覆盖"真实语料形状"，而不只是难度偏置池。
- 3 次 roll 而不是 2 次：§33.6 明确登记了"cycle 只取两条 roll，只能发现成对翻转的案例，
  '八次里只翻一次'的仍可能被当稳定案例计分"。2 次 roll 无法区分"稳定"与"低概率翻转"。
  3 次是最小可用面板；若预算允许，先把 S+G 这 12 例加到 8 次（§33 的对照口径），成本很小。
- 工具已有：`scripts/measure_noise_floor.py rolls <run tree...>` 提供
  `reading_stable_case_set(rolls)` 与转移计数（`_transitions`），无需新代码。

---

## 4. 阶段、命令与门

### Stage 0 — 开跑前（离线 + 小规模真实，约 4–6h）

| 步 | 动作 | 命令 |
|---|---|---|
| S0.1 | 离线复核（本会话已跑，重跑确认代码未漂移） | `python -m pytest -q` ； `python scripts/verify.py` |
| S0.2 | 两阶段 preflight 出 READY 与 warnings | `python scripts/preflight_real_run.py --config <cfg> --phase baseline` / `--phase evolution` |
| S0.3 | 生成并冻结测量集与顺序 | 新脚本 `scripts/freeze_measurement_manifest.py`（见 §8） → `datasets/protocols/measurement_v1.json` |
| S0.4 | 跑 NF 面板（3 次 roll × 40 例） | 三次 `run_baseline_693.py --manifest <40例清单> --run-id nf_roll1/2/3 --runs-root artifacts/noise_floor/runs --workers 8`，然后 `python scripts/measure_noise_floor.py rolls artifacts/noise_floor/runs/nf_roll1 …` |
| S0.5 | **workers 标定**（§32.7 登记为未测） | 同一小批在 W=8 与 W=16 各跑一次，比较每例中位耗时与 `provider_error`/`unresolved` 率；据此定 Stage 1 的 `--workers` |

**门 G0**（进入 Stage 1 的条件）：

- `verify.py` 全 PASS，preflight 两阶段 0 blockers；
- NF 面板产出**稳定案例清单 + 翻转率**（数字本身不设阈值，但必须存在）；
- W 由 S0.5 的实测决定，并写明理由（不要照抄配置里的 8）。

### Stage 1 — G0 基线与实测成本（真实，长任务；规模由 §4 门 G1 决定）

```
python scripts/run_baseline_693.py \
  --manifest datasets/protocols/measurement_v1.json \
  --run-id g0 --runs-root artifacts/baseline/runs \
  --workers <S0.5 实测值>
```

- **断点续跑**：同 `--run-id` 重入即跳过已有 `result.json`（前提是 `run.json` 指纹匹配）。
- 报告落 `reports/baseline_693_g0.json`；逐例耗时与最慢若干例在报告里。
- **本轮同时产出设定 Stage 2 超时所需要的那张分布**：`cycle_timeout` 不再靠猜，
  按实测 `max` 与 p99 定（规则见 Stage 2）。

**门 G1**（进入 Stage 2 的条件）：

- 完成案例数 N ≥ **240**（约 653 的 37%，按来源近似分层）。这是**建议下限**，理由：
  §33.4 的成本模型在 W=8 下 240 例 ≈ 7.4h 工作下界；这个规模能在一天内跑完并且仍能
  把"检测精度 + 定位分列 + 未决率"读到有意义的量级。若实测吞吐显著好于模型，**继续往上加前缀**
  （加，不是截断 —— 顺序已预注册，加不会引入偏置）。
- 报告给出：TP/FP/FN/TN、`decisive_accuracy`/`all_case_accuracy`、
  `step_localization` 的**动作步/内容步分列**、`unresolved`/`degraded`/`no_terminal_decision` 数、
  逐例耗时分布、**token 汇总**（见 §8 前置小修）。

### Stage 2 — 前代冻结与多代自进化（真实，约 9–18h）

**S2.1 先在候选出现之前冻结晋升批次**（§32.4 的纪律）：

```
python -m evolution freeze-protocol --batch <批次文件> --protocol-out artifacts/<work_root>/protocol_v1.json --config <cfg> --mechanism <M-…> --workers <W>
```

- 冻结时点必须在**任何候选输出之前**，否则"冻结"仍不成立。v1 现在已有 `protocol_hash`
  （§32.4 已把 v2 的纪律补到 v1），v2 另钉候选内容 hash 与 baseline registry hash。
- 批次文件按 §8.2 三类集合填：A 机制增益集（baseline 错误 ∧ 已确认属于该 mechanism ∧ 未被开发用过）、
  B 保护集（baseline 正确 TP + 正确 TN，localization 另含正确定位案例）、C 机制外挑战集。
- 保护集的极性可满足性由 `freeze_protocol` 在冻结时校验（2026-09-21 那轮的
  `protected_tp=0 < 2` 由构造即不可评估，已修）。

**S2.2 多代运行**：

```
python -m evolution experiment --cycles 3 --config evolution/configs/selfevo_real_multigen_20260923.json
```

新配置基于 `selfevo_real_dev2_20260923.json`（唯一一个在真实数据上跑通了开发阶段的配置），改动三项，
每项都有仓库事实支撑：

| 键 | 现值 | 建议值 | 依据 |
|---|---|---|---|
| `work_root` 及其派生的 registry/ledger/memory 路径 | `artifacts/selfevo-real-dev2-20260923` | **全新目录** | 同一 `work_root` 只允许一个 run（§32.7 修复边界：账本有跨进程锁，但 registry/检查点/缓存仍会互相覆盖） |
| `development.max_versions_per_mechanism` | 2 | **4** | §6.4 默认值就是 4；§31 run2 只拿到 2 个版本、两个都被 L1 证伪，等于"打完子弹就停" |
| `development.max_provider_calls` | 4 | **6** | 同上，§6.4 默认值 |
| `development.max_mechanisms_per_cycle` | 1 | **2**（可选） | 单机制单轮意味着准入概率 = 单次命中率；多给一个机制不放松任何门 |
| `budget.patrol_cases` | 8 | **18**（可选） | patrol_pool 就是 18，现配置只巡逻其中 8 例，丢掉一半的家族发现机会；代价约 +53 分钟/轮（§31 run2 实测 8 例 52.86 分钟） |
| `budget.cycle_timeout_seconds` | 14400 | **由 Stage 1 的实测分布决定** | 见下 |

`cycle_timeout` 的定法（先写规则，后填数字）：
`cycle_timeout ≥ max(2 × 实测单例最大耗时, §33.4 的每轮 fresh 上界 × 实测中位耗时 / workers × 3)`。
同时必须满足 preflight 的 `timeouts` 检查（`case_timeout / cycle_timeout` 不得接近 1），
否则 §33.5 那个"单例超时 3h > 整轮预算 2h"的 BLOCKER 会重演。

**每轮之间**：`python -m evolution verify-ledger --config <cfg>` 独立复核账本哈希链。

**同时是观察目标（此前两次真实运行都没写出来，§32.6 结论是"该写时不会漏写，能否触发取决于数据形状"）**：

- `route_failures.jsonl` —— localization 声明此前**条件不可满足**（两个独立硬阻断，§32 已修，
  离线复跑 `observed=1`）。本轮首次有机会看它是否被真实数据形状触发。
- `gaps.jsonl` —— 离线已能用真实 cycle 写出两种形状；看真实数据是否产出
  "机制超出动作词汇"的族。
- `memory/episodic.jsonl` —— 两次 §31 运行都没有这个文件。

**门 G2**：账本链 `valid=true`；每轮终态机器可读且五问可区分（P2）；准入数被如实记录。
**若某候选到达 L2**：额外走 S2.1 的冻结协议 `python -m evolution promote --protocol <…> --from-development … --version <v> --output-dir <…> --config <cfg>`，
使准入带走一份可独立复核的晋升记录（v1 有 `protocol_hash`）。**注意**：单轮墙钟必须为审计预留 ——
`engine._audit` 的指纹包含 `active_registry_hash`（`evolution/engine.py:2307`），
**一旦准入，下一轮审计缓存失效、重算**，§31 run2 实测审计 **118.37 分钟**。
所以：**每一次准入按 +118 分钟计**；0 准入的多轮则只付一次审计。

### Stage 3 — 效果测量（真实，配对；规模 = Stage 1 的前缀 P）

1. 由 Stage 1 的完成案例集生成**前缀清单**（纯列表，不看结果），写成
   `artifacts/baseline/runs/g0_prefix.json`。**必须逐例一致**，否则 `run.json` 指纹不匹配会被拒。
2. 部署臂：

```
python scripts/run_baseline_693.py \
  --manifest artifacts/baseline/runs/g0_prefix.json \
  --run-id g1 --runs-root artifacts/baseline/runs \
  --workers <W> \
  --interventions artifacts/selfevo-real-multigen-20260923/registry/active.json
```

- `--interventions` 走 `load_intervention_registry(..., require_executable_bounds=True)`
  （`scripts/run_baseline_693.py:536-539`），`active.json` 是
  `detector.intervention_registry.v1` 格式且带 `content_hash`，与基线加载器同源 —— 这条通路成立。
- **若 Stage 2 的 active registry 仍为空，Stage 3 必须记为 `not_measured` 而不是"无效果"。**
  这是本方案最容易被误读的一步：空 registry 的 B 臂 = A 臂，配对差恒等于噪声。

3. 配对分析：`decisive_fixed` / `decisive_broken`、定位分列、未决与降级；
   不稳定案例（NF 面板判定）单列；`paired_delta` 的既有字段作附录。

### Stage 4 — 归档与判定

归档到 `reports/<date>_real_experiment/`，含：只读复核脚本、逐例 `elapsed_seconds`、
token 汇总、`verify-ledger` 输出、NF 面板读数、A/B 配对表。
**README 必须写清"未做什么"，不只写做了什么**（仓库既有约定）。

判定按 §17 的五条逐条回答"成立 / 不成立 / 未测"：

1. 存在新机制的真实修复；
2. 无超限保护退化；
3. 已晋升能力继续存在；
4. 下一代确实基于上一代版本运行；
5. （多代）`G0 < G1 < G2`。

**另需独立回答 §21 的最后一条**：是否存在独立 holdout 用于最终泛化结论 ——
用户已决定不需要（§33.1：693 例本身就是验证数据集），所以本次实验**不产生任何泛化结论**，
只产生"在这 653 例上的读数"。这句话必须写进报告，不能被下游误引为泛化证明。

---

## 5. 预算

按 §33.4 的成本模型（W=8：工作下界 21.3h、效率修正 28.0h，样本 23 个唯一案例，
剔除 4 条散文读数后下界 9.4h）：

| 阶段 | 工作量 | W=8 估计 | 备注 |
|---|---|---|---|
| S0.4 NF 面板 | 40 例 × 3 roll = 120 次案例评估 | **3–6h** | 含 S+G+patrol，长尾概率高 |
| Stage 1 pass A（240 例） | 240 次 | **7.4h**（下界） | 653 例则为 21.3h（下界）/ 28h（效率修正） |
| Stage 2 三轮 cycle | 审计 1 次 + 3 轮巡逻 + 各门臂 | **4–8h** | 审计缓存命中；**每次准入 +118 分钟** |
| Stage 3 pass B（240 例） | 240 次 | **7.4h**（下界） | 与 pass A 必须同清单同 workers |
| **合计（240 例规模）** | | **约 22–30h 墙钟** | 约 1.5 个连续日 |
| **合计（653 例规模）** | | **约 55–80h 墙钟** | 约 2.5–3.5 个连续日 |

Token：每个语料 pass ≈ **18.6M**（§33.4 外推），两遍 ≈ 37M；NF 面板与 cycle 另计小量
（§30 proposer 实测 27,151 tokens/次；`reports/real` 实测 16,868.8 tokens/例）。
**限流与 W 的耦合未标定**（§32.7），所以上表的 W=8 是保守选择；
若 S0.5 的标定显示 W=16 可行，墙钟约减半，但**必须先测再上**。

---

## 6. 需要用户决定的事（不自行假设）

1. **规模**：240 例（约 1.5 天）还是 653 例（约 2.5–3.5 天）。
   —— 本方案的设计使这个决定**可以在 Stage 1 跑起来之后再定**，且怎么定都不产生偏置（§3.2）。
2. **是否允许 W≥16**：限流未测，需要用户明确是否接受"先小批标定、再上高并发"的风险。
3. **是否接受"准入 0 也是合法结论"**：P2 是本实验的主终点之一。若用户只接受 P1，
   则应先单独投入一轮把准入概率打上去（那是另一个课题，不是本方案）。
4. **门限一律不动**（建议）：动门限会让读数与 §30/§31 不可比，而 §33.3.3 已用
   三态噪声区分处理了噪声问题。本方案不主张放宽 `harmlessness_*`、`m_min_marginal_cases`
   或任何 `gates.*` 数值。

---

## 7. 预注册的失败与证伪条件

| 情形 | 判定 | 必须写进报告的话 |
|---|---|---|
| B 臂的 `decisive_fixed ≤ decisive_broken` | **自进化效果未成立** | 不是"跑通了"，是效果未成立 |
| 准入 0，且五问可区分出具体一条 | **P2 成立**：得到"为什么 0 准入"的机器可读归因 | 必须引用 §32.3 的修复作为"这是测量而非未跑到"的依据 |
| 准入 0，且五问**无法**区分（臂整批 fail-closed） | **实验未完成，不是否定结论** | 按 §32.3 记为未测量；先修机时/超时，不得写成"机制不成立" |
| NF 面板翻转率很高（例如 S+G 里 ≥2 例不稳定） | **门槛判定不可信** | 先解决测量稳定性（加大 S+G 或加重复），再谈效果 |
| B 臂为空 registry | **`not_measured`** | 空 registry 的 B 臂 = A 臂，配对差恒等于噪声 |
| 单例吃掉整轮 | 批级 fail-closed | 按 Stage 1 实测分布重设 `cycle_timeout` 后重跑该轮 |

---

## 8. 动手前的四项小修（低成本、高回报，都是离线可验）

1. **基线报告补 token 汇总**。`scripts/run_baseline_693.py` 的 `build_report` 目前不含 token；
   而逐例 `result.json` 的 `providers.*.usage.total_tokens` **已在盘上**，
   聚合逻辑在 `scripts/real_path_check.py:74-89` 已经存在，可直接复用。
   为什么必须做：`reports/evolution_real`、`reports/verification_20260917`、
   `reports/real_evolution_20260923`、`reports/real_dev_20260923` 的 `provider_usage` 实测都是 `{}`，
   成本模型因此**不得不引用 4 条散文读数**（占样本分钟 61%，直接导致 W=8 下界在 9.4h 与 21.3h 之间摆）。
   本次两遍语料正是补上这个汇总的最好机会。
2. **`measurement_v1.json` 生成器**（`scripts/freeze_measurement_manifest.py`）：
   排除 40 例 mini split + 来源轮转 + 固定种子，输出 `cases` 键。需在**任何真实读数之前**提交。
3. **前缀清单生成器**：Stage 3 的 `g0_prefix.json` 必须由 Stage 1 的完成集导出（只看清单不看结果），
   否则 `run_batch` 的 `run.json` 指纹不匹配会直接拒绝。
4. **`route_failures.jsonl` / `gaps.jsonl` 的触发观察**：不是代码改动，是 Stage 2 的显式观察项
   （§32.6 已判"该写时不会漏写"，剩余问题是数据形状）。

此外，以下两项**已具备**，不要重复实现：preflight（15 项，两阶段 READY）、
`measure_noise_floor.py`（`rolls` 与 `cycle` 两个子命令）、`cost_model_693.py`、
`run_baseline_693.py` 的断点续跑与 manifest 指纹。

---

## 9. 运维纪律（来自真实事故，§30.4 / §32.7 / NEXT_TASK）

- **同一 `work_root` 只允许一个 run**。启动后确认"只有**一个** cycle 进程"，
  不只是"启动了"。
- 判断进程存活**不要**用 `tasklist | grep -c python.exe`（本机输出中文，grep 静默返回空）。
  用 `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 并按命令行过滤。
- `python.exe` 里同时会有一批 `detector.provider_worker` 子进程，**那是正常的**，
  不要当成第二个 run。
- **测试必须离线**：`evolution/tests/conftest.py` 的 HTTP 守卫只保护 pytest，
  **不保护 `python -c` / 脚本式探测**；仓库历史上已两次在探测脚本里误发真实调用。
  写探测脚本时显式注入 fake seam（`--detector-factory scripts/dryrun_fake_detector.py`）。
- 长任务前确认 `AUTODL_API_KEY` 可用（preflight 的 `credentials` 项已覆盖，只查存在性）。
- 中途停止仍然安全：Stage 1/3 都可续跑；但**续跑必须用同一份清单**。

---

## 10. 明确不做

- **不创建 holdout**（用户已决定，§33.1）；因此本次不宣称任何泛化结论。
- **不改门限、不改划分、不改 gold、不重抽种子**（§23.2 红线）。
- **不与另一个 run 并发**（同一 `work_root`）。
- **不把 6/2 分裂的单元级案例（`BFCL/008`、`024`）当作"已修复"或"已回归"的证据**
  （§32.7、§33.6）。
- **不把 cycle 的内部读数（patrol/gain，12–30 例）当作效果证据** ——
  效果证据只能来自 Stage 3 的配对读数。
- **不在本方案里实现 8 次重复的 dispersion 面板**（§33.6 登记为未做）；
  NF 面板用 3 次，`S+G` 那 12 例若预算允许加到 8 次。
- **不动 `typed_semantic` 死通道**（§32.7 登记为未做：运行期从不注入 executor，
  必然 fail-closed 且被记为"候选有害"）。

---

## 附：与既有记录的关系

- 本方案取代 §23.4「检查点 A」的 7 条命令序列（那是 2026-09-22 版本；此后 §30/§31 已真实跑过
  cycle 与 develop，实际入口以 §33.7 与本文件为准）。
- 本方案不覆盖 §33.7 的三步建议，而是把它展开：§33.7 第 1 步（先跑基线）落在 Stage 1，
  第 2 步（读基线 + 噪声底）落在门 G1，第 3 步（再跑自进化）落在 Stage 2，
  并补上 §33.7 没有的 Stage 3（部署后配对测量）——因为**没有 Stage 3，
  "自进化提高基座能力"这句话在本仓库至今没有任何测量支撑**。
