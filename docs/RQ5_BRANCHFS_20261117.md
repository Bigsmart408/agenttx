# RQ5：BranchFS 原生实现补测

正式批次 **302/302 次会话通过，280 次正式测量，零失败、零模型调用**。此前 22 次独立预检也全部通过。六个子图均已加入 BranchFS，保留 Ext4 与开启 reflink 的 XFS 两组。

| 指标 | Ext4：AgentTX | Ext4：BranchFS | XFS：AgentTX | XFS：BranchFS |
| --- | ---: | ---: | ---: | ---: |
| 局部改写写入量（KiB/次） | 1,102.44 | 1,084.52 | 12.71 | 13.35 |
| 含 syncfs 的改写耗时（ms/次） | 4.496 | 5.533 | 1.331 | 2.275 |
| 单次快照延迟，30 次深度（ms） | 0.635 | 3.441 | 0.667 | 5.659 |
| 30 次快照完整流程（ms） | 95.22 | 470.98 | 99.22 | 631.27 |
| 30 次快照额外存储（KiB） | 1,524 | 14,720 | 1,284 | 1,404 |
| 30 次快照额外内存（KiB） | 2,760.4 | 20,426.8 | 2,499.2 | 6,071.6 |

30 次深度时，AgentTX 的单次快照相对 BranchFS 在 Ext4 上快 5.42 倍、XFS 上快 8.48 倍。BranchFS 同样利用了 XFS 的共享 extent 能力，XFS 下的局部改写写入和历史存储开销明显低于其 Ext4 结果。

完整流程包含各系统的原生初始化与收尾。30 次深度时，BranchFS 在 Ext4/XFS 上的初始化分别为 110.35/111.71 ms，逐级提交、卸载及清理分别为 137.39/220.28 ms；单次快照延迟单独计时。

## 实验设置

- 官方仓库为 <https://github.com/multikernel/branchfs>，固定提交 `a4b6592d31aacb4d2f94d0c80f43d47d38063fa9`，用 Rust 1.93.0 release 构建，未修改文件系统源码。源码归档、Cargo.lock、二进制及哈希均保存。
- 使用现有 RQ5 的同一 4 vCPU、12 GiB 虚拟机，Linux `6.8.0-147-generic`；同一 guest disk 上新建两个 4 GiB loop 卷，分别格式化为 Ext4 和开启 reflink 的 XFS。Rust 工作负载二进制的 SHA-256 与原 RQ5 完全一致。
- 快照通过同一工作进程写入原生 `.branchfs_ctl`，创建子分支并切换过去；保留父分支作为历史状态。每次快照不启动 CLI。初始化和最终卸载使用原生接口，均计入完整流程；最后用原生 commit 逐级合并到 base，并清理分支、挂载和 daemon。
- 局部改写沿用五次非截断 4 KiB pwrite、1 MiB 文件、初始快照及每次改写后的快照。对底层卷调用 syncfs，读取 loop 块设备写入计数。每组 20 次独立会话，先平均会话内五次改写。图中改写指标不包含随后快照。
- 快照负载沿用 100 个 target、10 个覆盖写 seed 文件，每个 4 KiB，快照深度 1、10、30；每组 1 次预热、20 次正式重复。完整流程包含准备、初始化、写入、快照、原生提交、卸载及清理；验证不计时，计时中不额外刷盘。
- 每个文件系统与深度做 10 对有/无快照资源测试，共 60 对。先扣除初始化占用，再计算有快照与无快照的资源增长差。物理存储继续按 FIEMAP extent 并集计数；内存按独立 worker cgroup 的 anon+file+kernel 计数，包含 BranchFS daemon，并逐条验证其 PID 位于该 cgroup。
- 每个正式样本均保留。延迟和资源分别测量，资源探针不计入快照计时。所有最终文件和历史版本均检查内容、长度与权限；每个文件系统另外检查三代不同历史内容。均值与 95% 区间沿用 10,000 次独立会话或资源对 bootstrap。

正式测量包括 40 次局部改写会话、120 次快照/完整流程会话，以及 120 次资源会话。22 次预热与历史验证也包含在正式批次的 302 次会话中。

实验挂载和 loop 设备已释放，无残留 BranchFS daemon，无内核错误。官方归档与实际构建源码逐文件哈希一致，见 [环境与清理记录](../review/rq5-branchfs-20261117/environment-and-cleanup.json)。

## 数据与复现

- [正文图](../paper/figures/rq5-storage/FIG-RQ5-Storage.pdf)、[所有图值和来源](../paper/figures/rq5-storage/figure-data.json)、[当前合并图说明](RQ5_UNIFIED_STORAGE_20261117.md)
- [全部原始记录](../review/rq5-branchfs-20261117/raw/)、[汇总](../review/rq5-branchfs-20261117/summary.json)、[核验日志](../review/rq5-branchfs-20261117/analysis.log)
- [原生适配器](../experiments/scripts/bench_rq5_branchfs.py)、[分析脚本](../experiments/scripts/analyze_rq5_branchfs.py)
- [官方源码与冻结的测量脚本](../review/rq5-branchfs-20261117/source/)

```sh
python3 experiments/scripts/analyze_rq5_branchfs.py \
  --input review/rq5-branchfs-20261117/raw \
  --output review/rq5-branchfs-20261117
python3 motivation/plot_rq5_storage.py
make -C paper TECTONIC=/home/bfq/.cache/agenttx-tools/tectonic-0.17.0/tectonic
```
