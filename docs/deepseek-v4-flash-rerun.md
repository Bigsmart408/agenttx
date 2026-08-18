# DeepSeek v4 Flash 实验重跑记录

本轮将所有需要真实大模型调用的实验统一切换为 `deepseek-v4-flash`。旧的
`deepseek-chat` 汇总文件没有与新数据合并，而是移动到
`experiments/results/legacy_deepseek_chat_20260817/`，仅作为历史归档。

## 运行边界

- provider：`deepseek`
- model：`deepseek-v4-flash`
- 文件系统跟踪：`strace`
- 每个 GitHub 任务/恢复策略组合：1 次；token-recovery 三种规模各 3 次
- 任务环境跳过主机上与工作区无关的 `/data/cubelet.img` 挂载。该镜像约 20 GB，跳过它只避免无关的 namespace 初始化复制，不改变工作区文件、effect ledger 或回滚逻辑。

## GitHub-context 短/中/长任务

脚本：`experiments/scripts/bench_github_token_tasks.py`。任务分别来自
`psf/requests`、`pallets/flask` 和 `pytest-dev/pytest` 的 pinned snapshot，
恢复目录与仓库源码分离。`total_tokens` 是失败诊断之后的自主恢复 token；
`success=false` 表示该粗粒度策略没有同时满足测试和独立工作保留条件，不能
把它解释成 AgentTX 正确性失败。

| scale | policy | total tokens | success | independent retained |
|---|---|---:|:---:|:---:|
| short | causal | 47,977 | yes | yes |
| short | temporal checkpoint | 28,209 | yes | yes |
| short | whole branch abort | 100,939 | no | no |
| medium | causal | 41,393 | yes | yes |
| medium | temporal checkpoint | 61,313 | no | yes |
| medium | whole branch abort | 68,449 | no | no |
| long | causal | 41,183 | yes | yes |
| long | temporal checkpoint | 69,643 | no | yes |
| long | whole branch abort | 65,505 | no | no |

原始结果：`experiments/results/deepseek/github_token_tasks_raw.csv`；汇总：
`experiments/results/deepseek/github_token_tasks.{json,md}`。

## 隔离 token-recovery 重跑

脚本：`experiments/scripts/bench_token_recovery.py`。每个规模分别使用
12、24、48 lines/doc，三次重复；causal 不需要 replay，checkpoint 需要重放
一个文档，whole-branch 需要重放两个文档。

| lines/doc | causal | temporal checkpoint | whole branch abort |
|---:|---:|---:|---:|
| 12 | 0 | 864.3 | 1,797.3 |
| 24 | 0 | 1,060.3 | 2,231.7 |
| 48 | 0 | 1,424.7 | 3,340.3 |

每个单元均 `success=1`、`tests_rc=0`、无 host leak。文件位于
`experiments/results/deepseek/token_recovery_v4_{12,24,48}.{csv,json}`，
三种规模的汇总 CSV 为 `token_recovery_v4_summary.csv`。

## 真实 Agent 恢复与健壮性

`bench_real_agent_recovery.py --repeats 3 --max-turns 30` 的 3/3 次成功，
均正确选择故障根、完成因果回滚、保留独立工作并通过测试；wall time 为
44.60/40.58/42.94 s。`bench_real_agent.py --repeats 3 --max-turns 35` 的
健壮性 3/3 次成功，wall time 为 18.68/16.33/16.56 s，均无 host pollution。

结果位于 `experiments/results/deepseek/real_agent_recovery.{csv,json,md}`
和 `real_agent_robustness.{csv,json,md}`。

## 可复现命令

所有命令都显式传入 `--model deepseek-v4-flash --trace-backend strace`，并在
远程 x86 环境加载 `~/.agenttx_llm.env`。重新运行时不要读取
`legacy_deepseek_chat_20260817` 中的历史行。
