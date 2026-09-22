# P1.4 + P1.5 + P1.2：执行器身份、校准审计、候选存档与组件库

日期：2026-09-22
范围：`evolution/contracts.py`、`evolution/calibration.py`（新增）、`evolution/archive.py`（新增）、
`evolution/gates.py`、`evolution/development.py`、`evolution/proposer.py`、`evolution/engine.py`、
`scripts/audit_calibration.py`（新增）
证据等级：**diagnostic-only**（离线；`calibration_audit.json` 是对归档行的审计，不是检测质量结论）

## P1.4 可执行身份（§12.2）

**修掉的缺口**：`structured_key` 的 `program_hash` 对非 `typed_dsl` runner 恒为空串，而 artifact
的路径与摘要**根本不进键**。实证：两个同 predicate、同 runner 名、但绑定不同模型的条目产生
逐字节相同的键，会被 STRUCTURAL 去重判为"与已部署重复"。

**落地**：

- `DETERMINISTIC_RUNNERS` = `deterministic` / `typed_dsl`：行为由条目自身完全描述。
- `artifact` 字段（`id` + `digest`）进入键尾。**路径刻意排除**：同一权重换路径是同一行为。
- `identity_incompleteness(entry)`：learned runner 缺 id 或 digest 时报出原因。
  **"无法判断是否相同"不得当作"不同"通过**——这正是 §23.2 "learned 产物必须有 digest" 的落点。
- 确定性条目得到统一空后缀，因此**等价关系不变**（键元组多了一位，但确定性条目之间
  比较结果与改动前一致；键不落盘，不是兼容性破坏）。
- 三处接线：STRUCTURAL 门（identity 不完整即拒）、冗余比较（不完整条目退化为 prose 比较）、
  L0 的 `structured_identity`。

**关于可达性**：目前**没有**注册任何 learned runner（那是 P2.1），所以 `validate_intervention`
会先在 schema 层拒绝未知 runner——identity 检查现在是纵深防御，不是当前把守者。这一点在测试
里写明并按此断言（`test_a_learned_runner_is_refused_by_validation_until_it_is_registered`），
另有两项用 monkeypatch 模拟 P2.1 注册后同样的检查会真正生效。

## P1.5 校准审计（§13.2）

**落地**：`evolution/calibration.py` + `scripts/audit_calibration.py`。

- **离散 reliability table**：逐级别的准确率、错误率、弃权率、覆盖率。
- **弃权是独立结局**，不是错误：把"拒绝决定"并入"错"会让谨慎的系统显得失准、鲁莽的显得精准。
- 分母为空时准确率是 `None` 而非 `0.0`："该级别没有案例"与"该级别全错"是两个不同发现。
- **per-source 分组**：级别可以整体良好而单一来源很差，拆分才看得见。
- **单调性作为发现报告**，不强制：非单调说明该级别不可用作排序，隐藏它就毁了审计的唯一目的。
- 连续指标（Brier / log loss / AUROC / calibration curve / accuracy-at-coverage / bootstrap CI）
  **明确不实现并说明原因**：`judge.confidence` 是无数字尺度的有序级别，计算它们等于编造映射。
  原因写进报告本身（`continuous_metrics_unavailable`），读者看到的是"为何没有"而不是"好像漏了"。
- 全部标 `calibration-only`。

**对真实归档行的读数**（30 例去重后）：high n=25 / 正确 20 / 准确率 0.80 / 覆盖 0.926；
medium n=4 / 正确 1 / 弃权 2 / 准确率 0.50。单调性成立。

## P1.2 候选存档与可复用组件（§9）

**落地**：`evolution/archive.py`，两个存储，与 Active Registry 三层分离。

- **候选存档** `candidates.jsonl`：记录**通过与否的每一个候选**及失败原因。
  按 `(mechanism_id, candidate_hash)` 去重；同内容换机制算是不同候选。
  `repeated_failure_reasons` 按**候选**计数（一个失败记一次），这正是"不要重复付同一个代价"
  要回答的问题。
- **可复用组件** `components.jsonl`：本框架里可复用的单元是**校验过的 typed-DSL 程序**——
  唯一同时具备稳定摘要、封闭词表、无自由代码、且有两个消费者（`evidence_check` 观察、
  `recall_trigger` 路由）的对象。prose directive 刻意不算组件：没有摘要、无法组合，
  "复用"只会是复制文本。
- 组件按 `(kind, 程序摘要)` 去重；`kind` 是检索输入，故同一程序在两个角色下是两个可达部件。
- **组件层无法晋升**：模块不导出任何通往 registry 的路径，记录里没有任何准入字段，
  有专项测试断言这两点。
- 检索有界且可解释：按机制 > 能力 > 角色的顺序排序并截断。
- 接线：`proposer.set_components()` 是**上下文不是约束**；引擎按机制所需的**能力**
  （`repair_requirements` 的 `control_flow`/`guard`/`observation`/`prompt`）检索并注入
  prompt，`OfflineProposer` 没有该方法则被 `hasattr` 跳过。

**过程中修掉的两个自己的缺陷**：
1. 引擎初版按 actionability 的 `classification`（`actionable`）检索，而组件按能力面词表
   （`control_flow`）登记——两个词表共用一个字段名，检索**永远匹配不到**。已改为用
   `repair_requirements(...)["required_capabilities"]`。
2. `record_component` 初版只按程序摘要去重，同一程序换 `kind` 会静默返回旧记录，
   使调用方刚声明的角色**不可检索**。已改为按 `(kind, digest)`。

## 验证

- `pytest -q`：**715 passed**（§25 结束时 680，本轮 +35：P1.4 六项、P1.5 十一项、P1.2 十七项、
  及既有文件中的接线回归）；
- `scripts/verify.py`：七项全 PASS；
- `scripts/audit_calibration.py` 对真实归档行跑通，读数见上；
- 端到端离线验证：注册组件 → `run_cycle` → mock proposer 收到该组件，含完整程序且无来源泄漏。
- **未运行真实服务，未创建 holdout。**

## 未做 / 仍未解决

- **P2.1 未开工**：`RUNNER_REGISTRY` 与 `typed_semantic` 未建，P1.4 的 identity 检查仍处纵深位置；
- **候选存档未被任何运行写入**：`archive_candidate` / `ingest_development_run` 已可用并有测试，
  但 `develop` / `cycle` 尚未调用它们。P1.2 的存储**可用但未接线到运行路径**——这是刻意的：
  接线需要决定归谁调用（开发循环还是 cycle 末），属下一步工作；
- 组件库同样只由显式调用写入，尚无"某个候选的 predicate 自动登记为组件"的路径；
- 检查点 A 仍未运行；独立 holdout（§8.3）未创建。
