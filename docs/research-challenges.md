# AgentTX 的三个核心系统难点

## 1. 不透明工具调用之间的因果依赖难以跟踪

Coding Agent 并不只调用结构化 API，而是持续执行 shell、编译器、测试
框架、包管理器和任意第三方程序。对事务系统而言，这些命令基本都是
opaque tool calls：系统只能看到命令边界，无法从命令文本可靠判断它读取了
什么、写入了什么，以及后续哪个动作依赖了这些结果。

仅记录时间顺序是不够的。步骤 B 晚于步骤 A，并不意味着 B 依赖 A；反过来，
一次通过间接路径完成的读取又可能形成隐藏依赖。如果依赖图发生漏边，因果
回滚会保留已经被污染的派生结果；如果产生过多假边，系统就会退化为普通的
时间点回滚，失去保留独立工作的价值。

AgentTX 在 tool-call 边界捕获 READ、NEGATIVE、WRITE 和 DELETE effects，
将动态文件系统行为转换为跨步骤 effect DAG。这里的关键不是简单记录文件
列表，而是从实际 effect 中恢复 producer--consumer 关系，并对路径层次、
负查找和 symlink alias 进行规范化。

## 2. 路径不等于对象，inode 与别名关系难以建模

文件系统依赖通常以路径表达，但路径不是稳定对象标识。symlink、hard link、
rename 和 bind mount 都可能让多个名字指向同一个底层对象；OverlayFS copy-up
还可能让原本共享 inode 的路径在事务执行期间发生分裂。

如果系统只比较字符串路径，就可能漏掉通过另一个名字发生的读写依赖；如果
系统简单地把相同 inode 的路径永久合并，又可能在 copy-up 已经分裂对象之后
制造错误依赖。因此别名处理必须同时考虑 namespace、对象身份以及具体
snapshot substrate 的可见性语义。

AgentTX 已覆盖路径层次和 symlink ancestor，但 Step 23 的实测表明：Linux
5.15 上的 OverlayFS 会在写入 lower hard link 时拆分 inode，使 sibling alias
在 speculative view 中继续读取旧值。这个问题发生在 ledger 分析之前，不能
只靠补一条 inode dependency edge 修复；完整支持需要 FUSE、内核辅助或不同
的 snapshot substrate。

## 3. 非连续因果回滚后的状态一致性难以保证

识别出因果关系只回答了“应该撤销什么”，并没有回答“怎样安全地撤销”。
传统 checkpoint 可以恢复到一个时间点，但会连同错误之后所有独立工作一起
丢弃。AgentTX 的目标集合可能是时间线上不连续的，例如撤销 `{3, 6, 9}`，
同时保留 `{4, 5, 7, 8}`。

这种 selective reconstruction 必须同时处理：

- 错误 producer 及其 transitive descendants；
- 时间上更晚但因果独立的有效效果；
- 同一路径的多次覆盖、删除、目录和元数据变化；
- partial frontier commit 与保留的 speculative suffix；
- 回滚或提交中途崩溃后的 durable recovery。

因此因果回滚不能实现为简单的 `undo(command)`。AgentTX 根据 per-step
upperdir snapshots 重建目标路径，对 retained-effect overlap 采取 fail-closed，
并通过 commit WAL 保护 host materialization。系统贡献不仅是 effect DAG，
更是将 DAG 中的非连续逻辑撤销集合转换成一致、可提交、可恢复的文件系统
状态。

## 论文中的统一表述

三个难点可以压缩为：

> **Dependency Discovery -- Object Identity -- Selective Reconstruction**

它们依次回答：哪些动作相关、这些路径是否代表同一对象，以及找到错误子图后
如何只撤销该子图而不破坏其他工作。
