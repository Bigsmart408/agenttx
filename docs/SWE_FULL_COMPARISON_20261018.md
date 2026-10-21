# SWE-agent 全量与历史对照

更新时间：2026-10-21 09:44:00（北京时间）；每 60 秒刷新。

本机 r760-ssd，300 题 × 6 策略 = 1800 单元，最多 8 路并行。代码冻结于系统盘；运行数据在 /mnt/data/agenttx-runtime。

当前批次：`sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`；审计后状态：`{'completed': 1799, 'infrastructure_error': 1}`（原始：`{'completed': 1799, 'infrastructure_error': 1}`）；停止原因：`None`；已结束：True。

实际结果上下文：`{'compact': 899, 'full': 900, 'unknown': 1}`；逐策略登记：`{'clean_recovery': 'compact', 'crash_no_rollback': 'full', 'temporal_checkpoint': 'full', 'whole_branch_abort': 'full', 'causal': 'compact', 'ideal_oracle': 'compact'}`。selective-compact-v1 比较 Causal+compact 与 full 对照的整体效果。r1/r2 漏传环境变量导致登记 compact、实际 full；依据实际结果解释。clean 是无故障参照上限，ideal 是精确恢复参照；不得把小样本总 token 更低解读为超过上限。

此监控的 token 是 SWE-agent model_stats 文本估算：主会话 total_tokens + 单列 doc_replay_tokens，不能视为完整 API 用量，推理 token 未完整计入。完整日志用量另用 audit_swe_token_usage.py 核对；见 HARNESS_TB_DIFFERENCE_ANALYSIS_20261018.md。环境无效单元单列；续跑单元注明来源批次，原始记录保持不变。

## Causal 与本批对照组

| 对照组 | 有效结束配对 | Causal / 对照成功数 | 双方成功配对 | Causal token 减少 | Causal 更省的题数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| clean_recovery | 299 | 284 / 273 | 264 | 8.0% | 138 |
| crash_no_rollback | 300 | 285 / 282 | 275 | 59.9% | 253 |
| temporal_checkpoint | 300 | 285 / 284 | 278 | 17.7% | 188 |
| whole_branch_abort | 300 | 285 / 243 | 239 | 23.5% | 184 |
| ideal_oracle | 300 | 285 / 289 | 282 | 1.9% | 134 |

同题、同策略条件及相同崩溃种子配对。成本只比较双方成功的单元，成功率同时列出；逐题倍率中位数保存在 JSON。样本量小时不宣称统计显著；跨修复批次的来源在 JSON 中保留。

## 历史诊断对照

| 策略 | 已尝试 | 有效结束 | 成功 | 环境错误 | 历史有效配对数 | 成功率差（百分点） | 双方成功 token 比（新/旧） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| causal | 300 | 300 | 285 | 0 | 215 | 5.58 | 0.506 (n=191) |
| clean_recovery | 300 | 299 | 273 | 1 | 278 | 13.67 | 0.599 (n=201) |
| crash_no_rollback | 300 | 300 | 282 | 0 | 276 | 9.06 | 0.647 (n=225) |
| ideal_oracle | 300 | 300 | 289 | 0 | 0 | — | — |
| temporal_checkpoint | 300 | 300 | 284 | 0 | 253 | 8.7 | 0.556 (n=214) |
| whole_branch_abort | 300 | 300 | 243 | 0 | 215 | -3.26 | 0.560 (n=155) |

历史总体状态：`{"completed_success": 1053, "completed_task_failure": 184, "terminal_timeout": 5, "environment_error": 175, "unfinished_or_error": 12, "never_attempted": 71}`。历史原始指标可用 1233 单元；缺少原始指标的任务仍保留其审计状态。

配对键为相同任务、相同策略、repeat=0。成功率配对包含双方有效的成功及任务失败；token 比仅统计双方成功且都有用量的配对。环境错误单列，不把未运行计为任务失败。

本批沿用已验证的 900 秒会话时限和种子 20261018；历史批次为 1800 秒和种子 20260930，代码与环境也不同。因此差值用于历史诊断，不能直接证明严格复现或策略因果优势。硬 token 预算、策略 payload 配平与双框架统一验证尚待完成，formal_eligible=false；全量运行与最终论文合规批次分开登记。

遇基础设施错误停止添加新单元、保留运行中的任务和原始证据；不按任务成败自动重跑。

TB：首轮已正常结束，计入独立修复后去重共 219/241，尚余 22 项环境错误。

执行登记：`/mnt/data/agenttx-runtime/results/sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z/registration.json`。完整对照：`/mnt/data/agenttx-runtime/results/sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z/historical-comparison.json`。
