# 双 harness 接口接入与真实运行时诊断

中英文策略名称与 ID 对应关系见[术语表](terminology.md)；历史结果保留各自实验命名空间。

最新推进：七策略 canonical 运行器及 TB 同容器恢复/验收路径已实现并实跑。29 次调试尝试中 25 次正常通过，1 次预算超时、3 次已定位并修正的集成错误保留；详见 [本轮实现与结果](PROTOCOL_COMPLETION_20261017.md)。下文较早轮次的状态保留作历史，不代表当前仍缺同一入口。

更新：2026-10-17，Asia/Shanghai。Codex 和 SWE-agent 已接入指定接口，历史模型 `gpt-5.6-luna`、reasoning `max` 均实际调用成功。**4 个恢复诊断单元通过，另保留 2 个基础设施失败尝试；正式 84/8400/5410 清单尚未执行。**

## 配置与环境

- 接口：`http://10.249.44.127:8080/v1`。模型列表返回 200，并包含指定模型；随后两个 harness 均完成真实写文件验证。
- 本地私有配置：`/home/bfq/.config/agenttx/experiment-20261017/`；远程：`/home/bfq/agenttx-validation-20261017-p0/private-profile/`。目录 700，配置与 auth 文件 600；不将密钥写入项目、实验记录或源码指纹。
- Codex 使用 `config.toml` 和 `auth.json`；SWE-agent 使用 `sweagent.toml` 和同一 auth。当前 CLI 0.163.0 忽略 `disable_response_storage`，已从 Codex 配置移除；SWE-agent 的独立配置保留项目字段，通过 LiteLLM 显式设置 `store=false`。这不是已经验证 Codex 服务端存储行为的声明。
- 远程使用独立 Python 3.11.17 环境。SWE-agent 1.1.0 来源固定为 GitHub 提交 `3ea751c087f32b16e039a2233dd6eefecef325d5`；SWE-ReX 1.4.0、LiteLLM 1.81.10、OpenAI 2.20.0。源码归档哈希见恢复根目录 [来源记录](/home/bfq/agenttx-recovery-20261016/reports/model-environment-bootstrap-20261017.json)，完整依赖见结果目录的 `model-environment.freeze.txt`。
- 修正正式 runner 的 SWE-agent 构造路径：支持 `CODEX_AUTH_FILE`、`AGENTTX_SWE_PROFILE`（缺省继承 `CODEX_CONFIG_FILE`）、`AGENTTX_SWE_AGENT_ROOT` 和显式 `--harness-root`，无需重建旧硬编码目录。
- 使用独立 bwrap namespace，`/root`、`/sys`、`/run` 为临时文件系统；原远程项目及历史结果未修改。

## 实际执行结果

结果目录：[runtime_dag_diagnostic_20261017](../experiments/results/runtime_dag_diagnostic_20261017/)。[attempts.csv](../experiments/results/runtime_dag_diagnostic_20261017/attempts.csv) 保留所有 6 次模型恢复尝试，[progress.json](../experiments/results/runtime_dag_diagnostic_20261017/progress.json) 为机器可读记录。两次单独的 endpoint/harness 写文件验证及两条无模型运行时检查另计，不相加当正式样本。

| 批次 | 策略 | 模型返回码 | 独立字节验收 | 说明 |
| --- | --- | ---: | --- | --- |
| Codex v1 | causal、Ideal oracle，各 1 次 | 均为 0 | 均失败 | 只有 CLI 主程序，缺少配套 `codex-code-mode-host`；工具没有执行。保留为基础设施失败 |
| Codex v2 | causal、Ideal oracle，各 1 次 | 均为 0 | **均通过** | 从同一安装包补齐配套程序后，在新目录重跑 |
| SWE-agent v1 | causal、Ideal oracle，各 1 次 | 均为 0 | **均通过** | 原生 adapter、真实模型工具调用及历史 projector 均生效 |

使用注册案例 `controlled-t1x16-f0.25-s0`，manifest 为 `56f1134973a1e175718f8764f9c6650fc7c8837ded3661af1f8a2dfc73ce1a4f`。16 个操作通过真正的 AgentTX 工具边界执行，开启 strace，未注册 `extra_reads`。故障只注入根操作，后继通过读取受污染父产物自然失效。实际污染集合 `[4, 6, 11, 12]` 与生成器真值一致，已知直接依赖边无遗漏。

causal 使用失败的产物合约 verifier 及 16 次反事实探测定位，不传入注入根或 oracle 集合；实际回滚集合另含 verifier 步骤 16。oracle 使用生成器完整真值调用 `rollback_exact`，不查询捕获闭包。两者均保留 12 个独立产物及对应活动消息，回滚后字节未改变。SWE-agent 向模型投影了 24 条保留的 precrash action/observation 消息。

模型随后在原事务内修复产物；最终验收既执行公开 checker，又由外部 evaluator 独立计算期望字节、读取实际产物并核对 `workload.py` 未被修改。Codex v1 的返回码为 0 但验收失败，说明“模型进程退出成功”没有被误计为任务成功。

## 适用范围与待完成工作

这是 `agenttx.runtime-dag-diagnostic/v1`，只验证执行链。它沿用注册 DAG 的拓扑、操作次序、故障位置和真值集合，但使用 digest 产物控制磁盘占用，precrash 调度由脚本生成，故障 override 命令如实保存在历史中。Codex 采用活动历史提示投影和外部进程边界；SWE-agent 使用原生 action/history adapter。SWE-agent 此次设 8 次模型调用上限，Codex 仅设 240 秒进程上限；**不用于两个 harness 或策略的正式效率比较**。

当前入口支持 `clean_recovery`（无故障恢复上下文对照）、`crash_no_rollback`（崩溃后不回滚）、`causal`（AgentTX 因果恢复）和 `ideal_oracle`（理想 oracle），其中本轮只验证后两条。它尚未实现正式三条 coarse 策略的自诊断/回滚协议，不替代完整 400-case 产物语义和七策略 runner，也不将这 4 个通过项填入预注册 84/8400/5410 CSV。正式批次仍需统一提示词和预算、冻结任务及镜像、完成 TB 镜像内执行和恢复中断矩阵。

初始直接运行没有遮蔽宿主 `/sys`，try 对 EFI 挂载反复尝试 overlay/copy，虽最终通过但有环境开销；后续模型批次统一使用 bwrap launcher。首次集成测试有一项硬编码 `/home/bfq/SWE-agent` 的路径断言失败，已改为从实际安装的源码定位 bundle；tiktoken 缓存也已按原哈希补齐。初始日志保留，最终本地 **79 passed**，远程 **100 passed**，另有 2 条上游 Pydantic 弃用警告；两组有重叠，不相加。

Codex v2 的执行源码指纹为 `c847a5e3edd15fda4ad62446bc194affe354725862a239a56c9dedb66c31599e`，与最终本地/远程工作区相同。SWE-agent v1 与 Codex v1 使用各自保存的较早指纹；之后增加 launcher 和软件指纹记录，不覆盖旧登记。原始运行时 session 留在远程，归档包含 ledger、恢复前后上下文、模型结果、SWE 轨迹、源码清单、提示词和测试日志；不打包整个 sandbox 或凭据目录。

## 复跑命令

远程独立验证目录中，使用新输出目录运行；不要覆盖已归档批次：

```bash
cd /home/bfq/agenttx-validation-20261017-p0/project
export AGENTTX_PYTHON=/home/bfq/agenttx-validation-20261017-p0/venv/bin/python
export CODEX_BIN=/home/bfq/agenttx-validation-20261017-p0/.tools/codex
export TIKTOKEN_CACHE_DIR=/home/bfq/agenttx-validation-20261017-p0/tiktoken-cache
bash experiments/scripts/run_runtime_recovery_pilot.sh \
  --trace-id controlled-t1x16-f0.25-s0 \
  --profile-dir /home/bfq/agenttx-validation-20261017-p0/private-profile \
  --harness codex --policies causal ideal_oracle \
  --output-dir /home/bfq/agenttx-validation-20261017-p0/runtime-model-codex-next
```

SWE-agent 将 `--harness` 改为 `sweagent`，另加 `--swe-agent-root /home/bfq/agenttx-validation-20261017-p0/SWE-agent`，并换独立输出目录。`--runtime-only` 只验证后端，不调用模型。launcher 使用实际 Python 路径，不伪造旧 conda 环境。
