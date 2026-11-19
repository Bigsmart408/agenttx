# RQ5：快照延迟、额外存储与内存

RQ5 两张后端主图的第二个子图现直接显示单次快照延迟，完整流程时间仍在正文报告。新增资源图比较开启与关闭快照的额外占用，使用原生 OverlayFS 同步卸载/重新挂载对照、原生 YoloFS，以及各自的 AgentTX 后端。

## 快照延迟

沿用未插入资源采样的原始测量：OverlayFS 每项 20 次，YoloFS 每项 10 次。以下为会话内快照平均延迟，再对所有会话求均值。

| 每会话快照数 | OverlayFS（ms/次） | AgentTX/OverlayFS（ms/次） | YoloFS（µs/次） | AgentTX/YoloFS（µs/次） |
|---:|---:|---:|---:|---:|
| 1 | 2.998 | 0.352 | 41.82 | 72.30 |
| 10 | 1.792 | 0.306 | 20.25 | 22.16 |
| 30 | 1.741 | 0.379 | 8.45 | 10.69 |

数据来自 `review/rq5-checkpoint-lifetime-20261116/summary.json` 和 `ports/yolofs/results/rq5-final/summary.json`。文件提交部分保持原始已核验数据。

## 额外存储与内存

新增补测 **268/268 通过**：240 次正式会话构成 120 对，24 次预热，4 次历史版本预检。每个后端、每种快照数有 10 对开启/关闭快照的会话，配对顺序和配置顺序随机交错。

- 使用同一专用 KVM、同一 Ext4 和原始 Rust `checkpoint-scaling` 负载：100 个 target、10 个 seed，每文件 4 KiB；每轮覆盖 10 个 seed。关闭快照的对照执行相同次数的写入和请求，只在请求处继续执行。
- 每个会话先记录初始化占用，再在全部写入结束、历史版本仍保留、提交尚未开始时记录占用。结果为 `(开启后的占用增量) − (关闭后的占用增量)`。
- 存储按底层 Ext4 的 `st_blocks × 512` 统计；每个 `(device, inode)` 只计一次，不跟随符号链接或进入挂载视图。
- 内存使用独立工作进程 cgroup 的 `memory.stat`，计入 `anon + file + kernel` 的已记账内存。它包含堆页、文件缓存和计入 cgroup 的内核分配。采样发生在遍历历史目录之前，避免把遍历自身的内存分配算入观察值。
- 资源采样单独执行，不替换未插桩的快照延迟记录。每次测量校验最终 110 个文件的名字、内容和权限；每个后端另核验三个不同内容的历史版本。

| 后端 | 快照数 | 原生额外存储（KiB） | AgentTX 额外存储（KiB） | 原生额外内存（KiB） | AgentTX 额外内存（KiB） |
|---|---:|---:|---:|---:|---:|
| OverlayFS | 1 | 12 | 16 | −93.6 | 15.6 |
| OverlayFS | 10 | 480 | 484 | 483.6 | 773.6 |
| OverlayFS | 30 | 1520 | 1524 | 1759.2 | 2772.0 |
| YoloFS | 1 | 0 | 0 | 2.8 | 9.6 |
| YoloFS | 10 | 372 | 372 | 501.6 | 504.0 |
| YoloFS | 30 | 1200 | 1200 | 1623.6 | 1627.6 |

OverlayFS 上，AgentTX 比同步卸载对照多占一个 4 KiB 块，用于代次记录。30 次快照时，额外内存分别为 2.707 和 1.718 MiB，AgentTX 多 0.989 MiB，差异主要来自保留挂载对应的内核元数据。YoloFS 上，两者的额外存储相同，额外内存接近。

原生 OverlayFS 单次快照的 −93.6 KiB 是相对关闭快照对照的净变化。分项记录显示匿名内存和文件缓存差异均为零，减少来自内核记账项：同步卸载释放了旧视图的缓存状态。该值及全部原始样本保留，未截成零。

## 复现与产物

```bash
python3 experiments/scripts/analyze_rq5_snapshot_resources.py \
  --source review/rq5-snapshot-resources-20261116/raw/results \
  --output review/rq5-snapshot-resources-20261116
python3 motivation/plot_rq5_backends.py
python3 motivation/plot_rq5_snapshot_resources.py
```

原始会话、协议、哈希及部署脚本保存在 `review/rq5-snapshot-resources-20261116/raw/`。分析保留全部正式样本，图示误差线为 10,000 次配对样本 bootstrap 的均值 95% 区间。图中每一组数据的来源和哈希分别保存在 `paper/figures/rq5-backends/figure-data.json` 与 `paper/figures/rq5-snapshot-resources/figure-data.json`。
