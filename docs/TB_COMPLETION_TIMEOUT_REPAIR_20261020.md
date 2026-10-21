# TB 注入残留与超时判定修复

更新：北京时间 2026-10-20 15:08。用户明确要求修复注入残留问题，并指出超时不应覆盖已经通过的结果。本节覆盖早先900秒与model_finished成功门槛。

## 实际修改

- `bench_tb_image_native.py` 升级 v5。原生恢复提示的末尾为所有策略统一追加公开清理合同：`recovery_build/derived.txt` 必须不存在，存在则删除，独立笔记保持完整。旧 native 路径绕过旧 `task_prompt()`，仅隐藏故障位置的 manifest 未交代这项检查，是本次根因。固定验收产物对各组同等公开；不公开不回滚/checkpoint/整分支的故障源码或 step。Agent 自行清理，工具和token开销照常记账。
- 成功规则现在允许模型rc0或124，且同时要求官方测试rc0、documents_valid、derived_removed。124另记model_timed_out／passed_after_model_timeout；验收超时、残留、笔记损坏仍不通过，harness故障不会被直接转成功。
- 模型会话60分钟，验收30分钟；main/replay各自同一会话上限，worker总包络160分钟，覆盖两次会话与验收及准备余量。六策略、双harness相同，总并发8，无step/call上限。
- API用量不完整与任务正确性分别报告。确认成功但最终账单缺失的观测仍保留异常、已知用量下界与排除理由，不补零进token比较。
- 新旧结果按实际protocol、cleanup合同、模型时限和验收时限分别统计配对成本。顶层配对比较只用当前配置；跨配置计数只作进度。继承旧原始结果，不修改success字段，不按成败重跑。变更必须以`--allow-protocol-update`及登记的previous/current参数承接，此参数已由用户本轮指令授权执行，无额外审批。

## 验证

1. 当前26项host检查通过，覆盖超时成功、残留／笔记／验收失败仍失败、六策略一致清理提示、隐藏故障位置、缺用量与配置变更保护、预算整数／浮点等价及配对分组。
2. 原任务镜像内47项回归通过，包含真实事务／依赖回滚与manifest一致性。host直接运行这部分需要root namespace，未通过宿主机的user namespace检查不能当作产品回归；容器内已完整通过。只读pytest缓存产生两条无关写缓存警告。
3. SWE-agent与Codex各一次broken-python原镜像完整preflight通过：真实adapter导入、污染状态被拒绝、经agent工具清理后状态可见、独立笔记保留、已验证超时通过；零模型调用。该任务使用此前已校验的只读便携Git前提，不修复题目Python故障。
4. 最初hello-world的诊断外层仅给180秒，Git下载耗时，其内部197.90秒后实际通过；外层诊断超时和内部通过分别保留，没有伪造host成功。这与正式新60分钟会话限制不同。

## 已有结果重算

独立审计快照（不是覆盖原结果）：完整观测的原成功150，按新超时判定152。变化是adaptive-rejection-sampler/SWE-agent的whole_branch_abort与ideal_oracle。另adaptive-rejection-sampler/Codex/Clean、amuse-install/Codex/checkpoint也通过官方与恢复检查，但缺完整API用量，仍单列保留异常。22条旧不回滚清理失败不能凭新提示追认成通过；其提示缺陷标记保留。

## 运行交接

持久服务`agenttx-tb-completion-handoff-20261020-r10`于15:06:38暂停旧dispatcher，仅让在途worker自然结束。15:15:12切换r10成功；15:16:23核验252完整、25保留异常、8路运行、2607待跑、stop_reason=null。277条旧终态均按原路径／哈希继承；新8路的60/30分钟预算、清理合同及冻结源码哈希已核对。只续未开始项，新旧配置分组比较。

交接中另查实QEMU initramfs/kernel缺Git时在线安装超过240秒。保留旧失败；两题现复用此前已校验的只读便携Git，均完成实际原镜像preflight（仍零模型调用），不向题目提供实现。

## 证据

- [/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/repair-registration.json](/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/repair-registration.json)
- [/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/historical-rescore.json](/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/historical-rescore.json)
- [/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/container-regressions/output.log](/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/container-regressions/output.log)
- [/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/portable-git-sweagent/crash_no_rollback/host-result.json](/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/portable-git-sweagent/crash_no_rollback/host-result.json)
- [/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/portable-git-codex/crash_no_rollback/host-result.json](/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/portable-git-codex/crash_no_rollback/host-result.json)
- [/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r10/registration.json](/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r10/registration.json)
- [/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r10/drain-progress.json](/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r10/drain-progress.json)

本次源码manifest SHA256：`e16b3fea16ef4705d0316dc6571aabdaaf6e1a9e6ea478c12294d8503babfdbd`。后续冻结版本应与其一致。原始日志与结果均在数据盘；系统盘仅代码。

最终运行核验证据：`/mnt/data/agenttx-runtime/validation/tb-completion-repair-20261020-r1/rollout-proof.json`。
