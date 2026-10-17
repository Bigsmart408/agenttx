# 中英文术语与对照组名称 / Bilingual terminology

以策略 ID 判定身份；中文正文使用中文名称，英文正文及图例使用英文名称，首次出现时附 ID。分析微基准的英文图例以 `experiments/motivation_strategies.py` 的 `POLICY_LABELS` 为准；端到端名称见下表。历史结果保留原 ID，不通过重命名合并实验。

Identify each policy by its ID. Use the Chinese name in Chinese prose and the English name in English prose and legends, with the ID on first mention. Analytical figure labels follow `POLICY_LABELS` in `experiments/motivation_strategies.py`. End-to-end names are listed separately below. Preserve historical result IDs and experiment provenance.

## 分析微基准 / Analytical microbenchmark

| 策略 ID / Policy ID | 中文名称 | English name | 语义 / Meaning |
| --- | --- | --- | --- |
| `no_recovery` | 不恢复 | No recovery | 不移除任何效果 / Remove no effects |
| `root_only` | 仅根恢复 | Root only | 只移除故障生产者，不传播到后继 / Remove only the fault producer |
| `turn_checkpoint_proxy` | 轮次检查点 | Turn checkpoint | 移除包含无效效果的完整轮次；策略代理 / Remove complete turns containing invalid effects; policy proxy |
| `temporal_step` | 时间步后缀 | Temporal step | 移除从故障根到分支结束的时间后缀 / Remove the chronological suffix from the fault root |
| `whole_branch` | 整分支回滚 | Whole branch | 移除完整未提交分支 / Remove the complete uncommitted branch |
| `ideal_oracle` | 理想 oracle | Ideal oracle | 使用真值移除精确无效闭包 / Remove the exact invalid closure using ground truth |

## 端到端实验 / End-to-end experiments

| 策略 ID / Policy ID | 中文名称 | English name | 语义 / Meaning |
| --- | --- | --- | --- |
| `no_fault` | 无故障普通提示词对照 | No fault | 无故障，普通任务提示词 / No injected fault; ordinary task prompt |
| `clean_recovery` | 无故障恢复上下文对照 | Clean recovery | 无故障，使用恢复形态的提示词/上下文 / No injected fault; recovery-shaped prompt/context |
| `crash_no_rollback` | 崩溃后不回滚 | Crash, no rollback | 注入故障，保留现场，由 agent 检查和修复 / Inject a fault; retain the workspace for agent repair |
| `turn_checkpoint` | 轮次检查点回滚 | Turn checkpoint | 使用实际 runner 的轮次边界回滚 / Roll back at the runner's actual turn boundaries |
| `temporal_checkpoint` | 时间检查点回滚 | Temporal checkpoint | 回到选定时间检查点，移除后缀 / Restore the selected chronological checkpoint and remove its suffix |
| `whole_branch_abort` | 整分支放弃 | Whole branch abort | 放弃完整未提交分支 / Abort the complete uncommitted branch |
| `causal` | AgentTX 因果恢复 | AgentTX causal | 根据 AgentTX 证据定位并恢复依赖闭包 / Diagnose using AgentTX evidence and recover the dependency closure |
| `ideal_oracle` | 理想 oracle | Ideal oracle | 使用独立真值作为理想参照 / Use independent ground truth as the ideal reference |

`agenttx_causal` 是部分 replay 实验中的实现策略 ID，其英文标签同为 AgentTX causal；保留该 ID 的来源，不擅自改为 `causal`。`shared_checkpoint` 是共享执行/快照的实现性能模式，不等于恢复策略 `temporal_checkpoint`。

`agenttx_causal` is an implementation policy ID in some replay experiments and also uses the label AgentTX causal; preserve its namespace rather than silently rewriting it to `causal`. `shared_checkpoint` is an execution/snapshot performance configuration, not the recovery policy `temporal_checkpoint`.

分析策略 `no_recovery` 不等于端到端的 `crash_no_rollback`；前者描述不做恢复时的状态，后者还包含 agent 修复。`turn_checkpoint_proxy` 不等于 `turn_checkpoint`，不能把策略代理写成 Crab 原生实现。`temporal_step` 与 `temporal_checkpoint`、`whole_branch` 与 `whole_branch_abort` 分别属于不同实验命名空间，即使边界相似也保留各自 ID。理想 oracle 使用真值，不是已实现的 AgentTX 因果恢复。

Analytical `no_recovery` is distinct from end-to-end `crash_no_rollback`, which includes agent repair. `turn_checkpoint_proxy` is distinct from `turn_checkpoint` and is not a native Crab measurement. Keep `temporal_step` separate from `temporal_checkpoint`, and `whole_branch` separate from `whole_branch_abort`, even where their boundaries coincide. Ideal oracle uses ground truth and is distinct from implemented AgentTX causal recovery.

## 通用术语 / General terms

| 中文 | English | 使用约定 / Usage |
| --- | --- | --- |
| 轮次 | turn | 一个 agent 轮次，可包含多个工具步骤 / One agent turn, potentially containing multiple tool steps |
| 工具步骤 | tool step | 单个工具操作；不要与轮次混用 / One tool operation; distinct from a turn |
| 检查点 | checkpoint | 通用机制名称；策略名使用上表全称 / Generic mechanism; use the full policy name for a comparison arm |
| 无效闭包 | invalid closure | 故障根及受其影响的后继 / Fault roots and affected descendants |
| 有效工作保留 | valid-work retention | 恢复后保留的独立有效工作 / Independent valid work retained after recovery |
| 有效工作丢弃 | valid-work discard | 粗边界额外移除的有效工作 / Valid work additionally removed by coarse boundaries |
| SWE-Bench Lite | SWE-Bench Lite | 保持项目现有显示名称；代码、路径和上游名称按原样保留 / Retain the project's display spelling and preserve literal code, paths, and upstream names |
| Terminal-Bench | Terminal-Bench | 简称 TB 首次出现时展开 / Expand TB on first mention |
| SWE-agent | SWE-agent | 系统名称；不改写 Python 类名 `SWEAgent` / System name; preserve the Python class name `SWEAgent` |
