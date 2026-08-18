# evolution_v3 实验说明

## 已交付的可复现实验

### E1：离线 gate 验证（已运行）

```bash
python -m evolution_v3 smoke
```

- 输入：`datasets/eval_splits/v3_evo50` 的前 32 例（仅构造 toy 失败，不使用 gold
  做候选文本）。
- 结果：`evolution_v3/work/smoke/experiments/smoke_cycle.json`。
- 预期：
  - patrol 形成 `FAM-fn-judge_false_no_recall_candidates` 稳定失败族；
  - 模板起草器生成 `IV-1-001`（scanner.system 文本边界）；
  - V1/V2/V3/M 全过，边际 +2 例，pre-commit 写入隔离 registry；
  - case-id 泄漏缺陷被 V1 拒，零边际噪声缺陷被 M 拒，audit passed。

### E3：100 例自进化 + 50 例验证（已运行，当前结论真源）

完整记录：**[tuning100_validation50_experiment_report.md](tuning100_validation50_experiment_report.md)**。

```bash
python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 1 --max-proposals 2 --m-min-marginal 1 --no-audit
python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 2 --max-proposals 3 --m-min-marginal 2 --no-audit
python evolution_v3/experiments/run_tuning100.py validate
```

摘要：100 例调优集基线 `runs_v2/v2_100_20w`；50 例验证基线
`evolution_v3/work/valruns/evo3_val_base`。两 cycle 准入 5 条干预；
100 例 G 22→25 correct；50 例 78%→84%（+6pp），FP 4→3，
`new_fn=0/new_fp=0`。调优与 active 验证臂均为缓存/frozen replay，
0 新增 LLM token。

### E2：正式 393/300 协议（需要 AUTODL_API_KEY 或 detector_v2 配置）

```bash
python -m evolution_v3 experiment --cycles 3 --backend detector_v2
python -m evolution_v3 final
python -m evolution_v3 meta
```

预算（每个 cycle 上限）以 `evolution_v3/configs/framework.json` 为准，与设计
文档 §7.2 对齐；`--backend detector_v2` 会在 `evolution_v3/work/cache/` 缓存
每个 registry-hash × case 的结果，重复跑不会重复调用 LLM。

配对执行控制：非 `judge.system` 候选会复用同案例空库的 frozen judge；在
`CachedRunBackend` 下进一步 frozen recall / upgrade / tracer / defense 基线
阶段，只有被干预 hook 覆盖的阶段重新执行。`judge.system` 候选自动走真实重跑
并在报告中标记 `frozen_judge_replay_compatible=false`。

空结果防护：初始巡逻无稳定失败族时，引擎会在 40 例预算内补采；失败族会用
5 个 deterministic predicates 做机械诊断，提案器按诊断信号生成最多 3 个不同
模板。先跑 `python -m evolution_v3 preview --cycle N --backend detector_v2`
可以在花 V2/M 预算前看到候选数量与 `empty_proposal_reason`。

## 输出物

```text
evolution_v3/work/
  cycles/cycleNNN.json       # cycle 完整报告（V1-V3-M、patrol、审计、退休/回滚）
  reports/meta_eval.json     # 进化机制自身指标
  final/final_report.json    # read-once 终审报告
datasets/evolution_v2/ledger.jsonl   # append-only hash 链
evolution_v3/registry/active.json    # 当前部署干预（pre-commit）
evolution_v3/registry/snapshots/     # 每次写入的不可变快照
```

## 终审判据

`final_report.json` 中的 `verdict.overall` 由以下规则机械生成：

1. active vs 空库的 `marginal_cases >= gates.m_min_marginal_cases`；
2. 单侧 McNemar p < 0.05；
3. `unresolved_added <= 0`；
4. step localization 不下降。

不通过时结论为“当前自进化框架无效”，回退检测器修复路线。
