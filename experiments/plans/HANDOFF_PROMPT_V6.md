# 交接 Prompt（复制以下内容作为下一个会话的首条消息）

```
我们在继续 SEHD / BCCSE 自进化幻觉检测框架的工作。请先完整阅读
experiments/plans/HANDOFF_V6_ADJUDICATION_EVOLUTION.md（唯一事实来源），
并在回复中用 5 行以内确认你理解的现状与方向，然后我们讨论后再动手。

背景摘要：
1. 上一阶段（v5.9a–e）用补丁式迭代修复了零接受死锁，两轮各产出 1 个被接受
   且改变检测结果的 Skill（champion af67ab00），但六轮数据证明补丁路线触及
   结构性天花板：69% 的触发是 unresolved 裁决级僵局，而现有 8 个 Skill 模块
   （自然语言规则注入 provider prompt）在结构上无法修复裁决层失败；提案
   8/14 无效或有害，控制面太弱。
2. 已确定的范式方向（交接文档第 4、5 节）：自进化不局限于 Skill——新增
   L2「裁决策略基因组」，用封闭词表的结构化策略项 + proof court 确定性求值
   接管裁决级失败的进化；L1 provider 规则只保留 artifact 级失败；触发归因
   从失败形态直接分类，废除 v5.8–v5.9e 的情景式仲裁补丁栈。
3. 纪律（必须遵守）：
   - 任何代码修改或实验必须先给我方案并获得批准；
   - 不硬调任何检测阈值（导师明确要求，见交接文档第 6 节）；
   - 每轮实验案例必须全新（排除 experiments/protocols/ 下全部已用 case_id，
     含 validation_100 预留集）；
   - 模型仅限 DeepSeek-V4-Flash/Pro；
   - 遵循 First-Time Principle：修根因不打补丁，不留注释掉的死代码，
     被取代的规则原地更新不追加"as of"笔记。

请按交接文档第 7 节的清单推进：第一步与我确认 L2 策略词表的初版枚举，
并对 5.5 三项检测器层疑似缺陷做离线复现定性（bug vs 策略问题），
给出结论后再进入实现。当前不要运行任何进化实验。
```

## 使用说明

- 新会话发送上述代码块内容后，等待确认回复，再逐项批准第 7 节清单。
- 若新会话需要定位代码：进化主循环在 `src/sehd_v6/core/bccse/evolution_runner.py`，
  反思引擎在 `reflection_engine.py`，proof court 契约在
  `src/sehd_v6/core/paca_tcl/contract_v1.py`（`adjudicate_proof_court`），
  genome 注入机制在 `src/sehd_v6/core/paca_tcl/skill_integration.py`。
- 实验账本与数据集构建：`experiments/protocols/build_full50_dataset.py`。
