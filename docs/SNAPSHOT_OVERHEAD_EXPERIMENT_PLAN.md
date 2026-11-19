# 每步快照与文件系统版本管理开销实验计划

2026-11-15最新核对：OverlayFS内核同步诊断、可选metacopy优化及try三个入口的补测已完成，392个主批次会话与24个配对会话全部通过。最终代码通过35项真实OverlayFS测试，结果见[最终结论](../review/rq5-overlay-try-audit-20261115/FINAL.md)和[逐负载分项](../review/rq5-overlay-try-audit-20261115/RESULTS.md)。本轮补齐执行至完整收尾与包含初始化的会话指标，不将它们与原单独提交接口计时混用。

状态：以下为原始计划。按2026-11-15的用户要求，RQ5主比较调整为直接测量快照与文件系统提交接口，复用YoloFS仓库原始固定负载；完整命令执行、写集发现、依赖记录和错误定位不纳入这两个接口的计时。最新补测增加try提交，统一OverlayFS和AgentTX的lower、upper布局、挂载参数、外层计时与快照完成条件：返回时历史版本不可变且可直接挂载，AgentTX的历史目录展开计入快照创建时间。每配置预热1次、测量5次，结果和设置见[try与统一快照补测](../review/rq5-try-comparison-20261115/RESULTS.md)及[设置核对](../review/rq5-try-comparison-20261115/SETTINGS.md)。上一轮排除历史目录展开的存储接口结果和此前完整文件记录通道的结果分别保留，正式批次继续暂停。

论文对应关系：RQ1 为 Microbenchmarks，RQ2 为 Real-World Workloads；RQ4 分析运行时各阶段和历史记录开销，RQ5 单独分析文件提交相对原生 Ext4、OverlayFS 叠层快照和 YoloFS 的额外成本。E3 是 RQ5 的核心实验，E4 补充完整工作流；新结果在实测完成后填入。

## 1. 要回答的问题

1. AgentTX 每个工具调用对应一个恢复位置，实际增加多少延迟、复制多少数据、占用多少历史空间？
2. 相同 Ext4 底层、相同命令和恢复边界下，AgentTX 与 YoloFS、基于 OverlayFS 叠层的快照方案相比如何？
3. 快照积累、已暂存文件增多、单次修改增大时，开销如何变化？
4. 在十万文件提交和真实内核开发流程中，这些成本占完整执行时间的多少？

这些实验使用固定命令，不调用 agent 或 LLM，不注入故障，不搜索候选错误。比较相同文件状态的历史保存与恢复能力。AgentTX 的选择性回滚收益继续由 RQ1/RQ2 评价。

## 2. 已核对的论文与仓库事实

### 2.1 固定来源

- [YoloFS SOSP 论文](https://doi.org/10.1145/3830418.3843858)：§5.3、§7，Figure 12/13，PDF 第 9、13–14 页。
- [文件系统仓库](https://github.com/YoloFS/YoloFS/tree/8848c243ba28ff91432c1da868671d6ed8910ed6)：`8848c243ba28ff91432c1da868671d6ed8910ed6`。
- [性能实验仓库](https://github.com/YoloFS/perf-eval/tree/bc32d0835ed8b9d28a799e59574f31855999a89a)：`bc32d0835ed8b9d28a799e59574f31855999a89a`。
- [公开结果仓库](https://github.com/YoloFS/perf-results/tree/7b6e3b4fa6737ea65c3128dd16911363a3b8aa57)：`7b6e3b4fa6737ea65c3128dd16911363a3b8aa57`。

下载内容、选取的源文件和校验记录放在 `review/snapshot-overhead-plan-20261108/`。仓库源码与公开结果分别固定版本；不假定该结果恰由当前源码提交生成。

### 2.2 YoloFS 实际如何创建快照

[`user/cmd/run.rs`](https://github.com/YoloFS/YoloFS/blob/8848c243ba28ff91432c1da868671d6ed8910ed6/user/cmd/run.rs#L206) 确认：`auto_snapshot` 默认在命令结束后请求快照，使用 `YOLO_SNAPSHOT_IF_CHANGED`；没有暂存修改时跳过空快照。显式 `yolo snapshot` 可以请求一个独立快照。

[`kmod/ioctl.c`](https://github.com/YoloFS/YoloFS/blob/8848c243ba28ff91432c1da868671d6ed8910ed6/kmod/ioctl.c#L229) 确认：快照检查状态、推进 generation 并追加 journal 标记。后续修改旧 generation 的文件时再触发 COW。因此必须同时测快照调用和之后的写入，不能只计 ioctl。

### 2.3 Figure 12 的具体负载

[`checkpoint_scaling.rs`](https://github.com/YoloFS/perf-eval/blob/bc32d0835ed8b9d28a799e59574f31855999a89a/src/workloads/checkpoint_scaling.rs) 创建 100 个读取目标和 10 个种子文件，每个文件 4 KiB。每层覆盖 10 个种子文件后创建快照，再测 100 次创建、100 次读取，或最终 commit/status。

这里控制的是历史深度，不能用它单独回答“已暂存十万文件时，创建一个新恢复位置有多贵”。需要增加文件规模轴。

### 2.4 Figure 13 的具体负载与快照频率

[`dev_workflow.rs`](https://github.com/YoloFS/perf-eval/blob/bc32d0835ed8b9d28a799e59574f31855999a89a/src/workloads/dev_workflow.rs) 和 [`overlayfs-ovl-file.json`](https://github.com/YoloFS/perf-eval/blob/bc32d0835ed8b9d28a799e59574f31855999a89a/fixtures/dev-workflow/overlayfs-ovl-file.json) 确认：

- 固定 Linux 基线 `c2c54b5f34f6^`，复现五个真实 OverlayFS 补丁。
- 创建 worktree；安装带 `CONFIG_OVERLAY_FS=y` 的 tinyconfig 并构建；逐补丁执行 search、read、atomic edit、增量构建和 git 操作；最后提交暂存状态到底层文件系统。
- 性能后端设置 `auto_snapshot: false`，由 benchmark 显式安排快照。
- 快照位于 worktree、config、初始构建、每条 atomic edit、每次增量构建及每次 git commit 后；search/read 不逐条打快照。
- 已发布记录含 149 个计时条目，其中 54 个为快照事件：3 个初始事件、41 个编辑事件、5 个增量构建事件和5 个 git commit 事件。149 不是工具调用数量。

论文的“提交十万多个文件额外约 3.5 秒”来自该完整流程的最终文件系统 commit，不是每步快照，也不是 git commit。另有 `write-files-100k` 等合成负载可用于文件数扩展实验。

公开 `report/results.json` 的 `dev-workflow` 单次记录如下：

| 后端 | 快照数 | 快照累计时间 | 最终 FS commit | 总时间 |
|---|---:|---:|---:|---:|
| Ext4/native | 54 个空操作边界 | 0 ms | 不适用 | 45.268 s |
| YoloFS/realistic | 54 | 56 ms | 3.667 s | 50.104 s |
| OverlayFS | 54 | 7.211 s | 2.641 s | 58.861 s |

这些值是作者机器上的已发布单次记录，用于核对负载与计时口径。源码把该快照时间转成整数毫秒，不能据此推导微秒级分布。我们的实验改用纳秒单调时钟并保留原始值。

### 2.5 OverlayFS 对照实际怎么做

[`src/backends/overlayfs.rs`](https://github.com/YoloFS/perf-eval/blob/bc32d0835ed8b9d28a799e59574f31855999a89a/src/backends/overlayfs.rs) 确认：将当前 upper 冻结为只读 lower，新建 upper/work，然后卸载、重新挂载。最终按旧到新顺序将各层修改应用到同一 Ext4 lower；同文件系统普通文件优先 rename，删除标记和 opaque 目录另行处理。

因此名称应写作“OverlayFS 叠层快照”，而非把 OverlayFS 本身当作具有原生 snapshot API 的文件系统。可达层数取决于挂载参数长度、路径、内核及实现；实测到哪一层失败就记录哪一层，不能硬编码 50。

## 3. 对照组与公共环境

主要展示 Ext4、OverlayFS 叠层快照、YoloFS、AgentTX；以下配置用于拆开具体来源：

| 配置 | 执行方式与作用 |
|---|---|
| Ext4/native | 直接执行相同命令；作为无历史保存的执行基线，快照与最终 FS commit 标为不适用 |
| OverlayFS，无历史保存 | 同一 merged view，只执行命令；衡量增加版本记录前的成本 |
| OverlayFS，叠层快照 | 复用作者的 upper→lower、增加 upper/work、卸载重挂载及最终 flatten 方案 |
| YoloFS，关闭权限检查 | `permission=false`、显式统一边界；比较文件版本保存本身 |
| YoloFS，启用权限检查 | `permission=true`、工作负载所需路径全部预先 allow；复现论文 realistic 配置 |
| AgentTX，文件记录路径 | 运行现有文件变化检测、历史保存与必要记录；不启用 agent/LLM、错误搜索和上下文推理 |
| AgentTX，完整记录路径 | 同一命令，经正常工具调用记录依赖和调用/结果历史；单列额外开销 |

AgentTX 文件记录路径使用当前运行时的变化检测，不把生成器提供的 changed-path truth 当成运行时输入来获得不现实的低开销。底层 `snapshot_before` 的独立计时另报。

公共条件：

- 相同机器、内核、CPU 配额、构建并行度、数据和执行顺序。lower、upper、work、历史 blobs、YoloFS staging 及提交目的地全部位于同一块 Ext4 文件系统，记录设备、挂载参数和文件系统特性。
- 当前项目所在文件系统已确认是 Ext4，内核为 `6.8.0-147-generic`。实际性能测试使用资源独占的测试时段或隔离测试机，避免与正在运行的 Codex 主实验争用磁盘/CPU。若使用 VM，所有后端都在同一 VM 的 Ext4 中重跑。
- 固定以上源码提交和 AgentTX 提交/工作树 hash；上游兼容性补丁只调整适配和纳秒计时，保存完整 diff。`perf-eval` 的 Cargo 路径依赖要求文件系统源码位于相邻的 `filesystem/`，按这个目录关系准备。
- 微实验以热缓存为主，首次准备开销另列；冷缓存仅在专用环境补测，不在当前主实验主机上全局清缓存。网络下载、编译工具本身和准备初始副本不进入运行计时。
- 一次独立预热后，微实验每配置至少 10 个独立 session；内核流程至少 5 次。按 workload/seed 配对，随机轮换后端顺序。初始快照不作为预热丢弃，而是单独报告并计入该 session 总时间。
- 主表采用普通 buffered execution，与原 benchmark 对齐；若补测持久化成本，所有后端统一执行并计入 `syncfs` 等收尾，不能把普通返回延迟和落盘延迟混在一起。

## 4. 计时与计数

保留以下原始阶段，使用 `perf_counter_ns` 或等价单调时钟：

1. 会话建立：namespace、mount、worker 初始化。
2. 命令执行：包含真实系统调用和运行时开启的 tracing。
3. 文件变化识别：upper 扫描、指纹/hash 等。
4. 历史保存：`snapshot_before`、YoloFS 快照调用、OverlayFS freeze/remount。
5. 依赖与上下文记录：ledger、调用/结果日志、元数据持久化。
6. 统一历史点恢复或最终 FS commit；清理另列。

主指标包括每步完整延迟、恢复位置准备延迟、纯快照原语延迟（p50/p95/p99）、首次后续写入延迟、总工作流时间、实际复制字节、历史物理占用、目录/文件条目数和挂载层数。父阶段与内部计时不能重复相加。

AgentTX 执行前保存状态、YoloFS 通常执行后打快照。统一逻辑边界为“第 i 条命令结束后的可恢复状态”；AgentTX 的下一次 before-image 归到这个边界，最后一条命令后显式 seal，避免漏计最后一个状态。只读命令的边界请求、跳过/复用和实际新建版本数量分别计数。

YoloFS 纯 ioctl 与 CLI 完整调用分开报；OverlayFS remount 原语与其 CLI/sudo 调用也分开报。跨系统主比较使用完整可用边界成本，不能拿一个系统的内核原语去比另一个系统的完整 CLI。

## 5. 实验矩阵

### E1. 单步保存历史的实际成本（核心新增）

每个 session 先在暂存区构造 M 个 4 KiB 文件，然后执行 30 条固定命令，每条命令后请求一个恢复边界。每次修改写入可区分的版本内容。30 步用于四组共同完成的主比较，另以 100 步测试连续运行；OverlayFS 如提前达到挂载限制，记录失败位置，不用部分执行时间参与完整 session 比较。适配预检若发现有后端无法完成 30 步，在正式计时前统一冻结所有后端都能完成的步数，并保存修改原因。

采用三个相互独立的参数扫描，避免无意义地全笛卡尔积：

- 已暂存文件量：`M = 1,000 / 10,000 / 100,000`，每步修改 10 个文件。
- 单步修改量：`K = 0 / 1 / 10 / 100`，`M = 10,000`；K=0 使用实际只读命令。
- 文件大小：只改变一个目标文件为 `4 KiB / 1 MiB / 64 MiB`，其余文件保持 4 KiB，分别覆盖写和追加 4 KiB。

同时给出冷启动的第一个快照、后续快照和第一次跨版本写入的成本。只有真正写入变化时才按后端正常语义创建独立版本；不强迫只读命令全量复制，也不把“跳过”记成原语耗时为零。

输出：每步 snapshot、变化识别、其余记录的分解；完整“命令+恢复边界”相对 Ext4 的增量；复制量与历史空间。此组直接回答“每步打快照究竟有多贵”。

### E2. 连续快照的扩展性

先复现作者的 100 个读取目标、10 个种子文件和每文件 4 KiB 配置。每层覆盖 10 个种子文件并保存历史，深度 `S = 1 / 10 / 20 / 30 / 40 / 50 / 60 / 100 / 250 / 500`。

在各深度测：新快照、100 次创建、100 次读取旧目标、同样目标上的 stat/readdir，以及最终 commit。深度扩展主实验固定文件集合，避免把文件增多误认为层数效应；另以 `M=10,000、S≤100` 补一个已暂存规模压力点。

OverlayFS 达到挂载限制时记录请求深度、实际完成深度、错误和 mount 参数长度；后续点标失败/不适用。完整工作流比较只使用完成同一深度的运行，扩展性图保留失败位置。

输出：延迟随深度的曲线、可达深度、历史空间和挂载层数。验证 AgentTX 历史不进入挂载层的效果，同时显示它是否存在目录/记录量增长成本。

### E3. 十万文件规模的最终提交

采用作者 `write-files`、`overwrite-files`、`rename-files`、`unlink-files` 的操作语义，文件数 `F = 1,000 / 10,000 / 100,000`，每文件 4 KiB，创建与覆盖是主组，rename/delete 是补充组。

两种模式：

- 作者式单批操作：一次完成 F 个操作后提交，隔离大规模 FS commit。
- 固定 10 个历史边界：将 F 个操作均分成 10 批，每批后保存历史，最后提交。保持快照数不变来扫描文件量。

目录布局使用作者的平坦目录作复现，并为十万文件加“每目录 100 文件”的补充点，记录布局。所有后端提交到同设备 Ext4，普通文件可用 rename；rename/copy 实际计数。Ext4 直接执行对应操作，没有独立 commit 阶段。

输出：执行、快照、最终 commit 和清理分别耗时；总时间相对 Ext4；提交的实际路径数、文件数、字节数。明确区分 FS commit 与 git commit。

### E4. 固定 Linux 内核开发流程

复用作者 `dev-workflow` 的五补丁 fixture、起始 commit、tinyconfig、search/read/edit 命令和 git 操作；不重新选一套容易的命令。统一构建并行度，并在各后端校验每个补丁后的源代码内容与构建结果。

- **原协议复现组**：沿用 54 个显式快照事件，完整记录 worktree、初始构建、读、编辑、增量构建、git 操作、快照及最终 FS commit。
- **逐调用边界组**：从同一命令表导出工具调用 manifest，在每个调用后请求恢复边界；无修改时允许原有的 skip/reuse。冻结真实调用数，不把 149 个计时条目当作调用数。

输出：每步延迟分布、按阶段的总时间、累计快照时间、最终 commit、最终路径数和相对 Ext4 开销。直接检验每步保存历史在十万文件规模真实开发流程中的成本。

### E5. 我们现有优化的贡献（补充消融）

在 E1 的 `M=1,000/10,000、K=0/10、100 步` 子集上比较当前实现与两种保持同样历史状态的朴素实现：每步复制完整 upper；无变化时仍物化历史目录树。需要新增的测量模式写在实验适配层，并保存补丁，不宣称当前已有开关。

检查共享文件内容、空变化复用分别减少多少复制、目录元数据操作、空间和延迟。主实验始终使用未改语义的当前实现。只读复用并非 YoloFS 所没有的能力，不能将其写成相对 YoloFS 的独有优势。

## 6. 正确性、统计与输出

- 每个微实验生成确定性版本内容，校验当前状态及抽取的早期/中期/最近历史点；restore 使用各后端共同支持的完整历史点语义。提交后校验路径集合、内容、删除/重命名结果和必要元数据。
- 保存全部运行与失败；后端失败、层数限制、数据不一致独立列出，不能当零耗时。主图均值和 bootstrap 置信区间按独立 session 计算，p99 同时注明样本数。
- 文件系统容量统计对硬链接按 `(st_dev, st_ino)` 去重并计入 `st_blocks`，同时记录目录和引用数量；不能对每份快照分别 `du` 后相加。
- 输出 `environment.json`、`source-manifest.json`、`workload-manifest.json`、逐步 `steps.jsonl`、`summary.csv` 和失败记录。时间至少包含 setup/execute/detect/snapshot/record/restore/commit/cleanup；版本记录包含请求次数、实际物化次数、复制字节和物理占用。
- 主文 RQ4 优先放两张单栏图：每步开销及其规模变化、内核工作流 run/snapshot/commit 分解；深度曲线和十万文件提交详表视版面放正文或附录。
- 现有 `138.31 ms/operation` 包含命令执行与记录，不能改名为“快照开销”。只有上述独立计时得到结果后才填入新快照数据。

执行顺序：先完成适配与版本恢复核验，再跑 E1/E2，随后 E3/E4，最后补 E5。当前步骤只形成可复核计划与来源记录，不改论文结果、不加载模块、不启动正式性能实验。


## 快照与提交模式的已完成子集及公平性更正（2026-11-15）

此前快照/提交专用模式的159次验证通过，但“所有配置不超过1.2倍”结论撤回。调用入口、层释放时机和最终seal计时没有对齐；原报告和所有原始数据保存在[原测量更正页](../review/rq5-snapshot-only-20261115/RESULTS.md)。

随后完成162个session的公平性补测：130次计时、26次预热、6个历史预检，全部通过。新增同一保留方式及相同发布原语的纯OverlayFS、直接YoloFS snapshot ioctl和直接Ext4。快照检查深度1/10/30；文件操作检查1万文件create/unlink。执行后不先做历史travel或读取验证，完成阶段包括seal、发布、close、卸载和暂存目录销毁。

30层快照API均值为AgentTX 0.549 ms/次、YoloFS 0.033 ms/次，1.2倍目标尚未达到；其余逐项数据及全部均值、中位数、长尾样本见[公平性核查](../review/rq5-storage-fairness-20261115/RESULTS.md)。后续优化须保持本次统一口径，同时报告API延迟和完整结束成本。原正式批次继续暂停；未执行本计划中的其他规模扫描或内核开发流程。


## OverlayFS增量开销优化（2026-11-15）

用户将后续优化目标收敛到相同设置的OverlayFS。本轮不新增YoloFS对照，普通OverlayFS挂载的同步语义保持不变；新接口通过紧凑uN/wN目录及有限平坦层检查，减少当前挂载和最终发布中完全被遮蔽的旧层。历史快照独立可读。

8组配置均完成独立预热和10次正式测量，共338个session；25项正确性测试及全部session通过。相对使用相同原语的OverlayFS，快照接口最大1.158倍，包含完整收尾的工作流最大1.151倍，包含初始化的完整会话最大1.151倍。详细设置、逐负载数据、置信区间与原始样本见[优化结果](../review/rq5-overlay-increment-20261115/RESULTS.md)。原正式批次保持暂停，未替换论文结果。
