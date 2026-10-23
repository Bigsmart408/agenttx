# TB 环境与续跑状态（2026-10-20）

**最新存储规则，2026-10-22 14:05（北京时间，用户明确授权）。** 已结束实验只保留关键信息，完整模型轨迹仅保留Clean（clean_recovery），两套harness均包含main与replay；其他五组删除完整轨迹，保留成功/失败、main+replay及逐调用token账单、模型错误/官方验收诊断、配置/源码清单、恢复摘要和原始host-result字节。所有组删除结束单元的事务工作快照、重复per-cell缓存及细粒度大ledger；Clean完整原生轨迹/模型结果/日志保留，Codex原生会话从事务快照提取后在原路径保留。非Clean会话只保留usage事件，冻结用量读取器仍可复算；非Clean模型原始stdout/events不再保留，accounting-summary保留必要模型计数与terminal error。正在运行及未开始的单元不清，单元结束后自动处理；原始输入数据、任务镜像、共享依赖缓存、环境准备证据完整保留，SWE-bench历史不处理。此规则明确覆盖上文“保留所有原始轨迹/原归档副本完整不删”的旧承诺；不删除新失败的结果和诊断摘要，不按结果重跑。持久服务agenttx-tb-summary-retention-20261022已启动，代码在本机系统盘/home/bfq/agenttx-maintenance/，摘要及审计在数据盘validation/tb-summary-retention-20261022；4种Clean/非Clean×Codex/SWE隔离验证均通过，涵盖清理后token不变、原始host-result不变、Clean完整轨迹保留、非Clean轨迹删除及幂等。此前r15归档已于11:56完成全部内容/元数据校验，125条注册结果哈希不变；按新授权也缩为上述保留规则。另本次发现GPG主进程结束后，孤儿gpg-agent仍被strace跟踪，致8路等待；仅终止各自容器内该strace的唯一剩余daemon tracee，保留完整处理记录，未修改题目文件/测试/提示/模型。该题双harness六策略12/12完成且全部成功，阻塞解除。证据：validation/tb-gpg-daemon-repair-20261022/。

**实时核验，2026-10-22 10:56（北京时间）。** r16于10:54快照569完整（520成功、49任务失败），7条原用量不完整异常未增加，8运行、2308待跑；比前次新增79完整。全部569条完整结果的账单齐全；无停派原因或监控告警。当前主要为cprofiling-python与count-dataset-tokens，真实模型和工具进程仍运行。Causal成功Codex48/48、SWE42/48。同题双方成功main+replay总token节省：对不回滚/checkpoint/whole/Clean/Ideal，Codex16.68%/15.42%/21.95%/1.05%/−9.17%，SWE-agent24.84%/29.94%/37.14%/8.05%/5.66%；各36–47对。Codex对Ideal仍多耗，Clean优势降至1.05%；SWE对Clean逐题中位数−10.02%，不能把总量优势说成所有题占优。磁盘约259GiB、空间停派线保持关闭；已启动agenttx-tb-cold-archive-20261022-r15，只归档停止r15的cells，复制/内容及元数据校验通过后以symlink保持原路径、释放SSD重复数据；当前仅复制阶段，尚未宣称容量释放。原结果、轨迹及失败不删除，不处理运行中的r16；校验失败保留原副本。证据：`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261022T025439.json`及`/mnt/data/agenttx-runtime/validation/tb-cold-archive-20261021-tb-dual-native-full-20261021-r15`。

**隔夜进度核验，2026-10-22 09:37（北京时间）。** r16保持8路运行、stop_reason=null、监控无告警；09:36快照490完整（450成功、40任务失败），7个原用量不完整异常未增加，8运行、2387待跑，相对上次新增372完整。7异常中另2条官方通过但账单不完整，仅保留为异常、不并入完整成本比较。490条完整用量均齐全。当前主要运行conda-env-conflict-resolution及configure-git-webserver；已核实conda求解进程有持续CPU活动，不能只因日志静默或总时长超过一小时判停滞。磁盘约598GiB，保护线仍关闭。Causal含main+replay总token节省，不回滚/checkpoint/whole/Clean/Ideal：Codex19.76%/15.89%/22.87%/14.63%/−11.05%，SWE-agent22.72%/14.97%/33.70%/8.77%/3.71%，各31至40个同题成功完整用量配对。逐题中位数：Codex30.32%/18.31%/4.66%/8.81%/0.48%，SWE-agent11.18%/5.84%/28.14%/−7.62%/0.61%。Causal成功数Codex41/41、SWE36/41。所有策略继续保留，Clean/Ideal/Causal compact，其余full；不按结果重跑。审计：`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261022T013625.json`、`/mnt/data/agenttx-runtime/validation/tb-runtime-health-20261022T0137.json`。

**中断已修复并恢复8路，2026-10-21 21:31（北京时间）。** r15在20:15排空停止，118完整（112成功、6任务失败）、7用量不完整异常，2767未启动；触发错误为ancient-puzzle/SWE-agent连接模型接口失败。已在原配套服务网络复现：本机既有NAT PREROUTING将所有目标8080重定向本机18080，host网络正常而bridge容器连接被拒绝。修复仅为自建网桥+子网到10.249.44.127:8080添加带归属comment的PREROUTING例外，随单元结束移除，不删除全局规则。原服务8090连通、认证模型接口HTTP200；15项回归通过。r16于21:30:36启动，125条已有终态完全继承、不重跑或补零，继续2767未启动项，六策略/双harness共8路，Causal/Ideal/Clean compact，其余full，空间停派线仍关闭。已确认ancient-puzzle的SWE Causal与Ideal产生真实API调用及token计数；监控无告警。7异常包括4条Codex会话超时缺最终账单、3条SWE连接错误，不把零计数当零成本纳入配对。当前完整成功配对main+replay总token节省，不回滚/checkpoint/whole/Clean/Ideal：Codex19.71%/17.63%/25.89%/25.20%/11.73%，SWE-agent58.58%/25.58%/58.38%/19.29%/22.99%；各8至10对，小样本。证据：`/mnt/data/agenttx-runtime/validation/tb-ancient-network-repair-20261021-r1`。

**运行复核，2026-10-21 17:51（北京时间）。** r15已完成71单元，最近统计70成功、1官方性能失败，8路运行，stop_reason=null，无基础设施异常或监控告警。当前7路集中add-benchmark-lm-eval-harness，存在ESCI原始数据及依赖下载长耗时，已查进程：Codex/Clean正在pytest、Codex/checkpoint正在国内源安装依赖；adaptive-rejection-sampler/SWE/checkpoint模型阶段结束，官方pytest/R仍有CPU活动，不能把79分钟总耗时直接判为卡死。磁盘约524GiB，空间保护线保持关闭。Causal当前双harness各6/6成功；同题成功配对、main+replay总token相对不回滚/checkpoint/whole/Clean/Ideal：Codex43.13%/14.00%/50.88%/21.99%/−11.35%，SWE-agent66.00%/43.66%/68.50%/28.65%/33.20%。除SWE checkpoint与Codex Ideal为5对外均6对；小样本，Codex对Ideal已变为多耗11.35%，不隐去。

**运行进度核验，2026-10-21 16:44（北京时间）。** r15为62完成（61成功、1任务失败）、0基础设施异常、8运行、2822待跑；全部62条终态用量完整，stop_reason=null，监控无告警，空间停派线保持关闭。当前推进adaptive-rejection-sampler，模型/工具日志仍更新。唯一已查明失败为accelerate-maximal-square/Codex/Ideal：官方性能测试要求加速超过100倍，实测15.75倍；模型正常退出、注入残留清理及笔记检查均通过，按官方任务失败保留，不选择性重跑。最新可比前5题的main+replay总token节省，对不回滚/checkpoint/whole分别为Codex41.23%/25.74%/35.59%、SWE-agent70.47%/43.66%/35.80%；仅为小样本阶段结果。证据：`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261021T084412.json`。

**执行核验，2026-10-21 15:57（北京时间）。** 清理全部完成：14个旧全量批次、旧趋势验证的cells及本轮两份归档副本已删除，565份逐单元host-result和批次JSON摘要保留。可用空间由约284GiB恢复至约946GiB（新任务仍在写入）。新r15已完成21单元，均成功且用量完整，8路继续运行，stop_reason=null、监控无告警；Ideal/Clean双harness均已有新成功结果。证据：`validation/tb-full-reset-20261021-r15/cleanup-complete.json`及`final-verification.json`。

**用户授权清旧重跑，2026-10-21 15:46（北京时间）。** 最新指令“你可以把之前跑的清掉再跑，不用管这个保护线”覆盖此前保留旧工作数据、只续pending、等待归档及400GiB空间条件。旧等待器、归档器及最后一条卡在外网下载的Clean验证已停止；验证历史为59/60终态、58成功、1失败，最后一条由本次切换主动取消，不能计为成功。r15已在本机实际启动全新241题×双harness×六策略=2892单元，总并行8，旧终态不继承。Ideal和Clean包含在内；Causal/Ideal/Clean compact，三个主对照full；模型、故障种子、任务及官方验收不变。以`--min-free-disk-gib 0`显式取消本轮350GiB停派线，监控同步读取注册值，不再等待400GiB。清理只限旧TB批次cells及本轮归档副本，保留结果摘要、逐单元host-result、镜像、缓存、环境证据、原始恢复源和SWE-bench历史；清理明细见数据盘`validation/tb-full-reset-20261021-r15/`。旧记录中的暂停、继承及空间等待均为已被此授权取代的历史状态。

**趋势验证进度，2026-10-21 15:12（北京时间）。** 固定批次59/60结束，58成功、1任务失败，无新基础设施异常；Causal双harness均5/5成功。对不回滚／checkpoint／whole，Codex含replay总token节省32.08%／44.34%／29.40%，SWE-agent16.34%／14.17%／54.22%，各自逐题中位数均为正。共同成功交集分别5／4／5题，三个主要对照的趋势门槛已满足。最后一条build-tcc-qemu/SWE-agent/Clean仍在外网wget下载处等待，整个60单元尚未完成，gate仍pending，全量继续暂停；磁盘358GiB，接近350GiB原保护线。详见 [固定验证](docs/TB_TREND_VALIDATION_20261021.md)。

**最新运行要求：先修复并验证趋势，2026-10-21 14:12（北京时间）。** 依据用户最新指示，已取消自动续全量：14:04暂停r14，14:09收齐在途任务后全量服务停止，保留473完整、33异常、2386待跑及原始证据。新增全量暂停文件与启动/续跑/派发保护；36项相关测试通过。14:10启动独立固定诊断批次：hello-world、csv-to-parquet、build-stp、build-tcc-qemu、catch-me-if-you-can，双harness六策略共60单元、最多8路，每单元一次，不替换题目、不按结果重试。先核验官方成功与完整main+replay用量，再用双方harness共同成功题比较Causal对三个对照的总量及中位数趋势；通过标准已预先登记，验证未结束，不能宣称达标。Clean/Ideal保留参照。验证不会自动启动全量。另已发现r14的Codex用量缺失含网关Responses HTTP404；当前Responses/Chat小请求200，不抹去原错误。两个旧在途容器137退出的具体原因待查。详见 [固定趋势验证](docs/TB_TREND_VALIDATION_20261021.md)。

**TB 代码劣化排查与修复，2026-10-21 11:30（北京时间）。** 已确认三类问题：Codex将内部定位verifier标签当成命令查找/执行（188条v6完整结果中9例）；隐藏故障路径被lstrip破坏（16处）；验收脚本重定向后的诊断没有返回调用方。已修复并登记v7：官方命令明确、Codex当前交接最后且取代旧控制器notice、保留隐藏路径、只读验收入口显示并归档诊断且保持原退出码。85项聚焦host测试、52项原任务容器测试、两套零模型adapter/事务预检通过；原hello-world官方失败码1保持，输出断言诊断。另3项镜像构建测试在旧冻结源码也失败，未算通过。188条用量复算无差异；另从94条Codex原始stdout独立复算main+replay一致，cached/reasoning子集未重复加。compact仍仅作用于恢复前工具前缀，62个已审计前缀均未超6次；历史也是一行笔记，不能用笔记变短解释劣化。已确认的接口问题尚不足以归因全部成本变化；tcc等主求解长尾仍需保留分析。11:28:35持久r13→r14交接已启动，旧dispatcher暂停派新，8个在途worker自然完成后只续pending；当前仍排空，未声称v7已有模型收益。旧结果不改写，不因成败重跑，v6/v7分开统计。详见 [代码审计与修复](docs/TB_CODE_REGRESSION_AUDIT_20261021.md)。

**TB 阶段结果与恢复，2026-10-21 10:37（北京时间）。** 累计421完整、28保留异常，r13已恢复8路、2435待跑；当前v6为158完整、118成功／40失败。r12实际04:35因circuit-fibsqrt/Codex/Causal服务端max_output_tokens结构化失败且缺最终用量而排空停止，至本轮未运行。现只修正已识别模型预算退出的调度分类，要求真实模型结果哈希／末尾turn.failed事件／返回码均匹配；29项检查通过，未知错误仍阻塞。449旧终态原样继承，不重跑、不补零成本。v6同题双方成功、含replay的Causal总token节省：Codex对不回滚12.17%（6对）、checkpoint -36.90%（8）、whole -4.86%（9）、Ideal0.21%（9）、Clean -12.63%（9）；SWE-agent对应14.60%（8）、-46.00%（8）、-81.04%（9）、-60.84%（9）、-74.79%（6）。负值为更多消耗。SWE/whole逐题节省中位数24.16%，但build-tcc-qemu的Causal单题5625800 tokens占配对Causal总量76.74%，拉高总成本；高值保留。Codex/whole逐题中位数-2.61%。磁盘约1049GiB可用。详见 [阶段结果](docs/TB_RESULTS_20261021.md)。

**TB 进度复核，2026-10-20 22:57（北京时间）。** r12为299完整、26保留异常、8运行、2559待跑，stop_reason=null，服务／监控active，无告警。相比22:21新增15完整（14成功、1官方验收未通过：build-stp/SWE-agent/Clean，正常模型退出且恢复检查通过），无新增基础设施异常。build-stp双harness六策略完成；正在跑build-tcc-qemu，Codex Clean也已成功。新15条用量完整、注入残留均清除。v6同题双方成功且含main+replay的Causal总token节省：Codex对不回滚12.03%（2对）、checkpoint -44.50%（3对）、whole -9.27%（3对）、Ideal -44.75%（3对）、Clean -18.48%（3对）；SWE-agent对不回滚8.51%（3对）、checkpoint -24.74%（3对）、whole28.26%（2对）、Ideal -9.33%（3对）、Clean41.38%（1对）。负值为更多消耗；仅1至3题，不能当全量。Codex build-stp Causal4159351 tokens，checkpoint2084039，whole1798009，拉低当前累计优势，尚未进一步归因。数据盘1323.1GiB可用。证据：`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261020T1457.json`。

**TB 磁盘恢复后续跑，2026-10-20 22:21（北京时间）。** r11实际于17:03:47触发data_disk_below_350_gib并排空停止（当时344.6GiB），没有持续跑到本次检查；284完整、26保留异常，较16:53新增10完整且均成功。当前磁盘已恢复1355.4GiB（空间在本轮前已释放，未将其归功于本轮清理）。22:21启动r12，仅续pending；核验284完整、26异常、8运行、2574待跑，stop_reason=null。全部310旧终态原路径继承，源代码SHA256与r11完全一致，协议无变化、未重跑失败。当前跑build-pov-ray/Ideal双harness及build-stp六项。v6同题双方成功、含main+replay的Causal总token节省：Codex对不回滚50.44%（1对）、checkpoint -13.06%（2对）、whole28.66%（2对）；SWE-agent对不回滚5.07%（2对）、checkpoint -18.62%（2对）、whole32.64%（1对）。负值表示多消耗；仅1至2题，不是全量结论。证据：`/mnt/data/agenttx-runtime/validation/tb-progress-resume-20261020T1422.json`。

**TB 进度复核，2026-10-20 16:53（北京时间）。** r11为274完整、26保留异常、8运行、2584待跑，stop_reason=null，服务与监控active、无新增基础设施异常。相对16:35新增11完整（9成功、2官方验收未通过）；11条均已清理注入产物，2条失败为build-pmars/Codex/不回滚与SWE-agent/whole，模型均正常退出。build-pmars/SWE-agent当前同题双方成功且包含replay的成本：Causal379627、不回滚472182（省19.60%）、checkpoint298324（多27.25%）；只有一道题，whole因验收未通过不进入成功配对。Codex Causal尚在运行，v6暂无Codex有效配对；不与v5内核构建数据混算。当前已推进build-pov-ray。数据盘353.4GiB，接近350GiB停止派发保护线；可直接清理的共享pip/uv缓存很小，未删除结果或镜像。证据：`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261020T0853.json`。

**TB 续跑与内部时限修复，2026-10-20 16:34（北京时间）。** r11 于16:32:45恢复，263完整、26保留异常、8运行、2595待跑，stop_reason=null、监控告警为空。r10新增11完整：Codex内核构建题六策略全通过（不回滚也已清除注入残留）；SWE-agent五条均触发上游累计工具1800秒退出，不能直接归因于模型能力。现补齐真实SWE配置：单命令3600／累计3601秒，外层会话3600秒统一兜底；内部累计超时返回124，仍须官方测试和恢复检查通过才成功。所有任务缺Git时用校验过的本地只读fallback，保留镜像原Git及loader，build-pmars双harness和原Git镜像预检通过。50项host、原容器49项回归通过（有重叠，不累加）；清理约12.8GB未使用构建缓存，空余364.7GiB，未删镜像／原始结果。v6与旧配置分组，全部289旧终态原样继承，只续未开始项。新8路实际冻结源码／上下文／本地Git挂载已核对。详见 [修复与证据](docs/TB_RUNTIME_PREREQUISITE_REPAIR_20261020.md)。

**TB 清理合同与超时修复，2026-10-20 15:08（北京时间，用户本轮要求优先）。** 原生 runner 的成功检查要求注入产物消失，却没把清理合同交给“不回滚”组，这是代码／提示对齐缺陷。v5 现给六策略、双 harness 统一追加 `recovery_build/derived.txt` 必须不存在、存在则删除、保留受保护独立笔记的要求；公开固定验收产物不公开基线的故障源码／step，清理由 agent 工具执行并计入实际 API／工具开销。官方测试与两项恢复检查均通过时，模型返回124计成功，另记 `model_timed_out`，不再因未正常退出判失败；缺 API 用量仍单列、不能补零进完整成本比较。模型会话900→3600秒，验收900→1800秒，单项worker安全包络9600秒（覆盖main+replay+verifier），无step／call cap，两套合计8路。此条覆盖旧900秒／必须model_finished的规定。新提示／预算与旧结果按实际配置分组比较，禁止混算配对收益。原始结果不改、不按结果重跑；独立重算显示旧完整样本中2条从超时失败改为通过，另2条官方与恢复通过但用量不完整的旧异常仍单列。26项host检查、原任务容器47项回归、双原镜像完整零模型预检通过。r10已于15:15:12完成切换；15:16:23核验252完整、25保留异常、8路运行、2607待跑，stop_reason=null。277条旧终态按原路径与哈希继承，新8路实际登记的预算／清理合同与冻结源码一致。另修复QEMU initramfs/kernel启动前Git在线安装超时，两题复用已校验只读便携Git并分别通过原镜像零模型预检。见 [修复与证据](docs/TB_COMPLETION_TIMEOUT_REPAIR_20261020.md)。

**TB 失败归因核查，2026-10-20 14:54:57（北京时间）。** 244 完整结果中 150 联合成功、94 失败，但官方测试实际通过 174：22 条仅不回滚组残留注入文件、2 条官方通过但模型超时。70 条官方非零中，9 条是 example.com 新版 HTML 与旧测试内容漂移，8 条为 pandas／网络阻塞导致测试未开始，2 条为题目 Python/pip 未修复，1 条验收超时，其余 50 条需结合实现／版本／性能继续归因。完整观测另有19条模型超时及2条 SWE worker exit_error（重叠，不能相加）。实际 native 不回滚提示隐藏产物路径且未明确清理合同，不能把其联合失败率当成标准 TB 解题失败率或独立定位收益证据。原结果和运行配置不变；8 路继续，stop_reason=null。详见 [失败归因](docs/TB_FAILURE_ANALYSIS_20261020.md)。

## 14:40 设备节点兼容修复、恢复8路

截至14:34检查，r8已于14:29:06排空停止：244条完整结果（SWE-agent127：66成功／61任务失败；Codex117：84／33）、22条保留异常，2626未开始，比上次223条新增21条。新增一次Codex模型限时缺最终用量，以及build-initramfs-qemu/Codex/Clean在模型创建rootfs设备节点后出现SpecialFileError；后者API记录355338 tokens，未丢弃、未归为任务失败或成功。

根因及修复：

- 快照只支持普通文件／符号链接／目录和字符0/0 whiteout，拒绝模型创建的dev/zero。现在字符／块设备及FIFO按原类型、rdev、权限、uid/gid和时间重建，不打开设备、不将活跃节点inode与快照硬链接，避免chmod/chown污染历史状态；快照之间允许链接不可变节点，完整／增量／局部恢复均验证。
- upperdir_digests原先将全部字符设备误判为删除。现只有0/0是whiteout；真实设备的类型／设备号／元数据进入指纹，FIFO单独跟踪。
- 原任务镜像`sha256:910ebc6fcf99144fc8ad813adf00dc4714e490140a85125b2af7da6aefc88154`内，真实事务执行mknod／mkfifo后可进入下一步，替换设备再回滚能恢复1:5设备号和640权限。测试在独立临时工作区，不创建题目答案。
- 85项相关回归直接通过，唯一失败为已有只读配置测试覆盖掉容器必需的TRY_BIND_READONLY挂载。修正测试为保留原挂载并追加测试配置后，该项单独通过。共86项独立检查，原失败日志保留，不冒称首轮全绿。

r9于14:39:42恢复，仅续未开始项，按原路径及SHA256继承266终态（244完整＋22异常），8个worker均存活，2618待跑，stop_reason=null。原模型结果及其token不覆盖、不重跑。执行源码变化及旧／新source_manifest分别登记；这批仍属于已有正式资格限制下的诊断数据，不能将源码变化隐去。

阶段token对比均为main＋replay、同题双方成功配对：SWE-agent对checkpoint节省38.88%（11对）、整分支40.68%（10对）；Codex对checkpoint节省32.34%（13对），对整分支多消耗8.92%（15对）。样本未跑齐，不能将阶段变化当最终结论。

证据：`/mnt/data/agenttx-runtime/validation/tb-device-node-repair-20261020-r1/`包含10项原镜像设备／所有权／快照检查、完整回归首轮及只读测试复验；`tb-runtime-node-resume-20261020-r9/registration.json`记录源码哈希，`execution.json`记录266份原始结果哈希及续跑核验。模型结果在`/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r9/`，源码冻结在本机系统盘相应execution目录。

## 13:44 进度与Git启动依赖修复（北京时间）

r7于13:03:03完成交接并接入修复后的241题清单。当前223条完整结果（SWE-agent115：62成功／53任务失败；Codex108：77／31）、20条保留异常／主动中断、2条运行、2647待跑，合计2892格，比12:57新增51条完整结果。

此次新堵点是broken-python两套agent的Clean启动在模型调用前安装缺失Git超时，240秒APT启动期限触发。其余两个SWE-agent尝试已成功装好Git并进入真实模型执行，尚未完成；不能把调度器存活当作仍在正常补满八路。

已修复：为broken-python复用此前broken-networking的哈希校验只读便携Git，仅提供协调器运行所需Git；不提前修复题目故意损坏的Python/pip，不修改任务APT、DNS或curl。两套原镜像增强预检均通过：实际adapter构造／导入、真实事务、model_calls=0、runtime_tools.git为只读便携依赖。31项相关回归通过。原两个启动失败保留，未按成败重跑。

13:44:05启动持久r7→r8交接服务；当前两个模型任务继续自然运行，结束后继承全部原终态、生成新清单，仅派发未开始项，两套agent合计8路。此快照交接尚未完成。数据盘剩余约414GiB，派发保护阈值350GiB未触发。

证据均在 `/mnt/data/agenttx-runtime/validation/`：`tb-broken-python-audit-20261020-r1/`、`tb-runtime-git-audit-20261020-r1/`、`tb-environment-continuation-20261020-r8/`。原始模型结果在`results/tb-dual-native-full-20261020-r7/`，保持不变。

## 12:57 新实验已起跑与运行器兼容修正（北京时间）

r5已于12:49:09启动，而非一直等待交接。但首次实际SWE-agent导入发现accelerate-maximal-square原Ubuntu18.04的GLIBC2.27不能加载worker中要求GLIBC2.28的tiktoken0.14.0二进制。此前双harness零模型预检只测事务，没有创建实际adapter，因此12:45的“全部环境解除”结论对此不充分。现在增强预检，必须创建真实adapter且零模型调用；不能把先前事务检查冒称完整运行器检查。

为按用户要求直接跑新任务，保留四个SWE导入异常，主动停止四个对应Codex容器，保存全部原日志、源结果哈希和已记录用量下界，不把中断当作题目失败、不按成败重跑。r6于12:52使用240题冻结清单立即启动，两套agent合计八路；该题尚未开始的四格暂列阻塞，原八个终态继续继承。12:56已有172条完整结果、18条保留异常；新派发任务已新增两条完整结果。r6没有停止原因，仍有新任务执行。

兼容修复已核验：

- 同版本tiktoken0.14.0原sdist SHA256 `231dec90efcdccf1b565a1416107736f1e09b1a08fe736ef9d6363e626d03874`。国内源下载；在原task image内编译，未升级／替换题目的libc与Python3.6。
- Rust1.89.0的rustc／cargo／rust-std按发行manifest SHA256校验，国内rsproxy传输；Cargo.lock及构建日志保留。构建只使用worker Python3.11，任务Python保持原版。
- 只对精确任务和镜像挂载同版本worker扩展，原全局venv不写入。扩展SHA256 `7c99833613831e30d9b1fed4685ead8ea23f95b8fc332782b8c23335d391cd0a`，证明文件和扩展哈希固定在执行源码中，拒绝错镜像／资产被改。
- cl100k_base、o200k_base两个编码器共1008组随机／Unicode／控制字符样本与原worker的token序列一致，SWE-agent真实导入成功。两套原镜像增强预检均通过、adapter_import_preflight=true、model_calls=0。
- 63项相关回归通过。[r6a清单](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r6a.json)恢复241可派发（229实际官方测试＋12逐题审计）。新增运行器预检不伪称额外官方测试执行。

持久r6→r7交接已启动，让正在执行的新任务完成后，继承全部终态并以修复版源码及241题清单只续未开始项。原八个异常／中断不重跑；修复会覆盖该题尚未开始的四格。并发仍两套agent合计8路。

证据：

- `/mnt/data/agenttx-runtime/validation/tb-direct-continuation-20261020-r6/interruption-registration.json`、`retained-interruptions.json`
- `/mnt/data/agenttx-runtime/validation/tb-worker-compat-20261020-r1/`：原sdist、Rust分发、构建脚本／命令／日志、wheel、Cargo.lock、1008组对照和原镜像导入检查。
- `/mnt/data/agenttx-runtime/cache/tb-worker-compat/tiktoken-0.14.0-glibc227/proof.json`
- `/mnt/data/agenttx-runtime/validation/tb-accelerate-audit-20261020-r3/`
- `/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r6/progress.json`
- `/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r7/`

## 12:45 全部环境解除阻塞（北京时间）

本机 r760-ssd 的241个主镜像全部建成，最新[不可变清单r4f](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r4f.json)确认241题可派发，blocked_tasks为空。其中229题实际执行官方测试，12题为逐题审计的预期未解题基线，且两套agent均通过原镜像零模型事务预检。审计项不计入官方测试实际执行数，环境可用不代表题目已解。

- Windows3.11：从原任务镜像指定下载层单独提取原磁盘，268435456字节，SHA256 `58fb76014dccf13ec048e894ca1e44773eee940ccbc71c97a2ab3d07285da553`。原盘核心文件测试通过；剩余QEMU／VNC未启动对应题目要求的输出，双原镜像预检和逐题审计通过。
- WindowsXP：原同名ISO从可达镜像下载，617754624字节，SHA256 `fd8c8d42c1581e8767217fe800bfc0d5649c0ad20d754c927d6c763e446d1927`匹配固定原值。进入官方测试：11项，2通过、9个未解题断言失败、零错误。
- LC0原权重：解压SHA256与指定网络ID一致，原验收SHA1也匹配；国内镜像的PyTorch2.8.0+cpu和torchvision0.23.0+cpu与原索引SHA256一致，完整文件校验后本地安装，其他原pin保持。
- LC0验收源码：原Git克隆停滞，改为归档传输，重组原签名提交字节，commit `69fbfc6567a19da09793a4418dc2c533d30bd064`、tree `758b7092c8196b2a50ba4088273111776adad50f`，fsck通过。原脚本没有固定源码版本；本次明确冻结到所取上游提交，只供可信验收阶段，不写进agent初始镜像。
- LC0编译：五个Meson资产按原wrap SHA256校验缓存；修复复制源码的属主及Ninja命令发现，使用可执行wrapper限制4路编译。r7诊断203.21秒结束，官方参考ONNX为14882469字节、SHA256 `c29e25a59a48f747993f1153fd804b42f923b3c97cd3c4458b5fecbf472b8755`，ONNX checker及ORT加载通过。numpy2.2.0、torch2.8.0+cpu、torchvision0.23.0+cpu、onnx1.18.0、onnxruntime1.22.1均实测导入核验。诊断4CPU／12GiB和1800秒上限不改变正式模型／验收900秒登记。
- LC0基线：原测试仅因题目要求创建的lc0utils.py缺失而collection失败。审计强制校验参考模型生成／加载证据、版本、哈希、原测试、唯一预期错误及双harness原镜像ID，不能用缺模块掩盖编译失败。不预写LC0Net、state_dict或任务答案。r1审计因pytest XML把错误放正文而拒绝；r2按原XML格式严格核对，复用两份已通过且哈希校验的零模型预检。原失败保留。

62项相关回归全部通过，涵盖下载层提取、原哈希拒绝、签名源码重组、基线审计拒绝坏参考模型／版本／额外错误、配套服务、镜像、bootstrap、native与调度器。

12:44:47启动持久交接服务 `agenttx-tb-environment-continuation-20261020-r5.service`。仅SIGSTOP旧dispatcher，8个worker继续自然运行；结束后按哈希继承所有终态，生成r5的241题冻结清单，仅续未开始项，两套agent合计8路。此快照仍在排空，尚未声称r5已派发。原r4冻结236题清单保持可追溯；主／replay用量及异常保留，不按成败重跑。监控继续每30秒检查。

证据均在 `/mnt/data/agenttx-runtime/validation/`：

- `tb-win311-asset-repair-20261020-r3/proof.json`、`tb-win311-audit-20261020-r3/install-windows-3.11/result.json`
- `tb-xp-asset-repair-20261020-r3/proof.json`、`tb-xp-image-repair-20261020-r1/`
- `tb-lc0-cpu-wheel-cache-20261020-r1/execution.json`、`tb-lc0-verifier-source-20261020-r2/proof.json`、`tb-lc0-packagecache-20261020-r1/execution.json`
- `tb-lc0-bootstrap-20261020-r7/leelachess0-pytorch-conversion/result.json`、同目录`lc0-verifier-prerequisites.json`
- `tb-lc0-audit-20261020-r2/leelachess0-pytorch-conversion/result.json`
- `tb-usability-inventory-20261020-r4f.json`、`tb-environment-continuation-20261020-r5/registration.json`、同目录`drain-progress.json`

以下保留早先快照。

## 12:16 修复快照（北京时间）

本机 r760-ssd：238/241 主镜像已构建，准备清单 [r4a](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r4a.json) 已核验238题可派发，其中228题实际执行官方测试、10题逐项审计为预期任务基线并通过双 harness 零模型事务预检。审计不冒充官方测试已执行，不要求未解题基线全绿。

| 堵点 | 实际修复及验证 |
| --- | --- |
| QEMU／Alpine | 保留原系统、signed Debian归档包和Alpine版本；仅验收补python3-distutils，两题进入官方测试。 |
| Flink | 原commit `fe3613574f76201a8d55d572a639a4ce7e18a9db`、tree `fe436b1700cddd42fad6821ecb43852d671097d9`，校验完整Git树和原提交对象后改用源码归档传输；原reset／移除tag和origin／gc保持。进入官方测试。 |
| C4 | 原revision `1588ec454efa1a09f29cd18ddd04fe05fc8653a2` 第00009分片318185592字节，SHA256 `807a548efbb10153c9eff0df5733a97a1b51ab1743242530de1b02a8ea17ace7`。只在可信验收阶段取数、校验，再改一次数据源类型和文件路径表达式；断言不变，不给模型预置heldout。131.77秒完成原测试，未解题失败，无环境错误。 |
| Spark | 4CPU／12GiB诊断完成12次原性能测量，582.06秒结束，三个失败均为缺任务要求的metrics.json。正式模型／验收仍沿用登记的900／900秒，不使用1800秒诊断上限。 |
| Python3.6／pytest8 | accelerate-maximal-square仅验收将pytest8.4.1改为最后支持Python3.6的6.2.5，原Python3.6／numpy1.19.5／断言保持。明确记录verifier_compatibility_exception；原verifier版本pin不保持。缺/app是题目要求创建/app/maximal_square.py的未解题状态，经逐题审计及双原镜像预检，不预写脚本。 |
| LC0原权重 | 原上游重定向到失效data域；storage同路径可用。gzip6346106字节，解压SHA256 `47e3f899519dc1bc95496a457b77730fce7b0b89b6187af5c01ecbbd02e88398`与任务完全一致，压缩SHA256 `7b14c647813a1620aff8935e8502ae16239acf29aa7377ff76578474644c50b1`。已缓存，仅替换下载传输，正在修原版依赖安装超时。 |
| Windows3.11 | 从alexgshaw原任务镜像的原wget下载层单独提取输入；layer SHA256、原磁盘SHA256均通过，268435456字节，未复用镜像做题后工作区或执行代码。正在构建。 |
| WindowsXP | BobPony目录提供原同名x14-73974 ISO，Range206与617754624字节总量已核验；并发下载触发503／429，降低并发续取完整分片，安装前必须匹配原固定SHA256。下载中，不宣称已解除。 |

12:06:12，持久交接流程仅暂停旧dispatcher，等八个worker全部自然结束后继承130个终态（126完整、4异常），未重写原模型结果、未按成败重跑；r4使用新冻结代码及236题清单，合计8路。新准备清单238题与正式冻结236题分别报告，未偷偷修改登记。最新进度126完整、4保留异常、8运行、2694待跑、60阻塞，stop_reason=null。

证据目录均在 `/mnt/data/agenttx-runtime/validation/`：

- `tb-environment-continuation-20261020-r4/execution.json`、`tb-usability-inventory-20261020-r3.json`、`tb-usability-inventory-20261020-r4a.json`
- `tb-qemu-bootstrap-20261020-r7/`、`tb-qemu-ssh-bootstrap-20261020-r8/`
- `tb-flink-source-repair-20261020-r3/proof.json`、`tb-flink-image-repair-20261020-r7/`
- `tb-c4-bootstrap-20261020-r5/reshard-c4-data/result.json`（含原／有效测试哈希和数据传输登记）
- `tb-spark-bootstrap-20261020-r2/predicate-pushdown-bench/result.json`
- `tb-task-baseline-audit-20261020-r1/`、`tb-task-baseline-audit-20261020-r2/`、`tb-intended-fault-audit-20261020-r2/`、`tb-accelerate-audit-20261020-r2/`
- `tb-lc0-asset-repair-20261020-r1/proof.json`、`tb-win311-asset-repair-20261020-r3/proof.json`、`tb-xp-asset-repair-20261020-r2/`

53项相关检查通过（新增分段下载前）；分段下载另补本地真实Range响应的重组验证。原失败和临时分片保留。下面保留早先快照。


## 11:43 修复快照（北京时间）

当前主镜像 231/241；220 个不同任务已进入官方测试流程，比 11:05 的清单增加 24 个。旧的 196 题派发清单尚未更新，当前 r3 模型进程仍使用冻结源码；以下准备结果不能混称为旧批次已纳入。

已解除的具体阻塞：

- 12 个配套服务任务全部在独立 Compose 项目／原始数据卷上运行到官方测试。专用 Docker 的 `iptables=false`、`ip-masq=false` 导致私有网桥缺 NAT；增加带项目标签、限定网桥／子网的 NAT/FORWARD 规则，并在结束时逐条清理，无 Docker 重启或全局规则重置。真实探针：任务 HTTP 200、国内 PyPI HTTPS 200、模型端点 TCP 可达；首次探针结束后规则清理成功。网络规则失败回退和拒绝全局范围的回归通过。
- simple-web-scraper 和 postgres-csv-clean 的 SWE-agent／Codex 共四次原镜像事务预检均通过，零模型调用。
- fix-git 原上游已删除。从 `alexgshaw/fix-git:20251031` 提取原始 Git 对象；setup.sh 和资源与固定任务完全一致，Git fsck 和 PIN→TIP 祖先关系通过。只导出原 TIP 可达的 master 历史，不使用旧镜像做题后的工作区；保留原 reset／detached commit／checkout 初始化过程。重建后官方测试运行成功（未解题基线失败）。
- html-finance-verify、query-optimize、path-tracing、path-tracing-reverse、word2vec-from-scratch 完成固定 SHA256 资产／原版安装依赖修复，进入官方测试；3 个事务适配任务及 video-processing 也进入测试。
- neuron-to-jaxley-conversion、mnist-learning-fix 的依赖失败来自 40 秒下载超时。保持原版本，国内源及单次下载上限 180 秒的独立尝试进入官方测试，原失败保留。

仍在处理：QEMU 的 Bullseye 安全包已移出国内在线镜像，已验证 Debian 归档同版本包可下载，保留签名校验，仅关闭归档索引的过期时间检查；Flink 首个代理克隆 680 秒后 early EOF，已换另一代理重试原 release-1.11.2；其余模型／数据资产仍由持久队列补齐。Windows 两个 Archive.org 资源的域名和备用入口连通性失败，LC0 原权重返回 404，accelerate-maximal-square 原 Python3.6 与 verifier 固定 pytest8.4.1 不兼容，均未通过换资源／改答案掩盖。5 个 collection／fixture 错误指向任务明确要求实现的输出，正做源码、日志哈希和双 harness 原镜像预检审计，不按测试全绿设门槛。

验证：43 项网络／镜像／bootstrap／native／queue 检查、2 项任务基线证据校验，共45通过。

证据：

- `/mnt/data/agenttx-runtime/validation/tb-network-egress-proof-20261020-r1/`
- `/mnt/data/agenttx-runtime/validation/tb-sidecar-official-20261020-r3/`（2题）和 `tb-sidecar-official-20261020-r4/`（10题）
- `/mnt/data/agenttx-runtime/validation/tb-sidecar-native-preflight-20261020-r1/execution.json`
- `/mnt/data/agenttx-runtime/validation/tb-fixgit-source-repair-20261020-r2/proof.json`
- `/mnt/data/agenttx-runtime/validation/tb-fixgit-image-repair-20261020-r2/`
- `/mnt/data/agenttx-runtime/validation/tb-missing-repair-20261020-r4/`
- `/mnt/data/agenttx-runtime/validation/tb-neuron-bootstrap-20261020-r2/` 和 `tb-mnist-bootstrap-20261020-r3/`

以下为先前快照，保留供追溯。


更新：北京时间 11:05，本机 r760-ssd。

环境尚未全部解除阻塞。241 个任务中，224 个主镜像已构建，196 个任务已通过原版安装／官方测试执行验证，45 个仍未获准派发。构建完成不能直接算作验证通过。

本轮新增四题：financial-document-processor、fix-ocaml-gc、shell-deobfuscation、sudo-llvm-ir。四题均在未解题的原镜像里完成 bootstrap 并进入测试，断言失败属于正常未解题基线。镜像和工作区未提交为模型起始状态，无模型调用、无答案代码。

- 补齐 uv 0.8.14：国内 PyPI 下载，按 PyPI 发布 SHA256 校验 wheel，运行版本核验通过；资产在数据盘工具目录。
- 环境诊断的可信 verifier 使用 root，修复原任务用户无法复制 `/tests` 的问题；agent 的原账号保持不变，与 v4 模型验证方式一致。
- implement-eigenvectors-from-eigenvalues-research-paper 的依赖安装成功，但 collection 导入缺少 `abs_eigvec_from_spectra`。task.yaml 明确要求模型实现这个函数，因此未通过提前实现答案来“修环境”；原始诊断保留，保守派发清单仍未纳入此题。
- 其余阻塞包括 17 个主镜像构建失败，以及仅完成事务／配套服务检查、测试 bootstrap 或任务基线问题尚未审计的任务。

## 实验中断与续跑

r2 于北京时间 10:24:28 排空在途任务后停止，留下 57 完整结果、3 基础设施异常。最新异常是 adaptive-rejection-sampler / Codex / Clean 达到 900 秒会话上限，没有 CLI 最终用量事件。这与此前 pip 系统路径／uv 缓存发布异常不同；后者已由私有事务视图验收修复。

对超时会话的实际 response usage 去重后可核对 59 条记录、4,659,881 tokens。这是已记录的消耗下界；终止时可能还有未返回请求，完整总额未知，不能把原来的零值视为实际消耗。审计另存，原模型结果不覆盖。

调度规则现在仅对已识别的模型限时、且其他运行环境／上下文检查均通过的 `incomplete_api_usage` 保留基础设施异常并继续未开始任务。它不进入完整账单配对比较；effects.json 单列全部保留异常及已知用量。其他缺账单、工作区／上下文不一致、worker 超时等仍停止新增派发。超时任务不重跑，不补零，不伪装为完整结果。

11:04:48 启动 `tb-dual-native-full-20261020-r3`，按原引用继承全部 60 个终态，仅派发未开始项。11:05:03 核验：57 完整、3 原异常、8 运行、2284 待跑、540 环境阻塞（45 题 × 12），stop_reason=null。两套 agent 合计 8 路，服务及监控均 active。四题解除阻塞新增 48 项可派发观测。34 项相关回归通过。

## 证据

- [新环境清单](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r1.json)：保留上一版及 SHA256，逐题引用验证结果。
- [新镜像首轮检查](/mnt/data/agenttx-runtime/validation/tb-new-images-bootstrap-20261020-r1/execution.json)：五题诊断与所有失败保留。
- [补齐版本／权限后复验](/mnt/data/agenttx-runtime/validation/tb-new-images-bootstrap-20261020-r3/execution.json)：financial、shell 两题进入原版测试。r2 因诊断代理配置依赖缺失 git 失败，记录保留；r3 未给无需 git 的脚本附加代理配置。
- [用量审计](/mnt/data/agenttx-runtime/validation/tb-codex-timeout-usage-audit-20261020.json)：源结果哈希、会话日志哈希、去重下界。
- [续跑审计](/mnt/data/agenttx-runtime/validation/tb-resume-audit-20261020-r3.json)：60 个终态原样继承及源结果哈希。
- [当前进度](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r3/progress.json)；[登记](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r3/registration.json)；[含异常用量的比较](/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r3/effects.json)。

运行代码冻结在系统盘 `/home/bfq/agenttx-executions/tb-dual-native-full-20261020-r3/project`；镜像、依赖、缓存、日志及结果在 `/mnt/data/agenttx-runtime`。监控每 30 秒检查、每半小时保存快照；它本身不自动重跑模型任务。
