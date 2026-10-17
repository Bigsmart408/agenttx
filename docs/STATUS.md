# Current status

**本机执行更新（2026-10-17）：** 代码修改、环境准备、核验和后续实验均在本机 `r760-ssd`；远程仅供参考代码，远程队列已停止。镜像及实验数据使用 `/mnt/data/agenttx-runtime`，模型实验继续暂停。此前远程环境就绪数量不计作本机进度。最新状态见 [本机环境准备](DOCKER_ENVIRONMENT_20261017.md)。

**Latest instruction: prepare the environment before experiments.** The pilot
is paused: 60 runtime cells passed, 8 interrupted, 16 deferred, and all 168
model cells unstarted. No automatic restart. See [Docker/environment
preparation](DOCKER_ENVIRONMENT_20261017.md); the earlier launch record below
is historical.

The [new stratified pilot](STRATIFIED_PILOT_20261017.md) is running remotely
with 8 experiment workers: 84 model-free runtime gates precede 168 queued
Codex/SWE-agent model cells. It uses frozen v5 sources and a common 900-second
model wall budget, with no automatic retries. This diagnostic cohort does not
fill the formal matrices; complete dual-harness micro scope is 16800 cells.

See the [current experiment inventory, including SWE-agent gaps](EXPERIMENT_STATUS_20261017.md)
and the [Chinese specification](../agent.zh-CN.md). The new formal 5410-cell
cohort remains registered but unexecuted; historical and validation cohorts
are listed separately in the inventory.

The 2026-10-17 [reproducibility check](REPRODUCIBILITY_20261017.md) completed
50 registered model attempts: 49 passed and one SWE-agent TB causal attempt
failed the official task tests after a rejected multi-block response and
premature submission. All 50 checked recovery states matched their prior
records. Regenerating the historical 400 analytical cases also matched all
24000 compared values. This small cohort does not complete the formal suite.

For the research plan and current experiment protocol, see [agent.md](../agent.md)
and [experiments/README.md](../experiments/README.md). This page replaces the
obsolete 2026-08 step-by-step status list; historical raw results remain under
`experiments/results/` and source history in Git.

The controlled 400-manifest Motivation and system microbenchmark are distinct
from the real-world SWE-Bench Lite and Terminal-Bench workloads. The native
trajectory census uses fault-free agent steps to assess how often a coarse
rollback would discard independent work. It does not directly measure replay
tokens.

The current five-mode full-run directories are diagnostic, not yet a paired
paper result. Codex has 2444/2705 recorded cells; Terminal-Bench clean has
196/241 recorded tasks and 84 successes (84/196 among recorded rows, not a
complete 84/241 success-rate measurement). Of those 196 rows, 48 report
`PermissionError: commit blocked by policy` after the host-based agent tried
to write outside its task workspace, commonly into the host Conda tree. The
other 45 expected clean task cells are missing, not known failures. Some
recorded verifier failures also lack task-image dependencies in the projected
host workspace. The six Terminal-Bench tasks whose Compose `client` service
builds from a nested `client/` context previously used the wrong Dockerfile;
the image path is corrected. A local `hello-world` official solution passes
both Docker verifier tests after this fix. Terminal-Bench preflight now checks
every selected image; the two QEMU images build against a pinned Debian
snapshot, and `llm-spec-decoding` uses verified local Hugging Face model
files. An image-native Codex `hello-world` smoke run completed and passed
both tests in the same task container. These repairs do not retroactively
make the host-projected five-mode results valid. The image cache is 239/241;
the two missing upstream inputs currently return 404. Two non-root image
tasks are explicitly rejected by the current native runner instead of being
run as root. The prior SWE-agent five-mode run was stopped after 451
completed cells because it carried a workload-derived step/model-call cap
that Codex did not; its result and log directories have been removed. The
replacement SWE-agent runner seeds a new native agent object with the scripted
pre-crash action/observation history, then uses the native history projector
to remove invalidated steps before the model resumes. Codex uses a fresh CLI
session and recovery handoff instead. A three-mode SWE-Bench smoke completed
3/3 cells without environment errors; the replacement 300-task, five-mode
SWE-Bench run is active in `sweagent_luna_max_swe_fair_20261012`. The
Terminal-Bench formal runner
also executes the agent
on the host while verifying in Docker. Image-native agent execution is
required before a new Terminal-Bench policy cohort can be called comparable.

Next validation gate: representative image-native Terminal-Bench policy
tests for both agents, with matching model, budget, fault, and verifier
configuration. Only then start a new Terminal-Bench full-run directory;
report missing and environment-error cells separately from task failures.
