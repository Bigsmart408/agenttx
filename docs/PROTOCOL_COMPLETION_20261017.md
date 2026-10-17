# 七策略运行时协议与 TB 镜像内恢复

中英文策略名称与 ID 对应关系见[术语表](terminology.md)；历史结果保留各自实验命名空间。

更新：2026-10-17，Asia/Shanghai。本轮新增实现和小规模模型验证；不覆盖历史结果或冻结的 84/8400/5410 登记。运行状态与全尝试统计以本页关联的机器记录为准。

## 新入口

- `experiments/scripts/run_runtime_recovery_system.sh` → `bench_runtime_recovery_system.py`：七策略真实运行时验证，使用 canonical 400 manifests，保留原来的父产物拼接顺序、角色 payload、操作顺序和 turn 边界。旧 digest diagnostic 入口保留。
- `experiments/scripts/bench_tb_image_native.py host`：TB 的恢复、模型工具执行和官方验收均在一个由固定 image ID 启动的临时容器内完成。保留镜像原工作目录和其他文件系统内容，不把主机目录投影到 `/app`。
- 每个 harness 使用相同模型 `gpt-5.6-luna / max` 和 240 秒模型 wall budget；SWE-agent 无额外 step/model-call cap。没有设置 token 数硬上限，必须明确记录为 `null`，不能宣称实现了固定 token 数预算。

## 七策略行为

| 策略 | 回滚边界来源 | 实际动作 |
| --- | --- | --- |
| `clean_recovery`（无故障恢复上下文对照） | 无故障 | 保留全部合法产物，模型检查 |
| `crash_no_rollback`（崩溃后不回滚） | 不回滚 | 模型自行检查和修复污染产物 |
| `turn_checkpoint`（轮次检查点回滚） | 对每个 turn 执行公共合约检查 | `rollback_turns` 恢复所有失败 turn 的完整操作集合，不额外扩张成因果闭包 |
| `temporal_checkpoint`（时间检查点回滚） | 从前向后执行公共单步合约检查 | 从首个失败操作执行真实 suffix rollback |
| `whole_branch_abort`（整分支放弃） | branch 起点 | 丢弃完整未提交 branch |
| `causal`（AgentTX 因果恢复） | 失败 verifier、捕获 ledger 与反事实探测 | AgentTX 自定位并回滚统一文件系统/上下文闭包 |
| `ideal_oracle`（理想 oracle） | 生成器独立真值 | `rollback_exact`，不查询待评估依赖图 |

粗粒度策略控制器不访问生成器故障根或闭包；公共检查、耗时和运行时调用全部留档。它是可执行合约辅助的边界基线，不是另外伪装成模型诊断的 oracle。模型修复阶段使用同一公共操作接口；审计拒绝读写源码/产物、批量循环、绕过接口的工具和重写保留合法产物。

故障只在生产根时通过短时 injector 环境改变 payload，前后重启 worker；clean 使用相同调度边界。实际历史 argv 不再含旧值 override。后继通过真实父文件读取自然污染。最终既运行公开 checker，也独立核对全部产物字节及 checker 未变。

新增 `AgentTX.rollback_turns` 明确保留 turn 边界，不借用 oracle API。多 turn 后端测试还会故意给保留消息添加错误依赖，检查 turn checkpoint 不因此扩张。

## TB 的执行与验收

1. 在独立缓存中补齐固定上游提交的任务资产；缺测试、镜像 build/探测失败或不支持的非 root 用户会在模型执行前记录基础设施错误。
2. Docker 标签先解析为 immutable image ID；模型和测试使用同一容器。输出不通过主机 `/app` 工作区交换；不挂载 `solution.sh` 或调用 gold solution，也不使用旧任务 prompt 中附带的解题提示。
3. 挂载独立 venv、实际 Python 基础安装、两个 Codex 程序、SWE-agent 来源和私有 profile；任务的 `python3` 仍是镜像自身解释器。事务 worker 单独使用 `AGENTTX_WORKER_PYTHON`，不修改任务 PATH。
4. 新 `TRY_BIND_READONLY` 仅让列明的工具/配置挂载穿过覆盖层，保留只读属性；任务文件仍走可写事务。Docker resolver 在 baseline 中保留，避免 overlay 丢 DNS。
5. 同容器 commit 后执行原官方测试；本地 uv 只替换下载安装步骤，记录原脚本、有效脚本和 uv 哈希。模型退出码、笔记合约、派生产物清理、官方测试返回码分别保留，进程退出 0 不代表任务成功。
6. v2 修复 clean SWE 没有 precrash 工具历史时的错误分支；两个 harness 的 compact view 都保留最近六个有效调用及结果，完整审计历史保留。v1 失败和新源版本重验分开保存。

本轮选择官方 `hello-world`。固定 source commit 为 `d28711d0da2675d0bb1d56de45ae5df6082438a3`，补取 `tests/test_outputs.py` 的 Git blob 为 `6329d060fc09b35f4b971efa7329eb9d3ef7d0d5`，与固定提交树一致。镜像 ID 为 `sha256:75cb41dc2d4f832786b0d4be2c474eb2177ece9d173bdfdff74af6509484dcc9`。任务 Python 3.13.1，事务控制器 Python 3.11.17。

## 版本和证据

- 新调试清单：`experiments/plans/protocol_validation_20261017/cells.csv`。14 个 canonical micro 尝试、10 个 TB v1 尝试、2 个 TB v2 重验尝试、1 个 worker 修复后的 v3 重验尝试，另有 1 个 v4 凭据留档回归和 1 个 v5 whole-branch 自动提交回归。该清单不是旧 pilot 的完成表，不用于替换正式单元。
- 冻结源码 v1：`06bdbe80f4ed451f421066470242ec8197729e7f02b2950ff059733bcfb38c18`。
- 冻结源码 v2：`3a5bd1d6c4311b8539f8e20a906e0ba824d40c68c8ce17b0a83fc1b99c51836c`。
- 冻结源码 v3：`4656ca2fbfba85f182e607fde043202404100d0b4de4103f2afde85bae9c06ff`。修正持久 worker 复用命令脚本被 copy-up 后执行旧内容的情况，当前命令正文通过 argv 传入；只将正常 `submitted` 认作 SWE 完成，排除内部错误的自动提交。
- 冻结源码 v4：`f494ec7f1cbcc91e9fb35c034a1d8606f13a6901a8e1f8b969461fc4e0d6862d`，增加凭据脱敏与临时 auth 清理。
- 最终工作区/远程源码 v5：`938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`，将相同清理用于 TB；不修改已有模型尝试的源码登记。
- 远程冻结副本：`/home/bfq/agenttx-validation-20261017-p0/protocol-source-v1` 、`protocol-source-v2` 、`protocol-source-v3`、`protocol-source-v4` 和 `protocol-source-v5`。后续编辑不改变已启动实验代码。
- 汇总、用量、运行时记录、恢复前后 conversation、ledger、prompt、独立验收、command audit 和源码清单按尝试归档。完整 sandbox 留在远程。

## 小规模结果

| 项目 | 结果 | 解释 |
| --- | --- | --- |
| canonical micro / SWE-agent | 7/7 通过 | 16 操作的一例 canonical manifest，七策略完整执行 |
| canonical micro / Codex | 6/7 通过 | turn checkpoint 在原 240 秒预算内执行到操作 14，超时；没有放宽预算或按成功筛选重试 |
| TB / Codex 五策略 | 全部已有通过记录 | hello-world；原生同容器 agent 与官方 tests |
| TB / SWE-agent 五策略 | 全部已有通过记录 | clean 的空历史错误在 v2 修正；temporal 的状态查询错误在 v3 修正；whole 的自动提交状态另用 v5 重验；原失败保留 |
| 全尝试 | 29 次中 25 次正常通过 | 另有 1 个预算超时、3 个 SWE 集成错误；跨源版本调试数据不得用于策略收益推断 |

旧 whole-branch 有 `official_success=true`，但退出状态为 `submitted (exit_error)`；保留该官方任务结论，归一化的正常完成数不纳入这行。`raw_completed` 和 `completed` 分列，不能把自动提交当作正常结束。

原始尝试和归一化汇总见 [证据目录](../experiments/results/protocol_validation_20261017/)，其中 `attempts.csv` 保留分母、源版本及失败类型，`progress.json` 记录总进度，`artifact-manifest.json` 校验文件。v4 的 SWE causal 凭据留档回归也通过。Codex 超时没有输出最终 usage；原始 parser 的 0 只是缺失占位，汇总中将实际 token 数记为 `null`，不作为零成本样本。

旧命令正文问题有独立重现：冻结 v1 源码在命令脚本 inode 被 copy-up 后执行旧命令而断言失败；修复后通过，并验证回滚后的下一条命令。TB SWE temporal 的新源版本实际模型重验也通过。早期回归未将 venv 加入 PATH，使注入合约中的 `python` 无法执行，日志中的三项失败保留；使用与实验一致的 PATH 重验。

归档前检查发现新增 `runtime_records` 包含 shell 环境中的模型认证值，以及一次 Codex 超时留下的临时 auth 文件和 snapshot blob。已仅将认证值脱敏、删除生成的认证副本，原退出码、验收结论和任务产物不变；原文件前后哈希和清理路径另存 `protocol-credential-cleanup.json`。私有 profile 源文件不变。新增回归验证不会改变活的执行命令、不沿硬链接修改私有源文件，且能处理带元数据地址的 snapshot blob；两个入口都使用清理逻辑。完整远程文件和本地归档均再次做字面凭据扫描，最终统计见机器记录。

## 验证

远程最终后端/策略/原生适配回归 **80 passed**（2 条上游 Pydantic 弃用警告），最后的凭据/compact view 定向回归 **6 passed**，其中 4 项与前者重叠；本地快速回归 **24 passed**，也有重叠，不相加。完整测试日志及 JUnit XML 在证据目录的 `validation/` 中。模型批次结束后检查无本轮 runner 或自有容器残留。

## 正式全量尚需完成

本轮是协议实现验证。尚未执行 12-manifest pilot 全覆盖，更没有完成 400 × 7 × 3 或 541 × 5 × 2。还需跨三个 Motivation 家族、多 turn 和依赖极值做双 harness 验证，统一并冻结正式 prompt/token budget 约定与 payload 计数，审计 TB 注入历史中的诊断 marker，验证更多官方镜像及资产/用户/运行时差异，再登记全量新 cohort。旧 TB host-agent guard 保留；不得将本轮或历史诊断行直接填入正式表。恢复中断矩阵、Breakdown、六类开销仍按总计划推进。
