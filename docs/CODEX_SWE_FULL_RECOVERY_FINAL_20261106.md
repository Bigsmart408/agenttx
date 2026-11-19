# Codex × SWE-Bench Lite 恢复实验收尾记录

北京时间 2026-11-06 04:44:07，300 题 × 6 策略，共 1800 个单元全部到终态。

- 成功：1671。
- 完成但未成功：87。
- 基础设施异常：42。
- 运行中、待跑：均为 0。

| 策略 | 成功 | 完成但未成功 | 基础设施异常 | 总数 |
|---|---:|---:|---:|---:|
| causal | 288 | 9 | 3 | 300 |
| crash_no_rollback | 239 | 43 | 18 | 300 |
| temporal_checkpoint | 290 | 6 | 4 | 300 |
| whole_branch_abort | 282 | 12 | 6 | 300 |
| clean_recovery | 286 | 7 | 7 | 300 |
| ideal_oracle | 286 | 10 | 4 | 300 |

最终完整审计时间：北京时间 2026-11-06 04:44:26；issues 为空，冻结源码未变，已有结果和终态哈希证据保留。监控在 04:44:27 更新为 `finished_with_infrastructure_errors`，无告警。实验服务和监控服务均自动正常退出（Result=success、ExecMainStatus=0）。

基础设施异常及未成功结果未重跑。此前每次只在同题零模型预检及保全检查通过后继续 pending；未修改注册模型、时限、上下文或冻结执行器。恢复经过与证据目录见 [持续监控记录](CODEX_SWE_MONITORED_RESUME_20261105.md)。

异常尝试的最终主用量不完整，零值不能视为实际零成本；用量事件仅提供观测下界。因此本记录不声称整批费用完整，也不据此给出完整成本比较。`formal_eligible=false` 保持不变。

原始批次：`/mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1`。机器可读最终汇总及关键证据 SHA256：`monitor/session-final-summary.json`；完整逐单元结果：`cells.json` 与 `cells/`；最终审计：`monitor/session-quality-audits.jsonl`。
