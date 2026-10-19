# 下一步预测完整链路修复与离线验收

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

2026-10-16。针对[代码结构与失败分析](next-action-architecture-analysis.md)实施资源、时序、比较合同及异常核验接线，已完成本轮源码的全范围离线验收。原始数据、历史实测和既有进化ledger均保留；修复前76/100、27/62不是本轮效果。

## 🔧 已实现机制

| 工作包 | 源规则与消费者 | 实现行为 |
| --- | --- | --- |
| 资源保护 | config/runtime → Forecast、比较及附加证据 | 同一父预算先保留原有诊断额度；预测超时不会同时耗尽原诊断预算；整例截止、取消、并发、原票数和原有模型审计保留 |
| 任务与顺序 | packet/action_context → 前缀与比较 | 只接受明确轮次元数据或匹配原文user事件；缺失映射保持未知；调用/自身返回与混合内容顺序分开表述，保留原事件定位 |
| 逐约束合同 | action_forecast/judge → proof | 独立constraint_id、执行健康、拒绝原因、有界拒绝原文；理由中的显式未来引用被拒绝；语义unknown不强制修复 |
| 局部修复 | action_forecast/judge/runtime | 剩余额度允许时仅修比较；修复不能修改基础Judge判决或借用未来轨迹；每请求与整例输出额度独立，所有调用计入观察 |
| 共享核验 | action_evidence → Recall/Upgrade/听证 | 重用现有局部算术证书，仅确认显式source_ref复制；模型比较需要quorum和多数，均不能直接定罪 |
| 完整验证链 | API/Recall/Upgrade/hearing/Tracer/Defense/replay | 阴性Judge有合格线索时重开Recall并经标准内容/材料性门；阳性保留原听证顺序并传递同指控来源提示；独立复核可拒绝，缓存与回放绑定实际新增输入 |
| 阶段调用兼容 | Judge、Recall、Upgrade、Tracer、Defense | 保留仅传本阶段路由的独立调用合同；无新增证据不依赖causal_audit；证据缺少策略时明确不准入 |

## 🧪 验证记录

本轮的反例、JUnit与修复前保护文件摘要保存在 `artifacts/detector/fixes/next_action_integrity`。修复前集成反例证明：预测耗尽后诊断也耗尽、多轮未来请求进入前缀、多条约束合并、理由借用未来事件，以及偏离未进入标准复核。修复后针对性用例覆盖这些路径及其正常/拒绝分支。

当前新增27项用例已通过，包括：基础判决不受比较修复篡改、整例预算不重开、整例期限保护、跨案例预算拒绝、真实BFCL/071的未来轮次隔离、未知不重试、孤立偏离票不强制听证、Upgrade拒绝与Defense推翻、来源证书重算、仅返回不能代证行动、重试不重复收取上下文、各证据消费者的回放身份隔离，以及独立阶段已有预算/缺失可选策略的兼容边界。

可复核执行记录见[离线案例汇总](../../../artifacts/detector/fixes/next_action_integrity/offline_examples/7cd44ffa7b76eda5/summary.json)，由[导出脚本](../../../artifacts/detector/fixes/next_action_integrity/export_offline_examples.py)生成，并以[源码身份](../../../artifacts/detector/fixes/next_action_integrity/offline_examples/7cd44ffa7b76eda5/identity.json)绑定。受控算术案例中，基础Judge一致判为正常、Scanner返回空，但合格偏离候选仍进入Upgrade及听证，最终检出并定位到来源事件1；将Upgrade改为拒绝或Defense改为推翻后，最终均为正常。它证明接线与否决分支，不能当作模型实测收益。真实BFCL/071仅使用question/history复查：event_001前缀没有未来轮次的胎压请求，映射不明时返回task_alignment_unknown，模型调用为0。

完整验收首轮源码5996006b...运行2621项测试，2551通过、70失败，外围检查通过；失败均为新增接线强制读取causal_audit，破坏既有仅传阶段路由的调用合同。该回归已在生产接线修正，保留原测试预期；70项失败与27项新增边界合计97项通过，检测器完整目录1240项通过。首轮失败报告保留于artifacts/code_quality/5996006bba5038a11dc499ae6774d0bbb9def7e547ccbaf771567dcb2b49da3b/973223643dba4591869102a485099b90，不作为验收通过。

最终源码`7cd44ffa7b76eda569d369907d60e6054832489fc0bcab2bf07504881e283b94`已通过`python scripts/verify.py --scope all`：2623项测试、18项检查，零失败、错误或跳过，验收期间源码未变；网络仅允许本地回路。[完整验收报告](../../../artifacts/code_quality/7cd44ffa7b76eda569d369907d60e6054832489fc0bcab2bf07504881e283b94/f2419cc8588f4db7b27d0e2cdcf0e686/README.md)和逐文件清单与该轮修复源码一致；随后默认开启的配置变更另列于本文末尾。

[最终保护核对](../../../artifacts/detector/fixes/next_action_integrity/verification.json)通过：938份保护文件摘要未变，4份既有文档保持原字节，技术文档的用户新增段落保留，git diff --check通过。原失败报告与修复前后反例均保留。本轮未调用真实模型服务或启动真实进化。

## 📌 能力边界

缺少轮次映射的多轮记录不能进行严格当前轮次预测；模型的自然语言约束与比较仍可能有语义错误；局部算术和显式字段复制不是通用事实核验或因果证明。共享规则学习、真实teacher-forced PPL、独立事实检索和严格在线调度未在本轮冒充已实现。该轮修复验收时默认开关为off，judge_context路径通过显式配置测试；用户随后要求默认开启，部署配置已按文末记录调整。

本轮不改写原有阳性指控与裁决优先级：Judge阳性先听证，同指控可携新增来源提示；被推翻后才进入Recall。新增线索重开机制针对阴性路径。它不保证穷举一个已检出轨迹中的全部独立错误，不能把局部来源回溯说成全局首错已经解决。

启用judge_context仍须配置足够的新增预算：默认30秒/6000输入字节额度被保留给原有诊断，仅切换开关可能使可选预测不准入；每请求输出上限也不增加整例总额度。是否生成、使用、修复或退化分别查看proof.action_forecast、proof.action_forecast_used和proof.next_action_vote_health；候选与各阶段证据准入另有记录。上述受控案例显式使用180秒/60000输入字节/4096输出额度，当时未修改部署默认配置；后续默认开启见文末。

上述新增预算约束可选预测、比较修复和附加上下文；线索触发的标准Recall/Upgrade/听证沿用原阶段投票、证据门和整例截止限制，全部请求计入execution。接通复核可能增加总请求，本轮不宣称降低费用。

下一轮效果测量需重新冻结同源码off/on、完整配置和恢复规则，在回归集及未见数据分别报告权威覆盖、检测/定位、误报、全部请求与时延。本轮离线通过不能证明真实模型准确率提升或绝对无错误。

## 默认部署开启（用户后续授权）

AutoDL默认部署及DeepSeek配置现显式开启judge_context，设置180秒、60000输入字节、8192输出token和2次新增可选请求，原诊断预留30秒/6000字节不变。默认配置与DeepSeek配置的Fake执行均实际使用预测并走通检出和来源定位，显式off无预测请求；见[执行证据](../../../artifacts/detector/fixes/next_action_integrity/default-enabled-smoke.json)。直接构造自定义配置仍按其配置解析，未声明模式时保留off；历史冻结配置与实验结果不变。该配置变更的回归与源码身份单独记录，不套用上文2623项历史完整验收。
