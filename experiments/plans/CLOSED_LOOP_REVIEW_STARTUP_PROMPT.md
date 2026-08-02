# 自进化闭环审查与前置准备 Startup Prompt

你负责对仓库内的「检测器 — 自进化」闭环做**代码模块级别**的审查，验证自进化流程的正确性，并为开启**小规模自进化验证实验**做好前置准备。本任务是审查 + 修复 + 准备，不是直接跑 50 例实验。

- 工作目录：`C:\Users\liuxinglin\Desktop\agent hallucination`
- 基线：git HEAD 应包含本轮已提交的 v5.6/v5.7 修复（`module_local_score` 偏置、循环导入、死代码清理、typed-fitness 审计接线）。先 `git log --oneline -5` 确认。
- 验证命令：`python -m pytest tests -q` 与 `python -m compileall -q src tests`。开始任何修改前先确认全绿。
- 源码含中文注释。**不要用 PowerShell 的 Set-Content / Out-File 写这些文件**（会破坏 UTF-8）。用 Edit/Write 工具。

## 必须保持的架构不变式（任何修改不得违反）

1. **gold 极性优先**：gold FP/FN 决定 error_type 与 primary module；process diagnostics 只提供 secondary 模块，不得改写极性。
2. **自监督/自我 replay 证据永不 promotable**：被编辑的 detector 不能自证正确；`directional_evidence` 一律不可升级为 strong/local。
3. **gold 不得进入 skill 内容或 reflection prompt**（`gold_label` 只能用于信号路由与 fitness 计分）。
4. **diagnostic_only 证据不得用作进化依据**（`should_evolve` 恒 False）。
5. 实验协议/切分/授权不属于框架代码，属于 `experiments/` 层；不得把实验协议塞进 `src/sehd_v6/core`。

## 任务一：闭环数据流逐模块审查

按数据流逐模块读代码并写审查结论（每个模块给出 file:line、职责、输入输出、发现的问题或"确认无问题"）：

1. `src/sehd_v6/detector/`（`detect_record`、`project_for_evolution`）→ PACA-TCL v3.5 判决与 `diagnostics` 块。
2. `step_attributor.py`：`attribute_failure` 触发器级联 → ErrorType/TriggerSource/模块路由；确认 diagnostics 只改 secondary、`runtime_invalid_signal` 只做遥测。
3. `signal_adapter.py`：`resolve_authoritative_state`、`extract_coverage`、`extract_crs_signal` 投影置信度。
4. `fitness.py`：`module_local_score` 8 分支、`assess_source_fitness`、gold 方向检查。**重点复核上轮修复是否引入新偏置**（abstention 对称性、evidence_obligation min()、coverage 表单一来源）。
5. `genome.py` / `safety_gate.py`：编辑结构门、`validate_edit`。
6. `reflection_engine.py`：反思 prompt 组装，确认无 gold/case-id/自由文本泄漏。
7. `evolution_runner.py`：`run_online_evolution` 主循环 —— **一条 case 从 detect 到 experience memory 的完整链路逐行核对**：
   - trigger → attribution → HTC-TES 类型路由 → LLM 反思 → 结构门 → shadow 提交 → source replay → canary → fitness 判定 → batch commit → experience memory。
   - 重点：`assess_source_fitness` 结果与 `_htc_success`/`promotable` 的关系；`typed_fitness_assessments` 审计字段；checkpoint/计数是否一致。
8. `htc_tes/`：`type_classifier`（多信号打分与置信度）、`type_conditioned_evolution`（方向推断、模块路由）、`type_aware_fitness`（三态过程评估、experience memory）。

每发现一个缺陷，按「设计意图 vs 缺陷」定性，并给修复建议；**修复前先写最小复现测试**（或 mutant 验证测试有效），修复后跑全量测试。

## 任务二：自进化流程正确性验证

- 用 `tests/` 现有套件确认闭环各阶段的契约测试覆盖；对没有直接测试的关键路径补测试（参照已有 `_score_signal`/`_typed_edit` helper 风格）。
- 对"一条 case 自进化流程"做端到端验证（可 mock mutation provider）：验证从记录输入到 `evolution_result.json` / `champion_genome.json` / `htc_tes_experience_memory.json` 的产物一致性。
- 验证计数不变式：processed = triggered + stable；accepted + rejected + no_edit 与 gate 记录一致；checkpoint 恢复不重复计数。

## 任务三：小规模自进化验证实验前置准备

完成以下清单并报告，**不要直接启动实验**：

- [ ] 确认 evolution manifest / 实验数据源（`datasets/` 或 `experiments/protocols/` 下）存在且格式符合 `load_records` 期望（`records.jsonl` 或 case_id→record 映射）。
- [ ] 确认 `.env` 有 provider 凭据（只检查变量存在，不打印/不写入值）；`python -c "from src.sehd_v6.env_loader import ..."` 验证 route 解析。
- [ ] 用 1–3 个样例 record 跑一次 `python -m src.sehd_v6.evolution --records <small.jsonl> --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json --output-dir <tmp>` 的 smoke run，确认闭环能跑通且产物齐全（不追求 accepted>0）。
- [ ] 定义小规模验证实验的最小成功判据（建议：≤10 cases；报告 triggered/accepted/rejected/no_effect；至少 1 个 case 触发；不要求提升，但要求链路可观测、计数一致、无 runtime error）。
- [ ] 把实验设计写成 `experiments/protocols/` 下的 README 或计划文件（协议属于实验层，不得进框架代码）。
- [ ] 检查 provider cache 配置（`PACA_GLOBAL_CACHE_ROOT=experiments/cache/provider`），决定本轮实验是否启用缓存。

## 完成条件

- 全量测试通过、compileall 干净；
- 每模块审查结论与发现的修复记录在案（报告或 `experiments/progress/` 文档）；
- 小规模实验的前置清单全部核验并明确标注"可执行/阻塞及原因"；
- 如修改了代码，用 git 提交（分逻辑提交，消息风格见 `git log --oneline`）。
