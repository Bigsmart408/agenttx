# SWE-agent 与 Terminal-Bench 差异分析及当前进度

核对时间：2026-10-18 15:01（北京时间）。只讨论本机证据；历史批次和新批次分开。

## 已确认的差异来源

### 1. 当前两个 harness 的 token 采集口径不同

Codex 历史行 usage_source=stdout_jsonl，从 CLI usage 事件取数。当前 SWE-agent 行 usage_source=sweagent_model_stats；固定依赖 models.py 的 _query() 用 token_counter 对消息及可见输出估算，未直接把 response.usage 记入 model_stats。AgentTX finish() 再读取 tokens_sent/tokens_received。可见文本估算不完整覆盖 API reasoning。加上 doc_replay_tokens 解决的是重放会话漏项，不能自动解决会话内部 reasoning 漏计。

本轮新增 audit_swe_token_usage.py，从 worker.log 的 API Usage 记录读取 prompt_tokens + completion_tokens，主会话和重放一起计入；reasoning 已包含在 completion，不能重复加。112/112 个已完成单元的日志响应数与 main model_calls + replay tool_calls 一致，且各响应 prompt + completion = total。此为日志复算，尚未与服务端账单独立核账；不改写实验原始字段，也不改变运行中的模型或提示词。历史 SWE-agent 未完成同等级 API 复算，不能把旧记录比例直接解释为完整账单差异。

### 2. 即使匹配题目，历史 harness 差距仍在

下面每行限定相同 SWE 任务，且 Codex causal/对照、SWE-agent causal/对照四个单元都成功；均含各自记录的 replay。使用历史原口径，仅作描述。

| 对照 | 四方成功同题数 | Codex 节省 | SWE-agent 节省 |
| --- | ---: | ---: | ---: |
| 不回滚 | 166 | 67.99% | 54.71% |
| 时间 checkpoint | 180 | 44.40% | 6.53% |
| 整分支 | 169 | 39.87% | 15.52% |

这排除了“只因两批完成题目不同”的完整解释，但仍混有模型使用模式、提示词、上下文处理、统计口径及历史环境/代码的差异；无法由此单独归因于恢复机制。

### 3. 重放节省与后续求解开销必须拆开

新批最初 17 对成功的 checkpoint 比较：Causal 主执行 1,563,169；checkpoint 主执行 1,459,441，另有 replay 79,862。Causal 避免了 replay，但主会话多花 103,728，抵消后还多 23,866（文本估算口径）。因此“保留更多有效工作”不保证一次随机模型求解的总 token 必然更低。

当前未使用 gold 或已完成参考解作为故障前前缀；本轮明确保留的独立工作主要是注入轨迹中的恢复笔记，并不能解释成已证明保留真实多轮解题中的全部有效进度。

### 4. 当前差距有具体的搜索轨迹证据

pydata__xarray-3364：Causal 23 次模型调用，checkpoint 14 次；主会话轨迹工具观察总字符分别 67,243 与 37,723。Causal 阅读源码更多，另有 rg/apply_patch 等命令不可用的尝试；这些额外步骤进入后续消息历史，累积输入开销。

按原记录含 replay，Causal 269,178、checkpoint 109,725，多花 159,453。按日志 API 含 replay，分别 288,449 与 119,037，多花 169,412。此题两组均成功，未观测到真正的验收基础设施失败。它说明主求解路径波动能够盖过重放收益；不能从一题断言 compact 是原因。

### 5. TB 的长尾和环境边界进一步混入差异

Codex/TB 与整分支配对的 179 个完成任务，Causal 在 117 个更省，逐任务节省率中位数 21.00%，但总 token 仅省 2.65%。双方成功子集 81 题中，fix-ocaml-gc 为 Causal 7,819,436、整分支 1,749,004，单题差 6,070,432；其他高值如 download-youtube、spinning-up-rl 也会放大总成本波动。不能因结果不利删除它们。

TB 历史批次为主机执行 agent、镜像验收，且基础设施错误/超时较多；它不具备当前要求的 agent 与 verifier 同任务镜像边界。因此已有长尾是事实，是否具体由网络、依赖或重复修复引起仍需逐条完整轨迹证明。部分历史行只保留 2000 字符 stdout 尾部，不能据此伪称已经查清完整调用循环。当前没有正在跑的 SWE-agent/TB 全量模型批次。

## 当前 SWE-agent / SWE-bench 全量

活动批次：sweagent-swe-local-monitored-20261018-selective-full-r2；300 题 × 6 策略 = 1800 单元。15:01 快照：112 完成、104 成功、8 任务失败、8 运行、1680 待跑，无活动基础设施错误，stop_reason=null。

14:25 前一段在 103 完成 + 1 基础设施分类单元后停止并排空。审计发现 django__django-10924/whole_branch_abort 的模型两次把注册镜像 SHA 加了第 65 个字符；正确的 64 位镜像可 inspect，最终固定镜像验收通过。已证明为模型命令错误。调度器仅对这类实际轨迹证明的额外字符错误放行，真实 Docker/验收错误仍停止新派发；测试 24 项通过。

14:56 续跑，按引用继承全部 104 个结果；上述单元在续跑审计中恢复为 completed，保留旧分类、证据哈希及原结果路径。没有重跑失败任务，没有改原数据。冻结执行核心与 r1 相同，仅调度器分类修复。8 路并行与半小时巡检已恢复。

| 策略 | 完成 | 成功 | API 日志 token（主执行+replay） |
| --- | ---: | ---: | ---: |
| causal | 19 | 18 | 1,786,219 |
| ideal_oracle | 19 | 18 | 1,735,838 |
| clean_recovery | 19 | 17 | 2,446,428 |
| crash_no_rollback | 18 | 18 | 4,025,616 |
| temporal_checkpoint | 19 | 18 | 1,857,810 |
| whole_branch_abort | 18 | 15 | 2,262,642 |

仅同题双方成功、使用 API 日志总用量的配对比较：

| 对照 | 配对数 | Causal 总 token 节省 | 逐题节省率中位数 | Causal 更省题数 |
| --- | ---: | ---: | ---: | ---: |
| crash_no_rollback | 18 | 57.36% | 54.22% | 17 |
| temporal_checkpoint | 18 | 1.44% | 14.02% | 13 |
| whole_branch_abort | 15 | 28.70% | 33.78% | 13 |
| ideal_oracle | 18 | -5.06% | -1.34% | 9 |
| clean_recovery | 16 | 29.97% | 10.86% | 9 |

当前只有约 6% 单元完成；完整成功率还不能按 300 题解释。clean 是无故障参照，ideal 是精确恢复参照，小样本结果不证明超越上限。Causal/ideal/clean=compact，其余=full；没有额外的全成功放行门槛。正式统计仍保留 formal_eligible=false 登记，不把诊断全量直接算作论文最终覆盖。

## 复核入口

- 历史：results/codex_luna_max_realworld_5mode_full_20260930/merged/official_tasks.json 和 results/sweagent_luna_max_swe_fair_20261012/merged/official_tasks.json（均在数据盘 runtime 下）。
- 活动批次指针：/mnt/data/agenttx-runtime/validation/active-swe-full-cohort.json。
- API 日志逐单元报告：/mnt/data/agenttx-runtime/validation/swe-api-usage-sweagent-swe-local-monitored-20261018-selective-full-r2.json。
- 模型命令错误审计：/mnt/data/agenttx-runtime/validation/swe-agent-verifier-command-audit-20261018.json。
- API 审计重算：`/home/bfq/.venvs/agenttx/bin/python experiments/scripts/audit_swe_token_usage.py`。
- 后续分析优先按完整 API 用量拆 main/replay、调用次数和观察体积；保留全部长尾及任务失败，环境错误独立列出。
