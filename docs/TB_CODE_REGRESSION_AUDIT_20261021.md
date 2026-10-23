# TB 代码劣化排查与修复

核对时间：2026-10-21，北京时间11:29。本机 r760-ssd；代码在本机，所有验证证据在数据盘。

已确认当前代码存在恢复交接及验收接口缺陷，有实际模型行为证据；尚未证明它们解释了全部 token 劣化，也未取得修复后的模型效果结果。修复登记为 `agenttx.tb-image-native-recovery/v7`，旧 v6 数据不改写、不与 v7 混算。

## 已证实的问题

1. **内部验收标签误导模型。** 交接要求先运行 named verifier，却没有给出官方验收命令；Codex 的旧控制器 recovery notice 又放在当前任务交接之后，含 `verifier:auto-localization-crash-verifier`。在本次审计的188条 v6 完整观测中，9个 Codex/Causal 结果实际查找或尝试执行该内部标签。`build-stp` 使用 `command -v auto-localization-crash-verifier`，`build-tcc-qemu` 直接执行 `verifier:auto-localization-crash-verifier`。这并不是官方任务验收。
2. **隐藏文件路径损坏。** `.lstrip("./")` 会按字符去掉开头所有点和斜线，使 `.agenttx_faults/build-stp.txt` 变成 `agenttx_faults/build-stp.txt`，同时破坏状态查询键。审计发现16处已披露路径与真实故障路径不一致，涉及 Causal/Ideal。不能将全部成本上涨都归因于此。
3. **验收诊断没有返回调用方。** 准备脚本将真正测试输出写到 `/results/verifier.stdout` 和 `.stderr`；接口只收集进程输出，常得到空的 `verifier.stdout.log`、`.stderr.log`，模型调用该脚本同样看不到完整诊断。

## 修复内容

- 对所有策略明确提供同一个命令 `bash /opt/agenttx-verifier/verify.sh`；说明内部定位标签不是官方 shell 命令，清掉合成 marker 不代表原任务完成。
- Codex 模型视图保留策略对应工具历史，去掉被当前权威交接取代的控制器 recovery notice，将当前任务和交接放在最后。原始审计历史不改写；full 对照保留全部有效工具历史，compact仍保留最近6次有效调用。
- 新入口只包装原验收脚本，返回其退出码，并将重定向的测试输出显示给调用方。每次调用保留独立档案，归档并清除旧固定输出，避免 bootstrap 早退后读到前一次通过结果。入口、测试和原 bootstrap 均只读挂载，注册 SHA256。
- 修正共享 manifest、历史运行入口的状态查询路径及 retained-artifact 路径规范化，保留隐藏文件前导点。
- 正式 grader 使用相同入口、原有受信验收账号；模型仍使用任务原账号。非 root 任务的 bootstrap 可能需要原有 grader 权限，新入口不授予模型提权能力，提示要求报告启动错误。

故障量、笔记内容、因果闭包、Oracle目标、模型、会话预算、八路并行及官方测试断言不变。没有预写任务解答，也没有读取参考解改善模型起点。

## 成本与历史的核验

- 审计188条 v6 完整观测，147条成功。按各 harness 的记录重新计算 API 用量，**0处与原 api_usage 不一致**；成本包括 main 与 replay，不把未知用量补零。SWE-agent 行内的 `total_tokens` 与日志完整用量口径不同，跨组比较继续使用完整 `api_usage.total_tokens`。
- 对交接快照中94条 Codex/v6 完整结果，另从原始CLI stdout独立提取 `turn.completed` 的 `input_tokens + output_tokens`，再加 replay，与模型及聚合结果均一致；没有额外计入已包含在输入/输出中的 cached/reasoning 子集，也未发现其他携带 usage 的顶层事件造成重复相加。这是CLI记录核验，不是服务商账单审计。
- 检查62个 Causal/Ideal 恢复前缀，没有一个超过6次有效调用。compact当前压缩的是恢复前工具历史，不是后续整个求解过程。三份一行笔记在历史批次也存在，不能解释为“这一版把笔记缩短了”。
- `build-tcc-qemu` 的 SWE-agent/Causal 共5,625,800 tokens、116次工具调用；whole为1,904,917。Causal主求解较长，是当前总量劣化的真实来源之一。此前九个成功配对中，此题约占 Causal 总消耗的76.74%；保留该高值。
- `build-stp` 的 Codex/Causal 共4,159,351 tokens，checkpoint为2,084,039、whole为1,798,009。Causal此题没有 replay，劣化不能归因于重复重放成本。
- 历史 Codex/TB 与当前 v6 只有3个四方成功同题交集：`catch-me-if-you-can`、`cobol-modernization`、`code-from-image`。在这3题中，对不回滚节省由历史27.40%变为当前−18.19%，对whole由33.09%变为−39.67%，而对checkpoint由33.63%变为87.45%。说明并非只有总体样本构成变化，但也不是所有对照都同向恶化。
- 历史是 host agent + container verifier，当前是同原任务镜像内求解与验收，执行边界不同；上述三题不能据此证明单一代码改动导致全部差异。当前已检查的文件及恢复历史未发现因果恢复误删独立笔记；这不是对所有未跑任务的保证。

## 验证

- 最终聚焦 host 回归：85 passed，9 deselected。未选择的9项属于三个旧镜像构建测试类别；完整运行原为91 passed、3 failed。旧 r13 冻结源码运行这些类别也为6 passed、3 failed，失败项完全相同，属于既有构建模块/国内镜像测试预期问题，未计作本次通过。
- 最终原任务容器回归：52 passed，包含恢复状态一致性、两套原生运行接口和调度测试。首轮环境继承compact导致一条要求full文案的断言失败；将验证命令环境设为full后通过，没有修改断言或正式实验的策略配置。
- Codex与SWE-agent真实 adapter 导入、事务与完成约束预检均通过，0次模型调用。
- 在未解答的原 `hello-world` 容器及私有事务视图中，原 bootstrap 与新入口均返回1；新入口输出3,525字节stdout、2,220字节stderr，能够看到官方失败诊断。未修改官方测试，未写答案，0次模型调用。
- 包装器回归覆盖连续调用、旧通过输出归档、bootstrap早退不复用旧输出、诊断及返回码保留。`git diff --check` 通过。

## 上线与续跑状态

北京时间11:28:35已启动持久交接服务 `agenttx-tb-environment-continuation-20261021-r14.service`。登记确认协议仅从v6升为v7；模型、上下文策略和预算不变。旧dispatcher已暂停派新任务，8个独立worker继续自然执行，完成后继承全部原终态、只续未启动任务。此时仍在排空，不能称v7已产生新结果或已恢复8路新任务。超出登记的worker包络时，交接程序会恢复旧dispatcher并报错。

此次审计快照和上线前剩余任务数量不同：11:25旧r13为451条完整、28条异常、8运行、2405待跑；上线时以交接目录保存的 original-progress 为准。v7新批次的同题成功样本与收益需后续单独核验。

## 证据位置

- `/mnt/data/agenttx-runtime/validation/tb-regression-audit-20261021.json`：逐案例问题、用量重算、历史交集、原结果路径及历史文件SHA256。
- `/mnt/data/agenttx-runtime/validation/tb-regression-repair-20261021-r1/`：源码manifest、两套预检、最终host/container测试日志、真实官方诊断及命令。
- `/mnt/data/agenttx-runtime/validation/tb-regression-audit-20261021-old-baseline-tests.log`：旧冻结源码同三项测试失败证据。
- `/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261021-r14/`：交接登记、原progress、排空及最终切换记录。
