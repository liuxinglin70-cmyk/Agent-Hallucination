# detector_v2.2 阶段 A 补充验证——Fresh-20 双跑合并报告（防过拟合）

- 日期：2026-08-19
- 代码：`detector_v2/` V2.2（证据闭包校验 + Judge/StepTracer/Upgrade/Defense 三票多数决）
- 配置：`configs/detector_v2_autodl.json`（Qwen3.5 判决 / gpt-5.6-luna recall+defense）
- 构成：`v2_fresh10_cases.json`（10 例，已跑 a1/a2）+ `v2_fresh10b_cases.json`（新增 10 例，本次跑 a1/a2）= 合并 20 例，与 frozen pilot20 零重叠
- runs：`v4_qg_fresh10_a1/a2`、`v4_qg_fresh10b_a1/a2`；合并评估见 `runs_v2/v4_qg_fresh20_merged/evaluation.json`

## 1. 合并 20 例终态一致率（a1 vs a2）

| case | gold | a1 | a2 | 一致 |
|---|---|---|---|---|
| OpenDeepSearch/007 | T | T | T | ✓ |
| Octotools/015 | T | 未决 | 未决 | ✓（均未决） |
| OpenDeepSearch/043 | T | F（FN） | F（FN） | ✓ |
| OpenManus/023 | T | T | T | ✓ |
| BFCL/112 | T | T | T | ✓ |
| Camel/050 | F | T（FP） | F | ✗ |
| OpenDeepSearch/055 | F | F | F | ✓ |
| Magentic_One/027 | F | F | F | ✓ |
| OpenManus/035 | F | F | F | ✓ |
| OpenDeepSearch/065 | F | F | F | ✓ |
| OpenDeepSearch/015 | T | T | T | ✓ |
| OpenDeepSearch/090 | T | T | T | ✓ |
| OpenManus/067 | T | T | 未决 | ✗ |
| Camel/037 | T | F（FN） | T | ✗ |
| OpenManus/079 | T | T | T | ✓ |
| Magentic_One/039 | F | T（FP） | F | ✗ |
| SmolAgents/064 | F | F | F | ✓ |
| OpenDeepSearch/014 | F | F | F | ✓ |
| OpenDeepSearch/053 | F | 未决 | F | ✗ |
| Magentic_One/040 | F | F | F | ✓ |

**a1 vs a2 一致率 = 15/20（75%），低于 18/20 门槛。**

## 2. 合并指标

| 指标 | a1 | a2 |
|---|---|---|
| decisive | 18/20 | 18/20 |
| unresolved | 2/20 | 2/20 |
| decisive accuracy | 0.778 | 0.944 |
| all-case accuracy | 0.700 | 0.850 |
| TP / FP / FN / TN | 7 / 2 / 2 / 7 | 7 / 0 / 1 / 10 |
| positive precision | 0.778 | 1.000 |
| positive recall | 0.778 | 0.875 |
| positive F1 | 0.778 | 0.933 |
| step localization | 3/10 | 3/10 |

## 3. 门评估（20 例，与协议 8.1 对齐）

| 门 | 阈值 | 观测 | 判定 |
|---|---|---|---|
| unresolved | ≤ 1/20 | 2/20（a1、a2 各 2） | ✗ 超门 |
| accuracy (a1) | ≥ 0.60 | 0.700 | ✓ |
| a1 vs a2 一致率 | ≥ 18/20 | **15/20** | ✗ 未达门 |
| provider 失败率 | < 10% | 0 次传输失败 | ✓ |

结论：**合并 fresh-20 双跑未通过阶段 A 门**，主要原因是 a1/a2 一致率 15/20 与 unresolved 2/20 双双不达标。

## 4. 5 个分歧逐案根因

全部 5 个分歧均落在 recall → upgrade → defense 链路（gpt-5.6-luna 路线），judge（Qwen3.5）在所有分歧中两跑一致：

- **Camel/050（F）**：judge 两跑 clean；recall+upgrade 两跑对 c1 3/3 升级；分歧在 **Defense 非确定性**——a1 确认（FP），a2 三票反驳（refuted：20–30 岁是论文入选标准、Table 1 的 22–26 是实测范围）→ TN。
- **OpenManus/067（T）**：judge 两跑 error；a1 有 2 票 error 且 defense 折叠 refuted 仍保持 error（authoritative）；a2 judge 2/3 票 error，defense 三票 inconclusive → 降级 `degraded_judge_only` → 未决。分歧为 T vs 未决。
- **Camel/037（T）**：judge 两跑 clean；**recall 候选集非确定性**——a1 只找到 c1（upgrade 1/3 不升级）→ FN；a2 找到 c1+c2，c2 3/3 升级 → TP。
- **Magentic_One/039（F）**：judge 两跑 clean；recall 两跑都找到 c1+c2；**upgrade 投票非确定性**——a1 对 c2 2/2 升级 → FP；a2 对 c2 0/3 不升级 → TN。
- **OpenDeepSearch/053（F）**：judge 两跑 clean；recall+upgrade 两跑均对 c1 升级；**Defense 非确定性**——a1 未反驳（3/3 确认）但最终仍降级为 degraded（unresolved）；a2 三票反驳（标题与轨迹一致、标题差异不影响“获奖者”交付）→ TN。

未决 2 例（两跑均未决）：
- **Octotools/015（T）**：judge/tracer 两跑一致指向 event_004，defense 两跑三票 inconclusive（“CD 仅 13 轨”只出现在断言中，轨迹无独立证据）→ degraded_judge_only。
- **OpenManus/067（T）**（a2 侧）：同上 defense inconclusive。

## 5. 成本

| run | total tokens | provider attempts | 失败 |
|---|---|---|---|
| fresh10_a1 | 197,062 | 29 | 0 |
| fresh10_a2 | 206,904 | 35 | 0 |
| fresh10b_a1 | 202,853 | 36 | 0 |
| fresh10b_a2 | 205,810 | 44 | 0 |
| 合计 | 812,629 | 144 | 0 |

## 6. 结论

- 机制泛化面（recall/upgrade 能找回 3 例 judge-clean 的真阳性：Camel/037、BFCL/112、OpenDeepSearch/090 等）得到复现。
- 但 **双跑一致率未达标（15/20 < 18/20）**，且未决 2/20 超门。分歧与未决全部集中在 gpt-5.6-luna 的 recall/upgrade/defense 链路，Qwen3.5 judge 稳定。
- 这说明当前三票机制只对 judge 生效，upgrade/defense 的多数决（per-candidate 3 票）仍会因候选集/单票语义波动整体翻转，导致终态在 20 例规模上不稳定。
- **阶段 B 不能放行**。建议下一步：将 defense/upgrade 的多数决升级为“候选级+证据闭包级”的双重多数决，或对 unresolved 降级路径增加二阶段复核，再做 frozen pilot20 与 fresh-20 双跑复验。