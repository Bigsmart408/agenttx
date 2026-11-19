# AgentTX 移植到 YoloFS 的可行性评估

**结论：可以移植。因果选择和 agent 上下文同步可以保留，主要工作是替换文件历史与恢复后端。原生 `yolo travel` 不能直接完成我们的选择性恢复。**

本评估核对了当前 AgentTX 实现，以及 RQ5 使用的 YoloFS `8848c24` 源码副本。本轮没有修改运行时代码或 paper，也没有启动新的性能实验。核对的 YoloFS 文件和 SHA-256 保存在 [source-manifest.json](../review/yolofs-portability-20261115/source-manifest.json)。

## 哪些部分能够复用

| 部分 | 移植判断 | 需要做的工作 |
|---|---|---|
| 文件依赖图与因果闭包 | 主体可复用 | 把 YoloFS 的路径、对象和版本标识转换为现有 Effect |
| 文件依赖与 agent 上下文依赖的联合闭包 | 可复用 | 保持同一组被撤销调用驱动两边恢复 |
| 错误候选与 verifier 搜索逻辑 | 算法可复用 | 候选验证使用的隔离分支需要后端适配 |
| agent 可见历史的删改与重新投影 | 主体可复用 | 在文件状态安装成功后，再允许 agent 发起下一次请求 |
| 读访问和不存在路径的依赖捕获 | 捕获方案可复用 | 适配挂载路径、进程树与执行环境 |
| 变化发现、历史版本保存、选择性恢复、提交 | 需要改造 | 替换 upperdir、whiteout 和 LayerStore 的 OverlayFS 假设 |
| 工具运行器与持续进程 | 需要改造 | 接入持久执行环境，处理开放文件描述符 |

AgentTX 的 `_unified_causal_targets()` 对文件依赖和上下文依赖反复取闭包；`_rollback_selected_targets()` 计算每组路径应恢复到哪个调用之前，再交给后端恢复。这些选择规则没有要求底层必须是 OverlayFS。当前代码仍有直接读取 upperdir 的路径解析，因此替换范围也包括 runtime 的少数辅助接口，并非只换一个挂载命令。

源码：[runtime.py](../src/agenttx/runtime.py)、[conversation.py](../src/agenttx/conversation.py)、[semisolate.py](../src/agenttx/semisolate.py)、[layers.py](../src/agenttx/layers.py)。

## YoloFS 提供了合适的基础，但还缺少什么

### 1. 文件映射与内容分离，有利于组合恢复状态

YoloFS 用目录树描述路径最终对应的内容：暂存 inode、底层路径重定向，或者删除标记。其 RESTORE/TRAVEL 接口能够接收用户态序列化的整棵树。因而可以设计一个适配器，保留独立对象的当前映射，并把受影响对象替换为需要的历史版本。

例如，错误修改 A，随后独立修改 B，再读取 A 生成 C。AgentTX 仍然负责选择撤销第一、第三次调用；YoloFS 后端负责恢复 A、去掉 C，并保留 B。单纯 travel 到错误之前会丢掉 B，需要补上这一步组合。

源码：[Backing 定义](../review/yolofs-portability-20261115/sources/user/journal/types.rs)、[视图安装接口](../review/yolofs-portability-20261115/sources/kmod/ioctl.c)。

### 2. 不能把 YoloFS 的 journal 当作每条命令的完整版本历史

当前 YoloFS 在同一快照代内重复写一个已暂存文件时，会复用并修改同一个 inode。只有代际变化等条件使它重新 COW 时，才产生新的内容版本。因此，如果正确调用先把 A 写成 A1，错误调用随后把它写成 A2，两次调用之间没有保护 A1 的版本记录，仅凭 journal 和当前 inode 不一定能找回 A1。

我们可以继续采用“已有快照加不可变增量”的方案：对需要保留的变化记录内容和拓扑版本，在恢复时解析所需版本。**不需要为每条命令创建完整文件系统快照，但必须保存能够重建所需状态的历史信息。** 后续若希望直接复用 YoloFS inode store，需要增加版本固定或对象级 COW 的支持，不能把所有暂存 inode 都视为不可变历史。

源码：[同代写入与 COW 判断](../review/yolofs-portability-20261115/sources/kmod/file.c)、[AgentTX 增量历史](../src/agenttx/layers.py)。

此外，YoloFS journal 记录 staging、删除、重命名、快照、travel 等事件。访问审计中的 G 记录覆盖需要询问或被拒绝的访问，不能替代我们需要的完整读依赖和不存在路径的依赖捕获。现有 eBPF/strace 捕获仍需保留并适配。

源码：[journal 格式](../review/yolofs-portability-20261115/sources/kmod/journal.c)。

### 3. 组合后的视图必须和提交、重新挂载使用同一份状态

现有 travel 的正常流程是：从一个历史 marker 重建树，将树交给内核安装，并追加 `T(target_gen)`。后续 commit 和重新挂载会根据 journal 再次重建状态。

如果直接把“历史 A + 当前 B”的自定义树交给 travel，却仍只记录某个旧 marker，那么当前挂载可能看起来正确，但 commit 或重新挂载会按旧 marker 重建，无法恢复自定义组合。RESTORE 本身也不写 journal，单独调用它同样不够。

因此，直接组合映射的方案需要同时保存该组合的持久描述，并让恢复、提交、重新挂载理解它。可以扩展 YoloFS 的 journal/重放路径，或者由 AgentTX 适配层维护权威状态并接管这些入口；不能仅修改传给 travel 的树。

源码：[travel 用户态流程](../review/yolofs-portability-20261115/sources/user/cmd/travel.rs)、[内核 travel/restore](../review/yolofs-portability-20261115/sources/kmod/ioctl.c)、[commit 重建状态](../review/yolofs-portability-20261115/sources/user/cmd/commit.rs)、[journal 重放](../review/yolofs-portability-20261115/sources/user/journal/core.rs)。

### 4. 持续进程和文件拓扑需要专门适配

YoloFS 的 snapshot、restore、travel 会在存在暂存写文件描述符时返回 `EBUSY`。仅暂停进程不会关闭这些描述符，因此不能把“暂停 agent”当作已经满足恢复条件。需要处理持续打开的写文件和 mmap，或扩展后端的视图切换支持。

原生 `yolo run` 每次建立新的 PID namespace，不能直接用作我们保留后台服务的持久工具运行器。可以使用 YoloFS 的挂载能力，另行接入持久运行器。

文件拓扑也并非完全等价：所核对版本的目录 inode operations 没有 `.link`，不支持直接创建硬链接；而 AgentTX 已有硬链接对象跟踪。要完整替换后端，需要补齐相应语义，并验证目录移动、符号链接和权限等操作。

源码：[FD 条件与视图切换](../review/yolofs-portability-20261115/sources/kmod/ioctl.c)、[原生运行器](../review/yolofs-portability-20261115/sources/user/cmd/run.rs)、[inode operations](../review/yolofs-portability-20261115/sources/kmod/inode.c)。

## 建议的移植顺序

1. **先做可选后端。** 保留因果选择、定位与上下文投影，抽出执行、变化捕获、版本读取、恢复、分支和提交接口。第一版继续保存不可变增量，通过挂载视图上的正常文件操作恢复选中的对象，使变更沿用 YoloFS 的原生日志路径；普通文件的原型可以先不改内核。
2. **先验证恢复语义。** 覆盖 A/B/C 交错依赖、同代连续覆盖、跨快照恢复，以及恢复后的继续执行、提交和重新挂载；再验证 agent 可见历史与保留文件一致。持续写 FD 和硬链接是完整兼容时必须处理的项目。
3. **再减少恢复的数据复制。** 通过持久的组合映射直接引用已保护的版本，配套修改 journal/重放和必要的内核接口。这样才能利用 YoloFS 的映射结构优化恢复，而不只是将它用作普通挂载层。

YoloFS 在最近测试中的快照和重命名性能说明它值得作为后端尝试；移植后的版本保护、依赖捕获和上下文处理仍有成本，具体收益需要在完成适配后测量。现有基础后端数据见 [RQ5 对照报告](RQ5_WORKLOAD_COMPARISON_20261115.md)。

**总体判断：核心策略的可移植性高，完整后端适配的工作量中等偏大。建议保留 OverlayFS 后端，增加 YoloFS 后端，先证明选择性恢复语义一致，再比较完整运行开销。**
