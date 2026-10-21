# TB 已建镜像验证结果

核验完成：2026-10-18 22:29（北京时间），本机 r760-ssd。

已对 219 个就绪镜像完成首轮无模型诊断及定向复核；其中 215 个通过原镜像内真实事务写入隔离、依赖追踪和回滚。镜像和上游测试源文件未改写，全部原始失败保留。

| 分类 | 数量 |
|---|---:|
| 官方脚本正反验证通过 | 2 |
| 基础事务及测试探针跑通 | 156 |
| 测试 bootstrap 或特殊入口待适配 | 45 |
| 配套服务待补齐 | 12 |
| 事务执行适配受阻 | 4 |

基础探针使用原镜像 Python 运行原版测试文件，最多运行到首个失败，限时 45 秒。正常未解题失败仅说明验收确实检测到缺失结果，不等于解题成功，也不足以证明完整官方安装及运行环境有效。部分失败属于探针自带依赖与旧 Python 不兼容，不能据此宣布任务镜像损坏。

`hello-world` 和 `csv-to-parquet` 额外执行实际官方安装/测试脚本：保持 uv 0.7.13 和依赖版本要求，uv 安装器改用相同版本本地二进制，APT/Python 包使用国内镜像。原始状态各有 2 项测试失败；按公开题目指令生成结果后各有 2 项测试通过。没有读取或挂载 solution.sh，没有模型调用。

实际发现并修复了宿主 strace 依赖新 glibc 的兼容问题：验证工具改用国内 Debian 镜像下载的旧版本 strace/libunwind，保留包 SHA256。Docker 专用守护进程关闭 bridge 网络，安装依赖的两项验证使用 host 网络访问国内源；未重启守护进程，SWE 八路实验保持运行。

4 个执行适配问题：accelerate-maximal-square 的旧 unshare 工具；privilege-escalation、slurm-simple-node-monitoring、unprivileged-headless-pyrender 使用非 root 镜像用户，当前事务适配器需要 root。没有强改这些任务的用户。

三个没有独立 tests 目录的任务 swe-bench-astropy-1/2 和 swe-bench-langcodes，在 run-tests.sh 内嵌官方测试补丁，源文件完整，已补验事务能力；完整验收需运行其专用脚本。

12 个需配套服务的任务：ancient-puzzle, debug-long-program, find-restaurant, home-server-https, interactive-maze-game, postgres-csv-clean, security-celery-redis-rce, security-vulhub-minio, simple-sheets-put, simple-web-scraper, spring-messaging-vul, sql-injection-attack。

本轮 14 项相关回归测试通过。未启动 TB 模型实验，验证结果不填入正式实验结果矩阵。剩余 22 个未建镜像仍在此范围之外。

汇总 JSON：/mnt/data/agenttx-runtime/validation/tb-ready-validation-summary-20261018.json

原始批次：/mnt/data/agenttx-runtime/validation/tb-ready-validation-20261018-r1；定向复核：/mnt/data/agenttx-runtime/validation/tb-ready-validation-20261018-r2；官方脚本验证：/mnt/data/agenttx-runtime/validation/tb-official-bootstrap-20261018-r2。
