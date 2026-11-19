# DeltaFS 同内核对照：源码核验与运行状态

2026-11-17（北京时间）继续追溯 Zenodo 第一版归档后，**已经找回论文使用的 DeltaFS 内核源码，并完成同内核对照，393/393 次会话通过**。源码压缩包与作者记录的 SHA-256 一致，OverlayFS 源文件也通过逐文件核验。测试统一运行于 Linux 6.8.0-147 和 Ext4，完整结果见 [实验报告](RQ5_DELTAFS_COMPARISON_20261117.md)。

此前“缺少内核源码”的判断只覆盖了第二版归档，遗漏了第一版 Git 对象中的源码包，现已修正。下文保留各次搜索发现及证据。

## 2026-11-17：从第一版归档找回论文内核实现

[Zenodo 第一版记录 23202153](https://zenodo.org/records/23202153) 提供 356,729,974 字节的 `DeltaBox-ATC2026.tar.gz`。下载文件的 MD5 为 `fa93443e92e39b71b9ff8fff639821ef`，与记录一致。第二版归档为 118,182,728 字节。

第一版工作目录未直接包含源码包，但其 `.git` 对象和索引保留了 `linux-6.8.tar`。通过 `git show HEAD:linux-6.8.tar` 恢复出 1,464,176,640 字节的源码 tar，Git blob 为 `5f5ce1f3831c8954ab7b3932aef182f56083e5ef`。源码 tar 的 SHA-256 为 `456df46277ccee96196f5dbd7d61d01f8f57c02e100fc6f693d523b3d0a92dbe`，与第一版 `linux/BUILD-PROVENANCE.json` 完全一致。17 个 `fs/overlayfs` 源文件与 `SOURCE-MANIFEST.jsonl` 的逐文件校验一致；其中包含 `OVL_IOCTL_CHECKPOINT` 的完整实现，`file.c` 也包含作者记录的旧 FD 修复。

恢复的源码位置：

```
/home/bfq/agenttx-workspace/rq5-20261113/sources/delta-box-ae-source-20261116/v1-source/linux-6.8.tar
```

实验使用独立副本，保留原始源码。调整模块名、文件系统注册名、inode cache 名和内建头文件引用路径，并内联未导出给模块的 `__iget()` 原始函数体，使 DeltaFS 可与原生 OverlayFS 共存，随后针对现有 6.8.0-147 内核编译并验证通过。修改和构建日志保存在 `/home/bfq/agenttx-workspace/rq5-20261113/deltafs-benchmark-20261117/`。来源证据见 [kernel-source-recovered-20261117.json](../review/rq5-deltafs-source-audit-20261116/kernel-source-recovered-20261117.json)。

## 已核验的公开材料

| 材料 | 核验结果 |
| --- | --- |
| [官方 AE 仓库](https://github.com/delta-box/delta-box-ae) | 可访问；核验提交 `11b1f535c9d3f47d5901511afca60fe8f3d0defe`。 |
| [内核说明](https://github.com/delta-box/delta-box-ae/blob/11b1f535c9d3f47d5901511afca60fe8f3d0defe/linux/README.md) | 提供预编译 Linux 6.8 内核、配置及一个增量修复补丁；完整 DeltaFS 实现指向独立仓库。 |
| [所链接的内核仓库](https://github.com/delta-box/d-overlayfs) | GitHub 仓库 API、`main` 树以及已记录源码提交的查询均返回 HTTP 404；这只说明当前公开访问不可用。 |
| [作者个人主页仓库](https://github.com/dongyunpeng-sjtu/deltabox) | README 的 Code Release 段仍写内核补丁尚未公开；这是该页面的陈述，不代表 AE 用户态代码未公开。 |
| [Zenodo 归档](https://doi.org/10.5281/zenodo.23202152) | 重定向至记录 `23408664`，已下载 `DeltaBox-ATC2026-v2.tar.gz`；包含预编译内核，未包含完整 `fs/overlayfs` 源码。归档 Git 历史只有一次导入提交。 |

归档内核 SHA-256 为 `0ba5e9ee744bcb2bac3391e967cf36cfeb10fd3add067055321cb136cd5004c0`，与作者发布的校验清单一致。其配置关闭了 `CONFIG_MEMCG`，不能直接运行现有的内存 cgroup 测量协议。公开的 `linux/overlayfs-fix.patch` 仅修复匿名 backing 的旧文件描述符行为，不是完整 DeltaFS 补丁。

已尝试 Google、Google 香港及文本代理搜索，均连接超时；随后核查 GitHub 仓库搜索、组织及作者仓库列表、AE 分支和 release 附件，未找到另一份公开的 DeltaFS 内核实现。`deltabox-lite` 中的 FUSE 实现与论文中的内核 DeltaFS 不同，未用其代替。

## 2026-11-17：已取得完整的公开 FS 源码

官方 [delta-box/deltabox-lite](https://github.com/delta-box/deltabox-lite) 仓库包含可独立构建的 [deltafs-fuse](https://github.com/delta-box/deltabox-lite/tree/9524f95eb5631dc0344b048ed7049efca34ca071/deltafs-fuse)，当前包版本为 `0.4.2`。本次完整源码包的 Git 提交由 tar 的 PAX comment 标识为 `9524f95eb5631dc0344b048ed7049efca34ca071`，已经下载并解包至：

```
/home/bfq/agenttx-workspace/rq5-20261113/sources/deltabox-lite-source-20261117/deltabox-lite-main
```

核验了实际实现，而不只是文档：`deltafs-fuse/src/engine/mod.rs` 提供 `checkpoint`、`rollback` 和 `commit`；`src/passthrough.rs` 提供 FUSE 文件操作适配；`src/control.rs` 提供 Unix socket 控制协议。README 指定构建命令为 `cargo build --release --features fuse`。该引擎通过记录修改前的数据和 epoch journal 维护恢复状态，与论文的 OverlayFS 内核层切换实现不同。源码包及关键文件身份见 [public-fs-source-20261117.json](../review/rq5-deltafs-source-audit-20261116/public-fs-source-20261117.json)。

进一步检查 AE 归档中的 `ae/docs/github-migration.md` 和 `ae/images/README.md`，发现明确的源码访问说明：

> `d-overlayfs`：Private；提供 Linux 6.8 源码及独立的旧 FD 修复提交。

> 需要含 DeltaBox 修改的 Linux 6.8 源码。从私有 GitHub d-overlayfs 源码仓库获取，先由维护者授予访问权限。

因此，论文内核版的获取问题有明确的上游说明，不再仅依赖 HTTP 404 判断。AE 记录的内核源码提交为 `256fb32c4e78474ceec2ba9ac44deeeb7d97fbb5`，基线分支为 `ae-source-6819771`。原文已保存至 `review/rq5-deltafs-source-audit-20261116/evidence/ae/`。

## 统一内核后的测试协议

全部配置统一 Linux 内核版本、构建配置及 Ext4 后端。保留原生 OverlayFS 作为对照，使用真实 DeltaFS 的层切换接口。现已取得完整源码，并在可共存模块上完成测试；此前不同版本预编译内核的试启动已停止，没有执行工作负载。

保持现有 RQ5 条件：4 vCPU、12 GiB RAM；原始 Rust `checkpoint-scaling` 的 commit 模式，100 个目标文件及每轮覆盖的 10 个种子文件，每个 4 KiB；快照深度为 1、10、30。只测试文件系统，不运行模型、agent、错误定位或 CRIU。

分别测量单次快照延迟、包含清理的完整生命周期、额外 Ext4 分配块和额外内存占用。延迟使用 20 次正式重复；资源使用 10 次有/无快照的配对重复，写入与快照请求保持一致。正式测量前检查三个不同历史版本，正式样本核验最终文件内容和权限。延迟与资源采样分开，保留全部样本并随机化顺序。

未向论文或现有性能汇总写入 DeltaFS 数据。源码证据、归档身份及协议见 [manifest.json](../review/rq5-deltafs-source-audit-20261116/manifest.json)。
