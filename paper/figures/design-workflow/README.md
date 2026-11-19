# Lancet 的可编辑设计图

正文使用五张单栏设计图：总览、依赖选择、版本保留、文件系统与上下文重建、Ext4/XFS 实现。总览区分 OverlayFS 原有结构与 Lancet 增加的依赖记录、对象版本和选择性恢复功能；各细节图使用相同的八条调用展开对应过程。图片使用白底、Times New Roman 字体；细节图中红色表示撤销、绿色表示保留、蓝色表示恢复，箭头不用绿色。上下文同步的独立图保留为可编辑素材。

## 贯穿三图的例子

| 调用 | 操作 |
|---|---|
| E1、E2 | 检查基线 |
| E3 | 错误写入 f1，返回输出文件名 report.json |
| E4 | 读取 f1，把 f 覆盖为 f2 |
| E5 | 独立更新 g1，穿插在受影响调用之间 |
| E6 | 读取 f2，删除 h |
| E7 | 复用返回的文件名，创建 p1 |
| E8 | 读取 p1 |

p 是返回文件名对应路径的简记。文件边为 E3→E4、E4→E6、E7→E8，以及同一路径历史产生的 E3→E6；结果边为 E3→E7。用户报告需要回退文件 f，参考 checkpoint C0 位于 E3 前；E3 和 E4 都是此后对 f 的修改。以二者为根，恢复集合为 S={E3,E4,E6,E7,E8}，E1、E2、E5 保留。turn 分组是 (E1,E2,E3)、(E4)、(E5,E6,E7,E8)。共享定义保存在 generate_diagrams.py 的 EXAMPLE 中，各图的布局报告记录同一份定义。

## 图的分工

| 图片 | 主要表达 |
|---|---|
| Figure 5 / Overview | 左侧实线框是原有 OverlayFS 执行视图；右侧蓝色虚线框标出 Lancet 新增的文件/结果依赖、对象版本、集合选择与验证、选择性恢复及上下文同步 |
| Figure 6 / Dependency Closure | 展开总览步骤 2：文件访问/修改、结果/参数记录作为证据，用户报告文件 f 及参考 checkpoint C0，取此后的写入 E3、E4 为根，联合依赖图计算 S，隔离残留检查通过后把同一个 S 交给文件系统恢复与上下文同步；不重复绘制 OverlayFS 状态和逐轮展开框 |
| Figure 7 / Version Capture | 区分增量对象历史与显式检查点，展示 retained mounts 的管理 |
| Figure 8 / Filesystem Reconstruction | 承接步骤 2 的集合与方案验证，执行步骤 3：在当前 upper 上恢复 f/h/p，保留 g1，并同步更新上下文 |
| Figure 9 / Implementation | 共享恢复控制器经 OverlayFS 使用 Ext4 文件复制或 XFS reflink |

Figure 8 中，各检查点展示同一 lower 上的 merged view：

| 版本 | 时点 | f | g | h | p |
|---|---|---|---|---|---|
| V0 | E3 前 | f0 | g0 | h0 | 不存在 |
| V1 | E6 前 | f2 | g1 | h0 | 不存在 |
| V2 | E7 前 | f2 | g1 | 不存在 | 不存在 |
| 恢复结果 | 在当前状态上组合 | f0 | g1 | h0 | 不存在 |

蓝色选择 f/V0、h/V1、p/V2，g1 从当前 upper 保留。没有任何一整行同时包含 f0 和 g1。此例的目标历史 upper 条目为空，移除当前 f2、h 的 whiteout 和 p1 后，merged 露出 lower 的 f0/h0，并恢复 p 的不存在状态。图中 WH 表示 whiteout，∅ 表示不存在。

## 样式与输出

正文图共用固定的字体和连接线标准。图 6 展开文件提示、联合依赖选择及隔离残留检查，并标出输入与下游恢复组件，文件系统状态留在图 7、8 中说明。Flow 为黑实线、File 为黑虚线、Result 为红实线；层间映射改用无箭头的细灰线，避免短线中出现过大的箭头。

线型只编码明确的区别：黑虚线箭头表示文件依赖，与黑实线流程、红实线结果复用区分；红虚线对象框表示待撤销效果，与实线保留/恢复对象区分。空对象已标为 ∅，使用灰实线框。图 6 的步骤 2 使用实线框，Residue audit 用蓝色虚线框表示隔离残留检查；图 8 仅隔离验证使用虚线分组框，实际 Apply S 使用实线框，图注说明验证框的含义。总览中的蓝虚线外框仍表示 Lancet 新增功能的边界。

- 全部文字使用 Times New Roman。节点文字和分组标题为 21 pt，辅助标注及步骤编号为 18 pt；仅分组标题加粗。∅ 使用数学字体 Cambria Math。
- 所有箭头的线宽为 1.60 pt，头部长 9.0 pt、宽 7.2 pt；方向、颜色、路径长度和转折均不改变头部尺寸。
- 箭头头部使用相同的原生三角形坐标输出 PPT 与 SVG，线身在三角形底边终止。不用随线宽缩放的 marker 或 Office 自动箭头头部。
- 所有箭头直接连接节点边框，不留悬空间距；upper/lower 的连接点按层片斜边计算。总览中两层分别连接 merged view，文件恢复与上下文同步从 Apply S 的不同端口出发，各自走线。分组边框在跨组连线处留出开口。需要转弯的连接使用连续三次贝塞尔曲线，PPT 与 SVG 输出相同的原生可编辑路径，保留起点和终点方向；直接相连的节点仍使用直线。层间组合线使用相同线宽，分隔线和分组边框单独使用较细的线。

| 原生 PPT | 正文 PDF | 预览 |
|---|---|---|
| Lancet overview.pptx | Lancet-Overview.pdf | overview-visual.png / .svg |
| Lancet-Dependency-Closure.pptx | Lancet-Dependency-Closure.pdf | dependency-closure-visual.png / .svg |
| Lancet-Version-Capture.pptx | Lancet-Version-Capture.pdf | version-capture-visual.png / .svg |
| Lancet-Design-Workflow.pptx | Lancet-Design-Workflow.pdf | design-visual.png / .svg |
| Lancet-Context-Alignment.pptx（素材） | Lancet-Context-Alignment.pdf（素材） | context-alignment-visual.png / .svg |

总览 PDF/PNG 同步到 paper/img/FIG-Lancet-Overview.* 和 motivation/FIG-Lancet-Overview.*。其余正文图直接引用本目录 PDF。生成器检查文字宽度、行高与画布边界，输出 layout-check-*.json。正文图均按完整单栏宽度排版，节点文字约 8.2 pt、辅助标注约 7.0 pt。图 6 保留文件提示及其修改根、全部文件/结果边和最终集合，移除重复的文件系统状态与扩展框；图 8 保留上下层状态、完整检查点矩阵、隔离验证和同步恢复流程。细节图共用 generate_diagrams.py 中的 DetailStyle，字体和箭头几何与总览一致。独立的上下文素材保持原样式。

总览中的 upper 和 lower 共同提供 merged view，不表示 lower 写入 upper；恢复箭头只指向 upper，lower 始终只读。布局报告记录路径、箭头头部尺寸以及各段文字的字体、字号和字重。

图 6 的顶部文件依赖绕线与文件提示流程之间留出空间。正文中的单栏图和跨栏图均使用 `[t]`，由 LaTeX 安排在栏顶或页顶。图 8 的检查点行距为 0.43 英寸，格框之间留出 0.13 英寸间隔；验证和应用阶段相应下移，避免通过压缩行距容纳内容。图 6 简化前的素材与正文保存在 review/design-figure6-simplified-20261117/before/；补充流程衔接前的版本保存在 review/design-figure6-role-20261117/before/。

图 7 的历史框与文件值框之间留出 0.34 英寸，横向历史框也保持 0.34 英寸间距，让箭头线身与边框分开。下方 RO + pin / Detach + mount 标注分列在流程线两侧；Checkpoint C1 与 Lower L 之间留出 0.18 英寸，检查点关联通过独立的平滑虚线接入只读 U0。曲线与间距调整前的源码、正文及所有素材保存在 review/design-curves-20261118T063857Z/before/。

总览用两个对齐的边界区分原有文件系统与新增运行时功能。左侧实线框包含 merged view、可写 upper U 与只读 lower L；upper/lower 是扁平层片。右侧蓝色虚线框标为 Lancet additions，步骤 1 明列 File edges、Result edges 与 Object versions；这些记录和版本由层外运行时维护，不是新加的挂载层或 OverlayFS 内核原语。步骤 2 从用户报告文件在 checkpoint 之后的修改出发，选择联合依赖闭包 S，并检查命令、文件效果和上下文残留，步骤 3 将同一个 S 应用于选择性对象恢复与 agent context 同步。蓝色分支分别标注 Selective restore 和 Sync。

三条主行的顶边、底边及主要文字基线对齐：Merged view / Record、Upper U / Select S / Apply S、Lower L / Agent context。主框和层片统一为 0.70 英寸高，Select S、Apply S 与 Agent context 等宽；Record、Apply S 和 Agent context 右边缘一致。左右分组边界的顶边和底边也相同。依赖扩展、隔离验证和版本恢复的具体算法由后两张细节图展开。

三张正文图共用步骤编号：1 Record、2 Select S、3 Apply S。细节图中的 Build joint dependencies、Choose checkpoints、Validate 属于编号步骤内部的过程，不再单独从 1 编号。

## 重新生成

```bash
/tmp/agenttx-paper-check-20261027/bin/python paper/figures/design-workflow/generate.py
/tmp/agenttx-paper-check-20261027/bin/python paper/figures/design-workflow/generate_recovery_details.py
/tmp/agenttx-paper-check-20261027/bin/python paper/figures/design-workflow/generate_version_capture.py
make -C paper TECTONIC=/tmp/agenttx-paper-toolchain-20261027/tectonic
```

本轮修改前的 PPT、PDF、预览、生成器和正文保存在 review/design-figure-novelty-fix-20261110/before/。更早的样式历史可查该备份中的 README 和 paper/figures/backups/。视觉样式延续此前参考 YoloFS 的白底、细黑结构与语义配色。

## Ext4 / XFS 实现图

4.5 Implementation 使用 `Lancet-Implementation.pdf`，对应可编辑素材为 `Lancet-Implementation.pptx`，预览为 `implementation-visual.png` / `.svg`。图中仅保留四个框：共享用户态恢复控制器、原生 OverlayFS、Ext4 文件复制、XFS reflink，突出恢复逻辑与存储实现的解耦；不再展开文件版本和数据块。黑实线表示控制流程。生成器沿用现有 Times New Roman、线宽和箭头几何，并核对文字及画布边界。

```sh
/tmp/agenttx-paper-check-20261027/bin/python paper/figures/design-workflow/generate_implementation.py
```

文件提示协议不再依靠 verifier 祖先搜索候选 step，也不以任务测试通过作为集合选择条件。隔离审计使用恢复前冻结的依赖、保存版本及 active call/result 标识；任务是否解出另行统计。正文 proof 证明指定文件回到参考 checkpoint、依赖残留清除和保留状态一致性，并把捕获完整性明确列为前提。
