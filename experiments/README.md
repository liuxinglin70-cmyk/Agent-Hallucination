# Experiments — 实验工作区

本目录是 SEHD-RISE 的**本地实验工作区**，用于后续试验验证：小规模逐案例
进化、ablation、transfer evaluation 与 SOTA 对比。除本 README 外，目录内
一切内容均被 git 忽略（运行产物、缓存、协议文件），可随时清理。

This is the local experiment workspace for SEHD-RISE validation. Everything
except this README is git-ignored.

## 纪律 / Discipline

实验层是框架的消费者，不是框架的一部分。遵守仓库
[README](../README.md) 的 "Core Objective and Design Discipline" 与
"Development Guidelines"：

1. 实验代码只通过公开门面调用框架：`sehd_rise.api.detection` /
   `sehd_rise.api.evolution`，或 `python -m sehd_rise <detect|evolve>`。
   不向 `sehd_rise/` 添加任何实验协议（分区、清单、授权、门控）。
2. 检测到改进的主张必须来自**独立 holdout 测量**；框架内部信号（经验记忆、
   类型遥测、outcome 漏斗）是开发证据，不是泛化证据。
3. 进度汇报必须给出具体的中间实验输出，不只是框架描述。
4. 不手调任何阈值或 CRS 权重；发现框架缺陷时回到框架层修根因。

## 布局约定 / Layout convention

```text
experiments/
├── README.md           # 本文件（唯一被 git 追踪）
├── cache/provider/     # 全局 provider 暖缓存（PACA_GLOBAL_CACHE_ROOT）
└── runs/<run-name>/    # 每次实验的输出目录（结果、基因组、审计、日志）
```

- `cache/provider/`：跨实验共享的 provider 响应缓存。在 `.env` 或进程环境中
  设置 `PACA_GLOBAL_CACHE_ROOT=experiments/cache/provider` 后，相同请求零成本
  确定性回放；修改 prompt 只会让受影响的缓存条目失效。
- `runs/<run-name>/`：一次实验一个目录，直接作为 CLI 的 `--output-dir`。
  命名建议带日期与假设，例如 `runs/2026-08-13-l1-ablation/`。

## 最小运行 / Minimal run

```bash
set DEEPSEEK_API_KEY=<secret>

python -m sehd_rise evolve \
  --records datasets/raw/agenthallu/... \
  --config sehd_rise/configs/detector_v3_5.json \
  --output-dir experiments/runs/<run-name> \
  --max-mutation-calls 100
```

产出：`evolution_result.json`（含四层 outcome 漏斗）、`champion_genome.json`、
`policy_genome.json`、`htc_tes_experience_memory.json`、`htc_tes_coverage.json`、
`evolution_state_manifest.json`。分析实验结果时按四层漏斗区分 framework
failure 与 learning reject，二者绝不合并统计。
