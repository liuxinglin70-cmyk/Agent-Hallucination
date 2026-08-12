# 阶段任务卡：SEHD-RISE Phase 8 — meta 策略层（Cross-Case Strategy Learner）

> 用法：本卡由用户在新会话手动粘贴启动（手动递推，不用定时任务）。本卡 + 仓库 = 全部所需上下文；
> 上一会话的结论不在对话里，而在 git 提交与计划文档实施记录中（§3.4 R5）。本卡是
> SEHD_RISE_Refactor_Execution_Plan.md §Phase 8 的渲染，与文档冲突时以文档为准并回写文档。

## 0. 前置校验（30 秒；任一失败即停，不实施、不生成下一阶段任务卡）
- [ ] `git log --oneline -10` 确认 Phase 0–7 已提交：5b88634、50fb9d7、0f144a9、f9b0883、f34495d、
      93b3334、fa04f51，且 Phase 7 提交为
      `refactor(sehd_rise): phase 7 — execution evidence chain and evaluation (effect/fitness/canary/gate/promotion)`
      （HEAD 为其后随卡提交或工作区干净）
- [ ] `git status` 干净（无未提交的 Phase 8 之前产物）
- [ ] `sehd_rise/meta/` 只有空白 `__init__.py`（Phase 0 骨架，本阶段从零生长）
- [ ] 计划文档含 Phase 2/3/4/5/6/7 实施记录小节（证明接续链正常、记录模式可循）
- [ ] 源文件行数与计划一致：`bccse/meta_skill.py`=303、`bccse/meta_review.py`=617
- [ ] 工作目录 = 仓库根目录；`python tools/parity/check_layers.py` 可运行且当前通过
      （Phase 7 终态：65 文件 / 13 层）
- [ ] 既有 6 个 harness 仍全绿：roundtrip_skills / compare_causal / compare_detection
      / compare_experience_typing / compare_planning / compare_effect（基线未回归）

## 1. 迁移映射（源文件只读，目标 sehd_rise/）

| 源（src/sehd_v6/core/bccse/，只读） | 目标 | 方式 |
|---|---|---|
| `bccse/meta_skill.py`（303 行） | `meta/strategy.py` | **重写（收窄）** |
| `bccse/meta_review.py`（617 行） | `meta/review.py` | 平移 + 接口重接 |

- meta/strategy.py：C7——状态/提交/回滚/持久化保留，但 MetaEdit 的可操作面收窄为：
  search prior、operation preference、mechanism ranking、retry strategy、candidate diversity；
  删除对 gate profile 的硬切换能力之外一切触及 owner/cut/repairability/hard gate 的通道
  （现状本就 boxed-in，本阶段把"只学策略不学真相"落成类型级约束）。
- meta/review.py：输入改为 `loop/ledger.py` 的 GateEvent 投影 + `experience/memory.py` 的
  已验证 RepairPlan outcome；输出仅为策略建议。

## 2. 防丢失清单（逐行勾销后方可提交）
- [ ] MetaEdit 可操作面收窄为闭词汇（search prior / operation preference / mechanism ranking /
      retry strategy / candidate diversity），闭词汇外操作全部拒绝（C7 类型级约束）
- [ ] Meta 不再触及 owner / cut / repairability / hard gate 的任何通道（"只学策略不学真相"）
- [ ] meta_review 输入重接：GateEvent 投影（loop/ledger.py）+ 已验证 RepairPlan outcome
      （experience/memory.py），不再读 raw detection / attribution
- [ ] meta_review 输出仅为策略建议，不产出 RepairPlan / edit / owner 裁决
- [ ] 状态/提交/回滚/持久化逻辑保留（MetaSkill 的运行时机制不收窄，只收窄可操作面）
- [ ] 从 meta_skill.py / meta_review.py 剥离的逻辑不得在旧 god object 留残渣副本（pristine：不留残渣）
- [ ] 删除项不得以任何别名/死代码残留于新包

## 3. 纪律约束（不可违反）
- 不修改 src/sehd_v6/ 任何文件；不分派 subagent；全程主会话直接执行。
- 包内一律绝对导入：meta 层
  `from sehd_rise.platform... / from sehd_rise.skills... / from sehd_rise.causal...
  / from sehd_rise.experience... / from sehd_rise.evaluation...`。
- 分层依赖（check_layers.py 为准）：meta → platform, causal, experience, evaluation；
  下方各层（platform/skills/detection/causal/experience/typing/planning/execution/evaluation）
  永不 import meta。
- 本阶段含**语义变更 C7（Meta 重定义）**：Meta Skill 重定义为 Cross-Case Strategy Learner，
  只产出 search prior / ranking / retry / diversity 策略；删除对 owner、cut、repairability、
  hard gate 的一切干预。凡故意偏离旧判定处必须能引用纲领条款（§2.8）。
- 移植即重命名：无 _old/_new、无版本号后缀、无兼容别名（pristine 上线初稿）。
- 注释只写 why；pristine-scan 信号词命中逐个收敛，保留误报必须写收敛记录（模式见 Phase 2/3/4/5/6/7）。

## 4. 验证（全绿才可提交）
1. 结构：`python tools/parity/check_layers.py`；全包 import 冒烟 `python -c "import sehd_rise"`
   （不只本阶段新增模块，§3.4 DoD）。
2. S1 维持证据：
   `grep -rn "from sehd_rise.meta" sehd_rise/experience sehd_rise/skills sehd_rise/causal
   sehd_rise/detection sehd_rise/platform sehd_rise/planning sehd_rise/typing sehd_rise/execution
   sehd_rise/evaluation` → 无命中。
3. MetaEdit 合法性枚举校验：闭词汇外操作全部拒绝（search prior / operation preference /
   mechanism ranking / retry strategy / candidate diversity 之外的 MetaEdit 操作 → reject）。
4. 同一 GateEvent 序列在新旧两包产生的候选建议可比（允许因 C7 收窄产生的差异，逐条注明，
   引用 §2.8）：新建 `tools/parity/compare_meta.py`（沿用 compare_causal/compare_planning/
   compare_effect 的同进程双包夹具驱动模式）。
5. 既有 6 个 harness 仍全绿（roundtrip_skills / compare_causal / compare_detection
   / compare_experience_typing / compare_planning / compare_effect），证明下层未回归。
6. 暖缓存回放不可用（§3.3 R2）→ 降级为离线契约校验（schema/序列化/确定性判定比对），
   提交信息与实施记录双重显式记录降级，不允许静默跳过。

## 5. 提交 + 回写（顺序执行）
1. git commit，格式：
   `refactor(sehd_rise): phase 8 — meta skill as cross-case strategy learner`
   正文含：迁移/重写源文件清单、旧逻辑去向勾销表、验证证据（MetaEdit 合法性枚举 /
   GateEvent 序列候选建议比对 / S1 维持 / 降级说明）、C7 偏差（引用 §2.8）、与旧包行为偏差。
2. 提交后回写计划文档「Phase 8 实施记录」小节（模式严格照抄 Phase 2/3/4/5/6/7 已有条目）：
   落地裁决、验证证据摘要、降级说明、纯净扫描收敛记录。回写内容并入本阶段提交
   （沿用 f34495d / Phase 5/6/7 做法：文档随阶段提交入库）。
3. 更新 zcode 记忆条目 sehd-rise-refactor-plan 的「下一阶段」状态行（一行索引，状态本体在仓库）。

## 6. 交接协议（用户手动递推，不用定时任务）
本卡由用户在新会话手动粘贴启动（Phase 8）。完成第 5 节后按下列步骤为 Phase 9 生成下一阶段任务卡，
由用户手动粘贴递推；Phase 10 完成后停止。

### 6.1 生成下一阶段任务卡
以 `SEHD_RISE_Refactor_Execution_Plan.md` §Phase(N+1) 为唯一内容源，套用本卡第 0–7 节
结构生成 `SEHD_RISE_Phase(N+1)_Prompt.md`（仓库根目录，随本阶段提交入库）。变更点：
- 第 0 节 commit 清单追加本阶段哈希
- 第 1/2/3/4 节按文档对应小节填充
- 第 5 节提交信息用文档原文
- 第 6 节递推到 N+2；Phase 10 改为「不生成下一阶段任务卡，第 7 节输出最终验收汇总」
- 第 7 节第 4 项改为「下一阶段任务卡文件名 + 全文已置于代码块供用户复制」

### 6.2 交付下一阶段启动 prompt（用户手动粘贴）
任务卡文件生成并提交后，**不调用 CronCreate**（用户拍板：手动递推，不用定时任务）。在最终回复（第 7 节）中：
1. 报告 `SEHD_RISE_Phase{N+1}_Prompt.md` 已生成并随本阶段提交入库；
2. 将该任务卡全文置于一个代码块内供用户复制（外层用 4 反引号包裹，使内层 3 反引号不破坏可复制性）；
3. 用户在新会话粘贴该全文启动 Phase {N+1}。

### 6.3 故障停止（不递推坏状态）
- 第 0 节前置校验失败 → 停止，不提交，报告故障项，由用户介入。
- 第 4 节验证未全绿 → 不提交，报告失败项与证据。
- 任务卡文件未生成或未提交 → 报告错误（下一会话读不到文件）。

### 6.4 终止条件
Phase 10 完成后不生成下一阶段任务卡；第 7 节输出最终验收汇总（各阶段哈希、C1–C10 语义变更落地清单、
parity 报告、纯净扫描终验），重构结束。

## 7. 最终回复格式（严格）
1. 完成声明：各验证项证据摘要 + 证据输出文件/命令原文。
2. 提交哈希 + 勾销后的防丢失清单。
3. C7 偏差记录（Meta 可操作面收窄 + §2.8 引用）+ 降级记录（R2，若有）。
4. 下一阶段任务卡交付确认：`SEHD_RISE_Phase9_Prompt.md` 已生成并入库。
5. `SEHD_RISE_Phase9_Prompt.md` 全文（置于代码块内，供用户复制；下一会话由用户手动粘贴启动）。
