# 10.249.188.30 上的 AgentTX 改动审查

核对日期：2026-10-16。只读查看 `bfq@10.249.188.30:/home/bfq/agenttx`，将选定源码、配置和文档快照复制到本地审查；没有修改远程工作区，也没有用其源码整体替换本地。

2026-10-17 实施补充：已逐项合入首批修复并验证，见 [推进记录](EXPERIMENT_PROGRESS_20261017.md)。进一步发现远程 `rollback_exact` 仍通过上下文 rewind 扩张捕获到的消息依赖；本地已补齐 oracle 的精确消息失效路径，并验证误报消息边不会删除独立消息。因此下面的候选评价不能理解为远程实现原样就已完全隔离所有被测图。

**结论：有值得逐项移植的改动，但远程版本同时缺少本地已经具备的功能，应按代码块选取。** 两边 HEAD 同为 `321f32ecee20a9d48c8a1b759f2b7817c97fb97c`，均有工作区修改。同提交不等于同实验协议，更不说明哪一侧整体更新。

## 1. 可复核的快照

- [源码快照](/home/bfq/agenttx-recovery-20261016/remote-review-20261016/source)：281 个选定文件，逐个验证大小和 SHA-256；不是整个远程项目的备份。
- [差异索引](/home/bfq/agenttx-recovery-20261016/remote-review-20261016/comparison.json)：选定范围内 32 个文件内容不同，6 个远程独有。
- [逐文件 diff](/home/bfq/agenttx-recovery-20261016/remote-review-20261016/diffs)：方向为本地→远程，减号通常表示远程缺少本地内容。
- [验证结果](/home/bfq/agenttx-recovery-20261016/remote-review-20261016/validation.json)：185 个 Python 文件语法通过，并对五类候选逻辑做了有界验证。

快照没有保存登录密码或项目凭据。下面的路径均以该快照为审查对象，避免远程后续修改改变依据。

## 2. 优先参考的实现

| 优先级 | 改动 | 参考位置 | 价值与移植约束 |
| --- | --- | --- | --- |
| 高 | oracle 独立真值恢复 | `src/agenttx/runtime.py:594`；`experiments/workloads/recovery_inject.py:255`；`experiments/scripts/bench_official_tasks.py:360` | 用注册 invalid set 调 `rollback_exact`，避免 oracle 与被评估的依赖捕获图共用错误闭包。应移植精确目标入口及真值传递；causal 仍独立定位 |
| 高 | 固定 SWE 官方镜像与真实 Python | `experiments/workloads/swe_bench_suite.py:257`；`experiments/scripts/verify_swe_workspace.py` | 检查 image ID；`--pull=never --network=none` 探测镜像内解释器，验证命令携带 `--image-id/--expected-python`。验收基础设施错误返回 70，任务失败返回 1，便于分开统计 |
| 高 | 恢复阶段计时和输入指纹 | `experiments/scripts/bench_official_tasks.py:1043`、`:1532`、`:1557` | policy/doc replay/model/grading 分段时间、恢复前后输入及覆盖审计，帮助配对与成本归因。保留本地 localization/full-wall 计时，不以远程列集合覆盖本地 schema |
| 高 | 私有 XDG 配置目录 | `src/agenttx/agents/external.py:745`；`bench_official_tasks.py:825` | run 内 config home 和 Astropy 目录减少共享配置/写权限错误。保留本地网络/代理控制，并检查目录不进入任务有效产物和输入指纹 |
| 中 | 清理未挂载的 temproot | `src/agenttx/semisolate.py:1575`；`tests/test_semisolate.py:10`、`:33` | 清理失效 mount view，拒绝根 symlink 和已挂载子树；保留实际 upper。需做真实 try/overlay 与并发回归，避免清理仍被使用的视图 |
| 中 | concise prompt 与 matched handoff | `experiments/workloads/recovery_inject.py:529`、`:746`；`tests/test_compact_recovery_prompt.py` | 可作提示词长度/策略标签的受控消融。`AGENTTX_RECOVERY_PROMPT=concise-v1` 和 `AGENTTX_MATCHED_SELECTIVE_HANDOFF=1` 改变输入协议，必须新批次 |
| 文档 | 幂等性证明草稿 | `docs/recovery-idempotence-proof.md` | 对闭包幂等、成功恢复后的状态幂等作了较准确限定；适合作附录草稿，不是中断恢复安全证明 |
| 文档 | 论文来源一致性审计与绘图脚本 | `docs/paper-polish-audit-20261016.md`；`motivation/plot_causal_capture_cost.py` | 来源审计能指导补证；绘图仅能改善表达，不能替代测量。论文故事中英文稿可供写作参考 |

### 独立 oracle 的适用范围

远程 `rollback_exact` 先去重排序，拒绝已提交、越界或已回滚目标，然后复用实际重建路径。新增的 `test_oracle_truth_restores_workspace_despite_incorrect_captured_edges`（`tests/test_recovery_state_consistency.py:504`）故意漏边/误加边，验证 oracle 不查询被测 captured closure，这是很有价值的测试。

但远程注入器注册 `[faulty, derived, failing]` 三步骤，只适用于该注入轨迹。完整 400-case 和多根场景必须由各自生成器提供独立、完整的真值集合，不能把这三个步骤硬套到所有案例。精确目标入口也不能泄漏到 causal 路径。

### 镜像固定要与本地功能合并

远程镜像校验值得采用，但其 SWE suite 同时删掉本地 `apply_test_patch` 参数、测试 patch 应用和 scratch cleanup。直接复制整个文件会破坏本地 native-capture 调用。应只移植运行时身份/解释器校验和 verifier 参数，并保留、回归现有参数兼容性。

### 指纹、计时和提示词要避免引入新偏差

`AGENTTX_CAPTURE_RECOVERY_INPUT=1` 是可选诊断，会额外扫描/调用 verifier；指纹须固定排除项、规范化规则和测量边界，不能把扫描开销只加在某个策略上。保留定位、恢复、文档重放和验收分段，主结果另计完整端到端时间及 main+replay tokens。

远程会把 causal/ideal 强制 compact，而 coarse 默认 full。它可以解释某个系统配置的总效果，但不能单独证明恢复边界带来全部节省。matched handoff 也只有在根与恢复内容真实一致时才应相同；不能为了让提示相同而把 oracle 的正确根或正确恢复状态交给 causal。

### 幂等性草稿可以支持什么

固定有限图上的闭包幂等，与已成功完成恢复后的重复显式根请求保持状态，都有明确前提。当前 API 对已回滚根抛 ValueError，**并不是第二次调用成功返回 no-op**。草稿未证明 workspace/context 更新中途崩溃时的原子一致性；仍需五阶段中断实验和实现机制支持。

### 论文审计揭示的实际问题

远程审计指出：正文写 144 次/三重复、64 calls 下 p95=238.9ms；历史文档写 p95=272.7ms；现存 `causal_retention.csv` 却是 48 聚合行×两重复=96 次，causal/size=64 的 p95=500.9884ms。应选择可追溯的冻结原始数据统一重算，或补足三重复。四行聚合数据的 capture-cost 绘图脚本不能填补缺失原始记录。

## 3. 不应带回的功能倒退

| 文件 | 远程相对本地的变化 | 处理 |
| --- | --- | --- |
| `experiments/scripts/run_parallel_official_tasks.py` | 固定 Codex，删除 `--harness`、suite 路由、SWE-agent bwrap/计数上限记录 | 保留本地，不能整体覆盖 |
| `bench_official_tasks.py` / harness 相关模块 | 恢复由 workload scale 推导的 `max_turns=turns`，preflight 使用 `args.max_turns or 30` | 保留本地“省略表示无 count cap”的规则；显式上限须两 harness 匹配 |
| `bench_official_tasks.py` / `src/agenttx/agents/swe_agent.py` / `sweagent_native_workload.py` | 删除已播种的 SWE 原生 precrash 历史及 projector，部分 model-call 元数据置零 | 保留本地原生历史/真实用量记录，避免悄悄退回合成上下文 |
| `bench_official_tasks.py` | 删除 host-projected TB 的正式运行拒绝 guard | 保留 guard；TB 正式路径必须镜像内 agent |
| `experiments/workloads/terminal_bench_suite.py` | 删除 nested Compose build-context 修正、固定外部资源、镜像用户非 root 检查、`include_verifier_files` 和 `prepare_official_test_script` | 保留本地，防止环境和正式/native 调用退化 |
| `experiments/workloads/swe_bench_suite.py` | 删除 `apply_test_patch` 兼容与 cleanup | 只合并镜像校验相关块 |
| `experiments/scripts/bench_intra_turn_replay.py` | 删除配置锁/恢复 schema 兼容检查，已有文件直接跳过 | 保留本地配置与结果兼容保护 |
| `src/agenttx/semisolate.py` | 清理修复夹带 trace spool 从可配置 `/home` 退回 `/var/tmp` | 只移植清理逻辑，保留本地 spool 选择 |
| `src/agenttx/agents/external.py` 等 | 删除部分直连/代理环境控制及计时 | 私有配置目录与本地控制合并 |
| `paper/main.tex` | 文字更完整，同时仍包含来源未统一的历史数字 | 可参考表达；不整体当作已验证论文版本 |

这些差异是同一工作区里的混合修改，不能把一个有用测试文件的存在当作整套远程 runner 已通过当前正式协议的证据。

## 4. 本次验证和未验证项

已经重新验证 281 个快照文件 SHA-256，185 个 Python 文件 AST。还使用本地独立进程、标准库 mock/临时目录检查：

1. `rollback_exact` 排序去重、空集及非法/提交/失效步骤在修改前拒绝。
2. ideal_oracle 确实调用显式真值集合，而不调用 captured closure。
3. 固定镜像探测使用预期 ID、拒绝不匹配 ID，不 pull，探测容器断网并清除代理（subprocess mock）。
4. matched handoff 在真实根/恢复内容相同时一致，根变化时不同。
5. 未挂载视图清理不跟随 symlink；模拟存在子挂载时拒绝清理。

检查脚本：[validate_remote_candidates.py](/home/bfq/agenttx-recovery-20261016/tools/validate_remote_candidates.py)。本机未重建原 Python 3.11/conda，也没有运行完整 pytest、真实 Docker/挂载后端、并发或模型实验。这些是候选逻辑验证，不能视为移植后的集成测试通过；正式采用前必须跑第 2 节对应后端/协议回归。

## 5. 顺带补齐的历史对象

远程同提交克隆有本地缺失的 4 个历史 Git blob，本次已取回：`a41fdc9…`（历史 agent.md）、`ee3ebc5…`（历史 STATUS.md）、`0f955a2…` 和 `9e98996…`（历史 notebook）。全部验证 Git blob SHA-1 与 SHA-256，坏的 loose object 已备份，`git fsck --full` 返回 0，可读 HEAD 文件从 570 增至 572。

这只修复历史对象，未覆盖当前工作区源码。凭证：[git_object_recovery_remote.json](/home/bfq/agenttx-recovery-20261016/reports/git_object_recovery_remote.json)。远程仍缺 `motivation/paper_figure_style.py`，也未找到缺失的完整系统微基准原始 CSV；这些缺口没有因代码快照取回而自动解决。
