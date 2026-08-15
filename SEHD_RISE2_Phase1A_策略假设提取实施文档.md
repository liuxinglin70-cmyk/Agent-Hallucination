# SEHD-RISE2 Phase 1-A 交付文档 — L2 策略假设提取（Policy Hypothesis Extraction）

状态：**已实施，待人工复核**（2026-08-15，`review_status: first_pass`）。
本文档记录 Phase 1-A 的裁决依据、产物、五条候选策略的机制聚类过程、
关键设计契约与 P1-B 入口条件。

路线定位（沿用外部评审结论）：Phase 0 失败理解层收尾后，Phase 1 选择
**L2 决策边界进化**（12 FP 全部 policy 类，且可在 100 例 run 树上离线重放）。
Phase 1 拆为四步：**P1-A 策略假设提取**（本文档）→ P1-B 离线重放 →
P1-C Policy Genome Contract → P1-D 真实进化实验。

---

# 1. 任务定义（评审第七、十一节的落地）

P1-A 把 Phase 0 的 12 个 policy 类 FP 变成第一批 **L2 Policy Candidate Set**，
严格保持三层结构：

```
Failure Case（learning case，Phase 0 产物）
    ↓ 逐案例机制归因（锚定 upheld 证书）
Policy Hypothesis（机制层假设，≥2 案例共同机制）
    ↓ 结构化为可重放的判定契约
Candidate Policy Item（trigger / action / expected effect / risk boundary）
```

评审的两条红线及其落地方式：

| 红线 | 落地 |
|---|---|
| 禁止 `one failure → one skill` | 校验器强制每条策略 `case_evidence ≥ 2` 个不同案例；五条策略分别聚合 6/3/2/2/2 条证书证据 |
| 禁止案例级规则化（`if output contains X: ignore`） | trigger 是**证书类**（closed vocabulary）而非文本模式；校验器强制 trigger 定义中不得出现任何 case id（`trigger_is_class_level`） |

# 2. 产物清单

| 文件 | 职责 |
|---|---|
| `sehd_rise2/evolution/policy.py` | 证书类/动作/状态三个封闭词表（单一真源）+ 候选集校验器（17 项检查）+ `validate`/`report` CLI |
| `datasets/policy_candidates/v1/policy_candidates.json` | 第一批 L2 策略候选集（5 条，`first_pass`，进版本控制） |
| `datasets/policy_candidates/v1/REPORT.md` | 复核面报告（report 命令重生成，含 12/12 覆盖矩阵） |
| `tests/test_policy_candidates.py` | 18 项测试：真实数据集全绿 + 每条校验分支的合成反例 |

全套 `pytest tests/` = **84 passed**（Phase 0 基线 66 + 18）；
`check_layers.py` 绿；候选集 `validate` 17/17 绿。

# 3. 机制聚类：从 gap 分类到 policy 机制

Phase 0 的 gap name 是**分类**（strictness_miscalibration 8 / expression_tolerance 3 /
evidence_weighting 1）；P1-A 的工作是把它们重聚类为**机制**——五条策略全部
横切 gap name，证明聚类不是分类的换名：

| 策略 | 证书类 | 动作 | 聚合案例（FP 消减） | 跨 gap name |
|---|---|---|---|---|
| L2-P1 建议性要求不承担定罪 | requirement_compliance | require_corroborating_content_error | 016/065/066/048/028（5） | strictness |
| L2-P2 表现层添加不是捏造 | presentational_addition | withhold_conviction | 014/127（2） | strictness + expression |
| L2-P3 散文重述粒度不是误报 | expression_variance | withhold_conviction | 114/064（2） | expression |
| L2-P4 任务/用户前提优先于外部纠错 | premise_conflict | withhold_conviction | 061/082（2） | strictness + evidence_weighting |
| L2-P5 单切片缺席不定罪 | absence_based | withhold_conviction | 142/028（2） | strictness |

并集覆盖 12/12 FP；上限（全部触发且零回归）= FP 12→0，决定性准确率
76%→88%。**这是上限不是承诺**——实际收益取决于 P1-B 中证书类绑定的精度。

# 4. 关键设计契约

## 4.1 证书类词表：定罪依据的类型学

六个类构成对「幸存证书为什么能定罪」的封闭类型学
（`policy.py::CERTIFICATE_CLASSES`，代码治理，扩展须代码审查 + 数据集证据，
与冻结 gap taxonomy 同款治理）：

- `content_contradiction`——基线类，不挂任何策略，独立定罪；
- 其余五类一一对应五条策略的 trigger。

类定义吸收了 TP/FN 两侧的反例证据（见 §4.3），这是 P1-B 绑定阶段的
操作语义，不是标签。

## 4.2 动作词表与 Camel/039 反例

动作只有两个：

- `withhold_conviction`——匹配证书不能定罪；
- `require_corroborating_content_error`——匹配证书不能作为**唯一**定罪依据，
  同案例若有幸存的 content_contradiction 证书仍然定罪。

第二个动作由真实反例驱动：Camel/039（TP）的 c1（"问用户而不是按要求检索"）
与 Magentic_One/065（FP）的 c2 表面形态几乎相同，但 039 还有内容误报证书 c3。
若 L2-P1 用无条件 withhold，039 会被错误开释——corroboration 要求保住它。
这是「证据解释边界调整」与「案例级规则化」的分界实例。

## 4.3 FN/TP 风险显性化（每条策略强制引用反例证据）

校验器强制：`fn_risk` 非空、`risk_boundary.do_not_apply ≥ 1`、
`tp_must_survive` 引用的案例必须是 gold=true 且当前已定罪（`tp_examples_convicted`）。
五条策略的边界各自由具体反例锚定：

| 策略 | FN/TP 侧反例（不得开释） |
|---|---|
| L2-P1 | BFCL/027（TP，Missing Required Call 定罪必须存活）；BFCL/067/071（FN，未来必须可检测） |
| L2-P2 | BFCL/104（gold 错误 = 无根据自评句）；OpenManus/060（捏造校名）；OpenDeepSearch/007（指称替换不是渲染） |
| L2-P3 | BFCL/025（TP，工具参数精确匹配存活）；BFCL/159（捏造参数值） |
| L2-P4 | Camel/053（FN 镜像：采纳用户错误建议本身就是 gold 错误）；Camel/059/060 |
| L2-P5 | OpenManus/060（全程无据的具体会；缺席豁免不保护）；BFCL/104 |

## 4.4 覆盖完整性

`fp_coverage_complete`：全部 12 个 policy 类 FP 必须被至少一条策略声明为
消减目标，或显式列入 `unaddressed_cases` 并给出理由（本批 12/12 全覆盖，
无 unaddressed）。

## 4.5 防火墙延续

候选集是 evaluator 侧产物（引用 learning case 与 gold 结局），与 Learning Case
同一条防火墙：不得进入检测器输入路径。P1-B 的重放引擎消费 run 树 +
证书类绑定 + 候选集，同样不触碰检测器运行时。

# 5. 不做的事（scope discipline）

- **不实现重放引擎**——那是 P1-B；本阶段只交付其输入契约。
- **不生成 policy genome**——`load_policy_genome` 的 items 格式在 P1-C
  才与检测器侧对接冻结；候选集 ≠ 基因组（未过重放与晋升门）。
- **不改 court / 不写 prompt**——L2 调整的是裁决解释，不是证据生产；
  检测器基座保持冻结。

# 6. P1-B 入口条件（复核通过后）

1. 证书类绑定：对 100 例 run 树中每张幸存证书给出类绑定 + 审计记录
   （绑定必须能引用 §4.1 的类定义逐条判定）；
2. 确定性重放：单策略与整集两种条件下重放裁决，零 provider 成本；
3. 报告四项指标：FP 消减、FN 回归、TP 存活（含 `tp_must_survive` 清单逐项核对）、
   决策稳定性（原正确案例被改变数）；
4. 晋升门（评审第九节三条件的操作化）：净准确率提升、无超出声明风险边界的
   FN 回归、在非派生案例上仍有效（防 dataset fitting）。

# 7. 复核请求（使用者裁决项）

1. 五条策略的机制聚类与类边界措辞（尤其 L2-P1 的 requirement_compliance
   是否应拆分、L2-P4 与 L2-P5 的边界是否够窄）；
2. `tp_must_survive` 清单是否需要扩充（当前每策略 1 例，P1-B 将全量核对）；
3. 复核通过后 `review_status` 置 `human_reviewed`，进入 P1-B。
