# 脚本入口

从仓库根目录运行脚本；输出统一放入 `artifacts/`。当前模块、预算和证据合同见[技术文档](../docs/technical-solution.md)，实验结果见[结果索引](../docs/results/README.md)。现有路径保留，历史脚本中的冻结标准不改写为新协议。

## 离线验证与读取

| 用途 | 脚本 | 运行边界 |
| --- | --- | --- |
| 离线验收 | [verify.py](verify.py)、[source_audit.py](source_audit.py)、[dryrun_fake_detector.py](dryrun_fake_detector.py) | verify 调用 Fake/Mock 或受控回环；报告明确检查范围 |
| 既有证据与成本 | [analyze_detector_calls.py](analyze_detector_calls.py)、[audit_calibration.py](audit_calibration.py)、[cost_model_693.py](cost_model_693.py)、[evidence_coverage.py](evidence_coverage.py)、[experiment_status.py](experiment_status.py)、[paired_endpoint.py](paired_endpoint.py)、[read_multigen.py](read_multigen.py) | 读取已保存记录，缺失身份或 usage 不补造 |
| 冻结与预检 | [freeze_measured_prefix.py](freeze_measured_prefix.py)、[freeze_measurement_manifest.py](freeze_measurement_manifest.py)、[prepare_g0_evolution.py](prepare_g0_evolution.py)、[prepare_next_action_ab100.py](prepare_next_action_ab100.py)、[preflight_real_run.py](preflight_real_run.py) | 冻结工具会写输出；preflight 只读检查，不调用模型 |
| 离线语料探针 | [predicate_corpus_probe.py](predicate_corpus_probe.py)、[probe_candidate_ordering.py](probe_candidate_ordering.py)、[calibrate_conviction_predicates.py](calibrate_conviction_predicates.py) | 金标仅用于离线评分；校准输出由参数控制 |

```bash
python scripts/verify.py --scope code_only
python scripts/verify.py --scope all --jobs 3
python scripts/cost_model_693.py --json artifacts/cost_model_693.json
```

`verification_plugin.py` 是 [pytest 执行完整性插件](verification_plugin.py)，由 verify 加载；[offline_guard/sitecustomize.py](offline_guard/sitecustomize.py) 约束测试及继承的 Python 子进程只能访问回环网络。这两个文件不是独立命令。

## 真实 API 与研究运行

| 用途 | 脚本 | 运行边界 |
| --- | --- | --- |
| 合成 API 容量 | [probe_api_capacity.py](probe_api_capacity.py) | 读取默认 Judge 路由；同步分档测试、无重试、出现失败停止升档；默认每档2轮 |
| 集成与重复性 | [live_smoke.py](live_smoke.py)、[real_path_check.py](real_path_check.py)、[measure_noise_floor.py](measure_noise_floor.py) | 调用真实模型；合成集成、已暴露样本重复性与效果评测分开解释 |
| 实验与研究驱动 | [run_baseline_693.py](run_baseline_693.py)、[run_deep_optimization.py](run_deep_optimization.py)、[run_next_action_experiment.py](run_next_action_experiment.py)、[run_validation100_20261017.py](run_validation100_20261017.py)、[run_research_round.py](run_research_round.py) | 调用真实服务，保留当时样本顺序、协议和验收标准 |

本节命令须有对应真实运行授权；整理仓库或运行离线验收不包含这类授权。凭据来自本地 `.env` 或进程环境，不入 Git。

容量测试的[已存结果](../docs/results/api-capacity-20261019/report.md)建议日常并发64，最高全通过合成档位128；未确认供应商硬限额。以下仅展示复现命令，本次整理不执行：

```bash
python scripts/probe_api_capacity.py --levels 16,32,64,128 --rounds 2 --max-tokens 1024 --output artifacts/api-capacity/new-run/short.json
```

参数细节以源码和支持的 `--help` 为准；不通过批量执行脚本来生成清单。
