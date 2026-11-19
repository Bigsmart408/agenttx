# Codex × SWE-Bench Lite 恢复实验进度

北京时间 2026-11-06 04:44:27；每 60 秒检查，健康汇总每 30 分钟保存。

批次：`codex-swe-full-recovery-20261104-r1`。300 题 × 6 策略；最多 8 路。

监控状态：`finished_with_infrastructure_errors`；协调器服务：`False`。

| 策略 | 已完成 | 成功 | 基础设施异常 | 运行中 | 待运行 |
|---|---:|---:|---:|---:|---:|---:|
| causal | 297 | 288 | 3 | 0 | 0 |
| crash_no_rollback | 282 | 239 | 18 | 0 | 0 |
| temporal_checkpoint | 296 | 290 | 4 | 0 | 0 |
| whole_branch_abort | 294 | 282 | 6 | 0 | 0 |
| clean_recovery | 293 | 286 | 7 | 0 | 0 |
| ideal_oracle | 296 | 286 | 4 | 0 | 0 |

停止原因：`None`。

告警：无新增运行告警。

已有结果与基础设施异常保留，不按成功率或 token 收益重跑。
执行器派发后响应丢失的异常用量不完整，日志中的 0 token 不视为实际零成本。
已知执行器异常仅在零模型环境检查通过后继续待运行单元；未知异常停止派发并持续记录。
`formal_eligible=false`；成功率和 token 比较须另做完整实验审计。

机器可读状态：`/mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1/monitor/latest.json`。
