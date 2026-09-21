# 真实数据集小规模路径与重复运行结果

执行日期：2026-09-16。结果：**PASS**（路径连通与运行一致性达标）。

在真实 AgentHallu 数据上固定底座后运行两次 `batch → evaluate`，并额外跑一个强制触发召回的阶段。案例来自开发复测集（已被开发使用），因此结论只覆盖**运行路径与运行一致性**，不是准确率，也不是独立泛化。

## 冻结底座

| 项 | 值 |
|---|---|
| 检测配置 | `detector/configs/autodl.json` |
| 模型（Judge/Defense/Recall） | `GLM-5.3-flash`，直连 `https://www.autodl.art/api/v1` |
| 思考字段 | 不发送 `thinking`（该模型始终思考，任何显式档位都会被间歇性 400 拒绝） |
| 票数 | Judge 3（补票 2）、Defense 3（补票 2） |
| 协议与案例清单 | [datasets/protocols/real_path_check.json](../../datasets/protocols/real_path_check.json) |
| 重复次数 / 并发 | 2 / 2 |
| 解析后配置 SHA-256 | `cace907899f43976d596027b9fcbb134c90660be8a3f096268b02fc6d4f2d0f1` |
| 协议 SHA-256 | `c9f0e1d0a3dfd70de5380af0b3894f55e7a51e351790e1b06844eb25015747bd` |
| 源码 SHA-256 | `452cd7ef94fa22a8c9ce8f8de51060086b1ed8421b500370d6a7379928661f8f` |

运行目录：`artifacts/real/real_path_20260916T083519896451Z`（运行时目录，已回收；逐例读数保存在本目录的 `rep1_evaluation.json`、`rep2_evaluation.json` 与 `recall_evaluation.json`）。冻结在首次运行前完成，运行后重新校验所有源码摘要，`source_unchanged` 为 `true`。

## 主检查结果

| 案例 | gold | 两次终态 | authority | 定位（gold→预测） |
|---|---|---|---|---|
| OpenDeepSearch/076.json | error | verified_error ×2 | authoritative ×2 | 1 → 3, 3 |
| OpenDeepSearch/078.json | error | verified_error ×2 | authoritative ×2 | 1 → 4, 1（不一致） |
| SmolAgents/022.json | error | verified_error ×2 | authoritative ×2 | 1 → 4, 4 |
| BFCL/012.json | clean | verified_clean ×2 | authoritative ×2 | 不适用 |
| Camel/028.json | clean | verified_clean ×2 | authoritative ×2 | 不适用 |
| Magentic_One/067.json | clean | verified_clean ×2 | authoritative ×2 | 不适用 |

- 终态一致性：判定层 **6/6 一致**；若把定位步一并计入，**5/6 一致**（OpenDeepSearch/078 的 `responsible_step` 在两次运行中为 4 与 1）。
- 6 例判定均与 gold 一致；0 unresolved、0 missing、12/12 authoritative。
- 步骤定位：两个正例批次分别命中 0/3 和 1/3，均值约 1/6，定位能力明显弱于判定能力。
- 真实路径覆盖：Judge、Step Tracer、Defense 均实际执行。

## 召回升级阶段

主检查的票数为 3，判定为 clean 的案例不会扫描，因此召回分支在主检查中始终为 `not_needed`。为覆盖该分支，额外用单票配置强制触发：

| 案例 | gold | 强制单票终态 | 阶段 |
|---|---|---|---|
| BFCL/012.json | clean | verified_error（FP） | recall_scanner ok → causal_upgrade ok |
| Camel/028.json | clean | verified_clean | recall_scanner ok |
| Magentic_One/067.json | clean | verified_clean | recall_scanner ok → causal_upgrade ok |

`recall_scanner` 在 3/3 案例上执行成功，`causal_upgrade` 在 2/3 案例上执行。**该阶段只用于证明路径连通，不能解读为准确率**：单票强制改变了判定条件，其中 BFCL/012 被升级为误报，正说明这条路径会被触发并产生实际影响。强制配置摘要见机器摘要的 `recall_phase.config_sha256`。

## 成本与波动

| 指标 | 值 |
|---|---|
| 单案耗时 | 最小 32.3 秒，最大 379.6 秒，均值 109.6 秒 |
| token | 12 次调用合计 202426，均值约 16869/案 |

同一案例两次耗时差异可达数倍（OpenDeepSearch/078 为 184.3 秒 vs 379.6 秒，SmolAgents/022 为 191.8 秒 vs 218.5 秒），属真实服务固有波动。

## 修复前的真实服务缺陷（已修复，留档）

本次检查在旧底座（`thinking.type = "low"`）上先失败过一次，暴露出两个真实缺陷，均已在本次修复并加回归测试：

1. **运行期间歇 400**：实测发 `thinking: {"type": "low"}` 时 20 次请求中 11 次被服务端以“该模型始终思考，不支持关闭思考”拒绝（约 55%），完全省略该字段则 20/20 成功。原配置 `"disabled"` 更是 100% 失败。修复后三个路由各 8 次探测无 400。
2. **故障不可区分**：`provider_worker` 对任何 HTTP 错误状态都直接崩溃，父进程只保留 200 字符 traceback，使“模型/契约错误”与“服务故障”无法区分。现在错误状态以同一信封返回，可读出 `http <status>` 与供应商原始错误信息。

## 证据边界

- 案例来自开发复测集，已被历史开发使用；6 例判定一致**不能**推断总体准确率或泛化能力。
- 0 unresolved 只说明这 6 例在两次运行中未被服务故障击穿，不代表大规模运行的稳定性。
- 定位一致性弱（5/6）与命中率低（约 1/6）是本次小样本观察，**应作为“判定正确但定位漂移”的独立失效形态继续度量**，不能当作定位能力的最终结论。
- 召回升级分支由单票强制触发，属于构造条件，不代表正常配置下的触发率或收益。
- 只验证了 Windows / Python 3.13.7 一种环境。

## 复现

```bash
python scripts/real_path_check.py
```

该命令调用真实模型服务，按协议固定模型、票数、预算与案例清单，主检查重复两次并追加召回强制阶段，生成带时间戳的新运行目录，写入 `reports/real/summary.json`、逐次评估与 `recall_evaluation.json`。离线 `scripts/verify.py` 不会重新执行此检查。
