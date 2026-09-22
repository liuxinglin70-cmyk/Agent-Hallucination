# P0.4：机制对齐的机会判定与晋升清单（2026-09-22）

对应[优化方案](../../agent_hallucination_self_evolution_optimization_plan.md) §23.3 P0.4 与 §8。把"这批数据里有多少属于本机制的可修复错误"变成晋升前就能回答的问题，并让"未晋升"有机器可读的具体原因。

复现：`python reports/promotion_alignment_20260922/run_promotion_alignment.py`

## 1. 数据与口径

| 项 | 值 |
|---|---|
| 两臂 | 脚本化（scripted）离线替身，无 provider 调用 |
| 案例 | 8 例合成案例：收益 2 / 保护 4 / 挑战 2 |
| 证据等级 | **diagnostic-only**：演示晋升规则，不是检测能力读数 |
| 说明 | 真实晋升仍需真实服务运行与独立冻结的协议；本报告不替代它 |

## 2. 核心改动：三类集合 + 机制机会

v1 协议按极性在冻结批次上计分，有两个已记录的仪器缺陷（方案 §2.4 与 §5.2）：候选可以因修复别的机制的案例而得分；无关退化也可能与收益相互抵消。

`evolution/promotion_alignment.py`（新增）与 `freeze_protocol` 的 v2 分支把四样东西一起冻结——**机制、评测方给出的成员证据、候选内容哈希、基线 registry 哈希**，并按三类互斥集合计分：

| 集合 | 成员要求 | 作用 |
|---|---|---|
| **机制增益集** | 目标机制的 `confirmed` 成员，且评测方标注 `repairable=True` | 候选必须在此改善 |
| **保护集** | 控制臂已判对、需保持的案例（含定位正确例） | 任何退化即拒绝 |
| **机制外挑战集** | 显式 `external`（表面相似、机制不同） | 退化按自身理由拒绝 |

成员关系**只能由评测方绑定提供**：`mechanism_opportunity` 只按 case id 查表，从不从 case id 解析来源、也不按同极性推断成员。测试用改名不变性锁住了这一点。

## 3. 五种终态的实测读数

| 情形 | 结果 | 原因码 |
|---|---|---|
| 机制修好、保护与挑战均干净 | `promoted` | `promoted` |
| 增益集无可修机会 | `promotion_unmeasurable` | `no_mechanism_opportunity`，**只跑了 control 臂** |
| 同时修好机制但破坏一个保护案例 | `promotion_rejected` | `protection_regression` |
| 破坏一个机制外相似案例 | `promotion_rejected` | `challenge_regression`（类别 `mechanism_external_regression`） |
| 对同一冻结协议发起第二次尝试 | `promotion_blocked_preflight` | `protocol_invalid`：`protocol already consumed` |

第二行是方案要求的关键区分：**没有机会不等于候选失败**，且候选臂根本没被测量（`arms_measured == ["control"]`），所以不会花掉一次候选预算去得到一个注定不可测的结果。

第五行保证冻结证据不能被反复重掷到通过为止。

## 4. 冻结时即拒绝的构造缺陷

这些都在**花费任何测量之前**拒绝，各有测试：

| 缺陷 | 拒绝理由 |
|---|---|
| 收益案例不是目标机制的 confirmed 成员 | 机制不匹配 |
| `status="ambiguous"` 或证据为空 | 歧义不是正式评估案例 |
| 挑战案例其实是机制成员 | 挑战集必须机制外 |
| 开发案例（含机制成员与近邻）出现在正式批次 | candidate-unused 规则 |
| 任何成员条目缺失 | 必须逐例覆盖 |
| 放宽任一零容忍门（如 `harmlessness_new_fn_allowed`） | v2 不允许放松 |
| 定位协议缺 gold step 注解 | 无法计分 |
| 机制 id 与其修复签名不符 | 身份不一致 |

## 5. 运行前复核与运行中阻断

运行前重新核对：协议内容哈希、候选内容哈希与开发证据哈希、机制描述一致性、开发暴露集合、门与库策略一致性、源码与检测配置哈希、**每条轨迹内容摘要**、活动基线 registry 哈希、账本完整性。任一不符即 `promotion_blocked_preflight`，不做任何测量。

运行中：保护集退化、挑战集退化、定位破坏（`joint_broken`）、既有正确决策丢失，都直接拒绝且**不与收益相抵**（沿用 §5.2 禁止加权抵消）。四门在 v2 中全部强制，不可按阶段裁剪。

## 6. 一个实现层面的注意点

`candidate_from_registry_patch` 等旧入口产出的候选没有机制绑定。v2 运行遇到未绑定候选时报 `mechanism_alignment_missing`（提示先绑定再冻结），而**未绑定候选配 v1 协议的老路径逐字不变**——这是刻意的：cycle 里的候选没有评测方成员证据，凭空推断正是机制工作要禁止的事。新增 `candidate_from_development()` 用于把 L1 通过的冻结开发版本转成绑定候选，并汇总**所有**开发版本与近邻的案例暴露。

另外新增 `bind_candidate()`：只能在**冻结前**给候选补机制绑定，运行期不存在事后绑定路径。

## 7. 边界

- 本报告不含检测准确率、定位能力或泛化结论；脚本化替身只用于让五种终态都可复现。
- `evidence_scope` 在 v2 报告里是 `candidate_unused_development_data`，在 v1 报告里是 `legacy_polarity_evaluation`：两者证据等级不同，不可混读。
- 真正的泛化声明仍需独立 holdout（方案 §8.3）；本轮未创建。

## 8. 与 P0 收尾的关系

P0 四项（机制表示、能力规划与反证、开发循环、机制对齐机会与晋升清单）至此全部落地。检查点 A（首次真实运行）的入口条件已具备：机制、能力需求、开发 lineage 与机制对齐协议四者可以串起来。
