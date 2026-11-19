# RQ1：Selected effects 补充对照

按 2026-11-14 的要求，将原 `Root only` 对照以 `Selected effects` 的显示名称
加入 motivation 和 RQ1 microbenchmarks。原始策略 ID 仍为 `root_only`。

Motivation 直接使用已核验的 400 个分析台账案例，长度扫描和依赖扫描图均显示
该对照。它只撤销错误根调用，不扩展依赖闭包；平均清除 17.03% 的受影响调用，
有效工作损失为 0，只有 10/400 个没有依赖后继的案例完整清除影响。

RQ1 补充执行同样的 400 条固定轨迹，每条重复三次，共 1,200 次实际运行。
使用原 RQ1 镜像和冻结的核心运行时，新增的基准分支通过 `rollback_exact`
只撤销给定错误根；不会调用因果扩展。4 个额外运行前检查覆盖三类扫描和密集依赖端点。
策略执行正确与清除全部错误影响分别计分。容器没有网络，不启动模型或原生 agent。

- 结果目录：`/home/bfq/agenttx-workspace/agenttx-experiment-data/results/rq1-selected-effects-1200-20261114-r1`
- 执行服务：`agenttx-rq1-selected-effects-20261114-r1.service`
- 监控服务：`agenttx-rq1-selected-effects-monitor-20261114-r1.service`
- 并发：32；执行异常时停止派发，保留已完成结果。
- 分析脚本：`experiments/scripts/analyze_rq1_selected_effects.py`。
- 分析输出：`review/rq1-selected-effects-final-20261114/`。

分析逐个核对结果哈希、输入 manifest、恢复集合、保存的文件字节、台账和可见调用历史。
直接重建正确及注入后的工件内容，验证选定根的文件已撤销、保留文件字节未变，
并从实际保留字节重新计算残留错误集合。

补跑于北京时间 2026-11-15 05:09:41 完成：1,200/1,200 次，执行异常为 0。
全部 1,204 次主实验及运行前检查的文件字节和历史核验通过。平均受影响调用清除率
为 17.03%，有效工作保留率为 100%，完整清除影响为 30/1,200 次（2.5%），
每次仅撤销一个调用。最终数据见 [结果汇总](../review/rq1-selected-effects-final-20261114/README.md)。

RQ1 论文图表将该补充对照与原有四种策略并列：400 × 5 × 3 = 6,000 次。
原始 6,000 次注册数据（其中包含未展示的 No rollback）保持原样，新增结果独立保存。

复现分析和图表：

```bash
PYTHONPATH=src:. /home/bfq/.venvs/agenttx/bin/python experiments/scripts/analyze_rq1_selected_effects.py \
  --cohort /home/bfq/agenttx-workspace/agenttx-experiment-data/results/rq1-selected-effects-1200-20261114-r1 \
  --output review/rq1-selected-effects-final-20261114
PYTHONPATH=src:. /home/bfq/.venvs/agenttx-paper/bin/python motivation/plot_rq1_fixed_trajectories.py
```
