# 本机 Docker 与实验环境准备

核对时间：2026-10-17 15:51:26（北京时间）。

**执行位置已改正：代码修改、准备和核验均在本机 `r760-ssd`。远程环境队列已停止，远程仅作为参考代码来源。此前远程 61 个就绪镜像和 TB 核验不计作本机进度。**

代码目录：`/home/bfq/agenttx-recovery-20261016/project`。Docker、缓存、结果、日志和临时文件位于 `/mnt/data/agenttx-runtime`；项目原有数据目录迁移并校验后保留软链接。

本机专用 Docker：`unix:///run/agenttx-docker/docker.sock`；数据根目录：`/mnt/data/agenttx-runtime/docker`。使用国内镜像源，无 daemon 代理；原有共享 Docker 和其他容器未重启。独立 daemon 禁用 bridge/iptables，需联网的构建和 native agent 显式使用 host 网络，验收可使用 none 网络。

当前镜像阶段：`{"image_ready": 121, "preparing": 2, "pending": 177}`。最多 2 个环境 worker；按 Docker API 下载/解压字节与完成层判断进展，连续 900 秒无进展或单源总时长 14400 秒才中断换源；连接错误立即换源。保留此前失败记录，不直接回退 Docker Hub。候选源：docker.1ms.run, hub.rat.dev, docker.1panel.live。

当前 TB 源码状态：`task_sources_hydrated`；已验证文件：1818。

此前 r2 状态：`{"image_ready": 121, "environment_error": 9, "preparing": 2, "pending": 168}`，完整记录保存在 `/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r2`；本批重新核验缓存并按仓库交错准备剩余镜像。

可用数据盘：1200.3 GiB。当前错误：`[]`。

**镜像就绪不等于任务环境就绪。** 干净 base-commit 源码、依赖、官方验收及协议门槛仍需完成；模型实验保持暂停。

本机监控每 60 秒更新。完整证据和进度：`/mnt/data/agenttx-runtime/validation/environment-preparation-local-20261017-r3`。
