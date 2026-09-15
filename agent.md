# AgentTX 在 SWE-Bench Lite + Terminal-Bench 全目录上的统一随机 Crash 七组实验矩阵

你是 `/home/bfq/agenttx` 上本实验的唯一操作员。目标是在 SWE-Bench Lite 与
Terminal-Bench 的完整官方目录上，以相同任务、相同随机 crash 位置和相同模型配置，
对比官方任务 runner 提供的全部七个实验 mode 的正确性、恢复完整性和总 token 成本。
先完成两个 suite 的 smoke gate，再启动全量实验。
不要把旧实验结果拼入新实验，不要在主实验运行期间修改策略、提示词、故障注入或
计量代码。

**全局并发硬限制：本用户账号在本项目中的 agent/model 请求并发最多为 4。** 该限制
适用于 smoke、主实验、续跑和第 11 节 motivation 实验。七个 mode 必须由最多四个
worker 排队调度；不得把一个实验拆成多个 supervisor、后台命令或对话来绕过限制。
启动前先检查本用户已有的相关实验进程，并把它们占用的请求计入总数。并发超过 4 会
导致 agent 账号卡顿，因此任何命令中的 `--workers` 和 `--max-requests` 都不得大于 4。

**Causal context 全局硬限制：所有实验中的 `causal` / `agenttx_causal` 必须全程使用
`compact` context。** 该限制适用于 smoke、主实验、续跑、失败补跑、消融和第 11 节
motivation；不得继承 `AGENTTX_RECOVERY_CONTEXT=full`，也不得临时切换为 full。通过
官方任务 runner 运行时必须显式传入 `--causal-recovery-context compact`，并检查
`run_config.json` 和每条 causal 结果的 `recovery_context_variant` 均为 `compact`。
motivation 的固定 manifest replay 不包含完整故障前历史，因此其 `agenttx_causal`
输入也必须保持 compact 表示。

## 1. 研究问题

主问题：agent 已经发生可观测故障后，不同回滚范围对最终成功率和恢复成本有何影响？
辅助问题是无故障执行、恢复提示本身和故障污染分别带来多少成本。

七个 mode 必须全部运行。它们不是七种 crash，也不是七种回滚算法，而是一个完整的
实验控制矩阵：

| mode | 注入 crash | 回滚 | agent 提示 | 角色 |
|---|---:|---|---|---|
| `no_fault` | 否 | 无 | 原始任务提示 | 干净执行基线 |
| `crash_direct` | 是 | 无 | 原始任务提示 | 故障污染本身的对照 |
| `clean_recovery` | 否 | 无 | recovery-shaped compact 提示 | 无故障 compact 恢复基线 |
| `crash_no_rollback` | 是 | 无 | recovery-shaped 提示 | agent 自行处理故障的对照 |
| `causal` | 是 | 因果影响锥 | recovery-shaped compact 提示 | AgentTX 主回滚策略 |
| `temporal_checkpoint` | 是 | 从故障时刻向后 | recovery-shaped 提示 | 时间回滚策略 |
| `whole_branch_abort` | 是 | 整个未提交分支 | recovery-shaped 提示 | 全量回滚策略 |

其中 `crash_no_rollback`、`causal`、`temporal_checkpoint` 和
`whole_branch_abort` 是同一 crash 下的四种故障处理/回滚选择。`crash_direct` 是原始
提示控制组，不应被称为第五种回滚算法。`no_fault` 与 `clean_recovery` 不注入故障，
也不能加入“随机 crash 后成功率”的分母。

## 2. 公平性约束

1. 正式全量必须同时包含 SWE-Bench Lite 和 Terminal-Bench，使用本地完整官方目录，
   不得把 50-task SWE pilot 称为全量。随机 crash、配对 seed 和 causal 自动定位必须
   在两个 suite 上都生效；任一 suite 未通过对应 smoke 时不得启动全量。
2. 七个 mode 必须使用同一任务集合、同一 `repeat`、同一模型、同一 reasoning effort、
   同一 verifier 和同一 timeout。
3. 五个有 crash 的 mode 必须共享同一注入器、faulty producer、独立文档集合、文档
   顺序、derived artifact、失败 verifier 和 crash 位置。固定 `AGENTTX_FAULT_SEED=0`；
   trial seed 由 `seed + task + repeat` 派生且不包含 mode，因此同一
   `(task, repeat)` 的五个 crash 组必须有相同的 `crash_trial_seed` 和
   `fault_after_independent_steps`。
4. 设置 `AGENTTX_RANDOMIZE_CRASH=1`。每个有 crash 的 cell 注入一次 crash；
   正式全量使用 `--repeats 1`，即每个任务只进行一次随机位置 trial。两个无 crash
   基线仍不注入。
5. 设置 `AGENTTX_AUTO_LOCALIZE=1`。`causal` 必须通过运行时故障定位得到根步骤，
   不得直接把隐藏的 injected root 当作 causal 的定位结果。
6. 全局设置 `AGENTTX_RECOVERY_CONTEXT=full`，但通过 runner 的
   `--clean-recovery-context compact` 和 `--causal-recovery-context compact` 分别覆盖
   `clean_recovery`、`causal` worker。`no_fault` 和 `crash_direct` 的记录应为 `none`，
   `clean_recovery` 与 `causal` 必须为 `compact`，其余三个 recovery-shaped 组必须为
   `full`。这项设计按要求以 clean compact 替换 clean full；
   因此 `no_fault` 与 `clean_recovery` 的差异同时包含恢复提示和 compact 投影影响，
   不能再解释成纯提示词消融。
7. 七个 mode 应放在同一个 run 中，并用 `--mode-parallel` 在最多四个 worker 上排队
   执行，避免不同日期、服务负载或代码版本成为混杂因素。
8. 正式 run 启动后，测试未通过是有效实验结果，不重跑。只有 `environment_error=True`
   或未完整落盘的 cell 可以用同一 run name 续跑。
9. 若修改了恢复策略、故障定位、注入轨迹、prompt 或 token 计量逻辑，当前 run 作废，
   必须换新 run name 从头开始。只修不影响实验语义的基础设施 bug 时，才允许续跑，
   并在最终报告中记录补丁和时间。

## 3. 固定配置

- 仓库：`/home/bfq/agenttx`
- Python：`/home/bfq/miniconda3/envs/agenttx/bin/python3.11`
- Harness：Codex CLI
- 模型：`gpt-5.6-luna`
- Reasoning effort：`max`
- Harness timeout：1800 秒
- 正式任务集：SWE-Bench Lite 完整 test split 300 tasks，加 Terminal-Bench
  `original-tasks` 完整目录 241 tasks，共 541 tasks
- 正式全量：541 tasks × 1 repeat × 7 modes = 3,787 cells
- 50-task SWE 冻结集仅作为 pilot/subset，不属于正式全量
- 并发：最多 4 个 worker、4 个外部模型请求；七个 mode 排队执行
- trace backend：默认 `strace`
- verifier：SWE-Bench 官方 Docker eval；Terminal-Bench 官方 task verifier/container

已有的 `causal compact`、clean 或其他旧结果只能用于辅助分析，不能替代本轮同期、
同代码、同随机注入计划跑出的七组数据。

## 4. 启动前检查

进入仓库并固定环境：

```bash
cd /home/bfq/agenttx
export AGENTTX_PYTHON=/home/bfq/miniconda3/envs/agenttx/bin/python3.11
export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx
export AGENTTX_SWE_VERIFY=docker
export AGENTTX_KEEP_EVAL_IMAGES=1
export AGENTTX_RECOVERY_CONTEXT=full
export AGENTTX_RANDOMIZE_CRASH=1
export AGENTTX_AUTO_LOCALIZE=1
export AGENTTX_FAULT_SEED=0
```

不要打印 API key。不要关闭或杀死其他用户、其他对话或无关实验进程。若资源不足，
等待资源释放；只能按准确 PID 停止本实验自己的 supervisor。

先执行定向测试：

```bash
"$AGENTTX_PYTHON" -m pytest -q \
  tests/test_fault_localization.py \
  tests/test_recovery_state_consistency.py \
  tests/test_parallel_official_tasks.py \
  tests/test_official_tasks.py
```

确认完整 catalog 数量，不要手工重排、删减或替换：

```bash
"$AGENTTX_PYTHON" - <<'PY'
from pathlib import Path
from experiments.workloads import swe_bench_suite as swe
from experiments.workloads import terminal_bench_suite as tb
root = Path("experiments/cache")
assert len(swe.load_tasks(root)) == 300
assert len(tb.load_tasks(root)) == 241
PY
```

使用完全相同的任务集完成无模型调用的环境检查。若失败，先修环境，不要启动模型：

```bash
"$AGENTTX_PYTHON" experiments/scripts/check_experiment_environment.py \
  --suite all --task-set full \
  --output experiments/results/environment_check_random_crash_official541
```

## 5. 双 Suite Smoke Gate

先选冻结任务集中跨 8 个仓库的 8 个 case，跑 2 repeats × 7 modes，共 112 cells。
这是 SWE 子门禁，只验证实验管线，不进入正式统计。由于旧版本未对 Terminal-Bench
随机化 crash，它不能单独放行正式全量。还必须在最新代码上运行下方 TB 子门禁。

```bash
SMOKE_RUN=codex_luna_max_random_crash_7mode_cleancompact_causalcompact_swe8_r2_smoke_v4_20260913
SMOKE_LOG=experiments/logs/$SMOKE_RUN
mkdir -p "$SMOKE_LOG"
SMOKE_TASKS=(
  swe:django__django-10924
  swe:matplotlib__matplotlib-18869
  swe:mwaskom__seaborn-2848
  swe:pylint-dev__pylint-6506
  swe:pytest-dev__pytest-7220
  swe:scikit-learn__scikit-learn-10297
  swe:sphinx-doc__sphinx-11445
  swe:sympy__sympy-11400
)

nohup setsid env \
  PYTHONPATH="$PYTHONPATH" \
  AGENTTX_SWE_VERIFY="$AGENTTX_SWE_VERIFY" \
  AGENTTX_KEEP_EVAL_IMAGES="$AGENTTX_KEEP_EVAL_IMAGES" \
  AGENTTX_RECOVERY_CONTEXT="$AGENTTX_RECOVERY_CONTEXT" \
  AGENTTX_RANDOMIZE_CRASH="$AGENTTX_RANDOMIZE_CRASH" \
  AGENTTX_AUTO_LOCALIZE="$AGENTTX_AUTO_LOCALIZE" \
  AGENTTX_FAULT_SEED="$AGENTTX_FAULT_SEED" \
  "$AGENTTX_PYTHON" -u experiments/scripts/run_parallel_official_tasks.py \
  --run-name "$SMOKE_RUN" \
  --workers 4 --max-requests 4 --mode-parallel \
  --clean-recovery-context compact \
  --causal-recovery-context compact \
  --repeats 2 \
  --modes no_fault crash_direct clean_recovery crash_no_rollback \
          causal temporal_checkpoint whole_branch_abort \
  --model gpt-5.6-luna --reasoning-effort max \
  --harness-timeout 1800 --python "$AGENTTX_PYTHON" \
  --tasks "${SMOKE_TASKS[@]}" \
  >> "$SMOKE_LOG/supervisor.log" 2>&1 &
SMOKE_PID=$!
printf '%s\n' "$SMOKE_PID" > "$SMOKE_LOG/supervisor.pid"
```

Smoke 放行条件：

- 112 个预期 key 全部有记录，且没有重复 key；
- `environment_error=0`；
- `no_fault`、`clean_recovery` 为 `fault_injected=False`，其随机 crash 字段为空；
- 另外五组均为 `fault_injected=True`、`fault_origin=harness_injected_crash`；
- `no_fault`、`crash_direct` 的 `recovery_context_variant=none`，`clean_recovery` 与
  `causal` 为 `compact`，其余三个 recovery-shaped 组为 `full`；
- 同一 `(task, repeat)` 的 `crash_trial_seed`、`fault_after_independent_steps`、
  `docs_before`、`docs_after`、`faulty_path` 和 `derived_paths` 在五个 crash mode 间一致；
- causal 的 `localization_validation=passed`、
  `localization_matches_injected_root=True`；
- `crash_no_rollback` 的 `rollback_targets=[]`；
- causal、temporal、whole 的 rollback 结果符合各自策略；
- supervisor 最终为 `failed_shards=[]` 且 `stop_reason=""`。

随机采样允许少量位置碰撞，不要求 2 次 repeat 必须落在两个不同位置。但必须输出
`fault_after_independent_steps` 的直方图，确认不是所有任务都固定在步骤 1。

Terminal-Bench 子门禁使用三个官方任务、2 repeats、7 modes，共 42 cells：

```bash
TB_SMOKE_RUN=codex_luna_max_random_crash_7mode_cleancompact_causalcompact_tb3_r2_smoke_v1_20260913
TB_SMOKE_TASKS=(tb:hello-world tb:csv-to-parquet tb:log-summary)

"$AGENTTX_PYTHON" -u experiments/scripts/run_parallel_official_tasks.py \
  --run-name "$TB_SMOKE_RUN" \
  --workers 4 --max-requests 4 --mode-parallel \
  --clean-recovery-context compact \
  --causal-recovery-context compact \
  --repeats 2 \
  --modes no_fault crash_direct clean_recovery crash_no_rollback \
          causal temporal_checkpoint whole_branch_abort \
  --model gpt-5.6-luna --reasoning-effort max \
  --harness-timeout 1800 --python "$AGENTTX_PYTHON" \
  --tasks "${TB_SMOKE_TASKS[@]}"
```

TB 子门禁执行与 SWE 相同的字段、配对和 rollback 检查。尤其要断言五个 crash mode
共享 `crash_trial_seed` 和随机位置，且 causal 的自动定位命中 injected root。两个
suite 的 smoke 都通过、环境错误补跑清零后，才能启动正式全量。

## 6. 主实验

只有 smoke 全部满足放行条件后才能启动。主实验必须使用新的空 run name：

```bash
MAIN_RUN=codex_luna_max_random_crash_7mode_cleancompact_causalcompact_official541_r1_20260913
MAIN_LOG=experiments/logs/$MAIN_RUN
mkdir -p "$MAIN_LOG"

nohup setsid env \
  PYTHONPATH="$PYTHONPATH" \
  AGENTTX_SWE_VERIFY="$AGENTTX_SWE_VERIFY" \
  AGENTTX_KEEP_EVAL_IMAGES="$AGENTTX_KEEP_EVAL_IMAGES" \
  AGENTTX_RECOVERY_CONTEXT="$AGENTTX_RECOVERY_CONTEXT" \
  AGENTTX_RANDOMIZE_CRASH="$AGENTTX_RANDOMIZE_CRASH" \
  AGENTTX_AUTO_LOCALIZE="$AGENTTX_AUTO_LOCALIZE" \
  AGENTTX_FAULT_SEED="$AGENTTX_FAULT_SEED" \
  "$AGENTTX_PYTHON" -u experiments/scripts/run_parallel_official_tasks.py \
  --run-name "$MAIN_RUN" \
  --workers 4 --max-requests 4 --mode-parallel \
  --clean-recovery-context compact \
  --causal-recovery-context compact \
  --repeats 1 \
  --modes no_fault crash_direct clean_recovery crash_no_rollback \
          causal temporal_checkpoint whole_branch_abort \
  --model gpt-5.6-luna --reasoning-effort max \
  --harness-timeout 1800 --python "$AGENTTX_PYTHON" \
  >> "$MAIN_LOG/supervisor.log" 2>&1 &
MAIN_PID=$!
printf '%s\n' "$MAIN_PID" > "$MAIN_LOG/supervisor.pid"
```

用 `$MAIN_LOG/supervisor.pid` 记录并核对 supervisor PID。每 5 分钟报告一次进展，
至少包含：正常完成数/3787、按 suite 和 mode 分组的成功数与成功率、环境错误数、
活跃 worker 数、累计总 token。不要用高频轮询影响实验。

## 7. 中断与续跑

先确认原 supervisor 已退出，不允许同一 run name 同时存在两个 supervisor。续跑必须
使用与首次启动完全相同的环境变量、任务顺序、worker 数、mode 顺序和参数，再增加
`--resume-mode-subset`。runner 会跳过所有 `_completed_attempt=True` 的 cell，并重跑
环境错误或中断项。

有效的 `tests_ok=False` 或 `success=False` 是策略结果，不得为了提高成功率重跑。
外部 session 未启动、timeout、Docker/verifier 未执行等 `environment_error=True` 才是
可重试项。补跑结束后执行一次规范合并：

```bash
"$AGENTTX_PYTHON" experiments/scripts/run_parallel_official_tasks.py \
  --run-name "$MAIN_RUN" --workers 4 \
  --modes no_fault crash_direct clean_recovery crash_no_rollback \
          causal temporal_checkpoint whole_branch_abort \
  --merge-only
```

merged 输出必须按冻结 task 顺序、mode 顺序和 repeat 排列。用
`(suite, task, mode, repeat, harness_backend)` 检查唯一性，不能按 CSV 行号配对。

## 8. 指标口径

主正确性指标：

- `success`：完整结束、提交成功、官方 verifier 通过且恢复不变量成立；
- `tests_ok`：官方修复是否通过；
- `independent_retained` / `independent_unchanged`：独立有效工作是否保留且未被修改；
- `derived_removed`：故障派生物是否清除；
- `recovery_manifest_authoritative` / `recovery_manifest_intact`：恢复证书是否有效；
- causal 定位准确率：`localization_matches_injected_root` 的均值；
- `environment_error`：基础设施无效 cell，单列并从策略成功率分母中排除，补跑后应为 0。

主成本定义必须是：

```text
combined_tokens = total_tokens + doc_replay_tokens
```

其中 `total_tokens` 是主 agent session，`doc_replay_tokens` 是粗粒度回滚后恢复独立
文档的额外 agent 成本。不得只报 `total_tokens`，也不得把 replay 忽略为 0。每个 mode
至少报告：

- 有效 cell 数、成功数、成功率和 tests pass rate；
- combined token 总和、mean、P50、P95、P99；
- 主 session token mean 与 replay token mean，分开列出；
- wall time mean、P50、P95；
- independent retention rate、derived removed rate；
- crash 位置直方图和 causal 定位准确率。

同时报告两套 token 统计：

1. **所有有效 cell**：反映实际预算消耗，失败也消耗 token；
2. **成功 cell**：仅用于比较成功解题的条件成本，必须与成功率并列展示。

策略差异使用同一 `(task, repeat)` 配对。额外报告：

- 七组都有效时的 paired success rate；
- 两组都成功时，`combined_tokens` 的均值差、中位数差、bootstrap 95% CI 和胜率；
- causal 相对每个对照的 token ratio；
- 不允许拿不同 case 数的总体均值直接宣称优劣。

至少给出以下成对因果对照：

- `no_fault` vs `crash_direct`：相同原始提示下的 crash 污染成本；
- `no_fault` vs `clean_recovery`：无 crash 时 recovery-shaped prompt 与 compact 投影的
  联合影响；不能据此单独归因 compact 或提示词；
- `crash_direct` vs `crash_no_rollback`：同一 crash、同为不回滚时提示条件的影响；
- `crash_no_rollback` vs causal/temporal/whole：回滚相对 agent 自行修复的效果；
- causal vs temporal vs whole：不同回滚粒度的核心比较。

## 9. 最终完整性检查

正式结果目录：

```text
experiments/results/<MAIN_RUN>/merged/official_tasks_raw.csv
```

发布前必须满足：

- 唯一 cell 数为 3,787；
- SWE-Bench Lite 恰好 300 tasks，Terminal-Bench 恰好 241 tasks；
- 七个 mode 各恰好 541 个 cell；
- 每个 `(task, repeat)` 恰好有七个 mode；
- `environment_error=0`；
- 所有行模型均为 `gpt-5.6-luna`；两个 direct-prompt 组上下文为 `none`，
  `clean_recovery` 与 `causal` 为 `compact`，其余三个 recovery-shaped 组为 `full`；
- 两个无 crash 组没有伪造注入字段；五个 crash 组真实注入同一 crash 计划；
- causal 的定位字段完整，定位失败不得静默改用 injected root；
- token 的 `usage_source` 不为 `none`；
- combined token 由原始两列逐行相加后再聚合；
- run config、supervisor log、原始 CSV 和代码版本信息一并保留。

最终在结果目录写 `REPORT.md`。先给事实表，再写结论；明确区分策略效果、compact
上下文优化和 agent 随机探索方差。不要因为 causal 偶然比 clean/no-fault 更省 token
就声称 crash 会降低成本；这种现象只能解释为提示信息改变了 agent 的探索路径。

## 10. 停止条件

只在以下情况停止并向用户报告：

1. smoke 不满足放行条件；
2. 正式 run 出现真实 runner 崩溃或配置漂移；
3. 需要修改会改变实验语义的代码，此时废弃当前 run，等待确认后换名重跑；
4. 3,787 个有效 cell 全部完成、环境错误清零、最终合并和 `REPORT.md` 完成。

50-task SWE 实验只能作为成本 pilot 或调试数据；不得替代、拼入或冒充上述双 catalog
正式全量结果。

## 11. Paper Motivation：Loop 内粗粒度回滚浪费

本节是论文 motivation 实验的执行规格，不与前面的七组随机 crash 主实验混合统计。
它只回答一个问题：**故障发生在一个 agent interaction loop 内时，以整个 loop、时间
后缀或整个分支为恢复单位，会额外丢弃多少本来仍然有效的工作？** 所有策略必须接收
完全相同的执行轨迹、故障根和依赖图；不得让不同策略重新采样 crash 位置。

### 11.1 术语和当前代码边界

这里把一个 loop 严格定义为一次模型 action turn，而不是整个 agent session。在当前
原生 SWE-agent 接入中，一次 `agent.step()` 产生的环境调用由
`SWEAgentNativeAdapter._record_swe_step()` 合并为一个 `TurnRecord`；每个环境调用仍是
一个独立 ledger step。`ConversationLog._messages_for_turn()` 可以从同一 turn 的
assistant `tool_calls` 中删除失效 call、保留 active call，所以当前 causal recovery
确实支持 **turn 内、tool-call 级** 的非连续恢复。

边界必须在论文中写清楚：一个 shell command 即使产生很多 syscall/effect，在当前
ledger 中仍是一个 step。AgentTX 目前不能保留该 command 内的部分 syscall 效果，
因此下面实验的最小恢复单位是 tool call/ledger step，不能把 syscall 数当成可选择性
恢复的 step 数。

Crab 的[论文](https://arxiv.org/abs/2604.28138)语义是根据 turn 的 OS effects 决定
是否以及保存哪类 checkpoint，并在 turn 边界建立/恢复 checkpoint。本文只能实现一个
`turn_checkpoint_proxy` 来复现这种恢复边界；本地没有 Crab 官方 artifact，严禁把
proxy 数字写成“Crab 实测性能”。该 proxy 假定目标 turn 之前恰好有可用 checkpoint，
是对 turn-boundary 方法有利的 best case；真实系统若最近 checkpoint 更早，只会丢失
更多工作。当前
代码的 `tx.rollback(fault_step)` 是更细的 step-suffix temporal baseline，甚至比按 turn
恢复更乐观；`tx.rollback(0)` 是 whole-branch baseline；
`tx.rollback_causal_many(roots)` 是 AgentTX 的实际 causal policy。

### 11.2 统一恢复策略

Motivation 图表统一包含以下策略。前六项执行真实恢复，第七项是解析上界：

| policy | rollback set | 作用 |
|---|---|---|
| `no_recovery` | 空集 | 展示污染未清除，不能作为正确方案参与节约比例 |
| `root_only` | 只撤销故障 producer | command undo 消融；可能留下 invalid descendants |
| `turn_checkpoint_proxy` | 故障所在 loop 的所有 step | Crab 风格 turn-boundary 语义代理，不是 Crab artifact |
| `temporal_step` | 从故障 step 到当前末尾 | 当前 `tx.rollback(fault_step)`；粗粒度方法的乐观下界 |
| `whole_branch` | 全部未提交 step | 当前 `tx.rollback(0)` |
| `agenttx_causal` | 故障根与真实 causal descendants | 运行当前源码的实际实现，不使用预先计算的 target 代替运行结果 |
| `ideal_oracle` | 精确的真实 invalid closure | 理想系统，只消除可避免浪费；不计实现开销，不作为已实现系统 |

`ideal_oracle` 的“解决所有浪费”只指不误删任何有效工作。故障根及其真正依赖项必须
被撤销和修复，这是不可避免的恢复成本，不能算成粗粒度浪费。若 AgentTX 捕获的依赖
完整且无路径冲突，它应接近 ideal；实验不能预设二者相等。

### 11.3 统一指标

对目标 loop 的全部 step 记为 `E`，ground-truth invalid causal closure 为 `C`，有效
集合为 `V = E - C`，策略实际撤销集合为 `R_p`。`C` 是 effect read/write edges 与
conversation copied-result edges 的联合传递闭包，不能只看文件名或时间顺序。每个 step
同时记录单位权重、原始生成 token 权重和实际 replay token。至少输出：

- `valid_retention = 1 - |R_p intersect V| / |V|`；
- `invalid_removal = |R_p intersect C| / |C|`；
- `useful_steps_wasted = |R_p intersect V|`；
- `useful_tokens_wasted = sum(token_weight(s), s in R_p intersect V)`；
- `invalid_steps_retained = |C - R_p|`；
- `replay_prompt_tokens`、`replay_completion_tokens`、`replay_total_tokens`；
- `final_correct`：invalid closure 全部移除、有效效果全部存在且 verifier 通过；
- `rollback_ms` 和 `recovery_wall_ms`，但不能用理想系统的零实现开销做性能比较。

相对每个现有策略 `p` 分别报告绝对值和比例：

```text
saved_replay_tokens(ideal, p) = replay_total_tokens(p) - replay_total_tokens(ideal)
saved_replay_pct(ideal, p) = saved_replay_tokens(ideal, p) / replay_total_tokens(p)
saved_replay_tokens(causal, p) = replay_total_tokens(p) - replay_total_tokens(causal)
saved_replay_pct(causal, p) = saved_replay_tokens(causal, p) / replay_total_tokens(p)
```

仅当 `p` 与 causal/ideal 都满足 `final_correct` 时计算 token 节约比例。`root_only` 或
`no_recovery` 若保留污染，只展示 correctness，不得用“低 replay token”宣称更省。
若分母为零，比例记为 `N/A`，不得记成 0% 或 100%。
除 replay 外，再报告无需模型的 `useful_tokens_wasted`，避免模型随机性掩盖恢复边界。
主表给 mean、P50、P95、P99 和 95% bootstrap CI，并保留 paired seed 的逐行差值。

一次模型响应中的多个 tool call 共用 prompt token，不能把整轮 API usage 任意均摊到
step。这里的 `token_weight(s)` 固定定义为用实验模型 tokenizer 对该 call 的 function
name、arguments 和 agent 生成的 artifact payload 序列化后计数；它是工作量归因，不是
账单 token。真实成本只使用 M3 返回的逐次 API prompt/completion usage。

### 11.4 M0：真实 Loop 宽度和可保留空间审计

这是后续实验的前置 gate。用最新 `SWEAgentNativeAdapter` 对成功的无故障 SWE-Bench
轨迹落盘 `TurnRecord`、`CallSpan.step_id`、ledger parents 和每次模型调用 token。至少
取冻结 50-task 集中 20 个成功 case；不足 20 个时继续取 case，而不是混入失败轨迹。

每个 loop 统计：

- tool-call/ledger-step 数、write/delete step 数和总 effect 数；
- loop 内 weakly connected causal component 数；
- 随机选择一个 state-mutating root 后，`|C|`、`|V|` 以及 turn checkpoint 相比
  causal 多撤销的 step/token；
- 多调用 loop 占比，以及存在至少两个互不依赖 stateful component 的 loop 占比；
- 每 case 和全局的 P50/P95/P99，不只报平均值。

只对同一 loop 内的 `V` 计数，之前和之后 loop 的工作都不进入本图。若真实轨迹大多数
loop 只有一个 ledger step，论文必须如实报告，并把后续 controlled experiment 表述为
“系统能力和压力场景”，不能声称它代表该 agent 的常态。也不能把一个 command 内的
多个文件 effect 拆成多个可保留 step。

建议输出：`motivation_turn_audit_raw.csv`、`motivation_turn_audit_summary.csv` 和一张
CDF，横轴为每个 loop 在 turn checkpoint 下可避免丢弃的有效 token。

已完成的原生 SWEAgent M0 位于
`experiments/results/paper_motivation_intra_turn_causalcompact_20260913/`。20 个公开成功
轨迹均通过当前 `SWEAgentNativeAdapter` 重放，三个 replay 分片失败数均为 0；共记录
638 个 turn。M0 报告显示多调用 turn 占比为 100%，但包含至少两个独立 stateful
component 的 turn 仅占 0.94%，因此后续 controlled experiment 必须按压力场景表述。

### 11.5 M1：Loop 内恢复粒度参数扫描

扩展 `causal_retention_dag.py` 和 `bench_causal_retention.py`，但新建 motivation 专用
入口，避免改变论文现有结果。每条轨迹通过真实 AgentTX overlay 执行，并把目标 steps
记录进同一个 `TurnRecord`。参数为：

| 因子 | 取值 |
|---|---|
| loop width | 8, 16, 32, 64, 128 tool calls |
| fault position | 5%, 25%, 50%, 75% |
| invalid-closure ratio | 3%, 10%, 25%, 50% |
| graph shape | chain, fan-out, layered |
| interleaving | independent-before, independent-after, alternating |

每个配置至少 5 repeats；恢复策略共享同一个 materialized trace 和 fault root。主要图是
两张 heatmap：`valid_retention` 和 `invalid_removal`，横轴 fault position，纵轴
invalid-closure ratio，分别按 loop width 分面。另画 `useful_tokens_wasted` 随 loop
width 的增长曲线，并把 `ideal_oracle` 画成上界虚线。

本实验应直接回答三个问题：turn 粗粒度在什么结构下最浪费；乐观的 step-temporal
仍会浪费多少 suffix 中的独立工作；root-only 为什么虽然保留多却不正确。若
`agenttx_causal` 未达到 ideal，必须把差值分成漏依赖、conversation copy edge 扩张和
path-overlap 安全拒绝三类，不能隐藏为均值。

### 11.6 M2：必须包含的极端 Case

构造一个 **单 loop、128 个 tool-call step** 的固定 layered trace：

- step 0 是有效且独立的前缀工作；
- step 1 是 faulty producer；
- 只有 step 32、64、127 依赖该 producer，故 `|C| = 4`；
- 其余 124 个 step 全部有效且彼此独立；
- 四个 invalid step 和有效 step 使用不同路径，排除人为 path conflict；
- 所有策略使用相同 trace、相同 fault root 和相同 replay 内容。

这个构造的无需运行即可验证的期望值是：

| policy | 有效 step 被浪费 | valid retention | invalid removal | 是否可能正确 |
|---|---:|---:|---:|---:|
| `no_recovery` | 0/124 | 100% | 0% | 否 |
| `root_only` | 0/124 | 100% | 25% | 否 |
| `turn_checkpoint_proxy` | 124/124 | 0% | 100% | 是，需重放 |
| `temporal_step` | 123/124 | 0.8% | 100% | 是，需重放 |
| `whole_branch` | 124/124（只计该 loop） | 0% | 100% | 是，需重放 |
| `ideal_oracle` | 0/124 | 100% | 100% | 是 |

`agenttx_causal` 不预填结果，必须运行最新代码后填写；预期 rollback set 为
`{1, 32, 64, 127}`。实验先断言实际 targets，再检查 overlay、conversation active
tool calls、commit 后 host 和 verifier 四者一致。任何一项不一致都算失败。

论文用这个 case 解释上界：turn/whole 粗粒度丢失当前 loop 的全部 124 份有效工作，
乐观 temporal 仍丢失 123/124，即 99.2%；ideal 消除全部可避免的有效工作损失。
这只是构造给出的 effect 数，实际节约多少 token 必须由 M3 测量后填写，不能用
124:4 的比例替代 token 结果。

### 11.7 M3：从丢失工作换算成真实 Replay Token

在 M1 的代表配置和 M2 极端 case 上进行固定协议的模型 replay。每个策略恢复后，给
同一模型一份只描述通用 artifact contract 的 opaque manifest。模型、reasoning、
timeout 和 seed 固定；每个 cell 5 repeats。不得让 coarse policy 获得额外解题提示，
也不得让 causal policy 跳过所有策略都必须完成的 faulty closure 修复。

必须额外运行同一 `(trace, repeat)` 的 `clean_recovery compact` 对照：不注入故障、
不回滚、所有 artifact 已正确保留，使用相同 compact manifest schema。
所有故障策略必须从完全相同的未知位置条件开始：manifest 只给通用 artifact contract，
必须保持 `missing=[]`、`repair=[]`、`fault_step_id=null`，且不得包含 retained count、
oracle closure、污染路径、目标内容或编码 fault position/closure ratio 的内部 trace id。
通用 contract 必须明确索引范围为 `[0, count)`。每个 artifact 对应一个原始 tool call；
`replay_units_required` 按逻辑 artifact 数量统计，但不限制 agent 的实际执行方式。agent
可以使用 loop、批量命令或 multi-file patch；所有策略必须使用相同权限。结果同时记录
逻辑 replay units、写事件数、批量写事件数和单事件最大覆盖路径数。最终成功由 harness
正常退出、token usage 有效和完整 workspace verifier 共同决定，不能因采用批量写而失败。
恢复控制器也不得直接读取 `fault_step_id` 或 `oracle_closure` 来选择回滚目标：它先为
所有策略运行相同的通用 contract 扫描并记录 `localized_fault_steps`，再把该定位结果交给
不同粒度的 rollback 策略。随后 agent 必须检查策略产生的 workspace，自行发现缺失或
污染 artifact 并按通用 contract 恢复。所有 fault arm 的 crash 通知必须逐字相同，且
都不知道 fault step/path。恢复策略使用删掉 invalid history 的 compact post-rollback
上下文；`crash_no_rollback` 则必须保留污染 workspace 和仍活动的完整 pre-crash tool
history，只要求继续原任务，不得额外提供“扫描并修复全部异常”的 repair plan。这里的
上下文差异是策略本身的结果，不是 fault-location 提示差异。外部黑盒 agent 无法恢复
注入的原生 session 时，允许投影完整 tool arguments/result，但投影不得包含 fault id、
oracle closure 或 evaluator 的污染标记。故障真值只用于注入和最终 verifier，以及事后计算
`localization_matches_injected_faults`，不得作为恢复定位或 agent prompt 的输入。
真实恢复额外开销定义为
`recovery_overhead(p) = actual_replay_tokens(p) - actual_replay_tokens(clean_recovery)`。
causal 相对粗粒度策略的节约比例必须以该 paired recovery overhead 为分母；不能直接
用包含 clean 基础调用成本的整体 token 作为分母。原始整体 token 仍需保留供审计。

同时记录成本和效果：

1. `attributed_lost_tokens`：原轨迹中被错误撤销的有效 call 对应 token，确定性指标；
2. `actual_replay_tokens`：恢复后真实额外模型调用的 prompt + completion token。
3. `success_rate`、所有 attempt 的 observed token 总和/均值，以及
   `observed_tokens_per_success = sum(tokens across all attempts) / successful cells`。

成功样本的 P50/P95/P99 仍需报告，但不得只用成功样本均值评价 no-rollback；否则超时或
失败会被从成本中删除，反而奖励更不可靠的策略。`no rollback 最差` 的可检验含义是固定
预算下的总体恢复效用最低（成功率更低和/或每次成功的总成本更高），不是要求它在每个
成功样本的 token 上都高于会重放整个 loop 的 turn/whole 策略。

从 M1 选择 12 个代表 cell：三种 loop width（16/64/128）乘两种 fault position
（5%/50%）乘两种 closure ratio（3%/25%），固定 layered + alternating；再加入 M2。
最终逐一列出 ideal 和 AgentTX 相对 `turn_checkpoint_proxy`、`temporal_step`、
`whole_branch` 的绝对节约 token、百分比和 paired bootstrap CI。正文只使用
`actual_replay_tokens`，attributed 指标用于解释方差。

当前正式 M3 只维护一套协议。所有策略使用相同 opaque artifact contract 和相同未知故障
条件；故障位置、影响路径、retained count 与 path-specific repair 均不进入 prompt。
`crash_no_rollback` 保留无标签 stale artifact、完整 active history 和 agent 已接受成功
observation 的上下文；其余恢复策略删除 invalid history 并使用 compact context。agent
可以自由使用单文件或批量工具，最终成功由 harness 状态、有效 usage 和完整 workspace
verifier 判断。写事件审计只记录 batching 行为，不限制工具粒度。协议字段使用稳定名称，
不在 schema 或目录中递增实现版本；语义变更由 Git commit 追踪。此前产生的试验目录仅作
历史审计，不得与当前正式结果混合。

SWEAgent M3 必须复用上述完全相同的 plan、policy、manifest、pairing、verifier 和
统计代码；统一 runner 只把每个 cell 的 workload backend 从 `CodexHarness` 替换成原生
`SWEAgentNativeAdapter`。SWEAgent 不能参与选择、安排或解释实验，也不能使用
Codex 黑盒任务流程。结果必须写入独立目录，且每行包含
`harness_backend=sweagent_native`、SWEAgent model/config hash、真实 model usage 和
`native_adapter=SWEAgentNativeAdapter`。
SWEAgent workload 必须显式使用本文件第 3 节规定的 Codex 实验配置：
`gpt-5.6-luna`、reasoning effort `max`；不得从当前用户 Codex 配置隐式继承其他模型或
reasoning 值。API endpoint、Responses 协议和鉴权仍复用该用户的 Codex provider 配置。

模型配置必须与本文件第 3 节的 Codex 实验口径一致。runner 从
`/home/bfq/.codex/config.toml` 和 `/home/bfq/.codex/auth.json` 读取 provider、base URL、
response storage 开关和凭据，但正式运行显式覆盖为 `gpt-5.6-luna`、
`reasoning_effort=max`，并在结果中记录非敏感配置快照。SWEAgent/LiteLLM 对 GPT-5 的协议兼容映射为
`temperature=1` 且不传 `top_p`，这不是改变模型口径。凭据不得写入日志、hash 或结果。
运行入口为：

```bash
experiments/scripts/run_sweagent_m3_bwrap.sh \
  --output-dir experiments/results/motivation_sweagent_full_20260915 \
  --sweagent-config /home/bfq/SWE-agent/config/bash_only.yaml \
  --model gpt-5.6-luna \
  --reasoning-effort max \
  --repeats 5
```

正式启动前先用 `--max-plans 1 --repeats 1 --policies temporal_step` 做一个真实模型
gate，要求实际创建缺失 artifact 后仍能 `submitted`、verifier 通过，且 model calls 和
token usage 均大于 0。clean-only gate 不覆盖 AgentTX commit 路径。当前
M0/M4 的无模型 replay 不能替代 SWEAgent M3。

### 11.8 M4：真实轨迹上的反事实恢复

从 M0 的成功轨迹中只选择 `calls_per_turn >= 2` 且 loop 内存在两个以上独立 causal
component 的 turns。每个反事实样本先将可重放 session 裁到目标 turn 的结束边界，
再对该 turn 中每个可变更状态的 step 轮流作为故障根，不重新调用 agent；随后在相同
session prefix 的 fork 上分别执行 `turn_checkpoint_proxy`、`temporal_step`、
`whole_branch` 和 `agenttx_causal`，以原 ledger/conversation 联合闭包作为 correctness
oracle。这样 checkpoint rollback 不会把真实轨迹中尚未发生的后续 turns 混入结果。

该实验报告真实代码任务中 loop 内的 **可挽回空间**，而不是端到端成功率：每个故障点
的有效 step/token 损失、invalid removal 和 AgentTX-to-ideal gap。对每个原始 turn
等权聚合，避免宽 turn 因含有更多候选 fault 而支配结果。主结果报告 median、P95、P99
及“至少可避免 25% / 50% / 75% loop 内有效 token”的 turn 比例。

反事实 fork 只能用于计算恢复边界。如果要声称最终 SWE-Bench 成功率或实际 replay
token，必须另做真实恢复后的 agent continuation；不得把 offline oracle 结果冒充
端到端 agent 运行。

已完成的原生 SWEAgent M4 与 M0 位于同一结果目录。20 条 native trace 中有 3 条轨迹
的 6 个 turn 满足独立 component gate，共产生 18 个 fault point、90 个成对
fault-policy rows；每个策略 18 行，`agenttx_causal` 全部使用 compact context。该批
反事实中 causal 与 ideal 均保留全部有效工作并移除全部 invalid closure，但该结论只
适用于 recovery boundary，不能作为端到端成功率或 billed replay token 结果。

### 11.9 公平性、实现和论文改写要求

1. 所有策略以 `(trace_id, turn_id, fault_step_id, seed)` 成对；注入清单 hash 必须一致。
2. `C` 只用于计分和 ideal，`agenttx_causal` 必须使用实际 ledger parents、
   conversation copy edges 和当前 rollback API，不能读取 oracle target。
3. `turn_checkpoint_proxy` 从目标 `TurnRecord.step_ids` 的最小 active step 开始恢复；不能
   偷换成 fault step suffix，否则低估 loop-boundary 浪费。
4. whole-branch 的主表只统计目标 loop 内浪费；额外丢掉的早期 loop 工作放附录，满足
   “只讲 loop 内”的范围。
5. 每个正确性结论同时验证 merged view、conversation view、commit 后 host 和 verifier。
6. Crab 和 DeltaBox 只作恢复边界的定性映射。没有官方 artifact 的系统不进入本地
   性能排名，不再把 `temporal_step` 的结果直接标成 Crab/DeltaBox 数字。
7. M0 是外部有效性 gate，M1/M2 是机制因果证据，M3 是 token 成本，M4 是真实轨迹
   分布；四类证据不得互相替代。

建议新增文件而不是覆盖旧结果：

```text
experiments/scripts/bench_intra_turn_recovery.py
experiments/scripts/analyze_intra_turn_traces.py
experiments/workloads/intra_turn_recovery_dag.py
experiments/results/paper_motivation_intra_turn_<date>/
```

论文 motivation 的推荐叙事顺序是：M0 证明真实 loop 中是否存在可保留空间；M1 展示
粗粒度浪费随结构增长；M2 用极端 case 直观说明 turn checkpoint 会丢掉什么；M3 把
effect 损失换算为真实 replay token；M4 给出真实任务 trace 上的分布。正文主张限定为
“在 tool-call 可分且依赖可观测的 loop 内，因果恢复消除粗粒度边界造成的有效工作
误删”，不扩展到单个 command 内的 syscall 级选择性恢复。
