# runs/ — 运行结果目录（本地产物，不入 git；README 除外）

每次检测运行落一个 `run_id` 子目录，结构由 `sehd_rise2.detector.runs`
冻结（schema `sehd2.run_record.v1`）：

```text
runs/<run_id>/
├── run.json            # 运行元信息：schema、created_at、配置指纹、案例清单
├── summary.json        # 无标签聚合统计（由案例台账推导，单一真源）
└── cases/<case_id>/
    ├── input.json      # 送入检测器的 label-free 输入（原样）
    ├── packet.json     # 编译后的数据包（事件/工具交互视图）
    ├── result.json     # 冻结检测结果信封（state/authority/proof/
    │                   #   census/certificates/defense_decisions/
    │                   #   providers/evidence_health，全量）
    └── audit.json      # 阶段审计 + provider 遥测 + 原始输出预览 +
                        #   authority 失败原因 + 时间线
```

## 两个硬约束

1. **单一真源**：`summary.json` 永远由 `cases/*/result.json` 重新推导
   （`runs.summarize_run`），不存在第二份可变计数器。
2. **Gold 隔离**：目录内一切产物 label-free（检测器从未接收 gold，因此
   不可能落盘 gold）。含真值的评测报告（准确率 / 步定位）由评测侧写入
   独立文件（惯例：`runs/<run_id>/eval_gold.json`，见
   `experiments/run_10_gold.py`），进化框架消费时须区分两类文件。

## 自进化读取面

- 每案例完整证据：census 候选、证书、辩护决策、coverage、responsible
  定位、provider 用量——`result.json` 即 `evolution_api.project_for_evolution`
  的完整超集；
- 失败诊断：`audit.json` 的 `authority_failure_reasons` 与 provider 错误；
- 统计：`summary.json`（无标签口径）与评测侧 `eval_gold.json`（含真值口径）。
