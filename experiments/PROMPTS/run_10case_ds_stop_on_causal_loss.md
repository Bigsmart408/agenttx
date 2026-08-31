# AgentTX：10 case live 对照；causal 差于 temporal 就分析、改、重测该题

把下面整段当作系统/任务 prompt 用。源码和实验只在远程 x86 上动，不要在 Windows 本地仓库改。

---

你在远程机器 `ssh x86`（跳板 `tiao2`，用户 `pengpeng`）上跑 AgentTX。仓库：`/home/pengpeng/agenttx`。Python：`/home/pengpeng/miniconda3/envs/agenttx/bin/python`（conda env `agenttx`，3.11）。密钥：`/home/pengpeng/.agenttx_llm.env`（mode 600），不要打印 key。不要 git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"，除非用户明确要求。作者若提交：`bifangqi <bifangqi@example.com>`，不要加 Cursor co-author。

## 不许做的事

- 不要 `git clone` / `git fetch` GitHub。SWE-Bench Lite 的 12 个上游和 300 道工作副本已经在 `experiments/cache/swe_bench/`。Terminal-Bench 在 `experiments/cache/terminal_bench/terminal-bench-1/original-tasks/`。缺本地树就报错停，不要去网上拉。
- 不要 OpenRouter。DeepSeek 用原生 `https://api.deepseek.com`。
- DeepSeek Harness **不要走 clash**。`src/agenttx/agents/external.py` 里 DSH 必须直连；启动时带 `AGENTTX_NO_PROXY=1`。`agentTX-clash` 只给 Codex/GPT（`gpt-5.6-luna`）用。
- 不要并行开 DS + GPT。两路会抢 `try` overlay，出现 `try overlay backend is unavailable` 或 `/tmp/agenttx-cmd-*/cmd.sh` 丢失，格子作废。
- 不要拉 SWE 官方 Docker 评测镜像。`AGENTTX_SWE_VERIFY=host`。
- `prompt_tokens` 是每一轮 input 的累加（uncached + cache_read + cache_write），不是最后一轮 context 大小。对比 recovery 成本用 `doc_replay_tokens`，不要拿整段 session `total_tokens` 当 recovery 代价。causal 的 replay 应为 0；temporal/whole 通常有几万 replay。官方 session prompt 往往远大于 replay。

## 这次要跑什么

只跑 DeepSeek：`--harness deepseek_harness --model deepseek-v4-flash --provider deepseek`。

10 道题（本地已有），每种策略跑一遍，`--repeats 1`，`--harness-timeout 1800`。按题顺序，一题过关再下一题：

Terminal-Bench：
- cancel-async-tasks
- llm-inference-batching-scheduler
- organization-json-generator
- recover-accuracy-log
- cross-entropy-method

SWE-Bench Lite：
- django__django-10914
- django__django-10924
- django__django-11039
- pallets__flask-4992
- pylint-dev__pylint-5859

每种策略：`causal` 和 `temporal_checkpoint` 必须成对比较。`whole_branch_abort` 可作辅证，但停机/改代码的判据只看 **causal vs temporal**。

结果目录用新的空目录，例如 `experiments/results/crash_vs_checkpoint_local10_ds/`。每次改代码后重测同一题，用带迭代号的 subdir 或同一 CSV 里新的 repeat，**不要把失败格和修好后的格混成一行**。旧 CSV 的 `skip existing` 会跳过同 key，重测前换目录或改 skip key。

启动示例（在 `/home/pengpeng/agenttx`）：

```bash
export PATH="/home/pengpeng/.local/bin:$PATH"
export AGENTTX_PYTHON=/home/pengpeng/miniconda3/envs/agenttx/bin/python
export PYTHONPATH=src:.
export AGENTTX_SWE_VERIFY=host
export AGENTTX_NO_PROXY=1
export GIT_TERMINAL_PROMPT=0
export HF_HUB_OFFLINE=1
set -a
. /home/pengpeng/.agenttx_llm.env
set +a

# 只跑当前这道题的 causal + temporal，便于失败后立刻对照
nohup env AGENTTX_NO_PROXY=1 "$AGENTTX_PYTHON" -u experiments/scripts/bench_official_tasks.py \
  --task-set selected \
  --tasks cancel-async-tasks \
  --modes causal temporal_checkpoint \
  --repeats 1 \
  --harness-timeout 1800 \
  --harness deepseek_harness \
  --model deepseek-v4-flash \
  --provider deepseek \
  --result-subdir crash_vs_checkpoint_fixloop_cancel \
  > experiments/results/crash_vs_checkpoint_fixloop_cancel/run.log 2>&1 &
```

PowerShell 连 x86 时用 SSH_ASKPASS，清掉本机 HTTP(S)_PROXY 再 ssh，远程命令用 base64 bash heredoc。

## 格子算不算跑成

看 CSV：`experiments/results/<subdir>/official_tasks_raw.csv`。

- `prompt_tokens == 0` 且墙钟只有几秒：没进 agent。常见原因是 overlay 探测失败。这种格子 **不能** 用来比较策略。
- agent 跑完后 `error` 里有 `cmd.sh` 丢失：verify 崩了，测试结果不可信，先修 runtime 再比策略。
- 可信对比至少要：`tests_ok`、`independent_retained`、`doc_replay_tokens`、session `prompt_tokens`。
- 单格 live 跑几分钟到十几分钟正常（`cancel-async-tasks` causal 大约 7–11 分钟、上百万 prompt）。超过 30 分钟还没写 CSV、DSH/node 无 CPU 无套接字，才当卡死。

## 何谓 causal 比 temporal 差 / 好

同一题、两格都是「真正跑了 agent」的前提下：

**差（必须进入分析-修改-重测循环）**，任一即可：

1. causal 的 `tests_ok` 为 false，temporal 为 true。
2. causal 的 `independent_retained` 为 false，temporal 为 true。
3. 正确性和留档都不更好，但 causal 的 session `prompt_tokens` ≥ temporal 的 **2 倍**（不要把 replay 加进 causal 的代价；causal 贵就贵在 session prompt）。

**好（可以进入下一题）**，必须同时：

- `tests_ok` 不差于 temporal（true≥true，或都 false）。
- `independent_retained` 不差于 temporal。
- 在上述打平的前提下，causal session `prompt_tokens` **低于** temporal 的 2 倍。更理想：明显低于 temporal（含或不含 temporal 的 replay）。causal 的 `doc_replay_tokens` 应为 0。

若两格都是基础设施失败，不算「差」，先修 overlay/`cmd.sh`，再重跑该题两格。

## 主循环（必须执行）

对 10 道题按顺序：

1. **只跑这一题** 的 causal 和 temporal（不要一上来把 10 题全丢进一个 nohup 里无人值守）。等两格都落盘。
2. 若 causal **不差于** temporal：记下数字，进入下一题。
3. 若 causal **差于** temporal：
   1. **停掉** 当前 bench 和 `dsh --profile headless`（不要动 vscode Codex / mihomo）。
   2. **分析原因**，写给用户，必须落到具体机制，不要空话。对照要看：
      - 官方 session prompt 为何膨胀（DSH 是否在列 `.dsh/node_modules`、是否反复打开 `recovery_notes/` 尽管 REM 已标 COMPLETE-PROTECTED、是否忽略 state certificate）。
      - rewind/REM 是否把不该回的会话或文件回掉，导致 agent 重做。
      - ledger parents 是否没并进 conversation span。
      - temporal 的 replay 成本和 causal 的 session 成本分别是多少。
      - verifier / `cmd.sh` / overlay 是否污染了结果。
   3. **给出并落地修改**（改 `src/`、recovery inject、harness adapter 等真正会被 live 跑用到的代码）。改完跑相关单测。
   4. **只重测这一题** 的 causal + temporal，换新的 `--result-subdir`（例如 `..._fixloop_<task>_r2`）。
   5. 重复 3.1–3.4，直到该题 causal 按上面的定义好于 temporal，或连续 3 轮修改仍无法翻转——停下来把分析和未解决点交给用户，不要偷偷开下一题。
4. 杀进程时清 `/tmp/agenttx-tb-*`、`/tmp/agenttx-swe-*`、`/tmp/agenttx-try-probe-*`、`/tmp/agenttx-cmd-*`。不要删 `experiments/cache/swe_bench/`。

## 已观察到的信号（上一轮，不要当新实验）

`crash_vs_checkpoint_local10` 里 DeepSeek 在 `cancel-async-tasks` 上：

- causal：tests_ok=False，retained=True，prompt=1,501,586，replay=0，wall=655s
- temporal：tests_ok=False，retained=True，prompt=239,854，replay=60,252，wall=326s

正确性和留档打平，causal session prompt 大约是 temporal 的 6 倍，已经触发第 3 条。下一轮应从这题开始：分析为何 causal 官方 session 比 temporal 贵一个数量级，改代码，只重跑 `cancel-async-tasks` 的 causal vs temporal，直到翻转。
