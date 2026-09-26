# AgentTX 当前实验设计与运行计划（2026-09-23）

本文档是 `agent.md` 的中文运行配套说明，用于统一研究问题、对照组、运行顺序、停止
条件和结果口径；发生冲突时以 `agent.md` 为准。模式名、脚本名、命令行参数及结果字段
保留代码中的英文标识，其余说明使用中文。

## 1. 实验目标

AgentTX 要验证的核心问题不是单纯的运行速度，而是：发生错误后，能否根据实际依赖
关系只撤销错误及其派生结果，同时保留因果无关的有效工作，并减少智能体重新执行这些
工作的令牌成本。

当前实验回答五个层层递进的问题：

1. Microbenchmark 中，依赖密度、轨迹长度和故障位置如何改变粗粒度回滚浪费？
2. 完整系统 microbenchmark 中，AgentTX 能否把语义收益转化为成功率、token 和时间收益？
3. 在 SWE-Bench Lite 与 Terminal-Bench 真实负载上，该收益是否仍然成立？
4. Dependency capture、object history、selective reconstruction 和 context rewind 各自贡献什么？
5. 正确性边界、恢复过程被中断时的行为，以及额外时间/空间开销是什么？

论文和执行顺序统一为：

```text
Microbenchmark（同一批 Motivation case 的分析与完整系统执行）
  -> Real-world Workloads（SWE-Bench Lite + Terminal-Bench）
  -> Breakdown
  -> Appendix Correctness / Interrupted Recovery
  -> Other Overheads
```

## 2. 真实负载范围

全量应用任务共 541 个：

| 套件 | 任务数 | 验证方式 |
|---|---:|---|
| SWE-Bench Lite | 300 | 官方 Docker 评测镜像 |
| Terminal-Bench | 241 | 官方任务验证器；特殊任务使用其 Dockerfile 和 `run-tests.sh` |

所有模式必须使用相同的任务版本、官方验证器、模型、推理强度、超时和并发配置。
聚合结果时，以
`suite/task/mode/recovery_context_variant/repeat/model/harness` 作为唯一键。

SWE-Bench Lite 与 Terminal-Bench 统一归入 Real-world Workloads。受控任务统一归入
Microbenchmark，并且必须复用 Motivation 的 case，不得另建一套 workload。

## 3. 对照模式

正式主实验使用以下恢复策略；`causal` 全程采用紧凑恢复上下文：

| 模式 | 含义 | 主要用途 |
|---|---|---|
| `no_fault` / `clean_recovery` | 不注入恢复故障，直接完成官方任务 | 无故障参考组 |
| `crash_no_rollback` | 注入相同故障但不回滚 | 负正确性对照 |
| `causal-compact` | 因果回滚，使用紧凑恢复证书 | AgentTX 核心方法的紧凑上下文版本 |
| `turn_checkpoint` | 回到受影响 turn 的起点 | turn 粒度对照，需 runner 提供独立边界 |
| `temporal_checkpoint` | 从错误发生的时间点向后回滚 | 时间点恢复对照组 |
| `whole_branch_abort` | 丢弃整个未提交分支 | 整分支恢复对照组 |

所有恢复配置使用相同的故障注入轨迹和成功判定。`no_fault` 不经历同一故障
恢复过程，因此它适合说明无故障工作量，但不能直接作为“因果回滚节省量”的唯一基线。
主要令牌结论比较 `causal-compact` 与无回滚和各粗粒度恢复策略。`causal-full` 只在
Breakdown 中用于量化恢复交接压缩的贡献，不进入真实负载主结果。

## 4. 紧凑恢复上下文

AgentTX 的紧凑恢复交接由以下环境变量控制：

```bash
export AGENTTX_RECOVERY_CONTEXT=compact
```

该开关只作用于 `causal`。虽然底层未设置时仍默认 `full`，但正式实验启动器必须显式
设置 `compact`，结果也必须满足 `recovery_context_variant=compact`。

历史运行中未设置该变量的 `causal` 是 **causal-full**，只能作为审计或 Breakdown 数据，
不能并入正式主结果。正式真实负载的必跑集合为：

```text
causal-compact
crash_no_rollback
turn_checkpoint（仅当 runner 提供独立的多 turn 边界）
temporal_checkpoint-full
whole_branch_abort-full
clean_recovery
```

Breakdown 中比较 `causal-full` 和 `causal-compact` 时，必须使用相同任务、模型、推理
强度、代码和验证器，并通过结果字段 `recovery_context_variant` 区分。Codex 自身可能
发生的自动上下文压缩不等于 AgentTX 的紧凑恢复交接，两者必须在论文中分开描述。

## 5. 分析型 Microbenchmark（Motivation）

Motivation 统一使用 `agent.md` 定义的 MB1--MB3 microbenchmark，不混入真实轨迹、模型
token 或已实现的 causal 策略：

1. **MB1 scale**：固定四步 closure，sweep turn width，共 100 个 case。
2. **MB2 chronology**：固定 width 和 closure，sweep fault position，共 90 个 case。
3. **MB3 dependency**：sweep 21 个 `dependency_value`，共 210 个 case。

Motivation 只保留两张主图：`FIG-Motivation-Coarse-Waste` 和
`FIG-Motivation-Dependency`。`agenttx_causal`、幂等性、连续隔离运行成本、长轨迹尾延迟、
native trace、真实模型 replay、SWE-Bench 和 Terminal-Bench 均属于 Evaluation 或
Appendix，不得与 MB1--MB3 合并成同一组 Motivation 数据。

完整复现命令、case grid、策略定义和图表契约以仓库根目录 `agent.md` 为准。

## 6. 完整系统 Microbenchmark

完整系统实验直接执行 Motivation 的同一批 400 个 MB1--MB3 manifest，不生成新的 case。
`case_id`、turn 数、操作顺序、依赖值、故障位置和预期 survivor set 必须完全一致。每个
case 和策略运行三次；策略集合、成功判定、prompt 公平性和验收条件以 `agent.md` 第 15
节为准。调试子集不进入论文结果。

## 7. 真实负载实验

### E0. 环境检查

任何模型实验开始前，都必须在正式 Conda 环境中执行环境检查：

```bash
source /home/bfq/miniconda3/etc/profile.d/conda.sh
conda activate agenttx
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

PYTHONPATH=src:. python experiments/scripts/check_experiment_environment.py \
  --suite all --task-set full \
  --output experiments/results/environment_check
```

首次准备环境时可以额外使用 `--prepare-missing`；正式启动前必须再执行一次不带该参数的
只读检查。检查范围包括 Conda 环境、Python 依赖、Docker 服务、SWE 官方镜像和
Terminal-Bench 任务定义，不运行 AgentTX、`try` 或完整官方测试。

通过条件为 541 个任务全部通过且脚本退出码为零。任何任务失败都必须先修复环境，不能
通过缩小任务列表绕过。

### E1. 代表任务冒烟实验

在分层代表集中运行全部适用的正式主配置，每格一次。所有
预期单元格必须完整，且满足
`environment_error=0`、`usage_source` 不为 `none`、验证字段完整，以及不同 worker
的模型、推理强度和超时一致。

### E2. 代表任务重复实验

对按 SWE 仓库和 Terminal-Bench 难度、类别分层的代表集运行全部适用的正式主配置，每格至少三次，
预算允许时运行五次。该实验用于报告方差、置信区间、中位数、第 95 百分位数及配对胜率。
不得在同一运行目录中途切换紧凑上下文变量。

### E3. 全量应用实验

以下历史运行仅作 provenance，不能作为新协议下的规范主结果：

| 项目 | 配置 |
|---|---|
| 模型 | `gpt-5.6-luna` |
| 推理强度 | `max` |
| 模式 | `no_fault causal temporal_checkpoint whole_branch_abort` |
| 重复次数 | 1 |
| 任务数 | 541 |
| 预期单元格 | 2164 |
| worker 数 | 4 |
| 最大并发模型请求 | 4 |
| 单次模型超时 | 1800 秒 |
| 恢复上下文 | `full`，未开启 compact |
| 镜像策略 | `AGENTTX_KEEP_EVAL_IMAGES=1`，保留评测镜像 |

当前运行名称为 `codex_luna_max_4mode_full_r1_parallel_20260907`。启动器必须显式传递
`model_reasoning_effort="max"`，把配置写入 `run_config.json`，在任一分片非零退出或
出现环境错误时停止全部分片，并且不得运行 Docker 镜像清理命令。中断后必须使用相同
运行名称、worker 数和模式集合续跑，只跳过已经完成的单元格。

本次运行记录的 Git 工作树为 dirty，因此可以作为当前全量测量和调试依据，但最终论文
的规范数据仍应在冻结提交上复现，或者在论文产物中明确记录该运行对应的完整差异。

新协议下的规范运行必须重跑全量 `causal-compact`，并与相同冻结版本下的其他正式主配置
配对：

| 项目 | 配置 |
|---|---|
| 模式 | `causal` |
| 恢复上下文 | `compact` |
| 任务数 | 541 |
| 预期单元格 | 541 |
| 模型与并发 | 与第一阶段完全一致 |
| 结果目录 | 独立目录，不得复用第一阶段目录 |

建议运行名称为 `codex_luna_max_causal_compact_full_r1_parallel_20260907`。启动时必须显式
设置 `AGENTTX_RECOVERY_CONTEXT=compact`，并继续设置
`AGENTTX_KEEP_EVAL_IMAGES=1`。完成后必须检查所有正式 `causal` 结果均满足
`recovery_context_variant=compact`。必跑单元格总数由冻结后的适用策略清单计算，不再
沿用历史四模式运行的 2705 单元格口径。

```bash
AGENTTX_RECOVERY_CONTEXT=compact \
AGENTTX_KEEP_EVAL_IMAGES=1 \
PYTHONPATH=src:. python experiments/scripts/run_parallel_official_tasks.py \
  --run-name codex_luna_max_causal_compact_full_r1_parallel_20260907 \
  --workers 4 --max-requests 4 --repeats 1 \
  --modes causal --model gpt-5.6-luna --reasoning-effort max \
  --harness-timeout 1800 --python "$CONDA_PREFIX/bin/python"
```

### E4. 第二模型泛化

Codex 主结果稳定后，在同一代表任务集上使用第二个模型运行四个恢复配置，每格至少三次，
其中 `causal` 同样只使用 `causal-compact`。
第二模型用于说明智能体层面的泛化，不要求第一阶段复制完整的 541 个任务。

## 8. 指标与统计口径

### 8.1 正确性指标

- `success`：智能体正常结束、提交成功、官方验证通过且恢复不变量成立；
- `tests_ok`：官方任务验证通过；
- `independent_retained`：因果独立的有效工作保持不变；
- `derived_removed`：错误派生结果已经删除；
- `environment_error`：验证器或运行基础设施未能正常执行。
- `idempotence_ok`：第二次相同恢复及 reload 后重放均不改变语义可观察状态；
- `second_recovery_targets`：第二次恢复实际选择的目标，预期为空或明确的 no-op。

### 8.2 令牌指标

- `total_tokens`：主修复会话报告的令牌量；
- `doc_replay_tokens`：重建被恢复策略删除的独立文档所消耗的令牌量；
- 合计令牌量：`total_tokens + doc_replay_tokens`；
- 主要节省量：同任务、同重复、双方都成功时，各对照策略合计令牌量分别减去
  `causal-compact` 的合计令牌量；
- compact 收益只在 Breakdown 中计算：配对成功样本中，`causal-full` 合计令牌量减去
  `causal-compact` 合计令牌量。

令牌比较必须优先使用配对成功样本，同时报告样本数、成功率、均值、中位数和配对胜率。
不能把失败较多但令牌较少的策略解释为更高效，也不能用尚未配齐全部适用配置的任务计算主
结论。

### 8.3 时间和系统指标

报告恢复时间、端到端时间、中位数、第 95 和第 99 百分位数，同时记录快照空间、跟踪
开销、worker 超时、崩溃恢复和宿主机污染情况。

## 9. Breakdown、正确性与其他开销

- Breakdown 在同一组 400-case Microbenchmark 上逐项移除 READ/NEGATIVE capture、
  object identity、selective history reconstruction 和 context rewind；persistent worker 与
  compact handoff 作为性能消融单独报告。
- Appendix correctness 覆盖 effect 类型、同路径多版本、目录层次、rename、symlink、
  hardlink、fd-relative、多个故障根、重复恢复、reload 和 workspace/context 一致性。
- Interrupted recovery 在恢复计划、generation 构建、workspace swap、context rewrite 和
  metadata persistence 五个阶段注入 `SIGKILL`。恢复后只能观察到完整旧状态或完整新状态。
- Other Overheads 报告 tracing、历史存储、RSS、ledger/message index、closure planning、
  reconstruction、context handoff、启动和 reload 成本。

详细矩阵、重复次数和验收条件以 `agent.md` 第 17--19 节为准。

## 10. 停止条件

出现以下任一情况时，停止扩大实验或由 supervisor 自动停机：

1. `environment_error` 非零；
2. 任一 worker 非零退出；
3. 任务数量或预期单元格数量与配置不一致；
4. 模型、推理强度、提示、验证器或代码在运行中发生变化；
5. `usage_source=none` 或令牌字段缺失；
6. 官方验证结果与 runner 的成功判定不一致；
7. 已完成单元格被重复执行或不同分片发生键冲突。
8. 幂等性矩阵中第二次恢复改变工作区、控制状态或最终提交结果。

任务本身未解决不属于环境错误，应保留为正常失败样本。

## 11. 数据管理

- 环境检查、冒烟、校准、正式运行和定点补跑使用不同目录；
- 每次运行保存代码提交、dirty 状态、主机环境、Conda Python、Codex CLI、模型、推理
  强度、超时、并发、命令和任务清单；
- 环境失败只允许在相同代码和配置下定点续跑；代码变化后必须新建运行代次；
- 图表只读取明确指定的规范 CSV，不扫描整个结果目录猜测“最新文件”；
- 分片结果先按唯一键去重，再生成合并表和论文图；
- 调试结果和历史结果不得混入最终规范数据。

## 12. 推荐执行顺序

1. 冻结代码、prompt、模型、推理强度、验证器和结果 schema；
2. 重现 MB1--MB3 的 400-case 分析型 Microbenchmark；
3. 完成协议调试后，对同一 400 个 case 运行完整系统三次重复；
4. 使用 Conda 完成 541 个真实任务的只读环境检查和分层冒烟；
5. 以一遍完整任务为主结果，运行 SWE-Bench Lite 与 Terminal-Bench 所有模式；
6. 审计环境错误、缺失单元格、重复键、上下文变体、成功率和 token 来源；
7. 在冻结配置下运行 Breakdown；
8. 完成 Appendix correctness 与五阶段 interrupted-recovery 矩阵；
9. 运行 Other Overheads 并报告时间、空间、RSS 和 context 成本；
10. 锁定规范数据，统一生成表格、图和论文文本。
