# DeltaFS：Ext4 / XFS 实测与原文核对

**后续补测已完成：2,871/2,871 会话通过；局部改写已呈现原文的 reflink 趋势，详见 [补测结果](RQ5_PARTIAL_OVERWRITE_20261117.md)。** 下文保留补测前的核对结论及当时数据。

## 同批实测

当前 RQ5 运行原版 DeltaFS 内核组件，在同一 Linux 6.8.0-147 虚拟机上比较 Ext4 与 XFS（reflink=1）。以下文件操作为初始化、执行、发布与清理的完整流程；快照延迟为整个用户态 snapshot 调用，不只计 ioctl。

| 指标 | Ext4 | XFS | XFS 相对变化 |
| --- | ---: | ---: | ---: |
| 创建 | 0.815 s | 0.776 s | -4.8% |
| 覆盖 | 1.329 s | 1.454 s | +9.4% |
| 重命名 | 2.864 s | 2.724 s | -4.9% |
| 删除 | 1.023 s | 1.164 s | +13.8% |
| 30 次时的单次快照 | 0.546 ms | 0.504 ms | -7.6% |
| 30 次快照完整流程 | 89.161 ms | 95.941 ms | +7.6% |
| 30 次快照额外存储 | 1520.000 KiB | 1280.000 KiB | -15.8% |
| 30 次快照额外内存 | 1785.600 KiB | 1490.000 KiB | -16.6% |

单次快照在深度 1、10、30 时分别变化 +1.0%、−8.8%、−7.6%，各深度的 95% 区间均重叠。覆盖操作本身从 0.396 s 降到 0.306 s（少 22.8%），但发布与清理从 0.745 s 增到 0.984 s，因此完整流程反而增加 9.4%。不能把完整流程的差值都视为 copy-up 差值。

## 原文测量与当前负载的差别

核对 [DeltaBox v3 §4.1、§6.3.1、§6.3.2](https://arxiv.org/html/2605.22781v3#S6.SS3.SSS2)。§4.1 明确区分动态切层与 reflink：前者在 OverlayFS 支持的底层 FS 上都能运行；后者减少必须复制旧文件数据时的 copy-up 量。

原文 Figure 9(a) 在 4 KiB–1 MiB 文件中固定改写 4 KiB，保留文件其他内容。Figure 9(b) 回放 SWE-Search 的真实编辑，统计块设备实际写入字节，包括日志和元数据。正文报告小文件 136→27 KiB（减少 80.1%），大文件 320.5→174 KiB（减少 45.7%）。这些是物理写 I/O，不是完整流程时间，也不是采样结束时保留的存储量。原文将小文件收益归于 XFS 元数据，将大文件收益归于 reflink 共享块。

当前覆盖负载及快照轮次内的 seed 改写均调用 Rust `std::fs::write`，以创建/截断方式打开后重写完整 4 KiB。原版 DeltaFS 的 `copy_up.c` 对 `O_TRUNC` 设置 `ctx.stat.size=0`，`ovl_copy_up_data` 遇零长度直接返回，因此该次 copy-up 无需复制旧数据。现有小文件整写负载没有充分施压于原文的“保留大部分旧数据、局部改写”场景。XFS 卷确实启用了 reflink，DeltaFS 源码也确实尝试 `vfs_clone_file_range`，但卷支持该功能不代表本次操作实际走了它。

原文 Table 3 报告 SWE-bench MCTS 回放中的 FS checkpoint 为 0.07 ms；我们 XFS 上整个 snapshot 请求为 0.417–0.724 ms，包含创建和打开新层目录、组装参数、ioctl 和用户态状态维护。负载、环境和计时范围都有差异，不能将当前结果称为复现了原文 0.07 ms。现有记录没有单独的 ioctl 时间，尚不能量化这几项分别解释了多少差距。

## 结论

现有实测中，DeltaFS 在 Ext4 与 XFS 上的快照延迟接近；文件提交完整流程没有一致的单方优势。原文在局部改写场景报告的物理写 I/O 优势与此不矛盾，但该优势尚未由本组 RQ5 负载复现。对齐原文需要加入不截断的 4 KiB 局部改写、4 KiB–1 MiB 文件大小 sweep，并分别统计修改/快照延迟、物理写入量和保留存储；如需区分文件系统与 reflink 的作用，再加入 reflink 关闭的 XFS 对照。

此次仅做数据、原文及实现核验，没有重跑或修改 RQ5 数值，也没有修改论文正文。

## 可核对的证据

- [同批原始统计](../review/rq5-backing-filesystems-20261117/summary.json)
- [在线原文缓存及分段摘录](../review/rq5-deltafs-paper-crosscheck-20261117/paper-excerpts.json)
- [原文 Figure 9](../review/rq5-deltafs-paper-crosscheck-20261117/figure9.png)
- [当前快照计时范围](../experiments/scripts/bench_rq5_checkpoint_lifetime.py)
- [DeltaFS 快照接口适配](../experiments/scripts/rq5_deltafs_backend.py)
- [原版 copy_up.c](../review/rq5-deltafs-comparison-20261117/source/upstream-deltafs/copy_up.c)
