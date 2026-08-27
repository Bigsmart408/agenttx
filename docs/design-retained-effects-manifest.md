# 设计与落地：Context-Aligned Retained-Effects Manifest（REM）

**状态**：v1 已实现并通过 deterministic/oracle smoke；待 live LLM A/B（2026-08-27）  
**适用范围**：AgentTX 因果回滚后，用全新黑盒 agent session 继续官方任务的路径。  
**核心目标**：新 agent 在第一次推理前就能准确知道哪些 effect 已完成并保留、哪些已撤销、当前该做什么；不再通过遍历工作区“考古”。

---

## 1. 结论先行

当前问题不是 `rollback_causal` 保留错了文件，而是 AgentTX 只恢复了文件效果，没有把与这些效果对齐的逻辑状态交给新 session。仅写一句“notes 已完成，不要打开”仍是无来源的自然语言声明，模型可能不信任并重新核验。

REM v1 把恢复交接改成一个可验证协议：

1. 恢复策略执行后，由 ledger 和当前 merged workspace **机器生成** manifest；
2. 运行时在 agent 启动前验证 retained artifact 的内容合同并计算 SHA-256；
3. 同一份 manifest 同时写入 prompt 和 `.agenttx/recovery_manifest.json`；
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

如果 manifest 只存在工作区里，agent 必须先发现并读取它，仍会产生额外探索。精简摘要必须直接进入启动 prompt；JSON 文件是审计回执和低成本 fallback，不是主要发现机制。

### 2.4 “不要修改”必须有事务级后果

提示词无法强制黑盒 agent 遵守。AgentTX 已经在外部 agent 整轮执行外再包一层事务，因此可以在 commit 前检查该轮的 effect：

- 读取 retained 路径：记录诊断指标；
- 写入或删除 retained 路径：本轮失败且不 commit；
- manifest 被修改/删除：本轮失败且不 commit。

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

### 4.6 工作区回执

写入 `.agenttx/recovery_manifest.json`：

- 供审计、复现和低成本核验；
- 必须加入 commit ignore，不发布到用户仓库；
- agent 不需要先读该文件，因为摘要已进入 prompt；
- session 后重新读取并比较，若被改写或删除则拒绝 commit。

## 5. 保护与观测

### 5.1 Retained write guard

外部 agent 在 AgentTX 中表现为一个带完整 R/W/D effects 的 transaction step。官方 session 前记录 `recovery_first_step`，结束后只审计该区间：

```text
retained path + R effect -> retained_paths_reopened / retained_read_effects
retained path + W or D   -> retained_paths_modified -> reject commit
manifest path + W or D   -> manifest_intact=false -> reject commit
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
| `experiments/scripts/bench_official_tasks.py` | policy/replay 后生成 REM；写回执；重建 prompt；commit 前保护检查；输出新指标 |
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

下一步只运行一个全新结果目录下的单题 live causal A/B；确认 agent 实际 `retained_paths_reopened` 降低后，再启动三 mode 和 10 题批次。使用旧 prompt schema 的 `codex_10` 目录已于 2026-08-27 清理，后续结果不得与旧跑次混合。
