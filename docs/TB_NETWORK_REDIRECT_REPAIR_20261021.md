# TB配套任务模型网络修复

**中断已修复并恢复8路，2026-10-21 21:31（北京时间）。** r15在20:15排空停止，118完整（112成功、6任务失败）、7用量不完整异常，2767未启动；触发错误为ancient-puzzle/SWE-agent连接模型接口失败。已在原配套服务网络复现：本机既有NAT PREROUTING将所有目标8080重定向本机18080，host网络正常而bridge容器连接被拒绝。修复仅为自建网桥+子网到10.249.44.127:8080添加带归属comment的PREROUTING例外，随单元结束移除，不删除全局规则。原服务8090连通、认证模型接口HTTP200；15项回归通过。r16于21:30:36启动，125条已有终态完全继承、不重跑或补零，继续2767未启动项，六策略/双harness共8路，Causal/Ideal/Clean compact，其余full，空间停派线仍关闭。已确认ancient-puzzle的SWE Causal与Ideal产生真实API调用及token计数；监控无告警。7异常包括4条Codex会话超时缺最终账单、3条SWE连接错误，不把零计数当零成本纳入配对。当前完整成功配对main+replay总token节省，不回滚/checkpoint/whole/Clean/Ideal：Codex19.71%/17.63%/25.89%/25.20%/11.73%，SWE-agent58.58%/25.58%/58.38%/19.29%/22.99%；各8至10对，小样本。证据：`/mnt/data/agenttx-runtime/validation/tb-ancient-network-repair-20261021-r1`。

此前r15原结果不改写。环境修复改变执行源码哈希，r16登记新冻结源码及续跑来源；模型、题目、提示、策略、时限与官方验收保持原注册配置。
