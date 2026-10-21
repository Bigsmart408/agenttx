# AgentTX 实验现场检查：2026-10-21

核验时间：北京时间 09:40–09:47。机器 `r760-ssd`（10.249.185.212），项目 `/home/bfq/agenttx-recovery-20261016/project`；`/home/bfq/agenttx` 指向该项目。

## SWE-agent / SWE-bench

当前批次 `sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`：300 题 × 6 策略，共 1800 单元；1799 完整、1 基础设施异常，已经结束，没有活跃模型 worker。半小时巡检和持续状态刷新仍存在，不代表实验仍在执行。实际 API Token 的最终统计见 `SWE_FINAL_RESULTS_20261019.md`，不能用动态报告的文本估算 Token 替代。

## Terminal-Bench

活动指针仍为 `tb-dual-native-full-20261020-r12`，但该批次已经停止，不是正在运行。

- 最终快照：2026-10-21 04:35:28（北京时间）。
- 421 完整、28 基础设施异常、2443 待跑，共 2892 单元；活跃 worker 为 0，协调进程 2599309 已不存在。
- 停止原因：`circuit-fibsqrt/codex/causal: incomplete_api_usage`。该单元于 03:42:28 记为异常；随后在途任务排空，最终批次在 04:35 停止。
- `agenttx-tb-dual-full-20261020-r12.service` 为 failed / exit-code；监控 `agenttx-tb-dual-monitor-20261019.service` 为 active。监控存活不能视为批次存活。
- 最新监控显示 241 主镜像就绪、241 任务登记为可派发，229 任务有官方测试执行记录。这些指标含义不同，不把镜像构建成功等同于逐任务官方验收已执行。
- `formal_eligible=false`；原始结果、异常和用量证据均保留，本次没有恢复派发或重跑任务。

证据：数据盘 `/mnt/data/agenttx-runtime/validation/active-tb-dual-native.json`、`tb-dual-native-monitor-20261019/latest.json`，以及 `/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r12/progress.json` 和用户服务 journal。

## 机器与代码备份

可用内存约 97 GiB；监控记录数据盘剩余约 1050 GiB。boot ID 为 `a4b0d376-577e-424b-8789-109b05d54c6d`，与前次排障一致。本次没有重启机器、更换内核、清理数据或停止实验进程。

本次按用户要求提交现有代码、脚本、测试和论文；运行数据链接、缓存、恢复片段和嵌套第三方 Git 仓库不纳入代码提交。已有实验代码未经本次重新跑完整实验验证；Git 备份提交不等于重新认证论文结果或实验有效性。

提交前 `git diff --check` 通过；`test_tb_dual_local.py`、`test_tb_worker_compat.py`、`test_swe_context_profile.py` 共 17 项检查通过（无模型调用）。
