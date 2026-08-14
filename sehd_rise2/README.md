# SEHD-RISE2 — 重构工作区（第一里程碑：冻结检测器）

本目录是重构后的代码框架工作区，与旧库 `sehd_rise/` 完全独立：**旧库保持
原样不动**；本目录中的代码从旧库**抽取**并重新组织（详见文末"抽取来源"）。

> 纪律：`sehd_rise2` 与 `sehd_rise` 之间禁止任何 import 依赖（由
> `tools/check_layers.py` 机械强制）。检测器基座冻结；一切改进必须来自
> 未来的两个可进化面（L1 Skill / L2 裁决策略），通过显式部署参数注入，
> 不引入框架级全局状态。

## 验证状态（第一里程碑）

- [x] `python sehd_rise2/tools/check_layers.py` → 44 文件、0 违例
- [x] 包可导入：`api` / `replay` / `evolution_api` / `authority`
- [x] `python -m sehd_rise2.detector --help` 正常（`--config` 缺省随包配置）
- [x] `detector_v3_5.json` 与基线基因组加载正常
- [x] 输入防火墙：合法记录通过；含 gold 字段（如 `is_hallucination`）被拒
- [ ] 真实 provider 端到端冒烟（需 API 额度，未执行）

## 目录结构

```text
sehd_rise2/
├── detector/                     # 冻结检测器包（可独立运行）
│   ├── cli.py                    # 唯一 CLI：python -m sehd_rise2.detector
│   ├── api.py                    # 公共门面（detect / 批量 / 复本聚合 / 加载器）
│   ├── authority.py              # fail-closed 结果读取（从 causal/signals 抽取）
│   ├── replay.py                 # 进化接口：候选基因组重放（从 execution/replay 抽取）
│   ├── evolution_api.py          # 进化接口：skill 加载 + Detection Truth 投影
│   ├── configs/detector_v3_5.json
│   ├── runtime/                  # 平台层：env / routes / models / usage / gold / jsonio
│   ├── court/                    # 证明法庭：engine / contract / audit / identity
│   ├── pipeline/                 # 检测流水线：core / packet / transport / egress /
│   │                             #   policy / injection / continuation /
│   │                             #   route_replay / replicas / batch
│   └── skills/                   # L1/L2 可进化面运行时：genome / rule / action /
│                                 #   binding / anchors / capabilities /
│                                 #   l1_execution / policy / vocabulary
├── evolution/                    # 未来自进化框架（占位；接口契约见 INTERFACES.md）
└── tools/check_layers.py         # 层级守卫（机械强制依赖边界）
```

层级规则（`tools/check_layers.py`）：

```text
runtime -> 无
skills  -> runtime
engine  -> runtime, skills      （court 与 pipeline 构成同一 engine 层）
core    -> runtime, skills, engine   （api/cli/replay/authority/evolution_api）
evolution -> detector 任意子层（单向）
```

## 独立运行

```bash
python -m sehd_rise2.detector --record trajectory.json \
    --output-dir out [--genome genome.json] [--policy-genome policy.json]
```

- `--config` 缺省使用随包发布的 `detector/configs/detector_v3_5.json`；
- 凭据从进程环境或仓库根 `.env` 读取（`runtime/env.py` 逻辑原样抽取）；
- 单案例输出 `out/prediction.json`；批处理 `--records` 输出 `out/summary.json`。

## 为自进化预留的接口（detector 侧，冻结）

| 用途 | 接口 |
|---|---|
| 加载 skill | `api.load_skill_genome` / `api.load_policy_genome` / `evolution_api.create_baseline_genome` |
| 部署执行 | `api.detect(record, genome=..., policy_genome=...)` |
| 候选重放 | `replay.run_replay` / `replay.run_constructive_replay` |
| 数据供给 | `evolution_api.project_for_evolution(result)` |
| 终态读取 | `authority.resolve_evolution_state` 等 |
| gold 隔离 | `FORBIDDEN_INPUT_FIELDS` / `EVALUATOR_ONLY_FIELDS` |

详细契约与进化框架的责任边界见 `evolution/INTERFACES.md`。

## 抽取来源与刻意未抽取

- **抽取**（代码内容按模块抽取后落入新结构，import 前缀随新结构改写）：
  - `runtime/` ← 旧 `platform/`；
  - `court/` ← 旧 `detection/court/`；
  - `pipeline/` ← 旧 `detection/` 其余（其中 `detector.py` 拆分出 CLI 后落为
    `pipeline/core.py`，CLI 单独新写为 `detector/cli.py`，消除旧库的双 CLI）；
  - `skills/` ← 旧 `skills/` 运行时子集；
  - `authority.py` ← 旧 `causal/signals.py` 的 fail-closed 读取助手（跨文件抽取）；
  - `replay.py` ← 旧 `execution/replay.py` 的重放封装（跨文件抽取，去掉进化侧
    预算计数）。
- **刻意未抽取**：
  - `skills/vocabulary/evolution.py`（L2 词表进化 planner，属进化侧）；
  - 旧库 `causal/ experience/ learning/ typing/ planning/ execution/
    evaluation/ meta/ loop/` 整层（进化框架，将在 `evolution/` 内重建）；
  - 旧库的调用预算/账本逻辑（属于进化侧职责）。
