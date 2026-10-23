# 修复后先验证趋势，再考虑全量

本记录执行用户最新要求，取代先前“修复后直接续全量”的安排。北京时间2026-10-21 14:04暂停r14派发，14:09完成在途任务回收；全量服务现已停止。原始结果、失败及用量未知状态保留。

## 固定验证批次

批次：`/mnt/data/agenttx-runtime/validation/tb-trend-validation-20261021-r1`。

代码已冻结，来源、镜像环境清单和SHA256均登记。验证使用已通过host及原任务容器回归的v7交接/路径/验收输出修复；暂停与判定新增36项测试通过。本轮不修改官方测试、故障量或模型，也不使用参考解。

预先固定五题：`hello-world`、`csv-to-parquet`、`build-stp`、`build-tcc-qemu`、`catch-me-if-you-can`。其中两个build任务覆盖已发现的高成本案例，不删除高值。两个harness各跑六策略，共60单元，每单元一次，不根据结果更换题目或重试。该批为单独诊断验证，不补入全量或改写旧结果。Causal/Ideal/Clean为compact，其余full；最多8路，旧全量在途任务结束后才开始。

服务：`agenttx-tb-trend-validation-20261021-r1.service`。14:10:32启动，最初8运行、52待验证；初次进度复核已完成12/60，12条全部通过官方及恢复检查；另8条运行，40条待验证。实时状态看该批`progress.json`，判定看`trend-gate.json`。当前没有完成全部验证，不能宣称趋势达标。

## 预先登记的判定标准

- 所有60单元必须结束，且没有环境/用量/上下文异常；失败与未知用量全部保留，未知成本不补零。
- 分别与不回滚、checkpoint、whole比较。每个对照使用同题、Codex与SWE-agent四方成功的共同交集，至少3题，避免两个harness拿不同题目作趋势比较。
- 对每个对照，两套harness都要求Causal含main+replay的总token更少，逐题节省率中位数不低于0；完整五题中Causal成功数不低于对应对照。
- 同时报告每组平均token、总量、逐题中位数、成功数与配对题目。Clean/Ideal全保留为参照，不强行要求随机求解结果逐题严格排序。

这些是运行全量前的诊断门槛，并不是已观测到的结果，也不能保证全量排名。门槛不通过就继续查原因，不能通过删失败、删高值、重复运行或事后调整门槛来取得想要的趋势。

## 全量暂停保护

`/mnt/data/agenttx-runtime/validation/tb-full-dispatch-hold.json`已记录暂停。新全量启动、自动续跑入口及派发循环都会检查该文件；只有固定验证允许运行。验证程序不会删除暂停记录或自动启动全量，即便自己的判定通过也先保存完整报告。全量是否满足恢复条件需核对全部验证证据。

现有监控每30秒同时报告`full_dispatch_held`与`trend_validation`，不会把旧全量结束和验证活动混为一谈。

## 新发现的服务异常

r14的conda任务中，三个Codex单元真实终止事件包含网关`/v1/responses`的HTTP404；whole已执行大量工具后中断，Causal/Ideal也出现同端点错误。不能把这些失败直接当成策略劣化或0token。14:06对当前注册模型和原地址的小请求，Responses及Chat Completions都已返回200；这是即时可用证据，不表示此前错误不存在或网关永久稳定。固定验证遇到未知服务/用量错误会停止继续派发并保留证据。

另外两个在途configure-git-webserver任务最终容器退出137，未产生完整模型结果，均按基础设施异常保留；暂未确认具体退出原因，不能归因于内存或任务能力。全量最终保留473完整、33异常、2386待跑。

为核对Codex接口要求读取了OpenAI Docs技能；官方配置参考页抓取返回403，未以未读到的文档作结论。上述404/200依据是本机保存的真实网关响应及模型原始事件。

## 证据

- `/mnt/data/agenttx-runtime/validation/tb-user-validation-hold-20261021-r1/`：暂停前progress、注册、在途回收记录及原结果哈希。
- 同目录`provider-404-evidence.json`、`gateway-probe.json`：网关历史失败及当前小请求结果。诊断API请求不并入任务效果成本，不能把用量缺失解释成零。
- 同目录`hold-and-trend-tests.log`：36项暂停/判定/原生接口回归。
- 验证目录`registration.json`、`source-manifest.json`、`progress.json`、`trend-gate.json`：固定任务及预设标准、冻结源码、真实进度、完整判定。
- 先前修复证据：[代码排查与修复](TB_CODE_REGRESSION_AUDIT_20261021.md)。

## 14:34阶段判断

已完成25/60，25条均通过官方及恢复检查，用量完整；8运行、27待验证，无新基础设施异常。仅hello-world和csv-to-parquet形成双harness共同成功配对，未达到预设至少3题且全60结束的门槛，全量保持暂停。

含main+replay的两题配对总token节省：Codex对不回滚−25.48%、checkpoint87.35%、whole76.99%；SWE-agent对应16.58%、39.59%、51.23%。Codex对不回滚目前比对照更贵，不能声称趋势全面一致。Codex此交集Causal410576 tokens、不回滚327195；csv题Causal341683、不回滚230815，是当前反向的主要来源。

检查csv题模型原始工具命令：Causal25次、不回滚11次；未再次查找内部auto-localization verifier。checkpoint主会话3117878+replay38631，whole主会话1642922+replay25872，其中有大量运行时/安装探查。因此当前对粗粒度组的总量优势也受单题长尾影响，不能只看总量就宣布实现问题全部解决。保持固定样本、保留所有结果，不修改在途代码以追求通过。证据：验证批次review-20261021T1434.json及各单元model-result.json。

## 15:12阶段判断

完成59/60，58成功、1失败（catch-me-if-you-can/SWE-agent/checkpoint），无新增基础设施异常。Causal双harness均5/5成功。对不回滚、whole有5个共同成功题，对checkpoint为4题；全体三个对照的双harness总量与逐题中位数节省均为正，成功数要求也满足。Codex含replay总token节省分别32.08%、44.34%、29.40%；SWE-agent分别16.34%、14.17%、54.22%。Codex中位数分别28.52%、21.24%、40.29%；SWE-agent分别7.64%、22.48%、51.85%。此前对不回滚的反向总趋势已在固定五题中转正，仍保留CSV单题的劣势。

最后一条为build-tcc-qemu/SWE-agent/Clean：容器仍存活，真实wget子进程在Savannah源码下载处等待约27分钟，日志最后动作14:45；未篡改在途任务或提前改变已登记期限。它不改变已完成的三个主对照结果，但整个60单元的完成门槛尚未满足，gate_passed仍false，全量保持暂停。数据盘358GiB，接近原350GiB派发保护线，恢复全量前还需核验容量。证据：review-20261021T1512.json、trend-gate.json及实际容器进程。
