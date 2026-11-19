# DeltaFS、OverlayFS 与 AgentTX 的同内核对照

**已找回论文的 DeltaFS 内核源码并完成测试：585/585 次会话通过，其中 520 次为正式测量。** 此次没有使用 DeltaBox Lite 的 FUSE 实现。连续快照下，DeltaFS 的单次快照延迟和额外内存低于 AgentTX；两者均显著快于常规 OverlayFS 的同步卸载/重挂载方案。

## 源码与统一内核

源码来自 [Zenodo 第一版归档](https://zenodo.org/records/23202153)。归档的工作目录未直接包含源码 tar，但 Git 对象中仍保留 `linux-6.8.tar`，现已恢复。源码 tar 为 1,464,176,640 字节，SHA-256 `456df46277ccee96196f5dbd7d61d01f8f57c02e100fc6f693d523b3d0a92dbe`，与作者构建记录一致；17 个 OverlayFS 源文件也通过逐文件核验。

三者均运行在同一台 4 vCPU、12 GiB 虚拟机、同一个 **Linux 6.8.0-147-generic** 内核和 Ext4 `/dev/vda1` 上，挂载参数均为 `metacopy=on,redirect_dir=on,index=off`。DeltaFS 作为独立模块注册，原生 OverlayFS 保留。模块和原生 OverlayFS 的 vermagic 完全一致。

为支持模块共存，仅修改文件系统/模块/inode cache 名、一个头文件引用路径，并内联未导出的 `__iget()` 的原始 `atomic_inc()` 函数体。完整补丁、原始源码、编译日志和源码哈希保存在 [实验目录](../review/rq5-deltafs-comparison-20261117/)。

## 负载与结果

### 文件提交：与 RQ5 相同的四种负载

每种操作处理 10,000 个 4 KiB 文件，使用原始 Rust 负载；非创建操作先准备相同的初始文件。四个系统在同一批次随机交替运行，每组一次预热、十次正式测量，全部样本保留。下表包括初始文件准备、初始化、操作、最终发布及会话清理，结果校验在计时之外。分层文件系统使用相同的最终发布代码。此组不创建快照，快照开销由下一组独立测量。

| 操作 | Ext4 | OverlayFS | AgentTX / OverlayFS | DeltaFS |
| --- | ---: | ---: | ---: | ---: |
| 创建 | 0.196 s | 0.765 s | 0.736 s | 0.749 s |
| 覆盖 | 0.480 s | 1.269 s | 1.243 s | 1.209 s |
| 重命名 | 0.385 s | 2.828 s | 2.772 s | 2.827 s |
| 删除 | 0.288 s | 0.934 s | 0.901 s | 0.946 s |

三种分层文件系统的提交耗时接近。AgentTX / OverlayFS 的均值为 OverlayFS 的 0.962–0.980 倍，各项均值的 95% bootstrap 区间相互重叠；这里不据此声称 AgentTX 能加速无快照的 OverlayFS。相比直接更新 Ext4，三者均包含分层视图的初始化和最终发布。

文件操作新增 192 次会话全部通过：16 次小规模正确性检查、16 次预热、160 次正式测量。每次均完整验证名称、4 KiB 内容及 0644 权限，删除负载验证最终目录为空。实验后无残留挂载或样本目录，无内核 error 日志，DeltaFS 模块已卸载。原始数据及分析见 [文件操作汇总](../review/rq5-deltafs-commit-20261117/summary.json)。

### 显式快照及资源

沿用原始 Rust `checkpoint-scaling` 的 commit 负载：100 个目标文件和 10 个种子文件，每个 4 KiB，每轮覆盖 10 个种子文件后请求快照；快照深度为 1、10、30。只运行文件系统负载，没有 agent、模型、定位或 CRIU。三个系统使用相同的最终文件发布代码，并计入完整清理。

单次快照延迟为每次会话内各次快照的平均，再对 20 次正式重复取平均：

| 快照数 | 原生 OverlayFS，同步重挂载 | AgentTX / OverlayFS | DeltaFS |
| ---: | ---: | ---: | ---: |
| 1 | 3.614 ms | 0.416 ms | 0.423 ms |
| 10 | 2.066 ms | 0.354 ms | 0.262 ms |
| 30 | 2.191 ms | 0.438 ms | 0.321 ms |

单次快照下，AgentTX 和 DeltaFS 接近。10、30 次快照下，AgentTX 的单次快照延迟分别为 DeltaFS 的 **1.35 倍、1.36 倍**；30 次快照时，AgentTX 比常规 OverlayFS 低约 **80.0%**。

包含初始化、文件写入、快照、最终发布与清理的完整生命周期：

| 快照数 | 原生 OverlayFS | AgentTX / OverlayFS | DeltaFS |
| ---: | ---: | ---: | ---: |
| 1 | 14.800 ms | 13.846 ms | 14.460 ms |
| 10 | 41.677 ms | 30.400 ms | 27.499 ms |
| 30 | 106.057 ms | 66.265 ms | 58.718 ms |

资源单独测量，每个配置采用 10 次有/无快照的配对重复，执行完全相同的写入和快照请求。额外开销定义为“有快照时的资源增长减去无快照时的资源增长”，采样时保留全部历史版本，尚未最终发布。

| 快照数 | OverlayFS 额外存储 / 内存 | AgentTX 额外存储 / 内存 | DeltaFS 额外存储 / 内存 |
| ---: | ---: | ---: | ---: |
| 1 | 12 / −92.4 KiB | 16 / 15.2 KiB | 12 / −98.4 KiB |
| 10 | 480 / 472.8 KiB | 484 / 760.8 KiB | 480 / 511.6 KiB |
| 30 | 1,520 / 1,761.2 KiB | 1,524 / 2,767.6 KiB | 1,520 / 1,806.8 KiB |

存储统计 backing Ext4 实际分配块并按 inode 去重；内存统计独立 worker cgroup 的 `anon+file+kernel`。1 次快照的负内存增量表示相对无快照执行释放了部分缓存与内核对象，并非负的物理内存占用。30 次快照时，AgentTX 比 DeltaFS 多 **4 KiB 存储和 960.8 KiB 内存**。

## 验证与复现

延迟实验 192 次会话、资源实验 201 次会话全部通过，包括预热和三代历史快照检查；正式样本为 180 次延迟会话及 180 次资源会话。快照负载均核验最终 110 个文件的内容和权限。加上 192 次文件操作会话，合计 585 次会话通过，另有 9 次资源协议冒烟验证。测试后没有遗留挂载或样本目录，没有内核 error 日志，DeltaFS 测试模块已卸载。

原始记录及来源见 [source-manifest.json](../review/rq5-deltafs-comparison-20261117/source-manifest.json)，全部样本、均值及 10,000 次会话/配对 bootstrap 的 95% 区间见 [summary.json](../review/rq5-deltafs-comparison-20261117/summary.json)。分析命令：

```sh
python3 experiments/scripts/analyze_rq5_deltafs.py \
  --latency review/rq5-deltafs-comparison-20261117/raw/latency \
  --resources review/rq5-deltafs-comparison-20261117/raw/resources \
  --output /tmp/agenttx-deltafs-reanalysis
```

文件操作重分析命令：

```sh
python3 experiments/scripts/analyze_rq5_deltafs_commit.py \
  --input review/rq5-deltafs-commit-20261117/raw \
  --output /tmp/agenttx-deltafs-commit-reanalysis
```

DeltaFS 已作为 OverlayFS 一组的额外对照加入正文 RQ5 的文件提交、单次快照延迟和快照资源图。YoloFS 组沿用已有的独立后端对照。
