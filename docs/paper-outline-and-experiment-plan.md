# AgentTX 论文大纲与实验补全计划

本文档按 OSDI 12 页系统论文组织当前材料。它只使用仓库中已有实现和结果，未完成的实验均明确标为缺口。

## 1. 论文主线

AgentTX 的核心论点是：多步 coding agent 的执行状态应以整条 trajectory 作为隔离单元，以版本化文件系统 effect 的因果子图作为恢复单元。命令、session 和时间 checkpoint 可以保存状态，却无法只撤销错误 producer 及其后继，同时保留时间上更晚但因果独立的工作。

论文需要始终区分三层内容：

- Agent Effect Transaction (AET) 是恢复模型。
- AgentTX 是 AET 的 Linux 实现。
- persistent worker、trusted declarations 和 incremental snapshot 是性能优化，不是新的正确性挑战。

当前可支持的强结论是：在受支持的文件拓扑和 syscall 覆盖范围内，AgentTX 能保留独立工作、删除错误的传递后继，并在提交前保持 host 不变。论文不能声称完整支持 bind mount、外部 alias、任意多对象原子可见性或非文件系统副作用。

## 2. 建议的大纲与页数

| 章节 | 页数 | 叙事任务 |
|---|---:|---|
| Abstract | 0.25 | 问题、现有恢复单元的缺陷、AET、AgentTX、核心结果 |
| 1. Introduction | 1.25 | 多步 agent 背景，恢复单元不匹配，三个挑战，方法和贡献 |
| 2. Background and Motivation | 1.50 | 最少背景，执行路径观察，恢复路径观察，导出设计要求 |
| 3. Agent Effect Transactions | 1.50 | AET 状态、effect DAG、因果闭包、重建不变量、frontier |
| 4. AgentTX Design and Implementation | 2.00 | effect capture、identity、shared speculation、reconstruction、WAL |
| 5. Evaluation | 4.00 | 语义、性能、机制、真实 agent、token、健壮性与边界 |
| 6. Discussion and Limitations | 0.50 | 支持边界和未解决的系统问题 |
| 7. Related Work | 0.75 | 按 recovery unit 分类比较 |
| 8. Conclusion | 0.25 | 问题、方法、结果 |
| **合计** | **12.00** | 参考文献不计页数 |

### 2.1 Introduction

逻辑顺序：多步 coding agent 已成为有状态系统任务；现有机制按命令、session、branch 或时间组织恢复；一个交错轨迹说明时间顺序不等于因果依赖；由此得到 dependency discovery、object identity 和 selective reconstruction；AET 改变恢复单元；AgentTX 给出受支持范围内的实现；最后列出可验证的贡献和结果。

引言只保留一组 headline 结果。建议使用因果保留率和 avoided replay tokens，把性能数字留到 Motivation 和 Evaluation，直到 x86 性能数据完成统一重跑。

### 2.2 Background and Motivation

本章以原 Motivation 为主线，Background 只定义 trajectory、READ/NEGATIVE/WRITE/DELETE、speculative view 和 recovery granularity。

叙事顺序：

1. 最小背景：工具调用是 opaque process tree，路径不等于对象。
2. 执行观察：逐调用隔离重复建立 namespace 和 overlay，共享状态降低固定成本，但 tracing 仍形成明显 tail。
3. 恢复观察：temporal rollback 删除正确后继，no-dependency 消融留下错误派生物。
4. 设计结论：trajectory 是 speculation unit，causal subgraph 是 recovery unit。

保留一个性能动机图和一个因果保留图即可。优化历史、长度 scaling 和 tail 可以合并成一个多面板图，避免 Background and Motivation 占用过多篇幅。

### 2.3 Agent Effect Transactions

本章只写抽象，不出现 `strace`、OverlayFS、upperdir、persistent worker 或具体 WAL 文件。

逻辑顺序：定义 $\langle V,L,H,F\rangle$；说明 append、recover 和 finalize；定义 effect overlap 和 dependency edge；计算错误节点的传递闭包；给出 selective reconstruction 不变量；定义允许 rolled-back holes 的 monotonic frontier。

当前 LaTeX 盒图应替换为正式架构图。图中需要显示 tool calls、speculative view、effect ledger、causal closure、historical state 和 approved frontier。

### 2.4 AgentTX Design and Implementation

按正确性数据流组织，而不是按代码文件组织：

1. Effect capture and coverage gate：写入来自 upperdir diff，读取和负查找来自 persistent `strace`，无法解析的 fd-relative path fail closed。
2. Object identity：层次路径、symlink 双名、rename delete/create、已验证 hard-link group 和不支持的 bind/external alias。
3. Shared speculation and reconstruction：共享 `try -N`、per-step historical state、retained-effect overlap 检查和 suffix rebase。
4. Durable publication：policy 在 WAL 之前执行，prepare/install/finalize 支持 crash recovery，但不保证外部观察者看到原子多路径切换。
5. Performance engineering：persistent worker、content-addressed blobs 和 incremental snapshots 单独作为优化段落。

删除正文中的 P0/P1、Step 27/28 和“本轮新增”等开发阶段术语。正文只描述最终机制和边界。

## 3. Evaluation 的最终结构

评估应先验证中心语义，再讨论成本。建议将当前 RQ 顺序调整为以下五组。

### RQ1: Causal recovery 是否同时保留有效工作并删除无效后继

已有证据：144 次 real-overlay run，覆盖 16/32/64 calls、三种 DAG shape、三种 fault position 和三种 independent-work ratio。完整 causal recovery 的 useful retention 和 invalid removal 均为 100%；64 calls 下 temporal retention 为 41%，无 dependency capture 时 invalid removal 为 4%。

主图：`FIG-Causal-Retention.pdf`。

还需补充：在图注和正文中明确 144 是 run 数，48 是聚合 configuration 数，并说明三次重复如何分配。

### RQ2: 连续隔离和 dependency capture 的成本是多少

已有证据：64-call workload、54/64/96-call scaling、trace microbenchmark、optimization history 和 comparison repeat 脚本。

已完成：在 x86 主机 `pengpeng-ubuntu-01-1`（Linux 5.4.0-216-generic，AMD EPYC 7713，Python 3.8.10）上以固定十次写入轨迹、每模式 50 个 fresh workspace 完成重跑。完整模式显式使用 `strace`，结果为 58.757 ms/step mean、57.738/63.511/68.169 ms/step p50/p95/p99，并在 50/50 次满足 causal-retention predicate。原始结果位于 `experiments/results/comparison_repeats.{csv,json,md}`，运行记录位于 `docs/x86-comparison-20260818.md`；历史 tiao2 数字仍只保留在 history，不进入正文。

主图：重新生成的 x86 p50/p95/p99 或 CDF。不要把 10-write microbenchmark 与 64-call coding workload 混成同一性能结论。

### RQ3: 哪些机制是正确性所必需的

已有证据：no-read-tracing 消融、parent/child、negative lookup、symlink、hard-link probe、persistent `strace` 与 eBPF 对比、snapshot storage 和 commit policy 测试。

需要补充一个 capability/ablation 表：

| 机制 | 删除后预期失败 | 当前证据 | 最终需要的结果 |
|---|---|---|---|
| READ dependency | 派生 artifact 被保留 | no-trace ablation | invalid removal rate |
| NEGATIVE dependency | absence assumption 丢失 | ledger integration test | targeted pass rate |
| Object identity | sibling alias 漏边或错误提交 | hard-link probe | supported/fail-closed matrix |
| Historical reconstruction | partial rollback 恢复错误版本 | same-path/frontier tests | state equality |
| WAL | crash 后 host/frontier 不一致 | crash tests | phase-by-phase recovery rate |

还需运行 syscall/identity coverage matrix，至少包含普通 path、`openat`、fd-relative read、symlink、rename、pre-existing hard link、upper-created hard link、bind mount 和 external alias。支持项报告正确率，不支持项报告 fail-closed 率。

### RQ4: 真实 agent 能否使用恢复接口，保留工作能否降低用户成本

已有证据：

- `deepseek-v4-flash` 的 causal recovery 3/3 成功，wall p50/p95 为 42.939/44.430 s。
- controlled replay-token sweep 有 27 个样本。48 entries 时 causal policy 相对 temporal 和 whole abort 分别避免 1,424.7 和 3,340.3 replay tokens。
- full autonomous recovery 已有 12/24/48 entries、三种 policy、每格两次的结果。
- GitHub-context Requests、Flask 和 pytest 各有三种 policy 的一次运行。
- AgentTX-LLM 与 Aider 各有一次同模型 refactor 结果。

当前不足：GitHub-context 每格只有一次，Aider 只有一次，不能作为统计结论。Full autonomous recovery 中 temporal 和 whole-abort 多数样本未满足 success predicate，token 更少时也不能解释为更优。最终图应同时显示 total tokens 和 success/independent-retention，或只比较所有策略都成功的配对样本。

最低补全方案：

1. 三个 GitHub-context 任务的每个 policy 至少运行 3 次，推荐 5 次。
2. 固定模型为 `deepseek-v4-flash`，固定 prompt、tool schema、max turns 和 validator。
3. 报告 success、causal-target accuracy、independent retention、host leak、total tokens 和 wall p50/p95。
4. Aider 对比至少 3 次，并使用相同任务、模型、timeout 和测试判定。
5. 将 controlled avoided replay 与 full-loop total token 分成两个结论，不能相互替代。

### RQ5: 系统在 crash、长 session 和并发下是否保持不变量

已有证据：persistent-worker crash fallback、256-step reload、4 个 disjoint agent、snapshot dedup、policy reload、hard-link publication 和 WAL 恢复测试。

最终还需：

- 对 prepare、install 和 finalize 的每个故障点做重复注入，报告恢复成功率和 host/frontier 一致性。
- 将 WAL crash test 从少量定点测试扩展为可统计的 fault matrix。
- 增加 shared-tree 或 same-path 并发；若不实现，则把结论严格限定为 disjoint workspace concurrency。
- 对 repository size、changed-path ratio 和 session length 做 snapshot 时间与空间 scaling。

## 4. 当前证据清单

| 证据 | 状态 | 可用于正文的结论 | 主要文件 |
|---|---|---|---|
| Causal retention sweep | 可用 | supported topology 下 100% retain 和 100% remove | `experiments/results/causal_retention.*` |
| 64-call runtime and scaling | 可用但需统一 generation | 长任务的开销和 tail | `motivation_runtime_comparison.*`, `long_workload_scaling.*` |
| 50-run comparison | 已完成，可用于正文 | 统一 x86 artifact、50 fresh workspaces、p50/p95/p99、causal predicate | `comparison_repeats.*`, `docs/x86-comparison-20260818.md` |
| Real-agent recovery | 可用，样本小 | 模型能使用 control plane | `experiments/results/deepseek/real_agent_recovery.*` |
| Controlled replay tokens | 可用 | avoided replay tokens | `experiments/results/deepseek/token_recovery.*` |
| Full-loop tokens | 可用（2 repeats） | 与 success 联合解释；已入正文 | `experiments/results/deepseek/token_end_to_end.*` |
| GitHub-context tasks | preliminary | 三种规模上 causal 均成功 | `experiments/results/deepseek/github_token_tasks.*` |
| Aider comparison | preliminary | 单次功能对照，不能作统计优势 | `experiments/results/deepseek/refactor_agent_compare.*` |
| Robustness | 可用但需 crash matrix | worker、reload、disjoint agents | `experiments/results/robustness.*` |
| Identity and tracing | 可用 | coverage + hard-link fail-closed | `coverage_matrix.*`, `hardlink_alias_probe.*` |
| WAL fault matrix | 可用 | 六相位 5/5 恢复 | `wal_fault_matrix.*` |

## 5. 投稿前的实验优先级

### P0: 必须完成

1. ~~统一 x86 canonical dataset，清除论文中混入的 tiao2 50-run 数字。~~ 已完成；正文使用新 x86 表，历史数字仅留在 history。
2. 在 x86 上随机交错运行 50 次 runtime comparison，生成 mean、p50、p95、p99 和置信区间。
3. 将 GitHub-context 扩展到至少 3 次重复；full autonomous recovery 已有 2 次/格并写入正文（须联读 success）。
4. ~~建立 WAL phase fault-injection matrix。~~ 已完成（5/phase，`wal_fault_matrix.*`）。
5. ~~建立 syscall/object-identity coverage matrix。~~ 已完成（3/case，`coverage_matrix.*`）。
6. 在 Setup 中补齐 CPU、memory、storage、kernel、Python、`try` commit、AgentTX commit 和频率控制信息。

### P1: 强烈建议

1. 公平重复 Aider 对比；对无法运行的 BranchFS、Waypoint、YoloFS 等给出 artifact/blocker 表。
2. 增加 shared-path concurrency 或明确删除相关泛化表述。
3. 对大 repository 和高 churn upperdir 做 snapshot scaling。
4. 增加第二个模型只用于 agent-level generalization；系统正确性结论仍由独立 validator 判断。

### P2: 可放入 artifact evaluation

1. 一键生成全部 CSV、notebook、PDF figure 和论文表格。
2. 为每个 result 写 manifest 和 SHA256，避免历史运行覆盖 canonical 文件。
3. 提供 unsupported syscall、bind mount 和 external alias 的 fail-closed regression suite。

## 6. 最终图表安排

| 位置 | 图表 | 当前状态 | 操作 |
|---|---|---|---|
| Section 2 | Motivation performance figure | 三张图信息重叠 | 合并 optimization、scaling 和 tail |
| Section 2 | Recovery granularity table | 可用 | 保留 |
| Section 3 | AET lifecycle | 当前是 LaTeX box sketch | 重画为正式双栏架构图 |
| RQ1 | Causal retention | 可用 | 作为 evaluation 第一张主图 |
| RQ2 | x86 runtime distribution | 当前混入非 x86 50-run 数据 | 重跑后生成 CDF 或 p50/p95/p99 |
| RQ3 | Mechanism and coverage table | 已写入 `tab:coverage` / `tab:wal` | 保持 |
| RQ4 | Controlled replay tokens | 可用 | 保留并明确 avoided replay |
| RQ4 | Full-loop token-success | 已有 `FIG-Token-End-to-End` | GitHub 仍需重复 |
| RQ5 | Robustness | 可用但较杂 | 用 crash matrix、session 和 concurrency 三个 panel |

## 7. 数据管理规则

最终论文只读取一个 x86 canonical result directory。每次运行必须记录 host、kernel、CPU、memory、git commit、model、trace backend、repeat、seed 和完整命令。历史优化结果保留在 history 目录，不得覆盖当前 baseline。Notebook 只读取明确的 canonical CSV，图注必须给出样本数和 workload。若外部 artifact 无法运行，正文和表格只报告可复现的 blocker，不填入推测数字。

## 8. 推荐执行顺序

第一阶段先统一 x86 数据和实验 manifest，并重跑 50 次 runtime comparison。第二阶段补 GitHub/full-loop repeats、WAL fault matrix 和 coverage matrix。第三阶段按新的 RQ 顺序重排 Evaluation，更新所有图注和 headline 数字，最后再做一次 LaTeX 全文一致性检查。
