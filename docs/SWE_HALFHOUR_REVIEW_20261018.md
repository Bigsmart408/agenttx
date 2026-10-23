# SWE-agent 每半小时巡检

核验时间：2026-10-23 19:00:11（北京时间）；当前批次 `sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`。

状态：`{'completed': 1799, 'infrastructure_error': 1}`；新增终止尝试：0；主进程存活：False。

评估：`infrastructure_attention_required`；基础设施告警：`['infrastructure_errors:1']`；数据盘剩余 1842.6 GiB。

## Causal 对本批对照组

| 对照组 | 双方成功配对 | Causal token 减少 | Causal 更省题数 |
| --- | ---: | ---: | ---: |
| clean_recovery | 264 | 8.0% | 138 |
| crash_no_rollback | 275 | 59.9% | 253 |
| temporal_checkpoint | 278 | 17.7% | 188 |
| whole_branch_abort | 239 | 23.5% | 184 |
| ideal_oracle | 282 | 1.9% | 134 |

以上 token 为 SWE-agent model_stats 的主会话及文档重放文本估算，未完整计入 API 推理用量；完整日志用量使用 audit_swe_token_usage.py 单独核对。只在同题且双方成功时比较成本；成功率和来源批次见每分钟报告。少量题目不能证明稳定优势或显著性。

## 历史对照巡检阈值

每策略至少 20 个历史有效配对后，成功率低于历史超过 10 个百分点标记复核；至少 10 个双方成功的成本配对后，token 比超过 1.25 标记复核。样本不足时不下效果结论。这些是巡检阈值，不是显著性检验。

效果差异只触发报告，不据此停跑、选择任务或自动重试。基础设施错误由实验调度器停止新增任务，保留现有尝试。历史时限、崩溃种子及代码不同，不能将配对差值视为严格复现证据。

巡检由本机 systemd 用户定时器运行，每逢整点和半点执行；快照写入数据盘，不主动发送聊天消息。

[每分钟配对报告](SWE_FULL_COMPARISON_20261018.md)

完整巡检：`/mnt/data/agenttx-runtime/validation/swe-full-halfhour-20261018/latest.json`。
