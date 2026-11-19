# RQ5：OverlayFS 快照挂载生命周期优化

本轮补测比较同步卸载的 OverlayFS 参考路径、只增加旧挂载保留的中间对照，以及现有 AgentTX 快照 API。192 次运行全部通过，包括 180 次正式测量、9 次预热和 3 次历史版本检查。

## 设置

- 在专用 KVM 中执行，4 vCPU、12 GiB 内存、Linux 6.8.0-147，所有数据在同一 Ext4 文件系统上。
- 使用 YoloFS 仓库原始 Rust `checkpoint-scaling` 的 commit 负载：100 个 target、10 个 seed，每文件 4 KiB；每轮覆盖 10 个 seed 后创建快照，最后提交。
- 分别创建 1、10、30 次快照。每个配置预热一次，正式测量 20 次，按重复轮次随机交错运行。
- 三组均使用进程内 mount 接口和相同发布实现，开启 `metacopy=on,redirect_dir=on,index=off`；不调用 agent、模型、定位或依赖跟踪。
- OverlayFS (sync)：采用公开基准的同步卸载、创建新可写层、重新挂载路径。
- Retained mounts：旧视图只读化，保留挂载引用，延迟卸载后挂载新可写层。
- AgentTX：直接使用当前 `SnapshotSession`，保留挂载引用并记录快照代次。没有为这轮测量修改生产实现。

## 结果

下表为全部正式样本的均值。完整流程包含初始化、工作负载、快照、提交、挂载引用释放和会话清理；最终结果校验在计时区间外。

| 快照次数 | OverlayFS sync：ms/次 | Retained：ms/次 | AgentTX：ms/次 | OverlayFS sync：流程 ms | Retained：流程 ms | AgentTX：流程 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2.998 | 0.366 | 0.352 | 12.48 | 12.07 | 12.77 |
| 10 | 1.792 | 0.279 | 0.306 | 35.46 | 25.76 | 26.19 |
| 30 | 1.741 | 0.349 | 0.379 | 88.10 | 56.58 | 58.96 |

AgentTX 相比同步卸载对照，快照创建分别加速 **8.52、5.85、4.60 倍**。计入收尾后，10 次和 30 次快照的流程耗时分别下降 **26.13% 和 33.08%**；1 次快照的总耗时为 12.77 对 12.48 ms，配对比值的 95% bootstrap 区间包含 1。

中间对照接近 AgentTX，说明加速主要来自保留挂载引用、避免每次快照同步销毁旧 superblock。释放引用的开销计入收尾，且多次快照的完整流程仍有收益。优化在用户态实现，OverlayFS 内核没有改变。

RQ5 的 OverlayFS 主对比图已更新：快照部分使用本轮同批测量的同步卸载 OverlayFS 和优化后的 AgentTX，各 20 次；文件提交部分仍使用此前不创建快照的 10 次测量。YoloFS 主对比中的对照本来就直接调用原生 mount、commit 和 checkpoint ioctl，无需替换其数据。两张图的每组数据来源、样本数和实际对照名称均记录在 `paper/figures/rq5-backends/figure-data.json`。此前保留挂载的 OverlayFS 对照留在历史数据中，不再作为主图的快照对照。

挂载生命周期消融图保留中间对照，用于隔离保留引用的收益。这里测量显式快照 API；设计中的增量对象历史另由 `LayerStore` 实现，并未把两条路径的收益混算。

## 校验与复现

每次正式测量检查最终 110 个文件的路径、内容和权限；每组另检查三个不同代次的历史文件内容。原始记录、部署源码与哈希保存在 `review/rq5-checkpoint-lifetime-20261116/raw/`。

```bash
python3 experiments/scripts/analyze_rq5_checkpoint_lifetime.py \
  --source review/rq5-checkpoint-lifetime-20261116/raw/results \
  --output review/rq5-checkpoint-lifetime-20261116
python3 motivation/plot_rq5_checkpoint_lifetime.py
```

分析保留全部正式样本；图中误差线为 10,000 次会话 bootstrap 的均值 95% 区间。论文新增设计小节 `Efficient Version Retention`，流程图源文件为 `paper/figures/design-workflow/generate_version_capture.py`，对应可编辑 PPT 和矢量 PDF 同目录保存。
