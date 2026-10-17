# 实验复现检查：2026-10-17

更新：2026-10-17T10:44:58.722414+08:00（Asia/Shanghai）。本轮按预登记完成 **50 次模型尝试，49 次正常通过**；全部尝试分类为 `{'passed': 49, 'task_or_protocol_failed': 1}`。历史 400 案例的分析结果重新生成后，2400 行、24000 个确定性字段与恢复数据逐项相同。

可支持的判断是：受控分析结果可以复现，已检查的回滚状态可重复；模型端到端通过情况见下表。**不能保证每次都成功，也不能保证 token、耗时或完整真实任务总体效果与历史完全一致。** 本轮模型部分只覆盖一个 16 操作 canonical trace 和一个官方 hello-world 任务；50 次尝试不是 50 个不同任务。

## 固定条件与完整分母

模型 `gpt-5.6-luna / max`、同一私有 profile 和指定网关；模型 wall budget 240 秒、官方 verifier 600 秒，无额外调用次数或 token 硬上限。固定 canonical ID `controlled-t1x16-f0.25-s0`、manifest `56f1134973a1e175718f8764f9c6650fc7c8837ded3661af1f8a2dfc73ce1a4f`。TB 的 agent 与官方 tests 均在固定镜像 `sha256:75cb41dc2d4f832786b0d4be2c474eb2177ece9d173bdfdff74af6509484dcc9` 的同一容器执行。

执行前登记 48 次最终源码 v5 的双遍复跑：双 harness ×（七种 canonical 策略 + 五种 TB 策略）× 两次；另加两次原源码 v4 的 SWE-agent causal 对照。失败不补跑，不按结果修改预算或源代码。

| 实验 | Harness | 正常通过 / 尝试 |
| --- | --- | ---: |
| canonical 微基准 | codex | 14/14 |
| canonical 微基准 | sweagent | 14/14 |
| 原版本同源码对照 | sweagent | 2/2 |
| TB hello-world | codex | 10/10 |
| TB hello-world | sweagent | 9/10 |

- `terminal_bench-sweagent-v5-r2/causal`：task_or_protocol_failed，model rc=0。

本轮 SWE-agent / TB causal 的第二遍失败有独立证据：causal 定位验证通过，回滚集合 `[2,4,5,6]` 与旧记录相同，上下文、独立笔记和派生产物清理均通过。模型随后把修复命令和 `submit` 放进同一条回复的两个 bash 块中，SWE-agent 按既有协议拒绝执行；轨迹 action 为空、执行时间为 0，并收到“必须恰好一个 bash 块”的反馈。下一次模型误称工作已完成，直接正常 `submitted`。最终 `/app/hello.txt` 不存在，官方两个测试均失败。因此这是模型任务/输出协议失败，不能因为 model rc=0 或恢复状态正确就计作任务通过。没有补跑或修改本轮代码，详细索引见 `failure-analysis.json`。

v5 源码哈希 `938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`；v4 为 `f494ec7f1cbcc91e9fb35c034a1d8606f13a6901a8e1f8b969461fc4e0d6862d`。运行前、各批次前及运行后校验冻结源文件，逐单元核对 source/profile/input 身份及旧结果文件哈希。SWE-agent 版本 1.1.0、SWE-ReX 1.4.0、LiteLLM 1.81.10、OpenAI SDK 2.20.0；CLI、companion、配置与测试脚本哈希均留档。输出路径与运行 UUID 每次不同；未声称请求字节完全相同，也未固定网关背后的模型权重。

四条独立 lane 共享远程主机与接口，lane 内顺序执行；因此耗时只作描述，不能从这轮计算受控加速比。

## 恢复状态与同源码对照

本轮与预选旧记录的状态字段匹配 **50/50**。微基准比对回滚集合、遗留无效操作、有效操作误删、依赖边捕获、上下文及保留文件字节；模型修复后另用独立完整字节检查和原 checker 不变检查验收。TB 比对回滚集合、上下文、独立笔记和派生产物清理，并执行原官方测试。正常完成还要求 SWE-agent 明确 `submitted`，排除内部错误自动提交。

可与上次相同源码直接比较的共有四次：v4 canonical SWE-agent causal 两次、v5 TB SWE-agent whole-branch 两次，结果为 `{'passed': 4}`，状态匹配 4/4。

| 同源码对照 | 上次 token | 本轮两次 token | 状态相同 |
| --- | ---: | ---: | ---: |
| canonical_micro / SWE-agent / causal | 19,021 | 18918 / 19002 | 2/2 |
| terminal_bench / SWE-agent / whole_branch_abort | 4,689 | 5361 / 7520 | 2/2 |

其余 46 次与旧结果的比较跨源码版本，属于修复后行为核对。旧 29 次中的 25 次正常通过、1 次 Codex turn checkpoint 超时及 3 次 SWE-agent 集成错误完整保留在旧证据目录，不能将这些调试失败从历史分母中删除，也不能把跨版本通过率变化单独归因于恢复策略。旧 Codex 超时缺终态 usage，token 记为未知，不能与新用量按 0 比较。

## 成本是否一致

同一 v5 单元的两次复跑使用相同实现、任务和预算，下表列 token 波动最大的六项。账单模型用量包括 harness 模板及历史重放；handoff payload 的编码估计另列在原始记录，不能相加后当作节省量。

| 同版本单元 | 两遍 token 范围 | 最大 / 最小 |
| --- | ---: | ---: |
| terminal_bench / sweagent / temporal_checkpoint | 5,996–10,709 | 1.79× |
| terminal_bench / codex / temporal_checkpoint | 79,236–118,969 | 1.50× |
| terminal_bench / sweagent / whole_branch_abort | 5,361–7,520 | 1.40× |
| terminal_bench / sweagent / causal | 5,473–7,648 | 1.40× |
| terminal_bench / codex / clean_recovery | 58,869–75,080 | 1.28× |
| canonical_micro / codex / whole_branch_abort | 278,836–351,604 | 1.26× |

例如 SWE-agent 的 TB temporal 两次工具调用为 4 / 2，prompt token 为 9,605 / 4,693，总 token 为 10,709 / 5,996；执行路径的差异增加或减少了历史重放。

这说明恢复状态和端到端任务完成必须分别评价，模型执行路径和用量也会变化。源码不同的历史用量比较另在 `comparisons.json` 标记 `exact_source=false`，不与同源码对照混合推断策略收益。耗时还受并发、服务负载及缓存影响；本轮不作总体 token 节省率、成本优势或统计成功率保证。

## 历史 400 案例重新生成

重新调用当前 canonical 生成器和原 `analytical_rows`，核对原三个 CSV 的输入哈希与 400 个 ID。逐行比较 manifest、回滚集合、无效效果移除、有效工作保留、有效操作与归因 token 损失、遗留无效操作及正确性标志；排除非确定性的计时字段。**24000 个值全部一致，差异 0 项。**

| 指标 | 本轮重新生成 | 历史值 |
| --- | ---: | ---: |
| turn checkpoint 有效工作丢弃均值 | 54.019579% | 54.019579% |
| temporal 有效工作丢弃均值 | 56.904261% | 56.904261% |
| 非执行前缀的精确保留集合 | 390/400 | 390/400 |
| 归因 token 损失较少：turn / temporal / 相同 | 139 / 251 / 10 | 139 / 251 / 10 |
| 最佳固定边界相对逐例最小损失多付出 | 10.478261% | 10.478261% |

这是 `ledger_analytical` 复现，归因权重不是模型账单，也不是新增 400 次真实文件系统或模型实验。完整 8400/5410 正式单元仍未执行，本轮不填入其完成表。

## 证据与逐策略结果

- [执行前登记](../experiments/plans/reproducibility_20261017/registration.json)：50 单元、完整命令、预算、baseline 哈希和比较规则。
- [全部尝试](../experiments/results/reproducibility_20261017/attempts.csv) 与 [状态/用量配对](../experiments/results/reproducibility_20261017/comparisons.json)。
- [失败定位](../experiments/results/reproducibility_20261017/failure-analysis.json)：保留正常模型退出与官方任务失败的区别。
- [双遍汇总](../experiments/results/reproducibility_20261017/paired-summary.json)、[完成状态](../experiments/results/reproducibility_20261017/progress.json)、[身份校验](../experiments/results/reproducibility_20261017/integrity.json)。
- [400 案例新生成数据](../experiments/results/reproducibility_20261017/historical-analytical/regenerated.csv) 与 [逐字段比较](../experiments/results/reproducibility_20261017/historical-analytical/comparison.json)。
- 同目录保留每次模型记录、上下文、ledger、command audit、官方验收日志、轨迹和注册；完整 session/workspace 留在远程 `reproducibility-20261017-r1`，本地归档排除这两类目录。凭据扫描和逐文件 SHA256 清单单独留档，不归档私有认证值。

结束检查：远程完整目录扫描 7905 个文件，字面凭据匹配 0、不可读文件 0；本轮 20 个临时容器全部移除，无实验 runner 残留，冻结源码和公开配置/工具哈希检查通过。本地归档另行扫描并逐文件校验，见 `final-scan.json` 和 `artifact-manifest.json`。

| Suite | Harness | 策略 | 正常通过 / 尝试 |
| --- | --- | --- | ---: |
| canonical_micro | codex | causal | 2/2 |
| canonical_micro | codex | clean_recovery | 2/2 |
| canonical_micro | codex | crash_no_rollback | 2/2 |
| canonical_micro | codex | ideal_oracle | 2/2 |
| canonical_micro | codex | temporal_checkpoint | 2/2 |
| canonical_micro | codex | turn_checkpoint | 2/2 |
| canonical_micro | codex | whole_branch_abort | 2/2 |
| canonical_micro | sweagent | causal | 2/2 |
| canonical_micro | sweagent | clean_recovery | 2/2 |
| canonical_micro | sweagent | crash_no_rollback | 2/2 |
| canonical_micro | sweagent | ideal_oracle | 2/2 |
| canonical_micro | sweagent | temporal_checkpoint | 2/2 |
| canonical_micro | sweagent | turn_checkpoint | 2/2 |
| canonical_micro | sweagent | whole_branch_abort | 2/2 |
| terminal_bench | codex | causal | 2/2 |
| terminal_bench | codex | clean_recovery | 2/2 |
| terminal_bench | codex | crash_no_rollback | 2/2 |
| terminal_bench | codex | temporal_checkpoint | 2/2 |
| terminal_bench | codex | whole_branch_abort | 2/2 |
| terminal_bench | sweagent | causal | 1/2 |
| terminal_bench | sweagent | clean_recovery | 2/2 |
| terminal_bench | sweagent | crash_no_rollback | 2/2 |
| terminal_bench | sweagent | temporal_checkpoint | 2/2 |
| terminal_bench | sweagent | whole_branch_abort | 2/2 |
