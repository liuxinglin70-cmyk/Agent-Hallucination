# 单机制诊断与下一轮预检

状态：真实诊断运行中，尚未达到新验证实验启动条件。

本目录是开发诊断证据，不是独立测试集结果。沿用原模型、投票、运行时限及准入门限；不修改旧运行和候选准入历史。

## 固定诊断

`protocol.json` 在真实调用前冻结：原候选 IV-1-001，两个原失败成员 BFCL/017、OpenDeepSearch/086，以及两个原基线正常负例 Magentic_One/036、OpenDeepSearch/002；每例重复两次，共八次，最多两个并发。冻结原 judge 及其投票，后续阶段真实执行。模型输入仍只含 question/history，不加入 gold。

`single_mechanism.py` 位于上级目录。每次结果独立落盘；运行异常与机制结果分别记录，不通过重复覆盖失败获取成功。`summary.json` 的 complete 字段表示八次诊断是否全部返回。

## 已确定的问题

Judge 合同明确规定：is_hallucination=false 时，hallucination_reason、hallucination_step、evidence_refs、root_cause 必须为 null。原候选把正常的空 hallucination_reason 解释为“无依据的否定判定”，这是字段语义误读。结构可执行不等于因果机制正确。

`selectivity.json` 对原 30 条基线进行真实规则离线执行，命中 6 FN、4 TN、2 TP。两个 TP 是终判类别，不代表 judge 为正；它们已被原后续流程纠正。此结果不能证明强制扫描无效，但已经否定“空原因本身识别异常”的解释。

输入可识别性也不同：BFCL/017 的标签要求 diff 比较，而可见任务只要求文件相同；OpenDeepSearch/086 的可见工具输出仅含 76 tons，标签理由中的 116 tons 及历史文本没有进入检测器输入。重新扫描同一轨迹不会自动补齐外部事实，不能将触发成功记为纠错成功。

BFCL/017 两次真实回放均成功触发。第一次扫描提出 3 个疑点，升级全部否定，终判 false；第二次扫描提出 4 个疑点，其中“#ProjectUpdate 与 ProjectUpdate 不同”在第 7 步获得升级和三票防御确认，终判 true。标注错误位于第 4 步，因此第二次只能记录为标签层面命中，不能据此宣称纠正了文件比较机制。两次结果均为 authoritative，但一致性不足。

OpenDeepSearch/086 两次均成功触发、各提出 2 个疑点，均终判 false、authoritative。其中第一次升级声称正确值是 91 tons，但未提供外部证据，防御拒绝了这一断言；第二次没有升级。标注理由写的是 116 tons，而检测器可见观察为 76 tons。本诊断不裁定三个数字的真实历史含义，结果只证明当前回放没有补齐可靠参考证据。

## 已实施的提案修复

`evolution/proposer.py` 明确提供 judge 字段合同，禁止将正常 null 或非空正例理由解释为异常/自我反驳；取消每个未扫描 FN 族必须生成 recall_trigger 的要求，允许缺乏可见证据时返回空提案。该修复没有改变本轮诊断候选，也没有参与其模型调用；诊断运行的是冻结的旧候选。修复后 proposer 测试 5 passed，全仓库回归 325 passed in 31.28s。

`analysis.json` 由上级 `summarize_single_mechanism.py` 汇总，单独记录标签命中、责任步骤、升级理由、防御结论及技术失败。两次重复不作为两个独立案例计算泛化效果。

## 新开发协议草案

`next_protocol_preflight.json` 记录一次预先固定的扩容：沿用原种子，S 从 3 正+3 负扩为 5 正+5 负，G 从 4 扩为 10，其余 10 例进入巡检。未搜索种子、未按候选结果替换样本、未降低门限。

历史记录显示 S 有 3 个可保护 TP、3 个可保护 TN；G 有 2 个 FN、4 个 FP。但 S 的 Magentic_One/049、G 的 OpenManus/064 存在非权威结果。必须先得到新运行的健康基线，再依据固定协议判断是否可测；预检失败时保留失败，不重新换种子。

配置：`evolution/configs/development_diagnostic_20260918.json`，独立输出目录，单轮、两并发。所有现有 693 条数据已有开发暴露，原 10 条 validation 也不是独立泛化证据。此配置尚未启动。
