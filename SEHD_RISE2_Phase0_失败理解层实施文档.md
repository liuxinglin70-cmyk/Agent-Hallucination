# SEHD-RISE2 Phase 0 交付文档 — Failure Understanding Layer（失败理解层）

状态：**已实施 + 已收尾**（2026-08-15）。本文档是 Phase 0 的交付与交接记录：
审查反馈落地、产物清单、真实数据发现、最终复核与词表冻结（§6.1）。
遗留决策仅剩 Phase 1 立项（§6.2，使用者裁决）。
数据锚点：`runs/random100_20260815_142446`（100 例真实实验，
决定性准确率 76.0%，FN=12 / FP=12 / unresolved=0，步定位 25/50）。

---

# 1. 审查反馈落点（五项必改 → 全部落地）

| # | 审查要求 | 落地方式 |
|---|---|---|
| 1 | Phase 1 不固定 L1 Skill，改为 capability-driven | 诊断协议的 `recommended_evolution_target` 由词表类别机械映射（`gap_taxonomy.CATEGORY_EVOLUTION_TARGET`），优先级由 §4 的真实分布决定；本文档不再预设 L1 优先 |
| 2 | 增加 Failure Observation Layer（Fact → Hypothesis → Capability） | Learning Case 三个块：`failure_analysis`（事实，机械推导+可审计 override）、`failure_hypothesis`（人工假设）、`capability_analysis`（能力抽象，受词表约束） |
| 3 | `adversarial_excuse` 不属于 verification | 移为 `policy/adversarial_defense_overturn`；verification 保留 evidence_insufficient / external_knowledge_limit / exactness_boundary |
| 4 | 删除/重定义 confidence | 诊断协议 `sehd2.capability_diagnosis.v1` 无 confidence 字段，改携带 `diagnosis_source ∈ {mechanical, human, mixed}`（有回归测试断言字段不存在） |
| 5 | taxonomy 冻结时间后移 | `gap_taxonomy.TAXONOMY_FROZEN = False`：标注期允许 `status="proposed"` 的新词（发现通道）；冻结 = 填定词表 + 翻转开关，校验器随即拒绝一切词表外取值 |
| 补 | 执行顺序 D1 先行 | D1（defense rationale 落盘）最先落地（§5.4），既有 100 例不重跑 |

# 2. 产物清单

## 2.1 代码（检测器保持独立：进化侧零改动检测器运行时）

| 文件 | 职责 |
|---|---|
| `sehd_rise2/evolution/gap_taxonomy.py` | 能力缺口词表（单一真源）+ 冻结开关 + 类别→进化目标映射 |
| `sehd_rise2/evolution/diagnosis.py` | 机械失败分类（`classify_failure` / `derive_failure_stage` / `localization_facts`）+ 诊断协议校验 |
| `sehd_rise2/evolution/learning_case.py` | Learning Case schema 常量 + 三层校验器 + 标注文件信封校验 |
| `sehd_rise2/evolution/assemble.py` | CLI：`assemble` / `validate` / `report`（投影式装配，见 §5.2） |
| `sehd_rise2/detector/court.py` | 仅 D1 数据完整性改动（§5.4），判定语义零变化 |

测试：`tests/test_gap_taxonomy.py`、`tests/test_failure_stage.py`
（含 24 例机械分类回归夹具）、`tests/test_learning_case.py`。
全套 `pytest tests/` = **284 passed**；pristine-scan 对全部新产物 **0 命中**。

## 2.2 数据（`datasets/learning_cases/v1/`，进版本控制）

```
datasets/learning_cases/v1/
├── annotations.json   # 人工层单一真源：49 例（12 FN + 12 FP + 25 LOCALIZATION）
├── cases/*.json       # 100 个 Learning Case（投影，可随时重装配）
└── REPORT.md          # 能力缺口分布报告（report 命令重生成）
```

## 2.3 运行命令

```bash
python -m sehd_rise2.evolution.assemble assemble \
    --run runs/random100_20260815_142446 \
    --dataset datasets/raw/agenthallu/AgentHallu \
    --annotations datasets/learning_cases/v1/annotations.json \
    --out datasets/learning_cases/v1
python -m sehd_rise2.evolution.assemble validate --dir datasets/learning_cases/v1/cases
python -m sehd_rise2.evolution.assemble report \
    --dir datasets/learning_cases/v1/cases --out datasets/learning_cases/v1/REPORT.md
```

# 3. 标注结论（Phase 0 的核心产出）

标注者 = 标注 agent（`review_status: first_pass`），全部条目待人工复核；
每条假设都引用 run 树证据（`hypothesis_basis`），推翻机械结论处均留
`failed_stage_override.reason`（共 18 处）。

## 3.1 FN 12 例：机械阶段 ≠ 能力归因（审查第七条意见的实证）

机械推导给出 census=2 / verification=4 / defense=6；语义分析后
**census=6 / verification=4 / defense=2**（5 例 override）。原因：多数
defense/verification 丢失的根源是 **gold 错误从未成为正确 framed 的候选**
（如 BFCL/067/071：法庭纠缠"safety reasons"措辞，而缺失的
lockDoors/pressBrakePedal 调用从未被提议）。→ 机械层定位"丢在哪一环"，
语义层定位"为什么丢"，两层都必须保留。

## 3.2 FP 12 例：全部是 policy 类

12/12 FP 的能力归因落在 policy（判决边界过严）：strictness_miscalibration
8、expression_tolerance 3、evidence_weighting 1。错误进入链的环节：
verification 支持 11 例、defense 维持 1 例。典型形态：把输出格式建议、
过程性指令、评价性措辞、转述用户建议当硬性事实约束。

## 3.3 LOCALIZATION 25 例：根因在前提采纳，候选在显现处

22 例 locus_vs_manifestation + 3 例 tie_ambiguity。gold 根因高度集中在
1–3 步（接受错误用户前提 / 早期错误假设 / 首个工具调用参数错），而候选
locus 落在下游显现处；3 例 tie 是"gold 步候选已 upheld 但最早 locus 规则
选了别的步"。attribution 类**仅诊断**，不派生检测能力。

## 3.4 能力缺口总分布（49 例）

| 类别 | 数量 | 细目 |
|---|---|---|
| attribution（仅诊断） | 25 | locus_vs_manifestation 22、tie_ambiguity 3 |
| policy | 14 | strictness_miscalibration 8、expression_tolerance 3、adversarial_defense_overturn 2、evidence_weighting 1 |
| observation | 6 | unsupported_claim_grounding 4、missing_required_action 2 |
| verification | 4 | excuse_based_rejection、source_credibility_conflict、unverified_derivation、exactness_boundary 1 |

**Phase 1 优先级信号（capability-driven，供决策）：**
极性收益最大的是 **policy 侧（14 例 = 12 FP + 2 FN）→ L2 决策边界**；
observation 6 例 FN（annotations 里已备好 `missed_candidates` 作为 L1 直接
输入）；verification 4 例 → evidence skill；attribution 是测量层改进
（最早 locus 选择规则），不生成能力。

# 4. 交付验收对照

| 验收标准 | 结果 |
|---|---|
| 100 案例全部结构化 | 100 个 Learning Case，`validate` 全绿 |
| 24 极性失败覆盖 | 每例有机械阶段 + 三层标注；18 处 override 均有理由 |
| Taxonomy 稳定 | 词表为代码常量；标注期发现 3 个 proposed 新词（均 1 例证据）；冻结待人工复核后执行 |
| 可指导 skill | `missed_candidates`（6 例）+ Top gaps 分布 + 每细目代表案例与泛化判断就绪 |
| 附加：25 定位失败 | 全部有 attribution 标注 |
| 附加：机械/人工一致率 | FN 12 例中 5 例被语义推翻（41.7%推翻率）——这是 Meta Skill 第二阶段要压低的基线指标 |

# 5. 关键设计契约（交接必读）

## 5.1 防火墙

Learning Case 含 gold 标签，是 evaluator 侧产物：
`packet.validate_record`（`EVALUATOR_ONLY_FIELDS`）在检测入口拒绝任何
携带 gold 的记录；进化侧代码不 import 检测运行时，只读
`detector.result.prediction_state` 权威读取函数与 run 树文件。

## 5.2 投影单一真源

`cases/*.json` 是投影（同 `summary.json` 之于案例台账）：检测事实的真源
是 run 树，gold 真源是数据集原始文件，人工层真源是 `annotations.json`。
**修改标注 → 重跑 assemble，不要手改投影文件。**

## 5.3 离线/在线视图分离

Phase 0 协议是离线复盘协议（gold 对齐）；未来在线 Meta Skill 必须消费
label-free 视图（`input` + `detector_execution`），两者输出 schema 相同。

## 5.4 D1（检测器侧唯一改动）

`court.py`：`_parse_defense` 保留 rationale，`proof_adjudication.defense_decisions`
值从裸 verdict 改为 `{"verdict", "rationale"}`；`_parse_certificates` 与
defense 共享 `RATIONALE_MAX_CHARS = 2000`；`stage_audit` 新增
`census_second_pass` 与 `census_deterministic_augment`。判定语义零变化，
`tests/test_detection_contract.py` 6/6 通过。既有 100 例 run 树早于该改动
（无 defense rationale），标注中相关假设基于证书 rationale + 证据重构；
**下次跑批起自动携带**。

## 5.5 词表治理

新增细目 = 修改 `GAP_CATEGORIES` 常量（代码审查）+ 至少 1 例案例证据；
冻结 = 填定词表 + `TAXONOMY_FROZEN = True`，校验器随即拒绝词表外取值
（`gap_allowed` 是唯一门函数，含未知类别拒绝）。

# 6. 收尾记录与下一步

## 6.1 最终复核 + 词表冻结（2026-08-15，已完成）

- 18 处 override 逐条对照 run 树决定性事实复核：全部成立，无修正；
  3 个 proposed 细目（excuse_based_rejection / source_credibility_conflict /
  unverified_derivation）各覆盖独立机制（真实需求偏差被域外借口驳回 /
  矛盾来源被扁平化裁决 / 未重推导的 agent 推导），裁决**收录**并写入
  `GAP_CATEGORIES`；
- `annotations.json` 置 `review_status: human_reviewed`，3 个 proposed 状态
  改为 known；
- `gap_taxonomy.py`：`TAXONOMY_FROZEN = True`；`gap_allowed` 拒绝一切词表外
  取值（proposal 通道关闭）；
- 冻结后全量验证：`pytest tests/` 284 全绿；100 例 Learning Case 在冻结词表
  下重新装配校验全绿；`REPORT.md` 重生成（`taxonomy_frozen: True`）；
  pristine-scan 0 命中。

## 6.2 遗留决策（使用者的裁决，非工程待办）

1. **Phase 1 立项**：按 §3.4 分布在 policy(L2) / observation(L1) /
   verification(evidence skill) 中选定首个进化目标——数据支持 policy 优先
   （14 例 = 12 FP + 2 FN），observation 次之（6 例，`missed_candidates`
   已备好作为 L1 直接输入）；
2. Meta Skill 第二阶段以本数据集为训练/评测基准，机械/人工推翻率
   （当前 FN 5/12）为其能力指标；
3. 词表在下一批新案例上的稳定性复检（新数据不再通过 proposal 通道，只能
   通过代码审查扩展词表）。
