# SEHD-RISE2 — 最小冻结检测器 + 自进化框架

本目录是**从零重写**的检测器框架，只保留检测器的本质：

> **census（候选发现）→ verification（证据核验）→ defense（对抗复核）
> → 确定性法庭聚合 → fail-closed 权威信封**

`detector/` 是冻结基座（可独立运行）；`evolution/` 是自进化框架
（稳定性标记 → 失败路由 → 提案修订 → 翻转门 → 能力库）。检测器的可
进化面有且只有一个显式参数 `detect(artifacts=...)`（统一 Evolution
Artifact 契约，见 `detector/artifacts.py`，经 `detector/deploy.py`
一次编译进 DeployPlan）。四种 artifact 各有执行面：`prompt_directive`
注入三阶段 prompt、`evidence_predicate` 在 verification 前执行结构化
检查、`candidate_generator` 扩展 census、`adjudication_policy` 在确定
性裁决内执行；另配手工约定层（`conventions.py` 基座）与冻结 census
配对重放入口。过程与迭代协议的真源是根目录
`FIX_VALIDATE_ITERATION_PLAN.md`；架构决策的真源是
`SEHD_RISE2_V2_REFACTOR_PLAN.md`。

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
│   ├── capabilities.py           # 可执行能力：确定性 predicate/generator runner
│   ├── deploy.py                 # DeployPlan：registry 一次编译（hash 缓存）
│   ├── runs.py                   # 运行记录：双指纹 + 每案例过程落盘 + 台账推导统计
│   ├── cli.py / __main__.py      # python -m sehd_rise2.detector
│   └── configs/detector_v1.json  # 三路由最小配置
├── evolution/                    # 自进化框架（直线依赖：worksheet ← 其他）
│   ├── worksheet.py              #   失败工作表：run tree + k=1 稳定性标记 + FN 归因 + evidence pack
│   ├── gap_taxonomy.py           #   冻结能力缺口词表（单一真源）
│   ├── router.py                 #   meta-skill 失败路由器（机械先占+闭词表+战绩记忆）
│   ├── capability_evolution.py   #   提案起草 + G1 机械门 + 有界修订循环
│   ├── artifact_validation.py    #   翻转门：stable 单滚 / unstable 三滚多数
│   ├── capability_library.py     #   持久能力库：registry 合并 + held/retired 台账
│   └── gates.py                  #   轮约束：runner 白名单 + 组合容差（datasets/calibration/）
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
  统一契约，加载 fail-closed，`detector/deploy.py` 按内容 hash 编译
  一次进 DeployPlan、同库多次检测复用同一编译）：`prompt_directive`
  型路由命中时 directive + NOT-WHEN 排除边界进入三个 system prompt；
  `evidence_predicate` 型对每个 census 候选执行结构化检查（verdict
  进 verification/defense 证据）；`candidate_generator` 型作为 census
  扩展阶段产出新候选；`adjudication_policy` 型在确定性裁决内执行
  （运行时证书类绑定，unclassified 弃权 = 基线行为）。每个 artifact
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
| 编译部署计划 | `deploy.compile_deploy_plan`（hash 缓存，分桶 + trigger 集合） |
| 部署执行面 | `api.detect(..., artifacts=...)`（四执行面，见 Detector API 节） |
| 配对重放锚 | `api.detect(..., frozen_census=...)`（裁决阶段专用） |
| 终态读取 | `result.resolve_evolution_state` 等 |
| gold 隔离 | `packet.EVALUATOR_ONLY_FIELDS` |
| 自进化轮编排 | `python -m experiments.run_fixval_round {baseline,evolve,deploy,report}` |
| 失败路由（轮内） | `evolution.router.route_failures`（闭词表 + 战绩记忆 hint） |
| 翻转门（轮内） | `evolution.artifact_validation.validate_artifacts` |

## 设计纪律（从重建中固化）

1. **检测器冻结**：一切改进必须来自可进化面，经显式参数部署。
2. **证据驱动，不手调阈值**：裁决是封闭词表的确定性聚合，无 LLM 定终态。
3. **失败即关闭**：provider/契约失败 → `runtime_valid=False`、
   `state=unresolved`、`decision_authority=invalid`，绝不冒充裁决。
4. **Gold 隔离**：gold 只属于评测侧，永不进入检测器提示词或产物。
5. **单一真源**：每个判定只写一次；`tools/check_layers.py` 机械强制依赖边界。
6. **翻转即证据**：循环内的进化判据是"错误案例被翻到正确极性"
   （unstable 案例三滚多数）；统计容差机器与入库后再验证已全部删除，
   代表性测量只属于 read-once 验证集报告。
7. **入库即部署**：能力库的每件 artifact 都带翻转门入场券；部署 =
   读库 → 编译 DeployPlan → 跑，没有二次审判。库的死亡过程（连续
   两轮闲职/证实伤人 → retired）是唯一的出库机制。
