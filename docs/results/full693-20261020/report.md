# 全693例检测结果：第二轮分类统一补测后

693例全部返回。原最终687例权威，本轮失败类1例和降级类5例统一补测后新增恢复3例；最终 **690/693权威**，3例未决。未恢复者保留原选定轮结果，不再重试。

| 指标 | 最终结果 |
| --- | ---: |
| 权威准确率 | 531/690 = **76.96%** |
| 全案例准确率 | 531/693 = 76.62% |
| 首错定位 | 206/443 = **46.50%** |
| TP / FP / FN / TN | 335 / 53 / 106 / 196 |
| 正类精确率 / 召回率 / F1（权威口径） | 86.34% / 75.96% / 80.82% |

## 轮次、冻结集合与采纳

用户授权本次为各类第二轮且为最后一轮；前一统一恢复full693_recovery1计为第一轮。降级类run_id full693_degraded1按用户指定命名，其逻辑类别轮次仍为2。底层传输重试和合同修复保持冻结配置，所有每次尝试均保留，不另开补测批次。

| 轮次 | 固定案例 | 新恢复 | 采纳条件 |
| --- | --- | ---: | --- |
| full693_main | 全693例 | 682权威 | 原始主轮 |
| full693_recovery1 | 主轮全部11例非权威 | 5 | 仅authoritative，原最终687 |
| full693_failed2 | Camel/039.json | 1 | 仅authoritative |
| full693_degraded1 | Camel/042.json、Camel/048.json、Octotools/015.json、OpenDeepSearch/000.json、SmolAgents/078.json | 2 | 仅authoritative |

案例集合按recovery1最新运行状态一次性冻结，选例及采纳均不使用金标或正确性。未重测其余687例权威案例；本轮没有修改detector源码、有效配置、提示词、预算、原始输入或freeze.json。BFCL162例轮次事件缺失的行动预测跳过是结构限制，没有列入补测。

最终693例采用来源：full693_main=685；full693_recovery1=5；full693_failed2=1；full693_degraded1=2。

| 案例 | 本轮来源 | 本轮状态 | 是否采纳 | 最终采用来源 |
| --- | --- | --- | --- | --- |
| Camel/039.json | full693_failed2 | authoritative | 是 | full693_failed2 |
| Camel/042.json | full693_degraded1 | degraded | 否，保留原轮 | full693_main |
| Camel/048.json | full693_degraded1 | authoritative | 是 | full693_degraded1 |
| Octotools/015.json | full693_degraded1 | degraded | 否，保留原轮 | full693_main |
| OpenDeepSearch/000.json | full693_degraded1 | authoritative | 是 | full693_degraded1 |
| SmolAgents/078.json | full693_degraded1 | degraded | 否，保留原轮 | full693_main |

## 仍失败与仍降级：停止重试

仍失败：无。

仍降级/语义未决：Camel/042.json、Octotools/015.json、SmolAgents/078.json。

- Camel/042.json：defense / inconclusive。本轮未authoritative，保留full693_main；类别轮次已达上限，停止。
- Octotools/015.json：defense / inconclusive。本轮未authoritative，保留full693_main；类别轮次已达上限，停止。
- SmolAgents/078.json：defense / inconclusive。本轮未authoritative，保留full693_main；类别轮次已达上限，停止。

## 同案配对比较

对最近随机100例的共同权威97例：历史73正确、当前77正确；检测6修正/2回退，差+4.12个百分点。

共同权威阳性64例首错：历史33、当前34；4修正/3回退。

检测修正：BFCL/028.json、BFCL/080.json、BFCL/109.json、BFCL/141.json、Magentic_One/077.json、OpenManus/069.json。

检测回退：OpenDeepSearch/083.json、OpenManus/071.json。

首错定位修正：BFCL/028.json、BFCL/141.json、Camel/055.json、Magentic_One/011.json。

首错定位回退：BFCL/082.json、Magentic_One/001.json、SmolAgents/001.json。

同样本100例的全请求口径：历史97例权威、73例正确；当前100例权威、80例正确，首错36/67。覆盖改善与上述共同权威配对收益分别报告。历史G0仅653例，不能称作历史693全量。

本轮只恢复覆盖，没有替换已有权威判断。整个实验与历史的源码/提示/预算/并发不同，不能归因于本次补测或单一模块。693例属于开发暴露集合，非独立盲测。

## 新增尝试成本与全部成本

本轮新增69 started、69 finished，0条结束记录缺usage，0条开始后结束未知。已知新增usage 1,119,341 tokens（prompt 542,597，completion 576,744）。

| 新轮 | 墙钟秒 | started / finished | 缺usage | 已知tokens |
| --- | ---: | ---: | ---: | ---: |
| full693_failed2 | 530.4 | 17 / 17 | 0 | 346,721 |
| full693_degraded1 | 719.6 | 52 / 52 | 0 | 772,620 |

含冒烟、主轮、recovery1及本次两类全部采用/未采用尝试：5875 started/5875 finished，5条缺usage；已知usage共70,352,749 tokens。所有缺usage均保留为未知，合计为下界；未据此换算费用。详情见[all-attempt-costs.json](all-attempt-costs.json)。

## 分家族

| 家族 | 请求/权威 | 正确/权威 | FP / FN | 首错/阳性 |
| --- | ---: | ---: | ---: | ---: |
| BFCL | 164/164 | 102/164 (62.20%) | 29 / 33 | 29/103 |
| Camel | 93/92 | 81/92 (88.04%) | 6 / 5 | 42/57 |
| Magentic_One | 94/94 | 73/94 (77.66%) | 3 / 18 | 22/54 |
| Octotools | 47/46 | 34/46 (73.91%) | 4 / 8 | 14/30 |
| OpenDeepSearch | 100/100 | 83/100 (83.00%) | 2 / 15 | 23/58 |
| OpenManus | 104/104 | 83/104 (79.81%) | 2 / 19 | 51/84 |
| SmolAgents | 91/90 | 75/90 (83.33%) | 7 / 8 | 25/57 |

分家族同案配对（最近random100共同权威）：

| 家族 | 共同权威 | 检测修正/回退 | 共同权威阳性 | 首错修正/回退 |
| --- | ---: | ---: | ---: | ---: |
| BFCL | 19 | 4/0 | 14 | 2/1 |
| Camel | 13 | 0/0 | 8 | 1/0 |
| Magentic_One | 24 | 1/0 | 14 | 1/1 |
| Octotools | 7 | 0/0 | 5 | 0/0 |
| OpenDeepSearch | 7 | 0/1 | 3 | 0/0 |
| OpenManus | 16 | 1/1 | 12 | 0/0 |
| SmolAgents | 11 | 0/0 | 8 | 0/1 |

## 审计与历史保留

2849个既有运行文件的字节摘要与补测前一致；原始主轮、recovery1及新轮逐例原件、请求日志和每次尝试保持。旧attention.json为前一控制器结尾审计断言历史现场，已在旧阶段完成安全接续，本轮未覆盖该文件。

新轮完整执行身份与各自recovery1原案例逐字段相同；commit、预测前缀原文与ID、生产裁决、最终采纳与全部请求计量均通过。阶段引用或听证未决不是可通过修改提示词/预算修复的本轮授权内容；本轮没有进行代码修复或调参。

补测前的全部汇总版本见 `artifacts\detector\live_checks\full693_20261020\history\before-category2_20261019T220416Z`（含artifacts与docs原字节、旧运行SHA256清单）。本报告和当前命名汇总为其后继版本；旧原件没有被覆盖。原始新轮在 `artifacts/detector/live_checks/full693_20261020/runs/`，冻结清单见category-round2-policy.json。
