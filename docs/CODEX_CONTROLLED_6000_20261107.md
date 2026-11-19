# Codex 6,000 次受控恢复实验

按用户要求补充 Codex：400 个原始受控案例 × 五种策略 × 三次重复，共 6,000 次主实验。五种策略为 AgentTX、no rollback、turn checkpoint、temporal checkpoint、whole-branch rollback，各 1,200 次。

原始输入、产物拼接语义、故障计划、恢复运行时、公开检查器、最终验证和命令审计沿用已完成 SWE-agent 批次。逐一核对 400 个 manifest hash；参考冻结源的 472 个文件中，471 个保持一致，仅编排器调整 Codex 入口和模型试跑门槛，另外添加三个独立的 Codex 记账与启动文件。冻结源和变更清单保存在结果目录。

使用相同镜像 `sha256:22feb19c04e4b8e8439ecafdfa0cba6430c415e0a8edea86c10e706a2646fed7`、`gpt-5.6-luna`、maximum reasoning、full context、每次 3,600 秒及 8,000,000 API token 上限，最多八个并行单元。模型 profile 逐文件 hash 与参考批次一致。

Codex 保留原生执行循环和恢复后任务提示。独立的本地 Responses 转发器记录每次提供方返回的 input/output/total tokens，在请求前预留输入字节与 framing 额度并限制输出。缺少提供方用量时保持未知并阻止继续请求，不用文本估算替代；响应断流、预算与基础设施异常保留原始结果。已结束单元的实际成本包含失败运行；后续策略比较按相同 case/repeat 配对。超时、审计失败等真实结果不因收益方向重跑。

先执行 60 项无模型运行时预检和五项短案例模型试跑，运行时全部通过、模型试跑无基础设施或记账异常后进入主矩阵；预检与试跑独立保存，不占主实验分母。四项离线检查覆盖流式实际用量、请求前预算阻断、未知用量保留及真实 Codex CLI 的本地端点路由，均通过；本地请求还验证仅转发一个 Content-Type。

## 运行入口

- 结果：`/home/bfq/agenttx-workspace/agenttx-experiment-data/results/system-micro-codex-6000-20261107-r4/`
- 冻结源：`/home/bfq/agenttx-workspace/agenttx-executions/system-micro-codex-6000-20261107-r4/project/`
- 实验服务：`agenttx-system-micro-codex-20261107-r4.service`
- 监控服务：`agenttx-system-micro-codex-monitor-20261107-r4.service`
- 进度：结果目录 `progress.json`、`cells.json`。
- 最新报告：结果目录 `monitor/latest-report.md`。
- 每分钟快照：`monitor/latest.json`、`monitor/samples.jsonl`。
- 五分钟报告历史：`monitor/reports.jsonl`。

监控每 60 秒核对主进程、准确匹配的 worker 容器、日志活跃度、磁盘、实际用量完整性和预算；每 300 秒及阶段/告警变化时生成报告。监控不调用模型。结果和 Docker 使用同一块大容量文件系统，启动时约余 2.55 TiB。低于 100 GiB 停止新增派发，原始结果继续保留。

注册与真实终态以结果文件为准。全部结束后核验 400 个输入、每策略 1,200 次覆盖、故障与任务提示一致性、API 记账及全部失败分母，再汇总正式结果。

## 启动修订

首轮 r1 的 60 项运行时预检全部通过；模型试跑发现 wrapper 对旧版不可变结果对象访问不存在的 metadata 字段，以及 Codex Docker 文件挂载未声明进现有 try 只读挂载列表。该轮无正式单元派发、无转发的 API 请求，全部原始证据保留。r2 移除多余字段访问，并在编排器声明 Codex 两个不可变可执行文件；恢复代码与实验输入不变。修订后在相同镜像和 try namespace 中运行 Codex --version，返回 0、版本 0.163.0。完整预检和模型试跑重新执行，修订记录见结果目录 startup-revision.json。

当前批次为 r4。r2 独立试跑完成任务和命令审计，但模型 CLI 的 exec 配置覆盖了顶层 endpoint override，转发器未收到请求；保留原生 harness 报告的 212,928 tokens，并将提供方逐请求用量标为未知。通过两个本地 HTTP 端点复现并修复参数层级问题，未调用外部模型。r3 则因 Content-Type 大小写键重复收到 HTTP 400，未生成有效模型输出。r4 统一请求头并在 exec 级注入 endpoint，独立试跑已实际捕获多次 HTTP 200 的提供方用量。正式实验仍独立于以上检查，启动修订与所有成本证据保留。

模型试跑只核验 harness 和记账链路。r4 独立短案例的 15 次实际请求共计 229,247 tokens，产物检查通过；一个模型生成的错误命令导致严格命令审计失败，按真实任务失败保留，不重跑、不放宽审计。正式派发前记录 canary-readiness-rule.json：若模型 canary 仅有类似真实任务失败，人工核验记账、原生进程及独立检查器证据后恢复编排；基础设施错误仍必须修复后重新注册。此规则未改变正式 6,000 次中的失败计数。

## 正式运行确认

北京时间 2026-11-07 13:57 已进入正式主实验，8 路并行。r4 的 60 项运行时预检及五种策略模型 canary 全部通过，未使用 canary 例外放行；实际 token、命令审计、产物检查与二进制 hash 已逐项核验，证据见 canary-verification.json。启动后的主实验样本再次独立核对注册 manifest、二进制 hash 与每次实际 API 用量之和，见 startup-health-verification.json。进展始终以 monitor/latest-report.md 和 progress.json 的实时内容为准。后台监控使用相同专用 Docker socket，每分钟巡检、每五分钟生成报告，不依赖当前聊天会话。

## 2026-11-07 上游断流恢复

北京时间 19:04:48，多个上游请求同时断开，六个请求没有最终 usage；本地保护器阻止未知用量之后继续请求，编排器在排空其余 worker 后于 19:09 停止。保留 556 次完整结果（529 通过、27 命令审计失败），后者最终产物验证均通过，未重跑。六次中断尝试独立归档在 recovery/ 下：已知 11,096,029 tokens、六个请求用量未知，未按零补记。原始 result、会话目录、日志和停止前 cells/progress/registration/execution 快照全部保留。

上游接口恢复后，新的短案例试跑通过，真实 usage 为 93,082 tokens。核对冻结源、原模型 profile 和二进制 hash 均未变，于北京时间 21:00 恢复原 6,000 个逻辑单元的 8 路执行；仅六个基础设施中断单元重试，已完成 556 行逐一核对保持一致。恢复核验见 recovery/*/verification.json。

新增独立恢复守护服务 agenttx-system-micro-codex-recovery-20261107-r4.service，每分钟巡检。仅对确认的上游断流/临时 HTTP 错误，在等待至少 300 秒、重新核对环境和接口可用后归档并重试，每个逻辑单元最多两次基础设施重试。真实模型/命令审计失败与预算耗尽不重试；超出限制或其他异常交由审查，监控报告保留告警。规则见 infrastructure-retry-policy.json，原始 ordinary_retries=0 保持。监控同时显示归档尝试的已知与未知用量；五项恢复、证据保存与成本保留检查通过。

## 完成与合并（2026-11-10）

2026-11-10 01:56:27 UTC 完成全部 6,000 次正式执行，5,645 次通过、355 次真实任务失败；最终结果没有基础设施错误，实际 API 用量 8,461,019,162 tokens，6,000/6,000 记账完整。355 次失败均通过产物检查，但未通过修复命令审计，按真实失败保留。27 次基础设施中断尝试独立归档：已知 31,553,103 tokens，27 个请求用量未知。

与 SWE-agent 的 6,000 次结果按同一组 400 个 manifest 和相同任务提示合并，逐项核验冻结源、原始结果 hash、最终检查与请求级用量。12,000 次中 11,618 次通过、382 次任务失败。AgentTX 的 2,400 次恢复状态检查全部通过，最终任务与命令检查通过 2,345 次。相对 Turn checkpoint、Temporal checkpoint 和 Whole branch，合并 token 节省分别为 67.97%、72.52%、81.23%，总耗时降低分别为 16.44%、23.59%、53.68%。36 次 SWE-agent 上下文消融保持单独统计。

复核入口：`experiments/scripts/analyze_controlled_both_agents.py`。完整导出、分 agent 与合并统计、失败列表、源码及重试审计、图表证据和更新前备份位于 `review/controlled-both-agents-20261110/`；中文汇总见其中 `summary.zh-CN.md`。
