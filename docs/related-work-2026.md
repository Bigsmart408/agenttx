# AgentTX 相关工作与开源基线调研（2026）

调研截止日期：**2026-08-06**。

范围：面向多步 Coding Agent / AI Agent 的文件系统副作用隔离、事务、checkpoint/rollback、selective commit、因果依赖追踪与策略执行。优先纳入 OSDI/NSDI/SOSP/ASPLOS 及 2026 年最新 arXiv 工作，并区分“论文公开”和“代码可复现”。

## 1. AgentTX 的准确定位

AgentTX 的事务单位是完整 agent trajectory，而不是单条命令或单个容器。它在 tool-call 边界记录文件系统读写与负依赖，把多步副作用组织成 causal effect DAG，在共享 semisolate 中保持未提交状态，并提供：

- commit frontier：只提交已批准的前沿，后续效果继续保持 speculative；
- causal rollback：回滚生产者及其依赖者，同时保留无关的更晚步骤；
- WAL / recovery：提交崩溃后恢复 session 与 host 的一致状态；
- 无特权、语言无关的 opaque-command 拦截。

一句话差异：**现有 checkpoint/branch 系统通常按时间点或整个分支回滚；AgentTX 按 effect causality 回滚 trajectory 中的非连续步骤。**

## 2. 主要结论

1. **最接近 AgentTX 的直接竞品是 YoloFS。** 它同样针对 agent 文件系统误操作，提供 staging、snapshot 和 progressive permission；但论文没有描述 per-tool causal DAG、非连续因果回滚或 commit frontier。
2. **AgentTX 的基础机制来自 try。** try 已支持 effect capture、introspection、optional application 和 effect stacking。论文不能把这些原语写成 AgentTX 首创；AgentTX 的贡献应落在 trajectory orchestration、causal recovery、commit frontier 和 durable recovery。
3. **BranchFS 是最合适的开源事务语义基线。** 它支持嵌套 COW branch 和原子 commit/abort，但操作单位是整个 leaf branch。
4. **Waypoint 是最合适的完整状态回滚基线。** 它覆盖 filesystem、process、memory 和 PTY，范围强于 AgentTX v0，但回滚是完整 temporal checkpoint。
5. **DeltaBox 和 Crab 是最新且重要的 checkpoint 工作，但不是 causal rollback。** 它们分别优化完整 sandbox C/R 的延迟和 checkpoint 时机。
6. **Sandlock、ActPlane 属于互补工作。** 前者强调 confinement 和单次运行 COW，后者强调跨事件 OS policy；二者都不提供 trajectory 内的选择性恢复。

## 3. 会议与 arXiv 工作差异矩阵

| 系统 | 发表状态 | 核心单位/机制 | 恢复或提交语义 | 开源状态 | 相对 AgentTX 的关键差异 |
|---|---|---|---|---|---|
| [try](https://www.usenix.org/conference/osdi26/presentation/lamprou) | OSDI '26 Best Paper + Distinguished Artifact | 单个 opaque command 的无特权 semisolate；effect inspection、optional apply、stacking | 由调用者选择 effect/layer | [MIT](https://github.com/binpash/try) | AgentTX 基于 try，新增 trajectory 边界、自动 causal ledger、cascade rollback、commit frontier 与 WAL |
| [hS](https://www.usenix.org/conference/osdi26/technical-sessions) | OSDI '26 | 动态检测 shell subprocess effect，乱序 speculative execution | 无冲突时选择性提交独立 subprocess effect | 截止调研未找到官方公开 artifact | 已证明动态依赖和 selective commit 不是 AgentTX 首创；目标是脚本并行加速，不是 agent 轨迹恢复 |
| [Incr](https://www.usenix.org/conference/osdi26/presentation/xie-yizheng) | OSDI '26 | effect/dependency analysis + shell 增量重执行 | 复用仍有效的中间结果 | 截止调研未找到官方公开 artifact | 优化多次执行间的复用；AgentTX 控制一次持续演化的 trajectory |
| [YoloFS](https://arxiv.org/abs/2604.13536) | arXiv 2026 | stackable kernel FS；staging、snapshot、progressive permission | session staging、snapshot/time travel、最终 commit | arXiv/论文页未给出公开代码 | YoloFS 的完整 mediation、权限 UX 和 FS data path 更强；AgentTX 强在 per-tool 因果关系、非连续回滚、commit frontier、无特权部署 |
| [Fork, Explore, Commit / BranchFS](https://arxiv.org/abs/2602.08199) | AgenticOS Workshop @ ASPLOS '26 | FUSE COW nested branches，支持并行 agent | 整个 leaf branch 原子 commit/abort | [MIT](https://github.com/multikernel/branchfs) | BranchFS 是 branch-granular；AgentTX 可在一条线性轨迹内部删除 producer+dependents 并保留独立后续工作 |
| [Waypoint](https://daplab.cs.columbia.edu/projects/waypoint/) | Systems for Agentic AI Workshop @ SOSP '25 | OverlayFS + CRIU 捕获 FS、process、memory、PTY | 完整 temporal checkpoint/restore/branch | [Apache-2.0](https://github.com/Alex-XJK/waypoint) | Waypoint 状态覆盖更完整；AgentTX 的优势是 causal/selective filesystem recovery |
| [DeltaBox](https://arxiv.org/abs/2605.22781) | arXiv 2026 | DeltaFS layers + DeltaCR incremental process C/R | 完整 sandbox checkpoint/rollback | [项目页](https://github.com/dongyunpeng-sjtu/deltabox)说明代码尚未公开 | 报告 14 ms checkpoint、5 ms rollback；没有 per-effect causal rollback/selective commit |
| [Crab](https://arxiv.org/abs/2604.28138) | arXiv 2026 | eBPF 判断 turn 是否有 recovery-relevant state；配合 ZFS/CRIU | 选择 checkpoint 时机，恢复整个 checkpoint | 截止调研未找到公开代码 | 优化“何时做完整 C/R”；AgentTX 解决“哪些因果 effect 应保留/撤销” |
| [Sandlock](https://arxiv.org/abs/2605.26298) | arXiv 2026 | Landlock + seccomp-notify process sandbox；COW workdir | 单次 process 成功提交、失败/预览时整体 abort | [Apache-2.0](https://github.com/multikernel/sandlock) | confinement 更强；没有 multi-step transaction、causal effect DAG 或 session 内选择性回滚 |
| [ActPlane](https://arxiv.org/abs/2606.25189) | arXiv 2026 | eBPF information-flow、cross-event ordering policy | block/notify/kill；不恢复状态 | [MIT](https://github.com/eunomia-bpf/ActPlane) | 适合作为 policy 对比/组合系统，不是 rollback baseline |
| [ACRFence](https://arxiv.org/abs/2603.20625) | arXiv 2026 | 记录不可逆外部 tool effect，防止 semantic rollback attack | checkpoint 后 replay-or-fork | 论文 artifact | 指出 AgentTX v0 的边界：email、支付、云 API 等不可逆非文件系统 effect |

补充工程基线：[AgentFS](https://github.com/tursodatabase/agentfs) 是 MIT 开源的 SQLite-backed agent filesystem，支持 queryable tool history、snapshot 和 time-travel fork。它适合比较 auditability/persistence，但不是论文基线，也不会从任意 opaque POSIX tool call 自动推导 causal effect graph。

## 4. 建议采用的开源对比工作

### 必须纳入后续实验

1. **Bare**：无隔离、无恢复的性能下界与 host pollution 上界。
2. **Per-call try**：每个 tool call 单独 semisolate；说明重复 setup 和跨步状态问题。
3. **Session try**：整段轨迹一个 semisolate；说明 staging 本身不等于 per-tool causal recovery。
4. **AgentTX**：shared semisolate + causal ledger + frontier/WAL。
5. **bubblewrap**：namespace sandbox/whole-session abort；代表传统 sandbox 只有整体丢弃、没有 causal retention。

### 在具备依赖的评测机上纳入

1. **BranchFS（首要开源 baseline）**：比较 whole-branch abort 与 causal non-contiguous rollback。
2. **Waypoint（首要 full-state C/R baseline）**：比较完整 temporal rewind 与只撤销因果相关文件 effect。
3. **Sandlock**：比较单次 run 的 confinement/COW commit-abort 与多步事务。
4. **ActPlane（可选）**：仅用于 policy coverage/overhead，不放进 rollback correctness 排名。
5. **AgentFS（可选）**：仅用于 persistence/auditability，不作为同语义事务系统。

YoloFS、DeltaBox、Crab、hS、Incr 在 artifact 可运行前只做 qualitative comparison。论文中必须把“作者报告的数据”和“本地复现实验”拆成两张表。

## 5. 当前远程 VM 的环境备注

2026-08-06 对 AgentTX 远程 VM 的只读探测结果：

- Linux kernel 5.15.0-139-generic；
- bubblewrap 0.4.0 的 namespace smoke test 通过；
- AgentTX 使用的 try 已在 third_party/try 构建；
- 缺少 Rust、FUSE3/fusermount3、Go、CRIU、Docker；
- 没有免密 sudo。

因此当前 VM 后续可运行 Bare、Per-call try、Session try、AgentTX 和 bubblewrap。BranchFS 缺 Rust+FUSE3，Waypoint 缺 Go+CRIU+权限，Sandlock 要求 Linux 6.12+ 且缺 Rust；这些是环境阻塞，不能写成 baseline 性能失败。本轮不安装或运行这些外部项目。

## 6. 公平评测设计

### 核心语义 workload

1. **Causal retention**：step 0 写 x；step 1 读 x 后写 y；step 2 独立写 z；撤销 step 0。AgentTX 应删除 x/y、保留 z。Temporal restore 会同时丢 z，branch/session abort 会丢弃整个分支/会话。
2. **Commit frontier**：提交较早已批准 effect，较晚步骤继续 speculative。
3. **Crash injection**：在 multi-path commit 中途杀进程，重启后检查 host、ledger、session 是否一致。
4. **Alias coverage**：父子路径、negative lookup、symlink、hard link、bind mount。
5. **Scaling**：20/100/1000 tool calls，固定 read/write 分布。

### 指标

- 显式批准前的 host pollution；
- rollback precision：错误 effect 是否删除、独立有效工作是否误删；
- recovery correctness / recovery latency；
- per-tool、commit、rollback、E2E 的 p50/p95；
- snapshot/ledger storage amplification；
- kernel/privilege 要求、process-state coverage、接入工作量。

对不能表达 causal non-contiguous rollback 的系统，应执行其原生最近操作（temporal restore、branch abort、whole-run abort），并报告独立工作损失。不能给 baseline 外挂 AgentTX 式 causal scheduler 后再把能力归给 baseline。

## 7. 论文 claim 边界

- 不应声称 AgentTX 首创 effect capture、layering、selective application 或 dependency tracking。
- 可以主张：把长期 adaptive agent trajectory 映射为 durable causal effect transaction，并统一提供 tool-boundary ledger、commit frontier、cascade rollback 和 crash recovery。
- 应主动承认：YoloFS 的完整文件系统 mediation/权限交互更强；Waypoint、DeltaBox、Crab 覆盖 process/memory state；Sandlock、ActPlane 的安全策略覆盖面更广。
- AgentTX 当前最有辨识度的实验不是单纯 latency，而是：**撤销错误因果链时，能否保留时间上更晚但因果独立的有用工作。**
