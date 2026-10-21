# TB 双 agent 重跑

**2026-10-20 11:05 更新：** 当前活动批次为 r3，196题环境已验、45题待解除；57完整、3异常、8运行。已识别的模型限时缺账单保留异常及已知用量下界，不阻止其他未开始项派发，也不重跑该项；其余运行／上下文／账单错误仍停止派发。以[最新状态](TB_ENVIRONMENT_STATUS_20261020.md)及当前登记为准，下文是历史快照。

**2026-10-20 更新：** r1 于昨晚 19:16:43 因两个发布/提交路径异常停跑。已修复为在同一私有事务视图内官方验收，32 项测试和原镜像零模型正反验证通过；09:38:37 启动 r2，保留 29 个终态，只继续未开始任务，8 路恢复。下文为最初启动记录，最新状态见 [TB 修复续跑](TB_RESUME_20261020.md)。

2026-10-19 18:54（北京时间），本机 r760-ssd。

用户要求 SWE-agent 和 Codex 的 TB 都重新跑。新全量批次 `tb-dual-native-full-20261019-r1` 于18:51:55启动，登记241任务×6策略×2套agent=2892项。192个已验环境任务提供2304项首批可派发实验，49个任务对应588项环境阻塞，不隐去、不当作已完成。

当前8项完成（6成功、2不回滚任务失败）、8项运行、2288项待跑；8路worker存活，无新增环境告警。不能据此估计全量优势。

## 固定协议

| 项目 | 设置 |
|---|---|
| 模型／effort | gpt-5.6-luna／max，两套profile已核对 |
| 策略 | Clean、不回滚、Checkpoint、整分支、Causal、Ideal |
| compact | Causal、Ideal、Clean |
| full | 不回滚、Checkpoint、整分支 |
| 并发 | 两套agent合计8路 |
| 故障安排 | seed=20261019；同题两套agent及故障策略共享随机文档顺序与故障位置；Clean无故障 |
| Ideal | 使用真值根及无效步骤集合精确回滚；不给任务答案 |
| 执行环境 | agent和官方verifier在同一原任务镜像容器，保留原workdir、任务解释器、任务用户 |
| 限时 | 每模型会话900秒，官方验证900秒，整项外层3000秒 |
| 调用／步数上限 | 无 |
| 用量 | main+replay；SWE-agent从已返回API响应日志核对，Codex从CLI JSONL核对；文本估计另存 |
| 重跑与覆盖 | 不继承旧结果或canary结果，不按解题结果重跑，保留失败与费用 |
| 正式资格 | false；硬Token预算、提示词费用核算等门槛仍未齐全 |

独立文档被粗粒度回滚丢弃时，先用同一agent模型重建，replay用量单列后加入总费用。任务失败是已完成观测；运行异常、上下文不符或API用量缺失会停止新增派发并保留在途结果。效率只比较双方成功任务，同时保留全部尝试成功数和费用。

## 实际验证及修复

首次模型前预检及canary r1发现原hello-world镜像无git，8项均在模型调用前失败，记录保留。现在只在缺git时通过国内APT补agent启动依赖，再建立事务overlay，不预写任务答案。r2的12项全部完成上下文/环境/API核对，10项解题成功、2项不回滚任务失败；没有成功门槛。

独立Docker CLI目录此前只有Compose，隐藏了已有Buildx，22项补建在实际构建前失败。原失败保留；已将Buildx复制到数据盘、核对SHA256并软链到专用CLI插件目录，未重启共享Docker。r3两路补建正在进行，已新增3个主镜像，累计222/241；新增镜像仍需环境检查。

相关回归测试34项通过，覆盖原镜像工作目录/测试资产、compact与full实际历史、任务失败与基础设施异常区分、API日志和文本估计分离、两套agent相同限额等。

## 后台任务与证据

- 用户systemd全量服务：`agenttx-tb-dual-full-20261019-r1.service`。
- 用户systemd镜像补建：`agenttx-tb-missing-images-20261019-r3.service`。
- 用户systemd监控：`agenttx-tb-dual-monitor-20261019.service`，每30秒检查、每半小时保存快照，不自动重跑模型任务。
- [活动指针](/mnt/data/agenttx-runtime/validation/active-tb-dual-native.json)
- [全量登记](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261019-r1/registration.json)
- [逐项进度](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261019-r1/progress.json)
- [效果对比](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261019-r1/effects.json)
- [12项启动证据](/mnt/data/agenttx-runtime/validation/tb-dual-native-launch-proof-tb-dual-native-full-20261019-r1.json)
- [后台监控](/mnt/data/agenttx-runtime/validation/tb-dual-native-monitor-20261019/latest.json)
- [镜像补建](/mnt/data/agenttx-runtime/validation/tb-images-local-20261019-r3/progress.json)

冻结执行源码：`/home/bfq/agenttx-executions/tb-dual-native-full-20261019-r1/project`。系统盘只存代码，结果、日志、缓存、镜像和临时文件均在数据盘。
