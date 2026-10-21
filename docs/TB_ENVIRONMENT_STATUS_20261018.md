# TB 当前环境状态

**TB 已建部分验证完成，2026-10-18 22:29（北京时间）。** 219 个镜像全部完成首轮检查和定向复核，215 个通过原镜像内真实事务隔离/回滚。2 个任务通过实际官方脚本正反验证（hello-world、csv-to-parquet）；另 156 个基础事务与测试探针跑通，45 个需补测试 bootstrap 或特殊入口、12 个需配套服务、4 个事务执行适配受阻。2+156+45+12+4=219。基础探针不等于完整官方验收；正常未解题失败与环境问题分别记录。未调用模型、未使用 gold，全部失败保留，14 项相关测试通过。见 [TB 验证报告](TB_READY_VALIDATION_20261018.md)。

核验时间：2026-10-18 21:57（北京时间）；机器：r760-ssd。

全部 241 个预期任务镜像标签已对照专用 Docker 实机清单：219 个标签与记录的 image ID 一致，22 个没有镜像。首轮完成 216 个，随后独立修复 3 个。准备及修复队列均已退出，目前没有活动构建。旧失败和修复证据分别保留。

此处就绪仅指镜像构建和身份核验，不代表完整任务运行环境通过。仍需解决 agent 执行环境与镜像依赖的一致性，并验证官方验收链路；当前没有本轮 TB 全量模型实验。

剩余 22 项：

- financial-document-processor：122.6 error: Failed to download https://github.com/astral-sh/python-build-standalone/releases/download/20250828/cpython-3.13.7%2B20250828-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
- fix-git：0.985 fatal: could not read Username for 'https://github.com': No such device or address
- fix-ocaml-gc：169.4 error: RPC failed; curl 28 Failed to connect to github.com port 443 after 136425 ms: Couldn't connect to server
- html-finance-verify：136.3 curl: (28) Failed to connect to huggingface.co port 443 after 136155 ms: Couldn't connect to server
- implement-eigenvectors-from-eigenvalues-research-paper：33.04 error: RPC failed; curl 16 Error in the HTTP2 framing layer
- incompatible-python-fasttext：构建超过 3600 秒
- install-windows-3.11：构建超过 3600 秒
- install-windows-xp：构建超过 3600 秒
- leelachess0-pytorch-conversion：RuntimeError: failed to build agenttx-tb-base:leelachess0-pytorch-conversion-5da1ca316f0d: domestic-only:
- llm-spec-decoding：290.6 OSError: We couldn't connect to 'https://huggingface.co' to load this file, couldn't find it in the cached files and it looks like HuggingFaceTB/SmolLM2-360M is not the path to a directory containing a file named config.json.
- mnist-learning-fix：RuntimeError: failed to build agenttx-tb-base:mnist-learning-fix-d8215f90f29c: domestic-only:
- path-tracing：144.0 failed to download https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz
- path-tracing-reverse：141.6 failed to download https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz
- qemu-alpine-ssh：114.9 E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
- qemu-startup：120.5 E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
- query-optimize：136.1 curl: (28) Failed to connect to huggingface.co port 443 after 136039 ms: Couldn't connect to server
- reshard-c4-data：871.1 FileNotFoundError: Couldn't find any data file at /app/allenai/c4. Couldn't find 'allenai/c4' on the Hugging Face Hub either: LocalEntryNotFoundError: An error happened while trying to locate the file on the Hub and we cannot find the requested files in the local cache. Please check your connection and try again or make sure your Internet connection is on.
- shell-deobfuscation：27 |     RUN test -f /app/suspicious_script.sh || (echo "Error: suspicious_script.sh was not created" && exit 1)
- sudo-llvm-ir：135.5 fatal: unable to access 'https://github.com/sudo-project/sudo.git/': Failed to connect to github.com port 443 after 135424 ms: Couldn't connect to server
- train-fasttext：构建超过 3600 秒
- vul-flink：132.6 fatal: unable to access 'https://github.com/apache/flink.git/': Failed to connect to github.com port 443 after 132471 ms: Couldn't connect to server
- word2vec-from-scratch：RuntimeError: failed to build agenttx-tb-base:word2vec-from-scratch-17360bbedb24: domestic-only:

身份及错误明细：/mnt/data/agenttx-runtime/validation/tb-readiness-recheck-20261018.json

Docker socket：`unix:///run/agenttx-docker/docker.sock`；镜像、缓存和日志均在 `/mnt/data/agenttx-runtime`。
