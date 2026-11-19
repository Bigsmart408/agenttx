# 从问题文件恢复并检查命令残留

新协议 `agenttx.file-report-step-controls/v1` 中，我们的 causal 方法接受问题文件和参考 checkpoint，不接收注入 step；其他对照策略仍直接接收具体 step。Checkpoint 是指定命令完成后的状态；省略时使用已提交边界。恢复范围包括该 checkpoint 之后对问题文件的全部修改，以及沿文件依赖和结果依赖传播的命令。它定义文件历史的撤销范围，不声称辨认其中哪一次编辑在语义上出错。

生产入口先在独立分支检查，再应用到当前会话：

```python
outcome = tx.recover_file(["src/parser.py"], checkpoint_step=checkpoint_step)
assert outcome.audit["valid"]
```

模型工具 `rollback_file` 的必填参数只有 `paths`；`checkpoint_step` 可选。用户消息只指出问题文件。CLI：

```sh
PYTHONPATH=src python -m agenttx.cli recover-file --session SESSION --dry-run src/parser.py
```

检查在恢复前冻结依赖和历史版本，在模型继续执行之前核对：

- 应撤销命令是否仍在 active ledger 或上下文中；是否存在已撤销命令指向仍保留命令的依赖。
- 文件内容、存在性、权限、符号链接及受支持的扩展属性是否匹配所选历史版本；报告文件是否回到参考 checkpoint。
- 保留文件是否改变，lower 是否改变，文件与上下文的选择是否一致。

依赖完整性以记录覆盖为前提。受控实验还需用独立工作流定义评分，避免用待测依赖图同时充当真值。`bench_file_recovery.py --demo` 已包含这种独立检查，使用真实 strace 捕获，不调用模型、不扫描故障标记。Demo 的对照策略接收生成器注册的 step，我们的方法只接收文件：

```sh
PYTHONPATH=src python experiments/scripts/bench_file_recovery.py --demo --output /tmp/file-recovery.json
```

也可对既有会话的各策略进行隔离比较。`--files` 为我们的方法提供输入，同时定义独立评估范围；对照策略只用显式 `--fault-steps` 选择撤销命令。Turn checkpoint 继续接收已登记的 affected steps：

```sh
PYTHONPATH=src python experiments/scripts/bench_file_recovery.py \
  --session SESSION --files src/parser.py --checkpoint-step CHECKPOINT \
  --fault-steps STEP --affected-steps STEP DEPENDENT_STEP \
  --output /tmp/file-recovery.json
```

应用基准 `bench_official_tasks.py` 的 CLI 默认使用 `--recovery-input file`，其中 causal 使用文件输入，其他策略（包括 ideal-oracle）保留 step 输入；输出到独立的 `file-report-step-controls-v1-HARNESS` 目录。程序调用 `run_once(..., recovery_input="file")` 显式选择新协议；既有程序入口默认保留 legacy，以免改变已经冻结的实验。旧协议可通过 `--recovery-input legacy` 复现，禁止同一结果目录混入两种协议。

只有我们方法的交接消息给出问题文件、不指出错误 step；其他策略的交接消息直接给出已知错误 step。Turn checkpoint 及 ideal-oracle 保留其原有的受影响 step 集合提示。恢复检查在独立笔记重建和模型续跑前记录；`file_recovery_audit` 与最终官方任务测试分别存储。`root_only` 是旧实验字段名，新协议将它映射为 `selected_effects`。不完整的对照策略会记录残留，不由验证器补做因果回滚。检查通过也不会向模型宣称原任务已经解出。

实际 Terminal-Bench 镜像运行器 `bench_tb_image_native.py` 也支持 `--recovery-input file`，host 会将参数传入容器，并用独立协议标识结果。它的默认值仍为 legacy，保留现有冻结任务的行为。
