# 文件恢复与 agent 上下文同步的交叉消融

补齐 Breakdown 的组件贡献实验。正文旧的 12 案例上下文消融表与对应分析已移除，原始结果继续保留。

根据用户对基线的修正，两个组件都关闭时使用不回滚，而不是 temporal checkpoint。四组使用完整历史格式、相同模型配置、任务镜像、故障种子、运行时实现与交接提示模板：

| 实验 ID | 选择性文件恢复 | 移除受影响调用及结果 |
|---|---|---|
| no_rollback | 关闭，保留故障后文件 | 关闭，保留原始上下文 |
| context_only | 关闭，保留故障后文件 | 开启 |
| filesystem_only | 开启 | 关闭，保留原始上下文 |
| agenttx | 开启 | 开启 |

范围为 SWE-Bench Lite 300 个任务、Terminal-Bench 241 个任务，分别使用 SWE-agent 和 Codex；共 541 × 2 × 4 = 4,328 个单元，每单元一次真实模型续跑。八路执行，TB 同时最多两路；各任务四组配置按固定种子打乱顺序。使用相同脚本生成的故障前缀，随后由原生 agent 调用真实模型完成 benchmark 任务；这批不属于无模型的 RQ1。

模型为 gpt-5.6-luna，reasoning effort 为 max；SWE 模型会话限时 900 秒，TB 3,600 秒、官方验收 1,800 秒。四组均无普通失败自动重试。所有终态尝试和 API 用量保留，服务异常不补零；监控识别已知服务故障后可以验证服务健康、续跑 pending，不覆盖终态结果。

不回滚组不运行定位器，也不撤销文件或上下文。其余三组用相同的验证器引导定位器获取因果集合，不直接读取注入真值作为选择依据。context_only 只在隔离分支验证候选集合，拦截 live apply，因此文件和 ledger 撤销状态保持不变；到最终交接时才移除选定观察。filesystem_only 与 agenttx 正常应用相同的选择性文件恢复；前者在交接时恢复原始观察，后者保留同步后的上下文。实验钩子仅安装在该批实验入口，不修改生产恢复代码。

每次保存 cross-precrash-conversation.json、cross-aligned-conversation.json、cross-model-conversation.json、cross-treatment.json。后者分别记录候选因果集合和实际文件撤销集合，核对模型可见原始调用、注入后及处理后的文件哈希。文件开关关闭的两组要求实际撤销集合为空、文件与注入后相同；上下文开关不能修改文件。完整四组还核对注入签名、同一文件开关下的最终文件状态，以及三种组件配置选择的因果集合。

统计分别报告四种 benchmark/agent 组合，既统计用量完整的全部四组结果，也统计四组均任务成功的共同集合。以 A/B/C/D 对应上表顺序，总 token 下降为 A−D；文件恢复贡献为 ((A−C)+(B−D))/2，上下文贡献为 ((A−B)+(C−D))/2。两项求和等于总下降量，保留负贡献。另报告有上下文同步时 B 对 D、有文件恢复时 C 对 D 的直接对照，以及两组件的交互项。

修正后批次为 /var/tmp/agenttx-recovery-cross/results/recovery-cross-20261118-r7/，运行协议 v2。旧 r4 的监控与派发已停止，14 次已完成调用及其 API 用量原样保留，不合并进新实验。r1–r3、r5、r6 均为零模型诊断批次。各批冻结源码和预检记录保留。

协调器绑定数据目录时不递归引入其他容器的子挂载，并在预检注入前撤销预检自身的文件修改，防止它们影响故障定位。TB 先用相同的无注入环境探测建立基础条件，再在自定义预检中注入并执行各自的真实组件配置，并检查原始用户仍可访问工作目录。这样环境探测不会令四组的注入前调用数量不同。先进行 20 项零模型预检（SWE 四组、TB 两种原始用户环境 × 两种 agent × 四组）；通过后执行 16 个真实模型 canary，门槛是正常执行与完整记录，不以任务成功筛选。

协调服务 agenttx-recovery-cross-20261118-r7.service；监控服务 agenttx-recovery-cross-20261118-r7-monitor.service 每分钟核对进度、终态结果哈希、冻结执行代码与配置。最新状态为批次下 supervisor/latest.json，任务状态为 progress.json，统计为 analysis.json。八项组件处理、提示和贡献计算检查通过。

实现入口：experiments/scripts/run_recovery_cross_ablation.py、recovery_cross_ablation.py、bench_recovery_cross_tb.py、launch_recovery_cross_ablation.py。分析入口为 analyze_recovery_cross_ablation.py；监控入口为 scripts/maintenance/supervise_recovery_cross_ablation.py。

2026-11-18 后续核查：20 项零模型预检及五个组合的四组状态交叉核验已通过，已真实调用模型。context_only 的定位器内部标记被通用检查误认为向模型披露错误位置；核对实际提示后仅更正调度分类，原始结果与用量哈希不变，不重试已完成调用。控制层包装及证据保存在 r7/dispatch-classification.py 与 classification-correction/。盘空间恢复后只续跑 pending，监控保持运行。

2026-11-18 17:04（北京时间）：按用户要求暂停 breakdown 交叉消融。协调器与自动续跑监控均已停止，当前没有活动任务。保留 64 个已完成结果、1 个基础设施异常记录及 4,263 个 pending 单元，未修改任务状态或已有结果。暂停记录为 r7/user-pause.json；只有用户明确要求恢复后才能重新启动监控或派发。
