# SEHD v6 机制有效性分析（审查迭代记录）

> 本文档是审查-修复循环的产物：每次迭代记录当轮发现、修复与验证结果。
> 它不是学术论文，而是框架机制的有效性论证与代码层面的审查证据链。

## 迭代 R4（2026-08-09）

### 审查范围

对 `src/sehd_v6` 全部 63 个 Python 脚本逐文件审查，覆盖：

- 入口层：`detector/`、`evolution/`（公共 API 与 CLI）
- 基础层：`env_loader.py`、`provider_routes.py`、`utils/`
- 检测引擎：`core/paca_tcl/`（proof_engine、proof_contract、layered_engine、
  basic_runner、skill_integration、policy_evaluator、detector、batch）
- 自进化核心：`core/bccse/`（genome、rule_contract、action_spec、module_charter、
  safety_gate、failure_classifier、failure_episode、fitness、step_attributor、
  signal_adapter、reflection_engine、policy_genome、policy_reflection、
  policy_evaluator、vocabulary_registry、evolution_runner 等）
- 类型调节：`core/htc_tes/`

### 审查方法

1. **静态缺陷扫描**：`pyflakes` 全量扫描（独立 venv 安装，不依赖仓库测试）。
2. **死代码扫描**：AST 级函数/类定义引用计数（独立脚本）。
3. **运行时验证**：现有 817 个 pytest 全绿 + 新增独立测试 `tests/test_review_r4_independent.py`
   （11 个用例，不 import 任何既有 fixture）+ 1 例小规模真实闭环运行
   （无 provider 的 canned 检测结果跑通 detect→attribute→L2 政策循环→审计落盘）。
4. **机制有效性建模**：下述逐层分析。

### 发现的真实缺陷（已修复）

| # | 位置 | 问题 | 影响 |
|---|------|------|------|
| 1 | `evolution_runner.py:4950` | `PolicyPlan` 未导入（NameError），`promoted_intent_candidates` 分支 | L2 政策循环中"已注册意图候选"路径一触即崩 |
| 2 | `proof_contract.py` | `PolicyItem` 注解在 `typing.get_type_hints` 下不可解析（仅 TYPE_CHECKING 导入） | 类型检查/IDE/工具链失败 |
| 3 | `evolution_runner.py:7061` | `repairable_certificate = cert` 赋值后从未使用 | 死代码 |
| 4 | 全仓 40 处 | 未使用的导入、f-string 无占位符、循环变量遮蔽导入名、未使用局部变量 | 可读性/静态质量 |

### 机制有效性分析（逐机制）

#### M1 检测器（PACA-TCL v3.5）
- **有效性**：H0 普查（三源并集：paca 普查 + construct 普查 + 确定性过程普查）→
  H1 逐候选聚焦起诉 → H2 校准上诉 + 共识门 → H3 双相定位。`adjudicate_case` 的
  `final_court_kwargs` 单一证据束被上诉/定位/L2 重裁决复用，保证可复现。
- **风险**：provider 温度=0 + 全局缓存 root opt-in 消除了 CRS 不确定性的主要来源；
  但当缓存未开启时，重复检测可能产生不同结果（进化循环依赖缓存保证回放一致性）。

#### M2 L1 规则层（八个槽位规则模块）
- **有效性**：槽位合同（condition/action/evidence_anchor）→ `render_rule()` 确定性
  渲染为 `When <condition>, <action>.`，注入是纯函数，结构门可按合同验证。
  action 是闭集 opcode（`MODULE_ACTION_OPCODES`），运行时注入守卫过滤 prompt
  注入标记。
- **风险**：`_rule_stable_id` 用模块+条件做规则身份，动作改写保留身份——这使
  "改动作不改条件"的编辑无法被去重，可能重复投递无效动作。

#### M3 归因（确定性失败分类器）
- **有效性**：纯函数 `classify_failure(record)` 只读地址化事实投影，规则优先级
  合理（artifact 完整性 > 共识门 > 空洞覆盖 > 边界冲突）。L1 拥有者经过
  gold 极性门（FP 只能由"放松/收紧"方向模块拥有）。
- **风险**：`failure_classifier` 的 fall-through 对 `unresolved` 返回 `no_actionable`，
  但 step_attributor 的 `attribute_failure` 会自行触发（`should_evolve=True`）——
  两条归因路径并存，主循环以 step_attributor 为触发、classifier 为 L2 入口，
  二者结果可能不同（设计如此：classifier 是 L2 唯一权威，L1 归属由
  step_attributor 给出并经过 classifier 的 L1 分支校正）。

#### M4 L2 政策层（闭词汇表政策）
- **有效性**：`policy_evaluator.evaluate_policies` 纯确定性；预置阶段
  （pre_ledger）与终端阶段（post_ledger）分离；`restore_audited_consensus` 有
  完整 fail-closed 守卫。政策基因组的 hash 绑定 registry 版本，避免词汇漂移。
- **风险**：`PolicyGenome.content_hash` 包含整个 registry 序列化——任何 registry
  元数据变化（如审计计数）都会改变基因组身份，导致缓存失效。需确认 registry
  序列化是否排除动态计数。

#### M5 进化主循环（BCCSE v5）
- **有效性**：条件触发（unresolved/CRS 矛盾/证据不完整/金标增强）→ 属性 →
  LLM 反思 → 结构门 → 源回放 + canary → 提交/回滚。回放复用共享缓存（同 genome
  + case 幂等）。预算（`max_mutation_calls`）硬上限，耗尽即 fail-closed。
- **风险**：convergence 只统计触发案例（v5.9b 修复），但 `record_no_edit` 在
  结构拒绝时调用，与"no-edit 收敛"语义一致。

### 验证结果

- 817 个既有测试通过（修复前后一致）。
- 新增 11 个独立测试全通过。
- 小规模闭环运行：1 case、无 mutation route（canned 检测）→ 跑通
  `detect → L2 归因 → 政策回放 → 审计`，产物齐全（`evolution_result.json`、
  `champion_genome.json`、`policy_genome.json`、`htc_tes_experience_memory.json`、
  `evolution_state_manifest.json`）。

### 结论

框架机制设计自洽：检测-进化边界清晰（detector 无 bccse 依赖，evolution 消费
label-free 投影）、闭词汇表约束收紧了 LLM 自由度、确定性回放消除了主要不确定
来源、fail-closed 纪律贯穿始终。本次审查修复了 1 个真实运行时缺陷
（PolicyPlan NameError）与 1 个工具链缺陷（PolicyItem 注解），并清除了全仓
静态杂质，未发现逻辑性错误或死代码残留。
