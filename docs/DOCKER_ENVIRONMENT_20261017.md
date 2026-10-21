# 本机 Docker 与实验环境准备

**完成后复核：2026-10-18 00:21（北京时间）。** 队列于 2026-10-17 23:30:32 正常完成，300/300 SWE 镜像通过内容核验。本机全部 300 个镜像身份与完成记录一致；进程与监控正常退出，不是异常中断。期间一次源空闲超时后自动切换 hub.rat.dev 并成功，旧失败保留。TB 镜像和完整任务环境仍待准备，模型实验保持暂停。复核证据：`/mnt/data/agenttx-runtime/validation/completion-recheck-20261018.json`。下文为队列完成时的监控快照。

核对时间：2026-10-17 23:31:01（北京时间）。

**代码修改、环境准备和核验均在本机 `r760-ssd`。远程准备队列已停止，远程只作参考代码来源；此前远程结果不计入本机进度。**

代码目录：`/home/bfq/agenttx-recovery-20261016/project`。镜像、缓存、结果、日志和临时文件位于 `/mnt/data/agenttx-runtime`。

本机 Docker socket：`unix:///run/agenttx-docker/docker.sock`；数据目录：`/mnt/data/agenttx-runtime/docker`。独立服务使用国内镜像，无 daemon 代理，原有 Docker 和其他项目容器未重启。本机 Alpine 完整拉取、断网容器、Docker 构建和运行通过；相关回归测试 18 项通过。

历史数据迁移完成：`True`。原缓存、日志和结果逐项校验后切为数据盘软链接。

当前阶段：`preparing_local_images`；准备进程存活：`False`；镜像状态：`{"image_ready": 300}`。最多 2 个镜像 worker，仅使用国内域名。根据下载/解压字节和完成层计数判断：900 秒无进展或单源总耗时 14400 秒才超时换源，连接错误立即换源；失败记录保留。镜像源顺序：docker.1ms.run、hub.rat.dev、docker.1panel.live。

当前拉取进展：`[]`。

此前批次状态：`{"image_ready": 121, "environment_error": 9, "preparing": 2, "pending": 168}`；旧记录完整保存在 `/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r2`，其中 preparing 表示切换时中断，见 validation 下 cutover-r2-to-r3-20261017.json。

TB 源码状态：`task_sources_hydrated`；本机已核验文件：1818。

数据盘可用：1038.9 GiB；监控告警：`[]`。独立观察进程每 60 秒检查准备进程、Docker、磁盘及任务日志，不自动重启失败尝试。

**镜像和源码就绪不等于完整任务环境就绪。** 干净 base commit、依赖、官方验收及正式协议门槛仍需完成，模型实验保持暂停。

入口：`source scripts/agenttx-local-env.sh`。准备脚本：`experiments/scripts/prepare_local_environment.py`；监控脚本：`experiments/scripts/watch_local_environment.py`。证据：`/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r3`；监控：`/mnt/data/agenttx-runtime/validation/local-monitor.json`。
