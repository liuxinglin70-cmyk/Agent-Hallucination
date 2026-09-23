# 真实运行 1（2026-09-23）：开发阶段接入后的首次真实 cycle

**结论：这一轮跑完并给出了三个真实读数，但没有测到开发阶段本身。** 开发循环启用了，然而它
**没有任何可开发的机制**（巡逻没诊断出稳定的失败族），于是本轮退回单轮起草。更重要的是，
这一轮暴露了一个此前没人知道的运行属性：**缺陷注入审计与巡逻共用同一个墙钟预算，而审计单独
吃掉了其中 74%**，导致 16 例巡逻里 10 例 fail-closed 成 `unresolved`——**这一轮的巡逻读数因此
不是检测能力的读数**。原始产物：[run1_archive/](run1_archive/)。

## 运行身份

| 项 | 值 |
|---|---|
| 运行目录 | `artifacts/selfevo-real-dev-20260923/` |
| 配置 | `evolution/configs/selfevo_real_dev_20260923.json`，SHA `c55a8aa263f2a6ba` |
| 源码摘要（启动前冻结） | `9c27b514b1613c5c`，git head `09cee47` |
| 检测配置摘要 | `da3131e671c5d981`（`detector/configs/autodl.json`） |
| 模型 / 端点 | `GLM-5.3-flash` / `https://www.autodl.art/api/v1` |
| 并发 | 8 workers，**全程只有 1 个 cycle 进程**（启动后即刻核对过） |
| 开发配置 | `enabled=true`、每轮机制上限 1、每机制版本上限 2、L1 上限 40 例评估 |
| 划分 | mini、种子 20260923：sentinel 6（3/3）、gain 6（4 error / 2 clean）、patrol_pool 18 |
| holdout | `final_size=0`、`final_reads=0`——**未创建也未读取** |
| 墙钟 | 总计 **197.4 分钟**（审计 89.4 + 巡逻 108.0） |
| 账本 | 10 条，`cycle_completed`，终态 `completed` |

源码摘要在运行期间发生了漂移（我在运行中提交了修复）。**如实记录并逐条说明为何无害**，
见 [SOURCE_DRIFT_DURING_RUN.md](SOURCE_DRIFT_DURING_RUN.md)：
`evolution/development.py` 与四门判据全程未改，被改的恢复分支在单轮运行中不可达，
且"是否可达"可由报告里有无 `recovered` 字段直接证伪。

## 读数

机器可读：[readings.json](readings.json)（只读提取，由 [extract_readings.py](extract_readings.py) 生成）。

### 1. 开发阶段：启用了，但没有对象（**本轮最重要的读数**）

```json
{"round_path": "development",
 "targets": 0, "candidate_count": 0,
 "reason": "no mechanism was eligible for development this cycle"}
```

**开发循环没有测到**：不是它被跳过，而是这一轮的巡逻没有产出任何稳定失败族，它没有可开发
的机制。所以本次运行**不能**回答"开发阶段在真实数据上是否有效"。

### 2. `round_path` 报了 `development`，但本轮实际是单轮起草——**这是我造的仪器缺陷**

同一份报告里：`proposal_summary.empty_proposal_reason` 说"没有稳定失败族"，账本里
**没有** `development` 事件，候选数 0。也就是说候选人来自 proposer 的单轮起草，而
`round_path` 却说 `development`。

根因：我把 `round_path` 判成"是否存在开发记录"，而正确的问题是"**开发循环的批次是否被采纳**"。
一个轮次可以启用循环、发现无可开发机制、然后回退到单轮起草——它有一份"为何什么都没开发"的
记录，但到达门的候选来自 proposer。

**已修复**（commit `0ae0d0d`）：引入 `development_used`，只在循环的批次被采纳时为真，并随
检查点持久化使 resume 后不翻转。修复后**账本与报告同源**：账本有 `development` 事件 ⇔ 报告说
`development`。回归测试断言这一等价关系。

**这条读数值得单独记**：它是"离线仪器实验七条主张全部成立"之后，被真实运行推翻的一条。
离线实验的问法（"循环开 + 候选修复/不修复"）覆盖不到"启用但无可开发机制"这一支。

### 3. 审计与巡逻共用墙钟，审计吃掉 74%（此前未知的运行属性）

| 阶段 | 起始 | 耗时 |
|---|---|---|
| cycle_started | +0.0 min | — |
| **audit** | **+89.4 min** | **89.4 min** |
| **patrol** | +197.4 min | **108.0 min** |

配置的 `cycle_timeout_seconds` 是 **7200 秒（120 分钟）**，实际用了 **197.4 分钟**。
`_ArmRunner` 的 deadline **按批生效**：它只在"这个案例还没开始"时跳过，已经在跑的不受影响。
审计 12 例新鲜评估（sentinel+gain）跑掉 89 分钟；巡逻随后开始，于是：

| 批 | 例数 | 结果 |
|---|---|---|
| 初始巡逻 | 8 | 全部真跑，但**两例各自吃掉约 100 分钟**（`Camel/018` 101 min、`Magentic_One/050` 108 min） |
| **bootstrap 补抽** | 8 | **8/8 全部 `batch_deadline_exceeded`、0.0 秒**——一批都没开始 |

巡逻混淆矩阵：`tp=3, tn=2, fn=0, fp=1, unresolved=10`。10 例未决的构成：
**8 例是上面那批被整体跳过的 bootstrap**，另 2 例是 `recall_unavailable` 与 `provider_error`
各 1。报告自己的 `bootstrap.family_found=false` 因此**不表示"补抽没找到家族"**，
而表示"补抽根本没测"——这两个读数的语义差别，正是靠逐例的
`elapsed_seconds=0.0` 与 `authority_failure_reasons=[batch_deadline_exceeded]` 区分出来的。

**这不是开发阶段的缺陷，也不是检测器质量读数**：10 例未决是基础设施耗尽墙钟的结果。
**此前不知道这一点**：2026-09-21 的晋升运行只报过单例长尾（`BFCL/032` 约 50 分钟），
没有报过"审计先耗尽共享预算、随后整批 fail-closed"这条链，也没有量化过审计的开销。

### 4. 提案与四门：本轮无候选

`proposals=[]`、`candidates_reaching_the_gates=0`、`admitted=[]`、`active_after_cycle=[]`。
按设计：没有诊断出机制就没有提案对象。

### 5. 存储

`route_failures.jsonl`、`gaps.jsonl`、`memory/episodic.jsonl`、`candidates/`、`components/`
**全部不存在**，`development_runs=[]`。其中：

- `route_failures` / `gaps`：本轮无可写内容（无提案、无路由，所以没有反证与缺口）——**这两处
  的真实写入因此仍未验证**，与 §26.5 记录的是同一类风险，本轮没有消除。
- `memory/episodic.jsonl` 不存在：因为**本轮没有拒绝**（没有候选被门拒过）。上一轮同位置的
  运行有 1 条记录，正是"拒绝被写入记忆"的对照。
- `candidates/` 不存在：`cycle_end` 已接线，但本轮 `candidate_count=0`，没有开发 run 目录
  可归档——**这与 §30 那次"目录不存在"的成因不同**（那次是未接线，这次是无可归档内容）。
  区分这两者要靠报告里的 `development.runs=[]`，不能只看目录。

## 这一轮证明了什么、没证明什么

**证明了**：
1. 开发阶段真的会被调用，且在"无可开发机制"时**如实报告**而不是伪造一次开发；
2. 启用循环不会让轮次变成"永远没有候选"——它按设计退回单轮起草；
3. 报告与账本可以交叉核对路径声明（修复后）。

**没有证明**：
1. **开发阶段在真实数据上有效**——没有任何机制被开发过；
2. **检测/定位能力**——10/16 巡逻案例是墙钟耗尽的未决，不是判定；
3. 自进化成功（0 准入）、泛化（无 holdout）、跨代继承（单轮且无准入）；
4. `route_failures`/`gaps`/存档的运行期写入（本轮无可写内容）。

## 这一轮暴露的自身缺陷（全部已修，各配回归测试）

| # | 缺陷 | 发现方式 | 修复 |
|---|---|---|---|
| 1 | `round_path` 把"有开发记录"当成"走了开发路径" | **本轮真实运行** | commit `0ae0d0d`，`development_used` |
| 2 | 抽取器把 ledger 的 `timestamp` 读成 `at`，丢掉 197 分钟的时长结构 | 写报告的核对 | 同 commit |
| 3 | 抽取器把巡逻混淆矩阵读错路径，把 `unresolved=10` 显示成 `null` | 写报告的核对 | 同 commit |
| 4 | 抽取器把 `errors`（计数）当成列表迭代，直接崩溃 | 运行抽取器 | 同 commit |

第 2–4 条同属一类：**读数脚本写错字段名，就把"墙钟耗尽导致 10/16 未决"这件大事藏起来**。
这正是"没有读数的仪器等于没有仪器"的又一例，也是我在报告阶段必须逐个核对字段类型的原因。

## 下一步：运行 2

运行 1 没能测到开发阶段，且其巡逻读数被墙钟污染。运行 2（`selfevo_real_dev2_20260923`，
已冻结 `prerun_state_run2.json`，源码摘要 `97bd8580208abd7b` / git `0ae0d0d`）只改一处：
`cycle_timeout_seconds` 7200 → **14400**。

- **这是基础设施预算，不是门限**：`gates` 整块逐字未改（已核对并记录在 prerun_state 里）；
- 不做这一步，审计会再次吃掉大半预算，巡逻再次 fail-closed，**测不到开发阶段**；
- 运行 2 必须重付一次审计（源码变了，其缓存指纹含 `source_digest`，因此**正确地失效**）。
  直接复制运行 1 的审计缓存会让报告声称"对这份源码跑过审计"——**不做这件事**。

## 复现

```bash
python reports/real_dev_20260923/extract_readings.py artifacts/selfevo-real-dev-20260923
python reports/real_dev_20260923/freeze_prerun.py      # 仅用于冻结，运行 1 已冻结过
```
