# XFS 与 reflink 支持

AgentTX 可以继续使用原生 OverlayFS，将底层文件系统换为开启 reflink 的 XFS。依赖跟踪、恢复范围选择和 agent 上下文恢复沿用现有实现；XFS 负责共享文件数据块，并在后续写入时分离被修改的数据块。

## 使用方式

将工作目录与会话目录放在同一个 XFS 文件系统上，会话目录放在工作目录之外：

```python
from pathlib import Path
from agenttx import AgentTX

tx = AgentTX.begin(
    workdir=Path('/data/xfs/project'),
    session_dir=Path('/data/xfs/agenttx-session'),
    trace_backend='strace',
)
```

仅使用显式文件系统快照时，使用同样的目录布局：

```python
from agenttx.snapshot import SnapshotSession

session = SnapshotSession(
    Path('/data/xfs/project'),
    Path('/data/xfs/snapshot-session'),
    metadata_copy=True,
)
```

两种接口均自动使用可用的 reflink，无需额外开关。`xfs_info /data/xfs` 可检查卷的 `reflink=1` 与 `ftype=1`。工作目录、upper、work、历史对象和发布目标位于同一个 XFS 卷时，可以在需要复制的路径共享数据块。跨文件系统或不支持克隆时，继续复制完整内容。

## 实现

- 普通文件保存、恢复、历史复制和提交 WAL 使用 `FICLONE` 优先的复制入口。源文件与副本具有独立 inode；硬链接别名仍在各自版本内部保持关系。
- 硬链接对象发布同样先尝试 reflink，保留原有的写入完成、元数据和同步处理。批量保存继续使用 `cp --reflink=auto`。
- 元数据模式发布通过 XFS 的 `XFS_IOC_FSGEOMETRY_V1` 读取 UUID，并结合 OverlayFS origin 和完整文件句柄核验原文件，保留已验证的重命名 inode 复用路径。
- 沙箱在构造视图前识别嵌套 Ext4、XFS、Btrfs 数据挂载；对其祖先分开建立 OverlayFS，避免父 lower 视图漏掉子挂载内的工作目录。安装脚本会自动应用这项兼容补丁。
- 显式快照仍通过冻结旧层、保留挂载引用和新建可写层完成。reflink 用于需要复制的文件路径，不增加逐命令完整快照。

## 验证结果

在 Linux `6.8.0-147-generic` 的独立 2 GiB XFS 卷上验证，卷配置为 `reflink=1,ftype=1`。

- 176 项存储与恢复检查通过，覆盖历史版本、选择性恢复、显式快照、发布、元数据、别名、whiteout、特殊节点和提交故障恢复。
- 39 项完整运行时检查通过，覆盖真实命令执行、strace 依赖捕获、选择性提交、回滚和后续执行。
- 同一虚拟机的 Ext4 上 209 项检查通过；6 项需要 XFS/reflink 的检查按预期跳过。

FIEMAP 实测：保存 1 MiB 文件后，两个不同 inode 共享全部 1,048,576 字节；当前文件改写 4 KiB 后，仍共享 1,044,480 字节，历史内容保持原样。选择性恢复后再次共享完整 1 MiB，并保留另一个文件的独立修改。读取的 XFS UUID 与块设备 UUID 一致。

原始日志、JUnit 结果、FIEMAP 物理区间及源码哈希保存在 [验证记录](../review/xfs-reflink-support-20261117/)。真实 XFS 集成检查可通过以下命令重跑；`--basetemp` 应为专用测试目录，pytest 会清空该目录：

```sh
PYTHONPATH=src python3 -m pytest -q \
  --basetemp=/data/xfs/agenttx-test-temp \
  tests/test_xfs_reflink.py tests/test_reflink.py \
  tests/test_snapshot_session.py tests/test_delta_history.py \
  tests/test_recovery.py tests/test_runtime_integration.py tests/test_semisolate.py
```
