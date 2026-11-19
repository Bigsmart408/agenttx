# 恢复后8路实际数据核验

核验时间：2026-10-25 19:15:43（北京时间）。批次 `tb-dual-native-full-20261025-r21-reboot-resume`。

8路均已生成真实模型响应和非零用量记录；这些是在运行期间采集的数据，不代表8路已通过最终验收。

| 任务 | 框架 | 策略 | 已记录token | 数据来源 |
|---|---|---|---:|---|
| hdfs-deployment | sweagent | 不回滚 | 6,782 | container.stdout.log:provider_usage |
| hdfs-deployment | codex | 不回滚 | 1,030,753 | native_session_jsonl:token_count |
| hdfs-deployment | sweagent | Checkpoint | 13,942 | container.stdout.log:provider_usage |
| hdfs-deployment | codex | Checkpoint | 1,331,648 | native_session_jsonl:token_count |
| huarong-dao-solver | sweagent | Causal | 5,604 | container.stdout.log:provider_usage |
| huarong-dao-solver | codex | Causal | 172,551 | model-result.json |
| huarong-dao-solver | sweagent | Ideal | 2,788 | container.stdout.log:provider_usage |
| huarong-dao-solver | codex | Ideal | 235,549 | native_session_jsonl:token_count |

Codex/Causal已写入model-result，模型退出码0，官方验收仍在进行。其他行是日志或原生会话的当前用量，可能继续增长；最终完整账单仍须用原结果核验。以上计数仅用于证明恢复后已实际执行，不用于最终算法成本对比。

原始证据和文件哈希：`/mnt/data/agenttx-runtime/validation/tb-reboot-resume-20261025/eight-worker-data-verification.json`。任务结果目录见证据中的evidence_path。独立容器网络检查为8/8私有，8080转发未修改。

本次核验结束后实验继续后台运行，总并行上限8；当前没有新增官方完成结果。监控每30秒记录状态，摘要保留规则仍为仅Clean保留完整轨迹。
