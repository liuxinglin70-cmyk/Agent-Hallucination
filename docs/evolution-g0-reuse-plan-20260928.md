# 693 例自进化实验划分与执行计划（2026-09-28）

## 固定输入与划分

- 原始语料：`datasets/protocols/development_exposure.json` 列出的全部 693 例。按本次要求，不因以往使用而排除案例。
- 划分文件：[`evolution_g0_reuse_20260928.json`](../datasets/protocols/evolution_g0_reuse_20260928.json)。验证集 139 例（20.1%，金标幻觉 90、正常 49）；自进化池 554 例（79.9%）。按来源 × 金标标签分层，以固定 SHA-256 顺序抽取；不使用 G0 预测正确与否决定验证归属。两集合无交集，合起来恰为 693 例。
- G0 快照：[`g0_baseline_snapshot_20260927.json`](../datasets/protocols/g0_baseline_snapshot_20260927.json) 保存已有 653 例逐案判定、定位、权威性及耗时，源报告 SHA-256 为 `f90208a68bae2b90ee976c8d91e62bdfa8e7b28a5b3eab10c928e8555d4d75db`。剩余 40 例没有这次 G0 结果，其中 33 例落在自进化池、7 例落在验证集；运行时会测量它们。

## 自进化池的实际使用

配置为 [`full693_g0_evolution_20260928.json`](../evolution/configs/full693_g0_evolution_20260928.json)，使用现有 `--mini` 开发协议读取自定义规模的划分，不再使用原 30 例 mini manifest。`--mini` 在此是代码入口名称，不表示只运行几十例。

| 用途 | 数量 | 作用 |
|---|---:|---|
| S 保护集 | 24（12 金标幻觉、12 正常） | 每个冻结候选检查已有正确判定是否受损 |
| G 边际集 | 96 | 候选正式收益门；按金标极性取样，G0 已知其中至少 13 FN、6 FP，可测机会远多于上次各 1 例 |
| Patrol 池 | 434 | 每轮抽 150 例诊断失败；开发目标及正常邻居从中选，三轮可覆盖不同问题 |
| 本轮验证 | 139 | 与上述 554 例隔离，仅在周期结束比较 active 与 baseline |

S/G/patrol 的确定性抽样在 `evolution.splits.build_split` 中完成，种子为 `20260928`。S∪G 每候选 120 例，比上次的 12 例大十倍。开发上限为每轮 3 个机制、每机制 3 个版本、5 次实际 provider 请求和 80 次局部 case-evaluation；全局 proposer 预算每轮 15 次，避免上次 3 次全局上限先于修订额度耗尽。三轮上限各 36 小时，8 个 worker。详细实测成本以新运行的 usage/ledger 为准；旧成本模型曾明显低估 token。

## 判据与限制

继续使用 L0/L1、正式保护、行为和 M 门以及版本冻结；不会因扩大数据而放宽“新增错误 0”的准入条件。新增结果需分别报告检测判定和责任步定位，并说明可测率、候选触发、FN/FP 修复与正常邻居损伤。更大的集提高发现可复现机会的概率，**不保证**模型必然提出有效候选或成功准入。G0 的错误类型只用于核查划分中是否有可测机会；具体 G/验证案例不进入 proposer 提示。

验证集只与**本轮**开发池隔离。完整语料及 G0 历史上已被读取，本实验的验证数字应称为内部比较；未来需要新语料作独立泛化评估。

## 执行与恢复

划分生成：`python scripts/prepare_g0_evolution.py`。预检：`python scripts/preflight_real_run.py --config evolution/configs/full693_g0_evolution_20260928.json --phase evolution`。真实运行：`python -m evolution experiment --config evolution/configs/full693_g0_evolution_20260928.json --cycles 3 --mini`。中断后使用同一配置和 `--resume`，不得重生成划分或更换源码/模型配置继续同一运行。运行产物写入 `artifacts/evo693_20260928/`，完成后将退出码、三轮门控、active registry 和验证差分追加到 [实验日志](experiment-log.md)。
