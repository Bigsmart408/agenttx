# SWE worker 总时限中断与续跑

核对：2026-10-19 14:48（北京时间）。

sympy__sympy-24102 / clean_recovery 在11:53启动，12:53达到3600秒worker总时限（实际3601.028秒，退出124），最终result.json未写出。协调器停止新派发，在13:10排空其他worker后结束。此前900秒模型会话限时未有效终止本次会话，属于已记录的计时偏差延续；未声称已修复模型计时器或正式协议合规。

轨迹有67个已完成step，模型统计api_calls=67，日志67份usage总计1,958,229 token。最后一条API请求没有响应，用量未知；所有可核对消耗计入不完整结果账本，不能宣称完整发票对账。原始日志/轨迹保留哈希，未创建虚构最终result或测试判定。

该单元保持infrastructure_error，不重跑。监控仅在退出124、登记总时限3600、实测3600–3635秒、clean策略、无结果文件、单条轨迹、非终态、轨迹step/API调用/日志usage数一致且每条usage算术正确时，允许继续待跑单元。其他未知环境错误仍停止派发。

14:46监控启动新批次 `sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`，继承1605个完成结果和1个异常记录，194个待跑继续执行；已核验8个worker存活。SWE模型执行核心与六策略上下文保持冻结r9；本次只改监控和日志记账。

60项相关回归通过，包含不重试异常项、早退出/错误退出码/缺失usage等拒绝条件及不完整用量隔离。

- 审计：`/mnt/data/agenttx-runtime/validation/swe-worker-wall-timeout-audit-20261019.json`。
- 新批次进度：`/mnt/data/agenttx-runtime/results/sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z/progress.json`。
- API审计：`/mnt/data/agenttx-runtime/validation/swe-api-usage-sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z.json`。

后续实验报告须区分已完成任务失败、异常无最终判定单元和待跑项；不能将该异常改写成正常成功/失败，也不能丢弃其费用。
