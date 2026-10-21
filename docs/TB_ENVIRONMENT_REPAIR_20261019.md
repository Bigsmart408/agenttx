# 本机 TB 环境修复与复验

**最新补充：** 2026-10-19 14:48，156项首轮官方流程检查全部结束：154项到达测试，2项因题目故意损坏的pip/网络无法bootstrap。当前192个任务已执行官方安装及测试（含2项正反验证），配套服务12/12；详见[清单](TB_USABLE_TASKS_20261019.md)。本轮环境进程已正常退出，TB模型全量尚未启动。

**后续批次：** 2026-10-19 11:26，`tb-basic-official-validation-20261019-r1` 已启动156项首次官方脚本验证，2路，900秒环境诊断上限及350GiB磁盘派发保护；两个容器实际运行。修复监控更新为 r8，SWE 八路不受影响。

**最新补充：** 四项 r6 安装超时修复全部运行到官方测试（110–347 秒）；Spring r5 修复 root systemd 的 Compose 插件发现后已通过启动/客户端连通，配套服务达到 12/12。当前逐层可用性与证据见 [TB 可试跑清单](TB_USABLE_TASKS_20261019.md)，38 个任务已运行官方安装及测试，六策略模型闭环尚未完成。

更新：2026-10-19 10:09（北京时间），机器 r760-ssd。各阶段均保留原始结果；当前进展见 latest.json。修复和运行均在本机，未重启 Docker。

## 已修复并实际验证

- 原来四项事务适配失败现均有通过证据。accelerate-maximal-square 使用旧 util-linux，补充等价 chroot 路径；单独构建静态 strace 6.13，解决其 glibc 2.27 兼容问题。旧静态编译失败及全部探针失败保留。
- privilege-escalation、slurm-simple-node-monitoring、unprivileged-headless-pyrender 使用原任务账号执行工具。root 仅负责 namespace 和事务协调；跟踪工具通过 strace -u 降权，不跟踪的工具通过 runuser 降权。三个任务均核验 uid/gid/HOME、无法读取 root 私有文件、隔离写入、回滚、提交后的文件属主及保存/加载会话后仍是原账号。
- 同时修复 try 新建顶层 overlay 目录时漏保留属主，以及快照复制/回滚只复制 mode、丢失 uid/gid 的问题。快照现在保留普通文件、目录、符号链接属主及 set-id 位。硬链接和选择性回滚相关回归通过。
- 实际 TB 镜像内执行脚本改用兼容 tracer，保留镜像自带 git；命令追踪临时文件放 /var/tmp。非 root 与旧 glibc 适配需显式选择参数，不能把任务账号直接改成 root。
- download-youtube 的官方验收导入 OpenCV 时缺少 libGL，补齐 libgl1/libglib2.0-0 后，原版脚本已运行至正常未解题断言失败。
- 实际运行脚本也已支持 run-tests.sh 内嵌官方测试补丁的任务，保留原脚本而不伪造独立 tests 目录。
- 实际官方验收脚本准备器现在匹配 uv 的原版本（含 0.8.5），生成有效的 env 文件，并使用国内 APT/Python 源；未找到匹配 uv 时国内源模式在模型调用前报错。未固定版本的上游 uv 安装脚本在此诊断中明确锁到 0.7.13。依赖版本、解释器选择、CPU PyTorch 专用源和测试调用保持原要求。

## 测试脚本与后续修复

45 项原版安装/测试脚本：已完成 45/45，原始分类 {"baseline_failed": 27, "timeout": 9, "test_setup_or_collection_error": 7, "baseline_passed": 1, "no_test_report": 1}。另有 download-youtube 修复批次通过验收环境复验。测试断言失败是未解题基线的正常结果，不代表任务解题成功。

若 collection/setup 失败来自题目要求创建的文件或软件，这属于未解题基线，不应“修环境”时提前代做。例如 grid_transform、pcap_to_netflow.py、QEMU 编译产物、lm-evaluation-harness 和 MAGSAC 安装均涉及任务目标。此类错误保留待逐项审计，不能凭分类名一概补答案。

两项 HF 安装在首轮 900 秒诊断上限内未完成约 3 GiB 的 torch 2.7.1/CUDA 依赖下载；已换阿里云 PyPI 镜像，以相同版本重新验证，环境诊断上限独立登记为 1800 秒；两项分别在 329 秒和 275 秒内完成安装并运行至正常未解题断言失败，复验通过。未替换 CPU 构建或放宽模型实验限时。

配套服务首轮 12/12 已结束，8 项通过、4 项失败。独立修复 Celery、MinIO、Postgres/simple-sheets-put 均通过，累计 11/12 个不同任务通过启动及客户端连通检查。剩余 Spring 的基础镜像在 docker build 阶段超过原 900 秒上限；已启动 `tb-sidecar-spring-base-repair-20261019-r4`，先按 Docker API 字节/空闲进度拉取，再以 immutable image ID 构建。服务连接检查尚不等于完整任务验收。

首轮 Celery 镜像诊断使用 300 秒总上限，三个源均未完成，不能据此判断版本不存在。修复后的重拉复用已有 Docker API 字节监控：900 秒无字节/解压/完成层进展才换源，单源总上限 14400 秒，明确网络错误立即换源。Celery 重拉及服务连通复验已经通过，目前与其他修复合计 11 个不同配套服务任务有通过证据。进度 JSON 记录源、下载量、解压量和空闲时间。

其余配套服务若同样因旧的 300 秒拉取上限失败，独立后续队列会只对这种已知环境故障执行一次字节监控重拉；成功服务不重复，未知构建/测试错误不作伪装。后续队列证据为 `tb-sidecar-byte-repairs-20261019-r3`。

sam-cell-seg 的 r5 已完成：源码固定提交 `34bbbfdface3c18e5221aa7de6032d7220c6c6a1`，全部 275 个 Git blob 验证通过。依赖安装与官方测试已运行，9 项断言失败、0 个测试错误，是未解题基线；不等于已解题。原上游 master 权重代理下载摘要为 `6dbb90523a35330fedd7f1d3dfc66f995213d81b29a5ca8108dbcdd4e37d6c2f`，记录摘要本身不代表独立权重身份校验。

新增索引优先级修复：真实 uv 0.7.13 + 本地 HTTP 复现表明，UV_DEFAULT_INDEX 会覆盖命令行旧式 --index-url，导致原本显式 CPU 源的诊断实际从国内默认 PyPI 解析 CUDA 包。现在取消该变量（含继承值），只设置 UV_INDEX_URL，使显式源生效。原结果与问题记录保留。已启动 `tb-bootstrap-index-repair-20261019-r6`，只补跑 pytorch-model-cli、torch-pipeline-parallelism、torch-tensor-parallelism、triton-interpret 四个经日志确认停在下载、尚未进入 pytest 的环境诊断；国内默认换阿里云，版本和显式 CPU 源不变，上限独立登记 1800 秒。predicate-pushdown-bench、neuron-to-jaxley-conversion、video-processing 的超时已进入测试，未混作下载失败重试。33 项相关回归检查通过，含实际本地 HTTP 请求路径及服务基础镜像先拉后构建。

所有旧失败、超时和日志保持原样。没有 TB 模型调用，没有使用 gold solution。原来的 22 个未建主镜像另属后续准备范围。本报告不能被用作全量 TB 环境已完成或正式模型实验已就绪的证明。

## SWE 队列修复

先审计并修复了把模型误填 63 位镜像 digest 当基础设施损坏的监控误报；原任务最终通过，结果和模型开销保留。

随后发现同一题多个策略在共享缓存中并发应用 test_patch，造成两例零 token / 零模型调用启动失败。调度器现在先串行完成 ensure_repo，再创建策略 worker；仅对有哈希、补丁反向校验和零调用证明的两例环境失败重跑。它们均已成功，其他任务结果不重跑。

当前 SWE：{"completed": 1359, "running": 8, "pending": 433}，告警 []。执行代码冻结在 selective-r9；TB 的新运行时修复未混入正在跑的 SWE 核心。900 秒会话计时偏差等既有实验限制仍保留，formal_eligible=false。

## 验证和证据

132 项运行时/快照/硬链接/恢复/调度/监控回归通过；新增官方 bootstrap 相关检查 28 项通过；Docker 字节进度监控 7 项通过。各组部分重叠，不能相加当作唯一测试数量。git diff --check 通过。

- 总监控：`/mnt/data/agenttx-runtime/validation/tb-environment-repair-20261019/latest.json`，30 秒更新；history.jsonl 保留历史快照。
- 四项事务证据：`tb-adapter-repair-20261019-r1/accelerate-maximal-square`、`tb-user-adapter-repair-20261019-r6/*`。
- 原版安装脚本：`tb-bootstrap-repair-20261019-r1`。
- OpenCV 修复：`tb-bootstrap-fix-libgl-20261019-r2`。
- MobileSAM 固定提交归档复验：`tb-bootstrap-sam-archive-repair-20261019-r5`（已运行官方测试）；r4 失败保留。
- Spring 预拉修复：`tb-sidecar-spring-base-repair-20261019-r4`；其余四项下载及 CPU 源优先级复验：`tb-bootstrap-index-repair-20261019-r6`（进行中）。
- HF 下载换源复验：`tb-bootstrap-wheel-mirror-repair-20261019-r3`。
- 服务原始批次：`tb-sidecar-repair-20261019-r1`；Celery 字节监控复验：`tb-sidecar-pull-repair-20261019-r2`。
- SWE 补丁竞争审计：`swe-repo-bootstrap-repair-20261019.json`，短 digest 审计：`swe-short-digest-audit-20261019.json`。

以上未带绝对前缀的证据路径均位于 `/mnt/data/agenttx-runtime/validation/`。代码及冻结执行副本保留系统盘，镜像、工具包、编译产物、缓存、日志和结果位于数据盘。
