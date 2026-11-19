# 快照与提交模式

`agenttx.snapshot.SnapshotSession` 用于只需要底层文件系统快照和最终提交的工作流。调用者直接在 `session.view` 中执行文件操作，在需要保存状态时调用 `snapshot()`。该模式不构造选择性回滚使用的依赖图、逐对象历史索引或 agent 上下文。

底层文件系统支持 Ext4 和开启 reflink 的 XFS。XFS 上需要复制的普通文件优先通过 `FICLONE` 共享数据块，无法克隆时继续普通复制；显式快照仍使用冻结旧层和新增可写层。目录布局与验证结果见 [XFS 支持](XFS_REFLINK_SUPPORT.md)。

```python
from pathlib import Path
from agenttx.snapshot import SnapshotSession

session = SnapshotSession(Path('/data/project'), Path('/data/session'))
try:
    # Run the workload against session.view, then quiesce its writers.
    (session.view / 'result.txt').write_text('version one')
    first = session.snapshot()
    (session.view / 'result.txt').write_text('version two')
    second = session.snapshot()
    session.commit()
finally:
    session.close(destroy=True)
```

创建快照时，将旧挂载变为只读并保留挂载引用，分离当前视图，再用新的 upper 层继续执行。旧版本保持不可变，后续修改由 OverlayFS 完成写时复制；无需复制文件内容或展开完整历史目录。`mount_snapshot(point, target)` 将保存的层组合为只读历史视图。

各层直接存放在状态目录的 `uN` / `wN` 下。小型平坦 upper 层最多检查 257 个目录项：如果某个旧层的全部名称都已被更新的普通文件、符号链接或删除标记遮住，当前视图不再挂载该旧层。旧层的文件与挂载引用继续保留，各历史快照记录自己的 lower 集合。包含子目录、特殊 OverlayFS 可见性属性或较多目录项的层沿用原有叠层方式。

最终提交消费历史状态，只发布当前视图仍然需要的层，按旧到新的顺序合并。完全被覆盖的旧版本由状态目录清理释放，避免反复将同一路径的过时版本发布到底层文件系统。只做最终提交的会话直接处理当前 upper，不额外扫描用于快照压缩的名称集合。

存储路径要求 Linux OverlayFS 挂载权限，调用者在创建快照或提交前暂停文件写入。状态目录位于工作目录之外；RQ5在同一Ext4卷上运行。这里保存的是普通buffered文件系统状态，不增加fsync持久化保证。保留的只读挂载引用在 `seal()`、`commit()` 或 `close()` 时释放；提交会消费暂存历史。快照创建时间与最终释放引用的时间分别记录。

挂载显式使用 `index=off,metacopy=off,redirect_dir=off`，upper 文件保存完整内容。性能比较中的纯 OverlayFS 使用相同参数、层目录、版本压缩和发布原语；比较同时记录快照接口、执行到完整收尾及包含初始化的完整会话。当前优化保持普通 OverlayFS 挂载的同步语义。

针对 lower 文件的重命名和权限修改，可显式选择
`SnapshotSession(workspace, state_dir, metadata_copy=True)`。
该模式启用 OverlayFS 的 `metacopy=on,redirect_dir=on`，先复制元数据，
避免在不改变内容的操作中立即复制并同步每个文件的数据。默认模式保持不变。

元数据模式在最终 `commit()` 时检查能否复用原文件。对平坦文件变更，发布器先
核对 OverlayFS origin 中的文件系统 UUID、文件句柄类型及完整句柄，确认实际数据
仍在原文件中。原文件和当前视图均只有一个硬链接，且原名称已由变更层覆盖时，
发布器可以保留原 inode。被新层遮住的旧元数据条目不再重复补齐内容。

需要复制的数据在合并视图仍挂载时全部准备好；随后释放当前及历史挂载引用，
先将所有可复用原文件移入暂存区，再合并各层，最后将它们直接发布到最终名称。
这一顺序支持重命名链、覆盖、交换及循环，避免提前覆盖另一个待发布的源文件。
权限、所有者、时间和公开扩展属性有变化时，同步应用这些变化；目录时间也保持
为提交前的视图状态。内容复制失败时不修改底层目录，可以重试；发布开始后的失败
保留尚未发布的原文件暂存数据，供检查，直至调用者显式销毁状态目录。

包含目录重定向、硬链接或无法核实原文件身份的变更，继续通过尚未卸载的合并视图
补齐真实内容。准备文件位于挂载层之外，释放全部挂载引用后才替换条目。
目录重定向仍需补齐未修改的后代。显式调用 `seal()` 保持底层工作目录不变，使用
内容补齐路径；直接 `commit()` 才会消费原 inode。`close()` 放弃会话时不做内容补齐。
所有准备、发布和清理工作均计入完成阶段，默认普通挂载模式保持不变。

元数据文件可能引用更老层的数据，因此该模式保留全部历史层，不执行按名称覆盖
进行的层压缩。性能报告同时列出执行阶段和完整收尾，不能只用 rename 的执行时间
评价这一选项。核对记录位于
`review/rq5-overlay-try-audit-20261115/`；本轮没有更改完整因果恢复运行时。
原 inode 复用的代码、功能检查及同批测量记录在
`review/rq5-publisher-inode-reuse-20261115/`。

发布普通文件时直接rename，保留inode的内容、权限和时间，不重复捕获、修改后再修复每个文件的元数据。目录、白化删除和对象类型变化保留处理。可选原生工具将大量删除在C中分类和批量执行；删除-only路径先检查完整来源层，混合层继续走通用处理。

在开发环境编译原生工具：

```bash
scripts/build_snapshot_native.sh
```

没有原生工具时使用Python实现。实验记录编译参数、源码与二进制SHA256；性能结果标明工具是否启用。

需要因果选择、对象版本恢复和agent上下文同步的任务继续使用 `AgentTX` / `SharedSemisolate`。这次新接口为独立的快照/提交模式；完整恢复运行时的默认执行路径未修改。
