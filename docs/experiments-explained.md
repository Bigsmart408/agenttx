# AgentTX 实验术语、设计思路与结果总览

本文档用于统一解释 AgentTX 当前实验中出现的专有名词、对照组、指标、
设计逻辑和结论。它面向论文写作与组内讨论，重点回答三个问题：

1. 每个实验名词到底表示什么，尤其是“乐观 checkpoint”等容易混淆的概念；
2. 为什么要这样设计实验，而不是只跑一个最终性能数字；
3. 当前结果能够支持什么结论，又有哪些结论还不能声称。

所有数字均来自仓库中已经提交的结果文件。不同时间生成的历史优化数据和当前
实现数据会明确区分，不能把它们拼成一次严格配对实验。

---

## 1. 整体实验逻辑

AgentTX 的 motivation 不是单一的“性能不够快”，而是两个同时存在的问题：

### Motivation A：传统恢复粒度过粗

Coding Agent 的轨迹不是一条严格串行依赖链。错误步骤之后仍可能出现与错误
无关的有效工作，例如文档更新、配置修改或另一个模块的实现。时间点回退会
删除错误之后的一切，整会话回退会删除全部 speculative work；两者都会迫使
Agent 重新执行已经正确完成的工作。

因此需要证明：AgentTX 不仅能删除错误本身，还能根据实际依赖关系只删除错误
及其派生结果，同时保留时间上更晚但因果独立的工作。

### Motivation B：朴素的逐调用隔离开销过高

最直接的保护方式是每次 tool call 都启动一个新的 `try` 隔离环境。然而长
Agent workload 会反复支付 namespace、OverlayFS、shell、脚本、strace 和
snapshot 的启动/扫描成本，而且独立的 per-call sandbox 无法自然形成跨调用
的连续状态。

因此还需要证明：AgentTX 可以将隔离环境和状态跨 tool call 复用，并通过一系列
可审计优化显著降低开销，同时不破坏依赖跟踪、回滚、崩溃恢复和 host 隔离。

当前实验按照以下证据链展开：

| 阶段 | 核心问题 | 对应实验 |
|---|---|---|
| 1. 暴露问题 | 朴素隔离多慢？粗粒度回退会丢多少工作？ | runtime comparison、comparison matrix |
| 2. 解释成本 | 时间花在 tracing、namespace、snapshot 还是脚本管理？ | optimization history、trace/storage microbench |
| 3. 优化系统 | 每轮优化是否真正减少对应热路径成本？ | iteration 00–06 |
| 4. 验证核心语义 | 能否只删错误子图并保留独立工作？ | causal-retention DAG sweep |
| 5. 接入真实 Agent | LLM 能否识别错误根并主动请求因果回滚？ | real-agent recovery |
| 6. 转化为用户收益 | 保留工作具体减少多少 LLM 重放 token？ | token-recovery sweep |
| 7. 验证工程稳定性 | 长 session、worker 崩溃、并发和尾延迟是否可控？ | robustness bundle |
| 8. 放大应用 workload | 同一套注入 DAG 与三种恢复策略，换到官方 SWE/TB 后是否仍然成立？ | official tasks（oracle 与 live harness） |

第 1–7 阶段的**测量原理不变**。引入 SWE-Bench Lite 与 Terminal-Bench 只是把
应用层仓库、官方 verifier 和独立工作的体量变大，不改变故障形态、对照策略
或成功定义。`bare` / `per_call_try` / `session_try` / `shared_try` 仍只出现
在第 1 阶段的隔离成本实验中，不进入官方题的恢复对照。官方题上也不把
进程 SIGKILL 当作故障：故障仍是轨迹里的错误 producer 这一步的 effect。

---

## 2. 基础对象与执行模型术语

### 2.1 Tool call

Agent 发起的一次外部动作，例如 `write_file`、`read_file`、`run_shell` 或
`run_tests`。AgentTX 以 tool-call 边界作为事务记录和恢复的基本单位，而不是
试图理解 Agent 的自然语言思考过程。

### 2.2 Trajectory / workload

- **Trajectory**：一次 Agent session 中按时间排列的 tool-call 序列。
- **Workload**：为了实验而构造的完整任务，包括初始仓库、trajectory、故障、
  修复、预期结果和验证条件。
- **Long Agent workload**：机制与 motivation 使用的确定性轨迹，包含探索、
  多文件重构、故障注入、失败 CI、独立文档/配置修改、修复和清理，共测试
  54、64、96 次 tool call 等长度。它证明隔离成本和因果保留，不是论文的
  应用层题目来源。
- **Official application workload**：SWE-Bench Lite 的 300 道 test 实例，以及
  Terminal-Bench `original-tasks` 树中带 `task.yaml` 的官方任务（当前缓存为
  241 道）。二者只提供官方仓库/工作区、instruction 或 issue、以及官方
  verifier。恢复实验仍在其上叠加与第 8、10 节相同的注入 DAG。

### 2.3 Speculative state / host state

- **Speculative state**：尚未提交、只存在于 AgentTX overlay 中的修改。
- **Host state**：用户真实工作目录中可以被普通进程直接观察到的文件状态。
- **Host clean**：在显式 commit 之前，host 与实验开始时完全一致。
- **Host pollution / host leak**：尚未批准或提交的 Agent 修改已经进入 host。

AgentTX 的目标是让 Agent 在 speculative state 中连续工作，验证成功后再选择性
提交；失败时在 overlay 中恢复，而不是先污染 host 再尝试手工清理。

### 2.4 Effect 与 effect DAG

AgentTX 为每个步骤记录四类文件系统 effect：

- `READ`：成功读取某个路径；
- `NEGATIVE`：查询路径但得到不存在，例如 `ENOENT`；
- `WRITE`：创建或修改文件、目录或元数据；
- `DELETE`：删除路径，包括 OverlayFS whiteout。

如果后一步读取了前一步写入的结果，就建立 producer–consumer 依赖边。所有步骤
及其边构成 **effect DAG**。因果回滚使用 DAG，而不是只使用时间顺序。

### 2.5 Upperdir、snapshot 与 commit frontier

- **Upperdir**：OverlayFS 中保存 speculative 修改的可写层。
- **Per-step snapshot**：执行某一步之前保存的 upperdir 状态，用于恢复历史状态。
- **Commit frontier**：已经持久化到 host 的最大步骤边界；frontier 之后仍是
  speculative suffix。
- **Selective commit**：只把 ledger 中允许且属于目标 frontier 的路径物化到
  host，而不是复制整个 overlay。

### 2.6 Official application instance

每一道官方题在实验里被实例化成一个 AgentTX session，而不是改成另一种故障模型。
分类必须保留官方原样，和我们自己的长度预算分成两条线，互不替代。

**分类轴 A — 官方原样（论文/表里的主分类）。**

- SWE-Bench Lite：官方没有 easy/medium/hard。主分类就是 12 个 `repo`
  （django 114、sympy 77、matplotlib 23、scikit-learn 23、pytest 17、
  sphinx 16，其余 astropy/requests/pylint/xarray/seaborn/flask）。
  辅助字段用官方自带的 `version`、`FAIL_TO_PASS` / `PASS_TO_PASS` 列表，
  不另发明难度标签。
- Terminal-Bench：用 `task.yaml` 原字段。难度是 easy / medium / hard
  （当前缓存 64 / 120 / 57），类别是 software-engineering、
  system-administration、security 等。不把 easy 改写成 short。

**分类轴 B — 实验长度预算（只影响注入文档行数和 max-turns）。**

short / medium / long 是 AgentTX 为了让独立工作体积可扫而设的内部桶
（SWE 按 `len(issue)+300×|FAIL_TO_PASS|` 得到 171/111/18；TB 按 instruction
长度辅助得到 48/125/68）。图上可以单独画一条“长短”线，**不能**当作官方
难度，也不能覆盖轴 A。

**实例内容（官方字段不改）。** SWE 300 道 test split：`repo`、`base_commit`、
`problem_statement`、gold `patch`、`test_patch`、`FAIL_TO_PASS`、
`PASS_TO_PASS`；Lite gold 全部只改一个源文件。TB 当前 241 道
`original-tasks`：instruction、Docker、`tests/`、多数有 `solution.sh`，
工作区对应 `/app`。

**叠加层（与旧 token/recovery 实验同构）。** 官方 snapshot 拷进共享 overlay
后，仍用 tool-call 边界写入：错误 producer（SWE 取 gold 中的那个源文件，
TB 取测试/solution 提到的输出路径）、时间上更晚的独立 `recovery_notes/`、
由 producer 派生的 artifact、以及应失败的官方测试命令。成功定义不变：
官方 verifier 通过，且独立文档仍在。

---

## 3. 对照组与恢复策略术语

### 3.1 `bare`

直接在真实工作目录执行命令，不使用 overlay、ledger、snapshot 或回滚。

- 优点：接近执行时间下界；
- 缺点：Agent 的每次写入立即污染 host，无法实现事务式恢复。

因此 `bare` 是性能下界，不是功能等价的竞争系统。

### 3.2 `per_call_try`

每个 tool call 启动一个新的 `try` sandbox，调用结束后丢弃。

- 可以隔离单次调用；
- 每次调用都支付 namespace/overlay 创建与销毁成本；
- 前一个调用产生的 speculative 文件不会自然传给下一个调用；
- 无法直接形成 Agent 需要的长生命周期跨调用状态。

它的作用是证明“简单地给每个工具包一层 try”既慢又缺少 trajectory 语义。

### 3.3 `session_try`

把整组命令放进同一次 `try` 执行。命令之间可以共享状态，但系统只看到一个
大 session，失败后通常只能整体接受或整体放弃，没有 tool-call 级依赖图。

### 3.4 `shared_try`

多次调用复用同一个 `try -N` upperdir，使后续 tool call 能看到前一步的
speculative state。它解决了状态连续性，但没有 AgentTX ledger 和因果恢复策略，
因此恢复仍然接近 whole-session discard。

### 3.5 `shared_checkpoint`

这是一个**运行时组件基线**：使用 AgentTX 当前的共享 semisolate、持久 worker
和 per-step filesystem snapshot，但关闭自动 read tracing，也不建立完整的
AgentTX 因果 ledger。它主要回答“共享 overlay + snapshot 本身要花多少时间”。

`shared_checkpoint` 不等同于后文 token 实验中的 `temporal_checkpoint`：

- `shared_checkpoint` 是实现/性能模式；
- `temporal_checkpoint` 是发生故障后选择哪些步骤回退的恢复策略。

### 3.6 `agenttx_without_read_tracing`

AgentTX 消融组。它保留 overlay、snapshot、ledger、commit 等机制，但关闭对
opaque shell/test 命令的自动 read tracing。

它通常更快，却可能不知道某个派生文件读取了错误 producer，从而在因果回滚时
错误保留污染结果。因此它用于证明 dependency capture 的必要性，不是一个满足
完整正确性的最终系统。

### 3.7 `agenttx_full`

当前完整系统：共享 overlay、effect ledger、自动 read/negative tracing、
per-step snapshot、因果回滚、选择性 commit 和恢复机制全部启用。

### 3.8 Causal rollback / 因果回滚

给定错误根步骤，只回滚：

1. 错误根本身；
2. 通过 effect DAG 依赖它的所有 transitive descendants。

时间上更晚但没有依赖错误结果的步骤会被保留。例如轨迹为：

```text
0: 正确设计文档
1: 错误代码                 <- fault root
2: 独立 changelog
3: 读取错误代码生成 artifact <- descendant
4: 读取错误代码运行失败测试   <- descendant
```

因果回滚目标为 `{1, 3, 4}`，步骤 `{0, 2}` 被保留。

### 3.9 Temporal rollback / 时间点回滚

从错误步骤开始，撤销它以及时间上所有后续步骤，不判断后续步骤是否真的依赖
错误。上述例子中目标为 `{1, 2, 3, 4}`，所以独立的 changelog 也会丢失。

### 3.10 乐观 checkpoint（optimistic checkpoint）

“乐观 checkpoint”是 temporal rollback 的最佳情况假设：

- 假设系统恰好在错误发生前保存了一个可用 checkpoint；
- 不计算查找 checkpoint、恢复进程/内存或外部系统状态的额外成本；
- 只计算从该最佳时间点恢复仍然会丢失的有效工作。

之所以称为“乐观”，是因为真实 checkpoint 系统未必恰好在 fault 前有检查点，
实际回退点可能更早、丢失工作更多。即使在这个对 checkpoint 最有利的假设下，
它仍会删除 fault 之后的因果独立工作。

### 3.11 Whole-session / whole-branch abort

放弃当前 session 或 speculative branch 的全部工作。上述例子中目标为
`{0, 1, 2, 3, 4}`。它能保证错误不会提交，却不能保留任何已经完成的有效工作。

### 3.12 `causal_without_dependencies`

保留 causal rollback 算法，但故意移除 read-dependency capture 的消融组。它
用于回答：效果究竟来自“回滚接口叫 causal”，还是来自准确的依赖图。

结果表明，没有依赖边时系统虽然保留了很多文件，却无法删除大部分真正受污染
的 descendants。

### 3.13 Recovery-granularity emulation

token 实验中的 checkpoint 和 whole-branch 对照在 AgentTX 的真实 overlay 上
执行实际回滚、commit 和测试，但它们只是复现对应的**恢复粒度**，不是安装并
运行 Waypoint、YoloFS 或 BranchFS 的外部 artifact。

因此当前结果可以表述为：

> 在相同 workload 和相同真实文件系统状态下，不同恢复粒度会导致多少工作和
> LLM replay token 被丢弃。

不能表述为：

> AgentTX 的端到端性能已经优于这些外部系统。

---

## 4. 指标术语

### 4.1 Wall time 与 ms/step

- **Wall time**：完整一次实验从开始到结束的现实时间；
- **ms/step**：wall time 除以 tool-call 数量，用于比较不同长度 workload；
- 对真实 LLM 实验，wall time 包含网络和模型延迟；
- 对确定性 runtime 实验，不包含 LLM API 延迟。

两类 wall time 不能直接放在同一性能柱子里比较。

### 4.2 Mean、standard deviation、p50 与 p95

- **Mean**：算术平均值；
- **Standard deviation**：多次重复的离散程度；
- **p50**：中位数，50% 的样本不超过该值；
- **p95**：95% 的样本不超过该值，用于观察尾延迟。

Agent tool call 的延迟分布通常非常偏斜：普通读写很快，测试、strace 或边界操作
很慢。因此只报告 mean 会隐藏长尾，必须同时报告 p50/p95。

### 4.3 表格中的 `failures`

long workload 中存在主动设计的失败，例如缺失架构文件的读取和故意注入的失败
CI。当前 runtime comparison 中 `failures=2` 表示同一 workload 的两个预期失败
被各模式正确观察到，不表示系统崩溃两次。

系统可靠性应看 `success rate`、`correct rate`、worker-crash 检查和最终 tests，
不能把 workload 内的预期非零退出码直接解释为基础设施故障率。

### 4.4 Rollback precision 与 recall

- **Precision** = 被回滚步骤中真正无效的数量 / 所有被回滚步骤数量；
- **Recall** = 已删除的无效步骤数量 / 应删除的全部无效步骤数量。

低 precision 表示删除了太多有效工作；低 recall 表示仍有污染结果残留。

### 4.5 Useful retained、invalid removed 与 recovery utility

- **Useful retained**：独立有效步骤中最终保留下来的比例；
- **Invalid removed**：错误根及其 descendants 中最终被删除的比例；
- **Recovery utility**：两者的乘积。

只看 retained 会鼓励“什么都不删”，只看 removed 会鼓励“全部删掉”。两者必须
联合报告。

### 4.6 Prompt、completion 与 total tokens

- **Prompt tokens**：发送给模型的 system/user/tool schema 等输入；
- **Completion tokens**：模型生成的内容和 tool-call 参数；
- **Total tokens**：两者之和，也是当前 token 实验的主要计费量。

### 4.7 Avoided replay tokens

因为恢复策略丢失了原本正确的工作，Agent 在故障后不得不重新生成这些内容；
这部分真实 API token 称为 **replay tokens**。

AgentTX 的 avoided replay tokens 为：

```text
粗粒度恢复需要的 replay tokens - AgentTX causal 需要的 replay tokens
```

当前 controlled workload 中 causal 保留了两份有效文档，因此 replay token 为 0。
这只表示“无需重新生成有效文档”，不表示整个恢复过程没有 runtime、测试、
ledger inspection 或 LLM planning 成本。

---

## 5. Motivation 实验一：为什么不能直接使用朴素隔离

### 5.1 设计思路

对所有模式执行相同的 64-call 确定性 Coding Agent workload。该 workload 包含
真实的多阶段结构，而不是重复空命令：探索、模块化重构、失败 CI、独立文档和
配置更新、修复与清理。

比较以下模式：

```text
bare
per_call_try
shared_try
shared_checkpoint
agenttx_without_read_tracing
agenttx_full
```

实验同时记录性能和 host pollution，避免把“直接写 host 所以很快”和“在隔离中
安全执行”当作功能等价结果。

### 5.2 当前结果

64-call 长 workload（`motivation_runtime_comparison.csv`）：

| 模式 | ms/step | 相对角色 | commit 前 host |
|---|---:|---|:---:|
| `bare` | 45.379 | 无保护性能下界 | 已污染 |
| `per_call_try` | 1671.902 | 每次调用重建隔离 | 干净 |
| `shared_try` | 1675.566 | upperdir 连续，但无因果恢复 | 干净 |
| `shared_checkpoint` | 110.804 | 共享执行/快照组件成本 | 干净 |
| `agenttx_without_read_tracing` | 109.970 | 依赖跟踪消融 | 干净 |
| `agenttx_full` | 161.098 | 完整正确性路径 | 干净 |

同一 mixed workload 上 32/64/96/128-call 的完整对比（含 YoloFS/BranchFS/Crab/
DeltaBox 与 AgentTX 系列）见 `motivation_complete_scaling.csv`，即论文评估
Table `tab:runtime`（64-call 切片）与 Table `tab:scaling`（多步 sweep）。

### 5.3 说明了什么

1. `bare` 最快，但代价是修改立即进入 host，不能作为安全系统结论；
2. `per_call_try` 和 `shared_try` 都约为 1672–1676 ms/step，说明简单反复调用 try
   会产生很大的固定成本；
3. 当前完整 AgentTX 为 161.098 ms/step，比 `per_call_try` 低约 90%，说明复用
   worker/overlay 的优化有效；
4. 完整 AgentTX 仍约为 `bare` 的 3.5 倍，说明性能问题尚未完全解决；
5. no-trace 虽接近 `shared_checkpoint`，但它不是正确性等价系统，会漏掉派生依赖。

这组实验支撑的 motivation 是：既不能接受 bare 的污染，也不能接受朴素 per-call
隔离的成本和状态割裂，需要一个跨调用、可跟踪、可恢复的专用 runtime。

---

## 6. Motivation 实验二：每一轮优化解决了什么

### 6.1 设计规则

每次性能修改前，将完整热路径源码冻结到：

```text
src/agenttx/optimization_history/iteration_NN_<name>/
```

这样最终代码不会覆盖最初的慢版本，motivation 中的每个 before/after 都可以追溯。
每轮不仅记录速度，还验证 causal recovery 是否仍正确。

### 6.2 优化链

| 迭代 | 优化 | 解决的重复成本 | 结果 |
|---:|---|---|---|
| 00 | trusted write/delete 跳过 read strace | 已知写工具仍启动 tracing | 单次结果无明确提升，保留为诚实负结果 |
| 01 | `read_file` 显式 READ/NEGATIVE | 可信读取仍启动 strace | full 500.906 → 437.534 ms/step，约 12.7% |
| 02 | 复用 command script | 每步创建/删除临时脚本目录 | 418.899 → 409.835，约 2.2% |
| 03 | 延迟 blob GC | 每个 snapshot 扫描 blob store | 406.099 → 397.104，约 2.2% |
| 04 | 直接执行带 shebang 的脚本 | 每步额外 shell parse | 397.104 → 393.631，约 0.9% |
| 05 | persistent try worker | 每步重建 namespace/overlay | 393.631 → 151.531，约 61.5% |
| 06 | incremental upperdir snapshot | 每步遍历/复制完整 upperdir | snapshot 阶段 0.384 → 0.158 s，约 58.9% |

### 6.3 如何解释这些数字

- 从最初 495.843 到 persistent-worker 后 151.531 ms/step，历史端点下降约
  69.4%；
- 最大收益来自 persistent worker，说明主要瓶颈曾是重复 namespace/overlay
  setup，而不是 Python 层一个小函数；
- iteration 06 只证明 snapshot 子阶段减少约 58.9%，其端到端数据受 VM 噪声
  影响，不能写成“整体又加速 58.9%”；
- iteration 00 没有正收益仍然保留，这对 OSDI 写作很重要：优化历史不是只挑
  有利数字；
- 历史行来自同一 VM 的不同时间点，部分没有 interleaved 执行，因此适合解释
  成本来源和工程方向，不应伪装成最终严格统计表。

---

## 7. Motivation 实验三：长度扩展与尾延迟

### 7.1 Scaling 设计

对 54、64、96-call workload 使用相同故障和修复结构，只延长轨迹中的正常工作，
观察固定成本是否被摊薄以及总运行时间是否稳定增长。

当前 motivation scaling 结果：

| 长度 | bare ms/step | no-trace ms/step | full ms/step |
|---:|---:|---:|---:|
| 54 | 53.970 | 148.711 | 205.308 |
| 64 | 45.027 | 115.262 | 165.251 |
| 96 | 31.249 | 81.591 | 116.870 |

随着长度增加，三种模式的 ms/step 都下降，说明 workload 中存在可被更多步骤
摊薄的固定成本。full 的总 wall time 仍从约 11.1 s 增长到 11.2 s，因此不能只
看下降的 ms/step 就声称更长 workload 总体更快。

论文评估中的多步 sweep（Table `tab:scaling`）使用与 motivation 图
（32/64/96/128-call mixed workload）完全相同的长度轴，并把 YoloFS/BranchFS/
Crab/DeltaBox 与 AgentTX 系列放在同一张表里，见 `motivation_complete_scaling.csv`。

### 7.2 Tail 设计

对每个 tool call 单独计时，并分别报告 step p50/p95 和整次 run p50/p95。当前
full 模式：

| 长度 | step p50 (ms) | step p95 (ms) | run p50 (ms) | run p95 (ms) |
|---:|---:|---:|---:|---:|
| 54 | 19.496 | 632.163 | 8,932.783 | 9,402.304 |
| 64 | 19.975 | 624.587 | 8,799.933 | 8,918.493 |
| 96 | 20.748 | 596.413 | 9,545.836 | 9,698.088 |

p50 只有约 19–21 ms，而 p95 接近 596–632 ms，说明成本由少数测试、tracing 或
状态边界步骤主导。后续优化应优先针对长尾重操作，而不是只优化普通小写入。

### 7.3 Notebook 对应关系

- `motivation/plot.ipynb`：优化链与当前 baseline；
- `motivation/plot_scaling.ipynb`：长度、总时间、ms/step 和相对 bare overhead；
- `motivation/plot_tail.ipynb`：runtime tail 与真实 Agent 延迟；
- `motivation/plot_tail_scaling.ipynb`：不同长度下的 p50/p95；
- `motivation/report.ipynb`：面向论文草稿的表格与短叙述。

---

## 8. 核心语义实验：因果保留是否真的成立

### 8.1 设计思路

构造真实执行的 effect DAG，而不是只在内存中模拟图。每个节点对应一次实际
AgentTX 文件操作，错误 producer 与其 descendants、因果独立步骤交错出现。

系统性改变四个维度：

- DAG 长度：16、32、64；
- 图形：chain、fan-out、layered；
- fault position：10%、50%、75%；
- independent-work ratio：25%、50%、75%。

每个配置重复三次，并比较 `causal`、`temporal`、`whole_session` 和
`causal_without_dependencies`。前三者获得相同的 declared read effects，确保
测量的是恢复策略，而不是 strace 是否开启。

### 8.2 关键结果

- 144/144 次运行在 commit 前保持 host 干净；
- AgentTX causal 在所有配置中保留 100% independent work；
- 同时删除 100% invalid descendants；
- 64-call 时 causal rollback p95 为 272.7 ms；
- 64-call temporal 虽删除全部无效结果，但只保留 41.0% independent work；
- whole-session 保留 0%；
- no-dependency 消融保留表面上的独立工作，却只删除 4.0% invalid subgraph。

### 8.3 结论

只实现“选择一个较小回滚集合”不够，准确 dependency capture 与 causal policy
缺一不可：

- temporal/whole-session 的 recall 高，但 precision 和 useful retention 低；
- no-dependency 的 retention 高，但 invalid-removal recall 极低；
- 只有完整 AgentTX 同时实现 useful retained = 1 和 invalid removed = 1。

对应图为 `motivation/plot_causal_retention.ipynb` 和
`motivation/FIG-Causal-Retention.{pdf,png}`。

---

## 9. 真实 Agent 实验

### 9.1 Real-agent robustness

给 `deepseek-v4-flash` 一个新生成的多文件重构仓库，让模型自行选择工具。所有工具
仍经由 AgentTX，模型结束后才执行受控 commit 和 host-side tests。

三次重复结果：

- wall p50/p95：16.564/18.465 s；
- tool-call p50/p95：15.0/16.8；
- finished、success、tests pass 均为 100%；
- commit 前 host leak 为 0%。

该实验说明 AgentTX 不只支持预先写死的 trajectory，也能承载真实 LLM tool
selection。它的 wall time 包含模型和网络延迟，不能直接与 runtime-only ms/step
比较。

### 9.2 Real-agent causal recovery

在模型开始前注入四个步骤：错误 pipeline、独立 release note、依赖错误 pipeline
的 artifact、失败 tests。模型必须：

1. 调用 `inspect_ledger`；
2. 找到最早的错误 producer；
3. 调用一次 `rollback_causal(step_id)`；
4. 保留 independent note；
5. 删除 invalid artifact；
6. 通过 tests 后结束。

三次 `deepseek-v4-flash` 重复中，fault-root selection、correct target、independent
retention、invalid removal 和 tests pass 均为 100%，host leak 为 0%；wall
p50/p95 为 42.939/44.430 s。

这回答了“LLM 是否能够使用因果恢复控制面”，但尚未覆盖多 package、大型真实
repository、恶意 prompt 或长时间开放式 CI 修复。官方 SWE/TB 实验（§9.3）
把同一控制面放到真实开源仓库和官方 verifier 上，不改故障与回滚定义。

### 9.3 Official SWE-Bench / Terminal-Bench recovery

#### 设计思路

第 8 节用受控 DAG 证明因果保留，第 9.2 节用 seeded 仓库证明 LLM 能调用
回滚接口。二者的应用层仓库都偏小，因此引入两个官方 benchmark 放大
workload。**测量原理与第 8、10 节相同**：

1. 把官方 snapshot 放进共享 `try -N` overlay；
2. 注入错误 producer、独立文档、派生 artifact、失败的官方测试；
3. 比较 `causal`、`temporal_checkpoint`、`whole_branch_abort`；
4. 再以 oracle（SWE gold patch / TB `solution.sh`）或 live harness
   （DeepSeek `deepseek-v4-flash` / Codex `gpt-5.6-luna`）完成官方题。

报告时两条分类线并存：轴 A 用官方 repo / TB difficulty·category；轴 B 才是
short/medium/long 的独立工作体积。主表按轴 A 汇总，轴 B 只作长度扫描。

成功谓词仍是官方 tests 通过 **且** independent documents 保留；token 分子
仍是回滚之后的 replay / 全回路用量，故障前 token 不算节省。

明确不混入的对照：

- `bare` / `per_call_try` / `session_try` / `shared_try` 属于第 5 节隔离
  成本，不在官方题上再跑一遍；
- 进程 SIGKILL、Crab 式 chat 恢复不是本实验的故障或主对照；
- 外部 harness 在账本里记成一次 opaque task，被恢复的是注入 DAG 的
  tool-call 步骤，不是 harness 内部 turn。

#### 当前结果

协议与 runner 已接到 `experiments/scripts/bench_official_tasks.py`。结果按
harness 分目录：`experiments/results/{deepseek_harness,codex}/` 与
`experiments/results/official/`（oracle）。全量 catalog 的配对 live 数字
仍以该 runner 写出的 `official_tasks.*` 与 `official_token_summary.*` 为准，
不能把未完成 inflight 行、旧 synthetic token CSV 或已回退的 crash-restore
目录拼进同一张表。

#### 结论

官方 benchmark 只替换 workload 的仓库与 verifier。若因果策略在 Lite/TB
上不能同时满足 useful retained、invalid removed 和官方 tests，则第 8 节
的机制结论不能直接写成应用层结论；若能，则第 10 节的 replay-token 收益
可以引用官方题上的配对 token，而不是只引用 12/24/48 行玩具文档。

---

## 10. Token 实验：保留工作最终节省了什么

### 10.1 为什么需要单独设计 token 实验

文件保留率是系统指标，但用户真正付出的成本还包括模型重新读取上下文和生成
内容的 token。开放式 Agent repair loop 的检查轮次具有较强随机性，容易让一次
偶然的 planning 行为盖过“重放一份还是两份文档”的差异。

因此 token 实验将公共的确定性 validation/runtime 工作与“因为有效文件被回退
而必须发生的 LLM replay”分开，只测后者。

### 10.2 Workload 与三种恢复策略

机制归因仍使用下面这条五步轨迹。官方 SWE/TB 实验把**同一条轨迹形状**
叠在官方 snapshot 上：producer 换成官方相关文件，独立文档换成
`recovery_notes/`，测试换成官方 verifier。对照策略不变。

轨迹固定为：

| step | 内容 |
|---:|---|
| 0 | 有效 `docs/design.md` |
| 1 | 错误 pipeline，fault root |
| 2 | 有效且独立的 `docs/changelog.md` |
| 3 | 依赖错误 pipeline 的 artifact |
| 4 | 依赖错误 pipeline 的失败 tests |

比较：

- AgentTX causal：回滚 `{1, 3, 4}`，两份文档都保留；
- 乐观 checkpoint：回滚 `{1, 2, 3, 4}`，重放 changelog；
- whole branch/session abort：回滚 `{0, 1, 2, 3, 4}`，重放两份文档。

文档规模为 12、24、48 行，每个 cell 三次重复，共 27 个样本。丢失的文档必须
由真实 `deepseek-v4-flash` 通过 `write_file` tool call 重建。模型输出若结构不完整
可重试，失败尝试的 token 也必须计费，不能静默丢弃。

### 10.3 结果

| 每份文档行数 | AgentTX replay | 乐观 checkpoint | whole abort |
|---:|---:|---:|---:|
| 12 | 0 | 864.3 | 1,797.3 |
| 24 | 0 | 1,060.3 | 2,231.7 |
| 48 | 0 | 1,424.7 | 3,340.3 |

27/27 样本成功，host leak 为 0，回滚目标全部正确。AgentTX 在这个受控 workload
上避免了 100% 的**可避免 replay token**：

- 相对最乐观 temporal checkpoint，节省约 864–1,425 token；
- 相对 whole branch/session abort，节省约 1,797–3,340 token；
- 绝对节省随被保留工作的大小增长。

这里的 0 只表示没有文档需要重新生成。测试、回滚、commit、Agent 诊断以及错误
发生前已经消耗的 token 都不是 0，也没有被计入“可避免 replay”结论。

### 10.4 完整自主恢复 token 对比（Step 26）

Step 24 用于机制归因，但实验部分还需要回答更直接的用户成本问题：将模型诊断、
tool schema/result、规划、验证和重建内容全部计费后，因果恢复是否仍然节省 token？
Step 26 在同一个五步 fault DAG、同一模型、prompt、工具集合、最大轮数、commit
边界和 validator 下，让 `causal`、`temporal_checkpoint` 与
`whole_branch_abort` 分别进入完整 `LLMToolAgent` 恢复循环。

每个样本记录 prompt/completion/total API token、model/tool call、recovery ledger
step、重建文档数、成功率、host leak、policy runtime 以及 recovery mean/p50/p95。
AgentTX saving 定义为粗粒度策略的完整恢复 token 减去 causal 的完整恢复 token；
故障前 token 仍是 sunk cost。Step 24 是低噪声的因果归因，Step 26 是包含 Agent
随机规划开销的用户侧对比，二者不能互相替代。

当前 VM 没有 OpenAI/OpenRouter 凭据，因此代码、notebook、文档和结构测试已经
完成，但 12/24/48 行、三策略、三重复的数值 sweep 尚未运行。仓库不加入占位
CSV、JSON 或图片；数值结果必须来自后续 credentialed run。详见
`docs/step26-end-to-end-token-comparison.md`。

### 10.5 Official application token path

兼容入口 `bench_token_recovery.py` 与 `bench_token_end_to_end.py` 不再创建
synthetic 文档仓库，而是转到 `bench_official_tasks.py`。应用层 token 结论
只应来自官方题回滚之后的 harness usage（`official_token_summary.*`），并
与 success / independent retention 联读。§10.3 的 12/24/48 行数字保留为
机制归因 provenance，不能再标成应用 workload。

---

## 11. Robustness 与辅助 microbench

### 11.1 Worker crash injection

在下一次请求前主动杀死 persistent worker。系统必须让当前命令走原始 one-shot
try fallback，并在后续调用重启 worker。当前 fallback 和 restart 检查通过。

### 11.2 Long-running session

执行 256 次写入，在第 128 步关闭但保留 session，然后用 `AgentTX.load()` 恢复，
完成剩余步骤并 commit。结果为 256/256 文件正确提交，step p50/p95 为
36.286/72.312 ms。

### 11.3 Concurrent agents

四个 Agent 使用独立 overlay/session 并发执行，每个写入 16 个文件并独立 commit。
结果 4/4 成功，无 cross-contamination，wall time 3.105 s。

当前并发实验使用互相独立的 workspace 子目录；它尚不能证明多个 Agent 同时修改
同一文件时的 commit fencing 正确性。

### 11.4 Read-tracing microbench

20 个 no-op 调用、三次重复中：

- trace off：295.63 ms/step；
- trace on：319.43 ms/step；
- 增量为 23.80 ms/step，约 8.0%。

该结果只描述其特定 no-op microbench。真实 long workload 中 shell/test 会访问
更多路径，full 与 no-trace 的差距更大，说明 tracing 开销具有 workload
相关性。

### 11.5 Content-addressed snapshot storage

128 个 64 KiB 文件、12 个 snapshots 的逻辑数据量为约 100.7 MB，实际唯一 blob
约 9.1 MB，physical/logical ratio 为 0.090。该实验说明内容寻址和去重能降低重复
snapshot 存储，但不代表 snapshot 遍历和 WAL copy 已经完全解决。

---

## 12. 当前整体效果

| 论文问题 | 当前答案 | 主要证据 |
|---|---|---|
| 为什么不能直接 bare？ | 快，但 commit 前就污染 host | runtime comparison |
| 为什么不能 per-call try？ | 约 260.7 ms/step，状态不连续 | motivation runtime comparison |
| 优化是否有效？ | 历史端点约降 69.4%，worker 是最大收益 | optimization history |
| AgentTX 是否仍有开销？ | 有，current full 约为 bare 的 3 倍 | current 64-call comparison |
| 因果回滚是否保留有效工作？ | 受控 DAG 中 100% 保留且 100% 删除无效结果 | 144-run DAG sweep |
| 没有依赖捕获行不行？ | 不行，64-call 时只删掉 4% invalid subgraph | dependency ablation |
| 真实 LLM 会使用恢复接口吗？ | 3/3 正确选根并恢复 | real-agent recovery |
| 能节省多少 token？ | 最高测试点相对 checkpoint/whole abort 节省 1,424.7/3,340.3 | token sweep（机制归因） |
| 完整恢复循环能否节省 token？ | deepseek-v4 已跑；须与 success 联读（causal 6/6） | `token_end_to_end.*` |
| 换到官方 SWE/TB 后原理变了吗？ | 没有。仍是注入 DAG + causal/temporal/abort，只是仓库和 verifier 换成官方题 | `bench_official_tasks.py` |
| 优化路径是否稳健？ | worker crash、256-step reload、4-agent concurrency 均通过 | robustness bundle |

当前最准确的总体结论是：

> AgentTX 通过 dependency-aware causal rollback，在保持 host 隔离的同时保留
> 时间上更晚但因果独立的 Agent 工作；相比乐观时间点回退和整会话放弃，它把
> “保留多少文件”的系统收益转化为可测量的 LLM replay-token 节省。经过共享
> worker 和增量 snapshot 等优化后，完整系统明显快于朴素 per-call isolation，
> 但相对 bare 仍存在约 3 倍的当前 workload 开销。

---

## 13. 当前不能越界声称的内容

1. checkpoint/whole-branch 是恢复粒度 emulation，不是外部 artifact 端到端结果；
2. Step 24 token 结果是 avoided replay tokens；Step 26 才计入完整 post-policy Agent 恢复循环；应用层 token 必须来自官方题配对行，并与 success 联读；
3. 第 9.1/9.2 节的真实 Agent 任务仍是 seeded repository；官方 SWE/TB 才是大型真实仓库上的应用层，二者不能混写成同一次实验；
4. full tracing 依赖 Linux `strace` 或 eBPF tracepoint 后端（Step 27，`--trace-backend auto` 优先 eBPF），两者均未覆盖所有 syscall 和非文件系统 effect；
5. hard-link/bind-mount alias 仍是 causal-by-default 的正确性边界；
6. concurrency 目前只覆盖 disjoint workspaces；
7. 历史优化数据部分未 interleave，适合 motivation 和成本分解，不应包装成严格
   的最终统计显著性结论；
8. 不能把进程 SIGKILL 或 Crab chat 恢复说成本工作的主故障模型；也不能把
   `bare`/`try` 隔离成本数字写成官方题上的恢复收益。

---

## 14. 结果文件与复现入口

### Motivation 与性能

- `motivation/README.md`
- `experiments/results/motivation_runtime_comparison.{csv,json,md}`
- `experiments/results/motivation_optimization_history.{csv,json,md}`
- `experiments/results/motivation_scaling.{csv,json,md}`
- `experiments/results/motivation_tail_scaling.{csv,json,md}`
- `docs/step18-optimization-iterations.md`
- `docs/step19-robustness-evaluation.md`

### 因果恢复

- `experiments/results/causal_retention.{csv,json,md}`
- `experiments/results/causal_retention_raw.csv`
- `docs/step20-causal-retention-evaluation.md`
- `experiments/results/real_agent_recovery.{csv,json,md}`
- `docs/step21-real-agent-causal-recovery.md`

### Token

- `experiments/results/token_recovery.{csv,json,md}`
- `experiments/results/token_recovery_raw.csv`
- `docs/step24-token-replay-evaluation.md`
- `experiments/scripts/bench_token_end_to_end.py`
- `motivation/plot_token_end_to_end.ipynb`
- `docs/step26-end-to-end-token-comparison.md`

### Official application workloads

- `experiments/scripts/bench_official_tasks.py`
- `experiments/workloads/swe_bench_suite.py`
- `experiments/workloads/terminal_bench_suite.py`
- `experiments/workloads/recovery_inject.py`
- `experiments/results/{deepseek_harness,codex,official}/official_tasks.{csv,json,md}`
- `experiments/results/{deepseek_harness,codex}/official_token_summary.{csv,json,md}`
- `experiments/scripts/plot_official_tasks.py`

### 主要复现命令

```bash
cd /home/pengpeng/agenttx
export PYTHONPATH=src:.

python motivation/bench_optimization_comparison.py --length 64 --repeats 2
python motivation/bench_scaling.py --lengths 54 64 96 --repeats 2
python motivation/bench_tail_scaling.py --lengths 54 64 96 --repeats 2
python experiments/scripts/bench_causal_retention.py --repeats 3
python experiments/scripts/bench_robustness.py \
  --tail-length 64 --tail-repeats 3 \
  --long-steps 256 --long-resume-at 128 \
  --agents 4 --concurrent-steps 16

/home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_real_agent_recovery.py \
  --repeats 3 --max-turns 30

/home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_token_recovery.py \
  --document-lines 12 24 48 --repeats 3

python3 experiments/scripts/bench_token_end_to_end.py \
  --document-lines 12 24 48 --repeats 3 --max-turns 20

# Official SWE/TB: same inject-DAG + causal/temporal/abort.
# Compatibility token scripts dispatch here and must not recreate the
# synthetic document repository.
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --oracle --suite all

PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --suite all \
  --harness deepseek_harness --modes causal temporal_checkpoint whole_branch_abort

PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --suite all \
  --harness codex --modes causal temporal_checkpoint whole_branch_abort
```
