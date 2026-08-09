# `osdi25-pan.pdf` 结构解析与 AgentTX 映射

## 1. 文档边界

参考 PDF 共 21 页：第 1 页是 USENIX 出版封面，论文正文从 PDF 第 2 页开始。
正文的 Abstract、Introduction、设计、实现、评估、Discussion、Related Work 和
Conclusion 主要分布在 PDF 第 2--15 页；后续为参考文献和一致性证明附录。

参考论文不是简单的“背景--设计--实验”三段式，而是先把旧方法按机制分类，再用
定量实验定位根因，随后让每个设计点逐一回答 motivation 中提出的 challenge。
评估开头再次列出研究问题，使图表和 claim 一一对应。

## 2. 章节功能与篇幅节奏

| 参考论文部分 | 大致页码 | 叙事功能 | AgentTX 初稿中的映射 |
|---|---:|---|---|
| Abstract + Introduction | 2--3 | 问题、旧方法缺陷、核心 insight、实现、数字、三项贡献 | 长轨迹副作用、粗粒度 rollback、causal effect transaction、核心结果 |
| Background | 3--4 | 将 crash consistency 机制分类并统一符号 | agent tool trajectory、semisolate、temporal/branch recovery taxonomy |
| Observations and Motivations | 4--5 | I/O path 分析 + latency breakdown + challenge 列表 | bare 污染、per-call try 成本、temporal rollback 丢失独立工作、三个系统难点 |
| Core model | 5--8 | overview、data structure、translation、layout、recovery | AET model、effect DAG、commit frontier、causal closure 和 safety invariants |
| Implementation | 8--10 | architecture、operation paths、recovery、optimizations | interception、strace/upperdir capture、persistent worker、snapshots、WAL |
| Evaluation | 10--14 | 先列问题，再按 correctness/performance/breakdown/case study 展开 | overhead、retention、ablation、scaling、real agent、tokens、robustness |
| Discussion | 14--15 | 主动回答设计边界和替代方案 | hard-link/bind mount、non-filesystem effects、observer atomicity、portability |
| Related Work + Conclusion | 15 | 按机制定位差异，结论回扣核心 insight | agent FS、branch/checkpoint、effect capture、trajectory causal recovery |
| Appendix | 20--21 | 给出 crash consistency 的形式化论证 | 后续可加入 AET safety model 和 reconstruction proof sketch |

## 3. 值得保留的写作方法

1. Introduction 在第一页就交代实现和最关键数字，不把贡献藏到后文。
2. Motivation 不只说“旧系统慢”，而是用 breakdown 找出具体根因。
3. Motivation 末尾显式列出 challenge，设计章节按同样顺序回答。
4. Evaluation 开头列 research questions，之后每个小节回答一个问题。
5. 性能、正确性、breakdown、case study 和资源开销分开报告。
6. Discussion 主动陈述为什么没有采用看似自然的替代设计。
7. 图大多使用双栏宽度，caption 自包含，读图时不依赖正文上下文。

## 4. AgentTX 应保持的差异

AgentTX 不应复用参考论文的句子或文件系统术语。参考论文的中心是减少 PM metadata
I/O；AgentTX 的中心是把长期 agent trajectory 转化为 durable causal effect
transaction。初稿因此把核心贡献收敛为：

- Dependency discovery：从 opaque tool calls 恢复 R/N/W/D effects 和 producer--consumer edges；
- Object identity：处理 path hierarchy 与 symlink alias，并诚实暴露 hard-link substrate 边界；
- Selective reconstruction：只撤销失败 producer 的因果闭包，同时保留时间上更晚的独立工作；
- Durable control：用 commit frontier、policy 和 WAL 将 speculative state 安全落到 host。

当前外部 baseline 尚未在同一 VM 上全部运行，因此初稿把 checkpoint 和 whole-branch
结果明确标注为 recovery-granularity emulation，不能写成外部系统端到端性能比较。
