# SWE-agent / SWE-bench 最终数据

论文数据、配图与 PDF 已于 2026-10-19 19:23（北京时间）更新完成。[论文 PDF](../paper/main.pdf)、[图预览](../paper/img/FIG-SWEagent-SWEbench-Results.png)、[矢量图](../paper/img/FIG-SWEagent-SWEbench-Results.pdf)。新图位于论文 PDF 第 10 页；已核验实际 API 数据和 Nimbus Roman 字体。

本轮 300 题 × 六策略 = 1800 个单元；1799 个完整结果、1 个 Clean worker 异常。下列成功率使用全部计划 300 题为分母，成本使用实际 API Token，包含 main 和 replay。成功指标沿用本实验的目标测试与恢复合约。

| 策略 | 成功 / 计划数 | 成功率 | 完整结果数 | 总 Token | 平均 Token / 完整结果 | replay Token |
|---|---:|---:|---:|---:|---:|---:|
| Causal | 285/300 | 95.00% | 300 | 40,501,138 | 135,004 | 0 |
| Ideal | 289/300 | 96.33% | 300 | 39,975,381 | 133,251 | 0 |
| Clean | 273/300 | 91.00% | 299 | 46,152,209 | 154,355 | 0 |
| 不回滚 | 282/300 | 94.00% | 300 | 93,502,009 | 311,673 | 0 |
| 时间检查点 | 284/300 | 94.67% | 300 | 47,760,194 | 159,201 | 1,954,855 |
| 整分支回滚 | 243/300 | 81.00% | 300 | 48,015,838 | 160,053 | 2,898,444 |

Clean 异常另外保留 1,958,229 已知 Token，第 68 次在途调用用量未知；不混入 299 个完整结果的均值。六组全部已知终态支出为 **317,864,998 Token**。

## 因果回滚的 Token 收益

同题双方成功配对，按 `1 − Σ Causal Token / Σ 对照 Token` 计算总节省，包含 replay。

| 对照 | 配对题数 | 总 Token 节省 | 95% 任务配对 bootstrap 区间 | 逐题节省率中位数 |
|---|---:|---:|---:|---:|
| 不回滚 | 275 | 58.43% | 53.42%–62.82% | 55.60% |
| 时间检查点 | 278 | 17.55% | 11.20%–23.48% | 18.91% |
| 整分支回滚 | 239 | 27.86% | 22.70%–32.91% | 32.31% |
| Ideal | 282 | 1.47% | −6.95%–9.26% | −2.25% |
| Clean | 264 | 14.38% | 3.25%–24.34% | 11.36% |

六组共同成功的固定 212 题上，Causal 对不回滚 / 检查点 / 整分支节省 **60.02% / 17.25% / 26.64%**。分别去掉单个绝对 Token 差最大点的敏感性分析为 **58.07% / 15.97% / 27.20%**，主统计保留全部配对任务。

包含失败的所有完整终态配对中，对上述三个对照的实际总支出减少 **56.68% / 15.20% / 15.65%**，对应每组 300 个配对。该项与成功配对成本分别报告。

## 数据来源与论文位置

- 批次：`sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z`。
- 审计快照：`/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019/report.json`，时间 `2026-10-19T11:10:00Z`。
- 逐单元 main/replay 拆账：同目录 `phase-accounting.json`。
- 论文：`paper/main.tex` 的 RQ2、表 `tab:swe-full-results` 和 `tab:swe-paired-savings`，以及该批次完成状态文字。
- 图：`paper/img/FIG-SWEagent-SWEbench-Results.pdf`（矢量）和同名 PNG（300 DPI），已接入 RQ2 的 `fig:sweagent-full`。四面板依次为六策略成功率、main+replay 平均用量、成功配对总节省及区间、逐题成本比例分布。
- 绘图脚本：`motivation/plot_sweagent_final.py`；沿用旧 `plot_swe_tokens.ipynb` 的 Nimbus Roman、17.8 cm 宽、白底内向刻度、顶部方框图例，以及红实线方块 / 蓝点划空心三角 / 橙点线叉号 / 黑虚线空心圆。Ideal / Clean 补紫色和青绿色；图中原有四组配色不变。字体文件缓存在数据盘。
- 图逐点数据及来源 hash：`/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019/figure-data.json`。
- Causal / Ideal / Clean 使用 compact；三个对照使用 full，沿用既定实验设置。

本表只含本轮 SWE-agent / SWE-bench；Codex 和 Terminal-Bench 分别更新，不混合统计。动态监控中的 SWE-agent 文本估算 Token 与此处实际 API 用量不同，论文数据以此快照为准。
