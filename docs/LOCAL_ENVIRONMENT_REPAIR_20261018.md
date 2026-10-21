# 本机运行环境修复与重新启动

**修复后真实启动验证全部通过，2026-10-18 01:31（北京时间）。** 本机新冻结批次 r3 的 clean、不回滚、时间检查点、整分支回滚、causal 五种策略均成功完成提交并通过官方验收（5/5），环境错误和宿主写入泄漏均为 0。87 项相关回归测试通过；TB 两项原下载失败已独立修复，当前去重后 53/241 镜像就绪，仍未修复的环境错误 0 个，主队列和后台监控继续。五项启动验证已按预登记上限正常结束；正式全量仍需共享 token 硬预算、提示计数匹配及跨框架协议检查，不能把本批 5 项记成正式覆盖。完整证据：`/mnt/data/agenttx-runtime/validation/swe-local-launch-success-20261018.json`；详见[修复报告](LOCAL_ENVIRONMENT_REPAIR_20261018.md)及[实时监控](LOCAL_CAMPAIGNS_20261018.md)。

更新时间：2026-10-18 01:25（北京时间）。执行机器为 r760-ssd。

本轮修复后，新冻结批次 SWE-agent r3 已启动。它保留固定请求任务的五策略启动验证，不把旧失败改为成功，也不将环境验证计入正式覆盖。代码和冻结副本在系统盘；镜像、缓存、结果、日志和临时文件在 `/mnt/data/agenttx-runtime`。

| 问题 | 修复 | 实测证据 |
| --- | --- | --- |
| 数据盘工作目录在 try 内不可见 | 使用数据盘绑定的 `/tmp` 别名 | 事务内依赖导入、写入隔离、提交通过 |
| 沙箱内 Docker socket 不可连接 | 显式绑定活 socket | 事务内查询固定官方镜像身份通过 |
| 验收元数据软链接指向不可见的数据盘目录 | 发布独立只读公共元数据缓存，移除 gold patch 和 hints_text | 全部 300 个任务元数据可本地读取，SHA256 a5857d184a0b7a5af7d2a76699cd88d1ca8b39cbc8540ea2f984b866a022436f |
| requests 测试依赖 httpbin，断网容器不能访问外部服务 | 构建固定镜像身份的本地 httpbin fixture；验收容器共享该服务的断网命名空间 | 原始源码 1 failed / 6 passed；上一批模型生成的补丁 7 passed |
| 回滚恢复 upperdir 后复用旧 OverlayFS workdir/index，报 Stale file handle | 恢复后重建挂载工作目录与视图，保留版本化 upper 内容 | 回滚后带 strace 的 git 命令正常；依赖、选择性回滚、提交等 87 项测试通过 |
| 验收程序删除运行中的 .venv/.tmp | 仅在打包复制到镜像时排除这些目录，保持运行中的解释器和临时状态 | 同一沙箱内连续两次完整验收及提交后命令通过 |
| 启动监控把 agent 侧环境错误漏计成普通 completed | 增加 worker 日志的 Docker、验收、依赖跟踪、集成错误检查 | 原 r2 结果不改，独立 postrun-audit 标注两项集成错误 |

完整无模型检查通过：依赖可见、事务内 Docker 可见、写入不泄漏、回滚、回滚后命令、原代码真实失败、修复后全部 7 个测试通过、提交、提交后继续执行。用于修复通过检查的补丁来自旧 r2 模型提交，仅用于独立环境诊断；新 r3 的模型工作区不预置它，也不使用 benchmark gold。

SWE r3 源码指纹：`c0cea4f2cbbb38aa5ab40f737202251c1f5e3296eea9ec3001e5b2f312cee1bd`。
固定官方验收镜像：`sha256:287f118ccaa9c0ba39e5f361550bd845d28933f34483861b8deaa4992f9d8d92`。
HTTP fixture 镜像：`sha256:462caaba497ce310ec1584e6e6021e66710570bcd815b2b7df80713b8d14683b`。

SWE 仍为 `formal_eligible=false`：本批验证环境与模型执行，不将它计入正式论文覆盖；共享 token 硬预算、策略提示计数匹配和跨框架协议核验仍是正式门槛。会话 900 秒，无步数／调用次数 cap。先 clean，再并行其余四策略；五项结束后停止扩张，真实任务失败保留。

TB 主队列继续。两项已知下载失败在独立修复批次修好：bn-fit-modify 的 CSV 校验 Git blob `9d2ff98d09f536bc6182baa1b58d8b726ee94db2`；build-stp 固定提交 `7a3fd493ae6f0a524f853946308f4f3c3ddcbe76` 的 537 个源码文件全部核对 Git blob。两个镜像均通过断网内容检查，旧队列失败记录保留；实时监控计算去重后的就绪数和仍未修复的错误。

证据均在数据盘：

- `validation/swe-workspace-mount-preflight-20261018-r7.log`：完整成功检查；r4/r5/r6 失败诊断另行保留。
- `validation/swe-workspace-mount-preflight-20261018-r4.json`：最终完整成功证明（由 r7 日志对应的执行生成）。
- `validation/swe-httpbin-verifier-20261018-r1.json`：原始源码与上一批模型补丁的对照；无模型调用、无 gold。
- `validation/swe-environment-repair-tests-20261018-r2.log`：87 passed。
- `validation/httpbin-fixture-20261018-r1/`：Dockerfile、构建日志和镜像指纹。
- `validation/tb-network-repairs-20261018-r1/`：两项独立修复和校验。
- `results/sweagent-swe-local-monitored-20261018-r3/`：新模型批次。

[实时监控](LOCAL_CAMPAIGNS_20261018.md) 每 60 秒刷新，报告真实结果及环境异常；进程存活不单独视为实验正常。
