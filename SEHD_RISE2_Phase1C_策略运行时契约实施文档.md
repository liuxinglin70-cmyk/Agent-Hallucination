# SEHD-RISE2 Phase 1-C 交付文档 — 策略运行时契约 + 证书绑定（Policy Runtime Contract + Certificate Binding）

状态：**已实施，待人工复核**（2026-08-15）。本文档记录评审最终裁决的落地
（P1 弃权、零已知回归原则）与 P1-C 三个子任务（C1 契约冻结 / C2 绑定契约 +
运行时 binder / C3 运行时绑定重放对比）。

评审定位：P1-B 证明的是「已有证据链 + oracle 绑定条件下，L2 决策边界可学习
可改善」；尚未证明「可部署的 L2 genome 在新案例上自动获得同样收益」。
当前最大缺口从 *policy 对不对* 转为 **系统能否在完全 label-free、无人工绑定
的情况下识别"什么时候该触发"**——P1-C 即为此而设。

---

# 1. 评审裁决落地（先行数据变更）

## 1.1 P1 弃权：delivery_form 类 + NS-1 发现

- 证书类词表新增第 7 类 `delivery_form`（**不挂任何策略**）：答案已提取、
  会话以 terminate(success) 收尾而不经预期渠道交付——abstain 类；
- `policy_candidates.json` 新增 `non_separable_findings`：
  **NS-1**（证据 = OpenManus/048 gold-clean vs 067/072 gold-error，
  证书层完全同构、当前证据表示不可分；处置 = 策略弃权，048 保留为已知
  FP 面，分离需要新的证据能力（L1 方向），而非更细的规则）；
- P1 触发定义剔除 delivery-channel；048 从 P1 消减目标移入 NS-1；
- 三张同构证书（048 c1 / 067 c1 / 072 c2）重绑 `delivery_form`；
- 校验器新增 4 项发现检查（案例存在、≥2 案例、**必须横跨两种 gold 极性**
  ——不可分性的定义性证据、状态词表）；FP 覆盖检查现在接受
  「策略消减 ∪ 显式未处理 ∪ 弃权发现」。

## 1.2 零已知回归原则下的 oracle 重放（P1-B 复核后数字）

| 配置 | TP | FP | FN | 准确率 | TP 损失 |
|---|---|---|---|---|---|
| baseline | 50 | 12 | 12 | 76.0% | 0 |
| L2-P1（精化后） | 50 | 9 | 12 | 79.0% | 0 |
| L2-P2/P3/P4/P5 | 50 | 10-11 | 12 | 77-78% | 0 |
| **全集（含精化 P1）** | **50** | **1** | **12** | **87.0%** | **0** |

弃权后全集零 TP 损失、零 FN 回归：FP 12→1（048 为弃权保留面）。
**全部 32 配置仍零 must-survive 违例。**措辞遵守评审：P2-P5（与精化 P1）
通过的是 **oracle replay gate**，不是 promotion——promotion 需要
fresh 未见案例（P1-D）。

# 2. P1-C1：Policy Runtime Contract（冻结）

`policy.py::build_policy_genome` 把过门策略冻结为检测器
`load_policy_genome` 可直接加载的 genome（`sehd2.policy_genome.v1`）：

```
PolicyItem
├── policy_id / version
├── target_certificate_class     （封闭词表）
├── precondition                 （trigger 定义）
├── exclusions                   （must_not_match）
├── action                       （封闭动作词表，仅两个：
│                                  withhold_conviction /
│                                  require_corroborating_content_error）
├── risk_boundary                （statement + do_not_apply）
└── provenance                   （derived_from / gap names /
                                   failure cases / 候选集状态）
```

- **动作词表保持两个**，绝不允许改写裁决——L2 不会变成第二个 court；
- content_hash 与检测器侧 `skills.load_genome_payload` 逐位同构
  （canonical JSON：sorted keys / compact separators / utf-8），
  进化侧构建的 genome 无适配层即可被冻结检测器加载（有跨层测试证明）；
- 交付 genome：`datasets/policy_candidates/v1/policy_genome.json`
  = **P2+P3+P4+P5**（评审指定的主候选）。
  精化 P1 现在同样零损失（全集 87%），是否并入 fresh 评估集
  `--include L2-P1,...` 即可——留给 P1-D 编制复核；
- 检测器基座零改动：`api.detect(policy_genome=...)` 原有接口 +
  provenance 记录即部署面；P1-D 的 fresh 流程走
  检测 → 运行时绑定 → 进化侧重放裁决，不需要改 court。

# 3. P1-C2：Certificate Binding Contract + 运行时 binder

`sehd_rise2/evolution/binder.py`——L2 的感知阶段：

- **输入契约（封闭 allowlist，8 字段）**：candidate claim、certificate
  verdict+rationale、evidence_refs、defense outcome、packet 事件投影
  （引用事件 + locus，内容截断 1200 字符）；
- **label-free 机械强制**：`assert_label_free` 递归扫描禁键
  （gold / hallucination / label / failure / prediction / decision /
  capability / gap / expected）+ 顶层 allowlist + 文本级 gold 令牌扫描，
  三重保证 binder 输入绝无结局信息（评审第八节）；
- **unclassified 永远可选**：binder 不确定 → 策略不触发 → baseline
  行为（fail-closed 感知，与检测器权威设计同构）；词表外答案是硬错误，
  绝不静默降级；
- **串行调用 + 增量落盘 + 断点续跑**（遵守 census 超时教训：不并发）；
  路由复用检测器 verification route（deepseek-v4-flash）；
- 输出 `runtime_bindings.json`（`sehd2.certificate_bindings.runtime.v1`，
  每条含 `binding_status ∈ {classified, unclassified}`）。

# 4. P1-C3：运行时绑定重放对比

`replay.py` 新增 `compare` 命令与 `compare_binding_layers`：

- oracle 绑定（严格校验）与 runtime 绑定（允许 unclassified）分别重放
  全部 32 配置；
- 对比指标（评审第九节清单）：
  **binding agreement / unclassified rate / 逐证书分歧清单 /
  逐配置 accuracy·FP·FN·TP-lost 双侧对照（decision delta）**；
- 产物：`runtime_replay_result.json` + `RUNTIME_REPLAY_REPORT.md`；
- 判读标准（评审）：runtime 绑定下的重放若仍接近 oracle 87%/83%，
  L2 工程闭环成立；差距即感知阶段的量化缺口。

## 4.1 实测结果（deepseek-v4-flash，按案例批量，串行 62 调用）

| 配置 | oracle → runtime 准确率 | FP | FN | TP 损失 |
|---|---|---|---|---|
| baseline | 76.0% → 76.0% | 12 → 12 | 12 → 12 | 0 |
| L2-P1 | 79.0% → 78.0% | 9 → 10 | 12 → 12 | 0 |
| L2-P2 | 78.0% → 76.0% | 10 → 12 | 12 → 12 | 0 |
| L2-P4 | 78.0% → 75.0% | 10 → 12 | 12 → **13** | 0 → **1** |
| **全集** | **87.0% → 77.0%** | **1 → 10** | 12 → 13 | 0 → 1 |

- **绑定一致率 84.0%**（26/162 分歧），**unclassified = 0**——
  binder 从不行使弃权权，双向过度承诺；
- 方向一（12 处）：oracle 判策略类、runtime 判 content——恰好集中在
  全部难例 FP 证书（114 近阈值、127 渲染、142 缺席、061/082 前提、
  064 转述、014 评价、028/065 部分）→ **oracle 的 11 例 FP 修复
  runtime 只回收 2 例**（016/066 两个最典型 requirement_compliance）；
- 方向二（11 处）：oracle 判 content、runtime 判 premise_conflict——
  P4 语义危险的实证（评审预判成立）：过度泛化到 gold-error 证书，
  导致 Octotools/022 唯一证书被错误开释（唯一 TP 损失）；
- 正面信号：binder **独立复现了 NS-1 弃权簇**（048/067 c1 →
  delivery_form，未触发任何策略）——类定义在无人工参与的判定中
  仍然成立的初步证据。

## 4.2 结论：工程闭环尚未成立，感知是当前瓶颈

评审判读标准的答案是否定的：runtime 77% 远离 oracle 87%。但 P1-C3
的职责正是把这个缺口量化——语义（policy semantics）已验证，感知
（policy perception）成为下一个能力目标，且失败模式明确：

1. **不弃权**：unclassified 设计存在但 0 使用——binder 需要校准
   （如置信要求、逐类反例对照、或两次独立判定不一致即弃权）；
2. **难例系统性偏向 content**：需要更强的类定义呈现（判别式对照
   而非描述式定义）或证据投影增强；
3. **premise_conflict 过宽**：P4 的运行时边界必须收紧到
   "复述已确立前提"，与评审第三节的警告一致。

这三项是 P1-D 之前的 binder 迭代清单；genome（P2-P5）本身不变。

# 5. 产物与验证

| 文件 | 职责 |
|---|---|
| `sehd_rise2/evolution/policy.py` | +delivery_form 类、发现校验、genome 构建器、`genome` CLI |
| `sehd_rise2/evolution/binder.py` | 运行时绑定器（C2 全部契约）+ `bind` CLI |
| `sehd_rise2/evolution/replay.py` | +unclassified 语义、`compare` CLI、对比指标 |
| `datasets/policy_candidates/v1/policy_genome.json` | P2-P5 冻结 genome |
| `datasets/policy_candidates/v1/runtime_bindings.json` | 运行时绑定（machine_generated） |
| `tests/test_runtime_binder.py` + 扩展 | 107 tests 全绿（含跨层 genome 加载） |

# 6. 下一步：P1-D fresh unseen evaluation（评审第十、十一节）

1. 从 AgentHallu 剩余样本固定 **fresh evaluation split**（未参与
   Phase 0/1-A/1-B/1-C 任何环节）+ 定向 canary 集（P2 表达增强实为新增
   事实 / P3 paraphrase 实为精度改变 / P4 用户错误前提被盲从 /
   P5 缺席实为全程无据）；
2. 流程：新轨迹 → 冻结检测器 → 运行时 binder → policy genome →
   进化侧裁决 → evaluator join gold；
3. 晋升门：fresh 集上零已知回归 + 净改善；此处通过才允许
   `promoted` 状态与真实部署叙事。
