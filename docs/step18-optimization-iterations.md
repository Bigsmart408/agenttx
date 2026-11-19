# Step 18 — optimization iteration history and hot-path changes

## Motivation and reproducibility rule

The performance gap is part of the systems motivation, so optimization cannot be
reported only as a final number. Before every production change, the complete
hot-path source is copied under:

```text
src/agenttx/optimization_history/iteration_NN_<name>/
```

Each snapshot contains the prior `runtime.py`, `semisolate.py`, `layers.py`, and
`harness.py`, plus a manifest with the previous benchmark. The snapshots are
source artifacts only and are not imported by AgentTX. This makes each iteration
replayable and prevents a faster implementation from erasing the original
motivation.

## Iteration 00 → 01: known write/delete tools

The unoptimized baseline started strace for every harness tool. The first change
allows `write_file`, `append_file`, and `delete_file` to opt out of read tracing;
overlay fingerprints still record their writes/deletes. Opaque `run_shell` and
`run_tests` remain fully traced.

The baseline was 495.843 ms/step for a 64-call full AgentTX run (two repeats).
A single post-change run measured 500.906 ms/step, so this change is correctness-
preserving but not yet a statistically demonstrated speedup. The result is kept
because it is the pre-image for the next iteration rather than silently removed.

## Iteration 01 → current: known read_file effects

The next change makes `read_file` explicit as either `READ` or `NEGATIVE` using the
merged host/upperdir view, then skips strace for that trusted tool. This preserves
negative-lookup dependencies while removing one strace process and parser pass.

The paired 64-call result was:

| mode | before (iteration 01) | current | recovery |
|---|---:|---:|:---:|
| AgentTX without read tracing | 385.294 ms/step | 385.294 ms/step | expected ablation failure |
| AgentTX full | 500.906 ms/step | 437.534 ms/step | passed |

The full run recorded the same causal targets `[27, 29, 30]` and retained the
independent docs/config edits. The no-trace ablation continued to retain the
derived report, as expected. The current full mean is roughly 12.7% below the single-run previous value, with 10.589 ms/step standard deviation across two repeats. The older baseline was not interleaved with this run, so a controlled paired run is still needed for a final claim.

## Iteration 02 ? current: persistent command script

The pre-image for this change is `iteration_02_known_read_effect_bypass`. The
previous implementation created a fresh `/tmp/agenttx-cmd-*` directory, wrote and
chmod-ed `cmd.sh`, then recursively deleted that directory after every tool call.
The current implementation allocates one private command directory per
`SharedSemisolate`, rewrites the same script each step, and removes it exactly
once in `close()`. The try command, shell body, tracing policy, and effect
collection are unchanged. A focused lifecycle test checks both path reuse and
close-time cleanup.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 331.599 ms/step | 328.601 ms/step | -0.9% | expected ablation failure |
| AgentTX full | 418.899 ms/step | 409.835 ms/step | -2.2% | passed |

The full after standard deviation was 6.003 ms/step across two repeats (before
standard deviation 0.344 ms/step), so this is retained as a low-risk directional
optimization, not a final OSDI performance claim.

## Iteration 03 ? current: deferred blob garbage collection

The pre-image for this change is `iteration_03_persistent_command_script`. Every
`LayerStore.snapshot_before()` call used to scan the content-addressed blob store
and remove unreferenced blobs. During a trajectory, retained snapshots cannot
lose those blobs, so the scan is unnecessary in the hot path. The current code
defers the scan until rollback (where snapshots are dropped) or an explicit
retained-session close; destroying a session still removes the whole tree.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 325.434 ms/step | 324.325 ms/step | -0.3% | expected ablation failure |
| AgentTX full | 406.099 ms/step | 397.104 ms/step | -2.2% | passed |

The full after standard deviation was 0.891 ms/step across two repeats. The
change is retained as a low-risk directional optimization; it does not claim a
final statistical improvement until an interleaved run is collected.

## Iteration 04 ? current: direct executable command scripts

The pre-image for this change is `iteration_04_deferred_blob_gc`. AgentTX already
kept one executable command script per semisolate, but invoked it as
`bash /tmp/.../cmd.sh`, causing an extra shell parse for every try call. The
current script uses a fixed `/bin/bash` shebang and executable mode, so the try
command executes the script path directly. The command body, quoting fallback,
trace placement, and cleanup lifecycle are unchanged.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 324.325 ms/step | 318.575 ms/step | -1.8% | expected ablation failure |
| AgentTX full | 397.104 ms/step | 393.631 ms/step | -0.9% | passed |

The full after standard deviation was 1.286 ms/step across two repeats. This is
retained as a low-risk directional optimization; an interleaved run is still
needed before making a final performance claim.

## Iteration 05 ? current: persistent try worker

The pre-image for this change is `iteration_05_direct_executable_script`. The
previous implementation invoked try once per tool call, so every step paid for a
new mount namespace, overlay setup, device setup, and teardown. The current
implementation starts `try -N` once per `SharedSemisolate` and runs a small framed
stdin worker inside that namespace. Each request executes the already-reused
command script and returns its exit code, stdout, and stderr; trace commands still
write the same raw log path for the existing parser.

Commit, rollback, reset, and close stop the worker before modifying or unmounting
the sandbox. If the worker exits or violates the protocol, the current step falls
back to the original one-shot try invocation and the next step can restart the
worker. This keeps the optimization fail-safe while retaining the existing CLI
try path.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 318.575 ms/step | 66.975 ms/step | -79.0% | expected ablation failure |
| AgentTX full | 393.631 ms/step | 151.531 ms/step | -61.5% | passed |

The full after standard deviation was 5.013 ms/step across two repeats. The
worker reduces the dominant per-call try setup cost; the remaining gap includes
command execution, upperdir snapshots, ledger persistence, and optional strace.


## Iteration 06 -> current: incremental upperdir snapshots

The pre-image for this change is `iteration_06_persistent_try_worker`. The
previous implementation copied the complete upperdir tree into a new
`before_<step>` directory at every step, even when only one or two paths changed.
The current implementation clones the previous snapshot, hard-links unchanged
regular files and native whiteouts, and replays only the current step's write and
delete paths. Symlinks, directories, metadata, and whiteout semantics continue to
use the existing snapshot helpers. A read-only step therefore reuses the prior
snapshot without replaying any path.

The optimization is deliberately conservative at state boundaries. After commit,
rollback, reset, or session restore, the next snapshot uses the full-copy path;
this avoids depending on a previous snapshot that may have been discarded or
reconstructed. If the changed-path list is unavailable, the full-copy fallback is
also used.

A controlled 64-call profile measured cumulative `snapshot_before` time of
0.384 s before the change and 0.158 s after it (63 incremental calls, about 59%
lower). The end-to-end pair was noisy on the VM: 151.531 ms/step before versus
162.104 ms/step after, with 18.839 ms/step after standard deviation. We therefore
report the snapshot-stage reduction but do not claim a total-latency speedup.
The unit suite (48 tests) and evidence suite both pass.

## Next optimization candidates

The source history now supports a clean before/after experiment for the remaining
higher-risk changes: journaled ledger persistence and snapshot/WAL traversal
reductions. Those changes must preserve
rollback, crash recovery, and the full-trace causal oracle.

Artifacts:

- `src/agenttx/optimization_history/README.md`
- `src/agenttx/optimization_history/iteration_00_unoptimized/`
- `src/agenttx/optimization_history/iteration_01_known_write_trace_bypass/`
- `src/agenttx/optimization_history/iteration_02_known_read_effect_bypass/`
- `src/agenttx/optimization_history/iteration_03_persistent_command_script/`
- `src/agenttx/optimization_history/iteration_04_deferred_blob_gc/`
- `src/agenttx/optimization_history/iteration_05_direct_executable_script/`
- `src/agenttx/optimization_history/iteration_06_persistent_try_worker/`
- `experiments/results/long_workload_matrix.{csv,json,md}`
- `experiments/results/optimization_iterations.{csv,json,md}`

## 待评估方案：SaSh 辅助故障定位（仅离线原型）

记录日期：2026-10-30。采用 *Ahead-of-Time Analysis of Shell Program Effects*
的第二种应用方式：用静态 shell 效果风险提示调整故障候选的探测顺序。
目标是减少定位阶段的无效 verifier/counterfactual 探测；是否降低总成本仍待验证。
本方案不是已部署的性能优化，不改变当前冻结实现或正在执行的实验。

参考：[论文](https://doi.org/10.1145/3830418.3843861)、
[SaSh 源码](https://github.com/atlas-brown/sash)。

### 技术分解与正确性边界

1. 从已结束轨迹导出故障发生时的 ledger、失败 verifier 观测及步骤原始 argv，
   使用冻结的 `FaultLocalizer` 生成原候选（原型默认最多 8 个）。
2. 在独立进程中用 SaSh 静态分析候选的命令文本，保留报警条件、源码行号、
   原始报告与分析耗时。仅接纳命令正文 SHA-256 匹配且行号有效的效果风险报警。
3. 对原候选做稳定排序：有有效风险提示的候选优先，两组内部均保留原顺序。
   候选集合、root IDs、原始分数、rollback targets 和动态依赖图保持不变。
4. 在线接入若经后续验证，仍须由现有 verifier/counterfactual 验证候选后才能恢复。
   静态报警是有条件的线索，不能直接作为故障事实或动态依赖边；无报警也不证明安全。

原型不执行输入的 shell 命令、verifier 或恢复操作。直接调用的 Python、pytest、
make、git 等不透明程序跳过；shell 内嵌不透明调用、Bash 扩展及环境缺失仍是覆盖限制。
无提示、不支持、解析失败、报告非法、分析器不可用、崩溃或超时，均保留原排序。
默认每个分析进程软预算 3 秒，外层再给最多 2 秒清理余量，硬超时终止进程组。
临时目录与清理过的环境只隔离工作产物，不构成内核级沙箱。
重排不能找回已被原候选上限截断的真根，也尚未覆盖多根探测策略。

### 已有离线证据及限制

独立原型位于 `/home/bfq/agenttx-workspace/agenttx-sash-localization`，依赖和冻结模块副本均在该目录；
未接入主项目 runtime、runner 或实验批次。SaSh 固定版本为
`5d66448e791f01f568e111277009c84c5e1b4b5e`，来源和模块哈希在原型 manifest 中记录。
2026-10-20 的验证结果如下，记录本方案时未重新运行实验：

| 手工机制样例 | 原始精确根排名 | 辅助排名 | SaSh 分析墙钟 |
| --- | ---: | ---: | ---: |
| `mv a target; mv b target` 条件性覆盖风险 | 2 | 1 | 约 0.532 秒 |
| 不透明 Python 内部错误 | 2 | 2 | 0 秒（跳过） |

覆盖样例的真实 SaSh 输出为第二行 `data_loss`，条件为未知路径被假定为普通文件，
并未核验真实目标状态。13 项单元测试通过，覆盖候选与回滚目标保持、失败回退、
提示绑定和超时清理等边界。两个样例均为手工构造，实际探测次数为 0、未执行恢复；
排名变化不能换算成已实现的探测节省、真实任务成功率或恢复时间收益。
本机证据见原型的 `README.md`、`REPORT.md` 及 `artifacts/`，这些材料尚未随主仓库收录。

### 后续评估与接入条件

1. **真实场景离线评估：** 按预登记顺序采集已结束任务的故障时刻快照，
   不使用恢复后的 ledger 冒充失败现场；保留无报警、不支持、超时和误报警样本。
   真根标签只供事后评分，不能进入排序输入。
2. **技术分解：** 在相同原候选集合上比较原排序与风险优先排序，报告真根覆盖率、
   精确根排名、top-k、排名改善与恶化、分析覆盖率及失败比例。
3. **正确性测试：** 检查输出是原候选的排列，逐候选核对回滚目标不变；
   在独立 session 副本中运行相同 verifier/counterfactual，核对恢复闭包和有效工作保留。
   排列不变量只能支持恢复输入不被改写，不能证明新排序在有限预算下不降低定位成功率。
4. **Other Overheads：** 保持任务、故障计划、候选预算和最大探测次数一致，比较实际
   探测数、定位成功率及“静态分析 + 探测”总墙钟；另计 session 复制、分析器启动、
   CPU/内存与超时清理成本，并报告不支持或误导排序时的额外开销。

在真实离线证据支持收益前，保持独立原型，不添加在线 hook、不安装依赖到主项目、
不修改当前实验协议或重启在途任务。未来接入应另做受控实验，不能混入当前批次结果。
