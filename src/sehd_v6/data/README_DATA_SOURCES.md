# SEHD V6 数据文件说明

## 已复制的数据文件

### 核心数据集
- `stratified_evolution_set.json` (1.07 KB) - 分层演化数据集（来自 tmp/bccse_analysis_assets/bccse_v5/）
- `validation_set_ids.json` (1.07 KB) - 验证集ID列表
- `data_partition_v5.json` (22.29 KB) - 数据分区配置
- `baseline_predictions_by_id.json` (7.53 KB) - 按ID的基线预测数据

### 演化结果参考
- `champion_genome_v13.json` (43.33 KB) - v13 冠军基因组
- `champion_genome_v51.json` (42.88 KB) - v51 冠军基因组
- `champion_genome_v52.json` (50.74 KB) - v52 冠军基因组
- `evolution_result_v51.json` (216.19 KB) - v51 演化结果
- `evolution_result_v52.json` (243.15 KB) - v52 演化结果
- `eval_metrics_v52_full.json` (1.2 KB) - v52 评估指标
- `eval_results_v51.json` (12.18 KB) - v51 评估结果

这些冠军和评估文件是历史参考产物。它们使用旧版 genome hash 和规则
契约，当前严格加载器会拒绝无法从现行 baseline 合法重放的 lineage；不得将
它们作为修复后框架的可执行 champion。正式评估应使用当前框架重新生成并
通过严格重放校验的 `champion_genome.json`。

当前 evolution run 还会输出：

- `htc_tes_coverage.json`：类型经验覆盖摘要
- `htc_tes_experience_memory.json`：包含轨迹特征描述、类型、模块、方向和
  source replay 成功状态的完整经验记忆

### 预测数据 (JSONL)
- `skill_predictions_analysis.jsonl` (2.7 MB) - 技能预测（分析数据）
- `skill_predictions_r2_validation.jsonl` (2.1 MB) - 技能预测（R2验证）
- `predictions_cesv_v41_r1.jsonl` (1.7 MB) - CESV v41 第1轮预测
- `predictions_cesv_v42_r2.jsonl` (1.8 MB) - CESV v42 第2轮预测
- `predictions_paca_tcl_v3_5_r1.jsonl` (2.8 MB) - PACA TCL v3.5 第1轮预测

---

## 未复制的大型数据（需从原始位置访问）

### 1. AgentHallu 案例数据集（> 10MB，按政策未复制）
- **位置**: `tmp_external/AgentHallu/AgentHallu/`
- **内容**: 
  - `BFCL/` - 164 个 JSON 文件（000.json ~ 163.json）
  - `Camel/` - 93 个 JSON 文件（000.json ~ 092.json）
- **总计**: 693 个文件，约 18.79 MB
- **用途**: AgentHallu 基准测试案例数据
- **访问方式**: 直接从 `tmp_external/AgentHallu/AgentHallu/` 目录读取

### 2. 大型预测文件（> 50MB 阈值限制或过大）
- **位置**: `tmp/bccse_analysis_assets/d_baseline_v3_5/round1/predictions.jsonl`
- **大小**: 约 27.8 MB
- **用途**: D-baseline v3.5 第1轮预测结果
- **访问方式**: 直接从原始路径读取

### 3. 其他未复制的 manifest/run 文件
大量演化运行 manifest 和结果文件位于：
- `tmp/all_runs/` - 多个演化运行目录
- `tmp/bccse_analysis_assets/bccse_v5/eval/` - 评估结果
- `验证文件/agent-hallucination-pilot/artifacts/` - 各种 artifact manifest
- `验证文件/agent-hallucination-pilot/results/` - 各阶段结果
- `验证文件/agent-hallucination-pilot/runs/` - 运行记录
- `验证文件/agent-hallucination-pilot/outputs/` - 各阶段输出
- `验证文件/agent-hallucination-pilot/data/` - 数据集和选择清单

---

## 配置文件位置

配置文件已复制到 `src/sehd_v6/configs/`，包括：
- 门控配置 (`dev_gate_config.json`, `e0_v2_gate_config.json`)
- 协议配置 (`e0_gate_sanity_protocol.json`, `e0_v2_protocol.json`)
- 技能状态 schema 和初始数据
- 危害检测器 schema 和模板库

原始配置文件位置：`验证文件/agent-hallucination-pilot/configs/vgse/`
