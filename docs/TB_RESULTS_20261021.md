# TB 阶段结果与续跑（2026-10-21）

北京时间10:37核对；本机r760-ssd，当前服务r13。

## 进度与结果范围

累计421条完整、28条保留异常，8路运行、2435待跑。当前v6配置的完整观测158条，其中118成功、40失败；SWE-agent为55/80成功，Codex为63/78成功。其余263条完整观测属于v3/v4/v5，仍保留但不混进v6收益。另有两条v6用量异常，不进入完整成本比较。

这里的完整结果允许任务失败；成功需要官方测试、受保护笔记与派生产物清理检查通过。模型超时124在验证全部通过时可算成功；用量不完整仍从完整成本配对中排除。下表成功率的分母是各策略已有完整观测，任务覆盖并非完全相同，不能当作最终同题成功率差异。

| 策略 | Codex成功/完整 | SWE-agent成功/完整 |
| --- | ---: | ---: |
| Clean | 13/14 | 7/13 |
| 不回滚 | 9/14 | 10/14 |
| checkpoint | 10/12 | 10/14 |
| whole | 12/13 | 9/13 |
| Causal | 9/12 | 10/13 |
| Ideal | 10/13 | 9/13 |

## 包含main与replay的token成本

只比较同harness、同v6配置、同题双方成功且用量完整的结果。每一行有自己的任务交集；节省率为`1 - sum(Causal tokens)/sum(control tokens)`，不是逐题百分比均值。

| 对照策略 | Codex Causal节省 | 配对 | SWE-agent Causal节省 | 配对 |
| --- | ---: | ---: | ---: | ---: |
| 不回滚 | 12.17% | 6 | 14.60% | 8 |
| checkpoint | -36.90% | 8 | -46.00% | 8 |
| whole | -4.86% | 9 | -81.04% | 9 |
| Ideal | 0.21% | 9 | -60.84% | 9 |
| Clean | -12.63% | 9 | -74.79% | 6 |

负值表示更多消耗。当前不能宣称对所有策略都有token优势；样本6至9对，仍为阶段数据。

部分成本细节：

- Codex对不回滚配对：Causal19,284,050，对照21,956,109 tokens；对checkpoint为36,741,460与26,838,995；对whole为39,011,648与37,204,240。
- SWE-agent对不回滚配对：Causal1,900,502，对照2,225,321 tokens；对checkpoint为7,181,044与4,918,689；对whole为7,331,066与4,049,428。
- SWE-agent/whole九对的逐题节省中位数为24.16%，但总量多耗81.04%。build-tcc-qemu单题Causal5,625,800、whole1,904,917，占Causal配对总消耗约76.74%；该点显著改变总量权重。保留该题，不删除异常高值。
- Codex/whole逐题节省中位数为-2.61%，与总量-4.86%均为轻微成本劣势。不能把SWE-agent的尾部解释直接套给Codex。

## 中断与修复

r12在04:35:29排空停止，此后至本次检查未运行；直接停止原因是circuit-fibsqrt/Codex/Causal缺最终API用量。结构化model-result最后事件为`turn.failed`，原因精确为`Incomplete response returned, reason: max_output_tokens`，模型rc1、container rc0。该题官方验收失败，原结果和用量未知状态保留；不能把0条用量记录当成0消耗。

另一个新增异常chem-rf/Codex/checkpoint达到会话期限，官方及恢复检查全部通过。按既定规则任务成功，但最终API用量缺失；会话去重记录仅提供42,331,287 tokens的下界，不能当作完整账单或补零进入成功成本配对。与circuit-fibsqrt分别记录，不相互混淆。

调度修复只改变已识别模型预算退出的停派分类，不改变模型、提示、预算或验证器。必须读取真实model-result并核对结构化终止事件、返回码、SHA256，才允许保留异常后继续pending；正文/工具输出里出现错误字符串、未知错误、工作区不一致等依旧停止派发。29项相关检查通过。没有放宽服务端输出限制，也没有给这条异常补造账单。

r13于10:36恢复，全部449条旧终态按原路径继承，协议参数无变化、无按结果重试。8个worker与各自冻结源码哈希已核对，正在推进classifier-debug、cobol-modernization等任务。磁盘约1049GiB可用，350GiB保护线保留。监控仍每30秒检查；未知错误需要诊断，监控不能自动修复所有阻塞。

## 证据

- `/mnt/data/agenttx-runtime/validation/tb-results-review-20261021T1038.json`：进度、各策略成功计数、配对任务与逐题成本、逐题中位数、源码哈希和继承登记。
- `/mnt/data/agenttx-runtime/validation/tb-provider-budget-repair-20261021-evidence.json`：原始失败模型结果SHA256及结构化终止事件。
- `/mnt/data/agenttx-runtime/validation/tb-provider-budget-repair-20261021-tests.log`：29项检查。
- `/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261021-r13/`：当前登记、进度与效果；原r12结果保持原路径。
