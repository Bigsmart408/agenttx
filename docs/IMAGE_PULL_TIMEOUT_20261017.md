# 镜像拉取超时诊断

2026-10-17，北京时间 15:40 在本机 r760-ssd 切换到环境准备批次 r3。模型实验继续暂停。代码位于系统盘，镜像、缓存、下载、日志和证据位于 `/mnt/data/agenttx-runtime`。

旧规则按每个源累计 1800 秒终止，不区分是否持续下载。实际卡住的 Matplotlib 镜像最大压缩层为 1493134556 字节，慢速下载可能超过该限制，不能据此认定镜像源失效。另有失败与 14:30 左右系统包更新导致的 Docker 服务停止／启动吻合，包括 unexpected EOF 和 socket 暂时消失；不能全部归因于网络超时。

新规则通过本机专用 Docker socket 读取引擎 NDJSON 事件：

- Downloading、Extracting 的层字节累计值增长，或某层首次完成／校验完成，视为进展。
- 重复的 Waiting、Retrying 或未增长字节不刷新计时。
- 连续 900 秒无进展才判为 idle_timeout，单源累计 14400 秒判为 wall_timeout；明确的连接／镜像源错误立即失败并换源。
- 源顺序：docker.1ms.run → hub.rat.dev → docker.1panel.live。仅使用国内可达代理域名，无直接上游回退。
- 每次保留原始事件、错误输出和最终原因；每 10 秒保存当前字节计数和空闲时长，独立监控每 60 秒观察一次。仅取消对应拉取客户端，不重启 Docker。

已对实际目标 `swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-24149` 实测 10 个候选源。三个可用源返回相同 manifest 摘要，并通过配置摘要核验和最大层 1 MiB 抽样：

| 源 | 抽样速率 MiB/s | 处理 |
|---|---:|---|
| docker.1ms.run | 0.424 | 继续首选 |
| hub.rat.dev | 0.146 | 新增第一备用 |
| docker.1panel.live | 0.108 | 保留备用 |

其余候选出现 403、429、502、连接超时或非 registry JSON 响应。本次抽样不能预测整个大层的稳定速度，也不代表镜像已完整拉取。完整就绪仍需成功拉取并通过镜像内容核验。

验证包括 7 项真实子进程流测试（持续下载、解压、重复等待、总时限、JSON 错误、流提前结束、完成层），以及本机 Docker API 拉取 Alpine 和断网容器运行。首次 API 冒烟发现引擎成功消息带 `Status: ` 前缀，已修正并复测通过，首次记录也保留。

切换前 r2 有 121 个就绪镜像、9 个环境错误、2 个正在拉取、168 个待处理。为启用新规则，主动终止旧准备进程及其两个拉取客户端，并单独登记中断，不把它们记为镜像源超时。旧目录保持不动。r3 先复核 121 个缓存镜像，再按仓库交错准备其余镜像；旧失败不会被覆盖。SWE 元数据和 TB 的 1818 个文件已重新校验后复用。

证据位于 `/mnt/data/agenttx-runtime/validation/`：

- `mirror-recheck-20261017/probes.json`：候选源及实际镜像抽样。
- `mirror-recheck-20261017/api-smoke-r2.json`、`api-smoke-r2.log`：修正后的 API 冒烟。
- `environment-interruption-diagnosis-20261017.json`：旧故障与服务重启诊断。
- `cutover-r2-to-r3-20261017.json`：切换前计数、主动中断任务及新旧进程。
- `environment-preparation-local-20261017-r2/`：原始批次，含旧失败记录。
- `environment-preparation-local-20261017-r3/registration.json`：新规则、顺序及脚本哈希。
- `environment-preparation-local-20261017-r3/<任务>/pull-progress.json`：当前实际下载／解压进展；各源 `.log` 保存引擎原始流。
