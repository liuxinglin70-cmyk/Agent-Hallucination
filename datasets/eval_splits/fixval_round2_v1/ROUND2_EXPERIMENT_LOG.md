# Round-2 完整自进化周期实验日志

- 日期：2026-08-16（18:29 – 21:00）
- 检测器指纹：`f7959faf5d1a570a`（当日 prompt 修复后的判决面）
- 数据：AgentHallu，round-2 全新重抽样（从未进入任何已记录 run）
- 并发：20 workers 全程；门校准：`datasets/calibration/gate_calibration.json`（绝对零容差，`test_retest_flip_rate=null` 未标定）
- 本轮是自进化框架历史上**第一次端到端完整周期**（resample → baseline → evolve → deploy → report）

## 0. 前置：框架闭环改造（本轮实验得以成立的结构修复）

当日机制审计发现三个使自进化无法累积生效的缺口，实验前全部闭合：

1. **约定通道跨轮无记忆** → 新增持久约定库 `datasets/convention_library/registry.json`：
   `merge_round_registry` 以 `R<轮>-<id>` 命名空间按轮折叠、幂等替换（171 tests 含锁定测试）。
2. **双通道从不组合部署** → deploy 臂改为组合注册表：约定库（1 条 R2-EC-05）+ 冻结策略库
   （5 条 L2-P1..P5），一次测量「完整进化检测器 vs 裸基线」。
3. **轮级对比被 census 噪声淹没** → deploy 臂改为**配对设计**：逐例冻结 baseline 臂的
   census，只重放判决层（verification/defense/binder），翻转全部归因于进化产物而非重采样噪声。

另：evolve 前的代码级审计已修复 replay unclassified→None parity 漂移、cycle 入口 binding
覆盖 fail-closed、G1 diagnostic-only 类别孔等 8 处断点（170→171 tests 全绿）。

## 1. 重抽样（fixval_resample --round 2, seed 20260818）

- evolution 60 = 36 error + 24 clean；validation 40 = 24 error + 16 clean
- 污染联合集 309 例（含当日全部 detector60 系列 run）全部排除；与历史 run 重叠 0、子集间重叠 0
- 清单：`evolution_cases.json` / `validation_cases.json`（运行前冻结）

## 2. Baseline（fixval_r2_baseline_20260816_182901）

裸检测器（无任何 artifact）、100 例、20 并发、约 43 分钟。

| 切片 | acc | TP | FP | FN | TN | 查准 | 查全 |
|---|---|---|---|---|---|---|---|
| 全量 100 | **0.67** | 50 | 23 | 10 | 17 | 0.685 | **0.833** |
| evolution 60 | 0.6667 | 31 | 15 | 5 | 9 | 0.674 | 0.861 |
| validation 40 | 0.675 | 19 | 8 | 5 | 8 | 0.704 | 0.792 |

- 100/100 权威判决、0 unresolved；state：73 error / 27 clean
- 遥测：261 次 provider 调用、3.96M tokens、缓存命中 244K、单例 p50=230s / max=1286s
- 参照：round-1 baseline（修复前检测器）acc 0.66（P .724 / R .70）——当日检测器修复在全新
  split 上泛化：查全 +13pp（FN 18→10），代价 FP 16→23，净 +1 例
- 误差结构：FP 23 / 33 = 70% 的误差面是误定罪——恰是两条进化通道的靶面

## 3. Evolve（fixval_r2 evolve）

### 3.1 失败工作表 + 复现过滤（一轮未引导重跑）

- miss：5 例中 **3 例复现**（2 例被重跑自然消除 = 采样噪声，符合噪声地板预期）
- wrongful conviction：15 例中 **14 例复现**
- 进入提取的工作表：3 miss + 14 定罪（evolution 子集内）

### 3.2 提取 + G1（1 次 provider 调用，deepseek-v4-pro）

5 条原始约定 → **5 条全部通过 G1**（0 拒绝；双向边界/闭卷词表/≥2 同机制案例全达标）：

| id | 方向 | gap | 主题 | 触发特征 |
|---|---|---|---|---|
| EC-01 | miss | exactness_boundary | 检索字段数值须对齐 | final_prose_report, numeric_observations, tool_calls_with_arguments |
| EC-02 | wrongful_conviction | strictness_miscalibration | 已恢复的参数错误不是终局幻觉 | final_prose_report, structured_status_observations, tool_calls_with_arguments |
| EC-03 | wrongful_conviction | unsupported_claim_grounding | 任务要求的完成态总结非无据主张 | final_prose_report, structured_status_observations, task_specifies_values |
| EC-04 | wrongful_conviction | strictness_miscalibration | 已更正的中间推导非终局幻觉 | final_prose_report, multi_tool_sequence |
| EC-05 | wrongful_conviction | expression_tolerance | 保内容的格式/措辞变化不是偏差 | final_prose_report, task_specifies_values |

### 3.3 G2 逐约定配对重放（冻结 census；哨兵=31 个 gold-error 定罪案例）

| id | targets | sentinels | clean池 | 修复 | 无罪释放 | 哨兵损失 | clean定罪 | discovery伤害 | 净增益 | 门 |
|---|---|---|---|---|---|---|---|---|---|---|
| EC-01 | 3 | 31 | 8 | 0 | 0 | **1** | 0 | 0 | **−1** | 拒（哨兵损失） |
| EC-02 | 2 | 31 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 拒（未接合：G2a） |
| EC-03 | 2 | 31 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 拒（无严格改进） |
| EC-04 | 2 | 31 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 拒（无严格改进） |
| **EC-05** | 2 | 31 | 8 | 0 | **1** | **0** | 0 | 0 | **+1** | **通过** |

- **EC-05 为唯一可部署约定**：其配对无罪释放 = SmolAgents/013.json（错误定罪的措辞级差异）
- 入库：`datasets/convention_library/registry.json` ← `R2-EC-05`（prompt_directive，
  gap=expression_tolerance/precision，含完整 gap + capability_delta 契约块）

## 4. Deploy（fixval_r2_deployed_20260816_201706，配对臂）

- 组合注册表：1 prompt_directive（R2-EC-05）+ 5 adjudication_policy（L2-P1..P5），hash `6b78466702bb…`
- 100 例 × 冻结 baseline census 判决层重放；EC-05 按触发特征在 68/100 例上激活
- 100/100 权威、0 unresolved；233 次调用、2.47M tokens、缓存 372K、单例 p50=145s / max=699s

## 5. Report（round_report.json）

| 切片 | baseline | deployed | 翻转 |
|---|---|---|---|
| validation 40（read-once） | acc .675 / TP19 FP8 FN5 TN8 | **完全相同** | **0 翻转** |
| evolution 60（分解） | acc .6667 / TP31 FP15 FN5 TN9 | acc .6667 / TP30 FP14 FN6 TN10 | 4 翻转，净 0 |
| 全量 100 | acc .67 | acc .67（TP49 FP22 FN11 TN18） | 净 0 |

### 4 个翻转的逐例归因（fired 记录为证）

| 案例 | 翻转 | gold | 归因（证据） |
|---|---|---|---|
| SmolAgents/013 | error→clean | clean | **约定 EC-05 的真实效果**：fired 空、EC-05 激活；与 G2 预测的配对无罪释放完全一致——G2 的 +1 精确迁移到部署臂 ✓ |
| BFCL/052 | error→clean | **error** | **策略 L2-P1 的 FN 风险落地**：fired=requirement_compliance×require_corroborating（binder 把真定罪证书归类为合规类且无 content 共证 → 撤销定罪）；EC-05 未激活 |
| OpenDeepSearch/013 | error→clean | **error** | 同上：fired=requirement_compliance×require_corroborating |
| OpenManus/102 | clean→error | error | **重放噪声**：EC-05 未激活、策略只能撤销定罪（不可能造出定罪）；v/d 温度 0 重判的自然波动；其 fired(premise_conflict withhold) 未阻止定罪 |

## 6. 结论

1. **闭环首次全链路跑通且各阶段行为符合设计**：复现过滤剔除 2/20 采样噪声失败；G1 五条全过；
   G2 以哨兵损失/未接合/无改进三由拒掉四条、仅放行净 +1 零损失的 EC-05；入库与组合部署按新结构执行。
2. **约定通道的首次真实兑现**：EC-05 通过 G2 的效果（1 例 FP 修复）在部署臂上原样复现，
   且零副作用——「门说什么、部署得什么」的因果链第一次被实测证实。
3. **策略层的真实成本首次在全新案例上测得**：L2-P1（requirement_compliance 佐证要求）在
   runtime binder 下撤销了 2 个真定罪（TP 损失）。这是 V2 组合部署的第一手证据：策略层的
   FN 风险不是理论——binder 把真错证书错分类时，corroboration 语义会吞掉 TP。
4. **净效应 = 0**（约定 +1、策略 −2、噪声 +1/−1 抵消）。validation 40 上零翻转：进化产物
   在本轮没有触碰未见过分布的可测改变。
5. 累积状态：约定库 1 条（R2-EC-05）——round-3 起将带着它运行，跨轮复利机制就位。

## 7. 成本汇总

| 阶段 | 调用 | tokens | 墙钟 |
|---|---|---|---|
| baseline 100 | 261 | 3.96M | ~43 min |
| evolve（重跑 20 + 提取 1 + G2 ~45 全量重滚 + ~200 配对/发现臂） | — (未落盘) | — (未落盘) | ~95 min |
| deploy 100（配对） | 233 | 2.47M | ~29 min |
| report | 0 | 0 | <1 min |

## 8. 遗留与下一步

- **策略层 TP 损失对策**：binder 对 requirement_compliance 的 over-binding（真内容错误被
  归为合规类）是 L2-P1 吞 TP 的直接原因——对应 binder 回路（over/under-bound 测量→定义
  收紧）与策略周期的 retire/amend 通道，round-3 的策略 cycle 应消费本轮证据。
- **evolve 阶段遥测未落盘**（confirm/G2 的 provider 用量只在 stdout）——应纳入下一次驱动改造。
- 门校准仍为绝对零容差（无稳定性双跑）；EC-01 的哨兵损失若含噪声成分会被误拒，标定后复验。
- validation 40 分辨率 ±3 例 vs 单约定效应 1-2 例：跨轮累积是让效应变得可测的唯一路径，
  约定库已为此就位。
