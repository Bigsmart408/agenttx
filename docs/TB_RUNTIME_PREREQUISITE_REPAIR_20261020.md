# TB 本地 Git 与 SWE-agent 内部时限修复

2026-10-20，北京时间 16:34，本机 r760-ssd。

## 已恢复运行

r11 于 16:32:45 接管，继承 r10 的全部 289 条终态（263 完整、26 保留基础设施异常），只派发未开始项。16:33:51 监控确认 8 路运行、2595 待跑，停止原因为空、告警为空，数据盘空余 364.7 GiB。r10 最后一条 Ideal 自然结束，未中断在途 worker，未重跑旧失败。

8 条新任务均为 build-pmars；实际容器命令、worker PID、冻结源码、上下文和 Git 挂载已经逐条核对。Causal/Ideal/Clean compact，其余 full；总并行仍为 8。

## 两个阻塞原因与修复

1. build-pmars/SWE-agent/Clean 在模型调用前在线安装 Git 达到 240 秒，调度器因未知基础设施异常停止派发。现在所有任务均挂载经过原 proof 文件逐项校验的只读便携 Git，优先保留镜像自带可用 Git；仅在缺少 Git 时创建指向本地工具的链接，不再等待 APT。便携动态加载器在私有 `/opt` 前缀运行，不覆盖镜像的 Git 或系统 loader。已有占用路径（含坏链接）拒绝覆盖。
2. 上轮只放宽了外层会话时限，遗漏 SWE-agent 上游默认的单命令 30 秒、累计工具执行 1800 秒。Linux kernel 的 SWE-agent 不回滚、checkpoint、whole、causal、ideal 五条新完整结果均触发 `exit_total_execution_time`，因此不能直接归因于模型解题能力。现在真实 SWE 配置设置单命令 3600 秒、累计 3601 秒，由原有外层 3600 秒会话期限统一约束；配置哈希包含限值。内部累计超时返回124，包括 autosubmit 情况；只有官方验收与恢复检查全部通过才算成功，其他内部错误保持失败。

会话 3600 秒、verifier 1800 秒、worker 包络 9600 秒，无 step/call cap，清理合同保持 v5 修复后的统一要求。协议升级至 v6，新旧结果按实际配置分别比较，不混算配对收益。

## 新完成结果

相对 r10 启动时新增 11 条完整结果：Codex 的 build-linux-kernel-qemu 六策略全部联合成功；SWE-agent 新增五条均失败，并记录上述内部预算退出。Codex 不回滚也已清除派生产物，因此此前注入残留的提示缺陷在本题修复有效。

该题 Codex 的 main + replay API tokens：

| 策略 | Tokens | Causal 节省 |
| --- | ---: | ---: |
| 不回滚 | 2,077,177 | 24.99% |
| checkpoint | 2,288,816 | 31.92% |
| whole | 1,743,984 | 10.65% |
| Causal | 1,558,174 | — |
| Clean | 1,626,295 | 4.19% |
| Ideal | 1,840,571 | 15.34% |

这些是 v5 下仅一个任务的配对，不能当全量收益；v6 尚无完整配对。旧22条不回滚残留失败不追认成功，原结果和用量不改。

## 验证与存储

- 50 项 host 检查通过；原任务容器 49 项事务／恢复回归通过（两组有重叠，不相加）。
- build-pmars 双 harness 零模型原镜像预检通过，真实清理／笔记保留／超时成功合同检查通过，实际 SWE 配置为3600/3601。
- build-stp 原镜像 SWE 预检通过，确认使用 `image-provided` Git；额外 hello-world 缺 Git 预检也通过。
- 仅清理专用 Docker 未使用构建缓存：旧缓存123.7 MB，加剩余未使用缓存12.78 GB；保留镜像、实验原始数据及日志。空余从约351.5恢复至364.7 GiB；350 GiB保护阈值未降低，后续仍需关注增长。
- 冻结源码 SHA256：`eaa8ac5ca6460a67f95e104456b99ea53f19b228fc9f84adaaf9f86eb5e81092`。

证据：

- `/mnt/data/agenttx-runtime/validation/tb-runtime-repair-20261020-r11/`（host-tests.log、container-regressions/output.log、pmars-*、native-git-sweagent、repair-registration.json、rollout-proof.json）
- `/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r11/`（旧worker排空、源结果哈希、交接登记）
- `/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261020-r11/`（当前登记、进度、按配置分组的结果）

服务 `agenttx-tb-dual-full-20261020-r11.service` 与每30秒检查的监控均 active。监控负责发现异常；不能把它描述为能自动诊断所有未知环境问题。
