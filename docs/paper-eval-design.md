# AgentTX 论文实验设计（第一性原理）

Last updated: 2026-08-12  
Repo: `/home/pengpeng/agenttx`  
Purpose: 统一「论文 Evaluation 应该怎么设计」与「当前仓库已实现哪些证据」，约束可声称结论的边界。

---

## 0. 一句话结论

实验的唯一正当理由是**证伪或支撑贡献主张**。AgentTX 的主张不是“更快”，而是：

> 在 opaque tool trajectory 上，能否用 effect DAG 做**非连续因果恢复**，并**保留独立工作**。

当前仓库在语义主线（因果保留 + 依赖消融）和动机/消融上已经很强；弱项在外部系统公平对比、真实多模型端到端，以及把「实现完备性实验」与「论文主实验」分清。

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

## 2. 理想实验设计（论文主文结构）

按主张倒推，主文建议压成 **4 块主实验 + 1 段诚实限制**。

### E0 — Motivation（主张 A）

**问题**：为什么「每 call 一层 try」和「时间点 / 整会话回退」都不够？

| 子实验 | 必须回答 | 正确对照组 |
|--------|----------|------------|
| E0a 隔离成本 | 长轨迹下 per-call isolation 是否不可负担 | bare（不安全下界）、per_call_try、shared_try |
| E0b 恢复粒度损失 | 故障后粗粒度会丢多少独立工作 | temporal / whole-session vs causal（同一轨迹） |

**原则**：E0 只建立问题，不证明 AgentTX 最优。

### E1 — 核心语义（主张 B）← 论文最重要一块

**问题**：因果回滚是否同时做到 *useful retained = 1* 且 *invalid removed = 1*？依赖捕获是否必要？

| 必须有 | 说明 |
|--------|------|
| 真实 overlay 上的 effect DAG（不是纯图仿真） | 已实现 |
| 扫 length / shape / fault position / independence | 已实现 |
| **消融**：`causal_without_dependencies` | 已实现，且结果非常关键 |
| 与 temporal / whole_session 同轨迹对比 | 已实现 |
| 指标：precision/recall、useful retained、invalid removed、host-clean | 已实现 |

**原则**：这是唯一可以支撑 “Agent Effect Transactions” 命名的实验。没有它，其它都是周边。

### E2 — Agent 可用性与收益（主张 B→C 的桥）

拆成两个**不可混写**的问题：

| | E2a Control-plane | E2b Utility |
|--|-------------------|-------------|
| 问 | LLM 能否读 ledger、选 fault root、调因果回滚 | 保留独立工件是否减少 **replay** token |
| 不应混入 | 总 session token、模型智商 | 诊断 / 测试 / 故障前已花 token |
| 当前状态 | `real_agent_recovery` 3× DeepSeek，指标齐全 | `token_recovery` 隔离 replay；Step 26 端到端仍待 credential |

**原则**：E2a 证明「接口可被 Agent 使用」；E2b 证明「语义转化为可量化收益」。二者缺一则要么“有机制无用途”，要么“有用途无机制”。

### E3 — 系统成本与稳健性（主张 C）

**问题**：实现上述语义要付多少税？坏掉时会不会静默污染？

应保留进主文的只有：

- 相对 per-call / bare 的 ms/step（**明确写 bare 非正确性等价**）
- 长度 scaling + p50/p95 尾延迟
- worker crash fallback、reload、并发隔离（disjoint workspace）
- hardlink 边界作为 **limitation 的实证**，不是失败掩饰

Optimization history（iter 00–06）更适合 **附录 / motivation 图**，不宜占主文半壁江山——它支撑「我们理解热路径」，不支撑核心科学主张。

### 不应进主文（或只能附录）的

- 大量 step1–14 的工程里程碑 microbench（whiteout、WAL、chmod…）→ **正确性回归**，用一句话 + artifact
- 无法跑起来的外部系统「定性点名」→ Discussion
- 未配对的历史优化数字与当前 baseline 混拼 → 文档已禁止，需严格执行

### 建议的主文叙事链

```text
E0 动机（贵 + 粗）
 → E1 因果保留 + 依赖消融     ← 核心贡献
 → E2a 真 Agent 调用控制面
 → E2b 避免 replay token        ← 把语义翻译成收益
 → E3 开销/尾延迟/稳健性 + hardlink 限制
```

附录：optimization history、snapshot storage、WAL crash、whiteout、evidence suite 全绿清单。

数量上：主文 **3–4 张表/图** 足够（runtime、causal retention、token、robustness）；其余进 artifact。

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
| 收益：避免 replay token | `token_recovery` 27/27，metric 定义干净 | **中高**（粒度仿真，非外部系统） |
| 边界诚实 | Step23 hardlink OverlayFS 拆 inode | **高**（limitation 金矿） |

### 3.2 已实现但定位要小心

| 内容 | 风险 |
|------|------|
| Optimization iterations 00–06 | 易被读成「刷分」；应标为 engineering ablation |
| Aider bakeoff | 环境/超时不公平时伤害可信度；「host 立刻污染」叙事可用，性能对比要克制 |
| `shared_checkpoint` vs `temporal_checkpoint` | 名字易混；`experiments-explained.md` 已澄清，论文必须同样严格 |
| Step26 端到端 token | 框架在，**credentialed 数字未齐**；不能当已完成主结果 |
| 外部系统（BranchFS / Waypoint / …） | 多数跑不起来；只能 qualitative |

### 3.3 明确缺口（相对完整评价）

1. **同构外部基线的 artifact-level 对比**（有则 RQ 更硬；无则必须在文中自我设限）。
2. **多模型 / 多任务** 的 E2a（现在 DeepSeek + seeded repo）。
3. **E2b 全自主回路**（Step26）的正式数字。
4. **因果回滚仍非默认 API**（hardlink 边界）——实验上已证明边界；叙事须为 *explicit causal*，不是 *default-on*。
5. 非 FS 副作用：正确列为 non-goal，不要用 `hide_network` 冒充覆盖。

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

## 5. 与论文现有 RQ 的对齐

| 论文 RQ | 对应设计块 | 当前主证据 | 主文优先级 |
|---------|------------|------------|------------|
| RQ1 Overhead & scaling | E0a + E3 | `comparison_repeats`、`motivation_scaling`、robustness tails | 高 |
| RQ2 Causal retention & ablation | E1 | `causal_retention.*` | **最高** |
| RQ3a Real-agent recovery | E2a | `real_agent_recovery.*` | 高 |
| RQ3b Avoided replay tokens | E2b | `token_recovery.*`；Step26 pending | 高 |
| RQ4 Robustness | E3 | `robustness.*`、hardlink probe | 中高 |

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

- **系统实现深度**：ledger / shared semisolate / causal rollback / selective commit / WAL / policy / agent 已成闭环，远超早期 scaffold。
- **实验相对贡献的对齐度**：E1（因果 + 消融）是最强资产；E0/E3 够用；E2 方向对但统计与任务多样性仍薄；Step26 与外部系统是最大未闭合环。
- **最大认知风险**：把「工程做完很多 step」误当成「评价做完」。审稿人只认：**主张 → RQ → 可区分对照组 → 可证伪指标**。

---

## 8. 相关文档

- 术语与不可越界声称：`docs/experiments-explained.md`
- 完成度与复现命令：`docs/STATUS.md`
- 三个系统难点：`docs/research-challenges.md`
- 论文草稿 Evaluation：`paper/main.tex`（`\\section{Evaluation}`）
