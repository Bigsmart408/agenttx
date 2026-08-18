# AgentTX 整体工作记录（动机 · 步骤 · 发现 · 最新结果）

> 本文件是项目的**总览/导航文档**：记录我们做了什么、为什么做、每一步的
> 结论与产物、以及截至 2026-08-14 的最新实验结果。细节以
> `docs/step*.md` 分步文档和 `docs/STATUS.md` 为准。

## 1. 我们做了一个什么事情

**AgentTX：面向多步智能体（agent）工作流的事务化副作用控制（effect
transactions）。**

一句话定位：`try`（semisolate）只能捕获**单个**不透明命令的文件系统副作用；
而智能体产生的是**多步、跨工具**的执行轨迹，副作用之间有因果依赖。AgentTX
把副作用捕获提升为**"效果事务"**：共享/增量 semisolate、因果效果账本
（effect ledger）、投机执行（speculative execution）、级联回滚与选择性提交
（cascade rollback / selective commit）。

目标场景：LLM 编码智能体在真实仓库上跑工具调用（读写文件、跑测试、改配置），
中间某一步出错时，能做到：

- 出错前**与故障无因果关系的独立工作全部保留**；
- 故障产生的**无效派生物（derived artifacts）全部清除**；
- 主机的真实工作区在提交之前**零污染**；
- 恢复过程**不需要 LLM 重放**有效工作（省 token）。

## 2. 动机（Motivation）

1. **单命令隔离无法表达轨迹级因果**：逐调用包 `try` 的开销巨大（每调用一次
   namespace/overlay 建立与销毁），且调用间无法共享状态、无法做跨调用因果分析。
2. **智能体错误的代价**：真实编码智能体（DeepSeek 实测）跑几十步工具调用，
   一步引入 bug 后，朴素回滚（整段丢弃 / 时间点回滚）会**连带丢掉之后完成的
   独立有效工作**，而保留故障分支又会让错误产物污染仓库。
3. **LLM 重放很贵**：被误删的有效工作必须由模型重新生成——实测重放一个文档
   就要 700~2900 tokens。恢复粒度越粗，重放成本越高。
4. **系统层面没有现成抽象**：overlayfs/`try` 提供的是"全有或全无"，
   BranchFS / Waypoint 等外部基线在环境上不可构建（见 `docs/related-work-2026.md`），
   不存在开箱即用的"效果事务"原语。

## 3. 整体步骤清单（Step 1 → 27 + 论文）

| 步骤 | 内容 | 关键产物 / 证据 |
|---|---|---|
| Step 1 | 逐调用 `try` 的开销测量（问题量化起点） | `experiments/results/try_overhead_n*.csv` |
| Step 2 | **共享 semisolate 池**（`try -N`）+ 账本：跨调用共享 overlay，upperdir 摘要 | `shared_overlay_n20.csv`、`docs/step2-shared-overlay.md` |
| Step 3 | **外科手术式回滚**：逐步 upperdir 快照（`layers.py`），只恢复出错步，保留无关工作 | `demo_surgical_rollback.py`、`docs/step3-surgical-rollback.md` |
| Step 4 | **编码智能体工作负载**：长轨迹跑在 AgentTX 上 | `long_trajectory.csv`、`docs/step4-coding-agent.md` |
| Step 5 | **路径选择性提交**：账本 frontier + 锚定 `try -I` 过滤器 | `test_runtime_integration.py`、`docs/step5-selective-commit.md` |
| Step 6 | **持久化恢复效果**：会话恢复、快照 id 单调递增、`agenttx.json` 原子替换 + fsync | `docs/step6-durable-recovery-effects.md` |
| Step 7 | **自动依赖追踪**（strace）：记录成功读 + ENOENT/ENOTDIR 负查找，含符号链接别名；默认开启、fail-closed | `test_trace.py`、`trace_overhead.{csv,md}`、`docs/step7-automatic-dependency-tracing.md` |
| Step 8 | **whiteout 安全的快照**：字符设备、`.wh.*` 作为删除效果；rollback 快照保留原生 whiteout | `test_filesystem_effects_integration.py`、`docs/step8-whiteout-safe-snapshots.md` |
| Step 9 | **显式因果回滚**：从故障前快照重建写/删路径，保留无关后续步；重叠时 fail-closed | `test_runtime_integration.py`、`docs/step9-causal-rollback.md` |
| Step 10 | **崩溃恢复 WAL**：提交前快照宿主路径 + upperdir，中断的物化可恢复 | `test_recovery.py`、`commit_wal.py`、`docs/step10-crash-recovery-wal.md` |
| Step 11 | **历史同路径 frontier 提交**：重建"此前版本"并保留后续投机状态 | `layers.py`、`docs/step11-historical-commit.md` |
| Step 12 | **内容寻址快照**：不可变 blob 去重 | `bench_snapshot_storage.py`、`snapshot_storage.{csv,md}`、`docs/step12-content-addressed-snapshots.md` |
| Step 13 | **层级因果依赖**：父/子路径边 | `test_ledger.py`、`docs/step13-hierarchical-causal-dependencies.md` |
| Step 14 | **符号链接别名依赖**：strace 保留请求路径与解析后 fd 路径 | `test_trace.py`、`docs/step14-symlink-alias-dependencies.md` |
| Step 15 | **基线对比矩阵**：bare / per-call try / session try / shared checkpoint / AgentTX（±追踪） | `comparison_matrix.{csv,json,md}`、`docs/step15-comparison-experiments.md` |
| Step 16 | **64 调用长智能体负载**：多文件重构 + 失败 CI + 独立编辑 + 派生物 | `long_workload_matrix.*`、`docs/step16-long-agent-workloads.md` |
| Step 17 | **长负载扩展与方差**（54/64/96，双重复） | `long_workload_scaling.*`、`docs/step17-evaluation-scaling.md` |
| Step 18 | **优化迭代链**：信任工具免追踪、持久命令脚本、延迟 blob GC、直执行、持久 try worker、增量 upperdir 快照 | `src/agenttx/optimization_history/`、`docs/step18-optimization-iterations.md` |
| Step 19 | **鲁棒性评估**：p50/p95、worker 崩溃注入/回退、256 步会话恢复、4 并发智能体隔离 | `robustness.*`、`docs/step19-robustness-evaluation.md` |
| Step 20 | **因果保留量化**：144 次真实 overlay 运行，48 配置 × 大小/形状/故障位置/独立性 DAG 扫描 | `causal_retention.*`、`docs/step20-causal-retention-evaluation.md` |
| Step 21 | **真实智能体因果恢复**：DeepSeek 读账本、选故障根、调因果回滚、保留独立工作 | `real_agent_recovery.*`、`docs/step21-real-agent-causal-recovery.md` |
| Step 22 | **提交策略强制**：allow/deny glob，API/CLI/会话重载统一 fail-closed | `docs/step22-runtime-commit-policy.md` |
| Step 23 | **硬链接边界探针**：OverlayFS copy-up 分裂 inode，别名读陈旧、选择性提交破坏 inode 同一性 | `hardlink_alias_probe.*`、`docs/step23-hardlink-overlay-boundary.md` |
| Step 24 | **避免的 LLM 重放 token**：12/24/48 行文档 × 三种恢复粒度，真实 DeepSeek 重放 | `token_recovery.*`、`docs/step24-token-replay-evaluation.md` |
| Step 25 | **重复对比**：比较矩阵重复运行 | `comparison_repeats.*`、`docs/step25-repeated-comparison.md` |
| Step 26 | **端到端自主恢复 token 对比**：完整诊断/工具/验证/修复循环的 prompt+completion 计费 | `token_end_to_end.*`、`docs/step26-end-to-end-token-comparison.md` |
| Step 27 | **eBPF 依赖追踪器**（strace 替代）：syscall tracepoint、pid 树过滤、release-marker 握手、`auto/strace/bpf` 后端、实机端到端捕获 | `bpf_trace.py`、`tests/test_bpf_trace.py`、`bench_bpf_trace.py`、`docs/step27-bpf-dependency-tracing.md` |
| 论文 | L0–L3 论证结构 + 可编译 OSDI 草稿 | `paper/main.tex`、`main.pdf` |
| 动机 | FAST/OSDI 风格图与可复现基准 | `motivation/`（9 个 notebook + PDF/PNG 图） |

## 4. 关键发现（Findings）

### 正确性 / 语义
1. **因果回滚是唯一能"删错留对"的模式**：基线矩阵里只有 full AgentTX 能在
   删除 `a` 与派生 `b` 的同时保留独立的 `c`；关闭读追踪会连 `b` 一起保留
   （无法发现派生关系）。
2. **依赖捕获是因果分析的前提**：禁用依赖捕获时，64 调用 DAG 中只能移除
   4.0% 的无效工作；因果回滚则移除 100%。
3. **时间点回滚 vs 因果回滚**：64 调用时，时间点回滚只保留 41.0% 的独立工作
   （之后完成的独立工作全丢），整段丢弃保留 0%。
4. **真实智能体能自主完成恢复**：DeepSeek 在 3 个全新会话中 100% 选中注入的
   故障根、100% 正确调用因果回滚、独立笔记 100% 保留、无效派生物 100% 移除、
   提交前宿主泄漏 0%。
5. **恢复粒度直接换算成 token 钱**：12/24/48 行文档时，乐观时间点恢复需重放
   1 个文档（均值 692/971/1336 tokens），整段放弃需重放 2 个
   （1436/1887/2891 tokens）；因果回滚 0 重放。
6. **提交策略不可绕过**：直接 API / CLI 子进程 / 会话重载三种路径对 deny 路径
   一律 fail-closed，被拒提交不触碰宿主、不推进 frontier。
7. **边界（诚实记录）**：硬链接在 OverlayFS copy-up 时分裂 inode，别名读陈旧、
   选择性提交破坏 POSIX 同一性——因果回滚默认化被此阻塞，需要换 substrate 或
   FUSE/内核支持；内核级原子提交超出范围（WAL 只能保证崩溃恢复，不能保证
   并发外部观察者看不到部分提交）。

### 性能
8. **逐调用 `try` 不可用**：64 调用负载上 per-call try 1671.9 ms/step vs bare
   45.4 ms/step（~37×）；共享池同样贵（1675.6 ms/step）——所以必须有"共享 +
   增量"设计。
9. **AgentTX 全模式可接受**：最新对比（本机）agenttx_full 161.1 ms/step
   （约 3.5× bare），shared checkpoint 110.8，无读追踪 110.0；
   读追踪增量约 8.0%（20 步无操作负载）。
10. **优化链有效**：持久 try worker（迭代 05）把 64 调用全模式从 393.6 降到
    151.5 ms/step；增量 upperdir 快照（迭代 06）把快照阶段累计时间从 0.384 s
    降到 0.158 s。
11. **持久化 eBPF 已取代逐步 attach**：维护的 `trace_backend=bpf` 在会话内只
    attach 一次；12 步 × 2 重复测得 61.39 ms/step（p50 26.29，p95 413.54），
    strace 为 25.60 ms/step（p50 11.67，p95 183.29），两种追踪均为 24/24
    READ 与 24/24 NEGATIVE。旧逐步 attach 后端及其高延迟实验已删除。
12. **内容寻址快照**：Step 12 负载物理/逻辑字节比 0.090（9.0%）。
13. **无特权递归 overlay 修复**：Ubuntu 5.4 的 SAUCE `clone_private_mount` 检查
    拒绝含 MNT_LOCKED 子挂载的 lowerdir（docker/snap/工作区全被锁），
    `scripts/bootstrap.sh` 现在给 `try` 打递归 overlay 补丁——102 项测试与全部
    实验在无特权下通过。

## 5. 最新实验结果（截至 2026-08-14）

### 5.1 运行时基线对比（本机 x86，64 调用确定性负载，2 重复）
`experiments/results/motivation_runtime_comparison.md`：

| 模式 | ms/step | 宿主污染 |
|---|---:|---|
| bare | 45.4 | 是（提交即污染） |
| per-call try | 1671.9 | 否 |
| shared try | 1675.6 | 否 |
| shared checkpoint | 110.8 | 否（但整段回滚） |
| agenttx 无读追踪 | 110.0 | 否 |
| **agenttx 全模式** | **161.1** | **否（且因果正确）** |

### 5.2 eBPF vs strace 追踪开销（root 实机，bpftrace 0.9.4，12 步 × 2 重复）
`experiments/results/bpf_trace_overhead.md`：

| 模式 | mean ms/step | p50 | p95 | 捕获保真 |
|---|---:|---:|---:|---|
| 不追踪 | 17.70 | 2.99 | 178.93 | — |
| strace | 25.60 (+44.6%) | 11.67 | 183.29 | 24/24 |
| 持久化 eBPF | 61.39 (+246.8%) | 26.29 | 413.54 | 24/24 |

### 5.3 端到端自主恢复 token（真实 DeepSeek，`deepseek-v4-flash`，2 重复）
`experiments/results/deepseek/token_end_to_end.md`：
完整恢复循环（诊断+工具+验证+重生成）中，因果回滚 0 重生成、成功 1.000；
粗粒度策略需重生成 1~2 个文档。12 行文档时全循环总 token：causal 28,442 vs
temporal_checkpoint 71,458 vs whole_branch_abort 106,130（即对比因果回滚，
时间点回滚多花 ~60%、整段放弃多花 ~73%）。48 行时 causal 61,506 最低。

### 5.4 真实智能体因果恢复（DeepSeek，1 重复）
`experiments/results/deepseek/real_agent_recovery.md`：成功率 / 故障根选择 /
因果目标正确 / 独立工作保留 / 派生物移除 / 测试通过 全部 1.0，宿主泄漏 0.0，
wall p50/p95 = 52.7 s。

### 5.5 因果保留（144 次真实 overlay 运行，48 配置）
64 调用 DAG：因果回滚保留 100% 独立工作、移除 100% 无效派生；时间点回滚保留
41.0%；整段丢弃 0%；无依赖捕获只移除 4.0%。因果回滚 p95 = 272.7 ms。

### 5.6 鲁棒性
无追踪 p50/p95 = 17.1/334.1 ms，全追踪 22.8/743.2 ms；注入 worker 崩溃可回退；
256 步会话在第 128 步重载；4 并发智能体零交叉污染；真实 DeepSeek 重构
p50/p95 = 12.3/14.2 s、100% 成功、提交前零泄漏。

### 5.7 外部基线（tiao2 ARM64 机，2026-08-11）
Bubblewrap 0.4.0 已装并测（0.848 ms/step，但只有整会话命名空间放弃、无因果）；
BranchFS 构建被 Cargo 1.75 / fuser API 不匹配阻塞；Waypoint 缺 CRIU 可执行文件；
DeltaBox/YoloFS/Sandlock/Crab/Cordon 均为工件或环境阻塞——**全部是显式记录，
不是零值基线**。

## 6. 当前状态与剩余工作

已完成（见 `docs/STATUS.md` "Completed" 全表）：v0 运行时 27 步全部落地、
证据链完整、真实 LLM 实验跑通、论文草稿可编译、动机图与可复现基准齐备。

剩余高优先级：
1. **因果回滚设为默认 API**——被 Step 23 硬链接语义问题阻塞（换 substrate/FUSE）；
2. 可扩展快照（目录遍历与历史/WAL 副本仍随投机状态增长）；
3. 内核级原子提交（当前 WAL 只保证崩溃恢复）；
4. overlay 开销进一步降低（共享池仍显著慢于裸执行）；
5. 追踪可移植性/完整性（非 AT_FDCWD dirfd、eBPF 持久化 attach 摊销）；
6. 非文件系统副作用（网络/云）仍只有粗粒度 hide_network；
7. 评估：多模型重复、更强 bakeoff、统计方差；
8. 产品：OSDI 论文打磨；Problem B（对抗性中介）显式推迟；
9. `main` 分支领先 `origin/main`，批准后才 push。

## 7. 复现入口

```bash
source ~/.agenttx_llm.env
export PATH="$HOME/miniconda3/envs/agenttx/bin:$PATH"
export PYTHONPATH=/home/pengpeng/agenttx/src:/home/pengpeng/agenttx
cd /home/pengpeng/agenttx
python -m pytest -q
python experiments/scripts/bench_evidence_suite.py
python experiments/scripts/bench_bpf_trace.py --steps 20 --repeats 3 --workload read
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py --document-lines 12 24 48 --repeats 3 --max-turns 20
```

完整命令清单见 `docs/STATUS.md` 的 "How to re-run evidence"。
