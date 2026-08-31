# 不停迭代，直到 10 题全部 tests_ok

你是 /home/pengpeng/agenttx 上的唯一操作员。立刻干活。不要等人。不要写完总结就退出。不要跳题。

## 成功基线

一格成功 = `tests_ok=True`。官方 verifier 过了，题才算做对。

这些不是成功门槛，只记旁边：
- `independent_retained`、`derived_removed`、CSV 复合字段 `success`
- token 倍数、wall、replay

不成功：
- `tests_ok=False`：题没做对。token 再低、文档还在，也不算过。
- `prompt_tokens=0` / overlay / `cmd.sh` 崩了：格子无效，先修 runtime，不能拿来比策略。
- 旧行里 `cancel-async-tasks` 两边都是 False（含 302k/241k 那对）：第 1 题没做成，必须重做。

`prompt_tokens` 是每轮 input 累加。recovery 成本看 `doc_replay_tokens`。causal 的 replay 应为 0。

## 一题怎样才算解决

该题 causal 有效格 `tests_ok=True`。这是放行下一题的唯一条件。

同一题要有一对有效 causal vs temporal。causal 做成之后，若 temporal 也做成且 causal 的 session `prompt_tokens` 更高，再改一轮代码重测；仍更高就记进 ANALYSIS.md 后进入下一题。不要因为 token 比不过而卡死整场。

禁止：
- 测试没过就进入下一题
- 连续 N 轮失败就放弃该题
- 写完 ANALYSIS 当整场结束
- 一条命令把 10 题全丢进后台
- 并行第二道题或并行 GPT

## 结束条件（只有这些）

1. 下面 10 题每一题 causal 都 `tests_ok=True`。
2. 人类在 `experiments/results/codex_operator_testsok/STOP` 放下停止文件。

没有「3 轮失败就跳题」。没有「太难就下班」。本题没过就继续改、继续测本题。

活着的 DSH（有 DeepSeek 套接字 / session 在涨 / CPU 非零）等到 `--harness-timeout 1800`。连续 15 分钟无套接字、无 CPU、session 不涨、无 CSV 才当卡死：杀掉、分析、修、重测该题。

## 10 题按序

TB: cancel-async-tasks, llm-inference-batching-scheduler, organization-json-generator, recover-accuracy-log, cross-entropy-method
SWE: django__django-10914, django__django-10924, django__django-11039, pallets__flask-4992, pylint-dev__pylint-5859

只跑 DeepSeek：`--harness deepseek_harness --model deepseek-v4-flash --provider deepseek`。`AGENTTX_NO_PROXY=1`。不要 clash 包 DSH。不要拉 GitHub / HF / SWE Docker。`AGENTTX_SWE_VERIFY=host`。
Python：`/home/pengpeng/miniconda3/envs/agenttx/bin/python`。密钥：`/home/pengpeng/.agenttx_llm.env`，不要打印。不要做任何版本库提交。

已有改动：`src/agenttx/agents/external.py` 里 profile 用 `rsync --exclude=node_modules`。可保留。

## 主循环

对当前未解决的第一题：
1. 新的 `--result-subdir`：`crash_vs_checkpoint_testsok_<task>_rN`，先 causal 后 temporal，setsid 启动，等两格 CSV。
2. 无效格：修 overlay / cmd.sh / timeout，重跑这一对。
3. causal `tests_ok` 没过：停 bench，分析机制（DSH 扫目录、反复读 recovery_notes、rewind/REM、ledger、提示是否让模型去做题），改 `src/` 或 recovery inject，跑单测，换目录重测这一题。
4. causal `tests_ok=True`：立刻下一题。
5. 若本会话被续跑：先读 `experiments/results/codex_operator_testsok/progress.txt`，从未解决的第一题接着干。

现在立刻从第一道未解决的题开始。
