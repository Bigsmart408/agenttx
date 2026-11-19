# 2026-10-25 关机后续跑

核验时间：2026-10-25 19:13（北京时间）。执行仍在本机 r760-ssd。

用户“继续跑实验吧，恢复”已撤销关机前暂停要求。

- 归档盘：`/dev/sdc /mnt/recovery-sdc ro,relatime,norecovery,stripe=32`。重启后缺失挂载造成 125 条旧结果不可读，挂载后缺失数为0。未编辑 fstab、未迁移或覆盖结果，挂载状态在再次重启后需复核。
- 当前批次：`tb-dual-native-full-20261025-r21-reboot-resume`。首轮继承完整完成1090、异常82，补跑继承完整完成133（64通过、69失败）、异常100。起跑时首轮4路运行/1716待跑，补跑4路运行/93待跑。
- 新调度器：`scripts/maintenance/run_tb_reboot_resume_20261025.py`，systemd用户服务 `agenttx-tb-dual-full-20261025-r21-reboot-resume.service`。不原地重启旧root，不覆盖终态；原有全部主运行、replay、重跑/修复费用累计。
- 冻结执行源码完全复用 r20，manifest SHA256 `e9c026d710dde567911bad20f4f2c6800d702640fa3766b1865dd18201e84568`。没有修改原题、验收或模型配置。Causal/Clean/Ideal compact，另三组full；模型 gpt-5.6-luna/max、8路总并行、3600s主会话/1800s验收/9600s worker，磁盘门槛0。
- 修正调度误判：新尝试只使用明确连接诊断；旧的开放事故时间窗不再把每次未来不完整账单认作网络故障。模型超时且账单不完整仍保留并排除完整成本配对，未知异常继续停派。
- 首轮和重跑公平调度，避免旧故障重跑一直占满8路而首轮完成数不变。已有补跑继续执行，没有只挑成功结果或无限重跑。
- `hf-model-inference`、`hf-train-lora-adapter` 的旧失败/超时仍保留，HF验收出网尚未修好，不自动新增修复重跑；当前待跑队列不含这两题，所以此次没有跳过未开始单元。
- 容器隔离、原API鉴权/DNS、两个harness原镜像零模型hosts/适配器预检均通过；44项针对性单元检查通过。8个实际worker均使用独立网络命名空间，监控无告警，已观察SWE-agent真实模型调用。尚不能将新启动当作验收通过。
- 8080规则全程未改，实际隔离验证确认宿主机NAT检查前后相同。没有本地模型转发/代理。
- 监控服务 `agenttx-tb-dual-monitor-20261025` 每30秒记录状态、半小时保存复核。摘要保留服务 `agenttx-tb-summary-retention-20261025` 使用新数据盘可写、旧归档只读挂载；仅Clean保留完整轨迹，其他保留结果、账单和诊断。

数据盘证据：`validation/tb-api-connection-repair-20261023/isolation-verification.json`、两套 `hosts-baseline-*-verification.json`（当前日期重新验证）；`validation/tb-reboot-resume-20261025/runtime-isolation.json`、`protected-results.json`、`restoration-status.json`、`dispatches.jsonl`。历史结果不改；新续跑结束时检查保护哈希并刷新最终报告，避免最后终态落后一分钟的问题。
