# 实验推进记录：2026-10-17

**本机执行更新（2026-10-17）：** 代码修改、环境准备、核验和后续实验均在本机 `r760-ssd`；远程仅供参考代码，远程队列已停止。镜像及实验数据使用 `/mnt/data/agenttx-runtime`，模型实验继续暂停。此前远程环境就绪数量不计作本机进度。最新状态见 [本机环境准备](DOCKER_ENVIRONMENT_20261017.md)。

**最新指令：先补环境，再起实验。** Docker 的国内镜像、代理清理与实际拉取/build 验证已完成；当前只运行环境准备。 本轮已停止，保留 60 个通过、8 个中断的运行时单元，16 个运行时单元和 168 个模型单元延期；没有模型单元启动，也没有自动重启。下文先前“运行中”等内容属于启动时记录，以 [Docker 与环境准备](DOCKER_ENVIRONMENT_20261017.md) 和 [暂停快照](STRATIFIED_PILOT_20261017.md) 为准。

当前执行：新的[分层 pilot](STRATIFIED_PILOT_20261017.md) 已在远程启动，最多 **8 路实验并行**；84 个运行时检查全部通过后，自动进入 168 个双框架模型单元。沿用冻结 v5 源码，统一模型时限 900 秒，不自动重试。完整双框架微基准目标已明确为 **16800**，真实任务目标仍为 **5410**；以下较早轮次的“未启动”“没有后台批次”和 8400 无框架清单均保留为历史记录。

当前完整进度与 SWE-agent 缺口见 [实验进度总览](EXPERIMENT_STATUS_20261017.md)：小规模验证已完成，新正式 5410 单元仍未执行；不要把下文历史轮次状态混为全量完成。

最新复跑：固定源码、配置和预算完成 50 次模型尝试，49 次正常通过；一次 SWE-agent / TB causal 因输出格式错误后提前提交而未创建目标文件，官方测试失败，原记录保留。50 次回滚状态字段均与对应旧记录相符，4 次同源码对照均通过；历史 400 案例重新生成的 24000 个确定性值全部一致。token 仍有波动，不能保证每次端到端效果相同；详见 [复现检查报告](REPRODUCIBILITY_20261017.md)。

最新推进：七策略 canonical 运行器及 TB 同容器恢复/验收路径已实现并实跑。29 次调试尝试中 25 次正常通过，1 次预算超时、3 次已定位并修正的集成错误保留；详见 [本轮实现与结果](PROTOCOL_COMPLETION_20261017.md)。下文较早轮次的状态保留作历史，不代表当前仍缺同一入口。

已完成前置修复，并接通 Codex / SWE-agent 的模型接口及真实运行时诊断链：**4 个恢复诊断单元通过，另保留 2 个 Codex 基础设施失败尝试**。正式 84/8400/5410 清单尚未执行；完整七策略协议与正式 TB 镜像内执行仍待完成。最新证据、配置位置和复跑命令见 [模型接入与运行时诊断](RUNTIME_MODEL_PILOT_20261017.md)，实验总计划见 [EXPERIMENT_REMAINING_PLAN.md](EXPERIMENT_REMAINING_PLAN.md)。以下先保留 P0 阶段记录，末尾列出当前缺口。

## 已实施的代码修复

| 项目 | 本轮行为 | 验证重点 |
| --- | --- | --- |
| 独立 oracle | 增加 `AgentTX.rollback_exact`；注入器注册真值集合；ideal 不再查询被测文件系统闭包 | 误报/漏报文件依赖不能改变 oracle 目标；拒绝非法、已提交或失效目标 |
| oracle 的上下文恢复 | 在 `ConversationLog.rewind/invalidate_steps` 支持关闭依赖扩张，oracle 使用完整注册集合直接失效消息 | 远程原改动仍会扩张捕获到的消息边，本轮已补上；误报消息依赖不再删除独立消息 |
| SWE 官方验收 | 固定镜像 ID、探测镜像内 Python、在提示和验证命令中传递版本；容器不拉取、不联网 | 镜像/解释器漂移在验收前拒绝；基础设施异常返回 70，任务失败返回 1 |
| 配置与临时目录 | 注入器和 Codex 使用私有 XDG/Astropy 配置目录；提交前清理已卸载的 temproot | 不跟随 symlink，不清理挂载子树，提交与独立文件保留 |
| 分段计时 | 分开记录 policy、文档重放、模型执行、官方验收时间，失败调用同样计时 | 保留原有 localization、端到端和模型用量字段 |
| 必需检查放行 | 修正 runtime preflight 原来把 required/optional 条件写反的问题 | 无 try/无 strace/执行失败均不能通过；可选 BPF 缺失不阻止 strace 路径 |
| 运行环境检查 | 支持激活的 venv，保留通过 symlink 调用的环境前缀 | 不再把 venv 的 Python 指向基础解释器误判为其他环境 |
| 配置与源码冻结 | coarse 的 context 记录与实际继承一致，causal 定位记录反映真实执行；保存执行源码 manifest，源码变化拒绝旧目录续跑 | 不因扩 worker 放行源码变化；不混合旧批次 |

本地双 harness、SWE-agent 原生历史投影、无默认 count cap、host-projected TB 拒绝保护、SWE test-patch 参数、scratch cleanup、可配置 trace spool 和网络控制均保留。未整体替换为远程版本。

当前 oracle 真值注册适用于现有注入 DAG，**不是完整 400-case 真值接入已经完成的声明**。400-case 的生成器真值仍需在全系统执行链中逐例接入。重复显式根目前仍以异常拒绝并保持状态，尚未满足原规范“成功 no-op”的更强 API 要求；中断恢复安全也未因这些测试而获得证明。

## 验证结果与适用范围

| 验证 | 结果 | 凭证 |
| --- | --- | --- |
| 本地最终针对性单测与消息逻辑测试 | **168 passed，0 failed/error/skipped** | [XML](/home/bfq/agenttx-recovery-20261016/reports/p0-final-local-20261017.xml) |
| 远程真实 try 后端第一轮 | **81 passed**，覆盖恢复、提交、持久化、工作区/上下文、依赖与 symlink 等 | [XML](/home/bfq/agenttx-recovery-20261016/reports/p0-remote-20261017/backend-tests.xml) |
| 消息闭包修复后的最终远程验证 | **41 passed**，包括误报消息边的 oracle、causal/temporal/whole 和上下文恢复 | [XML](/home/bfq/agenttx-recovery-20261016/reports/p0-remote-20261017/final-conversation-tests.xml) |
| 真实官方镜像身份与解释器探测 | Astropy 12907：Python 3.9.20；Django 13220：Python 3.6.13；均按 image ID 断网探测成功 | [镜像记录](/home/bfq/agenttx-recovery-20261016/reports/p0-remote-20261017/pinned-image-probes-available.json) |
| 远程运行时与 SWE 全局基础检查 | try/strace、激活 Python、模块导入和 Docker 通过 | [环境记录](/home/bfq/agenttx-recovery-20261016/reports/p0-remote-20261017/final-environment.json) |

这些是不同验证批次，有重复测试，不能相加当作独立实验样本。81 项对应消息闭包修复前的阶段快照；41 项验证了其后的最终修复。并未运行完整项目 pytest、官方任务解题、全部官方镜像验收，或五阶段 SIGKILL 恢复矩阵。

最初后端测试因未激活 Python 的 PATH，出现 3 个与验证命令无法正确执行有关的失败；激活后重新运行，81 项通过。初始日志与 XML 仍保留于 `reports/p0-remote-20261017/backend-tests-before-pathfix.*`。本地新增测试曾有 CLI 参数错误，修正后的最终结果见上表。以上验证过程没有被写入正式实验统计。

## 环境与来源

本地验证环境为 `/home/bfq/.venvs/agenttx/bin/python`，版本 **3.11.17**；依赖清单见 [freeze](/home/bfq/agenttx-recovery-20261016/reports/p0-python311-validation.freeze.txt)。这是新建的验证环境，不代表旧 conda 及完整 agent 依赖已恢复。Miniforge 安装器校验文件下载超时，未执行安装器，旧 `/home/bfq/miniconda3/envs/agenttx/bin/python3.11` 仍不可用。

本地用户仍无 Docker socket 权限，且用户命名空间映射被系统拒绝，因此实际后端验证放到远程。远程使用独立目录 `/home/bfq/agenttx-validation-20261017-p0` 和独立 Python 3.11.17 环境，未修改原 `/home/bfq/agenttx` 的代码或旧结果。

已从远程补回本地 `third_party/try/` 的 **83 个文件**，逐文件 SHA-256 校验通过；远程基准提交为 `833624c956a4f1a990eb3363dafc88b0dc347c16`，工作区补丁以实际文件哈希为准，不将其冒充纯净 upstream。详见 [依赖来源清单](/home/bfq/agenttx-recovery-20261016/reports/try_dependency_recovery.json)。该依赖本地 Git 元数据尚未重建。

P0 阶段执行源码指纹为 `0ff4325f574e597d66d1b938ff3f6312ee7131efeaaa48918b04a3ad8081c323`。当时本地登记清单与远程最终验证源码指纹一致。原有源码已在修改前备份，该阶段共修改或新增 **22 个源码/测试文件**，不包含文档和待跑 CSV。之后模型接入阶段的独立指纹与变更清单见新诊断记录，不覆盖这份登记。

- [修改清单](/home/bfq/agenttx-recovery-20261016/reports/implementation_changes_20261017.json)：逐文件前后哈希及备份位置。
- [本轮补丁](/home/bfq/agenttx-recovery-20261016/reports/p0-implementation-20261017.patch)：相对本轮开始时工作区的差异，保留此前未提交改动。
- [机器可读进展](/home/bfq/agenttx-recovery-20261016/reports/p0_execution_progress_20261017.json)：验证结果、单元数量与剩余工作。

## 已登记的后续实验

运行 `experiments/scripts/prepare_recovery_campaign.py` 验证全部 400 个注册 manifest，并生成 [登记目录](/home/bfq/agenttx/experiments/plans/recovery_20261017)。所有行均为 `registered_not_run`：

| 清单 | 数量 | 说明 |
| --- | ---: | --- |
| [system_micro_pilot_cells.csv](/home/bfq/agenttx/experiments/plans/recovery_20261017/system_micro_pilot_cells.csv) | **84** | 每个已注册家族按规范顺序选 4 例，共 12 manifest × 7 策略 × 1 次；只用于调试 |
| [system_micro_cells.csv](/home/bfq/agenttx/experiments/plans/recovery_20261017/system_micro_cells.csv) | **8400** | 400 manifest × 7 策略 × 3 次 |
| [official_cells.csv](/home/bfq/agenttx/experiments/plans/recovery_20261017/official_cells.csv) | **5410** | 300 SWE + 241 TB × 5 策略 × 2 harness；任务版本、镜像和预算仍须完整冻结 |

登记信息和文件哈希见 [registration.json](/home/bfq/agenttx/experiments/plans/recovery_20261017/registration.json)。pilot 是完整微基准的调试子集，不是另外 84 个正式样本；这些清单均不是已经执行的结果。

## 启动模型批次前仍需完成

1. **补齐正式完整运行时协议。** 新增 `bench_runtime_recovery_pilot.py` 已通过双 harness 的自动 tracing、回滚、上下文重建、模型修复和独立验收。它使用 digest 产物和脚本生成的 precrash 调度，只覆盖一例的 causal/oracle；尚不能替代 400-case 原产物语义和三条 coarse 策略的自诊断/实际回滚协议。旧 replay 和关闭 tracing 的物化路径仍不当作全系统测量。
2. **模型访问已解决。** 已验证指定接口的 `gpt-5.6-luna / max`，凭据置于本地和远程独立私有目录；两个 harness 均实际写文件成功，SWE 原生历史投影也通过验证。Codex 缺少配套执行程序的两次失败保留，补齐后两次恢复验收通过。
3. **补齐正式任务环境。** 远程独立环境已安装固定来源的 SWE-agent 及依赖，100 项集成回归通过；本地相关测试 79 项通过，两组重叠。原 conda 仍不可用，不以此宣称旧环境完全复现。TB 仍需正式镜像内策略执行路径；两个镜像探测成功不代表全部 541 个任务已就绪。
4. **冻结后再跑。** 为两个 harness 固定模板、策略 payload、故障 schedule、预算、task/image 版本和用量来源；再启动新目录。保留当前 TB guard，不能用 host-projected 旧结果填充新清单。

本次双 harness 诊断批次已结束，当前没有后台模型批次。所有 6 次模型恢复尝试与单独 smoke 记录已归档；诊断通过不能支持全量收益结论。恢复中断、完整语义矩阵、Breakdown 和六类开销仍按总计划排队。
