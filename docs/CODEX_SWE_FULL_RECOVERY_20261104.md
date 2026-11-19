# Codex × SWE-Bench Lite 全量恢复实验

2026-11-05 已恢复待运行队列并安装独立持续监控，见 [监控续跑记录](CODEX_SWE_MONITORED_RESUME_20261105.md) 和 [实时进度](CODEX_SWE_FULL_RECOVERY_PROGRESS_20261104.md)。原基础设施异常及成本记录保留。

## 补齐的分组

主恢复实验已有 SWE/SWE-agent、TB/SWE-agent、TB/Codex，缺少当前协议的 SWE/Codex。历史 Codex/SWE 五策略结果独立保留。新批次不复用历史结果，也不把已完成的原生无故障轨迹采集作为故障恢复结果。

批次：`codex-swe-full-recovery-20261104-r1`。

## 配置

- 300 个 SWE-Bench Lite 任务 × 6 策略 × 1 次，共 1800 单元。
- 策略：`clean_recovery`、`crash_no_rollback`、`temporal_checkpoint`、`whole_branch_abort`、`causal`、`ideal_oracle`。
- 模型 `gpt-5.6-luna / max`，模型会话 900 秒、单元 3600 秒，最多 8 路并行；未设硬 token 上限。
- 故障种子 `20261018`，不注入 gold 解答或已完成任务前缀。
- AgentTX、Clean、Ideal 使用 compact；其他三组使用 full。收益包含恢复策略和上下文组织的共同作用。
- 任务、镜像 ID、base commit、上下文设置和时限参照现有 SWE/SWE-agent 注册。代码使用本次独立冻结版本，源码差异记录在 `source-comparison.json`；不能称作与十月批次完全相同的实现。
- Agent 在独立挂载命名空间中执行，官方验收使用已固定的任务镜像。Docker 协调器使用 host 网络和 host 工具链。
- 六策略首题均通过执行、上下文和用量有效性检查后自动放行全量队列；不按解题成败决定放行。
- 真实任务失败保留；基础设施异常停止新派发并排空已有任务。重试前归档旧尝试及已知成本。
- 每单元保留主执行、笔记 replay、验收结果和计费证据。结束后清理运行时工作区，不做全局容器清理。

本批沿用既有 SWE 目标测试和恢复合约，`formal_eligible=false`。单次尝试成功数、官方验收、恢复合约和完整用量分别核算。

## 路径与检查

- 结果：`/mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1`。
- 冻结代码：`/home/bfq/agenttx-workspace/agenttx-executions/codex-swe-full-recovery-20261104-r1/project`。
- 服务：`agenttx-codex-swe-full-recovery-20261104-r1.service`（user systemd）。
- `registration.json`：执行配置、参考注册、源码和 profile 哈希。
- `progress.json`：总数、运行中单元、停止原因和空间。
- `cells.json`：逐单元状态与结果路径。
- `canary-proof.json`：六策略首题有效性检查，完成后产生。
- `preflight/proof.json`：零模型调用检查已通过，证明源文件恢复、独立笔记保留、受污染派生产物移除。前两次容器预检的记录完整保留，未执行模型。

```bash
systemctl --user status agenttx-codex-swe-full-recovery-20261104-r1.service
cat /mnt/data/agenttx-runtime/results/codex-swe-full-recovery-20261104-r1/progress.json
```
