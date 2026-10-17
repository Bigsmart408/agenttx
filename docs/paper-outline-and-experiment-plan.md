# Current evaluation plan

Terminology / 中英文策略名称：[reference / 术语表](terminology.md).

This page is a short companion to [agent.md](../agent.md), which defines the
case grid and the strategy-level acceptance criteria. In case of conflict,
follow `agent.md` and the actual frozen `run_config.json` for each cohort.

The paper's evidence chain is Microbenchmark, Real-world Workloads,
Breakdown, Appendix Correctness and Interrupted Recovery, then Other Overheads.
The Motivation figures use one common controlled manifest set and explain
why chronological order and dependency order diverge. The native trajectory
census tests whether independent work at risk of coarse rollback also occurs
in actual agent traces. It is not model-token savings, and it retains failed
and zero-opportunity traces in the denominator.

For real-world workloads, use all 300 SWE-Bench Lite and 241 Terminal-Bench
tasks. Each policy uses the same task version, Luna max model, fault schedule,
prompt budget, wall timeout, agent execution environment, and verifier.
`causal` uses compact recovery context. `clean_recovery` has no injected
fault. Report success and environment failures alongside paired-success
`main + replay` tokens; never infer success from a low token count.

Terminal-Bench's task image is part of its workload. Both the live agent and
the official verifier must run there. The current formal runner is
host-projected, so its existing Terminal-Bench rows are diagnostics only.
Codex's older five-mode directory contains 2444/2705 expected cells overall,
and only 196/241 Terminal-Bench clean cells. The stopped SWE-agent directory
contains 451 completed cells and had an extra count cap. Neither is a valid
complete matched pair for paper success-rate claims. Preserve these raw rows
for audit, but do not merge them with the new protocol. The clean native-image
trajectory census is a separate measurement, not a recovery-policy run.

Before new full runs: finish image-native Terminal-Bench policy execution,
validate representative tasks under both harnesses in Docker, confirm count
limits and prompts match, verify all task images and official verifiers, then
freeze a new run directory. Treat missing cells, incomplete harness exits,
verifier failures, and infrastructure errors as separate categories. Do not
carry completed cells from a different runner revision into that directory.
