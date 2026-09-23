# 开发循环接入 cycle（2026-09-23）

**结论：计划 §23.3 第 6 条要求的"cycle 内可选择启用开发路径"从来没有被实现，
`development.enabled` 是一个没有任何读取方的配置键。本次补上这条接线，并修掉接线过程中
被测试暴露出的两个真实缺陷。默认 `false`，未启用时行为逐字不变。**

## 为什么做这一条

不是从计划清单里挑的，是从第一次真实自进化实验的读数里读出来的。

真实运行（[reports/real_evolution_20260923](../real_evolution_20260923/README.md)）的账本事件序列是
`proposed → rejected`，中间**没有开发阶段**：LLM 一次性产出的候选**直接进入 62 分钟的双臂
晋升运行**，然后被 HARMLESSNESS 拒绝（保护集 4 例从 4 correct 打成 0）。而 L0 是零成本确定性
检查、L1 是廉价定向重放，本来就是为拦住这类候选而建的。

代码事实核对（逐文件 grep，不是读文档）：

| 事实 | 读数 |
|---|---|
| `_run_cycle`（686 行）引用 development/developer/develop_rows | **0 处** |
| `development.enabled` 被任何代码路径读取 | **0 处** |
| 把 `development.enabled` 设为 true 的测试 | **0 个** |
| `CandidateDeveloper` / `develop_rows` 的调用方 | 只有 `python -m evolution develop` 与测试/归档脚本 |
| 计划 §23.3 第 6 条要求的"本轮走哪条路径"报告字段 | 不存在 |

**这是一条"计划要求了、代码没做、且没有被登记为未做"的缺口**：§28.6 如实登记了存档与组件库
两处未接线，但没有登记这一处，而 §23.10 的进度表把 P0.3 记为已完成——它确实完成了组件，
只是没接进任何自动路径。

## 落地

| 文件 | 改动 |
|---|---|
| `evolution/engine.py` | 新增 `_development_targets`（可开发机制筛选，不可开发逐条报原因）、`_develop_candidates`（跑循环、冻结通过版本、按 `archive.on` 归档）、`_development_drafter`（把上一版本的 L0/L1 判决作为 gate feedback 交回 proposer）；`_run_cycle` 在起草处接入；报告新增 `round_path` 与 `development` |
| `evolution/config.py` | `development` 块新增 `max_mechanisms_per_cycle`（默认 1）；新增 `_copy_container`，修掉 `DEFAULTS` 块被跨 `load_config()` 共享的缺陷 |
| `scripts/verify.py` | 新增 `smoke_round_path_single_shot` 检查（12 项） |
| `evolution/tests/test_cycle_development.py` | 新增 14 项 |

设计要点（都是为了让读数可被判读）：

- **不开循环时不改变任何行为**：`round_path` 报 `single_shot`，不写开发生成记录，不建
  development 目录，不进 ledger。有专项测试与 verify 检查各一道。
- **开了循环时不偷偷回到单轮起草**：所有开发版本被否时，本轮候选数为 0，**不会**再把
  proposer 叫回来重新起草。否则这个阶段就是一个"多花几次调用、结果和以前一样"的摆设。
  有测试断言 `proposer 调用次数 == 记录的版本数`。
- **判决来自循环自己的口径**：`check_l1` 的四种拒绝理由（目标未改善／近邻退化／运行时健康
  退化／候选从未到达运行期）原样进入报告，不是"没测出收益"。
- **数据隔离由既有守卫执行**：L1 走 `develop_rows`，它对 sentinel/gain 有硬拒绝，所以开发
  阶段**不可能**在正式集上测量候选。有测试断言测过的案例集与 sentinel∪gain 不相交。
- **`cycle_end` 归档现在有运行路径了**：这是 §28.6 那两处"存储可用但未接线"之一能落地的
  前提——此前 cycle 侧根本不产生开发 run 目录可归档（真实运行里 `candidates/` 目录不存在的
  原因）。

## 过程中修掉的两个真实缺陷

**① `DEFAULTS` 的六个块被所有 `load_config()` 共享**（由本包测试暴露，不是计划里的条目）

`_deep_merge` 返回新的顶层 dict，但**配置文件中没有出现的块是按引用带过去的**，于是它们和
`DEFAULTS[...]` 是同一个对象。对随仓库发布的配置文件，处于这个位置的有六块：`archive`、
`development`、`coverage_response`、`patrol_registry`、`harness_dir`、`memory_root`。
后果：一处 `config["development"]["enabled"] = True` 会改变**本进程后续所有** `load_config()`
的默认值——`archive` 同理，而这两块正是运行路径会改的。

可复现证据（修复前）：

```python
from evolution.config import load_config, DEFAULTS
load_config()["development"]["enabled"] = True
assert DEFAULTS["development"]["enabled"] is True   # 泄漏
```

发现方式值得记录：本包测试**单独跑全绿、全量跑 12 项失败**。这正是"测试通过不等于接对"的
一个实例。修复：`_deep_merge` 对未覆盖的块做深拷贝。回归测试：
`test_loaded_config_does_not_share_mutable_blocks_with_defaults`。

**② 同一 cycle 内两个机制的候选会撞 id**

proposer 每次调用都从 1 开始编号，所以两个机制各自开发的候选都是 `IV-<cycle>-001`，
proposal 契约（批内 id 唯一）会以与候选质量无关的理由拒绝整轮。修复：由 drafter 在版本被
记录**之前**分配 id（`IV-<cycle>-D<序号>`），使冻结候选、其哈希与批次条目描述同一个对象。
有测试 `test_development_ids_are_unique_across_mechanisms` 覆盖。

## 读数

离线仪器实验：`python reports/cycle_development_wiring_20260923/run_experiment.py`
（约 2 秒，**零 provider 调用**，重复两次读数一致）。机器可读结果见
[experiment_report.json](experiment_report.json)。

同一套种植失败场景跑四种组合：

| 场景 | `round_path` | proposer 调用 | 到达门的候选 | 准入 | gain 集边际 |
|---|---|---|---|---|---|
| 循环关 + 候选不修复 | `single_shot` | 1 | 1 | 0 | 0 |
| **循环开 + 候选不修复** | `development` | 1 | **0** | 0 | — |
| 循环开 + 候选修复 | `development` | 1 | 1 | 1 | 2 |
| 循环关 + 候选修复 | `single_shot` | 1 | 1 | 1 | 2 |

七条主张全部成立（`experiment_report.json` 的 `claims`，全部为 `true`）：

1. 循环关闭时仍是单轮路径，proposer 只被调用一次；
2. 循环关闭时不写任何开发记录，ledger 里没有 `development` 事件；
3. **循环开启时，坏候选在门之前被否**：`proposals_reaching_the_gates == 0`、
   `gate_entries == 0`、最后一个版本的 `l1_passed is False`；
4. 循环开启时不会绕过循环重新起草（调用次数 == 版本数）；
5. 通过版本被带进门，且只带一个；
6. **通过版本的收益与旧路径逐项相同**（`marginal_cases=2`、`new_errors=0`），
   只有 id 按设计不同（`IV-1-D001` vs `IV-1-001`）；
7. L1 确实对着基线臂测量（`l1_cases` 消耗 > 0）。

第 6 条最初写作"id 也相同"，**跑出来是 false**——这是本实验修正了我自己的一个错误预期：
id 按设计必须不同，断言 id 相等等于用一个人为差异去否定收益一致性。已改为比较效果读数并在
脚本注释里写明理由。

**证据等级：仪器通过。** 全程 mock backend + 脚本 proposer，mock oracle 是目标可达的原因，
因此这些读数**不构成**检测质量、定位能力或自进化成功的结论。它说明的是：这个阶段真的会改变
哪些候选到达门，且除此之外什么也没改。

## 本包**没有**做什么

- **未改变任何门限**：新增的是路径与报告字段，`gates`/`mini_effect` 一律未动。
- **未创建 holdout**：`datasets/protocols/final_holdout_v1.json` 仍不存在（按红线该决定属于用户）。
- **未把 `development.enabled` 默认打开**：默认 `false`，仓库配置文件也未改动。
- **未改 `develop` 命令**：那条人工路径行为不变。
- **未接入真实 learned executor / 未生成组件源码**。
- **未验证 `route_failures.jsonl` / `gaps.jsonl` 的运行期写入**：仍只在测试与 mock 中出现过。
- **未在真实数据上验证本阶段的收益**：本包的离线读数不涉及真实分布，真实运行见
  [real_evolution_20260923](../real_evolution_20260923/README.md) 与后续轮次记录。

## 复现

```bash
python reports/cycle_development_wiring_20260923/run_experiment.py   # 离线，零 provider 调用
python -m pytest evolution/tests/test_cycle_development.py -q        # 14 项
python -m pytest -q                                                  # 841 项
python scripts/verify.py                                             # 12 项全 PASS
```
