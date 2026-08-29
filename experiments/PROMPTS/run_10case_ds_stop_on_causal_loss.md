# AgentTX：10 case live 对照，causal 变差就停

把下面整段当作系统/任务 prompt 用。源码和实验只在远程 x86 上动，不要在 Windows 本地仓库改。

---

你在远程机器 `ssh x86`（跳板 `tiao2`，用户 `pengpeng`）上跑 AgentTX。仓库：`/home/pengpeng/agenttx`。Python：`/home/pengpeng/miniconda3/envs/agenttx/bin/python`（conda env `agenttx`，3.11）。密钥：`/home/pengpeng/.agenttx_llm.env`（mode 600），不要打印 key。

## 不许做的事

- 不要 `git clone` / `git fetch` GitHub。SWE-Bench Lite 的 12 个上游和 300 道工作副本已经在 `experiments/cache/swe_bench/`。Terminal-Bench 在 `experiments/cache/terminal_bench/terminal-bench-1/original-tasks/`。缺本地树就报错停，不要去网上拉。
- 不要 OpenRouter。DeepSeek 用原生 `https://api.deepseek.com`。
- DeepSeek Harness **不要走 clash**。`src/agenttx/agents/external.py` 里 DSH 必须直连；启动时带 `AGENTTX_NO_PROXY=1`。`agentTX-clash` 只给 Codex/GPT（`gpt-5.6-luna`）用。
- 不要并行开 DS + GPT。两路会抢 `try` overlay，出现 `try overlay backend is unavailable` 或 `/tmp/agenttx-cmd-*/cmd.sh` 丢失，格子作废。
- 不要拉 SWE 官方 Docker 评测镜像。`AGENTTX_SWE_VERIFY=host`。
- 不要 git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"，除非用户明确要求。作者若提交：`bifangqi <bifangqi@example.com>`，不要加 Cursor co-author。
- `prompt_tokens` 是每一轮 input 的累加（uncached + cache_read + cache_write），不是最后一轮 context 大小。对比 recovery 成本用 `doc_replay_tokens`，不要拿整段 session `total_tokens` 当 recovery 代价。

## 这次要跑什么

只跑 DeepSeek：`--harness deepseek_harness --model deepseek-v4-flash --provider deepseek`。

10 道题（本地已有），每种策略跑一遍，`--repeats 1`，`--harness-timeout 1800`：

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

三种策略，按这个顺序：`causal` → `temporal_checkpoint` → `whole_branch_abort`。

结果目录用新的空目录，例如 `experiments/results/crash_vs_checkpoint_local10_ds/`，不要复用脏 CSV（`skip existing` 会跳过旧行）。

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

nohup env AGENTTX_NO_PROXY=1 "$AGENTTX_PYTHON" -u experiments/scripts/bench_official_tasks.py \
  --task-set selected \
  --tasks \
    cancel-async-tasks \
    llm-inference-batching-scheduler \
    organization-json-generator \
    recover-accuracy-log \
    cross-entropy-method \
    django__django-10914 \
    django__django-10924 \
    django__django-11039 \
    pallets__flask-4992 \
    pylint-dev__pylint-5859 \
  --modes causal temporal_checkpoint whole_branch_abort \
  --repeats 1 \
  --harness-timeout 1800 \
  --harness deepseek_harness \
  --model deepseek-v4-flash \
  --provider deepseek \
  --result-subdir crash_vs_checkpoint_local10_ds \
  > experiments/results/crash_vs_checkpoint_local10_ds/run.log 2>&1 &
```

PowerShell 连 x86 时用 SSH_ASKPASS，清掉本机 HTTP(S)_PROXY 再 ssh，远程命令用 base64 bash heredoc。

## 怎么判断格子算不算跑成

先看 CSV：`experiments/results/<subdir>/official_tasks_raw.csv`。

- `prompt_tokens == 0` 且墙钟只有几秒：没进 agent。常见原因是 overlay 探测失败。这种格子 **不能** 用来比较策略，整道题作废，不要据此判定 causal 好坏。
- agent 跑完后 `error` 里有 `cmd.sh` 丢失：verify 崩了，测试结果不可信。
- 可信对比至少要：`tests_ok`、`independent_retained`、`doc_replay_tokens`、session `prompt_tokens`。causal 的 replay 应为 0；temporal/whole 通常有几万 replay。官方 session prompt 往往远大于 replay。

单格 live 跑几分钟到十几分钟正常（例如 `cancel-async-tasks` causal 大约 7–11 分钟、上百万 prompt）。超过 30 分钟还没写 CSV、DSH/node 无 CPU 无套接字，才当卡死处理。

## 停机条件（必须执行）

目标不是把 10 题跑完，而是尽快看出 causal 相对另两种策略有没有优势。

**按题停，不按格停。** 一道题的 causal / temporal / whole 都落盘（且至少两格是真正跑了 agent 的）之后，立刻比较，不要开下一题。

在同一题上，若出现下面任一情况，杀掉 bench 和 `dsh --profile headless`，保留 CSV，向用户汇报后结束，**不要继续后面的 case**：

1. **正确性更差**：causal 的 `tests_ok` 为 false，而 temporal 或 whole 为 true。
2. **文档没留住**：causal 的 `independent_retained` 为 false，而 temporal 或 whole 为 true。
3. **同样没过/同样过，但明显更贵**：`tests_ok` 和 `independent_retained` 不优于另外两种，但 causal 的 session `prompt_tokens` 达到 cheaper 那一格的 **2 倍及以上**（不要把 replay 和 session 加在一起再当 causal 的代价；causal 贵就贵在 session prompt）。
4. **连续两道题** overlay/`cmd.sh` 基础设施失败，无法形成有效三策略对照——停下来修 runtime，不要空转。

若因果在该题上至少一项明显更好（测试过了而别人没过，或文档留下而别人没留下，或同样结果但 session prompt 明显更低），继续下一题。

杀进程时不要动 vscode Codex、不要动 mihomo（除非用户要求）。清 `/tmp/agenttx-tb-*`、`/tmp/agenttx-swe-*`、`/tmp/agenttx-try-probe-*`、`/tmp/agenttx-cmd-*`。不要删 `experiments/cache/swe_bench/`。

## 已观察到的信号（供对照，不要当新实验）

`crash_vs_checkpoint_local10` 里 DeepSeek 只跑完 `cancel-async-tasks` 的两格就停了：

- causal：tests_ok=False，retained=True，prompt=1,501,586，replay=0，wall=655s
- temporal：tests_ok=False，retained=True，prompt=239,854，replay=60,252，wall=326s

正确性和留档打平，causal session prompt 大约是 temporal 的 6 倍。按上面第 3 条，这已经构成「causal 效果差，停」。新 run 用空目录，不要接着这份脏结果混。
