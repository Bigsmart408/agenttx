# AgentTX 论文实验设计（第一性原理）

Last updated: 2026-08-24  
Repo: `/home/pengpeng/agenttx`  
Purpose: 统一「论文 Evaluation 应该怎么设计」与「当前仓库已实现哪些证据」，约束可声称结论的边界。

---

## 0. 一句话结论

实验的唯一正当理由是**证伪或支撑贡献主张**。AgentTX 的主张不是“更快”，而是：

> 在 opaque tool trajectory 上，能否用 effect DAG 做**非连续因果恢复**，并**保留独立工作**。

当前仓库在语义主线（因果保留 + 依赖消融）和动机/消融上已经很强；弱项在外部系统公平对比、真实多模型端到端，以及把「实现完备性实验」与「论文主实验」分清。

### 当前 workload 口径（2026-09-23）

实验统一分为五层：Microbenchmark、Real-world Workloads、Breakdown、Appendix
Correctness / Interrupted Recovery 和 Other Overheads。Microbenchmark 同时包含
同一批 Motivation case 的分析 sweep 与完整系统执行。SWE-Bench Lite 与
Terminal-Bench 是真实负载。旧的 toy refactor、GitHub
sidecar、oracle-repair official-task 和不完整全量结果仅保留作 provenance，不能替代
上述规范实验。完整定义以仓库根目录 `agent.md` 为准。

---

## 1. 第一性原理：实验必须服务什么主张

从 `docs/problem.md`、`docs/research-challenges.md`、`paper/main.tex` 压缩，系统贡献有三层：

| 层级 | 主张 | 若实验证伪会怎样 |
|------|------|------------------|
| **A. 必要性** | 单次 `try` / 时间点回退 / 整会话丢弃，无法同时满足「清掉错误子图 + 保留独立工作 + 长轨迹可负担」 | 贡献退化为工程包装 |
| **B. 机制正确性** | tool-boundary 捕获的 effect DAG + selective reconstruction，能做到非连续因果恢复 | 论文核心垮掉 |
| **C. 系统可行性** | 共享 semisolate、snapshot、WAL、policy 使上述语义可在真实 FS/Agent 上跑，且成本可解释 | 只剩算法 toy |

因此评价标准不是「实验多不多」，而是：

1. **每个主实验只打一个主张**（一个 RQ 一条因果链）。
2. **对照组必须与主张同构**：比较的是恢复粒度 / 依赖完整性，不是随便找一个慢系统。
3. **指标必须能区分「看起来对」和「真的对」**（例如 `causal_without_dependencies` 保留了文件却清不掉污染）。
4. **外部系统缺席时，只能声称粒度对比，不能声称端到端碾压**。

论文中的 RQ1–RQ4 基本符合这个骨架；需要严格执行的是**证据强度分层**和**哪些实验进主文**。

---

## 2. 规范实验结构

主文实验按证据强度组织，不再沿用旧的 E0--E3 或 RQ1--RQ4 编排：

### 2.1 Microbenchmark

Microbenchmark 对同一批 400 个 Motivation manifest 做两种测量。分析型 sweep 改变依赖
密度、turn 数量和故障位置，解释粗粒度恢复为何产生额外丢弃；完整系统执行在完全相同的
`case_id`、依赖图、故障位置和 survivor set 上运行真实 agent、工具、依赖捕获、回滚、
context 重建和验证。正式结果覆盖全部 400 个 case，每个 case 和策略重复三次；调试子集
不进入论文。

### 2.2 Real-world Workloads

真实负载只使用完整的 SWE-Bench Lite 和 Terminal-Bench，并分别报告后再汇总。主结果必须
使用 live agent 和官方 verifier；gold patch 或 oracle solution 只能用于环境检查或显式标注
的上界，不能算作任务成功。正式 `causal` 配置统一使用 compact recovery context。

### 2.3 Breakdown

Breakdown 在同一套 400-case Microbenchmark 上逐项移除 dependency capture、object
identity、selective history reconstruction 和 context rewind。Persistent worker 与 compact
handoff 作为性能消融单独报告，避免把语义不正确的配置解释为性能优化。

### 2.4 Appendix Correctness and Interrupted Recovery

正确性矩阵覆盖 effect 类型、版本、目录关系、rename、alias、多故障根、重复恢复、reload
以及 workspace--context 一致性。恢复过程的五个阶段分别注入中断；重启后只允许观察到完整
旧状态或完整新状态，不能出现 workspace 与 agent context 混合状态。

### 2.5 Other Overheads

单独报告 tracing、历史存储、RSS、ledger/context index、closure planning、reconstruction、
context handoff、startup 和 reload 成本，并给出重复测量的 mean、p50、p95 和 p99。

旧的 toy refactor 只作 smoke；GitHub sidecar、oracle-repair official-task 和未配齐的历史
结果只作 provenance。无法运行的外部系统只能进入定性讨论，不能替代同构基线。

---

## 3. 当前实现了哪些（按主张映射）

基于本仓库 `docs/STATUS.md`、`experiments/results/*`、`paper/main.tex` Evaluation。

### 3.1 已扎实实现（可直接服务论文）

| 主张 | 实现 / 产物 | 强度 |
|------|-------------|------|
| A：per-call 太贵 | `try_overhead_*`、`comparison_repeats`（50 samples）、motivation scaling | **高**（有分布） |
| A：粗粒度丢工作 | `causal_retention` 里 temporal/whole 的 useful retained | **高** |
| B：因果保留 | `causal_retention`：144 runs / 48 configs，causal 全 1.0 | **很高** |
| B：依赖捕获必要 | `causal_without_dependencies` recall 崩到约 4–14% | **很高**（好消融） |
| B：host 在 commit 前干净 | evidence suite、comparison recovery rows | **高** |
| C：共享 overlay + 优化后成本 | full vs per_call；64-call 量级开销 | **中高**（VM 相关，需写清） |
| C：稳健性 | worker crash、256-step reload、4 并发 | **中**（仅 disjoint workspace） |
| B→C：真 Agent 调回滚 | `real_agent_recovery` 3/3 | **中**（单模型单任务） |
| 收益：避免 replay token | `token_recovery` 27/27；`token_end_to_end` 6/6 causal success | **高**（须联读 success；非外部系统） |
| 边界诚实 | Step23 hardlink OverlayFS 拆 inode | **高**（limitation 金矿） |

### 3.2 已实现但定位要小心

| 内容 | 风险 |
|------|------|
| Optimization iterations 00–06 | 易被读成「刷分」；应标为 engineering ablation |
| Aider bakeoff | 环境/超时不公平时伤害可信度；「host 立刻污染」叙事可用，性能对比要克制 |
| `shared_checkpoint` vs `temporal_checkpoint` | 名字易混；`experiments-explained.md` 已澄清，论文必须同样严格 |
| Step26 端到端 token | **已有 deepseek-v4 数字**；须与 success 联读，不能单看 token 少 |
| 外部系统（BranchFS / Waypoint / …） | 多数跑不起来；只能 qualitative |

### 3.3 明确缺口（相对完整评价）

已闭合（2026-08-18）：
- E2b 全自主回路数字：`token_end_to_end.*` + `FIG-Token-End-to-End`（须与 success 联读）。
- RQ3 coverage / identity 表：`coverage_matrix.*`。
- WAL phase fault matrix：`wal_fault_matrix.*`（每相位 5 次）。

仍开放：
1. **同构外部基线的 artifact-level 对比**（有则 RQ 更硬；无则必须在文中自我设限）。
2. **多模型 / 多任务** 的 E2a（现在 DeepSeek + seeded repo）；GitHub-context 仍为每格 1 次。
3. **因果回滚仍非默认 API**（hardlink / bind / external 边界）——叙事须为 *explicit causal*。
4. 非 FS 副作用：正确列为 non-goal，不要用 `hide_network` 冒充覆盖。
5. Aider 等外部 agent 的公平重复对比。

---

## 4. 数字能撑住什么、撑不住什么

| 结果 | **可以**说 | **不能**说 |
|------|------------|------------|
| causal retention 100/100 | 在受控 effect-DAG 上，依赖感知因果恢复同时保留独立工作并清除污染 | 任意真实 GitHub agent 都正确 |
| no-dep ablation 失败 | 依赖捕获是语义必要组件，不是可选加速开关 | tracing 实现已完备 / 可移植 |
| token_recovery 因果侧 0 replay | **在相同轨迹与恢复粒度假设下**，保留工件避免再生 token | 已节省真实生产总账单；已优于 Waypoint 等 |
| comparison_repeats | 在本 VM 上 AgentTX full 是唯一 causal-correct=1 的模式 | AgentTX 比 bubblewrap「更好的系统」（bubblewrap 只是隔离下界） |
| real_agent_recovery 3/3 | 控制平面可被该模型在该任务上正确调用 | Agent 已学会通用排错 |
| hardlink probe | 当前 OverlayFS 底物上默认因果不安全 | 问题已在 ledger 层解决 |

红线与 `docs/experiments-explained.md` §13「不能越界声称」一致；论文 Evaluation 应以该节为边界。

---

## 5. 与新实验层级的对齐

| 实验层级 | 主要主张 | 当前证据状态 |
|---------|----------|--------------|
| Microbenchmark | 粗粒度浪费的机制；完整系统能否实现选择性恢复收益 | 400-case 分析型 sweep 已有；同 case 的完整系统数据待生成 |
| Real-world Workloads | 收益能否在 SWE-Bench Lite 与 Terminal-Bench 上成立 | 历史 pilot 可审计；冻结版本的完整 live-agent 数据待生成 |
| Breakdown | 每个语义机制和性能优化分别贡献什么 | 已有零散消融；统一 400-case 配对结果待生成 |
| Appendix Correctness / Interrupted Recovery | 支持边界内是否正确；恢复被中断时是否保持 workspace--context 一致 | 语义回归已有；五阶段中断矩阵待生成 |
| Other Overheads | tracing、历史、内存、规划、重建和 context handoff 的代价 | 零散数据已有；统一冻结版本结果待生成 |

---

## 6. 产物 ↔ 图表对照（写作时用）

| 建议图/表 | 主张 | 主要文件 |
|-----------|------|----------|
| Table: runtime modes | A/C | `experiments/results/comparison_repeats.md` / matrix |
| Fig: causal retention | B | `FIG-Causal-Retention.*` + `causal_retention.md` |
| Fig/Table: token replay | B→收益 | `FIG-Token-Recovery.*` + `token_recovery.md` |
| Fig: robustness | C | `FIG-Robustness.*` + `robustness.md` |
| Motivation scaling（可主文或附录） | A/C | `FIG-Motivation-Scaling.*` |
| Optimization history | 附录 | `motivation_optimization_history.*` |
| Hardlink boundary | Limitations | `hardlink_alias_probe.*`、`step23-*.md` |
| Evidence suite checklist | Artifact | `evidence_suite.md` |

---

## 7. 总判

- **系统实现深度**：ledger、shared semisolate、causal rollback、context rewind、WAL、policy 和 agent 已形成闭环。
- **当前最强证据**：分析型 Microbenchmark 与已有语义消融；它们解释机制，但不能替代完整系统和真实负载结果。
- **当前主要缺口**：同一 400-case Motivation 集合的完整系统 Microbenchmark、冻结版本的 541-task live-agent 结果、统一 Breakdown、五阶段中断矩阵和资源开销。
- **最大认知风险**：把历史 pilot 或工程里程碑误当作规范评价。所有主结论都必须满足“主张 → 同构对照 → 可证伪指标 → 冻结数据”。

---

## 8. 相关文档

- 术语与不可越界声称：`docs/experiments-explained.md`
- 完成度与复现命令：`docs/STATUS.md`
- 三个系统难点：`docs/research-challenges.md`
- 论文草稿 Evaluation：`paper/main.tex`（`\\section{Evaluation}`）
