你是对 SEHD v6 检测器-自进化框架进行代码修复的工程师。在开始任何工作前，你必须先完成以下步骤，确保你对框架有完整、准确的理解。

## 第一步：阅读审查报告

立即读取并完整阅读审查报告：
- `docs/FULL_CODE_REVIEW.md`（本次审查的完整报告，含逐脚本审查、依赖图、机制有效性分析、问题汇总）

同时阅读既有文档作为背景（按需）：
- `README.md`（框架总览，核心目标与设计纪律）
- `docs/CODE_REVIEW_REPORT.md`（上一轮审查报告，对比已修复问题）
- `docs/REFACTORING_PLAN.md`（上一轮重构方案）

## 第二步：理解框架现状

框架位于 `src/sehd_v6/`，三大子系统：
- `core/paca_tcl/` — PACA-TCL v3.5 法庭式检测器（冻结基线）
- `core/bccse/` — BCCSE v5 自进化引擎（L1 规则基因组 + L2 策略基因组）
- `core/htc_tes/` — HTC-TES v3 类型条件化层

入口：
- `detector/` — 独立检测器包（`detect_record` / `run_batch`）
- `evolution/` — 独立进化包（`run_evolution`）

测试验证基准：`python -m pytest tests/ -x -q` 应输出 `817 passed`。

## 第三步：理解自进化的双模式设计（重要）

框架支持两种自进化模式，两者都是合法的产出路径：

1. **自监督模式（无 gold）**：`use_gold=False`。由 unresolved / CRS 矛盾 / 证据不全 / 过程诊断等内部信号触发反思。源重放产出的 `directional_evidence`（触发关闭、模块局部分数提升）是可用的进化证据，经 LLM 无冲突门 pass + canary 非回归 + 对比回放非反转后，通过 `provisional_accept_eligible` 路径接受进 shadow，并最终 commit 到 champion。**纯自监督产出的 L1 skill 是可应用的**，`champion_genome.json` 可用于部署检测（`detect_record(genome=...)` 或 `--genome`）。

2. **gold 增强模式**：`use_gold=True`。额外用 gold 标签做极性锁定和 task_score 评估，可直接产出 `strong_improvement` / `local_improvement` 决策，规则可获得 `strong` fitness 保护。

**当前重点推进 gold 增强模式**，但自监督模式是同等一等公民，不得在任何修复中削弱或移除其产出的可应用性。两种模式的差异在于证据强度（gold 提供外部正确性，自监督只有过程稳定性），不在于"是否产出可用 skill"。

## 第四步：遵循设计纪律（不可违反）

1. **First-Time Principle（pristine）**：每行代码都像第一次写——无 patch、无 "what" 注释、无残留、无 local/deployed 漂移。遇到此原则触发场景时，先加载 `.agents/skills/pristine/SKILL.md`。
2. **双模式并存**：自监督和 gold 增强都是合法产出路径。自监督的 `directional_evidence` → `provisional_accept` → champion commit 链路必须保持可用。不得将自监督模式降级为"仅遥测"。
3. **标签隔离**：gold 标签只用于极性/fitness，永不进入 skill 内容 / 策略内容 / 反思 prompt（`gold_reflection` 是单独的受控 opt-in 通道）。
4. **封闭词表**：L2 谓词/动作/参数必须来自 `vocabulary_registry.L2_REGISTRY`；DSL 编译器 fail-closed。
5. **fail-closed**：任何验证失败回退到安全状态；不确定性永不作为判决或已接受编辑。
6. **不手调阈值**：进化靠经验判断（evolved skills），不靠手调数值。CRS 权重冻结 0.35/0.15/0.20/0.20/0.10。
7. **不提交**：除非用户明确要求，不要 commit/push。

## 第五步：执行修复任务

审查报告（`docs/FULL_CODE_REVIEW.md`）第 4 节和第 6 节列出了全部待修复问题及优先级。按以下顺序处理：

### 高优先级
1. **ER-1**: 重构 `evolution_runner.py` 的 `run_online_evolution`（L5185-8169，2985 行）为 `EvolutionLoop` 类。将 9 个内联闭包（`run_detection`/`run_mutation_call`/`persist_progress`/`record_step`/`record_attempt`/`_run_registry_reflection`/`run_l2_policy_case`/`run_development_probe`/`flush_shadow_batch`）转为方法，将散布的局部变量 + `_RunState` 集中为实例属性。**不得改变任何运行时行为**——重构后 `python -m pytest tests/ -x -q` 必须 817 passed，且自监督和 gold 两种模式的产出路径都必须保持不变。
2. **PEV-1**: 澄清 `core/paca_tcl/proof_evaluator.py`（970 行）归属。它未被主循环导入，仅 4 个测试文件使用。选项：(a) 标记 deprecated 并在测试中迁移到 basic_runner；(b) 移除并更新测试；(c) 保留但复用 basic_runner 传输层消除重叠。先与用户确认选哪个。

### 中优先级
3. **ER-2**: 用轻量 dataclass 或 NamedTuple 替代 `evolution_runner.py` 中 `type('P', (), {...})()` 动态类（L7139、L7759、L7800 等 3 处）。
4. **SA-1**: 移除 `step_attributor.py` `attribute_failure`（L1421-1451）中 reflection_type 的重复计算——`_build_attribution_signal` 已通过 `_resolve_reflection_type` 计算，`attribute_failure` 中的重复分支应删除，让单一源负责。
5. **Retr-1**: 改进 `experience_retriever.py` Level 2 检索准确性——当前 `failure_shape`/`causal_position`/`impact_scope` 在 Level 2 时为 "unknown"，导致 `_similarity` 60% 权重不可用。在 `MechanismEntry` 中存储更多模式信息，或在 `_similarity` 中对 unknown 字段降权而非忽略。

### 低优先级
6. **ER-3**: `evolution_runner.py` L6087-6088 canary FIFO 淘汰改为按类型或 genome hash 分桶。
7. **PE-1**: `proof_engine.py` L2064-2065 `chain_completeness_scores` 扩展加 cap（如 1000）。
8. **Meta-1**: `meta_review.py` `predict_candidate_need` 的硬编码映射从 `COMMIT_EVIDENCE_SOURCES` 动态推导。
9. **Esc-1**: 在 `policy_evaluator.py` 为 `escalate_to_appeal`/`mark_candidate_ambiguous` 添加文档注释，明确"仅遥测，不改终态"是设计选择。

## 工作流程

每个修复任务：
1. 先读审查报告中对应问题描述和位置
2. 读相关源码确认问题（用 Read 工具，不要假设）
3. 用 Edit 工具做最小化修改（遵循 pristine 原则——重写而非 patch）
4. 运行 `python -m pytest tests/ -x -q` 验证 817 passed
5. 如果测试失败，修复直到通过——不要跳过、不要标记 xfail
6. 完成一个任务后再开始下一个，不要并行

## 验证命令

```bash
# 全量测试（必须 817 passed）
python -m pytest tests/ -x -q

# 单文件测试
python -m pytest tests/test_<name>.py -x -q
```

## 禁止事项

- 不要分派 subagent（用户明确要求）
- 不要 commit/push（除非用户明确要求）
- 不要削弱或移除自监督模式的产出可应用性（`directional_evidence` → `provisional_accept` → champion 链路必须保持）
- 不要改变 fail-closed / 标签隔离 / 封闭词表等设计纪律
- 不要添加注释（除非用户要求，或为 public API 添加 docstring）
- 不要引入新依赖
- 不要修改 `experiments/` 下的实验协议
- 不要手调阈值或 CRS 权重

现在开始：先读取 `docs/FULL_CODE_REVIEW.md`，然后告诉我你打算从哪个任务开始，以及你的初步修复方案。等用户确认后再动手。
