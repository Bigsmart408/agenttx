# Selected effects 真实任务全量实验

批次：`selected-effects-full-20261116-r1`。2026-11-16 北京时间 11:29 启动。

| Benchmark | Agent | 任务单元 |
|---|---|---:|
| SWE-Bench Lite | SWE-agent | 300 |
| SWE-Bench Lite | Codex | 300 |
| Terminal-Bench | SWE-agent | 241 |
| Terminal-Bench | Codex | 241 |
| 合计 | | 1,082 |

论文显示名为 **Selected effects**，内部策略 ID 为 `root_only`。控制器获得注入时选定的准确根调用，只执行 `rollback_exact([root])`，不定位、不扩展依赖、不清理依赖后效。根调用的文件修改与会话片段被撤销；依赖调用及派生产物继续保留，由真实 agent 在完成原任务时处理。控制器提示不向模型透露根调用或故障路径。这个对照因而是获得准确选中调用的控制，并非额外测量定位效果。

模型、任务、预算和故障种子沿用现有全量真实任务实验：`gpt-5.6-luna`、reasoning effort `max`、上下文 `full`；SWE 模型会话 900 秒，TB 3,600 秒、验收 1,800 秒；种子分别 `20261018` / `20261019`。SWE 每单元一次，TB 允许一次普通失败补跑，所有尝试的费用累计保留。基础设施异常保留原记录，不填零、不作为普通解题失败，也不自动重试终态单元。已知服务异常恢复后只续跑 pending。

固定代码和配置后，真实 SWE 注入预检，以及 TB 的 `hello-world`（root）、`privilege-escalation`（原任务用户 alice）双 agent 预检均通过，外部模型调用为零。专门验证了：仅根调用被撤销、原文件恢复、独立文档保留、依赖调用会话保留、派生产物仍存在、交接证书如实描述残留状态。证书通过不表示故障后效已被完全清理；最终任务成功仍须通过原官方验收及恢复合约。

先执行四个组合各一个真实模型 canary，放行依据是执行路径和用量记录有效，不要求解题成功；之后自动执行完整队列。最多四路并发，其中 TB 最多两路。

结果根目录：
`/var/tmp/agenttx-selected-effects/results/selected-effects-full-20261116-r1/`

- `registration.json`、`source-manifest.json`、`project/`：注册范围、参数、哈希和冻结源码。
- `preflight/proof.json`、`preflight/*`：零模型语义与原用户环境验证。
- `canary-proof.json`：四个组合真实模型运行有效后生成的放行证据。
- `cells.json`、`progress.json`：每单元状态、所有尝试、并发、磁盘与停止原因。
- `cells/<benchmark>/<agent>/<task>/attempt-*`：原始执行、模型、用量、回滚账本和验收证据。
- `supervisor/latest.json`、`supervisor/history.jsonl`、`monitor/latest.json`：每分钟持久监控，核验所有已完成结果哈希；未知异常等待检查。

协调服务：`agenttx-selected-effects-full-20261116-r1.service`。
监控服务：`agenttx-selected-effects-full-20261116-r1-monitor.service`。

归档盘 `/mnt/recovery-sdc` 以 `ro,norecovery` 挂载，本次没有更改其挂载状态。新批次使用本机可写盘，并在新的 TB 尝试结束且结果落盘后，仅释放该尝试一次性的 OverlayFS 文件内容及包安装副本。完整模型日志、最终结果、用量、详细账本、会话元数据保留；原生 Codex 记录逐文件复制验哈希后保留原路径，原始系统调用日志无损压缩并验哈希。每次处理记录 `session-retention.json`。释放后的文件系统现场不能再次恢复，不能称为保留完整快照。旧实验不在清理范围。

输出盘低于 25 GiB 或 Docker 数据盘低于 15 GiB 时停止派发，现有任务继续收尾。该阈值是本批新注册设置；旧批次的配置不变。

实现入口：`experiments/scripts/run_selected_effects_full.py`、`launch_selected_effects_full.py`、`retain_selected_effects_session.py`、`scripts/maintenance/supervise_selected_effects_full.py`。启动审计位于 `review/selected-effects-full-20261116/launch-audit.json`。

本批正在运行，尚未将任何未完成结果计入论文。

## 首轮放行

北京时间 11:34，四个组合的真实模型 canary 均完成且执行、用量记录有效，全量队列已自动放行。TB 两个 agent 均通过联合验收；SWE/Codex 通过官方测试但未清理派生产物，按预定合约保留为任务失败，不重跑、不修改记录。SWE/SWE-agent 也未满足联合验收，原始失败结果保留。放行后保持四路运行，1,074 个单元待跑，无基础设施异常或停派原因。
