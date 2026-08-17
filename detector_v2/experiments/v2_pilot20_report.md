# detector_v2 Pilot-20 实验报告

- 日期：2026-08-17
- 运行：`python -m detector_v2 batch --workers 10`
- 配置：`detector_v2/configs/detector_v2_autodl.json`
- 案例：`datasets/eval_splits/v3_evo50/evolution_cases.json` 的前 20 例（冻结 split，非重抽）
- run id：`v2_pilot20_10w`，输出目录：`runs_v2/v2_pilot20_10w`

## 1. 总体指标

| 指标 | detector_v2（20 例） |
|---|---|
| completion | 20/20 = 100% |
| accuracy（decisive = all-case） | 65.0% |
| TP / FP / FN / TN | 5 / 2 / 5 / 8 |
| positive precision / recall / F1 | 71.4% / 50.0% / 58.8% |
| macro precision / recall / F1 | 66.5% / 65.0% / 64.2% |
| step localization（gold-positive 中命中） | 2/10 = 20.0% |
| unresolved | 0 |

成本：总 token 174,523（平均 8,726/case），34 次 provider attempt，墙钟约 3.8 分钟（10 并发）。

## 2. 与旧检测器同 20 例对比

| 指标 | detector_v2 | 旧检测器（decisive） | 旧检测器（unresolved 计错） |
|---|---|---|---|
| completion | 100% | 95%（BFCL/158 unresolved） | 100% |
| accuracy | 65.0% | 68.4% | 65.0% |
| TP | 5 | 8 | 8 |
| FP | 2 | 5 | 5 |
| FN | 5 | 1 | 2 |
| TN | 8 | 5 | 5 |
| positive recall | 50.0% | 88.9% | 80.0% |
| macro-F1 | 64.2% | 67.6% | 64.2% |
| step localization | 20.0% | 30.0% | — |

官方 AgentHallu 全量标准 prompt：GPT-5 macro-F1 70.2 / acc 70.6 / recall 73.2；本实验只 20 例，不能直接等价比较。

## 3. 逐案例表

| case | gold | V2 | old | judge | defense | resp/gold step | authority |
|---|---|---|---|---|---|---|---|
| BFCL/001 | False | True | True | True | substantiated | 7/— | authoritative |
| BFCL/006 | True | False | True | False | — | —/6 | authoritative |
| BFCL/010 | False | False | True | False | — | —/— | authoritative |
| BFCL/013 | False | False | True | False | — | —/— | authoritative |
| BFCL/024 | True | False | True | False | — | —/5 | authoritative |
| BFCL/025 | True | False | True | False | — | —/3 | authoritative |
| BFCL/051 | True | True | True | True | substantiated | 4/3 | authoritative |
| BFCL/055 | False | False | False | False | — | —/— | authoritative |
| BFCL/081 | True | True | True | True | substantiated | 8/7 | authoritative |
| BFCL/111 | True | True | True | True | substantiated | 4/7 | authoritative |
| BFCL/117 | True | True | True | True | substantiated | 8/8 | authoritative |
| BFCL/147 | False | True | True | True | inconclusive | 4/— | degraded_judge_only |
| BFCL/158 | True | False | unresolved | False | — | —/11 | authoritative |
| Camel/011 | True | True | True | True | substantiated | 3/3 | authoritative |
| Camel/038 | True | False | False | False | — | —/8 | authoritative |
| Camel/041 | False | False | True | False | — | —/— | authoritative |
| Camel/077 | False | False | False | False | — | —/— | authoritative |
| Camel/079 | False | False | False | False | — | —/— | authoritative |
| Camel/081 | False | False | False | False | — | —/— | authoritative |
| Camel/082 | False | False | False | False | — | —/— | authoritative |

## 4. 翻转账本（V2 vs 旧检测器）

V2 修复的旧 FP（3）：
- BFCL/010、BFCL/013、Camel/041

V2 新增/暴露的 FN（相对旧 decisive 的 4）：
- BFCL/006、BFCL/024、BFCL/025、BFCL/158（旧为 unresolved，现判 clean）

仍是 FP（2）：
- BFCL/001（defense 确认了与旧版相同的“报告子串”式定罪）
- BFCL/147（judge 阳性，defense 未翻案，降级保留）

仍是 FN（1）：
- Camel/038

## 5. 初步结论（机制层面，不是调参结论）

1. **completion 问题解决**：20/20 decisive，unresolved=0；新状态机没有把 Defense 故障变成 FN。
2. **FP 从 5 降到 2**：Judge-first + Defense 只翻案的结构按预期降低了误报。
3. **recall 成为当前短板**：V2 的 FN 全部来自 Judge=false（Defense 从未运行），其中 3 例旧检测器能召回。Judge 的假阴性率决定 recall 上界，需要下一步独立改进 Judge 召回，而不是给 Defense 增加权力。
4. **Step localization 20%，低于旧版 30%**：Judge 对最早因果步的定位能力不足（051、081、111 等均偏移），这是下一优先修复点。
5. **BFCL/147 的 defense inconclusive** 被正确降级保留，行为符合设计，但仍暴露 FP；不能靠 Defense 兜底，Judge 语义仍需收紧。
6. 成本：平均 8.7K token/case，20 例约 3.8 分钟，10 并发可扩展。

## 6. 下一步候选（不在本轮实验内调）

- Judge 提示只允许定义级修改（例如增加“报告抽取不等于事实捏造”的通用排除项），不能用这 20 例的 case id 特判；
- 为 Judge=false 增加可选 recall 保险（仅当 judge 低置信度时），但需要新的置信度输出字段；
- 冻结验证集后重测；
- 保持本目录独立，旧框架不参与。
