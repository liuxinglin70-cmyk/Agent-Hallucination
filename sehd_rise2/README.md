# SEHD-RISE2 — 最小冻结检测器 + 自进化框架

本目录是**从零重写**的检测器框架，只保留检测器的本质：

> **census（候选发现）→ verification（证据核验）→ defense（对抗复核）
> → 确定性法庭聚合 → fail-closed 权威信封**

`detector/` 是冻结基座（可独立运行）；`evolution/` 是自进化框架
（失败理解 → gap 归因 → artifact 合成 → 门控循环）。检测器的可进化
面有且只有一个显式参数 `detect(artifacts=...)`（统一 Evolution
Artifact 契约，见 `detector/artifacts.py`）：`prompt_directive` 型
注入三阶段 prompt、`adjudication_policy` 型在确定性裁决内执行；
另配手工约定层（`conventions.py` 基座）与冻结 census 配对重放入口。
过程与迭代协议的真源是根目录 `FIX_VALIDATE_ITERATION_PLAN.md`；
架构决策的真源是 `SEHD_RISE2_V2_REFACTOR_PLAN.md`。

## 目录结构

```text
sehd_rise2/
├── detector/                     # 最小冻结检测器（可独立运行）
│   ├── api.py                    # 唯一 Detector API：Detector.detect / detect()
│   ├── court.py                  # 最小法庭：三阶段 provider + 确定性裁决
│   ├── conventions.py            # 判决约定层：闭卷词表 + 包特征路由 + 双向边界渲染
│   ├── provider.py               # provider 传输：直连 HTTPS / 重试 / JSON 修复
│   ├── packet.py                 # 轨迹数据包 + gold 输入防火墙 + 分阶段视图
│   ├── result.py                 # 冻结输出信封 + fail-closed 权威读取
│   ├── config.py                 # 配置解析（env 覆盖优先级）
│   ├── env.py                    # 仓库 .env 加载
│   ├── artifacts.py              # 进化执行面：artifact 契约 + 词表 + 运行时绑定
│   ├── evolution_api.py          # 进化接口：Detection Truth 只读投影
│   ├── runs.py                   # 运行记录：双指纹 + 每案例过程落盘 + 台账推导统计
│   ├── cli.py / __main__.py      # python -m sehd_rise2.detector
│   └── configs/detector_v1.json  # 三路由最小配置
├── evolution/                    # 自进化框架
│   ├── gap_taxonomy.py           #   冻结能力缺口词表（单一真源）
│   ├── diagnosis.py              #   机械失败分类 + 诊断协议校验
│   ├── learning_case.py          #   Learning Case schema + 三层校验器
│   ├── assemble.py               #   装配 / 校验 / 报告 CLI
│   ├── policy.py                 #   L2 策略层：词表 + 候选集 + genome 构建
│   ├── replay.py                 #   离线重放：绑定校验 + 裁决重放 + oracle/runtime 对比
│   ├── binder.py                 #   运行时证书类绑定器（label-free 感知阶段）
│   ├── proposal.py / cycle.py    #   L2 提案生成 + 门控进化循环（累积重放门 + 台账）
│   ├── gates.py                  #   门校准单一真源（datasets/calibration/，默认绝对零容忍）
│   └── convention_evolution.py   #   约定进化：复现过滤 → 提取 → G1 → G2a/G2b 配对门
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
    artifacts=...,           # 进化 artifact registry：唯一部署通道
    frozen_census=...,       # 冻结候选集：跳过 census 阶段的配对重放锚
)
print(result["state"], result["decision_authority"])
```

- 输入必须是 label-free（含 `is_hallucination` 等 gold 字段会被拒绝）；
- `artifacts` 是**唯一有执行面的**部署通道（`detector/artifacts.py`
  统一契约，加载 fail-closed）：`prompt_directive` 型 artifact 路由
  命中时 directive + NOT-WHEN 排除边界进入 census/verification/defense
  三个 system prompt；`adjudication_policy` 型 artifact 在确定性裁决内
  执行（运行时证书类绑定，unclassified 弃权 = 基线行为）。每个 artifact
  必须携带 gap 块与 capability_delta——只加规则的产物无法加载；
- `frozen_census` 复用一次先前滚动的候选集、只重跑裁决阶段——
  guided-vs-unguided 差异归因于约定而非 census 重采样噪声；
- 判决接合事实落盘进 Detection Truth：`proof.active_artifacts` 与
  `proof.policy_evaluation.fired/skipped`，部署后是否真正执行可被
  任意 run 复查。

## 运行结果目录（runs/）

每次运行落一个 `runs/<run_id>/`（schema `sehd2.run_record.v1`）：`run.json`
运行元信息（`config_fingerprint` 配置身份 + `detector_fingerprint`
配置与判决面源码联合身份——同指纹 ⇔ 同判决法律）、`summary.json`
由案例台账**重新推导**的无标签统计（单一真源）、
`cases/<case_id>/{input,packet,result,audit}.json` 每案例全量过程记录。
目录内一切产物 label-free；含真值的评测报告由评测侧写
`runs/<run_id>/eval_gold.json`。详见 `runs/README.md`。

## 为自进化预留/已开的接口

| 用途 | 接口 |
|---|---|
| 加载 artifact registry | `artifacts.load_artifact_registry`（fail-closed） |
| 部署执行面 | `api.detect(..., artifacts=...)`（三阶段注入 + 裁决内执行） |
| 配对重放锚 | `api.detect(..., frozen_census=...)`（裁决阶段专用） |
| 数据供给 | `evolution_api.project_for_evolution(result)` |
| 终态读取 | `result.resolve_evolution_state` 等 |
| gold 隔离 | `packet.EVALUATOR_ONLY_FIELDS` |
| 约定进化循环 | `python -m sehd_rise2.evolution.convention_evolution` |
| 策略进化循环 | `python -m sehd_rise2.evolution.cycle` |
| 学习案例装配（离线复盘） | `python -m sehd_rise2.evolution.assemble` |

## 设计纪律（从重建中固化）

1. **检测器冻结**：一切改进必须来自可进化面，经显式参数部署。
2. **证据驱动，不手调阈值**：裁决是封闭词表的确定性聚合，无 LLM 定终态。
3. **失败即关闭**：provider/契约失败 → `runtime_valid=False`、
   `state=unresolved`、`decision_authority=invalid`，绝不冒充裁决。
4. **Gold 隔离**：gold 只属于评测侧，永不进入检测器提示词或产物。
5. **单一真源**：每个判定只写一次；`tools/check_layers.py` 机械强制依赖边界。
6. **测量先于解释**：任何指标差异先对照噪声地板（`tools/run_stability.py`
   双跑一致率）再归因；进化验证走冻结前缀配对重放。
