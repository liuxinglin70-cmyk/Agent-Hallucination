# 根因定位与答案提交步

定位遵循 `detector.semantics.LOCALIZATION_LAW`，Judge、Recall、Upgrade、Tracer 和 Defense 使用同一规则。目标是轨迹中最早引入实质错误的 assistant 内容或动作。任务、轨迹和因果证据决定定位，数据集标注只用于评估，不能进入定位或强制映射。

答案提交消息或工具调用允许作为候选：当它首次引入错误答案、错误动作或虚假完成声明时，它本身就是待纠正点。若它仅重复较早步骤中尚未纠正的错误，则是错误已经交付的证据，不是一个新的根因候选。工具观察本身不是 assistant 的幻觉；assistant 首次错误采用、转述或据此作出错误动作的位置仍需单独判断。

例如，步骤 2 首次声称船舶吨位为 76，步骤 3 仅提交同一答案时，应追溯步骤 2；如果步骤 2 内容正确而步骤 3 首次提交 76，则步骤 3 可定位。这个规则适用于所有案例，不硬编码 OpenDeepSearch/086，也不保证某次模型输出必然与标注一致。证据不足时保留定位不确定性。

## 两种步都算命中，但读数必须分列

按上面的口径，**内容步与动作步都在定位候选之内**，命中定义不变：只要采纳的
`responsible_step` 等于标注的 `hallucination_step` 就算命中，不因该步是内容步还是动作步而加权或折扣。
既有的命中率（如 72.6%）语义保持原样，没有放宽也没有收紧。

但**动作步命中率与内容步命中率不可混读**，两者回答的是不同问题：

- 动作步（`action_step`）：该事件自身 **没有** assistant 散文，只有工具调用。命中意味着"它找到了正确的工具调用"。
- 内容步（`content_step`）：该事件自身 **有** assistant 散文。命中意味着"它找到了正确的那句话"。
- 空步（`empty_step`）：该事件既无散文也无工具调用。只在结构异常时出现。
- 未分类（`unclassified`）：行里没有记录步的性质，且无法从轨迹重建（例如缺文件）。**不是**"空步"。

一个跨两类平均出来的数字会掩盖"这一臂究竟改善了哪一种"，所以在生产读数处必须分列报告：

- `evolution/metrics.py`：`localization_metrics` 返回 `step_kinds`（各 500 项 `*_scorable` /
  `*_hits` / `*_accuracy`）与 `localization_conflict_rows`；`paired_localization_delta` 返回
  `baseline_step_kinds` / `active_step_kinds` / `localization_conflict_rows`。既有的五个（或三个）
  头部计数**逐字未变**，分解只是同一批命中的第二种视图。
- `evolution/engine.py`：`_step_localization_check` 除原有 `n` / `baseline_hits` / `active_hits` /
  `not_regressed` 外，新增 `step_kind_split`（按 `baseline`/`active` × 四种性质分列）与
  `baseline_classified` / `active_classified`。`not_regressed` 的判据只读原有计数，**不**读分解。

分列的步性质是**机械判定**，不是字符串启发式：`detector/packet.py` 的 `step_kind()` 在
packet 构建后直接读事件结构——`content` 非空 ⇒ 内容步；`content` 为空而
`tool_interactions` 非空 ⇒ 动作步；两者都空 ⇒ 空步。既有散文又有工具调用的事件记为**内容步**
（该步有可就地检查的散文）。判定永不读取 gold、不读任务文本、不做长度或关键词启发。

该性质在定罪时写入行内：`detector/adjudication.py` 给每个定罪的 `responsible_step_kind`
赋值（无法在 packet 中定位该步时为 `null`，绝不是猜测值），`detector/result.py` 把
`responsible_step_kind` 与 `localization_authority` / `localization_conflict` 一并放进
detection envelope。对**早于该字段**归档的行，`evolution/engine.py` 的
`_step_kind_from_record` 用同一份 `{question, history}` 投影重建 packet 后调用同一个
`step_kind()`——同样的机械规则、同样的字节，仍然不是对行内数字的推断。两者都不可得时才记
`unclassified`，宁可留下未分类也不把猜测写进分解。

## 冲突必须降级，不得静默取其一

一次定罪可能同时携带**多份**关于责任步的声明：被判罪 hearing 的 tracer、给它定罪的 defense
的 `confirmed_step`、该 hearing 的指控步（allegation step），以及当 judge hearing 被
refute、由后续 recall-upgrade hearing 定罪时，**首个 hearing 的 tracer 步**——后者正是
envelope 里 `proof.step_tracer` 所报告的值。归档实测出现过"轨迹说 14、采纳 10"与
"轨迹说 3、采纳 1"这类同一行报告两个不同责任步、却仍记 `authoritative` 的状态。

采纳规则是**写明且可复现**的（`detector/adjudication.py::LOCALIZATION_STEP_PRIORITY`，写入
`localization_conflict.adopted_rule`）：被判罪 hearing 的 step tracer 优先；tracer 失败时才
回落到该 hearing 的 defense `confirmed_step`（且仅当 defense 是普通 `substantiated`，因为
`substantiated_upgraded` 的契约强制 `confirmed_step = null`）；再回落到指控自身的步号。

- **tracer 与 defense 不一致**：记录 `localization_conflict`（含
  `tracer_step` / `defense_step` / `allegation_step` / `adopted_step` / `adopted_rule` /
  `sources`），并把该行的 `localization_authority` 降级为
  `degraded_localization_conflict`。
- **首个 hearing 的 tracer 与最终采纳步不一致**（即上报了 `proof.step_tracer` 却采纳了别的步）：
  同样记录冲突并降级。这一条是"同一行两个责任步"的直接观测点。
- **指控步与 tracer 不一致不算冲突**：tracer 的职责就是找**更早**的 locus，比指控更早是它正常工作。

降级只作用于 `localization_authority`，**不动 `decision_authority`**：定罪的成立与否由 hearing
流程决定，与两个候选 locus 里采纳了哪一个无关。因此下游读取检测判定的契约（`prediction_state`
要求 `decision_authority == "authoritative"`、`arm_has_infrastructure_failure`、
`localization_behavior_gate` 依赖的 authoritative 行）完全不受影响；受影响的是"这行的定位能否
当作权威证据"，而这正是需要被降级的那一项。冲突行在分列分解中被**排除**（两个步都上报了，
按哪一个记为命中或未命中都是行本身无法支撑的断言），并由 `localization_conflict_rows` 单独计数。

## 既有计量不变

`_step_localization_check` 继续按既有规则单独计量，不把判定正确视为定位正确。本次没有改变步号、
评分函数、数据集标签或门限，没有删除任何既有检查项，也没有放宽任何命中定义；新增的是分解读数和
冲突记录。共享规则的离线测试证明五阶段都接收该口径；真实定位稳定性必须以独立重复运行的输出为据，
不能由提示词测试宣称解决。
