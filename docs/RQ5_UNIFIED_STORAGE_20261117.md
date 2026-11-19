# RQ5 合并图：局部改写、快照与资源开销

当前 RQ5 使用已核验的局部改写结果替换原先 10,000 个小文件的创建、覆盖、重命名、删除对比。快照和资源开销继续使用已完成的 Ext4/XFS 分组测量，六个子图均已加入 BranchFS 与 try 的原生实现。一张双栏大图包含六个子图，按两行三列排列，共用图例。

| 子图 | 内容 | 对照 |
| --- | --- | --- |
| (a) | 对 1 MiB 文件做非截断 4 KiB 改写时的块设备写入量，纵轴为对数尺度 | 原生 FS、OverlayFS、DeltaBox、BranchFS、try、AgentTX；分别展示 Ext4 与 XFS（开启 reflink） |
| (b) | 相同局部改写的耗时，包括 syncfs 完成 | 同上 |
| (c) | 单次快照延迟（对数纵轴） | OverlayFS、DeltaBox、BranchFS、try、AgentTX |
| (d) | 包含初始化、写入、快照、发布及清理的完整流程（对数纵轴） | OverlayFS、DeltaBox、BranchFS、try、AgentTX |
| (e) | 相对同写入无快照执行的额外存储 | OverlayFS、DeltaBox、BranchFS、try、AgentTX |
| (f) | 相对同写入无快照执行的额外内存 | OverlayFS、DeltaBox、BranchFS、try、AgentTX |

子图 (a)(b) 各点为 20 次独立会话的均值，每次会话内先平均五次改写。每次改写后都创建快照；图中改写写入量和耗时不包含随后快照。原生 FS 不保留快照，仅作为直接改写参考。子图 (c)(d) 各点为 20 次运行的均值，(e)(f) 为 10 对有/无快照运行的差值；四个子图均在内部并排展示 Ext4（左）与 XFS reflink 开启（右）。所有误差线为 95% bootstrap 区间，不跨文件系统取平均。

图内不再放置具体倍数或百分比标注，数值比较移入正文。子图名称只标明指标，负载、计量和统计方式在 RQ5 正文说明。OverlayFS、DeltaBox 和 AgentTX 的局部改写结果接近；BranchFS 的数据写入量也相近，但改写延迟较高。快照切层是 AgentTX 相对 OverlayFS 和 BranchFS 的主要收益。

四个来源批次均已完整通过核验：局部改写及快照诊断批次 2,871 次会话（2,700 次正式测量），原快照/资源批次 1,164 次会话（1,040 次正式测量），BranchFS 批次 302 次会话（280 次正式测量），try 批次 302 次会话（280 次正式测量）。图中按上述负载选取对应数据，未混合不同测试的均值。DeltaBox 使用原版 DeltaFS 内核组件，BranchFS 使用官方固定提交的原生实现。全部配置运行于同一 Linux 6.8.0-147 内核；原有 OverlayFS、DeltaBox、AgentTX 配置开启 metacopy 和 redirect_dir、关闭 index；try 使用原生挂载选项。BranchFS 通过原生控制文件创建和切换嵌套分支，内存统计包含 daemon。try 通过原生 `-N` 保存环境、`-L` 叠加历史环境，快照区间包含退出和重新进入；最终由原生 commit helper 顺序发布，资源统计包含其全部子进程。

- AgentTX 的 1 MiB 文件局部改写在 Ext4 上写入 1,102.44 KiB/edit、含 syncfs 耗时 4.496 ms/edit；在开启 reflink 的 XFS 上分别为 12.71 KiB/edit 和 1.331 ms/edit。XFS 上 OverlayFS 和 DeltaBox 同样利用共享数据能力，分别写入 12.71 和 12.73 KiB/edit。
- Ext4：AgentTX 单次快照比匹配 OverlayFS 快 4.83–10.75 倍，30 次快照完整流程耗时减少 30.8%。
- XFS：相应结果为 4.20–13.66 倍和 30.5%。
- 相对 BranchFS：30 次深度时，AgentTX 单次快照在 Ext4 上快 5.42 倍、XFS 上快 8.48 倍。完整流程耗时分别为 AgentTX 的 95.22/99.22 ms、BranchFS 的 470.98/631.27 ms。
- try：30 次深度时，保存环境切换的平均延迟在 Ext4/XFS 上分别为 400.738/394.905 ms，完整流程为 14.159/13.958 s；额外存储为 15,040/3,276 KiB，额外内存为 11,208.4/10,367.6 KiB。
- 两类文件系统上，AgentTX 的额外存储均比 OverlayFS 和 DeltaBox 多 4 KiB，保留挂载引用会增加内存；其存储和内存均低于本次 BranchFS 测量。DeltaBox 的重复快照延迟和完整流程耗时更低。

数据源为 [局部改写汇总](../review/rq5-partial-overwrite-20261117/summary.json)、[快照/资源汇总](../review/rq5-backing-filesystems-20261117/summary.json)、[BranchFS 汇总](../review/rq5-branchfs-20261117/summary.json) 和 [try 汇总](../review/rq5-try-20261117/summary.json)。完整方法和结果见 [局部改写报告](RQ5_PARTIAL_OVERWRITE_20261117.md)、[原批次归档报告](RQ5_BACKING_FILESYSTEMS_20261117.md)、[BranchFS 报告](RQ5_BRANCHFS_20261117.md) 与 [try 报告](RQ5_TRY_20261117.md)。旧文件提交结果已退出正文展示，原始测量保留在归档中。

[当前图](../paper/figures/rq5-storage/FIG-RQ5-Storage.pdf)、[图值与来源哈希](../paper/figures/rq5-storage/figure-data.json)、[论文](../paper/main.pdf)。
