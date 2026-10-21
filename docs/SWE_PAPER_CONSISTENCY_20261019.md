# SWE-bench 实验与论文一致性分析

核查时间：2026-10-19 19:10（北京时间）。核查范围是本轮 SWE-agent / SWE-Bench Lite 恢复实验，以及本地 `paper/main.tex`；不将 Codex 历史实验、TB 或 controlled suite 混入本轮统计。

**结论：主要对照组的成本优势有数据支持，但实验口径和归因存在实质缺口，当前不能作为标准 SWE-bench 成功率或“纯因果回滚收益”的最终论文证据。尤其确认了 gold patch 文件路径信息进入 Causal / Ideal 恢复提示，Clean 未获得同样提示。** 这不等于应用了 gold patch 的代码内容，但必须披露并用消融排除其影响。

## 1. 论文当前写了什么

核查对象 `paper/main.tex` SHA-256：`f179c77dfd41271f26b8e31685f9b61d114c6fdf726460dc99adbad52da77eb6`。

| 论文位置 | 原有叙述 | 本轮核查 |
|---|---|---|
| 第 984 行，RQ2: Real-World Workloads | 小节只有标题，没有结果表或正文 | 没有一张最终 SWE 成绩表可以逐项复现，不能声称已复现论文的 SWE 数值 |
| 第 1042 行附近 | 300-task、five-policy、still running | 已过时；本轮六策略，1800 个计划单元，1799 个完整结果、1 个 worker 异常终态 |
| 第 64、136 行，摘要/引言 | controlled suite 中相对 temporal / whole-session 避免 1425 / 3340 replay tokens | 属于另一套受控实验；本轮统计的是实际 API main + replay，用例、尺度、分母均不同，不能直接替换或视为冲突 |
| 第 513–535 行，自然轨迹机会分析 | 189 条 SWE 轨迹、236 个假想故障根；只有 4 条有 later independent write，3 条有 interleaved write；231/236 根无额外独立写可供 temporal 误删 | 与本轮人为插入恢复笔记的恢复实验不是同一实验。本轮收益不能推翻“自然机会稀疏”，也不能证明每条自然轨迹都有类似收益 |
| 第 1031 行附近 | 20 条成功轨迹 / 638 turns / 6 independent turns，以及旧 matched study | 应标注各数据集和版本；不能把这些旧分母与 189 条自然轨迹审计或本轮 300 题合并 |

本次没有修改主论文和 PDF；本报告给出应更新的口径。

## 2. 本轮终态及成功口径

活动批次：`sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`，包含按引用继承的原始结果。

300 题 × 六策略 = 1800 个单元；1799 个结果完整，其中 1656 个本实验合约成功、143 个任务失败；另 1 个 Clean worker 异常。任务失败没有按成败重试或从数据中删除。

| 策略 | 合约成功 / 计划数 | 完整结果数 | 完整结果实际 API Token，含 replay | 其中 replay | 完整结果平均 Token |
|---|---:|---:|---:|---:|---:|
| Causal，compact | 285/300，95.00% | 300 | 40,501,138 | 0 | 135,004 |
| Ideal，compact | 289/300，96.33% | 300 | 39,975,381 | 0 | 133,251 |
| Clean，compact | 273/300，91.00% | 299 | 46,152,209 | 0 | 154,355 |
| 不回滚，full | 282/300，94.00% | 300 | 93,502,009 | 0 | 311,673 |
| 时间检查点，full | 284/300，94.67% | 300 | 47,760,194 | 1,954,855 | 159,201 |
| 整分支回滚，full | 243/300，81.00% | 300 | 48,015,838 | 2,898,444 | 160,053 |

**上述成功率不是标准 SWE-bench resolved rate。** 当前 `test_command()` 从 `FAIL_TO_PASS` 构造 repo-specific 命令；没有完整执行 `PASS_TO_PASS` 与标准 resolved grader。合约成功另要求正常结束/提交、恢复笔记有效、derived 清除、保留对象不被改动以及 manifest 一致等。因此它既比单纯 FAIL_TO_PASS 多了恢复约束，又缺少标准回归检查，不能推导它与官方 resolved rate 的固定大小关系。检查点 `tests_ok=288`、合约成功 284，整分支分别为 248、243，也说明两者有区别。

Clean 异常为 `sympy__sympy-24102`：67 次已返回调用计 1,958,229 Token，第 68 次在途用量未知。上表不把该不完整单元混入完整结果均值，但原始费用单列保留。全部终态的**已知** API 用量为 **317,864,998 Token**，包含该异常已返回部分，不能称完整供应商账单。Clean 的 91.00% 用全部计划 300 作分母；在 299 个完整结果中为 91.30%。

计量采用日志中的实际 API `Usage.total_tokens`，而非 SWE-agent 的文本估算。1799 个完整单元逐项核对响应数等于 main 调用数加 replay 调用数；replay 先于 main 执行，按调用顺序拆账，reasoning 不重复相加。1799 个完整结果的 `precrash_progress_seeded=False`、`precrash_progress_source=none`、`oracle=False`，表示没有以 gold 修复内容预置完成前缀；这不能排除下文的 gold 路径元数据混杂。

## 3. 含 replay 的配对成本优势

主比较只取**同一题双方合约成功**的样本：`1 − Σ Causal Token / Σ 对照 Token`。这是成功条件下的成本比较，不能代替全体任务成功率或无条件成本结论。

| 对照 | 双方成功题数 | Causal 总 Token 节省 | 95% 任务配对 bootstrap 区间 | 逐题节省率中位数 | Causal 更省的题数 |
|---|---:|---:|---:|---:|---:|
| 不回滚 | 275 | 58.43% | 53.42%–62.82% | 55.60% | 254 |
| 时间检查点 | 278 | 17.55% | 11.20%–23.48% | 18.91% | 188 |
| 整分支回滚 | 239 | 27.86% | 22.70%–32.91% | 32.31% | 198 |
| Ideal | 282 | 1.47% | −6.95%–9.26% | −2.25% | 132 |
| Clean | 264 | 14.38% | 3.25%–24.34% | 11.36% | 170 |

区间基于固定种子、20000 次任务配对重采样。它只刻画这批成功筛选样本的任务差异，不刻画重复模型运行方差，也不是消除提示混杂后的因果区间。

补充核对：

- 六组共同成功的固定 212 题，Causal 对不回滚 / 检查点 / 整分支仍省 **60.02% / 17.25% / 26.64%**。主要优势不是只因每组成功样本不同。
- 分别去掉单个绝对 Token 差最大的任务作敏感性分析，仍省 **58.07% / 15.97% / 27.20%**。主结果保留所有任务，没有实际删点。
- 纳入双方所有完整终态、包含任务失败的 300 题配对，总实际支出相对上述三组低 **56.68% / 15.20% / 15.65%**。但整分支成功率明显低，不能将较低失败开销解释为同等解题效率。Ideal 在全体 300 题上比 Causal 少用 1.32% Token；成功子集与全样本排序不同，应一起报告。

成功率也需要单独判断：Causal 对不回滚只高 1.00 个百分点（配对 exact McNemar p=0.629），对检查点高 0.33 点（p=1），均不足以声称显著提升；对整分支高 14.00 点（p≈4.46×10⁻¹⁰）。对 Ideal 低 1.33 点（p=0.344）；对 Clean 在 299 对完整结果中高 3.68 点（p=0.061）。这些是探索性、未作多重比较校正的检验。

## 4. 最关键的归因问题：gold 文件路径进入恢复提示

已核实的代码与实际轨迹链条：

1. `experiments/workloads/swe_bench_suite.py:117` 的 `_fault_path_from_patch()` 明确从官方 solution `patch` 的 `+++ b/` 路径选择注入文件；`load_tasks():184` 将其设为 `faulty_relpath`。
2. 注入结果把该文件记录为 `injected.faulty_path`。全部 1500 个非 Clean 完整结果均有该路径，且全部属于相应官方答案补丁涉及的文件。
3. `recovery_inject.py` 把该文件加入 manifest 的 `invalidated`。compact 渲染在第 496 行附近仅让 Causal / Ideal 看到该列表，并要求从 disclosed source 开始定位。
4. 全部 300 个 Causal、300 个 Ideal 结果标记 `fault_location_disclosed=True`。其他组均标记 False，Clean 没有注入源文件或定位提示。
5. 实际抽查 `psf__requests-1963` 和 `sympy__sympy-11870` 的 Causal / Ideal / Clean 共六份模型轨迹：前两组确实在 **user 消息**中收到 gold 文件路径；Clean 明确说没有 source path。两题抽查证明提示确实送入模型；全量披露数量来自结构化结果字段，并非声称逐一人工阅读全部轨迹。

具体例子：`psf__requests-1963` 官方答案修改 `requests/sessions.py`，Causal 实际 handoff 写入：

```text
RECOVERY-INVALIDATED (repair or keep absent as specified):
- requests/sessions.py: fault_producer_rolled_back
```

随后提示要求从 disclosed invalidated source 开始。冻结 r9 源码中的路径选择和渲染链条也一致，证据包含冻结源码文件 hash、实际轨迹路径和 hash。

**这里确证的是答案涉及文件的路径元数据进入提示；没有据此确证模型得到或应用了答案补丁内容。** Causal localizer 可以真实定位注入步骤，但注入文件的选择本身使用了 gold，定位后公开它同时给了原始 SWE issue 的相关文件提示。某些题的 issue 或测试本就可能暴露相同信息，所以不能直接量化这一提示的额外收益；必须通过控制提示的消融测量。

这会影响三个解释：

- 不能把全部 Token 优势归为依赖闭包选择、避免误删或免 replay。
- Clean 虽然没有注入故障，却缺少 Causal / Ideal 的定位提示，且不承担相同恢复工件合约，不是信息和任务约束完全一致的优化上限。当前 Clean 较贵、成功率较低，不能只归咎模型随机性。
- Ideal 是预知故障步骤的恢复策略，能作为恢复集合选择的参照；它不是随机 LLM 总 Token 必然最低的数学下界。当前 Causal 与 Ideal 的成功配对成本区间跨零，不能声称超过 oracle。

## 5. replay 免除只解释部分净节省

| 同题双方成功比较 | Causal main | 对照 main | 对照 replay | 总净省 Token | 免 replay 占净省 |
|---|---:|---:|---:|---:|---:|
| 检查点，278 题 | 32,590,357 | 37,716,805 | 1,808,621 | 6,935,069 | 26.08% |
| 整分支，239 题 | 25,167,596 | 32,534,822 | 2,353,503 | 9,720,729 | 24.21% |

不回滚与 Causal 都没有单独 replay，会话成本差全部发生在 main。即使是检查点和整分支比较，大约四分之三净省也发生在 main；可能包含上下文压缩、定位提示、恢复状态、求解路径和随机性等作用，当前无法分离。

此外，按用户指定，Causal / Ideal / Clean 使用 compact，三个对照使用 full。这是已登记的**策略与上下文配置整体比较**，不应事后改成统一上下文，也不能把所得优势写成 rollback-only 的因果效果。本轮 replay 主要重建人为构造的独立恢复笔记；不能等同自然代理在完整修复轨迹中丢失代码工作的普遍重做量。

## 6. 仍未达到正式论文条件的部分

本轮注册 `formal_eligible=false`，`comparison_estimand` 明确为策略加上下文配置的整体效果。主要缺口：

- **标准验收：** 当前只有 FAIL_TO_PASS 命令及恢复合约，没有完整 PASS_TO_PASS / 标准 resolved grading；模型还能看到指定的测试及测试相关诊断，与标准自由求解设置应分别表述。
- **提示与故障选择：** gold 路径提示和 prompt/policy payload 没有配平；需要独立于 solution patch 的故障点，以及定位提示有/无的受控消融。
- **预算：** `token_budget=null`，缺少共享 hard Token budget；登记 900 秒会话但存在 3144 秒会话和 3600 秒 worker 超时异常，不能声称严格等时间预算。
- **冻结与重复：** 完整结果跨七个 source hash，包含多次环境/编排修复续跑；不能称全批完全同一冻结源码。单次运行也不足以估计重复模型方差。
- **跨后端：** 本报告只分析 SWE-agent / SWE-bench。不能据此确认 Codex 或 TB 的效果；新的 TB 双后端批次应独立分析。

## 7. 建议论文如何写、后续补什么

现阶段可写：在该注入恢复、逐策略上下文设置和 FAIL_TO_PASS 加恢复合约下，Causal 在双方成功样本上，相对不回滚 / 检查点 / 整分支的 main+replay API Token 分别低 58.43% / 17.55% / 27.86%。同时报告成功数、全部完整尝试支出、异常费用、成功筛选条件和提示混杂。

现阶段不宜写：“标准 SWE-bench resolved 95%”、“因果回滚本身节省上述全部 Token”、“显著提高所有对照成功率”、“Clean 已验证为优化上限”、“普遍自然轨迹都具有同等收益”或“Causal 超过理想 oracle”。

后续优先级：

1. 利用保留的模型补丁做独立的官方完整 grader 复验，补 FAIL_TO_PASS / PASS_TO_PASS / resolved 及 grader 环境异常；已有输出可用时无需先重新调用模型。
2. 冻结新协议，故障点不读 solution patch；独立控制恢复策略、compact/full 和定位提示是否披露。保留用户指定配置作为整体效果主组，额外消融用于分离机制，不覆盖原始主结果。
3. 定义能成立的 Clean 上限：与恢复组相同任务进展、可见信息和预算，只移除注入故障/恢复成本。自然 clean unsolved checkout 继续作为独立基线。
4. 正式运行统一冻结源码、落实硬预算、完成跨 harness 核验并登记重复次数；自然轨迹恢复机会与人为注入实验分开报告。

上述是分析后的补实验建议，本次未启动新的收费 SWE 任务。

## 8. 可复算材料

- 脚本：`experiments/scripts/analyze_swe_paper_consistency.py`
- 复算：`/home/bfq/.venvs/agenttx/bin/python experiments/scripts/analyze_swe_paper_consistency.py`
- 统计结果：`/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019/report.json`
- 1799 单元 main/replay 拆账：同目录 `phase-accounting.json`
- gold 路径全量字段核对、六份实际模型输入摘录与冻结文件 hash：同目录 `gold-path-evidence.json`
- 源 API 用量审计：`/mnt/data/agenttx-runtime/validation/swe-api-usage-sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z.json`

机器结果记录源审计和论文 hash；若活动批次、论文或原始日志改变，重新复算时应保留本快照并登记新版本。
