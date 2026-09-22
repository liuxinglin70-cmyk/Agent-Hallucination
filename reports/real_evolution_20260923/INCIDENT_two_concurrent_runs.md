# 运行事故记录：两个并发真实运行共用同一目录（2026-09-23）

## 事实

本轮的**第一次**真实运行于 02:11:13 启动（PID 14120），在 02:34 完成 patrol 阶段
（5/6 sentinel 案例已缓存）后继续运行。

我在 02:37 用 `tasklist | grep -c python.exe` 判断"进程已死"——**该判断是错的**：`tasklist`
输出为中文、格式与我的 grep 假设不符，`grep -c` 返回空，我把空读成了 0。据此我删除了
checkpoint 与 cache 并**重新启动**了一个 run（03:14:53，PID 14048）。

结果：**两个 `cycle` 进程并发写同一个 `artifacts/selfevo-real-20260923/`**，各自包装了
一个 `detector.provider_worker` 子进程，持续约 90 分钟（直到 04:47 我确认进程表后手动终止）。

```
ProcessId  StartTime              CommandLine
14120      2026/9/23 2:11:13      python -m evolution cycle --cycle 1 --mini --config ...
14048      2026/9/23 3:14:53      python -m evolution cycle --cycle 1 --mini --config ...
13400      2026/9/23 4:42:54      python -m detector.provider_worker   (14120 的子进程)
11004      2026/9/23 4:46:02      python -m detector.provider_worker   (14048 的子进程)
```

## 影响

- 两个 run 的**费用与耗时**均被浪费；被污染的读数不进入任何结论。
- 除"同日目录内文件互相覆盖"外，**未触及其他运行目录、registry 或原始数据**：两者的
  `work_root`、`registry_dir`、`ledger_path` 都指向同一个本次新建目录，原始数据集只读。
- 污染证据已保留在 `contaminated_attempt/`（首个 run 的 checkpoint 与 ledger、缓存计数），
  **不参与结论**，仅作为事故证据。

## 已做的修复

1. 终止全部 4 个相关进程（2 个 cycle + 2 个 provider worker），确认进程表为空。
2. 把污染的 checkpoint/ledger/缓存计数归档到 `contaminated_attempt/`，清空运行目录。
3. **重新启动单一 run**，并在启动后立刻用 `Get-CimInstance Win32_Process` 过滤
   `CommandLine -like '*evolution cycle*'` **确认只有一个 cycle 进程**，而不是用
   `tasklist | grep`。

## 教训（写给下一个会话来避免）

- **判断"进程是否存活"不要用 `tasklist | grep -c <name>`**：本机 `tasklist` 输出为中文且
  格式不同，grep 会静默返回空，而空与 0 在 shell 判断里难以区分。用
  `powershell -NoProfile -Command "Get-Process <name>"`，或
  `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 并按命令行过滤。
- **启动长任务后立即确认"只有一个"**，而不是只确认"启动了"。
- **同一 work_root 不允许两个 run 并发**：框架本身没有加锁（它假定调用方串行），
  这是已知的运维约束，不是缺陷，但会静默烧钱。
