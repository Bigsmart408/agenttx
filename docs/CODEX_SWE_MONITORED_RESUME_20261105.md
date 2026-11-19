# Codex × SWE-Bench Lite 持续监控续跑

2026-11-05 北京时间 09:07 恢复 `codex-swe-full-recovery-20261104-r1` 的待运行队列。模型、时限、上下文、任务列表、故障种子、镜像和冻结执行源码均沿用原注册；启动前逐文件核对冻结源码 SHA-256。

恢复前为 33 个完成单元、1 个基础设施异常、1766 个待运行单元。原异常为 `django__django-10914 / crash_no_rollback`：命令派发后未收到执行器正常响应，事务保护拒绝提交。现有日志不足以确定其底层原因，未据此修改运行时，也未重跑该单元。其已知记录完整保留；报告的 0 token 不代表实际零费用，用量完整性为未知。

在相同冻结环境中对原异常任务执行了零模型调用检查：故障注入有效、源文件恢复、独立文档保留和派生产物清理均通过。随后仅恢复 pending 单元，最多 8 路。恢复前的 `cells.json`、`progress.json`、`execution.json`、注册和异常结果保存在：

`/mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1/maintenance/monitored-resume-20261105T010618Z/`

## 服务与证据

- 协调器：`agenttx-codex-swe-full-recovery-20261104-r1.service`（user systemd）。
- 独立监控：`agenttx-codex-swe-monitor-20261105.service`（user systemd，失败自动重启，已启用）；用户 linger 已开启，退出远程窗口后继续运行。
- 每 60 秒检查协调器服务、进度新鲜度、8 路并发上限、单元墙钟时限、结果身份/源码/模型/上下文、token 算术、宿主写入保护和既有终态证据不变性。
- 每 60 秒保存活跃执行器的 strace 大小/更新时间、经过凭据过滤的执行器 stderr 尾部。每 30 分钟保存健康汇总；不导出模型请求或回复。
- 新的同类执行器响应异常先停止派发、排空运行任务；通过同任务零模型检查后仅继续 pending。异常单元保留，不重新排队。执行器响应故障自动继续最多每小时 3 次；严格核实的注册会话时限耗尽单独处理。未知异常、检查失败、结果不一致或达到故障次数限制均保留告警，等待诊断。
- 注册的 `formal_eligible=false` 不变。任务失败不按结果重跑，旧实验和自主 Clean 采集不混入本轮。

原始监控记录位于批次下 `monitor/latest.json`、`monitor/history.jsonl`、`monitor/halfhour-latest.json`、`monitor/terminal-evidence.json` 和 `monitor/service.log`。每次自动继续的检查及原终态快照保存在 `maintenance/automatic-pending-continuation-*`。

人可读状态：[实时进度](CODEX_SWE_FULL_RECOVERY_PROGRESS_20261104.md)。原协议：[实验登记](CODEX_SWE_FULL_RECOVERY_20261104.md)。监控源码：[monitor_codex_swe_recovery_20261105.py](../scripts/maintenance/monitor_codex_swe_recovery_20261105.py)。实际运行监控副本及 SHA-256 保存在上述 maintenance 目录。

已通过 4 项监控测试，覆盖终态证据替换/重新排队检查、已知异常分类、容器 PID 与宿主服务健康区分，以及自动继续时原单元/失败结果保持不变。

## 11:57 继续处理 Clean 执行器异常

11:56 直接复查发现队列停在 104 个完成、2 个基础设施异常、1694 个待运行。新异常为 `pallets__flask-4992 / clean_recovery`，监控在 09:38 保存了 `strace: ptrace(PTRACE_LISTEN,...): Input/output error` 的命名空间诊断，随后结果明确报告派发后执行器响应不确定。Clean 没有恢复提交，因此顶层 `error` 字段为空；原监控仅识别带恢复提交保护异常的情况，未自动继续。

修正的是监控分类，未改动冻结执行源码，也未重排这个失败单元。新增测试后共 5 项通过。同任务零模型预检全部通过，原终态和费用未知状态保留，11:57 恢复 8 路 pending。分类审计及诊断位于 `maintenance/clean-worker-classification-20261105T0357*`，继续运行审计位于 `maintenance/automatic-pending-continuation-20261105T035745Z`。当前会话按用户明确要求持续直接检查，不以安装后台服务作为本轮完成条件。

## 12:51 注册模型时限耗尽

直接监控捕获 `psf__requests-2674 / temporal_checkpoint` 的新异常。该次模型阶段 901.8 秒，返回码 124，stderr 明确记录 `external harness timeout after 900.0s`，与注册上限一致；未观察到此次执行器响应故障。原始记录仍按冻结执行器输出保留为基础设施异常，未重写为其他结果，也不重跑或放宽时限。

为避免将时限耗尽的 0 主执行 token 误作零成本，当前会话增加了只读的 live rollout 整数计数采样。只导出工具调用数、用量整数和时间戳，不导出模型提示或回复。该失败单元已观察到主执行用量下限 2,030,269 token，另有完整 replay 38,613 token；最终主执行用量仍未知。证据保存在 `monitor/session-model-activity.jsonl` 和 `maintenance/registered-timeout-review-20261105T045314Z/timeout-and-usage-audit.json`。

监控现可严格识别注册模型超时：任务/策略/模型/源码/上下文匹配、实际模型阶段在注册上限后 30 秒内、返回码和明确时限信息相符、未完成、未提交、无宿主写入泄漏。排空并完成同任务零模型检查后只继续 pending，不重排这个终态。此类时限耗尽不计入执行器响应故障的每小时 3 次保护阈值。7 项测试覆盖两种异常继续路径的终态保留和限时判定；冻结模型执行源码保持不变。


### 2026-11-05 14:20 北京时间：监控诊断超时容错

14:19 前台检查发现监控服务重启；日志确认只读 `worker_snapshot` 的 Docker exec 超过 25 秒，未捕获 TimeoutExpired。协调器和八路实验继续，333 completed、4 infrastructure_error、1455 pending，stop_reason=null。仅更新监控脚本：超时或非 JSON 响应记录 unavailable，并产生 worker_diagnostics_unavailable 告警，继续健康和质量检查。10 项测试通过；原监控脚本、注册和异常证据保留在 maintenance/monitor-diagnostic-timeout-20261105T062017Z。仅重启监控服务；新监控 SHA256 ac38d375c5c72ef8992a75ce398c11c34baf9f5d1ff0723260b5a383d6607c01。14:20:18 后诊断恢复正常，无告警。冻结执行源码、实验协议、已有终态和失败费用证据保持原样，继续会话内持续监控。


### 2026-11-05 15:21 北京时间：注册超时与沙箱启动失败复核后继续待跑

15:10:51 两项新异常终态保留：pytest-dev__pytest-5495/whole_branch_abort 明确超过注册的 900 秒模型时限，rc124、model wall 901.941958s，主用量观测下界 1,455,225、已知 replay 25,829，最终主用量不完整，原始 main=0 不当作零成本；结果 SHA256 35212d8e5c1f18ffb024c533a91ae099e5c4a96f9e554c115dab8c1018b0b781。pytest-dev__pytest-5692/crash_no_rollback 在 bwrap 进入 Python 前失败，日志为无法对 Docker overlay merged 挂载应用 flags（Invalid argument），rc1，result.json 仍缺失、tokens 仍 null；底层触发机制尚未证实。禁止补造结果或重跑终态。

缺失结果现在会让监控明确要求复核，避免直接读取不存在文件导致监控崩溃；11 项监控测试通过。只更新监控代码，SHA256 d4c5e7660adc89339db254fd375279b2d09ac237dfa128fce188b2487d0f5c4c。15:20 所有存活任务自然结束为 440 completed、6 infrastructure_error、1354 pending。启动失败按单次精确日志和原始 cell 人工复核，同任务零模型检查通过，记录 maintenance/startup-mount-review-20261105T071409Z/audit.json。仅将该项加入 reviewed_errors，既有监控随后独立校验注册超时并完成其同任务零模型检查，记录 maintenance/automatic-pending-continuation-20261105T072054Z/audit.json。没有新增通用挂载失败自动放行规则。

15:21 已恢复八路，440 completed、6 infrastructure_error、8 running、1346 pending，stop_reason=null；直接质量审计无问题。所有原始终态、缺失结果状态、费用证据、冻结源码和协议保留。用户持续监控要求仍有效，继续会话内报告，不以恢复派发为结束。


### 2026-11-05 16:42 北京时间：Sympy 两项超时保留，待跑恢复

sympy__sympy-13146/crash_no_rollback 于16:31:33结束为注册900秒模型超时，rc124、official_model_wall_s=904.224748，模型/源码/上下文身份与安全标志均满足既定严格判定。主用量观测下界3,434,171、replay=0，最终主用量不完整；结果 SHA256 13b900ed511427619d740f25886b19ccbd4e79034e2df098c63db818b4988802。

同题 clean_recovery 于16:39:23生成异常终态，rc124、明确900秒超时标记，但 official_model_wall_s=1383.373169，故既定自动判定拒绝。前台16:36只读进程检查已确认Codex进程退出、外层Python仍在CPU处理中（ticks30986→34198），保留strace共3,950,984,334字节。冻结代码中外层phase计时覆盖 tx.run_tool：try_worker独立按timeout_s等待并杀进程组，semisolate在收取响应后解析strace，再返回外层计时；因此aggregate phase wall不能视为纯模型运行时长。主用量观测下界5,215,940、replay=0，最终用量不完整。原始result、worker stderr中的exec进程创建错误和1383秒值均保留，不补造账单或改写分类。结果 SHA256 ffc71471b0cb25d7c29cb63a77bd5255efbc1a7bdeb087fb9c42e41884a44032。

所有存活任务自然结束后，仅对该Clean记录做单次证据复核和同任务零模型检查，记录 maintenance/trace-timeout-review-20261105T084210Z/audit.json；未扩大自动900–930秒判定、未改冻结执行器或模型时限。既有监控随后独立复核crash_no_rollback并通过其零模型检查，记录 maintenance/automatic-pending-continuation-20261105T084217Z/audit.json。16:42恢复待跑，578 completed（542 success）、8 infrastructure_error、8 running、1206 pending，stop_reason=null。16:42:36完整直接审计无问题。用户持续监控要求仍有效，继续会话内分钟汇报，不能以恢复八路为完成。


### 2026-11-05 17:25 北京时间：Sympy 13437 注册超时，保留终态继续待跑

sympy__sympy-13437/crash_no_rollback 于17:19:23达到注册900秒模型时限，rc124、official_model_wall_s=902.771173，未完成/未提交、无host leak，模型/源码/上下文身份匹配；stderr同时保留write_stdin Unknown process id 715诊断。原始结果 SHA256 2c6a0547e49ea655640ed3f2d246b23cfeefdb04c30bd9c5a3085ef1a56635f8。主用量观测下界2,049,400、replay=0，最终主用量不完整，raw main=0保持但不视为零成本。

其余任务自然收尾到652 completed、9 infrastructure_error、1139 pending。同任务零模型检查通过，既有监控按原严格规则恢复待跑，记录 maintenance/automatic-pending-continuation-20261105T092431Z/audit.json。17:25八路、1131 pending、stop_reason=null；未改冻结源码、模型/时限/上下文或已有终态，没有终态重跑。持续会话内监控和分钟汇报要求仍有效。


### 2026-11-05 18:43 北京时间：五项注册超时保留后恢复待跑

前台从18:23开始只读检查两路长等待，进程树显示Codex/code-mode等待工具返回，少量strace增长仅证明工作进程存活，未宣称模型调用仍在推进。18:33起以下五项均达到独立注册的900秒时限，rc124、明确timeout stderr、未完成/未提交、无host leak，身份/上下文/源码匹配，严格自动判定全通过：

| task / policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| django-11815 / whole_branch_abort | 904.306362 | 14,749 | 25,916 |
| matplotlib-25332 / crash_no_rollback | 903.196469 | 141,352 | 0 |
| sympy-13895 / crash_no_rollback | 904.148931 | 2,287,205 | 0 |
| sympy-13895 / whole_branch_abort | 903.472825 | 1,786,107 | 25,962 |
| sympy-13895 / ideal_oracle | 900.154707 | 未采到可用usage事件 | 0 |

五项最终main用量均不完整；Ideal自动审计中的lower_bound=0表示未观测到正下界，不是实际零成本。补充说明 maintenance/automatic-pending-continuation-20261105T104154Z/usage-caveats.json，原始自动审计/结果未改。18:41全部收尾为799 completed、14 infrastructure_error、987 pending；18:42直接完整审计无问题。既有监控逐项完成五个同任务零模型检查，完整哈希/分类/检查证据在该目录audit.json。18:43恢复八路、979 pending、stop_reason=null。冻结运行时、模型/预算/上下文未改，已有终态不重跑；继续在会话内持续监控和分钟汇报。


### 2026-11-05 19:14 北京时间：两项注册超时保留后恢复待跑

持续前台检查先发现 sympy__sympy-13915/crash_no_rollback 用量事件停留在18:49:26；18:54仅导出安全进程/事件元数据，Codex和code-mode进程仍在，最后事件为reasoning，未将strace增长当作模型推进。该单元19:02:55结束，rc124、明确900秒超时、official_model_wall_s=901.14192，结果SHA256 271058025c7f543469cd70901af35939655a9bd555509aa205c54e0f3209f4d3。主用量观测下界193,002，replay=0，最终用量不完整。

最后存活的 scikit-learn__scikit-learn-25500/crash_no_rollback 收尾期间持续有实际模型调用，19:12后同样达到注册时限，rc124、official_model_wall_s=903.247608，结果SHA256 210f3617afd3cc9092f1d529e6d51f0746517a10f7929a1cea6748e728a2ab97。主用量观测下界2,256,194，replay=0，最终用量不完整。两项均未完成/未提交、无host leak，身份/上下文/源码匹配既定严格自动分类；原始main=0不代表实际零成本。

19:13全部排空为843 completed（797 success）、16 infrastructure_error、941 pending。完整直接审计无问题，冻结源码和终态证据保留。既有监控完成两项同任务零模型预检并恢复待跑，审计 maintenance/automatic-pending-continuation-20261105T111339Z/audit.json。19:14:00已核实8 running、933 pending、stop_reason=null；旧dispatch_stopped告警等待下一次监控刷新。未改执行器、实验协议或模型时限，未重跑任何终态。继续会话内持续检查和分钟汇报，不以恢复调度为结束。


### 2026-11-05 19:51 北京时间：三项注册超时保留，继续待跑

持续检查捕获以下三个异常，均为rc124、明确900秒超时、未完成/未提交、无host leak，模型/源码/上下文身份符合既定严格自动判定：

| task / policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| sympy-14024 / crash_no_rollback | 903.217241 | 2,967,832 | 0 |
| matplotlib-26011 / ideal_oracle | 902.351793 | 117,215 | 0 |
| scikit-learn-25638 / whole_branch_abort | 902.313640 | 442,927 | 26,134 |

SymPy超时前仍有模型活动。Matplotlib和scikit-learn whole_branch_abort长等待时仅采集了安全进程字段和事件元数据，Codex/code-mode仍在但工具输出后没有后续事件，未将进程存活或trace增长视为模型推进；证据在 monitor/session-process-investigations.jsonl。新增只读辅助脚本 monitor/session_process_check.py，可按task/policy采集同类元数据，不输出提示/回复/环境或命令参数，不修改冻结实验执行代码。

19:49全部排空为880 completed（833 success）、19 infrastructure_error、901 pending。19:50:08完整审计无问题，源码/终态均保留。三个同任务零模型预检通过且proof哈希匹配，既有监控恢复待跑，审计 maintenance/automatic-pending-continuation-20261105T114958Z/audit.json包含全部结果哈希、用量下界及预检证明。19:50:27核实8 running、893 pending、stop_reason=null。原始main=0仍保留，三项最终主用量均不完整，不能作实际零成本。未重跑终态或修改注册模型/时限/上下文。继续会话内持续监控和分钟汇报。


### 2026-11-05 20:19 北京时间：四项注册超时保留后恢复待跑

20:06持续检查捕获SymPy 14308三项注册900秒超时；20:17最后一个Matplotlib 26020会话同样超时。四项均rc124、明确timeout标记、未完成/未提交、无host leak，身份/源码/上下文符合原严格自动规则：

| task / policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| sympy-14308 / clean_recovery | 905.537239 | 2,928,029 | 0 |
| sympy-14308 / temporal_checkpoint | 903.756440 | 2,892,932 | 25,975 |
| sympy-14308 / causal | 904.187756 | 1,921,805 | 0 |
| matplotlib-26020 / crash_no_rollback | 903.496701 | 64,550 | 0 |

Matplotlib长等待前台安全元数据检查见 monitor/session-process-investigations.jsonl：Codex进程存活，但12:02:53Z工具输出后事件未再更新，未将其宣称为模型持续推进。四项原始main=0与异常终态均保留，实际最终主用量不完整，不视为零成本。20:17:52完整审计无问题，895 completed（847 success）、23 infrastructure_error、882 pending，冻结源码和终态证据保持一致。

既有监控完成四项同任务零模型预检，逐份验证proof有效、model_calls=0、恢复/保留语义正确且哈希匹配，审计 maintenance/automatic-pending-continuation-20261105T121723Z/audit.json。20:18:55已核实8 running、874 pending、stop_reason=null。未重跑终态、修改注册模型/时限/上下文或冻结执行器；继续会话内持续监控和分钟汇报。


### 2026-11-05 21:17 北京时间：完成过千，继续运行

20:47:30完整审计覆盖950 completed（898 success）、23 infrastructure_error；21:17:16审计覆盖1006 completed（954 success）、23 infrastructure_error、8 running、763 pending，两次均issues为空、冻结源码未变、终态证据保留，磁盘剩余867.5 GiB。20:18续跑后已新增111个completed，无新增基础设施异常。持续只读采集模型活动并按分钟在会话内汇报；监控与实验服务active，stop_reason=null，无告警。此状态仍非全量完成，继续监控到1800个单元全部终态。


### 2026-11-05 21:57 北京时间：三项注册超时保留后继续待跑

持续前台检查捕获以下三项注册900秒超时，均rc124、明确timeout标记、未完成/未提交、无host leak，身份/源码/上下文匹配原严格自动判定。超时前采样仍有模型活动，未擅自缩短或放宽时限。

| task / policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| sympy-16281 / clean_recovery | 904.999716 | 1,473,387 | 0 |
| sympy-16281 / ideal_oracle | 903.729277 | 1,536,843 | 0 |
| sympy-16503 / crash_no_rollback | 903.132672 | 2,391,850 | 0 |

21:56排空为1048 completed（993 success）、26 infrastructure_error、726 pending。21:56:47完整审计无问题，冻结源码未变、终态证据保留，磁盘868.0 GiB。原始main=0保持，三项实际最终用量不完整，不能视为零成本。既有监控完成三个同任务零模型预检，proof语义和哈希逐份核验通过；全部结果哈希和证据见 maintenance/automatic-pending-continuation-20261105T135630Z/audit.json。21:57:11已核实8 running、718 pending、stop_reason=null。未重跑终态或修改冻结执行器/模型/时限/上下文。继续会话内持续监控及分钟汇报。


### 2026-11-05 22:44 北京时间：SymPy 17630 注册超时保留，恢复待跑

sympy__sympy-17630/crash_no_rollback 达到注册900秒时限，rc124、明确timeout标记、official_model_wall_s=903.889880，未完成/未提交、无host leak，身份/源码/上下文匹配原严格自动规则。结果SHA256 43783ee00a6b2da7d3115d7de00b58acecb6d7968a4b4283f49f3568717aa7bf；主用量观测下界2,616,137、replay=0，最终主用量不完整，原始main=0不代表实际零成本。其余会话自然收尾；最后Django会话在等待期间有真实模型活动。

排空后1125 completed（1066 success）、27 infrastructure_error、648 pending。既有监控完成同题零模型预检并恢复待跑，证明语义和哈希匹配，记录 maintenance/automatic-pending-continuation-20261105T144402Z/audit.json。22:44:13核实8 running、640 pending、stop_reason=null；22:44:31完整审计issues为空，冻结源码与终态证据一致，磁盘867.3 GiB。原始异常与费用未知状态保留，无终态重跑或实验协议改动，继续会话内持续监控和分钟汇报。


### 2026-11-05 23:22 北京时间：SymPy 18199 注册超时保留，继续待跑

sympy__sympy-18199/crash_no_rollback 达到注册900秒模型时限，rc124、明确timeout标记、official_model_wall_s=905.758479，未完成/未提交、无host leak，身份/源码/上下文符合既定严格自动判定。结果SHA256 d32e49afabe9ca2dd53c17a65c8a69edb9ca7eba38c491aed11735f5d81d9e4a；主用量观测下界2,612,033、replay=0，最终主用量不完整，原始main=0不视为实际零成本。其余任务自然收尾至1185 completed、28 infrastructure_error、587 pending。

同题零模型预检通过且proof哈希匹配，既有监控恢复pending，审计 maintenance/automatic-pending-continuation-20261105T152117Z/audit.json。23:21:29核实8 running、579 pending、stop_reason=null；23:22:31完整审计覆盖1186 completed（1122 success）、28 infrastructure_error、8 running、578 pending，issues为空、冻结源码和终态证据一致，磁盘867.1 GiB。终态不重跑，注册协议不变，继续会话内持续监控和分钟汇报。


### 2026-11-06 00:06 北京时间：SymPy 19254三项注册超时保留后恢复待跑

三项均达到注册900秒模型时限，rc124、明确timeout标记、未完成/未提交、无host leak，身份/源码/上下文符合原严格自动判定：

| policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| crash_no_rollback | 902.789508 | 1,571,889 | 0 |
| whole_branch_abort | 901.668707 | 897,480 | 26,022 |
| ideal_oracle | 901.656243 | 952,396 | 0 |

Ideal会话长等待后曾恢复调用，23:57安全进程/事件元数据证明确有新事件；后来仍在注册时限结束，未擅改时限或重跑。三项原始main=0保留，最终主用量均不完整，不视为实际零成本。最后Django会话自然完成，排空为1251 completed（1186 success）、31 infrastructure_error、518 pending。

既有监控完成三个同题零模型预检，逐份语义与哈希核验通过，完整结果哈希/分类/用量下界见 maintenance/automatic-pending-continuation-20261105T160535Z/audit.json。00:06:14核实8 running、510 pending、stop_reason=null；00:06:34完整审计issues为空，冻结源码和既有终态保持一致，磁盘866.9 GiB。未重跑终态或修改注册协议，继续会话内持续监控与分钟汇报。


### 2026-11-06 00:40 北京时间：SymPy 20322 注册超时保留，恢复待跑

sympy__sympy-20322/crash_no_rollback 达到注册900秒时限，rc124、明确timeout标记、official_model_wall_s=904.348050，未完成/未提交、无host leak，身份/源码/上下文匹配原严格自动判定。结果SHA256 97c7e31ab665eddc562a69a43b3887d54f1c5438ddee8eda5f8d684c2cc50920；主用量观测下界2,594,016、replay=0，最终主用量不完整，原始main=0不代表零成本。

其余任务自然完成，排空为1319 completed（1254 success）、32 infrastructure_error、449 pending。同题零模型预检语义与哈希通过，既有监控恢复待跑，审计 maintenance/automatic-pending-continuation-20261105T163903Z/audit.json。00:39:45核实8 running、441 pending、stop_reason=null；00:40:05完整审计无问题，冻结源码和终态证据一致，磁盘866.7 GiB。未重跑终态或改动实验协议，继续会话内持续监控和分钟汇报。


### 2026-11-06 01:37 北京时间：SymPy 21627五项注册超时保留，恢复待跑

持续前台检查捕获同题五项注册900秒超时，均rc124、明确timeout标记、未完成/未提交、无host leak，模型/源码/上下文身份符合原严格自动判定：

| policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| clean_recovery | 907.883006 | 6,296,695 | 0 |
| crash_no_rollback | 904.593266 | 4,108,690 | 0 |
| temporal_checkpoint | 904.309620 | 3,692,813 | 26,184 |
| whole_branch_abort | 905.523208 | 2,045,808 | 25,869 |
| causal | 903.280064 | 2,948,489 | 0 |

超时前采样仍有实际模型活动。原始main=0与异常终态保留，五项最终主用量均不完整，观测下界不是完整费用，不视为零成本。全部自然收尾后为1408 completed（1341 success）、37 infrastructure_error、355 pending；01:35:29完整审计无问题。

既有监控完成五个同题零模型预检，逐份valid/model_calls=0/源码恢复/独立文档保留/派生物删除及proof哈希均核验通过，完整结果哈希、分类和用量证据见 maintenance/automatic-pending-continuation-20261105T173525Z/audit.json。01:36:22恢复8 running、347 pending、stop_reason=null；01:36:46完整审计issues为空，冻结源码和既有终态一致，磁盘866.3 GiB。未重跑终态或改动注册协议，继续会话内持续监控和分钟汇报，不以恢复调度为结束。


### 2026-11-06 01:46 北京时间：Django 14411 Causal响应不确定，保留后续跑

01:39持续检查捕获 django__django-14411/causal 基础设施异常：harness rc127、official_model_wall_s=113.636866，明确 `_WorkerCommandUncertain: try worker failed after dispatch; refusing automatic replay`，未完成/未提交、无host leak，符合既有known_worker_response_failure规则；不是注册超时，具体worker底层故障根因未确定。结果SHA256 a86008bb67c7c23a069ed08992fa5ea482e855d699d175af17a8327a14a72afe。主用量观测下界61,669、replay=0，最终主用量未知，原始main=0保留且不视为零成本。

四个在途任务均自然完成，排空为1418 completed（1351 success）、38 infrastructure_error、344 pending。既有监控完成同题零模型预检，valid/model_calls=0/源码恢复/独立文档保留/派生物删除和proof哈希全部核验通过，审计 maintenance/automatic-pending-continuation-20261105T174459Z/audit.json。01:45:35核实恢复8 running、336 pending、stop_reason=null；01:45:46完整审计issues为空、冻结源码和终态证据一致，磁盘866.3 GiB。仅续跑pending，不重试终态，不改注册协议。继续会话内持续监控和分钟汇报。


### 2026-11-06 02:40 北京时间：SymPy 24102三项注册超时保留后继续待跑

持续检查捕获同题三项注册900秒超时，均rc124、明确timeout标记、未完成/未提交、无host leak，身份/源码/上下文符合既定严格分类：

| policy | aggregate model phase wall(s) | 主用量观测下界 | 已知 replay |
|---|---:|---:|---:|
| clean_recovery | 906.104507 | 1,971,017 | 0 |
| crash_no_rollback | 904.338421 | 2,527,566 | 0 |
| temporal_checkpoint | 903.021471 | 1,599,306 | 38,742 |

Checkpoint较长工具等待后恢复调用；超时前有实际新事件。Ideal最后自然完成，未扩大模型时限。三项原始main=0及异常终态保留，最终主用量均不完整，不将观测下界或原始零值当作完整费用。排空为1511 completed（1441 success）、41 infrastructure_error、248 pending。

既有监控完成三个同题零模型预检，valid/model_calls=0/源码恢复/独立文档保留/派生物删除和proof哈希逐份核验通过，完整分类/结果哈希/用量证据见 maintenance/automatic-pending-continuation-20261105T183921Z/audit.json。02:39:57核实恢复8 running、240 pending、stop_reason=null；02:40:09完整审计issues为空，冻结源码与终态一致，磁盘866.1 GiB。未重跑终态或修改注册协议，继续会话内持续监控与分钟汇报。


### 2026-11-06 03:15 北京时间：Django 15202不回滚注册超时，保留未知用量后续跑

持续采样发现 django__django-15202/crash_no_rollback 无可用用量事件，02:59安全进程/事件元数据检查显示Codex仍在、最后事件02:56:06；未把进程存活或trace增长当作模型推进。该会话最终达到注册900秒时限，rc124、明确timeout标记、official_model_wall_s=900.196022，未完成/未提交、无host leak，身份/源码/上下文符合原严格自动规则。结果SHA256 4e862a42e235ef21ac4a8b702dc2220b8290ffe92b8a217cfa25b9fc5963803d。

原始main=0保留，自动审计observed_session_token_lower_bound=0表示没有可用事件提供正下界，实际最终主用量未知，不能认为零成本；同目录usage-caveats.json补充说明且未改自动审计/原结果。其余任务自然排空至1576 completed（1496 success）、42 infrastructure_error、182 pending。

既有监控完成同题零模型预检，valid/model_calls=0/源码恢复/独立文档保留/派生物删除及proof哈希核验通过，审计 maintenance/automatic-pending-continuation-20261105T191452Z/audit.json。03:15:10核实恢复8 running、174 pending、stop_reason=null；03:15:15完整审计issues为空，冻结源码与终态一致，磁盘866.0 GiB。无终态重跑或注册协议改动，继续会话内持续监控和分钟汇报。


### 2026-11-06 03:46 北京时间：完成1632个，完整审计通过

03:45:40完整审计覆盖1632 completed（1550 success）、42 infrastructure_error、8 running、118 pending，issues为空，冻结源码和终态证据保持一致，磁盘865.6 GiB。自03:15续跑新增56个completed，无新增基础设施异常。监控与实验服务active、stop_reason=null、无告警，持续采样模型活动及会话内分钟汇报；剩余126个未到终态，继续监控到全部1800单元结束。


### 2026-11-06 04:44 北京时间：1800单元全部结束，最终审计通过

04:44:07全部到终态：1758 completed，其中1671 success、87未成功；42 infrastructure_error，running/pending均为0。04:44:26完整审计issues为空，冻结源码与终态证据一致；04:44:27监控更新finished_with_infrastructure_errors，无告警。实验和监控服务均自动正常退出，Result=success、ExecMainStatus=0。失败和异常未重跑，费用未知状态保留，formal_eligible=false。最终分策略结果见 [收尾记录](CODEX_SWE_FULL_RECOVERY_FINAL_20261106.md)，机器汇总与证据哈希见批次monitor/session-final-summary.json。本轮持续监控目标已完成，无需继续轮询已结束队列。
