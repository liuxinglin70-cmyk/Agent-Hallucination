# 检测器底座

`detector` 不依赖 `evolution`，公开入口为 `Detector.detect`、`detect` 和 `python -m detector`。

```python
from detector import Detector
from detector.dataset import project_record

# raw_record 由调用方加载；project_record 只保留 question/history。
result = Detector().detect(project_record(raw_record), case_id="example")
```

命令：`detect` 单例检测、`batch` 批量检测、`evaluate` 连接标签评估。`batch --cases-file` 接受案例 ID 的 JSON 数组；检测结果默认写入 `artifacts/detector/runs/`，单例写入 `artifacts/detector/single/`。

默认配置 `configs/autodl.json`；备选 `configs/deepseek.json`。当前路由模型为 `GLM-5.3-flash`（Judge/Defense/Recall 同一模型）。该模型始终思考，并且对**任何**显式 `thinking` 字段（含 `low`/`high`/`max`）都会在部分副本上以 400 间歇性拒绝，因此 GLM 路由不发送该字段。环境覆盖使用 `DETECTOR_ENDPOINT`、`DETECTOR_API_KEY_ENV`、`DETECTOR_MODEL`、`DETECTOR_JUDGE_MODEL`、`DETECTOR_DEFENSE_MODEL`、`DETECTOR_RECALL_MODEL` 等稳定前缀；模型、票数与思考档位只由配置文件决定。

输入契约严格限制为 `question` 和 `history`。原始数据中的标注由 `dataset.project_record` 隔离；结果含 `state`、`decision_authority`、`responsible_step`、`proof`、阶段状态与调用审计。

测试：`python -m pytest detector/tests -q`。

`tests/test_cli_integration.py` 在本地 HTTP 服务上实际启动 `detect`、`batch`、`evaluate` 子进程，验证多票、结果落盘、指标计算与原始标签隔离。该测试随全仓库验证运行，不访问外部模型服务。

真实服务联调可运行 `python scripts/live_smoke.py`。它使用两个独立生成的算术案例，保留配置中的模型和票数，将单请求超时限制为 45 秒、单案例 180 秒，关闭传输重试和补票；结果位于 `reports/live/`，详细运行产物位于 `artifacts/integration/`。执行会调用配置的模型服务。

真实数据集上的小规模路径与重复运行检查运行 `python scripts/real_path_check.py`。它按 `datasets/protocols/real_path_check.json` 固定案例清单、模型、票数和预算，冻结底座后多次运行 `batch → evaluate`，记录终态一致性、authority、unresolved、定位、耗时和 token；结果位于 `reports/real/`。案例已被开发使用，结论只覆盖路径连通与运行一致性，不是准确率。

完整逻辑见 [框架技术方案](../docs/technical-solution.md)；部署接口见其中的第 5 节。
