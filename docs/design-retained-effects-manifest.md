# 设计与落地：Context-Aligned Retained-Effects Manifest（REM）

**状态**：v1 已实现；当前增量加入 session control-plane manifest 与 bounded model view，并已完成远端 live A/B 复测（2026-08-30）
**适用范围**：AgentTX 因果回滚后，用全新黑盒 agent session 继续官方任务的路径。  
**核心目标**：新 agent 在第一次推理前就能准确知道哪些 effect 已完成并保留、哪些已撤销、当前该做什么；不再通过遍历工作区“考古”。

---

## 1. 结论先行

当前问题不是 `rollback_causal` 保留错了文件，而是 AgentTX 只恢复了文件效果，没有把与这些效果对齐的逻辑状态交给新 session。仅写一句“notes 已完成，不要打开”仍是无来源的自然语言声明，模型可能不信任并重新核验。

REM v1 把恢复交接改成一个可验证协议：

1. 恢复策略执行后，由 ledger 和当前 merged workspace **机器生成** manifest；
2. 运行时在 agent 启动前验证 retained artifact 的内容合同并计算 SHA-256；
3. 同一份 manifest 同时写入 prompt 和 AgentTX session control plane（不暴露在 agent 工作区）；
4. 三种恢复模式使用同一 prompt schema，只让 manifest 数据不同；
5. 外部 agent 的文件访问由 AgentTX trace 记录，读取 retained artifact 计为 reopen，写入/删除计为保护违约；
6. 保护违约在 commit 前拦截，因此受损 retained artifact 不会发布到 host workspace；
7. manifest 不一致时 fail closed，不允许 agent 自行“修现场”。

这不是完整的 conversation checkpoint。它是当前黑盒 harness 约束下的 **context projection**：把与保留 effect 有关、且能由账本和文件验证的最小逻辑状态投递给新 agent。

## 2. 为什么现有设计还不够

### 2.1 手写完成提示没有证据链

现有 `recovery_prompt` 按 mode 拼接散文：causal 说 notes 已保留，粗粒度策略说 notes 已丢失。声明没有绑定具体 ledger step、内容版本或 workspace generation，agent 无法区分“运行时事实”和“普通用户建议”。

### 2.2 prompt 描述的不是 agent 真正看到的最终现场

粗粒度策略会先在独立 replay session 中重建 notes，再启动官方任务 session。旧 prompt 仍告诉官方 agent “notes was lost; if absent, recreate”，而此时文件实际已经存在。这会主动制造 context/workspace mismatch。

正确口径应是：

- `origin=retained_by_causal_recovery`：回滚时保留下来；
- `origin=regenerated_after_recovery`：粗粒度回滚后由隔离 replay 重建；
- 两者在官方任务 session 开始时都属于 `complete-protected`，不需要再次读取或修改。

### 2.3 只写回执文件仍会诱发考古

如果 manifest 只存在工作区里，agent 必须先发现并读取它，仍会产生额外探索，而且清理命令可能误删它。精简摘要必须直接进入启动 prompt；完整 JSON 保存在 AgentTX session control plane，用于审计和一致性核验，不是主要发现机制。

### 2.4 “不要修改”必须有事务级后果

提示词无法强制黑盒 agent 遵守。AgentTX 已经在外部 agent 整轮执行外再包一层事务，因此可以在 commit 前检查该轮的 effect：

- 读取 retained 路径：记录诊断指标；
- 写入或删除 retained 路径：本轮失败且不 commit；
- session control-plane 副本缺失/不一致：本轮失败且不 commit。

这样保护不是依赖模型自觉，而是依赖事务发布门槛。

## 3. 与相关系统的对应

| 工作 | 已验证的机制 | AgentTX 采用的部分 |
|---|---|---|
| [Crab](https://arxiv.org/abs/2604.28138) | 环境与 agent 逻辑状态分歧时，live LLM 会尝试修复缺失状态并进一步扰动执行；agent process 较旧时用缓存 request/response fast-forward 对齐。 | 不让模型推断恢复现场；由运行时生成权威状态。未来有 turn bridge 时用 replay/fast-forward，v1 先做黑盒 context projection。 |
| [DeltaBox](https://arxiv.org/abs/2605.22781) | 文件系统和进程状态需要成对 checkpoint/restore，单边恢复会产生 semantic inconsistency。 | manifest 必须绑定 ledger generation 和 workspace digest；不把 fresh handoff 宣称为完整 resume。 |
| [AgentRewind](https://arxiv.org/abs/2608.14380) | checkpoint 同时包含 context 与 environment；恢复已完成前缀，另外保留针对失败后缀的 rewind memory。 | REM 交付 retained completion state，并把 fault cone 作为简短 invalidated state；不把原始长轨迹塞进 prompt。 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | messages/todos/state 通过 thread checkpoint 保持连续性。 | 后续 native adapter 应恢复 thread state；v1 的 pending/complete 状态采用固定字段，为升级留接口。 |

共同原则是：**“已完成”必须存在于与当前环境对齐的结构化状态中，而不是让新 agent 从目录内容反推。**

## 4. REM v1 协议

### 4.1 生成时机

顺序必须固定：

```text
注入故障 DAG
  -> 执行 recovery policy
  -> 必要时隔离 replay 丢失的独立工作
  -> 运行时验证最终 retained artifacts
  -> 生成并写入 REM
  -> 构造官方任务 prompt
  -> 启动新 agent session
  -> effect 审计与 manifest 后验校验
  -> 通过保护门槛后 commit
```

manifest 不能在 policy 前生成，也不能在粗粒度 replay 前生成，否则描述的不是官方 agent 真正接收的 workspace。

### 4.2 唯一事实来源

REM 只能使用以下机器事实：

- `Ledger.steps`：`step_id`、`status`、`parents`、`effects`；
- policy 返回的 `rollback_targets`；
- 注入 DAG 中的 `root_step`、`independent_steps`、`derived_step`；
- AgentTX merged view 中实际读取到的 retained artifact 内容；
- 内容合同校验结果和 SHA-256；
- 当前 `committed_frontier` 与 ledger step count。

禁止从 mode 名称直接推断文件是否存在；mode 只说明恢复策略，最终状态必须从 ledger + merged view 得出。

### 4.3 Schema v1

```json
{
  "schema": "agenttx.recovery_manifest/v1",
  "state_id": "sha256-of-canonical-state",
  "policy": "causal",
  "authoritative": true,
  "generation": {
    "ledger_steps": 9,
    "committed_frontier": -1,
    "rollback_targets": [0, 3, 4]
  },
  "retained": [
    {
      "path": "recovery_notes/design.md",
      "state": "complete-protected",
      "origin": "retained_by_causal_recovery",
      "producer_step": 1,
      "sha256": "...",
      "contract": "DESIGN-001..032",
      "contract_valid": true
    }
  ],
  "invalidated": [
    {
      "path": "astropy/modeling/separable.py",
      "reason": "fault_producer_rolled_back",
      "current_state": "restored"
    },
    {
      "path": "recovery_build/derived.txt",
      "reason": "fault_dependent_rolled_back",
      "current_state": "absent",
      "must_stay_absent": true
    }
  ],
  "pending": [
    "implement the official task",
    "run the official verifier"
  ]
}
```

`state_id` 覆盖除自身外的 canonical JSON；任何 retained hash、step status 或 rollback target 变化都会改变 state ID。

### 4.4 authoritative 判定

只有同时满足以下条件，`authoritative=true`：

1. 每个声明为 complete 的 artifact 在 merged view 中存在；
2. 内容合同通过；
3. SHA-256 已计算；
4. origin 与原 independent step 的 ledger status 一致；
5. derived invalid artifact 当前不存在；
6. manifest 中没有同时出现在 retained 和 invalidated 的路径。

live agent 启动前若 `authoritative=false`，运行必须终止并报告 state mismatch。不能把不一致现场交给模型自行修复。Oracle/诊断模式可输出非权威 manifest，但不得把它当作 live recovery 成功。

### 4.5 Prompt 投递

所有 mode 使用同一固定结构：

```text
## AgentTX recovery state (machine-generated; authoritative)
State ID: <id>

COMPLETE-PROTECTED — already verified outside this LLM session:
- recovery_notes/design.md
  origin=retained_by_causal_recovery
  sha256=<digest>, contract=DESIGN-001..032 passed

INVALIDATED BY RECOVERY:
- recovery_build/derived.txt: absent; must stay absent
- astropy/modeling/separable.py: faulty overlay reverted

PENDING:
- implement only the official task
- run the official verifier

Do not read, validate, rewrite, or recreate COMPLETE-PROTECTED paths.
Their hashes and contracts were verified by AgentTX after recovery.
If a tool reports a mismatch, do not repair these paths; report
AGENTTX_STATE_MISMATCH and stop touching them.
```

关键点：

- 不再按 mode 写三套不同的 note 指令；
- 告诉 agent **当前最终状态**，而不是历史上曾经丢失过什么；
- origin 只用于解释来源，不改变 complete-protected 行为；
- 完整 hash 写 JSON，prompt 可显示缩短 digest，控制输入长度；
- 明确提供 mismatch 出口，避免 Crab 所观察到的“发现差异后继续乱修”。

### 4.6 控制面回执

写入 AgentTX session directory 的 `recovery_manifest.json`，不写入 external agent 的工作区：

- 供审计、复现和低成本核验；
- external agent 无法通过工作区清理命令删除或改写它；
- agent 不需要先读该文件，因为摘要已进入 prompt；
- session 后由 host 直接比较控制面副本，不把该检查转换成 agent 的一次 `read_file`；
- 如果控制面副本缺失或不一致，保持 fail closed。

## 5. 保护与观测

### 5.1 Retained write guard

外部 agent 在 AgentTX 中表现为一个带完整 R/W/D effects 的 transaction step。官方 session 前记录 `recovery_first_step`，结束后只审计该区间：

```text
retained path + R effect -> retained_paths_reopened / retained_read_effects
retained path + W or D   -> retained_paths_modified -> reject commit
control-plane copy differs -> manifest_intact=false -> reject commit
```

路径比较采用 workspace-relative canonical path，并按祖先/子孙关系判断，防止通过目录级操作绕过。

### 5.2 成功判据

REM 开启后的 live row 必须满足：

- `recovery_manifest_authoritative = true`；
- `recovery_manifest_intact = true`；
- `retained_paths_modified = []`；
- `independent_unchanged = true`；
- fault-dependent derived artifact 已删除；
- 官方 verifier 通过；
- 外部 harness 正常完成。

`retained_paths_reopened` 第一阶段作为诊断指标，不直接判失败：某些通用工具可能隐式读取路径。实验目标是该值趋近 0，并结合 trace 判断是真正内容核验还是目录元数据访问。

### 5.3 指标

新增 raw fields：

- `recovery_manifest_state_id`
- `recovery_manifest_authoritative`
- `recovery_manifest_intact`
- `retained_paths_reopened`
- `retained_read_effects`
- `retained_paths_modified`

token 分析必须分开：

- `doc_replay_tokens`：粗粒度策略重新生成独立工作的成本；
- `official session tokens`：新 agent 完成主任务的成本；
- `alignment overhead`：manifest prompt 本身和 agent 对 retained 内容的额外审计；
- `net recovery cost = replay + official session`。

不能仅用 causal 与 whole-abort 的官方 session token 差，直接宣称“因果恢复节约了多少再生成 token”。

## 6. 实施映射

| 文件 | v1 改动 |
|---|---|
| `experiments/workloads/recovery_inject.py` | manifest builder、canonical state ID、固定 schema renderer、retained access 审计 |
| `experiments/scripts/bench_official_tasks.py` | policy/replay 后生成 REM；写入 session control-plane 回执；重建 prompt；commit 前保护检查；输出新指标 |
| `experiments/workloads/swe_bench_suite.py` | `task_prompt(..., recovery_manifest=...)`，移除 REM 路径上的 mode-specific note 事实 |
| `experiments/workloads/terminal_bench_suite.py` | 同上 |
| `src/agenttx/policy.py` | `.agenttx/**` 作为运行时元数据，不 materialize 到 host |
| `tests/` | schema、一致性、prompt、ignore policy、effect 审计、旧结果字段兼容 |

v1 不改 `rollback_causal`、overlay 或 WAL 语义。

## 7. 评估设计

### 7.1 对照组

在相同 task、model、harness、repeat 下比较：

1. `causal_fs_only_fresh`：旧手写 prompt；
2. `causal_rem_fresh`：机器生成 REM；
3. `causal_rem_guard_fresh`：REM + commit 前保护（v1 默认）；
4. `temporal_checkpoint_rem_fresh`；
5. `whole_branch_abort_rem_fresh`；
6. `aligned_resume`：未来能恢复 chat/turn log 时的上界。

机制正确性先用 deterministic/fake harness；行为结论必须使用 live LLM，不能用 trace replay 代替。

### 7.2 主要假设

- H1：REM 使 causal 的 retained reopen 显著下降；
- H2：REM 不降低官方任务成功率；
- H3：同一 schema 消除 mode-specific prompt 对 session token 的混杂；
- H4：write guard 将 retained corruption publication rate 降为 0；
- H5：native aligned resume 仍优于 fresh-session REM，说明 REM 是黑盒约束下的近似而非完整恢复。

### 7.3 推进顺序

1. 单元测试：schema、hash、status 映射、fail closed、effect audit；
2. fake external harness：确认 manifest 同时出现在 prompt 和 workspace；
3. 单题三 mode smoke；
4. 单题多 repeat A/B；
5. 10 题 live；
6. 全量任务。

在第 3 步通过前不应直接续跑已有 10 题批次，否则会把旧 prompt 与新 REM 数据混在同一个结果目录。

## 8. 后续版本

### v2：TurnRecord bridge

在每个 LLM/tool boundary 持久化 request/response hash、tool event、effect links、todo/fact delta。恢复时回放已完成前缀而不重新调用模型，接近 Crab fast-forward。

### v3：Context projection

对非连续 retained suffix，不拼接任意旧 chat；从 retained effect 生成 typed facts/todos/certificates，只恢复由 active effects 支撑的上下文事实。

### v4：Native aligned resume

若 harness 支持 thread checkpoint，则原子恢复 `(messages/todos, workspace, ledger generation)`。REM 继续作为一致性证书和跨 harness handoff 格式。

## 9. 非目标与限制

- v1 不恢复 provider 隐藏推理或完整对话；
- 不伪造 chain-of-thought，只保存可审计的状态事实；
- AgentTX 当前主要恢复 workspace effects，不恢复任意网络服务和外部进程状态；
- read audit 受 trace backend 精度影响，因此写保护是硬门槛，读取是诊断门槛；
- REM 解决“新 agent 不知道保留了什么”，不能保证模型一定选择最优主任务策略。

## 10. 本轮实施验证

截至 2026-08-27：

- REM、policy、SWE prompt、external harness 和汇总兼容性相关 **37 个定向测试通过**；
- 真实 OverlayFS + strace 检查确认：恢复后内容可从 merged view 验证，manifest 写入后可逐字回读；
- commit 检查确认：业务文件正常发布，`.agenttx` 目录与回执不会 materialize 到 host；
- `pytest-dev__pytest-8906 / causal / oracle` 完整路径 smoke 成功；
- smoke row 中 `authoritative=True`、`manifest_intact=True`、`independent_unchanged=True`、`retained_paths_modified=[]`、官方测试通过；
- 旧 `tests/test_token_recovery.py` 仍有 3 个与本改动无关的 dependency-trace 失败：旧 workload 的 test step 没有识别到 fault producer parent。它不应混入 REM 回归结论，需单独修复。

单题 live 复测已经完成；后续结果均写入独立结果目录，不与旧 prompt schema 的 `codex_10` 跑次混合。三 mode 和 10 题批次仍需等 adapter 级硬预算补齐后再启动。

### 7. 当前成本迭代（2026-08-30）

- native tool-calling agent 使用 `ConversationLog.model_messages()`：只压缩旧的 active effect turns，完整 turns、ledger 和 rewind 语义保持无损；`AGENTTX_CONTEXT_RECENT_TURNS` 与 `AGENTTX_CONTEXT_RESULT_CHARS` 可调节模型视图预算。
- external black-box agent 无 turn-level hook，因此不伪装成 native conversation；恢复证书直接进入 prompt，完整副本放在 AgentTX session directory，避免任务清理命令删除工作区内的控制文件。
- 恢复 prompt 约束单代理、禁止 delegation、禁止重复成功命令，并要求 verifier 失败后只做一次聚焦修复；SWE 任务再限制到失效源文件和命名 verifier。
- 远端 live 复测：pylint 两种模式均通过；在证书前置后的 r2 A/B 中，causal 为 `498597` prompt / `521368` total tokens，temporal 为 `848209` prompt / `874532` total tokens，causal 少 `353164` total tokens（约 40.4%），达到“causal 成本低于 checkpoint”的单题验证目标。
- Codex 代表性批次（3 个 SWE + 3 个 Terminal-Bench，6 个 paired cases）两种模式均成功。只计 official session 时，causal 均值 `155068`、temporal `107351`；把 temporal 的独立文档 replay 加回后，temporal 均值为 `144569`。causal 在 all-in 口径下胜出 `4/6`，median 为 `113558` 对 `141456`，但均值仍高约 `7.3%`，主要受 pylint 长任务单点拖累，因此聚合成本目标尚未足够鲁棒。
- scheduler 的 control-plane 修复复测中，causal 成功且 manifest 完整：`6034520` prompt / `6302399` total tokens，`41` 次 tool call，耗时 `1363.3s`；temporal 因外部 sandbox 在超时后失效而失败：`10759121` prompt / `11070852` total tokens，`65` 次 tool call，返回码 `124`。因此这次运行只能说明 causal 在该非对称结果中更低，不能作为“两个成功样本下 causal 更便宜”的严格结论。
- 当前剩余工作是为 external adapter 增加硬 turn/token/time budget 和超时后的可控终止/清理，并针对长任务减少 causal 的无效源码/证书探查；之后需要扩大重复次数和真实 scheduler 成功样本，再以 all-in 成功样本成本作为门槛验证目标。

### 7.1 远端 Codex context-bounded 复测（2026-08-31）

- 在远端 Codex `gpt-5.6-luna` 上重新跑了相同的 3 个 SWE-Bench Lite + 3 个 Terminal-Bench representative cases；6 个 case 的 causal/temporal 共 `12/12` 成功，测试、manifest 完整性和 retained 不变性均通过。
- r2 的 official session tokens 总计为 causal `731161`，temporal `855907`；temporal 缺失文档 replay 另计 `199487`，所以公平的 all-in temporal 成本为 `1055394`。均值分别为 `121860` 与 `175899`，causal 低 `54039`，约 `30.7%`。
- 按 all-in 成本，causal 在 `5/6` 个 case 胜出；唯一败例是 Flask（causal `256531`，temporal all-in `248575`）。Pylint causal `184215`，temporal all-in `386159`，说明此前的长任务异常点已在本轮下降，但单 repeat 仍不足以给出低方差结论。
- 为减少无效上下文，SWE prompt 增加了任务级硬约束：源码读取只允许单符号/单路径的 `rg` 和有界 `sed -n`，禁止 `cat`、`find`、递归目录列举、无范围 `git diff/status` 和全量测试。该约束不改变 manifest 的状态语义；后续仍应以至少 3 repeats 的 paired all-in 成本和成功率作为发布门槛。

### 7.2 远端 Codex no-fault 基线（2026-08-31）

- runner 新增 `--no-fault` 模式：从干净官方 workspace 启动同一 Codex，不执行 recovery DAG、rollback、manifest 或文档 replay；SWE/TB verifier 在该模式下只检查官方任务和 derived artifact。
- 首次 no-fault 跑次发现 runner 在提交前仍误判“缺少 manifest”为保护错误，6 个会话虽完成 verifier 却未提交；该目录 `no_fault_codex_r1` 不纳入统计。修复后 hello-world smoke 通过，完整 r2 为 `6/6` 成功。
- 复核发现上述 `no_fault_codex_r2` 仍不是严格的 clean baseline：它沿用了 recovery prompt 的故障叙述、恢复协议和 recovery 产物规则，虽然没有真的注入故障，但控制组获得了不同于普通任务的额外上下文；因此该目录保留作诊断数据，不用于严格成本比较。
- 修正后的 `recovery_prompt(mode="no_fault")` 只保留普通任务上下文、官方 instruction 和通用直接执行规则；不再出现 faulty producer、recovery protocol、recovery notes、derived/replay 或 rollback 语义。SWE/TB 的 no-fault task prompt 同时改为只限制任务所需文件，runner 仍跳过 fault DAG、rollback、manifest 和 document replay。针对性测试为 `27 passed`。
- 修正后的结果目录为 `no_fault_codex_clean_r1`，6/6 成功，逐题 tokens 为 pytest `117450`、Flask `237552`、Pylint `507361`、hello-world `26987`、csv-to-parquet `71019`、log-summary `69928`；总计 `1030297`，均值 `171716` tokens。
- 与同一批但独立 Codex session 的 causal r2（总计 `731161`，均值 `121860`）和 temporal all-in（official `855907` + replay `199487` = `1055394`，均值 `175899`）相比，本次单 repeat 中 causal 低 `29.0%`，temporal all-in 高 `2.4%`。这只能说明 causal 的失败定位/恢复上下文在该批次改变了探索轨迹，不能说明“有故障恢复天然比无故障执行更便宜”；no-fault、causal、temporal 的 session 不是同一条随机会话，且 causal 获得了额外的失败定位信息。
- 后续正式门槛应使用同一模型、同一任务集合、至少 3--5 个 paired repeats，报告每题 median/p95 和成功率；成本拆成 `official session tokens`、`doc_replay_tokens`、恢复控制面/主任务 prompt 长度，并将 clean baseline 与“同等 task-context 的 fault/recovery 对照”分开，避免把 agent 探索方差误归因于恢复机制。

### 7.3 远端 Codex 原始 direct no-fault baseline（2026-08-31）

- 按实验定义进一步收紧 no-fault：不调用 `recovery_prompt`，只发送 SWE-Bench 原始 `problem_statement` 或 Terminal-Bench 原始 `instruction`（仅做 `/app` 到 workspace 相对路径的必要转换）；不写入 `agenttx_task_spec/TASK.md`，不创建 recovery DAG、manifest、rollback 或 document replay。
- 直跑仍保留 host-side 的 workspace materialize、专用 conda/依赖准备、token 统计和最终 verifier；这些是 benchmark harness 的环境与计量操作，不是 agent 获得的任务提示或恢复信息。Codex 的专用 Python 环境固定为 `/home/pengpeng/miniconda3/envs/agenttx`，user-site 兜底重定向到 workspace，避免任务安装依赖时污染宿主 `~/.local`。
- 过渡跑次 `no_fault_codex_direct_r1` 中 Flask 的官方测试失败，csv-to-parquet 因宿主 user-site 越界写被提交保护拦截；两者分别暴露了原始直跑的真实 agent 失败和 harness 环境隔离问题，不作为最终 baseline。固定专用 conda 后，`no_fault_codex_direct_conda_r1` 的 6 个 case 全部成功，且 `recovery_steps=0`、无 recovery 产物。
- 最终逐题 official session tokens 为 pytest `767141`、Flask `359203`、Pylint `340233`、hello-world `26555`、csv-to-parquet `54003`、log-summary `55511`；总计 `1602646`，均值 `267108` tokens，成功率 `6/6`。
- 该结果高于带恢复定位上下文的 causal r2（`731161`，均值 `121860`）和 temporal all-in（`1055394`，均值 `175899`），恰好说明原始 direct agent 的自主探索与环境准备成本很高；这不是矛盾，而是 no-fault 与 recovery session 的 prompt 信息量不同。以后报告 no-fault 时应使用本节 direct 结果，不再把带通用限制的 `no_fault_codex_clean_r1` 当作“直接 benchmark”结果。

### 7.4 控制矩阵 smoke run（2026-08-31）

为隔离“自然执行成本、故障本身、恢复提示和回滚策略”，runner 新增四个可组合的控制臂：

- `no_fault`：干净 workspace + 原始 benchmark instruction；不注入、不生成 recovery DAG、manifest 或 replay；
- `crash_direct`：注入一次 synthetic crash，但仍只发送原始 benchmark instruction，不回滚；
- `clean_recovery`：不注入故障、不回滚，但使用 recovery-shaped prompt，单独测提示/上下文外壳成本；
- `crash_no_rollback`：注入一次 synthetic crash，保留故障现场，并使用 recovery-shaped prompt，但不执行 rollback。

正式策略 `causal`、`temporal_checkpoint` 和 `whole_branch_abort` 保持原语义。另一个重要的 harness 修复是将 synthetic trajectory 和外部 Codex 子进程的 `TMPDIR/TMP/TEMP` 统一指向 scratch workspace 的 `.tmp`；否则 pytest 的临时文件会落到宿主 `/tmp`，使控制组在 verifier 前被 commit guard 错误拦截。

同一远端 Codex、同一 `pytest-dev__pytest-8906` case、每个模式一次的最终结果如下；`crash_direct` 与 `crash_no_rollback` 使用修复后的 `control_matrix_pytest_r1_fix2` 行，其余模式使用 `control_matrix_pytest_r1` 的有效行：

| mode | fault injected | rollback | success | total tokens | derived removed |
|---|---:|---|---:|---:|---:|
| `no_fault` | no | none | yes | 595,214 | yes |
| `crash_direct` | yes | none | yes | 819,585 | no |
| `clean_recovery` | no | none | yes | 235,800 | yes |
| `crash_no_rollback` | yes | none | yes | 141,507 | no |
| `causal` | yes | causal cone | yes | 65,674 | yes |
| `temporal_checkpoint` | yes | crash timestamp | yes | 114,998 | yes |
| `whole_branch_abort` | yes | whole overlay | yes | 97,152 | yes |

该 smoke run 证明七个 mode 均可启动、完成 verifier、生成 raw row 并正确区分 fault/rollback 状态；它不是成本结论，正式成本比较仍需同一 task 集合上的 paired repeats。特别是 no-rollback 组的 `derived removed=no` 是设计预期，不应按严格 recovery manifest 的“失效产物必须消失”规则判败。
