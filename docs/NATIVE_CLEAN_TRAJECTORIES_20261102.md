# SWE-Bench / Terminal-Bench 自主 Clean 轨迹采集

用户要求使用真实任务上的 Clean 执行分析 Motivation 的依赖与交错结构。
2026-11-02 启动独立批次。原 400-case Evaluation 补充对照和上下文消融继续运行，未停止。

| Benchmark | SWE-agent | Codex |
| --- | ---: | ---: |
| SWE-Bench Lite | 300 | 300 |
| Terminal-Bench | 241 | 241 |
| 合计 | 541 | 541 |

共 1082 次任务执行，每个任务、每个 agent 运行一次。任务提示来自原始 problem statement / instruction。
不注入错误，不植入既定工具轨迹，也不增加合成产物修复任务。模型自主选择工具调用和解题路径。
使用原任务镜像、原工作目录与原任务用户；SWE 源码回到任务 base_commit，保留镜像安装的运行环境。
TB 沿用已有镜像、侧车服务、私有网络及官方测试引导配置。代码和模型 profile 在启动时固定，结果独立保存。

模型为 `gpt-5.6-luna`、reasoning=max。模型阶段每次 3600 秒，评分阶段 1800 秒，无额外工具步数或模型调用上限。
独立队列并发 4，不修改已有 Evaluation 的 8 路运行。四个组合的首条真实执行用于核查采集有效性，仍属于原任务总体；
四条均结束且访问归属有效后才继续派发后续任务。基础设施错误会停止新增派发，保留在途结果，不按成败自动重跑。

## 保留内容和统计口径

- SWE-agent：完整原生 `.traj`、模型用量、逐步骤 ledger、agent 对话记录。
- Codex：实时写盘的 JSON 事件、原生 sessions 对话、strace、工具事件时间区间、文件变化和依赖导出。
- 两者均导出 `clean_recovery/native-trace.json`，并记录原始提示、镜像、源码及配置 hash、执行结束状态、官方评分结果。
- 模型执行的轨迹在评分前保存，评分命令不计入模型工具轨迹。
- 完整执行但任务评分失败的轨迹也保留；超时前缀、访问归属无效等状态单列，不能当作无机会样本。
- Codex 只导出一个外层 exec turn，不伪造内部模型轮次；共同指标按工具调用计算，跨轮指标另用于 SWE-agent。

这些记录服务于真实轨迹的结构分析。已有 400-case 仍服务于 Evaluation 中策略恢复后继续执行的成本比较；
其故障前轨迹由脚本构造，不能替代本批的自主执行轨迹。
原论文正文及图暂不更新，等待采集完成并核对。

## 运行与复现

- 结果目录：`/home/bfq/agenttx-experiment-data/results/native-clean-swe-tb-20261102-r1/`
- 冻结代码：`/home/bfq/agenttx-executions/native-clean-swe-tb-20261102-r1/project/`
- 用户服务：`agenttx-native-clean-swe-tb-20261102-r1.service`
- 入口：`experiments/scripts/run_native_clean_campaign.py`
- 进度：结果目录的 `progress.json`、`cells.json`、`coordinator.log`。
- 每任务结果：`cells/{swe,tb}/{sweagent,codex}/TASK/clean_recovery/`。

四个组合的无模型环境检查已经通过，保存在
`/home/bfq/agenttx-experiment-data/validation/native-clean-20261102/`；TB 的 r1 检查暴露了无 Git 镜像的导入顺序问题，
调整为先启用已验证的只读 Git 工具，再导入 agent，r2 检查通过。原始失败日志保留，未产生模型调用。
