# 分层 pilot 推进：2026-10-17

核对时间：2026-10-17T03:17:42.057498+00:00（UTC）。这是状态快照，后台运行仍可能继续。

批次 `stratified-pilot-20261017-r1` 已按用户最新要求暂停，优先配置 Docker 和补齐环境；实验进程已停止，不会自动重启。
状态同步进程也已停止；本页为暂停后的最终快照，可运行文末命令重新核对。已完成及被中断尝试均保留，原登记不变。
先完成 84 个运行时单元，全部通过才自动启动 168 个模型单元；任何运行时检查失败都阻止模型阶段。每个单元只执行一次，无自动重试。

| 阶段 | 计划 | 当前状态 |
| --- | ---: | --- |
| 不调用模型的运行时检查 | 84 | passed: 60；interrupted_by_user: 8；deferred_until_environment_ready: 16 |
| 双框架模型实验 | 168 | deferred_until_environment_ready: 168 |

模型阶段分框架：codex {"deferred_until_environment_ready": 84}；sweagent {"deferred_until_environment_ready": 84}。

选例沿用此前登记的三个家族、每家族四例，共 12 个 manifest；覆盖 16–160 操作、1–10 轮和稀疏至稠密依赖。每例七策略，Codex / SWE-agent 各一遍。
执行源码为冻结 v5，指纹 `938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`。模型统一 `gpt-5.6-luna / max`，模型时限 900 秒，定位探测上限 `2 * operation_count + 8`，无模型调用次数和 token 硬上限。900 秒与先前 240 秒批次分开统计；不能据共享服务器上的耗时宣称受控加速。

**验收：** 自动依赖捕获、工作区与消息一致、保留字节不变；模型单元另需命令审计、独立最终字节及 checker 不变性检查，SWE-agent 必须正常 `submitted`。退出码为 0 不单独构成通过。

**范围：** 本批为协议验证，`formal_eligible=false`；完整双框架微基准目标 16800，正式真实任务目标 5410，目前均不能由 pilot 填充。固定 token 预算、提示词匹配及正式任务环境冻结仍待完成。

证据：[不可覆盖的登记](../experiments/plans/stratified_pilot_20261017/registration.json)、[252 单元清单](../experiments/plans/stratified_pilot_20261017/cells.csv)、[状态汇总](../experiments/results/stratified_pilot_20261017/summary.json)、[全部单元状态](../experiments/results/stratified_pilot_20261017/attempts.csv)。

刷新命令（恢复根目录工具）：

```bash
/home/bfq/.venvs/agenttx/bin/python /home/bfq/agenttx-recovery-20261016/tools/collect_stratified_pilot_20261017.py
```
