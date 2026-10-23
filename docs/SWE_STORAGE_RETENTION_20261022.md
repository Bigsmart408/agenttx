# SWE-bench 数据保留规则

2026-10-22 14:16（北京时间），用户明确要求：**“swebench的结果也是一样的，只需要保留clean的完整轨迹”**。本规则覆盖此前“SWE-bench 历史不处理”的范围限制。

- Clean（`clean_recovery`）：保留现存完整 main / replay 轨迹和原始 worker 日志，逐文件记录并核验哈希。
- 其他策略：删除已结束单元的完整轨迹，将 worker 日志缩为按原顺序排列的 API Usage 数字记录。保留成功/失败、错误与验收诊断、恢复信息、配置和源码清单。
- 原始 `result.json` 及历史汇总 JSON / CSV 保持字节不变。新增 `accounting-summary.json` 保存逐调用用量及 main / replay 拆账；SWE 的 replay 先于 main，不重复增加 reasoning token。
- 历史轨迹只在结果明确证明所属 shard 为单一 SWE 策略时处理。混合策略、无法确认来源的对象保留并记录，不凭目录名判断 Clean。
- 不处理正在运行的单元、原始恢复源、实验输入、镜像和共享依赖缓存；TB 的 8 路实验继续运行。

维护代码在本机系统盘 `/home/bfq/agenttx-maintenance/retain_swe_summaries_20261022.py`，服务为 `agenttx-swe-summary-retention-20261022.service`。计划、逐项操作、清理前后实际用量和原始文件哈希在 `/mnt/data/agenttx-runtime/validation/swe-summary-retention-20261022/`。

隔离验证已通过：Clean 的 main / replay 完整保留、非 Clean 对话删除、API 用量兼容原审计读取器、原结果不变，以及重复执行幂等。实际清理完成状态以该审计目录的 `verification.json` 为准。

**已完成，2026-10-22 14:18（北京时间）。** 共处理 1808 个结束单元及 8 个历史轨迹分片：304 个 Clean 单元/分片保留轨迹，1512 个非 Clean 单元/分片缩为摘要；移除文件净字节约 3.58 GiB。1869 个原始结果/历史汇总文件哈希不变，最新全量 1799 条完整用量记录和全部已知 317,864,998 API token（含 replay、含异常已知支出）保持不变，清前清后逐项成功/失败和配对统计一致。独立核查确认 597 个现存 Clean 轨迹文件哈希一致，Clean 原始 worker 日志一致，全部计划内非 Clean 轨迹目录已移除。

历史 Clean 分片中有 2 个原本无法解析的轨迹文件，原件和哈希照常保留；这不代表恢复出了缺失内容。当前全量 Clean 单元未发现此类解析错误。清理期间遇到 root 目录权限后，以本机无网络维护容器续跑；完成服务为 `agenttx-swe-summary-retention-20261022-resume.service`，未改实验或测试代码。原始恢复源、实验所需输入和 TB 的 8 路运行未受影响。
