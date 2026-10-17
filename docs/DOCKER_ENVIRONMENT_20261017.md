# 本机 Docker 与实验环境准备

核对时间：2026-10-17 16:15:27（北京时间）。

**代码修改、环境准备和核验均在本机 `r760-ssd`。远程准备队列已停止，远程只作参考代码来源；此前远程结果不计入本机进度。**

代码目录：`/home/bfq/agenttx-recovery-20261016/project`。镜像、缓存、结果、日志和临时文件位于 `/mnt/data/agenttx-runtime`。

本机 Docker socket：`unix:///run/agenttx-docker/docker.sock`；数据目录：`/mnt/data/agenttx-runtime/docker`。独立服务使用国内镜像，无 daemon 代理，原有 Docker 和其他项目容器未重启。本机 Alpine 完整拉取、断网容器、Docker 构建和运行通过；相关回归测试 18 项通过。

历史数据迁移完成：`True`。原缓存、日志和结果逐项校验后切为数据盘软链接。

当前阶段：`preparing_local_images`；准备进程存活：`True`；镜像状态：`{"image_ready": 125, "pending": 173, "preparing": 2}`。最多 2 个镜像 worker，仅使用国内域名。根据下载/解压字节和完成层计数判断：900 秒无进展或单源总耗时 14400 秒才超时换源，连接错误立即换源；失败记录保留。镜像源顺序：docker.1ms.run、hub.rat.dev、docker.1panel.live。

当前拉取进展：`[{"task": "pydata__xarray-3364", "wall_s": 420.78, "idle_s": 1.001, "downloaded_bytes": 581238816, "extracted_bytes": 0, "last_event": {"status": "Downloading", "layer": "d0e25e76b4c8"}, "reason": null, "error": null, "mirror": "docker.1ms.run", "observed_at": "2026-10-17T08:15:21.231510+00:00"}, {"task": "pylint-dev__pylint-5859", "wall_s": 319.348, "idle_s": 0.0, "downloaded_bytes": 243236864, "extracted_bytes": 0, "last_event": {"status": "Downloading", "layer": "40a3ace33efe"}, "reason": null, "error": null, "mirror": "docker.1ms.run", "observed_at": "2026-10-17T08:15:23.021051+00:00"}]`。

此前批次状态：`{"image_ready": 121, "environment_error": 9, "preparing": 2, "pending": 168}`；旧记录完整保存在 `/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r2`，其中 preparing 表示切换时中断，见 validation 下 cutover-r2-to-r3-20261017.json。

TB 源码状态：`task_sources_hydrated`；本机已核验文件：1818。

数据盘可用：1172.0 GiB；监控告警：`[]`。独立观察进程每 60 秒检查准备进程、Docker、磁盘及任务日志，不自动重启失败尝试。

**镜像和源码就绪不等于完整任务环境就绪。** 干净 base commit、依赖、官方验收及正式协议门槛仍需完成，模型实验保持暂停。

入口：`source scripts/agenttx-local-env.sh`。准备脚本：`experiments/scripts/prepare_local_environment.py`；监控脚本：`experiments/scripts/watch_local_environment.py`。证据：`/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r3`；监控：`/mnt/data/agenttx-runtime/validation/local-monitor.json`。
