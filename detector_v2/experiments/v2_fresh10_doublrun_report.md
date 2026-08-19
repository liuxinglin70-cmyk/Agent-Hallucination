# detector_v2.2 阶段 A 补充验证——Fresh-10 双跑报告（防过拟合）

- 日期：2026-08-19
- 代码：`detector_v2/` V2.2（Judge + Recall/Causal Upgrade + Step Tracer 三票 + Defense 三票，证据闭包校验）
- 配置：`configs/detector_v2_autodl.json`（config_hash 见各 run.json）
- 目的：为避免对 `pilot20` 冻结集过拟合，抽出 10 例全新案例（与 pilot20 零重叠）做阶段 A 同构双跑（a1/a2），验证机制泛化性。
- 清单：`detector_v2/experiments/v2_fresh10_cases.json`
- run id：`v4_qg_fresh10_a1` / `v4_qg_fresh10_a2`，8 并发
- 输出：`runs_v2/v4_qg_fresh10_a1/`、`runs_v2/v4_qg_fresh10_a2/`

## 0. 清单构成（与 pilot20 零重叠）

5 阳性 + 5 阴性，覆盖 6 个框架、5 个领域：

| case | gold | framework | domain |
|---|---|---|---|
| OpenDeepSearch/007.json | true | OpenDeepSearch-CodeAct | Science |
| Octotools/015.json | true | Octotools | World Knowledge |
| OpenDeepSearch/043.json | true | OpenDeepSearch-ReAct | World Knowledge |
| OpenManus/023.json | true | OpenManus | Math |
| BFCL/112.json | true | Function-calling Agent | Tool Use |
| Camel/050.json | false | OWL | World Knowledge |
| OpenDeepSearch/055.json | false | OpenDeepSearch-ReAct | World Knowledge |
| Magentic_One/027.json | false | Magentic-One | Science |
| OpenManus/035.json | false | OpenManus | Math |
| OpenDeepSearch/065.json | false | OpenDeepSearch-CodeAct | World Knowledge |

## 1. 双跑终态与一致率

| case | gold | a1 预测 | a2 预测 | 一致 |
|---|---|---|---|---|
| OpenDeepSearch/007 | T | T | T | ✓ |
| Octotools/015 | T | 未决（degraded_judge_only） | 未决（同） | ✓ |
| OpenDeepSearch/043 | T | F（FN） | F（FN） | ✓ |
| OpenManus/023 | T | T | T | ✓ |
| BFCL/112 | T | T | T | ✓ |
| Camel/050 | F | T（FP） | F | ✗ |
| OpenDeepSearch/055 | F | F | F | ✓ |
| Magentic_One/027 | F | F | F | ✓ |
| OpenManus/035 | F | F | F | ✓ |
| OpenDeepSearch/065 | F | F | F | ✓ |

**终态一致率 = 9/10（90%）**，唯一翻面：`Camel/050`。
按 20 例门 18/20 的比例折算（9/10）刚好达标。

## 2. 指标

| 指标 | a1 | a2 |
|---|---|---|
| decisive | 9/10 | 9/10 |
| unresolved | 1/10 | 1/10 |
| decisive accuracy | 0.778 | 0.889 |
| all-case accuracy | 0.700 | 0.800 |
| TP / FP / FN / TN | 3 / 1 / 1 / 4 | 3 / 0 / 1 / 5 |
| positive precision | 0.750 | 1.000 |
| positive recall | 0.750 | 0.750 |
| macro-F1 | 0.775 | 0.883 |
| step localization | 1/5 = 0.200 | 1/5 = 0.200 |

## 3. 门评估（按 20 例门比例折算到 10 例）

| 门 | 阈值（20 例） | Fresh-10 观测 | 判定 |
|---|---|---|---|
| unresolved | ≤ 1/20（比例 0.05） | 1/10（0.10） | ✗ 超比例 |
| accuracy (a1) | ≥ 0.60 | 0.778 / 0.700 | ✓ |
| a1 vs a2 一致率 | ≥ 18/20（比例 0.90） | 9/10（0.90） | ✓ 刚好 |
| provider 失败率 | < 10% | 0 次传输失败 | ✓ |

注意：fresh-10 是协议外的补充防过拟合验证，不替代协议 8.1 的 frozen pilot20 双跑；
unresolved 比例按 1/10 折算超过 1/20，属于未达门的观测。

## 4. 唯一翻面根因：Camel/050（gold=false）

- Judge（Qwen3.5 三票）两跑一致判 clean（`votes=3, cast=3, positives=0`）。
- Recall Scanner（gpt-5.6-luna）两跑一致产出候选 c1（“报告 20–30 岁参与标准 vs Table 1 实测 22–26 岁”）。
- Causal Upgrade（gpt-5.6-luna）两跑一致 3/3 升级为 error（corrected_step=7）。
- 分歧出现在 **Defense（gpt-5.6-luna 三票）**：
  - a1：Defense 未反驳 → `verified_error`（authoritative，FP）；
  - a2：Defense 三票反驳（refuted：论文“Participant selection”明确写明 20–30 岁为入选标准，Table 1 的 22–26 为实测范围，非矛盾）→ `verified_clean`。

即：同一条 recall 升级候选，Defense 一次确认、一次驳回，翻面来自防御路线的非确定性，而非 judge/recall/upgrade。

## 5. 未决案例根因：Octotools/015（gold=true）

- 两跑均判 `verified_error`，但 Defense 两跑均 `inconclusive`（“CD 共 13 轨”只出现在正判断言里，轨迹无独立证据支撑），authority 降级为 `degraded_judge_only`。
- `prediction_state` 对非 authoritative 一律计未决 → 两跑 unresolved=1/10。
- 双跑在未决上一致，但该例是系统性的“证据不足导致不可定案”，并非传输失败。

## 6. 成本

| run | total tokens | provider attempts | 失败 |
|---|---|---|---|
| a1 | 197,062 | 29 | 0 |
| a2 | 206,904 | 35 | 0 |

按目的分：judge≈57.6k、recall_scanner≈70k、causal_upgrade≈34k、step_tracer≈11k、defense≈31.4k（每 run 量级相当）。

## 7. 结论

- 泛化验证初步通过机制面：9/10 双跑一致、双跑 accuracy 均 ≥0.60、零传输失败。
- 唯一翻面仍来自 **Defense 非确定性**（gpt-5.6-luna 对同一候选一次确认一次反驳），与 pilot20 上诊断的“双跑差异主要来自 provider 非确定性”一致；证据闭包校验未在本批制造任何拒绝或错步。
- 未决 Octotools/015 暴露的是“证据不足以支撑定罪”的保守行为，方向上正确但使 unresolved 比例超 1/20 折算门。
- 结论：机制泛化合格，但 8.1 协议门仍以 frozen pilot20 双跑为准；fresh-10 结果不能单独放行阶段 B。