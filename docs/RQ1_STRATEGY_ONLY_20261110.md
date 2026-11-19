# RQ1：固定轨迹、无模型的全量重跑

本轮按用户在 2026-11-10 的要求重新执行整个 RQ1。旧的模型续跑结果保留，
不计入这轮策略实验。RQ2 的真实 agent 实验保持独立。

- 案例：原先注册的 400 个固定轨迹案例，输入 manifest 哈希逐个与旧注册表核验。
- 策略：AgentTX、No rollback、Turn checkpoint、Temporal checkpoint、Whole branch。
- 主实验：400 × 5 × 3 = **6,000 次**，每种策略 1,200 次。
- 运行前检查：另取覆盖三个参数扫描和密集依赖端点的四个案例，五种策略各跑一次，共 20 次，不计入主实验。
- 并发：最初 8 路，2026-11-10 按用户要求增加到 12 路；每次执行独立容器，不重试或覆盖已有结果。
- 执行：脚本运行固定工具命令，在实际 OverlayFS/strace 运行时捕获依赖、执行恢复、检查文件状态及可见历史。没有自主解题阶段。
- 模型/API：不启动 SWE-agent 或 Codex；不挂载模型配置、凭据或客户端；容器使用 `--network none`。

RQ1 报告错误影响清除率、有效工作保留率、有效调用丢弃量、恢复集合准确性，
以及保留文件字节与 agent 历史的一致性。
AgentTX 使用运行时捕获的依赖和验证器定位；生成器的错误根和依赖闭包仅用于事后评分。
本轮没有实际模型 token 消耗，不报告 API token 节约。

`policy_contract_passed` 表示实现遵循该策略；`recovery_complete` 表示是否清除了所有错误影响。
No rollback 应通过前一项，但由于保留错误影响，不应通过后一项。

## 运行与数据位置

结果目录：
`/home/bfq/agenttx-workspace/agenttx-experiment-data/results/rq1-strategy-only-6000-20261110-r1`

冻结源码：
`/home/bfq/agenttx-workspace/agenttx-executions/rq1-strategy-only-6000-20261110-r1/project`

运行服务：`agenttx-rq1-strategy-only-20261110-r1.service`。
监控服务：`agenttx-rq1-strategy-monitor-20261110-r1.service`。
监控每分钟检查进度、结果哈希和零模型调用，每五分钟写报告。
执行异常时停止派发新任务，保留错误记录和已经完成的数据。

- `registration.json`：实验设计、固定镜像、并发、零模型调用配置。
- `source-manifest.json`：冻结源码文件哈希；原始运行时的 475 个文件未改动。
- `cells.json` / `progress.json`：完整矩阵和实时状态。
- `main/<trace>/<policy>/repeat-XX/result.json`：单次评分、耗时、实际依赖和运行记录。
- 对应的 `precrash-conversation.json`、`recovered-conversation.json`、`recovered-ledger.json` 和 `session/`：恢复证据。
- `monitor/latest-report.md`：五分钟汇报。
- `monitor/latest.json`、`samples.jsonl`、`reports.jsonl`：最新检查及历史记录。
- `launch-validation.json` / `startup-main-validation.json`：启动时的挂载、网络、源码哈希和主实验结果核验。

20/20 次运行前检查已通过，主实验于北京时间 2026-11-10 21:05:41 完成：
6000/6000 次，执行异常为 0，模型调用及 API 请求均为 0。
4800 次完成完整恢复，No rollback 的 1200 次保留错误影响。
最终汇总见 [RQ1 最终结果](../review/rq1-strategy-only-final-20261110/README.md)。

离线协议检查：
`/home/bfq/.venvs/agenttx/bin/python -m unittest tests.test_rq1_strategy_only -v`。
五项检查已通过，覆盖 400 个案例的恢复集合性质、No rollback 的评分区分、
无网络/无模型配置的容器命令、旧运行时的模型调用分支隔离，
以及以 12 路续跑时只调度未完成任务并保留原始开始时间。

并发切换先暂停旧调度器的派发，等待 8 个在途容器正常结束，核验并接收其结果，
再用 12 路继续剩余队列。原始注册表、案例、策略、重复次数和冻结的容器运行代码
均保留；宿主机调度器的新版本、已完成结果哈希及切换记录保存在结果目录
`orchestration/workers-12-*/`。监控仍每分钟检查、每五分钟写报告，并按实际并发显示。

400 个案例标识唯一。其中两个参数扫描交点各共享一份 DAG，共 398 个不同的
manifest 哈希；保留原始注册的扫描设计，没有增删或替换输入。
