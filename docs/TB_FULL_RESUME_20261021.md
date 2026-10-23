# 六组全量清旧重跑（2026-10-21）

**用户授权清旧重跑，2026-10-21 15:46（北京时间）。** 最新指令“你可以把之前跑的清掉再跑，不用管这个保护线”覆盖此前保留旧工作数据、只续pending、等待归档及400GiB空间条件。旧等待器、归档器及最后一条卡在外网下载的Clean验证已停止；验证历史为59/60终态、58成功、1失败，最后一条由本次切换主动取消，不能计为成功。r15已在本机实际启动全新241题×双harness×六策略=2892单元，总并行8，旧终态不继承。Ideal和Clean包含在内；Causal/Ideal/Clean compact，三个主对照full；模型、故障种子、任务及官方验收不变。以`--min-free-disk-gib 0`显式取消本轮350GiB停派线，监控同步读取注册值，不再等待400GiB。清理只限旧TB批次cells及本轮归档副本，保留结果摘要、逐单元host-result、镜像、缓存、环境证据、原始恢复源和SWE-bench历史；清理明细见数据盘`validation/tb-full-reset-20261021-r15/`。旧记录中的暂停、继承及空间等待均为已被此授权取代的历史状态。

新服务：`agenttx-tb-dual-full-20261021-r15.service`，2026-10-21 15:46:42（北京时间）登记；结果根目录：`/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261021-r15`。首次核验8个实际worker、8个任务容器，`stop_reason=null`；241题全部就绪，没有环境阻塞。

冻结源码SHA256：`9912a798e1bab5ddfceb665dd7ecfafb4f22e3c36c5c63f740c53f808e11f99f`。继续使用v7协议，模型`gpt-5.6-luna/max`、fault_seed=20261019，模型会话3600秒、verifier1800秒、单worker安全包络9600秒。没有提高token或调用预算，没有修改测试或提供答案。此次重跑为用户整批授权，后续不按失败或收益选择性重跑。

`run_tb_dual_local.py`新增可登记的`--min-free-disk-gib`（原默认350，本轮明确传0）；监控读取同一注册值。12项调度与趋势回归检查通过。保留磁盘剩余量上报，不以新的磁盘阈值替代已解除的保护线。

旧诊断的最后一条build-tcc-qemu/SWE-agent/Clean因下载等待而被主动取消。59个终态及原trend-gate留档；不将其声称为60/60通过，也不混入新全量。前三个主要对照在双harness的含replay总成本、中位数、成功数门槛已通过，Ideal10/10成功、Clean已完成9/10成功，均仅作为诊断样本。

清理审计：`/mnt/data/agenttx-runtime/validation/tb-full-reset-20261021-r15/authorization.json`、`before/`、`retained-host-results/`、`released-hold.json`、`cleanup-progress.json`；`cleanup-complete.json`生成后表示全部目标已删除。旧cohort保留`superseded.json`，指出完整轨迹已清理、摘要在审计目录，不能再声称旧完整原始轨迹仍可读取。原始恢复目录`/mnt/recovery-sdc/recovery-20261014/restored/`不在清理范围。

先前r5归档预检通过、r12复制未完成的迁移安排已取消；其生成的副本列入本次清理。旧r15等待启动器不再运行，不会二次发起任务。每30秒健康监控、每半小时效果快照由持久monitor服务执行，token统计继续包含main+replay。

**执行核验，2026-10-21 15:57（北京时间）。** 清理全部完成：14个旧全量批次、旧趋势验证的cells及本轮两份归档副本已删除，565份逐单元host-result和批次JSON摘要保留。可用空间由约284GiB恢复至约946GiB（新任务仍在写入）。新r15已完成21单元，均成功且用量完整，8路继续运行，stop_reason=null、监控无告警；Ideal/Clean双harness均已有新成功结果。证据：`validation/tb-full-reset-20261021-r15/cleanup-complete.json`及`final-verification.json`。
