# 三组补充对照与上下文小消融：2026-10-31

用户确认补跑不回滚、按轮回滚、整分支回滚，并同意加入上下文同步的小消融。复用已完成的 400-case 因果恢复与时间后缀结果，不重新运行对应模型单元。

## 批次组成

| 策略 | 案例数 | 重复数 | 新模型单元 |
| --- | ---: | ---: | ---: |
| `crash_no_rollback` | 400 | 3 | 1200 |
| `turn_checkpoint` | 400 | 3 | 1200 |
| `whole_branch_abort` | 400 | 3 | 1200 |
| `causal_workspace_only` | 12 | 3 | 36 |
| 合计 | | | 3636 |

另有 48 个无模型运行时预检（12 个代表案例 × 4 个变体）和 4 个短案例模型 canary，单独保存，不填入主结果。36 个消融单元排在主队列最前面，四个变体共用 8 个并发槽位。

## 小消融检验什么

完整 AgentTX 在定位后沿依赖图恢复文件，并使失效效果对应的工具观察退出可见上下文。`causal_workspace_only` 使用相同的定位、依赖闭包与文件恢复，随后恢复失败前的旧工具观察；恢复通知与恢复代次保持一致。这样比较的是文件恢复后同步上下文的作用。

消融保留旧观察是预定处理，因此其恢复时文件/历史一致性应为假。结果分别报告最终任务完成（最终验证与命令审计）和恢复合约通过（还要求上下文一致、有效文件字节保留），不能把任务最终修好等同于恢复状态一致。消融若没有增加修复成本或降低完成率，也按实际结果报告。

12 个案例与既有预检选择一致，覆盖执行长度、故障位置与依赖密度。具体 ID 已在 `registration.json` 的 `ablation_cases` 固定。与参考批次按同一 case/repeat 配对，复用 36 个完整因果恢复结果；统计时保留每个 case 的三次重复关系。

## 执行流程与可比性

原始轨迹执行与故障注入 → 各策略恢复 → 构造恢复后的完整模型上下文 → SWE-agent 继续修复 → 命令审计与最终验证 → 实际 API 用量核算。

参考批次为 `/mnt/data/agenttx-runtime/results/system-micro-canonical400-paired-20261030-r2`。补充批次复制该批冻结源码；审计确认原有 472 个文件中 471 个逐字节一致，仅活动编排器更新，并另加隔离的上下文消融入口。原始 benchmark、运行时和输入 manifest 均沿用参考版本。

- SWE-agent 原生执行，`gpt-5.6-luna`，reasoning=max，full context。
- 每单元模型时限 3600 s，实际 API token 预算 8,000,000，无调用/步数上限。
- 定位探测预算固定为 `2 × operation_count + 8`。
- 不按任务结果重跑；基础设施异常停止新增派发并保留在途结果。
- 不增加 Clean/Ideal 模型组，不修改纯读命令过滤或依赖图策略。
- 镜像固定为 `sha256:22feb19c04e4b8e8439ecafdfa0cba6430c415e0a8edea86c10e706a2646fed7`；模型 profile 与参考批次逐文件校验。

## 存储与运行入口

初始 r1 的无模型预检运行时发现数据盘仅余约 478 GiB。迁移前尚未派发 canary 或主矩阵模型调用，r1 原始记录保留，八个明确属于该批次的运行时容器已清理。迁移记录为 r1 目录中的 `pre-model-storage-migration.json`。

r2 改存 `/home` 所在的大容量文件系统，启动时约余 4.2 TiB。2026-10-31 北京时间 21:03 启动服务；48 项运行时预检完成后进入 4 个模型 canary，再派发主矩阵。

- 结果：`/home/bfq/agenttx-experiment-data/results/system-micro-controls-context-20261031-r2/`
- 冻结源：`/home/bfq/agenttx-executions/system-micro-controls-context-20261031-r2/project/`
- 实验服务：`agenttx-system-micro-controls-context-20261031-r2.service`
- 监控服务：`agenttx-system-micro-controls-context-monitor-20261031-r2.service`
- 监控快照：`/mnt/data/agenttx-runtime/validation/system-micro-controls-context-monitor-20261031-r2/latest.json`
- 活动批次指针：`/mnt/data/agenttx-runtime/validation/active-system-microbenchmark.json`

独立监控每 60 秒读取进度、准确对应的 worker 容器、日志、已完成结果的用量与预算以及磁盘空间。监控不调用模型、不修改 worker、不重试任务。磁盘不足 150 GiB 告警，编排器低于 100 GiB 停止新增派发。实时计数以结果目录的 `progress.json`、`cells.json` 与监控快照为准。

## 启动核验

北京时间 21:11 核验：48/48 运行时预检、4/4 模型 canary 通过，主队列已派发 8 个消融单元。四个 canary 的任务验证、命令审计、有效文件保留检查及实际 API 用量记录均通过，合计 132986 tokens，无超预算。上下文一致性在三个正常对照中为真，在消融中按设计为假。

消融 canary 的 SWE-agent 模型视图保留 32 条失败前动作/观察消息，对应完整 AgentTX canary 为 24 条；差异恰好是被回滚的 4 个操作的动作/观察对。36 个待运行消融单元均已匹配同 case/repeat 的已完成因果恢复单元，manifest 哈希一致，参考用量完整。逐项启动核验存入结果目录的 `launch-validation.json`。

## 网关异常与用户授权重跑

北京时间 22:40，主矩阵在 192 个通过、5 个基础设施异常后停止派发。这 5 个单元均通过任务验证和命令审计，但共 7 个请求出现 `BadGatewayError`，费用未知；其余响应的已知用量为 2131979 tokens。

用户明确要求“异常就重跑”。北京时间 23:21，将全部 5 个异常单元的完整输出、日志、批次状态和逐项费用记录移入结果目录 `retry-history/20261031T152138Z/`，登记 `retry-registration.json`，然后仅把这 5 个单元重置为待运行，连同原有 3439 个待运行单元恢复派发。192 个已通过单元不重跑。原始未知费用保持未知；重跑费用独立记录，后续总开销统计需包含原尝试的已知费用并注明未知部分。原冻结源码、模型配置和停止条件保持一致。该次基础设施重跑是用户对初始不重跑规则的明确变更。

## 11 月 1 日恢复与持续监控

北京时间 15:34，批次在 1586 个通过（包含 36 个消融）、1 个任务失败和 4 个基础设施异常后停止。其中一个容器创建失败并报 `no space left on device`；另外三个任务完成验证，但四个 API 请求未返回完整用量。结果位于 `/home`，Docker 数据根位于 `/mnt/data`；原先只检查结果所在盘，未覆盖 Docker 所在盘。

用户要求继续运行并持续监控。16:35 恢复 8 个并发 worker。四个异常单元的原输出、日志与批次状态已归档到 `retry-history/20261101T083454Z/`，原尝试的已知用量为 3832162 tokens，四个请求的未知用量继续保留为未知。其余 1587 个已结束单元继续复用，其中命令审计失败的一个单元不重跑。恢复时 2049 个单元待执行，包含四个重跑单元。

此次仅修改编排器的磁盘检查：派发前同时检查结果盘和 Docker 盘，任一低于 100 GiB 则停止新增派发。模型、输入、恢复算法与验证逻辑沿用注册版本。冻结目录中只有编排器变更，原文件、修改前后 hash 和运行时预检结果存入 `maintenance/resume-20261101/`；额外预检不计入实验分母。磁盘检查与监控的两项回归测试已通过。

独立监控每 60 秒检查进度、实际 worker、API 用量完整性、预算及两块盘的余量。每 30 分钟、运行阶段变化或告警变化时写入一份汇总；当前报告为监控目录的 `latest-report.md`，历次报告为 `reports.jsonl`，每分钟快照继续保存在 `latest.json` 与 `samples.jsonl`。磁盘低于 150 GiB 告警，低于 100 GiB 触发停止派发；监控本身不调用模型、不重试任务。

16:51 核对：四个异常单元均已重跑通过，任务验证、命令审计和 API 用量完整性检查均通过，无单元超预算。本轮重跑合计 3913507 tokens，原尝试的已知用量和四个未知请求另计。逐单元证据见 `maintenance/resume-20261101/retry-validation.json`。8 个并发 worker 继续派发后续待运行单元。

18:42 核对：本轮在途任务已全部结束，协调器停止，独立监控保持运行。三组对照共 1650 次通过、1 次任务失败、2 次基础设施异常、1947 次待运行；36 次上下文消融另外完成。两个异常单元分别为 `controlled-t10x16-f0.5-s7/whole_branch_abort/repeat-01` 和 `controlled-t10x16-f0.5-s8/turn_checkpoint/repeat-01`，任务验证及命令审计均通过，各缺一个 API 请求的用量；已知用量合计 1872848 tokens，未知部分未补零。协调器首先因用量异常停止新增派发，随后 Docker 所在盘余量降到 100 GiB 以下，在途任务全部结束时仅余约 86.9 GiB，因此尚未恢复派发。逐项终态核对见 `maintenance/resume-20261101/drain-completion.json`。

## 11 月 1 日 Docker 迁移与再次续跑

北京时间 21:37，将本批实验切换到 `/home` 所在的充足空间。旧 Docker 上另有一个运行中的容器，因此新增隔离的 `agenttx-home-docker.service` 和 `agenttx-home-containerd.service`，不重启旧服务。新 Docker socket 为 `unix:///run/agenttx-home-docker/docker.sock`，数据目录为 `/home/bfq/agenttx-experiment-data/docker-home-runtime/docker`；启动核验时余量约 3655 GiB。仅导入原实验镜像，镜像 ID 逐字匹配，不复制旧服务的其他镜像和容器。

编排器和监控通过 `AGENTTX_DOCKER_HOST` 选择新 socket，默认值保留旧入口。冻结源码清单逐文件核对：只有编排器发生运维修改，其余 472 个已登记文件一致。新服务上的额外无模型预检通过，磁盘检查两项回归测试通过；这些预检不计入实验分母。迁移脚本和编排修改证据位于 `/home/bfq/agenttx-experiment-data/docker-home-migration-20261101/`，逐项运行核验位于本批 `maintenance/docker-home-20261101/migration-validation.json`。

两个用量不完整的异常单元原输出、日志和状态已归档到 `retry-history/20261101T133720Z/`，原尝试已知用量 1872848 tokens，两个未知请求仍为未知。仅重置这两个异常单元，已完成的 1651 个有效对照与 36 个消融不重跑。恢复时剩余 1949 个对照单元，21:38 核验已有 8 个真实 worker 运行、1941 个待派发；两个异常重跑均在首批 worker 中。独立监控已切到新 socket，每分钟记录，新快照无告警。旧 Docker 上的运行容器保持运行。
