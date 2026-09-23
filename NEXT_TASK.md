# 新会话启动指令（复制下面横线内的全部内容）

---

工作目录：`E:\agent hallucination\agent hallucination`

请先读两处，**不要相信任何文档描述的代码状态——文档比仓库旧**：

1. `agent_hallucination_self_evolution_optimization_plan.md` 的 **§23.10 进度表**与**最后一节执行记录**；
2. `README.md` 的「当前主文档」与「历史进展摘要」。

**当前基线**：`841 passed`，`python scripts/verify.py` 十二项全 PASS。
（本文件上一版写的 730 passed 与"剩余任务 P2.3/P2.2"已过期：那两个工作包与 §28.6 的存档接线
已由 §29 完成，检查点 A 首次真实运行与开发循环接入 cycle 已由 §30/§31 完成。
按旧清单开工会重做已完成的包。）

## 本仓库的两条主线

`detector/` 是检测器底座；`evolution/` 是自进化框架。当前重点是**第二个**：让框架真实生效，
提高基座的幻觉检测与定位能力。以实际检测/定位收益衡量进展，不以增加报告、通过工程测试或
候选数量替代目标。

## 已知的下一步方向（按我评估的优先级）

1. **真实数据上验证 `route_failures.jsonl` 与 `gaps.jsonl` 的运行期写入**——这两处至今只在
   测试与 mock 中出现过，真实运行两次都因"机制全部 actionable + 无路由反证"而无内容可写。
2. **多轮真实运行以检验跨代继承**（§17 的 `G0 < G1 < G2`）——需要多轮各自都有可修机会的设置。
3. **定位方向**：`docs/optimization-round2.md` §6 的证伪条件 1 已触发（结构上可自动修复的
   案例只有 `BFCL/008` 一个，单例不足以宣称机制成立）。备选路线见该文 §6 (a)/(b)/(c)，
   其中 (b) 需要对 BFCL 以外 529 例未测案例跑一次巡检——这是一次真实的预算支出。
4. **P3 开放式演化**前置未满足：`capability_surface.open_ended_evolution=False`，阻塞项是
   `typed execution-graph artifacts` 与 `immutable capability artifact store`。

## 需要用户决定的事（不要自行假设）

- **holdout 策略**：`datasets/protocols/final_holdout_v1.json` 不存在，693 例全部处于开发
  暴露范围。是否创建独立终审集。
- **真实服务预算**：多少调用/时长。运行真实服务前先确认。

## 必须遵守

- 每条高危结论先**可执行复现**再动手；区分"活缺陷"与"潜伏缺陷"，不要虚报。
- 每个工作包：一个提交；`python -m pytest -q` 全绿；`python scripts/verify.py` 全 PASS；
  归档到 `reports/<date>_<topic>/`（README 必须写清**未做什么**，不只写做了什么）；
  更新计划文档 §23.10 进度表并新增一节完成记录。
- 不运行真实服务（除用户明确批准）；不创建 holdout（除用户明确要求）。
- 结束后清理 `artifacts/` 中由本次运行产生的临时产物。

## 长任务的两条运维教训（来自真实事故）

- 判断进程存活**不要**用 `tasklist | grep -c python.exe`（本机输出为中文，grep 静默返回空）。
  用 `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 并按命令行过滤。
- 长任务启动后确认"只有**一个** cycle 进程"，不只是"启动了"。同一 `work_root` 不允许两个
  run 并发（框架未加锁，会静默烧钱）。`python.exe` 里同时会有一批 `detector.provider_worker`
  子进程，那是正常的，不要当成第二个 run。

---
