# SEHD-RISE2 — 最小冻结检测器 + Phase 0 失败理解层

本目录是**从零重写**的检测器框架，只保留检测器的本质：

> **census（候选发现）→ verification（证据核验）→ defense（对抗复核）
> → 确定性法庭聚合 → fail-closed 权威信封**

`detector/` 是冻结基座（可独立运行）；`evolution/` 是 Phase 0 失败理解层
（已落地：Learning Case 装配 + 冻结能力缺口词表 + 诊断协议），详见根
README 的 Phase 0 一节。

## 目录结构

```text
sehd_rise2/
├── detector/                     # 最小冻结检测器（可独立运行）
│   ├── api.py                    # 唯一 Detector API：Detector.detect / detect()
│   ├── court.py                  # 最小法庭：三阶段 provider + 确定性裁决
│   ├── provider.py               # provider 传输：直连 HTTPS / 重试 / JSON 修复
│   ├── packet.py                 # 轨迹数据包 + gold 输入防火墙
│   ├── result.py                 # 冻结输出信封 + fail-closed 权威读取
│   ├── config.py                 # 配置解析（env 覆盖优先级）
│   ├── env.py                    # 仓库 .env 加载
│   ├── skills.py                 # 进化接口：load_skill_genome / load_policy_genome
│   ├── evolution_api.py          # 进化接口：Detection Truth 只读投影
│   ├── runs.py                   # 运行记录：每案例过程落盘 + 台账推导统计（runs/）
│   ├── cli.py / __main__.py      # python -m sehd_rise2.detector
│   └── configs/detector_v1.json  # 三路由最小配置
├── evolution/                    # Phase 0 失败理解层（已落地）
│   ├── gap_taxonomy.py           #   冻结能力缺口词表（单一真源）
│   ├── diagnosis.py              #   机械失败分类 + 诊断协议校验
│   ├── learning_case.py          #   Learning Case schema + 三层校验器
│   └── assemble.py               #   装配 / 校验 / 报告 CLI
├── runs/                         # 运行结果目录（本地产物；布局见 runs/README.md）
└── tools/check_layers.py         # 层级守卫（detector ← evolution 单向）
```

## 独立运行

```bash
python -m sehd_rise2.detector --record trajectory.json --output-dir out
```

- `--config` 缺省使用随包 `configs/detector_v1.json`；
- 凭据从进程环境或仓库根 `.env` 读取（env-var 名不落盘）；
- 输出 `out/prediction.json`（冻结信封 `sehd2.detection_result.v1`）。

## Detector API

```python
from sehd_rise2.detector.api import Detector, detect

detector = Detector()                      # 缺省加载随包配置
result = detector.detect(
    {"history": [{"role": "user", "content": "..."},
                 {"role": "assistant", "content": "..."}]},
    case_id="case_1",
    genome=...,        # 可选：进化出的 skill genome 载荷
    policy_genome=..., # 可选：进化出的策略 genome 载荷
)
print(result["state"], result["decision_authority"])
```

- 输入必须是 label-free（含 `is_hallucination` 等 gold 字段会被拒绝）；
- genome / policy_genome 是**不透明载荷**：加载校验 + provenance 记录，
  执行语义由未来的进化框架定义（见 `evolution/INTERFACES.md`）。

## 运行结果目录（runs/）

每次运行落一个 `runs/<run_id>/`（schema `sehd2.run_record.v1`）：`run.json`
运行元信息、`summary.json` 由案例台账**重新推导**的无标签统计（单一真源）、
`cases/<case_id>/{input,packet,result,audit}.json` 每案例全量过程记录。
目录内一切产物 label-free；含真值的评测报告由评测侧写
`runs/<run_id>/eval_gold.json`。详见 `runs/README.md`。

## 为自进化预留的接口

| 用途 | 接口 |
|---|---|
| 加载 skill | `skills.load_skill_genome` / `load_policy_genome` |
| 部署执行 | `api.detect(..., genome=..., policy_genome=...)` |
| 数据供给 | `evolution_api.project_for_evolution(result)` |
| 终态读取 | `result.resolve_evolution_state` 等 |
| gold 隔离 | `packet.EVALUATOR_ONLY_FIELDS` |
| 学习案例装配（离线复盘） | `python -m sehd_rise2.evolution.assemble` |

## 设计纪律（从重建中固化）

1. **检测器冻结**：一切改进必须来自未来的可进化面，经显式参数部署。
2. **证据驱动，不手调阈值**：裁决是封闭词表的确定性聚合，无 LLM 定终态。
3. **失败即关闭**：provider/契约失败 → `runtime_valid=False`、
   `state=unresolved`、`decision_authority=invalid`，绝不冒充裁决。
4. **Gold 隔离**：gold 只属于评测侧，永不进入检测器提示词或产物。
5. **单一真源**：每个判定只写一次；`tools/check_layers.py` 机械强制依赖边界。
