# AgentTX Paper Review Comments：系统一致性与 Motivation

**评审范围：** 对照当前实现与 `paper/main.tex`，重点检查系统架构、因果语义、
恢复机制和 Motivation。本文档不讨论 workload 的选择和覆盖范围。

**总体评价：** 论文的核心 thesis 是成立的：speculation 的生命周期和 recovery
的作用范围应当解耦；当有效工作与错误派生状态在时间线上交错时，causal
dependency 比 temporal order 更适合作为恢复依据。当前系统已经实现了这个
thesis 的一个有意义版本。但是，论文有若干地方描述了一个比代码更完整、更强的
系统。最主要的问题不是措辞，而是 Overview 混淆了控制面与数据面、
reconstruction 描述了尚未实现的 fresh-generation 原子切换、Motivation 又把策略
模拟结果放在外部系统名称之下。这些问题应在继续润色论文之前修正。

## Major Comment 1：区分 Runtime 控制面与 OverlayFS 数据面

当前 Overview 把 `E1 -> E2 -> ...` 画在 OverlayFS 内部。这会让读者认为
OverlayFS 保存 effect record 或维护调用历史，但实际架构不是这样。

当前实现的数据路径是：

```text
Agent / tool call
        |
        v
AgentTX runtime / interceptor
        |
        +---- execute command ----> shared OverlayFS view
        |                              |
        |                  upperdir difference: W/D
        |                  syscall observation: R/N
        |                              |
        <--------- observed effects ---+
        |
        v
User-space step ledger / causal DAG
        |
        +---- causal closure ----> snapshot-based path reconstruction
        |
        +---- policy + WAL ------> host filesystem
```

OverlayFS 是 speculative filesystem state 的数据面；用户态 ledger 是 causal
control plane。包括 DAG 在内的 session metadata 持久化在 `agenttx.json` 中，
该文件与 `upperdir` 同级，而不是写入 OverlayFS inode、xattr 或内核元数据。

**要求修改：**

1. 删除 OverlayFS 框内的 effect 节点链。
2. 将 Agent 到 Runtime 的 `Cmd + effects` 改为 `command`。
3. 分别画出 upperdir diff 提供 mutation evidence，以及 syscall tracer/trusted
   declaration 提供 observation evidence。
4. commit 路径应从 reconstructed speculative view 经过 policy 和 WAL 到 host，
   而不是从 DAG 直接指向 filesystem。
5. DAG 应明确位于 AgentTX Runtime 边界内。

相关实现：`src/agenttx/runtime.py:326`、`src/agenttx/semisolate.py:949`、
`src/agenttx/ledger.py:75`。

## Major Comment 2：统一使用 step-level DAG 语义

实现维护的不是 effect-level graph。每个节点对应一次 tool step：

```text
Step(step_id, tool_name, effects[], parents[], status)
```

Runtime 将新 step 的 effects 与 active earlier steps 的 effects 比较，然后把依赖
记录为 step ID 之间的边。Section 4 的部分文字已经正确描述了这一点，但 Overview
和诸如 “a failed effect becomes the root” 的表述仍然混淆了 effect 与 step。

论文应统一使用 **effect-derived step DAG** 或 **causal step ledger**。当前 API
只能用 `step_id` 作为 rollback root，不能单独选择一次调用内的某个 effect。
因此，如果一个 opaque command 同时修改了有效文件和错误文件，当前恢复粒度不能
把该 command 内部拆开。这是系统语义边界，必须明确。

论文还应删除“保留调用内 observable effect order”的说法。trace effects 和
mutation effects 在进入 ledger 前会被去重或排序，ledger 也只推导跨 step 的依赖。
当前支持的是 per-call net mutations，加上捕获到的 reads 和 negative lookups，
不是完整的调用内 effect history。

相关实现：`src/agenttx/ledger.py:46`、`src/agenttx/ledger.py:120`、
`src/agenttx/semisolate.py:1041`。

## Major Comment 3：按实际代码重写 Causal Reconstruction

Section 5 当前声称 AgentTX 会构建 fresh upper generation、把 retained speculative
suffix rebase 上去、验证结果，然后切换到 live session。当前 causal rollback 并不
执行这套算法。

真实实现流程是：

1. 在用户态 ledger 中计算 failed step 的 transitive descendant closure。
2. 收集 closure 中所有 `WRITE/DELETE` 路径。
3. 如果 selected path 与 active retained effect 重叠，则拒绝恢复。
4. 停止 persistent worker。
5. 从 `before_min(selected_steps)` 快照中，把 selected paths 直接恢复到当前
   `upperdir`。
6. 将 selected ledger steps 标记为 rolled back，并持久化 metadata。

系统确实会在**部分 commit 且仍有 retained suffix**时重建一个干净的 upper
generation，但这是 commit/rebase 路径，不是 causal rollback 算法。论文当前把两条
路径合并成了一条不存在的实现。

建议将核心 claim 改为：

> AgentTX performs conservative path-selective reconstruction from a
> step-indexed upperdir snapshot. It preserves non-overlapping retained effects
> and rejects recovery when selected paths overlap retained effects.

除非先补齐 staged reconstruction、完整验证和 generation activation，否则论文不能
声称 rollback 使用 atomic generation switch。当前 restore 会原地修改多个 upperdir
路径；如果中途发生异常，speculative view 可能处于部分恢复状态。

相关实现：`src/agenttx/runtime.py:394`、`src/agenttx/semisolate.py:1584`、
`src/agenttx/layers.py:479`。

## Major Comment 4：收窄 Object Identity 与 Versioning 的 claim

“versioned object identity” 容易让读者理解为通用 object graph 或 per-object
MVCC。当前实现的边界更窄：

- 普通 dependency construction 基于 path，并把 ancestor/descendant 路径视为
  overlap；
- 支持的 symlink ancestor 会被规范化为额外 path effects；
- host-side catalog 为已知的、预先存在的 regular-file hard-link groups 分配
  session object ID；
- catalog 在 session 开始和 commit generation 边界刷新，而不是每次 speculative
  topology change 后刷新；
- publication 可以发现并保持部分 upperdir hard-link group，但这不表示新建 alias
  的所有访问都获得了同一个 ledger object ID；
- bind mounts、external aliases、任意 replacement history 和通用 multi-object
  topology 不在当前 contract 内。

历史状态也不是通用的 per-object version store。它是 step-indexed
`before_NNNN` upperdir snapshots，并用 content-addressed blobs 去重 regular-file
内容。`Effect.object_version` 主要是 catalog generation annotation，不是每个文件
对象的完整版本号。

建议使用以下措辞：

- 将“general versioned object identity”改为“step-indexed history and a bounded
  alias-identity contract”；
- 明确区分 **dependency identity** 与 **publication topology preservation**；
- 明确说明 upper-created hard-link 的 publication support 强于 DAG 对其所有 alias
  dependency 的推导能力。

相关实现：`src/agenttx/object_identity.py:78`、
`src/agenttx/semisolate.py:1326`。

## Major Comment 5：准确描述 Effect Capture 的完整性边界

当前 Runtime 有两个互补的 evidence sources：

- `W/D`：tool call 前后 upperdir fingerprint 的净变化；
- `R/N`：strace 或 persistent eBPF 捕获的受支持 pathname operations，也可以来自
  trusted declarations。

当 tracer 不可用或没有产生可用 stream 时，Runtime 会 fail closed；但这并不证明
所有 filesystem accesses 都被捕获。parser 只识别有限 syscall set，其他 traced
syscalls 可能被忽略。trusted declaration 也可能随工具实现变化而失效。

论文必须区分三种情况：

1. tracer/session failure：当前实现 fail closed；
2. 被明确检测到且无法解析的 descriptor-relative path：在受支持路径上 fail
   closed；
3. unsupported/unmodeled syscall：当前实现没有完整的 fail-closed coverage gate。

不要声称所有 unsupported identity/access 都会被记录为 unknown。Ledger 没有通用
`UNKNOWN` effect kind，某些不支持的行为只是没有进入图。

Implementation 应把 strace 和 eBPF 描述成可替换的 observation backends；主要
evaluation 仍可固定使用 strace 以保证可复现性。

相关实现：`src/agenttx/trace.py:140`、`src/agenttx/bpf_trace.py:60`、
`src/agenttx/semisolate.py:1033`。

## Major Comment 6：区分 Fault Localization、Closure 与 Recovery

AgentTX 不会自动判定某个 step 在语义上是 faulty。Runtime 负责记录 tool result 和
filesystem evidence；agent、harness、policy 或用户需要提供 rollback root，并显式
调用 causal recovery。command 返回非零也不会自动触发 rollback。

此外，默认 `rollback()` API 仍然是 temporal rollback。非连续 causal recovery
由显式的 `rollback_causal(step_id)` 提供。Discussion 虽然提到这一点，但 Abstract
和 Overview 仍容易让读者认为所有 failure 都会被自动 causal recovery。

论文应明确拆分：

- **fault localization**：产生 failed step ID，不属于核心 AET 算法；
- **closure computation**：AgentTX 根据 ledger 计算；
- **workspace reconstruction**：AgentTX 在 overlap 和 identity contract 下执行。

## Major Comment 7：纳入 Conversation Consistency，但不要过度扩展 AET

当前系统包含论文尚未描述的新能力：native agent conversation rewind。只恢复
filesystem 会让模型继续相信已被撤销的 tool results。当前 adapter 持久化
tool-call spans，将 ledger parents 与启发式 copied-result dependencies 合并，
invalidate 受影响的 conversation spans，并插入权威 recovery notice。external
black-box agent 只能收到 retained-effects handoff，并不具备完整 resume。

建议把它写成 **agent-adapter context reconciliation layer**，而不是完整的
conversation transaction 或 process checkpoint。现在就把形式化 AET tuple 从
`<V,L,H,F>` 扩展为带 conversation state 的模型，会高估 external adapter 和
hidden model state 的保证。

推荐在论文中这样安排：

- Causal Reconstruction 后增加一段，解释 agent-visible context 为什么必须与
  recovered workspace 对齐；
- Discussion 中区分 native conversation rewind、black-box context projection 与
  full process/model-state checkpoint；
- 在形成统一 contract 和独立 evaluation 前，不把它提升为新的核心 contribution。

相关实现：`src/agenttx/conversation.py:1`、
`src/agenttx/conversation.py:495`。

## Major Comment 8：保持 Publication Guarantee 的准确边界

论文关于 publication 的主体描述与实现基本一致：

- materialization 前执行 policy check；
- frontier 可以发布 historical version，同时保留 later speculative suffix；
- WAL 支持 host materialization 中断后的恢复；
- host 上的 multi-path install 对 concurrent observers 并非原子可见。

这个边界必须在全文保持一致。系统支持的是 **durable publication** 和
**crash-recoverable commit**，不是 ACID 风格的 multi-path atomic visibility。

新 Runtime 中可选的 external-write policy 也不应扩大论文的 filesystem model。
它只是 integration escape hatch，而 read dependency capture 和 identity reasoning
仍以 workspace 为中心。

## Motivation Review

### 核心问题

当前 Motivation 从 isolation overhead 开始，容易使论文看起来主要在论证 AgentTX
是一种更快的 OverlayFS 复用方式。这不是最强贡献，也会引出不必要的问题：完整
系统仍慢于 bare，tracing 和 snapshot 成本也没有消失。

Motivation 的主线应当是语义问题：

> A long-lived speculative workspace is useful for agent continuity, but the
> correct recovery unit can be a non-contiguous causal subset of that workspace.
> Therefore, the speculation unit and recovery unit must be decoupled.

性能应当是 feasibility constraint 和 evaluation question，而不是 root cause。

### 当前 Baseline 表述存在的问题

Motivation figures 将本地 configuration 或 recovery-policy emulation 标为 YoloFS、
BranchFS、Crab 和 DeltaBox。但仓库其他文档已经明确说明，这些 external artifacts
多数没有实际运行。把 per-call `try` proxy 映射到 YoloFS/BranchFS、把 checkpoint
floor 映射到 Crab/DeltaBox，不能得到这些系统的测量结果。

建议：

- 改用 `per-command remount`、`session/branch abort`、`temporal checkpoint` 等
  mechanism labels；
- 将 recovery experiment 定义为同一 execution 上的 counterfactual policy
  evaluation；
- named external systems 只放在 Background 和 Related Work，除非确实执行了其
  artifact；
- 不能把本地 proxy measurement 放进以外部系统命名的表格行。

当前 `causal_without_dependencies` 也不应称作 “command undo without
dependencies”。它仍包含 write/write edges 和 hierarchical path edges。准确名称应
是 `causal selection without read dependencies` 或 `no-read-dependency ablation`。

### 推荐的 Motivation 结构

#### 3.1 Temporal Order Is Not Causal Dependence

从一个交错 trajectory 开始：

```text
s0: write faulty formatter
 |\
 | +--> s1: read formatter, generate report --> s3: consume faulty report
 |
 +     s2: edit unrelated documentation
```

期望 rollback set 是 `{s0,s1,s3}`。root-only undo 会留下 derived invalid state；
suffix rollback 会同时删除 `s2`；whole-session abort 会删除全部状态。该例子应成为
第一张 motivation figure，或者合并图的第一个 panel。

#### 3.2 Recovery-Unit Tradeoff

在相同 execution 上评估三类抽象策略：

- root-only recovery；
- temporal suffix recovery；
- Whole branch abort。

同时报告 useful independent work retained 和 invalid descendants removed。当前
41%/0%/4% 可以使用，但必须明确它们来自 controlled policy emulation，不是 external
artifact measurement。这些比例也依赖实验设置中的 interleaving distribution；
它们证明 granularity mechanism，而不是任意 agent trajectory 中的出现频率。

#### 3.3 Dependencies Are Hidden at Tool Boundaries

说明为什么只改变 selection policy 仍然不够。commands 可以启动 descendants，
并通过 reads、failed lookups、generated files 和 aliases 传播状态。用受支持的
traced causal path 对比 no-read-dependency ablation，由此推出必须依据 observed
effects，而不能根据 command text 猜测依赖。

必须准确披露 evidence provenance：当前 controlled causal-retention benchmark 中，
多数 causal modes 使用 trusted `extra_reads`；只有显式 traced mode 从 tracer 获取
reads。如果 figure 用于论证 automatic capture，就应使用 traced mode，或者明确说明
依赖来自 trusted declarations。

#### 3.4 Derived Requirements

Motivation 最后只推出四个 requirements：

1. **Continuous speculation：** later calls 能看到 earlier uncommitted state，host
   在 approval 前保持不变。
2. **Effect-derived causality：** Runtime 根据受支持的 `R/N/W/D` evidence 构建
   step DAG。
3. **Historical reconstruction：** 保存足够的 step-indexed state，以删除 selected
   closure 并保留 non-overlapping later effects。
4. **Separate publication：** recovery 只改变 speculative state；只有经过 policy
   检查且 crash-recoverable 的 frontier 才能改变 host。

Conversation reconciliation 可以作为 end-to-end consequence 简短提出，但当前不应
成为第五个形式化 requirement。

### 当前 Performance Motivation 的处理方式

建议把 `Isolation Overhead Overview` 的主体移到 Evaluation RQ2。如果 Motivation
需要一个 practicality 结果，只保留一个紧凑 panel/table，并且只使用真实执行过的
configuration：

```text
unsafe bare execution
per-call try
persistent shared semisolate
full AgentTX
```

结论应保持克制：persistent execution 可以摊薄重复 setup，但 effect capture 和
history maintenance 仍有可测成本。论文成立与否不应依赖某台 VM 上 per-call `try`
的具体延迟。

## 建议的 Motivation 核心段落

可以围绕下面这段论证重写：

> Coding-agent trajectories interleave dependent and independent filesystem
> effects. A failure therefore does not generally invalidate either one command
> or one temporal suffix. Root-only recovery leaves derived state behind,
> whereas temporal and whole-session recovery discard valid later work.
> Recovering the correct subset requires dependencies that are not visible in
> command text, including reads, negative lookups, and supported aliases. These
> observations require a runtime that keeps the trajectory speculative, records
> an effect-derived step DAG, reconstructs a selected causal closure from
> historical state, and publishes recovered state through a separate durable
> frontier.

这条论证可以直接引出 AET，并且不依赖具体 OverlayFS 实现或当前性能数字。

## Revision Checklist

### Claim correctness blockers

- [ ] 重画 Overview，明确 Runtime/DAG 与 OverlayFS 的不同角色。
- [ ] 将 fresh-generation causal rollback 改成当前实现的 in-place、fail-closed path
      reconstruction，或者先实现论文描述的算法。
- [ ] 删除保留 intra-call effect order 的 claim。
- [ ] 将 object identity 收窄到实际 alias contract。
- [ ] 区分 tracer failure 与 unsupported-syscall coverage。
- [ ] 说明 fault-root selection 来自系统外部，causal rollback 是显式操作。
- [ ] 从 proxy/policy-emulation 数据中删除 external-system names。

### Conceptual clarity

- [ ] 全文统一使用 `effect-derived step DAG`。
- [ ] 将 history 描述为 step-indexed upperdir snapshots，而不是通用 per-object
      MVCC。
- [ ] 将 performance-first Motivation 移到 Evaluation RQ2。
- [ ] 将 conversation rewind 定义为 bounded adapter-level reconciliation。
- [ ] 始终区分 crash recoverability 与 externally atomic visibility。

### 建议的论文结构

```text
1. Introduction
2. Background: speculative state and recovery units
3. Observation and Motivation
   3.1 Temporal order is not causality
   3.2 Recovery-unit tradeoff
   3.3 Hidden dependencies
   3.4 Derived requirements
4. Agent Effect Transactions
5. AgentTX Implementation
6. Evaluation
   RQ1 recovery semantics
   RQ2 runtime cost
   RQ3 mechanism boundaries
   RQ4 agent integration
   RQ5 crash and session robustness
7. Discussion and Limitations
8. Related Work
9. Conclusion
```

## Final Recommendation

论文应准确声称当前系统已经证明的内容：在 shared speculative filesystem 之上维护
用户态 effect-derived step ledger；显式选择 causal closure；从 step-indexed
snapshots 保守地恢复 non-overlapping paths；通过 policy-checked、crash-recoverable
frontier 发布状态。这已经构成一个连贯的 systems contribution。相反，general
object versioning、complete mediation、atomic rollback generation switching，以及对
未实际运行 external systems 的性能优势，会削弱而不是增强这项贡献。
