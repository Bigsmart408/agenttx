# RQ5 补测：局部改写、块设备写入与 DeltaFS ioctl

补测完成：**2,871/2,871 次会话通过，2,700 次正式测量，零失败、零模型调用**。正式测量包含 2,160 次局部改写会话及 540 次快照诊断会话；每个数据点有 20 次独立重复。另有 171 次独立预检全部通过。

## 对照与方法

- 同一 4 vCPU、12 GiB 虚拟机，Linux 6.8.0-147-generic；同一 guest disk 上三个新建的 4 GiB loop 卷：Ext4、XFS reflink=0、XFS reflink=1。所有分层挂载为 metacopy=on,redirect_dir=on,index=off。
- 四种系统：原生文件系统、原生 OverlayFS 的同步切层方案、原版 DeltaFS 内核组件、AgentTX。文件系统实现使用与已完成 RQ5 相同的冻结源码；没有新增性能优化。
- 对齐 DeltaBox Figure 9(a) 的固定局部改写：文件大小 4/8/16/32/64/128/256/512/1024 KiB；每次使用 O_WRONLY + pwrite 改写其中 4 KiB，不截断、不替换文件、不更改长度。
- 分层系统先创建文件并保存初始快照；每个正式会话连续执行 5 次改写，每次改写后保存快照。后续写入总是面对历史层，触发真实的 copy-up。逐代核验完整内容、长度与权限；预检还挂载并读取每个保存的历史视图。
- 单独报告修改期间的写入量，以及包含随后快照的写入量。两者均读取该卷块设备的 sectors_written × 512，在初始状态及每次修改/快照后 syncfs。计数包含文件数据、日志和元数据，但不包含初始化、验证、最终清理，也不把外层宿主文件系统的二次写入混入此卷。
- 修改耗时分成 open+pwrite+close，以及延长至 syncfs 返回的耗时。物理 extent 和存储统计在写入计数及延迟窗口之外采样。
- 快照诊断独立运行：100 个目标、10 个种子文件，均为 4 KiB；用不截断的写入构建深度 1/10/30。记录完整 snapshot 请求，DeltaFS 另外在同一进程内计时单次 ioctl。此诊断不在快照之间额外刷盘。
- 一次预热，20 次正式重复；随机交替配置，保留全部正式样本。先对每次会话内的 5 次改写取均值，再对独立会话做 10,000 次 bootstrap，给出 95% 区间。

## 1 MiB 文件每次改写 4 KiB：块设备写入量

下面仅计修改阶段（KiB/edit），快照写入另列：

| 系统 | Ext4 | XFS / reflink 关闭 | XFS / reflink 开启 |
| --- | ---: | ---: | ---: |
| 原生 FS | 19.200 | 5.065 | 4.915 |
| OverlayFS | 1,102.400 | 1,041.670 | 12.710 |
| DeltaFS（DeltaBox） | 1,101.840 | 1,040.765 | 12.730 |
| AgentTX | 1,102.440 | 1,041.540 | 12.710 |

包含修改及随后快照（KiB/edit）：

| 系统 | Ext4 | XFS / reflink 关闭 | XFS / reflink 开启 |
| --- | ---: | ---: | ---: |
| 原生 FS | 19.200 | 5.065 | 4.915 |
| OverlayFS | 1,240.640 | 1,048.070 | 19.075 |
| DeltaFS（DeltaBox） | 1,180.560 | 1,045.265 | 16.530 |
| AgentTX | 1,197.000 | 1,051.040 | 21.665 |

原生 FS 没有历史快照，其该表数字仅为直接改写参考。

## 完整大小 sweep：DeltaFS 的修改阶段写入量

| 文件大小（KiB） | Ext4 | XFS / reflink 关闭 | XFS / reflink 开启 |
| ---: | ---: | ---: | ---: |
| 4 | 81.600 | 16.495 | 10.640 |
| 8 | 85.640 | 20.540 | 11.870 |
| 16 | 93.760 | 28.420 | 12.060 |
| 32 | 109.600 | 44.490 | 12.705 |
| 64 | 141.640 | 76.435 | 12.535 |
| 128 | 205.680 | 144.830 | 12.570 |
| 256 | 333.600 | 272.785 | 12.570 |
| 512 | 589.600 | 528.860 | 12.550 |
| 1024 | 1,101.840 | 1,040.765 | 12.730 |

## 1 MiB 文件的改写耗时

每格为“open+pwrite+close / 加入 syncfs”的毫秒数：

| 系统 | Ext4 | XFS / reflink 关闭 | XFS / reflink 开启 |
| --- | ---: | ---: | ---: |
| 原生 FS | 0.020 / 0.818 | 0.023 / 0.694 | 0.023 / 0.680 |
| OverlayFS | 3.586 / 4.574 | 2.738 / 3.623 | 0.224 / 1.447 |
| DeltaFS（DeltaBox） | 3.477 / 4.435 | 3.026 / 3.930 | 0.193 / 1.284 |
| AgentTX | 3.487 / 4.496 | 2.901 / 3.783 | 0.206 / 1.331 |

## DeltaFS 快照请求拆分

每格为“完整 snapshot / 纯 ioctl”，单位毫秒：

| 底层 FS | 1 次深度 | 10 次深度 | 30 次深度 |
| --- | ---: | ---: | ---: |
| Ext4 | 0.362 / 0.172 | 0.326 / 0.112 | 0.395 / 0.151 |
| XFS / reflink 关闭 | 0.391 / 0.199 | 0.292 / 0.102 | 0.345 / 0.136 |
| XFS / reflink 开启 | 0.333 / 0.170 | 0.276 / 0.100 | 0.362 / 0.158 |

在 XFS reflink=1、深度 30 时，完整请求为 0.362 ms，其中 ioctl 为 0.158 ms，其他用户态准备与维护为 0.204 ms。原文 Table 3 的 FS checkpoint 均值为 0.07 ms；本机的纯 ioctl 仍高于这一数值，单独计时并未完全复现原文绝对延迟。原文基于 NVMe 上的 SWE-Search 回放，本次基于 loop 卷与固定轨迹；该拆分只量化当前环境内的组成，不将剩余差距全部归因于某一原因。

## 对原文和 AgentTX 的结论

- DeltaFS（DeltaBox）：开启 XFS reflink 后，1 MiB 文件局部改写的块设备写入从 1,040.765 降到 12.730 KiB，减少 **98.78%**；含 syncfs 的改写时间从 3.930 降到 1.284 ms，减少 **67.3%**。
- AgentTX：开启 XFS reflink 后，1 MiB 文件局部改写的块设备写入从 1,041.540 降到 12.710 KiB，减少 **98.78%**；含 syncfs 的改写时间从 3.783 降到 1.331 ms，减少 **64.8%**。

Ext4 与 XFS 关闭 reflink 时，copy-up 写入量随原文件大小增长；XFS 开启 reflink 后保持在约 10–13 KiB/edit，呈现原文 Figure 9(a) 的主要趋势。FIEMAP 核验也确认只有开启 reflink 的大文件历史版本共享数据块，且修改后各历史版本的内容均正确。

该收益在 OverlayFS、DeltaFS 和 AgentTX 中都存在，来自共同使用的 XFS reflink copy-up 能力。AgentTX 的用户态控制没有丢失这部分收益。快照的切层延迟则仍是另一项指标，不随减少的数据复制量同比下降。

这里对齐的是原文 Figure 9(a) 的固定改写负载，没有回放原作者 Figure 9(b) 的 SWE-Search 编辑。因此本次 10–13 KiB/edit 不与原文 136→27 或 320.5→174 KiB 的真实编辑统计逐项等同；可核对的是按文件大小增长与近似平坦的趋势。

## 图与原始记录

- [正文 RQ5 六子图](../paper/figures/rq5-storage/FIG-RQ5-Storage.pdf) 的 (a)(b) 已使用本批次 1 MiB 文件的写入量和含刷盘改写耗时，替换旧的小文件提交对比；其余四个子图保留独立快照/资源测量。详见 [当前图说明](RQ5_UNIFIED_STORAGE_20261117.md)。
- [六个子图：块设备写入与含刷盘改写耗时](../paper/figures/rq5-partial-overwrite/FIG-RQ5-Partial-Overwrite.pdf)
- [绘图数值与来源哈希](../paper/figures/rq5-partial-overwrite/figure-data.json)
- [全部原始样本](../review/rq5-partial-overwrite-20261117/raw/)、[统计汇总](../review/rq5-partial-overwrite-20261117/summary.json)、[逐条核验输出](../review/rq5-partial-overwrite-20261117/analysis.log)
- [原文与此前负载核对](RQ5_DELTAFS_PAPER_CROSSCHECK_20261117.md)

```sh
python3 experiments/scripts/analyze_rq5_partial_overwrite.py \
  --input review/rq5-partial-overwrite-20261117/raw \
  --output review/rq5-partial-overwrite-20261117
python3 motivation/plot_rq5_partial_overwrite.py
```
