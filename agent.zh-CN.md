# AgentTX 实验规范

本文是 [agent.md](agent.md) 的中文译本，保留原文的章节编号、约束、参数、命令和结果边界；如有歧义，以英文原文为准。文中的“当前”、2026-10-06 运行状态及 Conda 路径属于原文的历史记录。恢复后的最新环境和实验进度见 [当前实验进度](docs/EXPERIMENT_STATUS_20261017.md)，不要将下文的实验要求当作已经完成的实验。

**本机执行更新，2026-10-17。** 代码修改、环境准备、核验和后续实验均在本机 `r760-ssd` 完成。远程机器仅作为参考代码来源，其准备队列已停止；此前远程镜像和 TB 检查仅保留为历史诊断，不计入本机就绪进度。代码保留在本项目，Docker 镜像层、缓存、结果、日志和临时文件使用 `/mnt/data/agenttx-runtime`。通过 `scripts/agenttx-local-env.sh` 选择本机专用 Docker socket 和数据路径，不重启原有共享 Docker。本机已通过 Alpine 拉取、断网容器运行、Docker 构建及运行验证，以及 18 项相关回归测试。恢复的缓存、日志和结果数据经内容校验后迁移，项目原路径改为软链接。

当前仅进行环境准备。本机队列使用两个 worker 核验 300 个 SWE 镜像，并向数据盘缓存补齐固定版本 TB 输入。完整任务环境仍需通过 base commit 干净源码、依赖和官方验收检查。保留失败和中断尝试，不自动启动模型，也不按结果重试。最新本机进度持续写入[环境准备记录](docs/DOCKER_ENVIRONMENT_20261017.md)。

**镜像拉取超时规则更新，2026-10-17。** 新环境批次 r3 替换 r2 的“累计 30 分钟即超时”：读取 Docker API 的下载／解压字节和完成层事件，连续 900 秒无实际进展，或单个源累计 14400 秒时才超时换源；明确的镜像源或连接错误立即换源。源顺序为 `docker.1ms.run`、`hub.rat.dev`、`docker.1panel.live`，不直接回退上游。已针对实际卡住的 SWE 镜像核验候选源的 manifest、配置摘要及大层抽样，三个可用源返回相同 manifest 摘要；抽样可达不代表完整大镜像已拉取成功。7 项超时／进展测试及本机 API 拉取、断网容器冒烟验证通过。缓存镜像重新核验而不重复下载，固定版本 TB 的 1818 个文件及 SWE 元数据已在本机重新校验。r2 的 121 个就绪镜像、9 个错误及切换时主动中断的 2 个拉取均保留原始记录，中断另记，不修改旧结果。新规则只针对环境下载，模型预算不变。详见[超时诊断与证据](docs/IMAGE_PULL_TIMEOUT_20261017.md)。

**此前启动、现已暂停的批次。** 新的[分层 pilot](experiments/plans/stratified_pilot_20261017/registration.json) 已在远程验证机器启动，最多 **8 个实验进程并行**。先跑 84 个不调用模型的运行时检查（三个家族共 12 个 canonical manifest × 7 策略）；全部通过后，才启动队列中的 168 个模型单元（12 × 7 × 2 框架，各一遍）。Codex 和 SWE-agent 均使用 `gpt-5.6-luna / max`，统一模型时限 900 秒、无模型调用次数上限，定位探测上限按 `2 * operation_count + 8` 在执行前固定。本批与此前 240 秒复跑分开统计，固定 token 预算和模板计数匹配仍是正式实验的前置条件。保留所有失败和超时，不按结果挑选重跑。执行源码冻结为 v5：`938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`。状态快照和完成证据见 [pilot 进度](docs/STRATIFIED_PILOT_20261017.md)。

完整系统微基准明确覆盖**两个框架**：400 manifest × 7 策略 × 3 遍 × 2 框架 = **16800 单元**。旧 8400 行登记没有框架维度，继续保留为不可覆盖的历史计划。真实任务仍为 5410 单元（300 SWE + 241 TB，五策略，双框架），pilot 结果不填入正式矩阵。

本文件规定实验的权威要求。证据必须按以下顺序产生和呈现：

```text
微基准
    -> 真实工作负载（SWE-Bench Lite 和 Terminal-Bench）
    -> 系统组件分析
    -> 附录中的正确性与恢复中断测试
    -> 其他开销
```

各层证据不能互相替代。微基准结合分析型参数扫描与完整、可复现的长 agent 轨迹；两个官方测试集提供真实仓库和 shell 工作负载；组件分析将结果归因到具体机制；正确性测试验证不变量；开销实验则界定端到端完成指标未能体现的成本。

已有的 400 案例 Motivation 集合继续作为受控微基准层。独立的原生轨迹审计可以检验真实 agent 工作中是否存在相应恢复机会，但必须使用自己的抽样总体和分母。先前未完成的 541 任务运行仍属于审计数据，不能视为已完成的真实工作负载结果。最终论文结果必须从冻结的代码版本按下述协议重新生成。

# 第一部分：Motivation 微基准

## 1. 目标

测量包含多个 agent 轮次的分支中，不同恢复粒度的行为。扫描 `dependency_value` 和任务长度，后者用 `turn_count=1..10` 表示；不能只选一个稀疏的单轮图来放大理想 oracle 的优势。

这是受控微基准，类似系统基准中的线程数扫描。它用于建立 Motivation 论点背后的机制：

> 当时间顺序不能准确代表因果依赖时，按时间、轮次或分支回滚会浪费工作；理想选择性回滚界限量化了精确恢复机制可能保留多少有效工作。

重新生成受控 Motivation 图不需要运行 SWE-Bench 或 Terminal-Bench。拟议的真实轨迹发生比例图单独使用这两个测试集；其端到端策略结果仍属于第二部分。

## 2. Motivation 论证与统一微基准

关于恢复边界的三个观察使用同一个受控微基准家族。原生轨迹中的发生比例可在其后作为单独标记的外部有效性证据，但它的案例、分母和 token 权重代理不能与受控 manifest 或真实模型 token 混合。受控论点是：

> 现有恢复边界遵循执行结构——时间、轮次或分支，而污染沿依赖传播。回滚不足会留下无效后继；正确的粗粒度回滚又会删除独立有效工作。随着任务长度、故障位置和依赖结构变化，没有一种固定粗边界始终占优。

所有受控实验使用同一个确定性文件系统效果生成器、规范策略定义、配对 manifest 和验证器。原生轨迹审计则使用实际观测到的工具效果。理想 oracle 是分析上界，不是已实现系统。Motivation 不得运行、绘制或声称获得 `agenttx_causal` 的结果。

### 2.0 Motivation 写作提纲（权威要求）

叙事结构参考 [WOLVES（OSDI '25）](https://www.usenix.org/conference/osdi25/presentation/pan) 的证据组织顺序：分析现有路径，测量并分解成本，说明意义，解释渐进式尝试为何仍不充分，最后推导关键洞见和设计要求。借鉴的是论证推进方式，不是其文件系统论断或术语。

本节必须回答一个核心问题：

> 为什么仅按执行结构定义的恢复边界，无法同时为多轮 agent 提供完整恢复和较低的附带损失？

三个观察不是并列事实，而是以下因果链：

```text
粗边界只有多删才能正确恢复
    -> 因为时间顺序没有编码故障依赖
    -> 因而选择或调优某一种固定粗边界不能解决问题
    -> 恢复需要在长生命周期轨迹内部定义基于依赖的恢复单元
```

#### 3.1 分析有状态 agent 的恢复路径

用一个短段落和 Background 中的恢复边界表开篇。按能恢复的状态对现有机制分组：失败步骤、轮次、时间后缀、整个分支。中立地说明共同局限：这些边界由执行结构定义，而不是由哪些后续效果实际受到故障影响来定义。此处不介绍拟议系统，也不预先宣称因果关系就是答案。

本小节相当于参考论文的 I/O 路径分析：在测量开销前，先梳理各代表机制如何到达恢复后的状态。使用同一个多轮示例，展示仅根恢复、轮次检查点、时间步后缀和整分支回滚各自选择的集合；理想集合只用作目标参照。解释每条路径保留了什么，以及必然无法区分什么；本小节不报告基准百分比。

当前 `paper/main.tex` 中的 **Recovery Boundaries in Stateful Agent Execution** 小节应放在这里，移到实验之前，不能留在观察 1–3 后面。将现有边界和状态传播段落压缩到路径分析中，设计要求移入下面的第 3.4 节。

#### 3.2 分析恢复开销

本小节对应参考论文的崩溃一致性开销分析。先说明方法，再报告分解结果、三个观察及其意义。所有结果都应是第 3.1 节恢复路径的后果。

在此处而非路径分析中定义两个测量轴：

1. **恢复完整性：** 故障影响的效果中，有多少被移除？
2. **附带恢复成本：** 在满足完整性之后，有多少独立有效工作被丢弃？

这样可以避免 `no_recovery` 或 `root_only` 仅因留下污染后继就显得高效。

##### 方法：一个受控工作负载家族与配对总体

只介绍一次多轮微基准：每轮 16 个操作、1–10 轮；控制故障位置、依赖值、图形状和交错方式；六种策略使用完全相同的 manifest 与故障根。

三个主要观察和后续每个完整系统微基准实验都使用同一组 400 个配对 manifest 作为统计总体：

- 100 个任务长度案例；
- 90 个故障位置案例；
- 210 个依赖值案例。

论文应只定义一次这组 400 案例总体，并在三个受控观察中复用。某小节可放大某个已登记扫描以解释机制，但随后必须回到同一个全套总体，不能把切片说成新工作负载。第 2.8 节的观测型原生轨迹审计分母不同，不能当作这 400 案例的另一个切片。

##### 观察 1：正确性带来附带损失困境

**论点。** 正确的粗粒度恢复会移除无效闭包，同时丢弃有用工作；成本低但恢复不足不是有效替代方案。

**证据。** 从整个共享集合的正确性门槛开始。不恢复策略移除无效闭包的比例为 0%；仅根恢复的平均比例为 17.03%。轮次检查点、时间步后缀、整分支回滚和理想 oracle 均移除 100%。在通过正确性要求的策略中，轮次检查点、时间步后缀和整分支回滚平均丢弃 54.02%、56.90% 和 100% 的有效工作，理想 oracle 不丢弃有效工作。任务长度扫描只用于具体展示放大效应：故障闭包固定为四个操作时，分支越长，粗粒度回滚越大，而精确目标不变。

**意义。** 现有边界面临正确性与保留之间的困境：少回滚是不正确的，安全回滚又可能抹掉长任务中的大部分有用进展。

**图的作用。** `FIG-Motivation-Suite` 是观察 1 的独立图，展示 400 个配对案例的正确性门槛与附带成本分解。`FIG-Motivation-Coarse-Waste` 保留为单独的受控规模图，不要与三个观察图合并。论文中 `FIG-Motivation-Suite` 放在单栏内，两个内部面板横向排列。

##### 观察 2：时间顺序与依赖关系错位是根因

**论点。** 浪费不是某种 checkpoint 策略的偶然属性；根因是故障沿依赖传播，而粗粒度恢复遵循时间、轮次或分支。

**证据。** 在观察 1 已建立症状后，才在此引入 `dependency_value`。共享的 400 个 manifest 中，390 个恢复目标（97.5%）不是执行前缀状态。已登记的依赖扫描中，只有依赖值接近 1 时，时间恢复才接近精确界限；依赖稀疏时，独立工作与受影响工作交错，时间步后缀回滚会删除夹在其中的独立效果。

**意义。** 精确恢复后的状态往往从未作为时间 checkpoint 存在过。更快地创建 checkpoint、更密地放置 checkpoint，或更便宜地放弃分支，可以降低机制开销，但无法表达这种非前缀状态。

**图的作用。** `FIG-Motivation-Dependency` 是根因图。横轴为依赖值，曲线展示丢弃的有用工作、保留的有效工作和回滚放大。每一项机制论断必须对应图中可见的趋势。图放在单栏内，所有内部面板横向对齐。

##### 观察 3：换一种固定边界也不能解决问题

**论点。** 没有一种静态粗边界能在不同工作负载结构中始终最佳。

**证据。** 复用同一组 400 案例。按归因权重衡量，139 例中轮次检查点浪费较少，251 例中时间步后缀策略较少，10 例相同。已登记切片之间的排序会反转：轮次检查点在任务长度切片的 60/100 例和故障位置切片的 60/90 例中占优，时间步后缀策略在依赖切片的 191/210 例中占优。即使选取最好的固定边界，其丢弃的有效 token 权重也比两种粗策略事后逐例最小值多 10.5%。

**意义。** 这排除了对观察 2 最直接的渐进应对：在全局选择更好的轮次检查点或时间步后缀策略。边界选择会改变丢失哪部分独立工作，但固定执行边界都不能表达所需的恢复集合。

**图的作用。** `FIG-Motivation-Boundary-Crossover` 是局限性图，同时报告排序反转和固定策略成本。逐例最小值是用于诊断的粗策略包络，不是理想 oracle，也不是已实现的自适应策略。图放在单栏内，两个内部面板横向排列。

第 3.2 节最后用一个**意义**段落串起三个观察，不重复数字：正确的粗粒度恢复会浪费工作，这种浪费来自结构而非实现细节，任何固定粗边界都无法消除这一权衡。该段不引入解决方案。

当前 `paper/main.tex` 的 **Recovery Microbenchmark** 小节成为第 3.2 节。恢复策略定义移到第 3.1 节，工作负载与测量留在这里，删除观察 1 和观察 2 之间独立展开的全套总体说明。

#### 3.3 真实轨迹发生比例与案例剖析

在受控观察之后，呈现第 2.8 节独立来源的审计，回答真实 agent 执行中是否包含会被正确粗粒度回滚丢弃的独立有用工作。在任何百分比前列出完整的任务、轨迹、轮次和候选故障分母。展示零机会案例，以及一个正例和一个负例轨迹。

将反事实结果称为*保留机会*，不能称为实际观测的崩溃、已实现的 token 节省或已测得的 AgentTX 结果。若合格样本仍很少，应把分析移到附录并缩小正文论断，不能从六轮 pilot 外推。

#### 3.4 Motivation：已有尝试、洞见与设计挑战

按参考论文 Motivation 的相同顺序结束本节：

1. **已有尝试。** 仅根恢复保留工作但不满足完整性；轮次检查点把损失限制到受影响轮次，却无法保留轮内有效工作；只有所有后续工作都依赖故障时，时间步后缀回滚才精确；整分支回滚是安全但最粗的方式。提高 checkpoint 频率改变成本，不改变可表达的状态。
2. **洞见。** 推测执行的生命周期与恢复单元是两个不同的系统决策。保留轨迹作为推测执行单元，但按受影响效果定义恢复单元，不按连续执行区间定义。
3. **设计挑战。** 提出洞见隐含的非平凡问题：（i）如何发现不透明工具调用与后代进程中的依赖；（ii）如何跨别名、rename、替换与 copy-up 跟踪对象身份和历史版本；（iii）如何在工作区与 agent 可见上下文之间一致重建非前缀状态；（iv）如何只发布已批准的持久化前沿，而不暴露推测执行后缀。
4. **过渡。** 最后说明下一节将引入应对这些挑战的抽象。Motivation 中不出现 AgentTX 名称，也不介绍实现机制；系统名应在后续设计节开头出现。

##### 章节流程与图的顺序

正文预期顺序：

```text
3.1 梳理现有恢复路径（不作性能论断）
    -> 3.2 统一方法与全套正确性门槛
       -> 观察 1 + 正确性/附带成本图
       -> 观察 2 + 依赖扫描图
       -> 观察 3 + 边界交叉图
       -> 综合意义
    -> 3.3 独立的真实轨迹发生比例 + 正反案例剖析
    -> 3.4 已有尝试 -> 洞见 -> 设计挑战 -> 过渡
```

三个观察图保持独立。不要把 `FIG-Motivation-Suite`、`FIG-Motivation-Dependency` 和 `FIG-Motivation-Boundary-Crossover` 压成一张组合图。单独的 `FIG-Motivation-Coarse-Waste` 在观察前记录受控任务长度、时间顺序和回滚放大的趋势；单因素敏感性图仍是辅助材料。

### 2.1 MB1：粗边界的规模变化

**问题。** 真实故障闭包保持较小时，分支从一轮增长到十轮，每种正确策略会多删除多少不必要的工作？

**输入。** 扫描 1–10 轮，每轮 16 个操作。故障固定在完整分支的 25% 位置，无效闭包固定为四个操作。每种轮数使用 10 个配对 manifest，保留完整策略 manifest 以便审计，但效率论断不纳入不安全的对照。

**图 1a/1c。** 扫描每轮 16 操作、1–10 轮，报告有效操作丢弃量与回滚放大。故障闭包始终四个操作，因此增长来自任务长度，而不是故障规模。

### 2.2 MB2：时间顺序敏感性

**问题。** 依赖不变、故障沿执行顺序移动时，时间步后缀策略会如何变化？

**输入。** 固定任务长度 10 轮、每轮 16 操作、闭包大小四个；全局故障位置从 0.10 扫到 0.90，每点 10 个配对 manifest。

**图 1b。** 报告有效操作丢弃量。轮次检查点跟随包含无效效果的轮次，整分支回滚策略跟随完整分支，理想 oracle 始终精确，时间步后缀策略的浪费随后缀变化。这将时间顺序与依赖结构分离。

### 2.3 MB3：依赖值扫描

**问题。** 因果密度从稀疏到稠密时，错位如何变化？

**输入。** 五轮、每轮 16 操作、全局故障位置 25%、layered 图、alternating 交错；覆盖 `dependency_value` 从 0.00 到 1.00 的全部 21 点，每点 10 个配对 manifest。

**图 3。** 为四种正确策略绘制有效工作丢弃、有效工作保留和回滚放大。保留所有预登记点，包括时间步后缀策略接近理想 oracle 的稠密案例。

### 2.4 固定边界交叉

**问题。** 任务结构变化时，一种粗恢复边界能否始终更优？

**输入。** 原样复用 MB1–MB3 的 400 个配对 manifest。不得生成新工作负载或选择有利子集。对每个 manifest，以 `useful_tokens_wasted` 比较 `turn_checkpoint_proxy` 和 `temporal_step`，报告哪个浪费更少，并保留平局。

**图 4。** 分别报告任务长度、故障位置、依赖值和合并切片中各策略浪费较少的频率；同时报告各固定策略的平均浪费和两者事后逐例最小值。逐例最小值是分析型粗策略包络，不是可实现的 oracle，也不是新增恢复策略。

### 2.5 辅助敏感性与分解

轮数、故障位置、图形状和交错方式的单因素扫描使用同一生成器和策略语义。支持独立论点时可放正文，否则放附录。全套汇总使用 MB1–MB3 的全部 400 个配对 manifest：100 个轮数案例、90 个故障位置案例、210 个依赖值案例。先报告所有策略的恢复完整性，再仅对移除完整无效闭包的策略测量附带恢复成本。

原生轨迹发生比例只能按第 2.8 节的独立观测协议支持 Motivation。真实模型重放、SWE-Bench 与 Terminal-Bench 的端到端策略结果属于 Evaluation，不能与 400 个受控 manifest 混合，也不能替代其扫描。

### 2.6 正文图的安排

Motivation 图的数量不设固定上限，但正文应遵循第 2.0 节的三个观察链条。当前产物与其对应如下：

1. **正确性与保留困境：** 使用 `FIG-Motivation-Suite` 的全套正确性和附带成本结果支持观察 1。`FIG-Motivation-Coarse-Waste` 展示受控任务长度、时间顺序和回滚放大趋势。
2. **时间与依赖错位：** `FIG-Motivation-Dependency` 中的 MB3 支持观察 2。MB1–MB3 中 390/400 个精确保留状态不能由单个时间前缀 checkpoint 表达，说明执行顺序与故障依赖发生偏离的频率。
3. **粗边界交叉：** `FIG-Motivation-Boundary-Crossover` 使用同一组 400 个 MB1–MB3 manifest，通过轮次检查点与时间步后缀恢复在不同登记切片中的排序反转，支持观察 3。

轮数、故障位置、形状和交错方式的单因素扫描保留在 `FIG-Motivation-Sensitivity` 中，作为稳健性辅助证据，不算独立的正文观察。

`FIG-Motivation-Suite` 与 `FIG-Motivation-Coarse-Waste` 保持分离：前者支持观察 1，后者展示受控规模变化。只有支持独立观察时，正文才可新增另一种受控视图。真实轨迹发生比例图若通过第 2.8 节验收，应放在观察 3 后，作为独立来源的外部有效性图；它不是第四个受控观察，也不是 400 案例图中的一个面板。

`FIG-M1-Strategy-Outcomes` 是历史非规范图，`FIG-M2-Extreme-Tradeoff` 是同一个 MB1 端点的历史渲染，不能把它们算作新增证据。

### 2.7 当前数据状态

以下状态按英文原文的记录保留：

| 证据 | 协议 | 状态 |
| --- | --- | --- |
| MB1 任务长度变化 | 1–10 轮，每点 10 个配对 manifest，每轮固定 16 操作，闭包固定为 4 | 完成；100 个配对案例 |
| MB2 时间顺序 | 9 个故障位置，每位置 10 个配对 manifest，10 轮 | 完成；90 个配对案例 |
| MB3 依赖扫描 | 21 个值，每值 10 个配对 manifest，5 轮 | 完成；210 个配对案例 |
| 边界交叉 | 从同一组 400 个 MB1–MB3 配对 manifest 推导 | 完成；没有新增案例 |
| 辅助敏感性 | 轮数、故障位置、形状、交错 | 完成；没有缺失的配对单元 |

### 2.8 真实 agent 轨迹发生比例与案例研究计划

**问题与范围。** 当真实 agent 的某个实际修改状态的步骤发生故障时，正确的轮次、时间或分支级恢复会丢弃独立有用工作的轨迹有多少？这是观测型的*机会*研究，不能估计 agent 自然故障频率或恢复系统已实现的节省。YoloFS 的 290 份报告研究分类的是公开误用报告；这里的单位是观测到的 agent 执行，不是事故报告或合成 manifest。

**已有证据与缺口。** 当前原生 SWE-agent pilot 有 20 条成功的源/重放轨迹，共 638 轮。其中三个任务的六轮通过了“至少两个独立有状态组件”的严格门槛。其反事实 token 权重是序列化工具调用 payload 大小，不是 LLM 用量。官方 541 任务实验预置了合成的崩溃前轨迹并随机注入故障，衡量的是注入条件下的恢复，不能反映未经修改的 agent 行为中该结构的普遍程度。任一数据集单独都不足以支持机会很常见的论断。

**总体与采集。** 对每个官方任务，从干净工作区运行一次真实 agent，不注入故障、不回滚、不预置参考解、不提供恢复提示。这次干净执行就是原生轨迹；保留原始 agent 轮次与每个工具步骤，包括只读和失败步骤。分别建立四个独立的源系统/测试集分层：Codex / SWE-Bench Lite、Codex / Terminal-Bench、SWE-agent / SWE-Bench Lite、SWE-agent / Terminal-Bench。每个源系统分层都以全部可用的 300 个 SWE-Bench Lite 和 241 个 Terminal-Bench 任务 ID 为目标，不按已观察优势选样。

分析前冻结模型（两种 agent 均为 Luna max）、harness、普通任务提示词和任务版本。保留每个采集尝试的任务 ID、尝试序号和配置来源，包括部分执行和失败；不能静默去重重复尝试。已有 clean 运行若还保留原生步骤记录，可以复用；否则使用被动 trace 采集重跑 clean。当前 `clean_recovery` 提示词描述的是恢复上下文对照，`no_fault` 使用普通任务提示词但缺少完整事务步骤导出；任一模式的聚合 CSV 都不能单独算作原生依赖轨迹。

任务成功单独记录，不能据此过滤已记录步骤；通过流量表报告日志缺失、重放不匹配、环境错误、不支持的效果和重复尝试。公开的 SWE-agent/Claude 轨迹属于另一个源系统分层，不能静默并入新的 Codex/Luna 轨迹。不能从官方实验中的合成恢复文档推断原生依赖。测试失败、执行不完整、commit 失败和采集警告都是标签，不是分析纳入条件。

Terminal-Bench 干净轨迹必须让 agent 在任务官方 Docker 镜像中运行，保留 `/data` 等镜像路径；验证器文件不能放进 agent 可见工作区。最终工作区通过官方镜像的 `run-tests.sh` 后才能标记成功。缺少验收依赖或下载失败是环境错误，不是任务失败。早期主机投影的 clean 预检排除在该总体之外。原文所述的正式 Terminal-Bench runner 仍在主机上执行 agent，不能仅因最终通过 Docker 验收就称其为镜像内原生运行。

**机会普查。** 采集每个真实步骤观测到的读、写、删除、negative lookup、对象身份、轮次、时间戳和工具结果来源。对每个之后还有观测工作的实际写/删除步骤，反事实地将其标为故障，计算观测依赖闭包和每种正确粗边界删除的有效步骤。只有文件系统和显式上下文依赖均被核算时，保留步骤才可计入保留机会；无法解析的效果标为不确定，不能呈现为已证明的节省。

查看结果前冻结候选步骤规则与策略语义。保留所有零机会任务与所有合格故障位置，主要统计不得为每个任务挑选最有利根。另以每条观测边跨越的步骤数、轮数、每任务最长有向依赖路径，以及合格根的依赖工作延伸距离来刻画长依赖。长链本身不证明恢复浪费：要统计与长链交错的独立有用步骤，并与各粗边界会丢弃的步骤比较。

**报告分母与指标。** 四个 agent×测试集分层分别报告唯一任务 ID、采集尝试、记录轮次与步骤、修改状态的根，以及归因警告。不得合并 Codex 与 SWE-agent 的轮级指标：Codex 一次 `exec` 是含多个原生工具事件的 CLI 轮次，而 SWE-agent 暴露自己的模型轮次。

主要发生比例是“至少包含一个正机会位置的记录任务比例”，并同时报告“存在正浪费的合格位置比例”；前者必须标为*存在性*指标，不是故障概率。聚合均值对任务等权，bootstrap 区间按任务抽样，避免长轨迹支配结果。报告零值占比、被丢弃有效步骤与序列化工具结果 token 权重的中位数/P90/P99，以及轮内/跨轮分解。对相同故障根比较轮次检查点、时间步后缀、整分支回滚和理想 oracle；仅根恢复留下无效后继时，不纳入效率论断。

还要报告按步骤与轮次计量的边跨度和最长路径分布、含跨轮依赖的任务比例，以及按依赖跨度和交错独立工作条件化的浪费。工具结果 token 权重是工作量代理，不能当作模型或重放 token 用量。

**验证与案例重放。** 用原始 syscall/effect 日志审计预登记的正、零、未知分类样本，报告分歧，对依赖歧义保守处理，不认证其独立性。在模型继续执行前，从固定分层选择案例：高/中/零机会、轮内/跨轮、两个测试集；不能按已实现节省挑选。每种策略在相同真实步骤注入同一故障，执行官方验证器、保留文件哈希和 agent 上下文检查。记录全部成功、失败、环境排除、模型 token、重放 token 和墙钟时间。只有确认原生轨迹与故障计划相同后才能比较重放案例。理想边界是 oracle 下界；已实现 AgentTX 的结果属于 Evaluation，不属于 Motivation 的机会发生比例论断。

**输出与验收。** 输出冻结的总体 manifest、源轨迹哈希、逐步骤效果/边记录、逐根策略反事实、含零值的任务级汇总、排除表，以及可复现的正负案例时间线。正文发生比例图必须展示分母和零机会占比；机制可用单独案例时间线解释。两个真实工作负载分层都具备足够可用覆盖、依赖审计通过之前，不得声称广泛存在。若发生比例低或某个测试集缺少可用轨迹，应报告该结果并缩小论文论断，不得修改总体或故障位置规则。

**实现路径。** 在官方 clean 任务路径销毁工作区和 session 之前，增加被动步骤/效果导出，保留普通 `no_fault` 任务提示词，检查 tracing 不改变 agent 可见工作流。持久化完整工具调用序列、轮次边界、ledger/effect 来源与源配置，并验证 `fault_injected` 和 `precrash_progress_seeded` 为 false。

`replay_sweagent_native_traces.py` 只用于重放不变的源动作序列并导出效果来源；Codex 原生轨迹通过其任务 harness 采集，不能从带注入的官方运行 CSV 重建。将 `analyze_intra_turn_traces.py` 的六轮资格门槛扩展为保留每个可用任务、轮次和候选根（含零值）的普查。扩展 `bench_real_trace_counterfactual.py` 输出完整逐根策略表和不确定性标签，不能把已实现 causal 恢复画成 Motivation 证据。

旧 20 轨迹 pilot 保持不可变。新的总体、原始审计、排除、图和案例重放输出放进单独版本的 `experiments/results/motivation/real_trajectory_audit/` 目录，不覆盖受控 400-manifest 结果。

**2026-10-06 已开始执行（历史记录）。** 第一批使用原生 SWE-agent / Luna max 和普通无故障任务指令。通过进程内适配器记录每个动作，不注入崩溃、不预置参考 patch、不提供恢复提示。两个完成的预检案例 `django__django-10924` 与 `analyze-access-logs` 均显示跨轮工作区文件依赖，但未观察到独立修改状态的保留步骤；两者保留为零机会案例。

运行目录的 `cohort_phase1.json` 预登记了 12 任务第一阶段样本：每个测试集的短/中/长分层各选两任务。两个 agent 使用相同任务 ID，在四个分层共形成 24 个 agent-任务单元。镜像内输出在 `experiments/results/motivation/real_trajectory_audit/` 下按 agent 与 suite 分开；早期 `clean_native_luna_max_20261006` 主机投影 Terminal-Bench 记录不属于官方镜像总体。

独立的 20 轨迹公开 SWE-agent/Claude 重放只作方法校准分层，不并入 Luna。当前分析统计观测到的工作区路径依赖并排除工具/缓存路径；不能据此认证其他所有工作独立，也不能把步骤数换算为模型 token 节省。

Codex 普通外部事务是一个进程步骤。另设的被动原生采集观测 Codex JSON 工具事件，在每个事件区间内归因 syscall 读，并在事件边界记录工作区写入指纹。对修改状态的工具事件重叠、不支持的此类事件或无法归因的最终写入，排除相应轨迹；只读事件重叠时保守地多归因。Codex `web_search` 是真实步骤，但也是不透明的上下文屏障，跨过它的工作永不认证为独立。不能把 Codex 当作一个事务步骤，也不能人为推断模型轮次边界。

四个分层都在各自官方 Docker 任务镜像中验证后再做总体分析，早期主机投影的 Terminal-Bench 轨迹继续排除。截至 2026-10-06，冻结的六任务 pilot 在四个 agent×测试集分层各记录六条原生轨迹，共 24 条。

SWE-agent / Terminal-Bench 集合包含一个已提交但官方测试失败的案例，以及一个记录真实步骤后以 agent 退出格式结束的案例。Codex / SWE-Bench 的 Requests 案例也在有效采集 39 步后未通过官方测试。三条轨迹全部保留在步骤普查中。官方测试失败不能作为删除已记录步骤的理由；verifier/环境错误使成功标签保持未知。修改状态的 Codex 工具事件重叠时，步骤归因不确定，保留并标记采集警告。早期 SWE-Bench pilot 的 verifier 未应用官方 test patch，因此即使六条步骤轨迹已记录，其成功标签仍未知。所有 pilot 分母、重复尝试与警告都记录在真实轨迹审计下的四个 `analysis_*_phase1` 目录。

24 单元 pilot 后，四个持续运行的完整总体采集从每种 agent 剩余的 294 个 SWE-Bench Lite 与 235 个 Terminal-Bench 任务启动。其 tmux 会话为 `agenttx_native_{swe,codex}_{swe,tb}`，日志为结果目录旁的 `full_{sweagent,codex}_{swe,tb}_20261006.log`。原文此时记为进行中，不能视为已完成评估。SWE-Bench 采集允许 Astropy 镜像构建时在 `pyproject.toml` 中把 `setuptools` 精确固定为 `68.0.0`；其他源码内容差异仍不能通过 image/base 检查。

## 3. 硬性约束

- 仓库：`/home/bfq/agenttx`。
- Python：`/home/bfq/miniconda3/envs/agenttx/bin/python3.11`。
- Motivation 中不得运行或绘制 `agenttx_causal`；其系统实现结果属于后续 Evaluation，不属于机会研究。
- 同一配对单元中的各策略必须接收相同的物化轨迹、故障根、依赖图、token 权重和 seed。
- 不得删除不利点、失败单元或稠密依赖点。
- 单个极端稀疏案例可保留为附录回归测试，但不是正文证据，也不能替代参数扫描。
- Crab 只能用 `turn_checkpoint_proxy` 作为轮次边界的策略代理表示；不能将其数字称为 Crab 原始实现的实测值。

## 4. 依赖值

对单根轨迹，设：

- `r` 为故障操作；
- `S = {s | s occurs after r}` 为根之后的时间后缀；
- `C` 为 `r` 的传递因果闭包，包含 `r` 本身。

定义：

```text
dependency_value = |(C - {r}) intersect S| / |S|
```

因此，`dependency_value` 表示故障之后真正依赖故障的操作比例：

- `0.0`：没有后续操作依赖故障；
- `1.0`：全部后续操作都依赖故障；
- 中间值：依赖与独立工作共存。

该定义与分支长度独立，不能为满足指定闭包大小而移动故障根。生成器先固定总操作数与 `fault_position`，再通过对 `dependency_value * post_fault_operations` 取整确定依赖操作数。每条结果必须同时记录请求值与实际实现值。

旧字段 `closure_ratio = |C| / |E|` 仅用于兼容；它混合了故障位置与依赖密度，不能作为主横轴。

## 5. 恢复策略

中英文名称、策略 ID 和跨实验命名空间的对应关系见[术语表](docs/terminology.md)。

| 规范 ID | 图中标签 | 回滚边界 | 预期作用 |
| --- | --- | --- | --- |
| `no_recovery` | 不恢复（No recovery） | 无 | 正确性负对照 |
| `root_only` | 仅根恢复（Root only） | 仅故障生产者 | 依赖传播消融 |
| `turn_checkpoint_proxy` | 轮次检查点（Turn checkpoint） | 包含无效效果的每个完整轮次 | 对轮次边界有利的基线 |
| `temporal_step` | 时间步后缀（Temporal step） | 从故障根到分支结束 | 时间后缀基线 |
| `whole_branch` | 整分支回滚（Whole branch） | 完整未提交分支 | 最粗基线 |
| `ideal_oracle` | 理想 oracle（Ideal oracle） | 精确无效闭包 | 分析上界 |

这些 ID 与标签只在 `experiments/motivation_strategies.py` 定义一次。Motivation 图绘制全部六种策略。端到端模型实验使用独立命名空间：`clean_recovery`、`crash_no_rollback`、`temporal_checkpoint`、`whole_branch_abort` 和 `causal`；不能静默把它们改名成微基准策略。

`no_recovery` 与 `root_only` 可能成本低但不正确。token 或工作量节省只在移除完整无效闭包且通过所有正确性检查的策略之间比较。

## 6. 主扫描

主要实验是依赖密度的单因素扫描：

| 因素 | 主配置 |
| --- | --- |
| 轮数 | 5 |
| 每轮操作数 | 16 |
| 故障位置 | 25% |
| 依赖值 | 0.00–1.00，步长 0.05，共 21 点 |
| 图形状 | layered |
| 交错方式 | alternating |
| 每梯度 seed/案例 | 0–9，共 10 例 |

这 21 个依赖点均已预登记。全部报告，包括理想 oracle 与粗基线相同的点。

主图：

1. `dependency_value` 与有效操作浪费量。
2. `dependency_value` 与有效 token 保留率。
3. `dependency_value` 与理想 oracle 相对时间步后缀回滚和轮次检查点的节省。

采用统一折线图风格，每种策略一条线；展示均值和 95% 配对 bootstrap 置信区间，保留原始点。

## 7. 敏感性扫描

以主配置为基础，每次只改变一个因素：

| 扫描项 | 取值 |
| --- | --- |
| 轮数（任务长度） | 1–10 |
| 故障位置 | 0.10、0.25、0.50、0.75、0.90 |
| 图形状 | chain、fan-out、layered |
| 交错方式 | independent-before、alternating、independent-after |

每项敏感性扫描仍覆盖 `0.00` 到 `1.00`、步长 `0.05` 的同一组 21 个依赖值，每梯度 10 个 seed/案例。不要无必要地对所有干扰因素做完整笛卡尔积；预期设计就是主扫描与单因素敏感性扫描。

## 8. 指标

设全部操作为 `E`，无效闭包为 `C`，有效操作为 `V = E - C`，策略回滚集合为 `R_p`，记录：

```text
valid_retention        = 1 - |R_p intersect V| / |V|
invalid_removal        = |R_p intersect C| / |C|
useful_steps_wasted    = |R_p intersect V|
useful_tokens_wasted   = sum(token_weight(s), s in R_p intersect V)
invalid_steps_retained = |C - R_p|
```

同时记录请求/实际依赖值、故障位置、闭包大小、旧闭包比例、精确回滚集合、manifest 哈希、回滚时间和四种正确性视图：合并后的 overlay、conversation、已提交主机状态、verifier。

按 tokenizer 归因的工作量不等于模型计费用量。后续模型重放必须分别报告 prompt、completion 和 total 用量。

## 9. 正确性门槛

接受分析型扫描前必须：

1. 对每个请求依赖值，断言实际依赖操作数等于 `round(dependency_value * suffix_size)`，实际报告值等于该数量除以后缀大小。
2. 断言生成器按已说明的离散取整准确实现请求的依赖值。
3. 对已登记的全部五个轮数组和代表性依赖值运行实际 overlay 单元。每组使用六个不同 manifest，因为该门槛验证执行语义，不估计总体均值。
4. 对每个实际执行的策略，检查合并 overlay、conversation 活动调用、已提交主机状态和 verifier 结果。
5. 断言同一配对单元的各策略拥有相同 manifest 哈希。

固定的 128 操作稀疏轨迹保留为附录回归测试，不能作为独立正文 Motivation 结果。

## 10. 分解

对每个非 oracle 策略，将它与 `ideal_oracle` 的差距分解为保留的无效工作和额外丢弃的有效工作。这是策略粒度分解，不是 AgentTX 组件分析。

## 11. 微基准范围边界

AgentTX causal 的有效性、ledger/查询开销、compact context 成本、commit 延迟、RSS 和模型用量属于后续系统评估，不能混入本 Motivation 数据集。

## 12. 执行

先运行定向测试：

```bash
cd /home/bfq/agenttx
export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx
PY=/home/bfq/miniconda3/envs/agenttx/bin/python3.11

"$PY" -m pytest -q tests/test_intra_turn_recovery.py
```

运行确定性的依赖扫描：

```bash
"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 \
  --dependency-values 0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 \
    0.50 0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0 \
  --turn-counts 5 --widths 16 \
  --fault-positions 0.25 \
  --shapes layered \
  --interleavings alternating \
  --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark
```

运行 MB1–MB2：

```bash
"$PY" experiments/scripts/bench_controlled_motivation.py \
  --output-dir experiments/results/motivation/controlled_case
```

随后运行单因素敏感性扫描：

```bash
DEPENDENCY_VALUES=(
  0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 0.50
  0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0
)

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 1 2 3 4 5 6 7 8 9 10 --widths 16 --fault-positions 0.25 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/turn_count

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.10 0.25 0.50 0.75 0.90 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/fault_position

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 \
  --shapes chain fan-out layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/shape

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 --shapes layered \
  --interleavings independent-before alternating independent-after \
  --repeats 10 --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/interleaving
```

不能合并不兼容 schema 的结果，也不能混入早期只使用 closure-ratio 的运行行。

## 13. 必需产物

```text
experiments/results/motivation/microbenchmark/config.json
experiments/results/motivation/microbenchmark/motivation_recovery_raw.csv
experiments/results/motivation/microbenchmark/motivation_recovery_summary.csv
experiments/results/motivation/microbenchmark/REPORT.md
experiments/results/motivation/controlled_case/config.json
experiments/results/motivation/controlled_case/controlled_recovery_raw.csv
experiments/results/motivation/controlled_case/controlled_recovery_summary.csv
motivation/FIG-Motivation-Dependency.pdf
motivation/FIG-Motivation-Dependency.png
motivation/FIG-Motivation-Coarse-Waste.pdf
motivation/FIG-Motivation-Coarse-Waste.png
motivation/FIG-Motivation-Suite.pdf
motivation/FIG-Motivation-Suite.png
motivation/FIG-Motivation-Boundary-Crossover.pdf
motivation/FIG-Motivation-Boundary-Crossover.png
motivation/FIG-Motivation-Sensitivity.pdf
motivation/FIG-Motivation-Sensitivity.png
```

报告必须列出完整扫描网格、计划与完成的配对单元、正确性门槛状态、所有排除及原因，以及运行使用的 Git commit 或含未提交改动的工作树指纹。

## 14. 外部实现基线

论文的恢复比较应尽可能包含真实外部实现。表 1 用于分类语义，不表示每行都能运行。作者报告的数字与本机测量结果必须分开。

| 系统 | 原生边界 | 实现状态 | 实验状态 |
| --- | --- | --- | --- |
| try | 单个不透明命令的效果集合 | MIT；本地已固定版本 | 现在运行 |
| Sandlock | 一次沙箱进程运行 | Apache-2.0；本地可构建，但主机内核禁用 Landlock | 之后在启用 Landlock 的主机上运行，作为进程边界/开销基线 |
| YoloFS | session 快照 | GPL-2.0；本地已固定版本并挂载 | 现在运行 |
| BranchFS | 叶分支 | MIT；本地已固定版本并挂载 | 现在运行 |
| Waypoint | 文件系统与进程 checkpoint | Apache-2.0；已固定版本；CRIU 4.2.1 在特权评估容器通过 | 现在运行，但容器结果与裸主机延迟分开报告 |
| Crab | 沙箱 checkpoint | 未找到官方实现 | 仅定性比较，不得把代理标为 Crab |
| DeltaBox | 沙箱 checkpoint | 作者声明实现未公开 | 仅定性比较 |

统一的外部实现原生实验复用微基准物化的多轮 manifest。每个系统获得相同初始工作区、轮次边界、依赖值、故障位置和崩溃通知，随后只使用自身原生恢复操作：try 与 Sandlock 丢弃命令/进程效果，YoloFS 使用 `snapshot`/`travel`，BranchFS 使用叶分支 `abort`，Waypoint 使用 checkpoint `fork`，AgentTX 使用依赖选择恢复。不得把 AgentTX 的依赖调度器嫁接到外部实现上。

用同一个 oracle 验证所有恢复后工作区。报告无效状态移除、有效状态保留、恢复延迟、端到端延迟、setup 成本、存储放大和权限/内核要求。无法表达选择性保留状态的基线应使用其原生粗边界，记录其丢弃的有效工作。环境失败单列，不纳入性能聚合。

本机可立即进行的配对运行集合为 AgentTX、try、YoloFS、BranchFS 和 Waypoint。Waypoint 在特权、断网容器中运行，运行时目录从主机 ext4 bind mount；若没有明确匹配的容器运行，不得把其延迟与裸主机延迟合并。Sandlock 在启用 Landlock 的内核上再加入。适配器和原始结果必须记录上游 Git commit、本地 patch、命令行、主机指纹，以及每个单元真正运行了外部实现还是仅运行策略代理。

论文结果必须使用完整的 400-manifest Motivation 总体：100 个 MB1 任务长度案例、90 个 MB2 故障位置案例、210 个 MB3 依赖案例。Smoke test 与有利子集只能用于验证。外部实现 runner 通过 `--suite motivation400` 重建这些案例，任何 `trace_id`/`manifest_hash` 与登记的 Motivation CSV 不同时都拒绝运行。完整命令：

```bash
PYTHONPATH=src:. /home/bfq/miniconda3/envs/agenttx/bin/python3.11 \
  experiments/scripts/bench_external_recovery_artifacts.py \
  --suite motivation400 \
  --backends agenttx session_try yolofs branchfs waypoint \
  --workers 6 --resume \
  --output-dir experiments/results/external_recovery_motivation400_20260922
```

# 第二部分：系统评估

## 15. 系统微基准：端到端执行 Motivation 案例

完整系统微基准执行与分析型 Motivation 扫描完全相同的案例。它不是新工作负载，也不能替代官方真实工作负载测试集。一次运行中覆盖模型、工具 runner、依赖采集、恢复、上下文重建、修复和任务验证，同时保留相同的受控依赖结构。

### 15.1 工作负载

使用规范的 400 个 MB1–MB3 manifest ID，不改变轮数、操作顺序、图形状、依赖值、交错、故障根或预期保留集合。每个 manifest 物化为可执行仓库任务，生成任务描述和 verifier。生成任务必须精确保留 manifest 语义，不得为使结果更有利而添加依赖。

对同一案例 ID，各策略获得相同初始快照、任务文本、模型配置、故障计划和 token 预算。Pilot 可以执行预登记子集以调试协议，但子集结果不能写成论文结果，也不能替代完整 400 案例微基准。

### 15.2 策略与重复次数

运行 `clean_recovery`、`crash_no_rollback`、`turn_checkpoint`、`temporal_checkpoint`、`whole_branch_abort`、`causal` 和 `ideal_oracle`。

`ideal_oracle` 可以获得精确故障步骤和闭包；其他崩溃策略只能获得相同崩溃通知和各自的恢复边界。`causal` 必须通过 AgentTX 自身证据定位故障。

完整 400 案例系统微基准中，每案例、每策略重复三次。主模型及 reasoning 配置必须与真实工作负载一致。除无法避免的策略 payload 外，提示词模板必须做 token 匹配；这些 payload token 单独报告。

### 15.3 指标与验收

报告任务成功、无效效果移除、有效效果保留、工作区与上下文一致性、包含重放的模型总 token、工具调用、恢复延迟和端到端墙钟时间。效率比较使用配对成功单元，同时展示全部尝试的成功情况和成本。

只有满足下列条件，系统微基准才算完成：400 个 Motivation manifest ID 全部有完整系统结果；每种策略满足三次重复；没有静默排除环境失败；每个配对单元具有相同 manifest 与故障计划。

## 16. 真实工作负载：SWE-Bench Lite 与 Terminal-Bench

SWE-Bench Lite 与 Terminal-Bench 是论文的真实工作负载，不能称其为合成微基准，也不要另外增加一个玩具“真实场景”。

### 16.1 范围

运行全部可用测试集：300 个 SWE-Bench Lite 实例、241 个 Terminal-Bench 实例。论文主运行每任务、每策略一次，不要求完整测试集重复五遍。分层 pilot 可以重复以估计方差，但不能替代全量运行。

主路径使用真实 agent 和各测试集官方 verifier。Gold patch 和 oracle 解只允许用于环境验证或明确标记的 oracle 上界，不能当作任务成功结果。Terminal-Bench 还要求 agent 在任务官方 Docker 镜像中执行，仅把 verifier 放在镜像内不够。现有主机投影的正式 Terminal-Bench 行属于诊断，不能作为可比较的真实工作负载成功结果，也不得续跑或发布为镜像内原生批次。新的正式运行必须在所有模式中使用镜像内 agent 路径。

所有策略与两种 agent harness 必须冻结相同模型、reasoning effort、提示词、崩溃计划、任务集合、墙钟超时、verifier 和调用次数限制政策。Codex 没有对应 cap 时，不得从合成工作负载规模继承 SWE-agent 每任务 step/model-call cap。不传 `--max-turns` 表示不限制调用次数；任何显式 cap 必须记录在 `run_config.json`，并为 Codex 建立独立匹配协议。未完成单元与基础设施失败必须在分母中可见，不能把 expected-cell 数当作完成数。

### 16.2 恢复协议

每个启用崩溃的任务在不同策略间必须注入相同的已登记故障计划，不披露故障步骤。Agent 只能被告知发生了崩溃。粗策略应用其声明的边界；AgentTX 可以查询 ledger 后将推断的定位结果写入 recovery handoff；只有 `ideal_oracle` 可以直接披露精确失败步骤。

主模式为 `clean_recovery`、`crash_no_rollback`、`temporal_checkpoint`、`whole_branch_abort` 和使用 compact context 的 `causal`。Runner 暴露可区分的多轮边界时，再增加 `turn_checkpoint`。所有崩溃模式的故障数量和位置相同；`clean_recovery` 不注入崩溃，定义任务不可避免的基础成本，不能将其当作恢复节省。

### 16.3 指标

报告官方成功、故障恢复成功、工作区与上下文一致性、总 token（`main + replay`）、墙钟时间、恢复时间、工具调用和环境失败。先分别呈现 SWE-Bench 与 Terminal-Bench，再考虑合并。token 比较必须同时给出成功率和配对成功样本数；低 token 的失败运行不算节省。

## 17. 系统组件分析

组件分析使用同一组 400 个 Motivation manifest，使每个组件配置看到相同完整轨迹。比较：

| 配置 | 移除的机制 | 预期可观察失败/成本 |
| --- | --- | --- |
| Full AgentTX | 无 | 参照 |
| No READ/NEGATIVE capture | 自动生产者—消费者发现 | 无效后继保留下来 |
| Path-only identity | 别名/对象跟踪 | 别名相关污染仍在，或恢复保守拒绝 |
| Temporal history only | 面向对象的选择性重建 | 交错的有效版本丢失 |
| Workspace-only rollback | 统一上下文闭包与 rewind | 陈旧工具结果与文件不一致 |
| One-shot worker | 持久化执行 | 语义相同，每步延迟更高 |
| Full context handoff | compact recovery handoff | 语义相同，prompt/token 成本更高 |

原文要求前四行解释正确性和保留效果，最后两行解释工程成本。不能把语义错误的消融与延迟均值混合，伪装为性能优化。每一行均报告成功、无效移除、有效保留、上下文一致性、token 和墙钟时间。

## 18. 附录正确性与恢复中断测试

正确性证据放在附录与实验产物中，正文给出简要汇总。

### 18.1 语义矩阵

覆盖普通读/写/删除、negative lookup 后创建、同路径多版本更新、目录祖先、rename、symlink、支持的 hard-link 组、fd-relative 操作、多故障根、重复回滚、reload，以及对象身份不确定时保守拒绝的边界。每个案例断言：

1. 所有选中的文件系统效果都消失；
2. 所有受支持的独立效果都保留；
3. 所有无效工具结果片段都消失；
4. 保留消息与文件对应同一组活动步骤；
5. 第二次相同恢复为 no-op。

### 18.2 恢复中断实验

实验针对回滚过程中的中断，不是向用户工作区发布的过程。在以下恢复阶段注入 `SIGKILL`：

1. 统一恢复计划记录之后、重建之前；
2. 新 upper generation 建成之后、激活之前；
3. 工作区 generation 切换之后、上下文重写之前；
4. 上下文重写之后、元数据持久化之前；
5. 持久化之后、恢复响应到达 agent 之前。

每次中断后重新加载同一个 session 并继续恢复。唯一可接受的可观察状态是完整的恢复前工作区/上下文对，或完整的恢复后状态对。工作区与上下文混合、无效消息复活、独立效果丢失、重复恢复动作都算失败。每阶段至少注入 20 次，并公开所有阶段结果。这是宣称 crash-safe synchronized rollback 前必须通过的门槛。

## 19. 其他开销

测量任务成功或总墙钟时间无法体现的成本：

1. 按命令类型和 trace 体积测量依赖 tracing 开销；
2. 历史存储放大随仓库大小、改动字节和轨迹长度的变化；
3. ledger 与消息索引内存随工具步骤数的增长；
4. 闭包规划和重建延迟随闭包大小、受影响路径数、别名组大小、故障根数的变化；
5. full 与 compact handoff 的上下文重建时间和 prompt token；
6. 启动、销毁、reload 和持久 worker 回退成本。

有重复测量时报告均值、p50、p95 和 p99。除归一化百分比外，也报告 RSS、写入字节、快照字节、元数据字节和恢复延迟。不支持的单元与环境失败保持可见。

## 20. 全局执行规则

- 规范批次启动前冻结代码、提示词、模型、reasoning effort、任务版本和 verifier。
- 顺序为：分析型微基准、系统微基准 pilot、完整系统微基准、真实工作负载环境检查、完整 SWE-Bench/Terminal-Bench、组件分析、附录正确性/中断矩阵、其他开销。
- 每条结果记录 Git 版本与 dirty diff 哈希、环境、manifest 哈希、模型配置、提示词哈希、用量来源和排除原因。
- 只有将环境失败单列，并在完全相同冻结配置下重跑时，才能将其排除在任务成功分母之外。
- 不能只挑选有利案例，也不能混合代码、提示词或结果 schema 不兼容的运行结果。
