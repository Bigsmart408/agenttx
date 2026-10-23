# AgentTX Experiment Specification

**Mandatory user constraint — leave port 8080 forwarding untouched (2026-10-23 10:19, Asia/Shanghai).** The user explicitly instructed “不要动8080的转发” and requested this rule in agent.md. Do not add, delete, modify, flush, replace, restore from a snapshot, or bypass any existing host or shared port-8080 forwarding, redirect, DNAT/SNAT, proxy, or firewall rules. Do not add endpoint-specific ACCEPT/RETURN exceptions to bypass those rules. Do not create local forwarding/proxies or change Codex/provider configuration to work around them. Keep the configured remote API connection direct. This overrides older instructions and repair scripts that add a port-8080 PREROUTING exception. Diagnose connections with read-only checks. Fix experiment network isolation and scheduling instead; task containers must use private network namespaces rather than privileged host networking, so task firewall operations cannot alter host port-8080 forwarding. Any task-bridge egress rules must stay scoped to owned bridges/subnets and must not modify or bypass port-8080 forwarding. Do not run old network helpers that still add the superseded bypass. Record evidence and original experiment outcomes unchanged.

**Latest commit/status audit, 2026-10-23 19:02 Asia/Shanghai.** r20 stopped dispatch and drained at 17:01:56; active workers=0. First attempts:1090/2892 complete (936 success,154 failure),82 infrastructure/accounting exceptions,1720 pending. Retries:133 complete (64 success,69 failure),100 exceptions,97 pending. Recomputed final accounting has182 incomplete attempts; the coordinator's saved report predates its last terminal cell. Three Codex HF training attempts timed out with incomplete final billing; the open-ended incident-window rule inferred connection failure, without proving a new API outage. HF official-verifier outbound failures also remain unresolved. This commit records the pause, not a restart or completed repair. Full status and paired cumulative main+replay savings: [2026-10-23 status](docs/EXPERIMENT_STATUS_20261023.md). Preserve all original outcomes/costs and the mandatory 8080 constraint above.

**Verified restart success, 2026-10-23 11:05 Asia/Shanghai.** Active r20 has 4 fresh complete observations, 4 official passes and 0 fresh infrastructure exceptions, with 8 workers active and API authenticated health 200. The four verified create-bucket passes are SWE-agent/Clean (41885 main+replay tokens), SWE-agent/no-rollback (55096), Codex/Clean (233593), Codex/no-rollback (321278); all bills complete, totaling 651852 tokens. Host 8080 rules match the initial read-only observation, and all seven task containers inspected during the snapshot used private network namespaces (one slot was starting). Evidence: validation/tb-original-service-resume-20261023/restart-success-progress.json and runtime-isolation-check.json. These fresh outcomes are separate from adopted prior attempts and environment interruptions.

**Current r20 continuation and original service repair, 2026-10-23 11:01 Asia/Shanghai.** Active r20-original-service-resume supersedes r19 dispatch. Hosts-fixed create-bucket attempts still failed because the worker replaced the original LocalStack ENTRYPOINT and tool-started daemons did not reliably survive to verification. Preserve the original task-image ENTRYPOINT as an independent-lifetime service in the same private network; agent and official verifier share it, with no bucket/public ACL pre-created. Real service health=200, initial S3 buckets empty, both zero-model adapter/transaction preflights and 50 focused checks pass. Four affected old SWE-agent attempts were interrupted (a fourth Ideal started before dispatch paused), retaining at least 665343 logged tokens as incomplete lower bounds; exclude them from complete-cost comparisons and algorithm-failure counts, never erase their spend. Adopt four unaffected HF workers and fill four new slots, total <=8. Preserve every stage and cost, add at most one eligible attempt per audited repair stage, report actual fresh success only after official tests. No host 8080 rule or model configuration changes. See docs/TB_NETWORK_RESUME_20261023.md and validation/tb-original-service-resume-20261023/.

**Hosts-baseline repair and current continuation, 2026-10-23 10:45 Asia/Shanghai.** Active r19-hosts-resume supersedes r18 dispatch. The first r18 completed Codex/create-bucket/no-rollback attempt billed 557866 tokens but official tests failed with localhost name resolution; preserve this outcome and spend. The transaction baseline had copied Docker resolv.conf but omitted Docker hosts. Copy both before model execution and check localhost resolution; Codex and SWE-agent zero-model preflights in the original image pass, with 44 focused checks. r19 adopts all live r18 workers (including inherited r17 ones), preserves all attempts, registers an additional repair for the demonstrated localhost environment failure, and keeps <=8 total. Stopped ancestor dispatchers are only retired after adopted workers drain; do not resume old host networking. New r19 attempts use the new frozen baseline; inherited workers retain their original source version. No 8080 rule or model endpoint configuration is modified. Report fresh r19 successes separately from adopted outcomes and preflight/API checks. See docs/TB_NETWORK_RESUME_20261023.md.

**Connection-incident continuation, 2026-10-23 10:33 Asia/Shanghai.** User requests resuming from the connection-refused failures and reporting verified execution/success after restart. r18-network-resume preserves r17 original/retry records and adopts three in-flight workers, keeping total concurrency <=8. Prioritize 132 incident-affected cells in source failure-time order (44 explicit model-connection diagnostics, 88 inferred incomplete bills during the confirmed incident); reuse pending retries, add at most one separately recorded repair attempt after a connection-failed previous retry. Sum every original+retry+repair main/replay cost; incomplete attempts exclude the cell from complete-cost pairs. Task workers use private network namespaces with a pinned slirp4netns general outbound stack, no host firewall writes, host port listeners, endpoint proxy or API configuration changes. Actual namespace/firewall-isolation/authenticated API/DNS checks passed; 38 focused checks pass. No 8080 rule is added, removed or bypassed with a port-specific exception. API health gates dispatch; fresh unknown environment exceptions halt dispatch. Model/context/seed/tests/limits and Clean-only full trajectories remain fixed. See docs/TB_NETWORK_RESUME_20261023.md; start alone is not proof of task success.

**User-authorized failed-cell retries, 2026-10-22 18:23 China time.** Latest instruction "任务失败的需要重启" supersedes earlier no-outcome-retry restrictions. Active r17-retry1 adopts r16 running workers without interruption, continues pending first attempts and gives every failed/incomplete first attempt one retry, at most 8 workers combined. Initially117 queued (107 task failures +10 accounting exceptions); repeated failures are preserved, not retried indefinitely. Original outcomes remain byte-identical; retry-cells and retry_tasks are separate. effects.json is first-attempt only; retry-effects.json is retry-only; retry-report.json uses the latest attempted outcome and cumulative original+retry main+replay costs, excluding any cell with incomplete billing from complete-cost pairs. Same frozen worker sources/model/context map/seed/verifier/deadlines, disabled disk guard and Clean-only full-trajectory retention. Two retries actually dispatched;894 original result hashes verified;4 focused tests pass. Service agenttx-tb-dual-full-20261022-r17-retry1. See docs/TB_FAILED_RETRY_20261022.md; audit validation/tb-failed-retry-20261022/.

**SWE retention completed, 2026-10-22 14:18 China time.** Processed 1808 terminal cells and 8 historical shards, preserving 304 Clean roots / 597 existing trajectory files with matching hashes; summarized 1512 non-Clean roots. Removed 3.58 GiB. All 1869 original result/aggregate hashes and the latest 1799 complete bills, 317,864,998 known API tokens and matched outcome statistics are unchanged. Two already-unparseable historical Clean files are preserved verbatim; no such parse issue in current full-cohort Clean cells. Verification: validation/swe-summary-retention-20261022/verification.json and independent-verification.json. Local root maintenance container completed via agenttx-swe-summary-retention-20261022-resume; active TB workers and inputs preserved.

**SWE-bench storage update, 2026-10-22 14:16 China time.** The user explicitly extends Clean-only full-trajectory retention to SWE-bench, superseding the previous exclusion. Preserve Clean main/replay and original worker logs; other ended cells retain original results, sequential API usage, main+replay accounting, recovery and failure/verifier summaries. Historical single-policy SWE trajectory shards are covered; experiment inputs and active TB runs are preserved. Maintenance: agenttx-swe-summary-retention-20261022.service; audit: validation/swe-summary-retention-20261022/. See docs/SWE_STORAGE_RETENTION_20261022.md.

**Latest user-authorized storage policy, 2026-10-22 14:05 China time.** Retain full model trajectories only for Clean (clean_recovery), both harnesses, main and replay. Other five arms retain key outcome/main+replay/per-call usage/configuration/source manifests/recovery summary/failure and verifier diagnostics, but remove full trajectories. Original host-result bytes remain unchanged. Remove terminal-cell transaction snapshots, duplicate cell caches and large detailed ledgers for every policy. Preserve Clean model/replay outputs and logs; extract native Codex transcripts before snapshot removal and keep original transcript paths. Non-Clean sessions keep accounting events only so frozen usage readers can still recalculate; model summary preserves essential terminal errors. Never clean running/pending cells, original inputs, images, shared dependency caches or environment evidence; SWE-bench now follows the same Clean-only full-trajectory rule under the latest explicit user instruction; see docs/SWE_STORAGE_RETENTION_20261022.md. This explicit user instruction supersedes earlier full-trajectory/archive-retention statements. No failure/outcome summaries deleted or selective reruns. Persistent agenttx-tb-summary-retention-20261022 is running from system-disk maintenance code; audit on data disk validation/tb-summary-retention-20261022/. Four isolated Clean/non-Clean×Codex/SWE verification scenarios passed, covering usage, original host results, full Clean trajectories, non-Clean removal and idempotence. Earlier r15 archive completed11:56 with content/metadata verification and125 unchanged result hashes; now also follows this policy. GPG hang repaired by terminating only each task-owned orphan gpg-agent which was the sole remaining strace tracee after the command exited; no task/test/prompt/model changes. All12 decommissioning-service cells now complete and successful. Audit: validation/tb-gpg-daemon-repair-20261022/.

**Runtime review, 2026-10-22 10:56 China time.** r16 at10:54:569 complete (520 successful,49 task failures),7 unchanged incomplete-usage exceptions,8 running,2308 pending;79 new complete observations. All569 complete bills available; no stop reason/monitor alert. Active cprofiling-python and count-dataset-tokens model/tool processes confirmed. Causal success48/48 Codex and42/48 SWE. Paired main+replay savings vs no rollback/checkpoint/whole/Clean/Ideal: Codex16.68%/15.42%/21.95%/1.05%/−9.17%, SWE24.84%/29.94%/37.14%/8.05%/5.66%,36–47 pairs. Codex still costs more than Ideal; Clean advantage only1.05%. SWE Clean per-task median−10.02%, so pooled advantage is not universal. About259GiB free, disk guard disabled. Persistent agenttx-tb-cold-archive-20261022-r15 started to copy stopped r15 cells to the other local data disk, verify all content/metadata, then preserve original paths by symlink and release duplicate SSD data. Still copying; no reclaimed-space claim. No active r16 files or raw observations removed. Audit: validation/tb-progress-review-20261022T025439.json and tb-cold-archive-20261021-tb-dual-native-full-20261021-r15/.

**Overnight review, 2026-10-22 09:37 China time.** r16 remains active with8 workers, no dispatch stop/monitor alert. At09:36:490 complete (450 successful,40 task failures),7 unchanged incomplete-usage exceptions,8 running,2387 pending;372 new complete observations since last report. Two exceptions passed official checks but lack complete billing and remain outside complete-cost comparisons. All490 complete usage records available. Conda conflict resolution and git webserver tasks are active; conda solver CPU activity confirmed. About598GiB free; disk guard disabled. Paired main+replay savings vs no rollback/checkpoint/whole/Clean/Ideal: Codex19.76%/15.89%/22.87%/14.63%/−11.05%, SWE-agent22.72%/14.97%/33.70%/8.77%/3.71%,31–40 successful complete-usage pairs. Per-task median savings: Codex30.32%/18.31%/4.66%/8.81%/0.48%, SWE-agent11.18%/5.84%/28.14%/−7.62%/0.61%. Causal successful41/41 Codex and36/41 SWE. All six policies retained with original compact/full map and no selective retry. Audit: validation/tb-progress-review-20261022T013625.json and tb-runtime-health-20261022T0137.json.

**Interruption repaired and8 workers resumed, 2026-10-21 21:31 China time.** r15 drained and stopped20:15:118 complete (112 successful,6 task failures),7 incomplete-usage exceptions,2767 unstarted. Ancient-puzzle SWE connections failed because a host NAT PREROUTING rule redirected all TCP port8080 to local18080. Reproduced in the original service network. Repair adds/removes an owned bridge+subnet exception only for10.249.44.127:8080, leaving the global redirect intact. Original sidecar8090 and authenticated API HTTP200 verified;15 tests pass. r16 started21:30:36 with all125 terminal observations unchanged and only pending cells dispatched. Six policies/two harnesses share8 workers, original compact/full map and disabled disk guard retained. Real SWE Causal/Ideal API calls and usage confirmed. No monitor alerts. Seven exceptions comprise4 Codex deadline/missing-final-usage records and3 SWE connection errors; none is imputed as zero cost. Paired main+replay savings vs no rollback/checkpoint/whole/Clean/Ideal: Codex19.71%/17.63%/25.89%/25.20%/11.73%, SWE-agent58.58%/25.58%/58.38%/19.29%/22.99%;8–10 pairs, preliminary. Audit: `/mnt/data/agenttx-runtime/validation/tb-ancient-network-repair-20261021-r1`.

**Runtime review, 2026-10-21 17:51 China time.** r15 has 71 completed cells (latest tally70 successful,1 official performance failure),8 running, no stop reason/infrastructure error/monitor alert. Seven cells are on add-benchmark-lm-eval-harness with large ESCI/dependency downloads; Codex/Clean is in pytest, Codex/checkpoint is installing dependencies from a domestic mirror. The long SWE/checkpoint sampler has finished model execution and is actively running official pytest/R; total elapsed time alone is not a hang. About524GiB free; disk guard remains disabled. Causal6/6 successful on each harness. Paired main+replay savings vs no rollback/checkpoint/whole/Clean/Ideal: Codex43.13%/14.00%/50.88%/21.99%/−11.35%, SWE-agent66.00%/43.66%/68.50%/28.65%/33.20%. Five pairs for SWE checkpoint and Codex Ideal, six otherwise. Preliminary only; Codex currently consumes11.35% more than Ideal.

**Runtime review, 2026-10-21 16:44 China time.** r15: 62 completed (61 successful, 1 task failure), 0 infrastructure errors, 8 running, 2822 pending; all 62 terminal usage records complete. No stop reason or monitor alert; disk guard remains disabled. Adaptive-rejection-sampler model/tool logs continue updating. The diagnosed accelerate-maximal-square/Codex/Ideal failure is the official speed test: 15.75x versus required >100x; model exit and artifact cleanup checks passed. Retained as task failure without selective retry. First5 paired-success tasks give main+replay savings versus no rollback/checkpoint/whole: Codex41.23%/25.74%/35.59%, SWE-agent70.47%/43.66%/35.80%. These remain preliminary small-sample estimates. Audit: `/mnt/data/agenttx-runtime/validation/tb-progress-review-20261021T084412.json`.

**Execution check, 2026-10-21 15:57 China time.** Cleanup completed: cells from14 old full cohorts and the old validation, plus both superseded migration copies, were deleted;565 per-cell host-result records and cohort JSON summaries remain. Free space rose from about284GiB to about946GiB while new workers continue writing. Fresh r15 has completed21 cells, all successful with complete usage, with8 workers running, no stop reason or monitor alert. Ideal and Clean each already have new successes on both harnesses. Audit: validation/tb-full-reset-20261021-r15/cleanup-complete.json and final-verification.json.

**User-authorized fresh full run, 2026-10-21 15:46 China time.** The instruction to delete previous runs and ignore the disk guard supersedes the earlier retain-all/resume-pending/archive/400GiB requirements. The old launcher, archive job and final download-blocked Clean validation were stopped. Historical validation remains59/60 terminal,58 successful and1 failed; its last cell was canceled for this reset, not passed. Local r15 has actually started a fresh241-task ×2-harness ×6-policy grid (2892 cells), with8 shared workers and no inherited observations. Ideal and Clean are included; Causal/Ideal/Clean use compact, the three controls full. Model, seed, tasks and official verification are unchanged. Explicit --min-free-disk-gib0 disables this run’s350GiB dispatch guard; monitoring reads the registration and no400GiB wait applies. Cleanup covers old TB cells and this migration’s archive copies, retaining summary JSON, per-cell host-result records, images, caches, environment evidence, original recovery sources and SWE-bench history. Audit: /mnt/data/agenttx-runtime/validation/tb-full-reset-20261021-r15/. Earlier hold/resumption/space statements below are historical and superseded.

**Trend validation, 2026-10-21 15:12 China time.** 59/60 terminal observations:58 successful,1 task failure,no infrastructure exceptions. Both Causal harnesses succeed5/5. Against no rollback/checkpoint/whole, Codex main+replay savings32.08%/44.34%/29.40%, SWE-agent16.34%/14.17%/54.22%; all per-case medians positive, using5/4/5 common four-way-successful tasks. All three primary trend checks now pass. Final build-tcc-qemu/SWE-agent/Clean remains in an external wget download; full-panel completion is pending and full dispatch stays held. Data disk358GiB, near the unchanged350GiB guard. See [fixed validation](docs/TB_TREND_VALIDATION_20261021.md).

**Latest instruction: repair and validate trends before full dispatch, 2026-10-21 14:12 China time.** Full r14 dispatch was held14:04 and its remaining workers reconciled14:09; the full service is stopped, preserving473 complete observations,33 exceptions and2386 pending cells. A durable hold blocks full launch, continuation and dispatch;36 relevant checks passed. A separate fixed diagnostic panel started14:10: hello-world, csv-to-parquet, build-stp, build-tcc-qemu and catch-me-if-you-can, both harnesses and all six arms,60 cells, at most8 workers, one attempt each without task replacement or outcome-based retries. Preregistered checks require complete official outcomes/usage and common four-way successful cases, lower Causal main+replay totals and nonnegative median savings against the three controls in both harnesses, with no lower Causal success count. Clean/Ideal remain reference arms. Validation is incomplete and does not auto-launch full work. Three r14 Codex failures include actual Responses HTTP404; current Responses/Chat probes return200. Two old in-flight containers exited137 without complete model results; exact cause remains unresolved. See [fixed trend validation](docs/TB_TREND_VALIDATION_20261021.md).

**TB code regression audit and repair, 2026-10-21 11:30 China time.** Confirmed handoff defects: 9 Codex/Causal results searched or executed an internal localization-verifier label; 16 disclosed hidden fault paths lost their leading dot; redirected official-test diagnostics were not returned to callers. v7 supplies the real verifier command to all policies, replaces stale Codex controller notices with the final authoritative handoff, preserves hidden paths, and exposes/archives unchanged verifier diagnostics and exit codes through a read-only wrapper. 85 focused host checks, 52 original-container regressions and both zero-model adapter/transaction preflights passed; untouched hello-world verification still fails with rc1 and now displays assertions. Three unrelated image-build tests also fail against the frozen old source and are not claimed as passing. Recorded usage agrees for188 v6 complete results; independently parsing94 Codex raw CLI stdout main+replay totals also agrees, without adding cached/reasoning subsets twice. Compact still limits only the retained pre-crash prefix; none of62 audited prefixes exceeds6 calls, and historical notes were also one line. These defects do not yet explain all cost changes or establish repaired model benefits; long main-solving tails remain. Persistent r13→r14 handoff started11:28:35: old dispatcher is paused,8 independent workers finish naturally, then only pending cells resume under v7. At this snapshot it is still draining. Preserve all old outcomes/raw usage, no outcome-based retries, and separate v6/v7 comparisons. See [audit and repair](docs/TB_CODE_REGRESSION_AUDIT_20261021.md).

**TB results and resumption, 2026-10-21 10:37 China time.** 421 complete,28 retained exceptions; r13 restored8 workers with2435 pending. Current v6 has158 complete:118 success/40 failure. r12 stopped at04:35 after circuit-fibsqrt/Codex/Causal ended with structured provider max_output_tokens failure and no final usage; it was idle until this review. Only orchestration classification changed:hashed model result, exact terminal turn.failed and matching return codes required;29 checks passed, unknown errors still block. All449 terminal observations retained; no retries or zero-imputed costs. Same-v6 paired-success Causal main+replay savings:Codex no rollback12.17%(6 pairs), checkpoint -36.90%(8), whole -4.86%(9), Ideal0.21%(9), Clean -12.63%(9); SWE-agent14.60%(8), -46.00%(8), -81.04%(9), -60.84%(9), -74.79%(6), respectively. Negative means more consumption. SWE/whole median per-case saving24.16%, but Causal build-tcc-qemu alone used5625800 tokens,76.74% of paired Causal total; retain the high value. Codex/whole median per-case saving -2.61%. About1049GiB disk free. See [stage results](docs/TB_RESULTS_20261021.md).

**TB progress, 2026-10-20 22:57 China time.** r12:299 complete,26 retained exceptions,8 running,2559 pending; no stop reason/alerts. Added15 complete since22:21:14 passed; build-stp/SWE-agent/Clean failed official verification despite normal model exit and valid recovery. No new infrastructure exception. All build-stp arms finished; work advanced to build-tcc-qemu, whose Codex Clean already passed. All15 new API usages are complete and cleanup checks passed. Same-v6 paired-success Causal main+replay token savings:Codex no rollback12.03%(2 pairs), checkpoint -44.50%(3), whole -9.27%(3), Ideal -44.75%(3), Clean -18.48%(3); SWE-agent no rollback8.51%(3), checkpoint -24.74%(3), whole28.26%(2), Ideal -9.33%(3), Clean41.38%(1). Negative means higher consumption; only1–3 tasks. Codex build-stp Causal used4159351 tokens vs checkpoint2084039 and whole1798009, reducing current aggregate savings; deeper cause not yet established. Disk free1323.1GiB. Evidence:`/mnt/data/agenttx-runtime/validation/tb-progress-review-20261020T1457.json`.

**TB resumed after disk space recovered, 2026-10-20 22:21 China time.** r11 drained and stopped at17:03:47 on the350GiB guard (344.6GiB free); it was not running during the intervening hours. It finished with284 complete and26 retained exceptions, adding10 successful observations since16:53. Space had already recovered to1355.4GiB before this turn; no cleanup attribution is claimed here. r12 now resumes pending work:284 complete,26 exceptions,8 running,2574 pending, no stop reason. All310 terminal observations remain unchanged; frozen source SHA256 matches r11, protocol unchanged, no outcome-based retries. Current work:build-pov-ray Ideal and build-stp. Same-configuration paired-success Causal main+replay token savings:Codex no rollback50.44%(1 pair), checkpoint -13.06%(2), whole28.66%(2); SWE-agent no rollback5.07%(2), checkpoint -18.62%(2), whole32.64%(1). Negative means higher cost; only1–2 tasks, not a full-suite conclusion. Evidence:`/mnt/data/agenttx-runtime/validation/tb-progress-resume-20261020T1422.json`.

**TB progress review, 2026-10-20 16:53 China time.** r11: 274 complete, 26 retained infrastructure exceptions, 8 running, 2584 pending; no stop reason or new infrastructure exception. Added 11 complete observations since 16:35: 9 passed, 2 failed official verification; all 11 removed the injected artifact. For the single paired-success build-pmars SWE-agent task, including replay, Causal used 379627 tokens versus 472182 for no rollback (19.60% less) and 298324 for checkpoint (27.25% more). Whole failed verification and is excluded from paired-success comparisons. Codex Causal is still running; no current v6 Codex pair yet, and v5 results remain separate. Work has advanced to build-pov-ray. Disk free is 353.4 GiB, close to the unchanged 350 GiB dispatch guard; no results or images deleted. Evidence: `/mnt/data/agenttx-runtime/validation/tb-progress-review-20261020T0853.json`.

**TB resumed with local Git and aligned SWE tool limits, 2026-10-20 16:34 China time.** r11 started at16:32:45:263 completed,26 retained infrastructure exceptions,8 running,2595 pending; no stop reason or monitor alerts. r10 added11 complete observations:all six Codex Linux-kernel arms passed, including no-rollback cleanup; five SWE-agent arms hit upstream cumulative tool timeout1800s. The prior repair changed only the outer deadline. Actual SWE tool limits now use3600s per command/3601s cumulative under the existing3600s session deadline; internal timeouts return124 and still require official tests and recovery checks to pass. Hash-verified local Git is used only when image Git is absent, without replacing native Git/loaders. Both build-pmars harness preflights and a native-Git image preflight passed;50 host checks and49 container regressions passed (overlapping suites). About12.8GB unused build cache reclaimed;364.7GiB free, images/raw results retained. All289 old terminal observations inherited unchanged; pending work alone resumes. v6 comparisons stay separate from older configurations. The eight live workers' frozen code, context and local Git mounts were verified. See [repair evidence](docs/TB_RUNTIME_PREREQUISITE_REPAIR_20261020.md).

**TB completion-contract and timeout correction, 2026-10-20 15:08 Asia/Shanghai (current user instruction supersedes earlier limits).** Native v5 discloses the identical reserved derived-file cleanup requirement to all six policies and both harnesses, preserves protected independent notes, and keeps cleanup tool/API cost inside the attempt. It exposes no baseline fault source or step. A model deadline (rc124) is successful when official tests and recovery checks pass; model_timed_out remains recorded. Incomplete usage is still excluded from complete-cost comparisons. Model sessions are3600s, verification1800s, worker envelope9600s covering main+replay+verification; eight workers total and no step/call caps. This supersedes900s and the normal-exit success gate. Old raw results are unchanged; two complete old deadline outcomes rescore as successful, while two additional verified outcomes with incomplete usage remain separate exceptions. Changed prompts/budgets have separate comparison groups, with no outcome-based reruns.26host checks,47 original-container regressions and both complete zero-model native preflights passed. r10 activated15:15:12. At15:16:23 it has252 complete,25 retained exceptions,eight live workers,2607 pending,stop_reason=null. All277 old terminal result references/hashes are inherited; actual new launch limits, cleanup contracts and frozen source hashes match. QEMU initramfs/kernel Git transport also reuses the existing verified read-only portable Git; both original-image zero-model preflights passed. See [repair evidence](docs/TB_COMPLETION_TIMEOUT_REPAIR_20261020.md).

**TB failure audit, 2026-10-20 14:54:57 Asia/Shanghai.** Of244 complete observations,150 pass the joint contract and94 fail, while174 pass official tests:22 fail only injected-artifact cleanup (all no-rollback), two pass tests after model deadlines.70 official nonzero outcomes include9 live example.com HTML/fixture drift cases,8 pandas/network bootstrap or collection blockers,2 still-broken task Python/pip cases,one verifier deadline and50 other test failures requiring implementation/version/performance attribution.19 model deadlines andtwo SWE worker exit_error cases overlap those outcomes. The actual native no-rollback prompt hides invalid paths and does not explicitly state the cleanup contract; its joint failure rate is not standard TB resolved and cannot alone establish localization benefit. Original attempts and running configurations remain unchanged; eight workers continue,stop_reason=null. See [failure audit](docs/TB_FAILURE_ANALYSIS_20261020.md).

**TB device-node repair and live resume, 2026-10-20 14:40 Asia/Shanghai.** r8 stopped14:29:06 after244 complete results (SWE-agent127:66 successes/61 task failures; Codex117:84/33) and22 retained exceptions. New exceptions are a Codex model deadline with incomplete final usage and build-initramfs-qemu/Codex/Clean failing to snapshot its model-created device node; the latter consumed355338 logged tokens and remains an infrastructure exception. Snapshot code previously rejected ordinary device nodes and upperdir digests incorrectly treated every character device as a deletion. Layers now recreate real character/block devices and FIFOs without opening them or sharing live inode metadata; whiteouts remain only character0/0. Full, incremental and surgical restoration preserve type, rdev, permissions and ownership. Real transaction creation/next-step/replacement/rollback passed in the original task image.85 related tests passed initially; one test overwrote required native-container read-only mounts, was fixed to append its test mount and passed separately (86 distinct checks). No task implementation or model rerun was supplied. r9 activated14:39:42, inherits all266 terminal rows by unchanged result path/hash, and now has244 complete,22 exceptions,8 live workers,2618 pending,stop_reason=null. Pending work includes the remaining policies on build-initramfs-qemu. Both harnesses share eight workers and all prior contexts/budgets. Paired-success main+replay Causal savings: SWE-agent checkpoint38.88% (11 pairs), whole branch40.68% (10); Codex checkpoint32.34% (13), whole branch−8.92% (15). These remain small partial samples. Evidence: `validation/tb-device-node-repair-20261020-r1/` and `validation/tb-runtime-node-resume-20261020-r9/` on the data disk; source/runtime changes are explicitly registered. See [environment status](docs/TB_ENVIRONMENT_STATUS_20261020.md).

**TB progress and Git transport repair, 2026-10-20 13:44 Asia/Shanghai.** r7 activated13:03:03 with the repaired241-task inventory. Current223 complete observations (SWE-agent115:62 successful/53 task failures; Codex108:77/31),20 retained infrastructure/interruption exceptions,2 active,2647 pending; all2892 cells remain accounted for. This adds51 complete observations since the12:57 snapshot. Two new broken-python Clean starts timed out installing missing Git through domestic APT before model execution, halting new dispatch. Their original attempts remain exceptions and are not retried. Reuse the hash-verified read-only portable Git prerequisite already used for broken-networking, now also for broken-python; do not repair the task's intentionally broken Python/pip or modify task APT/networking. Both enhanced native adapter/transaction preflights passed without model calls,31 relevant regression checks passed. At13:44:05 the persistent r7-to-r8 handoff began; two already-running SWE-agent attempts have actual model activity and continue naturally, then all terminal results are inherited and only unstarted cells resume with eight workers total. The two-worker drain is not a claim that eight workers are currently running. Evidence: `tb-broken-python-audit-20261020-r1`, `tb-runtime-git-audit-20261020-r1`, `tb-environment-continuation-20261020-r8` under the data-disk validation directory. See [environment status](docs/TB_ENVIRONMENT_STATUS_20261020.md).

**Direct new dispatch and runtime correction, 2026-10-20 12:57 Asia/Shanghai.** r5 started12:49:09 with all241 tasks but accelerate-maximal-square's SWE-agent adapter import exposed tiktoken0.14.0 requiring GLIBC2.28 on the original GLIBC2.27 image. The earlier zero-model check exercised only transactions, so the12:45 environment claim did not cover this runtime import. Preserve four failed SWE imports and four Codex attempts deliberately interrupted under the user's direct-new-dispatch instruction, including partial usage; do not retry them. r6 immediately resumed other tasks at12:52 with eight workers total and the four unstarted accelerate cells excluded temporarily; new tasks have produced two additional complete observations (172 complete,18 retained exceptions at12:56). Same-version tiktoken0.14.0 has now been rebuilt from its hash-verified original sdist inside the original image, with a pinned Rust toolchain and recorded Cargo.lock. Only the verified worker extension is mounted read-only for this exact task/image; task libc/Python, global venv and task answers remain unchanged. 1008 token-sequence parity cases across cl100k_base/o200k_base and actual SWE-agent import passed. Both enhanced native preflights now construct their real adapters without model calls.63 regression checks passed. The [r6a inventory](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r6a.json) again verifies241 ready (229 official-test executions+12 audits). A persistent r6-to-r7 handoff is incorporating this fix while active new workers finish; it will inherit every terminal result and dispatch only unstarted cells. See [runtime evidence](docs/TB_ENVIRONMENT_STATUS_20261020.md). This correction supersedes the earlier transaction-only readiness claim.

**TB environment unblocked, 2026-10-20 12:45 Asia/Shanghai.** All 241/241 main images exist and all 241 tasks are dispatch-ready in the immutable [r4f inventory](/mnt/data/agenttx-runtime/validation/tb-usability-inventory-20261020-r4f.json): 229 executed official tests; 12 individually audited expected unsolved baselines passed both native zero-model transaction preflights. This is environment readiness, not 241 solved tasks or a six-policy completion claim. Windows3.11's original disk and XP's original ISO passed their fixed SHA256 checks. LC0's original weights, CPU wheels, exact signed upstream source commit/tree and original Meson package hashes are verified locally; a real Ninja wrapper fixes Meson's command discovery. Its official reference ONNX was generated, checked and loaded by ONNX Runtime; all original pinned runtime versions match. The originally unpinned verifier tool is explicitly frozen to commit `69fbfc6567a19da09793a4418dc2c533d30bd064`, not silently called unchanged. LC0's missing task-required lc0utils.py is audited only after those prerequisites pass; no answer module is supplied. All 62 related regression checks passed. At12:44:47 the persistent r4-to-r5 continuation paused only the old dispatcher and began draining eight active workers. It will inherit every terminal result, generate the final 241-task frozen inventory and resume only unstarted cells with eight workers total; the handoff is not yet complete at this snapshot. Original failed attempts and real usage remain. Source is local; images/assets/cache/logs/results remain on /mnt/data. See [repair evidence](docs/TB_ENVIRONMENT_STATUS_20261020.md) and [handoff status](/mnt/data/agenttx-runtime/validation/tb-environment-continuation-20261020-r5/drain-progress.json).

**TB environment repair, 2026-10-20 12:16 Asia/Shanghai.** Preparation now verifies 238/241 tasks: 228 executed official tests and ten individually audited expected missing-output baselines passed both native zero-model transaction preflights. C4 uses the hash-verified original heldout shard through a verifier-only local transport replacement; its assertions remain unchanged and both test hashes are registered. Spark completed all 12 original performance runs and failed only on the task-required absent metrics.json (582 seconds total). QEMU's signed archive packages/distutils and Flink's exact commit/tree source transport are repaired. accelerate-maximal-square preserves Python3.6/numpy1.19.5; its verifier alone uses pytest6.2.5 because upstream pytest8.4.1 cannot run on Python3.6. This explicit compatibility exception is not an unchanged dependency pin. Missing /app is the output directory the task asks the agent to create; no implementation is supplied. Original LC0 weights are recovered and their decompressed SHA256 matches the requested network; Windows3.11 input is recovered from its original image download layer with the registered SHA256. Both images are rebuilding; XP's exact ISO is downloading with range verification and final SHA256 required. Original failed attempts remain. The r3-to-r4 model handoff completed at12:06:12 without interrupting workers or changing raw results; all130 terminal rows (126 complete, four retained exceptions) are inherited. r4 freezes a 236-task inventory (227 executed tests+nine audits), eight shared model workers,60 blocked cells; newer readiness awaits an audited pending-only continuation. 53 related checks passed before the additional range-download change. See [environment evidence](docs/TB_ENVIRONMENT_STATUS_20261020.md).

**TB environment repair, 2026-10-20 11:43 Asia/Shanghai.** 231/241 original task images now exist; 220 tasks have executed the official verifier bootstrap/tests (24 additions since the 196-task inventory). All 12 sidecar tasks now run official tests with fresh per-attempt services and original data volumes. The dedicated Docker daemon disables automatic iptables/NAT: scoped, removable rules for each owned bridge restore task DNS/service access, domestic PyPI and model-endpoint TCP without restarting Docker or changing global firewall policy. Both harnesses passed zero-model transaction preflights for simple-web-scraper and postgres-csv-clean. Restored fix-git Git history from a public original-task image; setup/resources byte-match the pinned task, git fsck and required ancestry pass, then rebuilt and ran official tests. Neuron/MNIST dependency transport retries now reach tests with original version pins. Five task-required missing-output cases are being audited separately, never pre-solved. QEMU archive transport, Flink clone EOF, remaining model/data assets, Windows archive reachability, deleted LC0 weight and the Python-3.6/pytest-8.4.1 incompatibility remain under repair. 45 relevant checks passed. Active model r3 remains frozen to its registered 196-task inventory until an audited pending-only continuation; new readiness is not silently admitted. Logs/cache/assets stay on /mnt/data; source stays local. See [environment evidence](docs/TB_ENVIRONMENT_STATUS_20261020.md).

**TB environment and queue update, 2026-10-20 11:05 Asia/Shanghai.** Environment readiness increased from 192 to 196/241 tasks: financial-document-processor, fix-ocaml-gc, shell-deobfuscation and sudo-llvm-ir now execute official tests in disposable unsolved images. Pinned uv 0.8.14 is hash-verified; trusted verifier root permissions are fixed without changing agent users. 45 tasks remain blocked (540 cells); 224 images are built, 17 still fail builds. r2 drained at10:24:28 after a Codex model deadline lacked final CLI usage. Its session records establish a 4,659,881-token lower bound, not a complete bill; original results remain unchanged. Recognized model deadlines with missing usage retain infrastructure-exception status and are excluded from complete-cost comparisons, but now allow pending work to continue; other infrastructure/context/usage failures still halt dispatch. r3 started11:04:48, inherits all60 immutable terminal rows without retry:57 complete,3 exceptions,8 running,2284 pending,540 blocked, no stop reason at11:05:03. Combined model concurrency remains8.34 tests passed. See [current environment and continuation](docs/TB_ENVIRONMENT_STATUS_20261020.md); this update supersedes earlier blanket missing-usage stop rules for the narrow recognized-deadline case.

**TB interruption fixed and pending work resumed, 2026-10-20 09:38 Asia/Shanghai.** r1 stopped the previous evening at 19:16:43 with 27 complete observations (17 successes, 10 task failures) and two infrastructure exceptions. Final publication rejected container-local pip/system writes and an external uv cache path. v4 grades the original task container's private agent transaction view directly, retaining default commit protections; recovery artifact checks use that same view. 32 tests and a zero-model positive/negative check in the original csv-to-parquet image passed. `tb-dual-native-full-20261020-r2` inherits all 29 immutable terminal observations and dispatches only unstarted cells, with no outcome-based retry. At09:38:57: 27 complete, two retained exceptions, eight running,2267 pending,588 environment-blocked; stop_reason=null. The eight workers are shared by both harnesses. Image r3 finished with five additions (224/241 built) and17 failures;192 tasks are verified runnable,49 remain environment-blocked. See [repair and continuation](docs/TB_RESUME_20261020.md). Original source hashes and validation-boundary change are registered explicitly.

**SWE figure completed, 2026-10-19 19:23 Asia/Shanghai.** `paper/img/FIG-SWEagent-SWEbench-Results.pdf` and PNG contain the final six-policy outcomes, mean API main+replay cost, matched savings/intervals, and task-level cost distributions. They preserve the historical SWE plot's Nimbus Roman typeface, 17.8 cm width, boxed top legend, and original four policy color/line/marker encodings. `motivation/plot_sweagent_final.py` reproduces the figure from audited data. Paper source and `main.pdf` are updated; compilation succeeded, the figure is on PDF page 10, and fonts/data were verified. Build logs, prior PDF, and hash proof are under `/mnt/data/agenttx-runtime/validation/swe-paper-update-20261019`.

**SWE final data update and user direction, 2026-10-19.** Preserve the user's confirmed interpretation of the causal recovery benefit and update measured results without reopening the wording/attribution discussion. The current SWE-agent/SWE-bench final table is [here](docs/SWE_FINAL_RESULTS_20261019.md): 1799 complete results plus one Clean worker exception; Causal 285/300 successes. Actual API main+replay savings on matched successful tasks are 58.43% versus no rollback (275 pairs), 17.55% versus checkpoints (278), and 27.86% versus whole-branch rollback (239). Paper RQ2 now includes all six policies, complete-run token means/replay, matched savings/medians, and sensitivity checks; the obsolete five-policy/still-running sentence is replaced. This instruction takes precedence over the previous audit's proposed editorial changes; original measurements and audit evidence are retained.

**SWE paper consistency audit, 2026-10-19 19:10 Asia/Shanghai.** The SWE cohort has 1799 complete results plus one worker exception. Causal's 285/300 is recovery-contract success with FAIL_TO_PASS verification, **not standard SWE-bench resolved rate**; full PASS_TO_PASS/grader validation is missing. Success-paired API main+replay savings versus no rollback/checkpoint/whole branch are 58.43%/17.55%/27.86%, but these measure the registered policy/context combination. Injection selects a file from the gold solution patch, and Causal/Ideal disclose that file path in the recovery prompt (confirmed in actual trajectories); Clean lacks the same hint. This is solution-path metadata, not proof of applying gold patch contents. Do not claim pure rollback attribution, a matched Clean upper bound, or superiority over Ideal. The paper's RQ2 is empty and its five-policy/still-running description is outdated. See [Chinese audit and evidence](docs/SWE_PAPER_CONSISTENCY_20261019.md). Original results and ongoing campaigns remain in place.

**Fresh dual-harness TB suite launched, 2026-10-19 18:54 Asia/Shanghai.** `tb-dual-native-full-20261019-r1` started at18:51:55 after all12 live infrastructure/context/usage canaries completed (10 task successes, two no-rollback task failures; no success-based reruns). Full scope is2892 cells; current counts are8 completed (six successes, two task failures),8 running,2288 pending,588 blocked by49 unready tasks. All eight workers are alive, with no stop reason or monitor alert. The192 previously checked tasks supply2304 initially runnable cells; canary outcomes and old TB outcomes do not fill coverage. Fresh source is frozen under `/home/bfq/agenttx-executions/tb-dual-native-full-20261019-r1/project`; both agents and verifier run in each task's original container. Actual API usage is complete for all eight finished cells, including separate replay sessions.34 relevant tests passed. A separate two-worker domestic image queue has built three additional images (financial-document-processor, fix-ocaml-gc, implement-eigenvectors-from-eigenvalues-research-paper), bringing the built-image count to222/241; build completion does not unblock model cells without environment checks. Health monitoring runs every30s and saves half-hour snapshots. See [launch record](docs/TB_DUAL_RERUN_20261019.md). Formal eligibility remains false.

**TB rerun authorization, 2026-10-19.** The user requires fresh Terminal-Bench runs for **both SWE-agent and Codex**. Register all 241 tasks × six policies × two harnesses = **2892 cells**, one repeat, with **eight model workers total** across the two harnesses. Keep old TB attempts as diagnostic history; do not inherit their model outcomes. Both agents and the official verifier must execute in the same task-image container, preserving its working directory, task interpreter and task user. Causal/Ideal/Clean use compact; no rollback/checkpoint/whole branch use full. Both harnesses use gpt-5.6-luna/max, seed 20261019, 900-second model sessions, 900-second verification and no step/call cap. Record main and independent-document replay usage separately and include both in costs. Failed tasks are completed observations, not infrastructure failures; no outcome-based retries. The infrastructure canary checks actual contexts/runtime/API usage, with no task-success gate. Run ready tasks while separately retaining unready tasks in the full denominator. All work is local; frozen code stays on the system disk and runtime data stays on /mnt/data. Missing Git is installed as an agent prerequisite before creating the overlay, through domestic APT; original task outputs are not pre-solved. Buildx discovery was repaired in the dedicated data-disk Docker CLI directory. The initial zero-model Git failures and zero-build plugin failures are retained. New live checks and missing-image preparation are active; this is not yet a claim that the full cohort has launched or formal eligibility has passed.

**Cohort completion audit, 2026-10-19 16:39 Asia/Shanghai.** The six-policy SWE-agent queue finished at 16:15:41: 1799 completed results (1656 successes, 143 task failures), one retained infrastructure error, zero running/pending cells. Success counts: causal 285/300, ideal 289/300, no rollback 282/300, checkpoint 284/300, whole branch 243/300, clean 273/299 completed plus one missing-result worker timeout. No failed model attempt was retried. The 16:38:56 API-log audit fully covers all 1799 completed results, including main and replay. Paired-success causal savings: no rollback 58.43% (275 pairs), checkpoint 17.55% (278), whole branch 27.86% (239). Known logged usage across all 1800 terminal attempts is 317,864,998 tokens, including 1,958,229 from the incomplete clean attempt; its in-flight request usage remains unknown. These are API-log reconstructions, not a provider invoice. The supervisor remains alive, with no new alerts. Existing protocol gaps and the session-timer deviation remain; formal_eligible=false. TB is unchanged: 219/241 main images, 192 tasks reached official installer/test execution, 12/12 sidecars passed startup/connectivity, no active TB preparation/validation/model workers and no six-policy model cohort. See [API audit](/mnt/data/agenttx-runtime/validation/swe-api-usage-sweagent-swe-local-monitored-20261018-selective-auto-20261019T064657Z.json).

**Progress check, 2026-10-19 15:23 Asia/Shanghai.** SWE {"completed": 1680, "infrastructure_error": 1, "running": 8, "pending": 111}, 1541 successes and 139 task failures, all eight workers alive, no new alerts. The single truncated worker attempt remains visible and is not retried. Latest logged API audit fully covers 1679 completed results; the separate missing-result attempt retains1,958,229 known tokens with unknown in-flight usage. Paired-success causal savings including replay: no rollback 58.00%, checkpoint 16.79%, whole branch 27.75%. TB environment batches have ended; no TB preparation, validation or model process is running. Official installer/test execution remains192 tasks, sidecar connectivity12/12, main images219/241; six-policy model validation has not started.

**Interruption audit and progress, 2026-10-19 14:48 Asia/Shanghai.** SWE dispatch drained at13:10 after sympy-24102/clean reached its registered3600s worker cap (exit124, no final result). All67 responses and1,958,229 logged API tokens are retained; in-flight usage is unknown. The cell stays infrastructure_error and is never retried or assigned an invented task verdict. Narrow wall-time/exit-code/trajectory/response-count auditing now permits pending-only continuation. A new cohort resumed eight workers at14:46. Current {"completed": 1605, "infrastructure_error": 1, "running": 8, "pending": 186}, with 1471 successful and 134 failed completed results. All60 related checks passed; model core remains frozen r9 and the known900s session-timer deviation remains. TB all156 additional checks ended:154 reached official tests, bringing official-flow execution to192 tasks (190baseline+2positive/negative). Broken Python/networking tasks retain their intended initial faults. Sidecars12/12, main images219/241; no current TB environment workers or full model cohort. See [interruption audit](docs/SWE_WORKER_TIMEOUT_20261019.md) and [TB inventory](docs/TB_USABLE_TASKS_20261019.md).

**Progress and next validation batch, 2026-10-19 11:26 Asia/Shanghai.** SWE {"completed": 1490, "running": 8, "pending": 302}, 1374 successes and 116 failures, no supervisor alerts; latest logged API audit covers 1487/1487 cells. Paired-success causal savings including replay: no rollback 58.06%, checkpoint 17.22%, whole branch 26.77%; checkpoint advantage has narrowed. These remain diagnostic. Prior TB repair batches ended: 38 tasks reached official installer/test execution, 12/12 sidecars passed startup/connectivity, 219/241 main images exist; no full TB model run. Started `tb-basic-official-validation-20261019-r1` for the first official bootstrap/test execution on 156 basic-probe tasks: two workers, 900-second environment diagnostic cap, stop new dispatch below 350 GiB free (currently about621 GiB). Two real containers confirmed running. No model calls/gold; prior results preserved.

**TB usability inventory, 2026-10-19T10:28:35.721179+08:00.** 38 distinct tasks reached actual official installer/test execution (two positive/negative checks, 36 baseline checks). Another 156 have basic probes only, four have repaired transaction-adapter checks only, and 12 have sidecar startup/connectivity only. Nine baseline tasks remain unresolved; 22 main images remain unbuilt. These disjoint groups total 241; 219 images exist. No complete six-policy TB model validation has run. Spring base image is built; root systemd Compose discovery is repaired using a dedicated data-disk Docker CLI plugin directory and the new r5 check passed; all 12 sidecars passed startup/connectivity. See [task inventory](docs/TB_USABLE_TASKS_20261019.md).

**Environment repairs and progress, 2026-10-19 10:09 Asia/Shanghai.** SWE: {"completed": 1359, "running": 8, "pending": 433}, no supervisor alerts. All 45 first-pass TB scripts ended; this does not mean all are ready. MobileSAM r5 verified all 275 Git blobs at the pinned commit, installed dependencies and ran official tests (nine unsolved assertions, zero test errors). Postgres repair passed, bringing distinct sidecar startup/connectivity passes to 11/12. Spring base-image pull exceeded the former combined build cap; an independent r4 service now pre-pulls with byte-progress deadlines before building. Confirmed UV_DEFAULT_INDEX overrode explicit --index-url; removed that conflicting variable, retaining domestic UV_INDEX_URL and upstream explicit CPU indexes. Four audited installer timeouts are now being rechecked in r6; three other timeouts already reached tests and are kept separate. No model outcomes retried. All 33 related checks passed, including actual local HTTP index precedence. Local execution only; prior failures and logs remain; no TB model run has started.

**Local repair queue checked, 2026-10-19 09:39 Asia/Shanghai.** TB original-script diagnostics: 41/45 complete, four still running. Ten of 12 distinct sidecar tasks passed startup/client connectivity (eight original, plus repaired Celery and MinIO); Postgres 15 is being pulled with byte-progress deadlines and Spring is building. MobileSAM proxy Git fetch ended with early EOF before tests; an independent r5 service now downloads the exact pinned source archive, verifies every Git blob against GitHub API metadata, and only then installs locally, preserving dependency pins and tests. r5 remains unfinished. All 17 related checks passed. SWE: {"completed": 1310, "running": 8, "pending": 482}, alerts []. Original failures and usage remain preserved; no full TB model run has started. See [repair evidence](docs/TB_ENVIRONMENT_REPAIR_20261019.md).

**Local environment repairs, 2026-10-19 09:20 Asia/Shanghai.** All four previously failing TB transaction adapters now have passing real-container evidence; non-root tasks retain their original uid/gid/HOME through tracing, rollback, commit and session reload. Fixed overlay/snapshot ownership loss, old unshare/glibc compatibility, OpenCV shared libraries and version-matched domestic verifier bootstrap. The 45-script batch has completed 33/45; seven sidecar tasks passed startup/client connectivity checks. Remaining installs and sidecars are still running, HF wheel and Celery mirror repairs have passed, while remaining scripts/services and pinned MobileSAM transport checks continue. 132 runtime/monitor tests, 28 bootstrap-related checks and seven pull-progress tests passed (overlapping suites). SWE's shared test-patch initialization race was fixed in the frozen r9 coordinator; only two audited zero-call infrastructure failures were repeated, both now successful. SWE snapshot: {"completed": 1264, "running": 8, "pending": 528}, alerts ["workers_exited_awaiting_coordinator_reconciliation"]. No TB model calls or gold solutions; this is not full TB verifier/model readiness. See [repair evidence](docs/TB_ENVIRONMENT_REPAIR_20261019.md).


**Built TB image validation completed, 2026-10-18 22:29 Asia/Shanghai.** All 219 images received first-pass diagnostics and targeted follow-up; 215 passed actual image-native transaction isolation/rollback. Two public-instruction negative/positive fixtures passed the actual official bootstrap and tests (hello-world, csv-to-parquet); 156 additional basic transaction/test probes ran, 45 need test bootstrap or special-script handling, 12 require sidecars, and four have transaction adapter incompatibilities. Basic probes are not full verifier validation. No model calls or gold solutions were used; raw failures remain immutable. All 14 related tests passed. See [TB validation](docs/TB_READY_VALIDATION_20261018.md).

**TB local environment recheck, 2026-10-18 21:57 Asia/Shanghai.** Of 241 expected task image tags, 219 match recorded local image identities (90.9%); 22 have no local image. These comprise 216 initial builds plus bn-fit-modify, build-stp and slurm-simple-node-monitoring repairs. No additional image progress occurred, and no preparation/repair process is currently running. Remaining blockers include GitHub/Hugging Face assets, package downloads and four 3600-second build timeouts. Image identity is not complete per-task container-agent and official-verifier readiness; the previously observed host-agent/image-verifier execution mismatch remains unresolved. No current TB full model cohort has launched. See [TB environment status](docs/TB_ENVIRONMENT_STATUS_20261018.md).

**Progress check, 2026-10-18 21:55 Asia/Shanghai.** 544/1800 cells completed: 498 successes, 46 failures; eight running, 1248 pending, no stop reason or supervisor alerts. Since 21:21: +74 outcomes (70 successful, 4 failed), with no new continuation. API audit at 21:54 covers all 541 outcomes then available. Paired-success aggregate causal token savings including replay: 56.06% vs no rollback (84 pairs), 18.56% vs temporal (82), 24.48% vs whole branch (71). At that audit causal succeeded in 86/90 cells. The advantages are broadly consistent with the previous snapshot; this remains diagnostic and does not resolve the registered timing deviation.

**Progress check, 2026-10-18 21:21 Asia/Shanghai.** 470/1800 cells completed: 428 successes, 42 failures; eight workers active, 1322 pending, no stop reason or supervisor alerts. Since 20:01, 158 additional outcomes were recorded, including 152 successes and six failures; no new continuation was needed. The API audit at 21:21 covered all 468 outcomes available at that snapshot; two more cells finished afterward. Paired-success aggregate causal token savings, including main and replay: 57.34% vs no rollback (72 pairs), 19.55% vs temporal (70), 24.51% vs whole branch (61). At the API-audit snapshot causal had 74/78 successes; these are interim diagnostic results and the recorded timing deviation remains unresolved.

**Verified progress, 2026-10-18 20:01 Asia/Shanghai.** 312/1800 cells completed: 276 successes, 36 failures; eight workers active, 1480 pending, no supervisor alerts. Two new cells succeeded after continuation. API-log coverage 312/312; paired-success aggregate token savings including replay are 50.76% vs no rollback (46 pairs), 13.10% vs temporal (47), 20.19% vs whole branch (40).

**Registered session-timeout classification repaired, 2026-10-18 20:00 Asia/Shanghai.** The prior queue stopped dispatch at 19:24 after `sphinx-doc__sphinx-7686/clean_recovery` hit the registered 900-second wall limit (actual main session 900.351 seconds). SWE-agent caught the typed timeout and returned generic `exit_error`; monitoring misclassified it as integration failure. Auditing now requires the typed traceback, terminal structured trajectory, matching main call/token counts, registered timing, correct local image, valid final verifier and unchanged failed outcome. Other infrastructure signatures still block dispatch. All 48 relevant tests passed. Frozen r7 changes only orchestration; model core and protocol remain unchanged. At 20:00 the supervisor launched `sweagent-swe-local-monitored-20261018-selective-auto-20261018T120026Z` with all 310 outcomes preserved by reference: 274 successes, 36 failures including this timeout, eight running, 1482 pending. No completed task was retried and all costs remain. Logged API usage is complete for 310/310 cells; successful-pair aggregate causal savings including replay: no rollback 50.44% (45 pairs), temporal 13.00% (46), whole branch 20.08% (39). These remain interim diagnostic results; the previously recorded 3144-second deadline deviation is unresolved. Evidence: `/mnt/data/agenttx-runtime/validation/swe-registered-session-timeout-audit-20261018.json`.

**Source-quotation false alert repaired, 2026-10-18 19:09 Asia/Shanghai.** Dispatch stopped at 17:16 and drained at 17:52 after pytest-5221 read verifier source containing the exception-message literal. Persistent monitoring retained the alert but could not repair this previously unknown class. Tool-observation auditing now distinguishes quoted source from actual runtime errors; mixed genuine errors still block dispatch. All 42 relevant checks passed. At 19:08 the supervisor automatically launched `selective-auto-20261018T110835Z`, referencing all 276 outcomes: 244 successes, 32 task failures including two session timeouts, eight running, 1516 pending, no alerts. Raw outcomes/costs are unchanged and no task was rerun. Only monitoring classification changed. API-log coverage is 276/276; successful-pair savings are 51.42% vs no rollback (40 pairs), 14.08% vs checkpoint (41), and 24.33% vs whole branch (36). The clean pytest-5221 main session actually took 3144 seconds despite its registered 900-second limit; preserve actual usage/time and record this protocol deviation rather than claiming strict equal budgets.

**Persistent supervision and continuation, 2026-10-18 15:24 Asia/Shanghai.** The user requests continuous monitoring and continuation after environment repairs. Active `selective-full-r3`: 132 completed, 121 successes, 11 task failures, eight running, 1660 pending, no stop reason; all eight workers alive and logged API usage complete for 132/132 cells. Both pylint alerts in r2 were agent-authored image-digest substitutions; correct images exist and final verification passed. All 129 prior outcomes and costs were preserved by reference without reruns. Argument-error classification now checks actual tool episodes for altered image arguments; real infrastructure errors remain distinct. All 38 relevant tests passed. Enabled `agenttx-swe-supervisor.service`: checks queue/workers/dedicated Docker/disk every 30 seconds, API usage every five minutes, maintains reports and half-hour reviews, and automatically continues pending cells after audited model-argument misclassifications. Unclassified environment failures retain diagnosis evidence and require a verified repair before continuation; task outcomes never trigger retries. See [persistent monitoring](docs/SWE_PERSISTENT_MONITOR_20261018.md).

**Difference audit and continuation, 2026-10-18 15:01 Asia/Shanghai.** Active `selective-full-r2` references all 104 terminal r1 outcomes and continues with eight workers: 112 completed, 104 successes, eight task failures, eight running, 1680 pending, no stop reason. The 14:25 stop was an agent-authored 65th image-digest character, independently verified against actual tool episodes and the correct local image. Preserve original outcomes and costs; no outcome was rerun. Genuine infrastructure errors still stop dispatch. All 24 scheduler checks passed. **Token accounting correction:** SWE-agent model_stats estimates visible text and does not fully include API reasoning, even after replay is added. The offline API log audit matches response counts for 112/112 cells. API-log paired savings are 57.36% vs no rollback (18 pairs), 1.44% vs checkpoint (18), and 28.70% vs whole branch (15). Keep these separate from historical text estimates. See [analysis](docs/HARNESS_TB_DIFFERENCE_ANALYSIS_20261018.md).

**Full-suite progress snapshot, 2026-10-18 14:05 Beijing.** 65 cells finished, 62 successful; eight workers remain active with no stop reason. Completed contexts match the registered three compact/three full profile and no infrastructure errors are recorded. Task failures remain visible and the queue continues. Costs use SWE-agent recorded main + replay; comparisons use same-task paired successes and small samples do not establish a stable ordering. Snapshot: `/mnt/data/agenttx-runtime/validation/swe-selective-full-progress-20261018.json`.

**User correction: withdraw the assistant-imposed success gate; full suite launched, 2026-10-18 13:35 Beijing.** Requiring all six arms to solve the task was not a user requirement and is withdrawn from code and this specification. Infrastructure, real usage and registered contexts determine launch validity; task failures remain results and neither block expansion nor trigger retries until success. Existing six-arm environment evidence directly launches `sweagent-swe-local-monitored-20261018-selective-full-r1`: 300 tasks × six policies = 1800 cells, with eight running and 1792 pending, no stop reason, and no inherited canary outcomes. Causal/ideal/clean use compact; all three comparison controls use full. There is no serial ideal launch gate. All 32 relevant orchestration/context regressions passed. Historical statements requiring six task successes record a withdrawn assistant-added restriction and must not be applied.

**Six-arm canary ended; full suite has not started, 2026-10-18 13:16 Beijing.** All six actual contexts match registration (three compact, three full), with zero infrastructure errors and five task successes. Whole-branch abort submitted recovery notes without executing the source fix; official tests report six passes and one failure. Preserve this valid task failure without an outcome-based retry. The launcher stopped at 13:05 because the all-six-success gate was not met; no model workers are currently running. Main+replay tokens: ideal 32,358; causal 42,997; clean 54,492; temporal 54,880; no rollback 92,603; failed whole branch 21,991. Combined spend is 299,321. This is one task, not full-suite efficacy evidence. Review: `/mnt/data/agenttx-runtime/validation/swe-selective-canary-review-20261018.json`. Parser audit at 13:32 Beijing reproduced the rejection of the multi-block reply, followed by a valid `submit` response; no parser defect is demonstrated. No new model attempts or full-suite launch have occurred.

**Current experiment requirements (user-corrected 2026-10-18):** the six-arm context table in Section 16.2 and the execution rules in Section 16.4 are authoritative and override conflicting historical launch notes below. Causal, ideal, and clean use compact; the three comparison controls use full. The old monitored campaign has been stopped and its runtime results deleted; the new campaign starts from zero.

**User-requested reset and fresh launch, 2026-10-18 12:58 Beijing.** Stopped all old workers and removed seven old monitored cohorts, their results/trajectories/logs, the active old workspace/session and three orphan evaluator containers. No old cohort process remains; old experiment data was deleted without an archive. Pinned images, dependency caches and no-model environment proofs remain. Fresh six-arm canary `sweagent-swe-local-monitored-20261018-selective-canary-r1` is now running, starting with clean. Causal/ideal/clean use compact; no rollback/temporal/whole branch use full. Zero old outcomes are inherited. Infrastructure-valid attempts permit the separate 1800-cell full campaign with at most eight workers, regardless of task success. Earlier result figures below are retired historical notes, excluded from current statistics. Existing formal protocol gaps remain registered (`formal_eligible=false`). 54 relevant checks passed. Cleanup evidence: `/mnt/data/agenttx-runtime/validation/swe-user-requested-reset-20261018.json`.

**User-specified context profile, 2026-10-18 (supersedes the all-full protocol below).** `causal`, `ideal_oracle`, and `clean_recovery` use **compact**; `crash_no_rollback`, `temporal_checkpoint`, and `whole_branch_abort` use **full**. The previous r5 is actually all-full, as verified from registration and result files; its percentages are historical full-context diagnostics. The new `selective-compact-v1` profile is implemented with worker-local settings, actual-result checks, a six-arm canary including ideal, and comparisons against the registered per-policy contexts. It measures the combined recovery-policy and context treatment. The local launcher waits for r5 workers to drain, runs a fresh six-arm canary, and launches a separate 300 × 6 cohort after environment/context validation, without conditioning on task success. No previous full-context causal/ideal/clean result fills this profile. SymPy verification now selects all published FAIL_TO_PASS names within the patched test files, with file arguments before `-k`; no-model validation confirms pristine source fails and the existing model patch passes in about four seconds each. Previous timeout outcomes and costs remain recorded. Launch status: `/mnt/data/agenttx-runtime/validation/swe-selective-launch-20261018.json`. Earlier dated notes remain historical.

**Earlier waiting stage (retired by the user-requested reset above).**

**Expectation audit and large-command repair, 2026-10-18.** Ideal passed all 15 completed tasks: exact truth rollback in 15/15, no localization, valid independent work retained. On 14 tasks successful in clean/ideal/causal, mean main+replay tokens are 86,547 / 84,082 / 85,296; clean is not the aggregate minimum, so the intended total-cost ordering is not established. Median per-task cost is 10.9% above clean for ideal and 9.1% above clean for causal; independent task solving and prompt differences remain confounds. Causal saves 57.3% / 17.4% / 14.8% against no rollback / temporal / whole-branch on paired successes; versus ideal it is 0.9% lower in aggregate but 6.7% higher at the median task ratio (15 pairs). Do not report a clean/ideal upper-bound violation or stable ranking. r4 stopped on four zero-token matplotlib-22711 injection errors: a 143 KB write exceeded Linux exec argument limits. Unique private script files fix large commands without stale overlay reuse. The no-model real-transaction proof validates fault injection, exact rollback, restored original source and retained independent work. 137 regressions passed; the updated coordinator checks passed 22 tests. Frozen r5 now runs eight workers, references 91 valid terminal outcomes, and repeats only the four audited pre-model failures; six arms remain full. Evidence: `/mnt/data/agenttx-runtime/validation/swe-expectation-check-20261018.json`.

**Clean upper reference and ideal supplement, 2026-10-18.** Clean is the optimization upper reference: no fault and no fault-induced recovery cost. Ideal knows the faulty steps and true invalid descendants before recovery, uses exact rollback without localization, and still solves the task with the live model (no gold solution). Do not interpret independently sampled small-sample token totals below clean as exceeding this reference. Causal and all existing arms actually use `full`. User-authorized frozen r4 adds 300 full-context ideal cells, making 1800 cells across six arms, preserves every existing terminal result by its original path, and never mixes full with compact. r2 has drained; r4 is now active, inheriting 72 terminal outcomes. The first ideal cell is running alone to verify infrastructure, followed by at most eight total workers. The original compact canary is preserved; a separately hashed proof verifies the five already-successful full-context canary cells. Regression checks: 119 passed in the root runtime; three failures caused by missing `python` on root PATH were corrected in test invocation and all three plus the oracle exact-rollback check passed (4/4). Source execution core is unchanged; added ideal orchestration and reporting remain diagnostic, formal_eligible=false.

**Protocol correction, 2026-10-18.** The active r1/r2 results use `full` recovery context; the registration incorrectly stated `compact` because the launcher omitted `AGENTTX_RECOVERY_CONTEXT`. The five arms are clean, no rollback, temporal checkpoint, whole-branch abort, and causal; no `ideal_oracle` arm is scheduled. Earlier cost figures are full-context diagnostics. At the user’s request, continue this same full protocol. After the already-signaled r2 workers drain, frozen r3 resumes pending cells and references every terminal outcome, including failures and inherited paths, with no outcome-based repeats. The r3 registration reports the actual context. Compact and ideal comparisons require a separately registered matched cohort.

**Full-suite review and repair continuation, 2026-10-18 10:41 Asia/Shanghai.** Raw r1 counts: `{'completed': 45, 'running': 1, 'pending': 1454}`. All five `psf__requests-2148` cells are independently diagnosed as environment-invalid: the isolated verifier cannot resolve the test’s off-host redirect origin. Raw results are preserved. New launches stopped while in-flight work drains. The repaired offline origin passed no-model checks: pristine source fails only the issue test, the prior model patch passes 10/10, and the original canary patch still passes 7/7. All 49 relevant tests passed. Frozen r2 is queued to continue with eight workers after r1 drains, referencing all valid terminal outcomes including failures; only the environment-invalid task is repeated across all five policies. TB is now 219/241 ready, with 22 unresolved. [Within-cohort controls and historical diagnostics](docs/SWE_FULL_COMPARISON_20261018.md) now include document-replay tokens. Causal uses 31.2% fewer tokens than temporal checkpoints (eight paired successes), 30.3% fewer than whole-branch abort (seven), and 56.4% fewer than no rollback (eight). Both arms succeed within these recovery-control pairs; this supports a preliminary cost advantage, not a success-rate or statistically established full-suite advantage.

**SWE-agent full suite launched, 2026-10-18 10:12 Asia/Shanghai.** After user authorization, frozen cohort `sweagent-swe-local-monitored-20261018-full-r1` schedules all 1500 cells (300 tasks × five policies) with eight workers. All 55 relevant regression tests passed; the five-policy live canary evidence and unchanged SWE execution core were verified. Current snapshot: `{'completed': 4, 'running': 8, 'pending': 1488}`, stop reason `None`. Validation results do not fill this new cohort and outcomes are never automatically retried. [Historical paired comparisons](docs/SWE_FULL_COMPARISON_20261018.md) refresh every minute; [half-hour reviews](docs/SWE_HALFHOUR_REVIEW_20261018.md) run at each hour and half-hour, survive logout, and write local reports without chat notifications. TB finished its first queue normally at 09:19: 218/241 unique images are ready including two independent repairs, with 23 unresolved. Stage-aware image rewriting passed regression checks and a separate Slurm rebuild is active. The new validated configuration uses 900 seconds/session and seed 20261018, versus historical 1800 seconds and seed 20260930. Comparisons remain diagnostic. Hard token budgets, payload matching and cross-harness checks remain pending, so `formal_eligible=false` and this is separate from final paper coverage. All execution is local; older updates below are retained chronologically.

**All live launch checks passed, 2026-10-18 01:31 Asia/Shanghai.** Frozen local cohort r3 completed all five policies successfully: clean, no rollback, temporal checkpoint, whole-branch abort, and causal. Every cell committed and passed the official verifier (5/5), with zero environment errors or host write leaks. All 87 relevant tests passed. Both known TB download failures were independently repaired; 53/241 unique task images are ready and 0 preparation errors remain unresolved. TB and background monitoring continue. The five-cell launch validation ended normally at its registered cap; shared hard token budget, matched prompt accounting and cross-harness checks remain formal full-suite gates. This is not formal coverage. Evidence: `/mnt/data/agenttx-runtime/validation/swe-local-launch-success-20261018.json`; see [repair report](docs/LOCAL_ENVIRONMENT_REPAIR_20261018.md) and [monitoring](docs/LOCAL_CAMPAIGNS_20261018.md).

**Environment repairs and restart, 2026-10-18 01:25 Asia/Shanghai.** Fixed data-disk workspace visibility, the sandbox Docker socket, verifier metadata visibility, offline httpbin service, stale OverlayFS indexes after recovery, and verifier deletion of the running workspace interpreter. A complete real-transaction preflight passed without model calls: base source fails only the issue test, and the prior model-generated patch passes all seven tests. All 87 relevant tests passed. Frozen r3 is running with the full agent-visible verifier; two known TB download failures were independently repaired and content-checked while the main queue continues. Preserve prior failures. This remains launch validation, not formal coverage. See [repair evidence](docs/LOCAL_ENVIRONMENT_REPAIR_20261018.md) and [live monitoring](docs/LOCAL_CAMPAIGNS_20261018.md).

**Validation audit, 2026-10-18 01:04 Asia/Shanghai.** Local SWE-agent r2 finished all five canary attempts at 00:58:47; none passed the final verdict. Three sessions submitted; two failed on sandbox Docker / dependency-trace infrastructure. The original result-only launch gate missed these two infrastructure failures. The updated gate scans worker evidence; a separate `postrun-audit.json` preserves the raw results and old coordinator counts. An explicit live-socket bind passed Docker identity inspection inside a real traced transaction; 14 regression tests passed. A no-model, no-gold diagnostic using base source plus official test_patch confirmed six tests fail to reach httpbin in the offline verifier. This cohort cannot establish policy efficacy. No more SWE model calls are scheduled until the HTTP test service and complete verifier command are validated. TB preparation continues; see [live monitoring](docs/LOCAL_CAMPAIGNS_20261018.md). Evidence: `/mnt/data/agenttx-runtime/results/sweagent-swe-local-monitored-20261018-r2/postrun-audit.json`, `/mnt/data/agenttx-runtime/validation/swe-workspace-mount-preflight-20261018-r3.json`, `/mnt/data/agenttx-runtime/validation/swe-requests-base-verifier-20261018-r2.log`.

**Local monitored launch, 2026-10-18 00:47 Asia/Shanghai.** The user authorized concurrent TB image preparation and a monitored SWE-agent / SWE-Bench launch. All 300 SWE images are ready; TB cohort r1 remains active (see [live monitoring](docs/LOCAL_CAMPAIGNS_20261018.md)). SWE launch r1 stopped before any model call because try could not see the nested data-disk workspace. Preserve that infrastructure failure. A data-backed `/tmp` alias and explicit read-only dependency mounts passed a real no-model transaction check: dependency imports, no host write before commit, rollback, commit, and pinned official-image interpreter access. Frozen cohort r2 has started the fixed `psf__requests-1963` clean canary. Only after its infrastructure gate passes may the other four policies run concurrently; the five-cell canary does not automatically expand to the full suite. Thirteen launch-gate regression tests passed. Sessions have a 900-second wall limit and no step/call caps; shared hard token budget, matched policy-prompt accounting and cross-harness validation remain pending. This is `formal_eligible=false`, not formal coverage. Code and frozen execution copies remain on the system disk; runtime data remains under `/mnt/data/agenttx-runtime`.

[中文版本](agent.zh-CN.md) · [当前实验进度（中文）](docs/EXPERIMENT_STATUS_20261017.md)

**Completion check, 2026-10-18 00:21 Asia/Shanghai.** The local SWE image
queue completed normally on 2026-10-17 at 23:30:32: all 300 images passed
content probes. All 300 local image identities were checked again against the
completion record. The preparer and monitor exited after completion; they
were not interrupted. One idle timeout switched to `hub.rat.dev` and completed;
the failed source attempt remains recorded. TB has 1818 verified source files;
TB images and full task/verifier readiness were outstanding at this check.
Model runs were paused then; see the newer monitored launch above. Evidence: `/mnt/data/agenttx-runtime/validation/completion-recheck-20261018.json`.

**Local execution update, 2026-10-17.** All code changes, environment preparation,
validation and future experiments run on local host `r760-ssd`. The remote
machine is a reference-code source only; its preparation queue has stopped.
The earlier remote image and TB checks are historical diagnostics and do not
count as local readiness. Code remains in this project; Docker image layers,
cache, results, logs and temporary files use `/mnt/data/agenttx-runtime`.
Source `scripts/agenttx-local-env.sh` to select the dedicated local Docker
socket and data paths. The shared system daemon is not restarted. Local
Alpine pull, offline runtime, Docker build/runtime and 18 relevant regression
tests passed. Existing recovered cache/log/result data is moved with content
verification before switching the original project paths to symlinks.

**Historical environment-only stage (superseded by the monitored launch above).** The local queue
verifies 300 SWE images with two workers and hydrates pinned TB inputs into
the data cache. Full task readiness still requires pristine base-commit
source, dependencies and official verifier checks. Preserve all failed and
interrupted attempts; no model launch or result-based retry is automatic.
Current local progress is maintained in the
[environment preparation record](docs/DOCKER_ENVIRONMENT_20261017.md).

**Image pull deadlines, 2026-10-17.** Environment cohort r3 replaces r2's
30-minute total cutoff with Docker API byte/milestone monitoring: fail over
after 900 seconds without download/extraction progress, or 14400 seconds
total per source; explicit registry/transport errors fail over immediately.
Sources are `docker.1ms.run`, `hub.rat.dev`, then `docker.1panel.live`; no direct
upstream fallback. A real blocked SWE image's manifest/config and large-layer
sample were checked across candidates; all three usable sources returned the
same manifest digest. This is a reachability sample, not a completed large
image pull. Seven timeout/progress tests and a local API pull/offline-container
smoke check passed. Existing cached images are reverified, not redownloaded;
all 1818 pinned TB files and SWE metadata were rechecked locally. Preserve r2
with 121 ready images, 9 errors and 2 pulls interrupted deliberately at the
cutover; record the interruptions separately, without changing old outcomes.
The new rules apply to environment downloads only; model budgets are unchanged.
See [timeout diagnosis and evidence](docs/IMAGE_PULL_TIMEOUT_20261017.md).

**Earlier launch, now paused.** The new
[stratified pilot](experiments/plans/stratified_pilot_20261017/registration.json)
is launched on the remote validation host with **8 concurrent experiment
processes**. It first runs 84 model-free runtime gates (12 canonical manifests
across all three families × 7 policies). All gates must pass before the queued
168 model cells (12 × 7 × 2 harnesses, one repeat) begin. Both Codex and
SWE-agent use `gpt-5.6-luna / max`, a common 900-second model wall limit,
no model-call cap, and a localization probe limit of `2 * operation_count + 8`
fixed before execution. This is a separate diagnostic cohort from the earlier
240-second repeats: token-budget enforcement and matched template accounting
remain formal gates. Preserve every failure and timeout; do not retry by
outcome. The frozen v5 execution source is
`938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`.
Live snapshots and completion evidence are linked in the
[pilot progress report](docs/STRATIFIED_PILOT_20261017.md).

The complete system microbenchmark covers **both harnesses**: 400 manifests ×
7 policies × 3 repeats × 2 harnesses = **16800 cells**. The older 8400-row
registration has no harness dimension and remains an immutable historical
plan. Real-world scope remains 5410 cells (300 SWE + 241 TB, five policies,
both harnesses); pilot results do not fill that formal matrix.

This document is the authoritative experiment specification. The evidence must
be produced and presented in this order:

```text
Microbenchmark
    -> Real-world workloads (SWE-Bench Lite and Terminal-Bench)
    -> System breakdown
    -> Appendix correctness and interrupted-recovery tests
    -> Other overheads
```

No layer substitutes for another. The microbenchmark combines analytical
sweeps with complete, reproducible long agent trajectories; the two official
suites provide real-world repository and shell workloads; the breakdown
attributes the result to specific mechanisms; correctness tests establish
invariants; and overhead experiments bound costs not visible in end-to-end
completion metrics.

The existing 400-case Motivation suite remains the controlled Microbenchmark
layer. A separate native-trajectory audit may establish whether its recovery
opportunity occurs in real agent work; it must keep its own sampling frame and
denominators. The earlier partial 541-task runs remain audit data, not completed
real-world results. Final paper results must be regenerated from a frozen
revision under the protocols below.

# Part I: Motivation Microbenchmark

## 1. Goal

Measure how recovery granularity behaves across a branch containing multiple
agent turns. The benchmark sweeps both `dependency_value` and task length,
represented by `turn_count=1..10`, rather than selecting one sparse,
single-turn graph that maximizes the ideal oracle's gap.

This is a controlled microbenchmark, analogous to sweeping thread count in a
systems benchmark. It establishes the mechanism behind the motivation claim:

> Temporal, turn-level, and branch-level rollback waste work when chronology is
> a poor proxy for causal dependence; the ideal selective rollback bound
> quantifies how much valid work a precise recovery mechanism could preserve.

No SWE-Bench or Terminal-Bench run is required to regenerate the controlled
Motivation figures. The proposed real-trajectory incidence figure draws on
these suites separately; their end-to-end policy results remain in Part II.

## 2. Motivation Argument and Unified Microbenchmark

The three recovery-boundary observations use one controlled microbenchmark
family. Native-trace incidence may follow them as separately labeled
external-validity evidence, but its cases, denominators, and token-weight
proxies must not be pooled with controlled manifests or real-model tokens.
The controlled claim is:

> Existing recovery boundaries follow execution structure (time, turn, or
> branch), while corruption follows dependencies. Under-rollback leaves
> invalid descendants; correct coarse rollback removes valid independent work.
> No fixed coarse boundary dominates as task length, fault position, and
> dependency structure change.

All controlled experiments use the same deterministic filesystem-effect
generator, canonical policy definitions, paired manifests, and validators.
The native-trajectory audit instead uses observed tool effects. The ideal
oracle is an analytical upper bound, not an implemented system. Motivation
must not run, plot, or claim results for `agenttx_causal`.

### 2.0 Motivation writing outline (authoritative)

The narrative structure is adapted from the evidence flow in
[WOLVES (OSDI '25)](https://www.usenix.org/conference/osdi25/presentation/pan):
analyze the existing paths, measure and break down their cost, state the
significance, explain why incremental attempts remain insufficient, then derive
the key insight and design requirements. We borrow this argumentative
progression, not its file-system claims or terminology.

The section must answer one central question:

> Why can no recovery boundary defined only by execution structure provide
> both complete recovery and low collateral loss for a multi-turn agent?

The three observations are not parallel facts. They form the following causal
chain:

```text
coarse boundaries can recover correctly only by over-deleting
    -> because temporal order does not encode failure dependence
    -> therefore choosing or tuning one fixed coarse boundary cannot solve it
    -> recovery needs a dependency-defined unit inside a long-lived trajectory
```

#### 3.1 Analyzing Recovery Paths of Stateful Agents

Open with one short paragraph and the recovery-boundary table from Background.
Group existing mechanisms by the state they can restore: failed step, turn,
temporal suffix, or whole branch. State the shared limitation neutrally: these
boundaries are defined by execution structure, not by which later effects were
actually influenced by the failure. Do not introduce the proposed system or
claim that causality is already the answer.

This subsection plays the same role as the reference paper's I/O-path analysis:
before measuring overhead, trace how each representative mechanism reaches its
recovered state. Follow one shared multi-turn example and show the selected set
for Root only, Turn checkpoint, Temporal step, and Whole branch recovery. Use
the ideal set only as a reference target. Explain what each path preserves and
what it necessarily cannot distinguish; do not report benchmark percentages in
this subsection.

The current `paper/main.tex` subsection **Recovery Boundaries in Stateful Agent
Execution** belongs here. It must be moved before the experiments, not left
after Observations 1--3. Its existing-boundary and state-propagation paragraphs
should be condensed into this path analysis; its design requirements belong in
Section 3.4 below.

#### 3.2 Analyzing Recovery Overheads

This subsection corresponds to the reference paper's crash-consistency
overhead analysis. It first states the methodology, then reports a breakdown,
three observations, and their significance. Every result is a consequence of
the paths defined in Section 3.1.

Define the two measured axes here, rather than in the path analysis:

1. **Recovery completeness:** what fraction of failure-affected effects is
   removed?
2. **Collateral recovery cost:** how much valid, independent work is discarded
   once completeness is satisfied?

This distinction prevents `no_recovery` or `root_only` from appearing efficient
merely because they leave corrupted descendants behind.

##### Methodology: one controlled workload family and paired population

Describe the multi-turn microbenchmark once: 16 operations per turn; 1--10
turns; controlled fault position, dependency value, graph shape, and
interleaving; identical manifests and fault roots for all six policies.

All three main observations and every later full-system Microbenchmark
experiment use the same 400 paired manifests as their statistical population:

- 100 task-length cases;
- 90 fault-position cases;
- 210 dependency-value cases.

The paper should state this 400-case population once and reuse it for all
three controlled observations. A subsection may zoom into one registered sweep
to explain a mechanism, but it must return to the same suite-level population
and must not present that slice as a new workload. The observational native-
trajectory audit in Section 2.8 has a different denominator and cannot be
reported as another slice of these 400 cases.

##### Observation 1: correctness creates a collateral-loss dilemma

**Claim.** Correct coarse recovery removes the invalid closure but also
discards useful work; low-cost under-recovery is not a valid alternative.

**Evidence.** Begin with the correctness gate over the shared suite. No recovery
removes 0% of the invalid closure and Root only removes 17.03% on
average. Turn checkpoint, Temporal step, Whole branch, and Ideal oracle all remove
100%. Among these correctness-qualified policies, Turn checkpoint, Temporal step, and
Whole branch discard 54.02%, 56.90%, and 100% of valid work on
average, while Ideal oracle discards none. Use the task-length sweep only to make the
amplification concrete: as a branch grows around a fixed four-operation
closure, coarse rollback grows while the exact target does not.

**Significance.** Existing boundaries face a correctness--retention dilemma:
rolling back less is incorrect, while rolling back safely may erase most of a
long-running agent's useful progress.

**Figure role.** `FIG-Motivation-Suite` is the independent Observation 1
figure. It shows the correctness gate and collateral-cost breakdown over the
400 paired cases. `FIG-Motivation-Coarse-Waste` remains a separate controlled-
scaling figure; do not merge it with the three observation figures. In the
paper, keep `FIG-Motivation-Suite` within one column and arrange its two internal
panels horizontally.

##### Observation 2: chronology--dependency mismatch is the root cause

**Claim.** The waste is not an incidental property of one checkpoint policy;
it arises because failure propagation follows dependencies while coarse
recovery follows time, turns, or branches.

**Evidence.** Introduce `dependency_value` only here, after Observation 1 has
established the symptom. Across the shared 400 manifests, 390 recovery targets
(97.5%) are non-prefix states. In the registered dependency sweep, temporal
recovery approaches the exact bound only as dependency value approaches one;
when dependencies are sparse, independent and affected work interleave and
temporal rollback discards the independent effects between them.

**Significance.** An exact recovered state often never existed as a temporal
checkpoint. Faster checkpoint creation, denser checkpoint placement, or a
cheaper branch abort can reduce mechanism overhead, but cannot represent that
non-prefix state.

**Figure role.** `FIG-Motivation-Dependency` is the root-cause figure. Its
x-axis is dependency value and its lines show discarded useful work, retained
valid work, and rollback amplification. Every statement about the mechanism
must be tied to a visible trend in this figure. Keep the figure within one
column and keep all internal panels horizontally aligned.

##### Observation 3: selecting a different fixed boundary does not solve it

**Claim.** No static coarse boundary is consistently best across workload
structures.

**Evidence.** Reuse the same 400 cases. Turn checkpoint has lower attributed
waste in 139 cases, Temporal step in 251, and they tie in 10. The ranking
reverses by registered slice: Turn checkpoint wins 60/100 task-length and 60/90
fault-position cases, while Temporal step wins 191/210 dependency cases. The best
fixed boundary still discards 10.5% more useful token weight than the post-hoc
per-case minimum of those two coarse choices.

**Significance.** The result rules out the obvious incremental response to
Observation 2: picking a globally better checkpoint or suffix policy. Boundary
selection changes which independent work is lost, but no fixed execution
boundary expresses the desired recovery set.

**Figure role.** `FIG-Motivation-Boundary-Crossover` is the limitation figure.
It reports both rank reversals and fixed-policy cost. The per-case minimum is a
diagnostic coarse-policy envelope, not the ideal oracle and not an implemented
adaptive policy. Keep the figure within one column and arrange its two internal
panels horizontally.

End Section 3.2 with one **Significance** paragraph that combines the three
observations without repeating their numbers: correct coarse recovery wastes
work, the waste is structural rather than an implementation artifact, and no
fixed coarse boundary removes the tradeoff. Do not introduce the solution in
this paragraph.

The current `paper/main.tex` subsection **Recovery Microbenchmark** becomes
Section 3.2. Move its recovery-policy definitions to Section 3.1, keep its
workload and measurements here, and remove the standalone suite-level detour
between Observations 1 and 2.

#### 3.3 Real-Trajectory Incidence and Case Anatomy

After the controlled observations, present the separately sourced audit in
Section 2.8. It asks whether real agent executions contain independently
useful work that a correct coarse rollback would discard. State the full task,
trajectory, turn, and candidate-fault denominators before any percentage.
Show zero-opportunity cases and one positive and one negative trace example.
Call the counterfactual result a *retention opportunity*, not an observed
crash, realized token saving, or measured AgentTX result. If the eligible
sample stays small, move the analysis to an appendix and narrow the main-text
claim rather than extrapolating from the six-turn pilot.

#### 3.4 Motivation: Attempts, Insight, and Design Challenges

Close the section in the same order as the reference paper's motivation:

1. **Attempts.** Root only recovery preserves work but fails completeness.
   Turn checkpoint constrains loss to affected turns but cannot retain valid
   work within them. Temporal step is exact only when all later work
   depends on the fault. Whole branch is safe but maximally coarse.
   Increasing checkpoint frequency changes cost, not representability.
2. **Insight.** The lifetime of speculation and the unit of recovery are
   different system decisions. Keep the trajectory as the speculation unit,
   but define the recovery unit by affected effects rather than by one
   contiguous execution interval.
3. **Design challenges.** State the non-trivial questions implied by the
   insight: (i) how to discover dependencies hidden inside opaque tool calls
   and descendant processes; (ii) how to track object identity and historical
   versions across aliases, renames, replacements, and copy-up; (iii) how to
   reconstruct a non-prefix state consistently across workspace and
   agent-visible context; and (iv) how to publish only an approved durable
   frontier without exposing a speculative suffix.
4. **Transition.** End by stating that the next section introduces an
   abstraction addressing these challenges. Do not name AgentTX or describe
   implementation mechanisms inside Motivation; the system name belongs at
   the start of the following design section.

##### Section flow and figure order

The intended main-text order is:

```text
3.1 trace existing recovery paths (no performance claims)
    -> 3.2 unified methodology and suite-level correctness gate
       -> Observation 1 + correctness/collateral-cost figure
       -> Observation 2 + dependency-sweep figure
       -> Observation 3 + boundary-crossover figure
       -> combined significance
    -> 3.3 separate real-trajectory incidence + positive/negative case anatomy
    -> 3.4 attempts -> insight -> design challenges -> transition
```

Keep the three observation figures independent. Do not compress
`FIG-Motivation-Suite`, `FIG-Motivation-Dependency`, and
`FIG-Motivation-Boundary-Crossover` into one composite. The separate
`FIG-Motivation-Coarse-Waste` figure documents controlled task-length,
chronology, and rollback-amplification trends before the observations;
one-factor sensitivity plots remain supporting material.

### 2.1 MB1: Coarse-boundary scaling

**Question.** If the true fault closure stays small while the branch grows from
one to ten turns, how much unnecessary work does each correct policy remove?

**Input.** Sweep 1--10 turns with 16 operations per turn. Fix the fault at 25%
of the complete branch and its invalid closure at four operations. Use 10 paired
manifests per turn count and keep the full policy manifest for audit, but
exclude unsafe controls from efficiency claims.

**Figure 1a/1c.** Sweep 1--10 turns at 16 operations per turn and report valid
operations discarded and rollback amplification. The fault closure remains
four operations, so growth is caused by task length rather than fault size.

### 2.2 MB2: Chronology sensitivity

**Question.** Does a temporal policy change when dependencies remain fixed but
the fault moves through execution order?

**Input.** Hold task length at 10 turns, turn width at 16, and closure size at
four. Sweep global fault position from 0.10 to 0.90 with 10 paired manifests
per point.

**Figure 1b.** Report valid operations discarded. Turn checkpoint follows the
turns containing invalid effects, Whole branch follows the complete branch,
Ideal oracle remains exact, and Temporal step waste varies with the suffix. This isolates
chronology from dependency structure.

### 2.3 MB3: Dependency-value sweep

**Question.** How does the mismatch change as causal density ranges from sparse
to dense?

**Input.** Use five turns of 16 operations, a 25% global fault position,
layered shape, alternating interleaving, all 21 `dependency_value` points from
0.00 to 1.00, and 10 paired manifests per point.

**Figure 3.** Plot useful work discarded, valid work retained, and rollback
amplification for all four correct policies. Include every preregistered point,
including dense cases where Temporal step approaches Ideal oracle.

### 2.4 Fixed-boundary crossover

**Question.** Can one coarse recovery boundary remain the better choice as
task structure changes?

**Input.** Reuse exactly the 400 paired MB1--MB3 manifests. Do not generate a
new workload or select a favorable subset. For each manifest, compare
`turn_checkpoint_proxy` and `temporal_step` by `useful_tokens_wasted`, report
which policy has lower waste, and retain ties.

**Figure 4.** Report lower-waste policy frequency separately for the task-
length, fault-position, dependency-value, and combined slices. Also report the
mean waste of each fixed policy and the post-hoc per-case minimum of the two.
The per-case minimum is an analytical coarse-policy envelope, not a realizable
oracle and not an additional recovery policy.

### 2.5 Supporting sensitivity and breakdown

One-factor sweeps over turn count, fault position, graph shape, and interleaving use
the same generator and policy semantics. They may appear in the main text when
they support a distinct claim; otherwise they belong in the appendix. The
suite-level summary combines all 400 paired manifests from MB1--MB3: 100
turn-count cases, 90 fault-position cases, and 210 dependency-value cases. It reports
recovery completeness for all policies, then measures collateral recovery cost
only for policies that remove the complete invalid closure.

Native-trace incidence may support Motivation only under the separate
observational protocol in Section 2.8. Real-model replay, SWE-Bench and
Terminal-Bench end-to-end policy results belong in Evaluation. None may be
pooled with the 400 controlled manifests or used to replace their sweeps.

### 2.6 Main-text figure disposition

There is no fixed upper bound on the number of Motivation figures, but the main
text should follow the three-observation chain in Section 2.0. The current
artifacts map to it as follows:

1. **Correctness--retention dilemma:** use the suite-level correctness and
   collateral-cost results from `FIG-Motivation-Suite` as Observation 1. Use
   `FIG-Motivation-Coarse-Waste` for controlled task-length, chronology, and
   rollback-amplification trends.
2. **Temporal--dependency mismatch:** MB3 in `FIG-Motivation-Dependency`,
   supporting Observation 2. Across MB1--MB3, 390/400 exact survivor states
   cannot be represented by one temporal-prefix checkpoint, demonstrating how
   often execution order diverges from failure dependence.
3. **Coarse-boundary crossover:** the same 400 MB1--MB3 manifests in
   `FIG-Motivation-Boundary-Crossover`, supporting Observation 3 by showing
   that Turn checkpoint and Temporal step recovery reverse rank across the
   registered workload slices.
The one-factor turn-count, fault-position, shape, and interleaving sweeps remain
available in `FIG-Motivation-Sensitivity` as supporting robustness evidence,
but they are not a separate main-text observation.

`FIG-Motivation-Suite` and `FIG-Motivation-Coarse-Waste` remain separate:
the former supports Observation 1, while the latter shows controlled scaling.
Additional main-text figures may
report another controlled view only when it supports a distinct observation.
The real-trajectory incidence figure, if the Section 2.8 acceptance gates
pass, is a separately sourced external-validity figure after Observation 3,
not a fourth controlled observation or a panel of the 400-case figures.
`FIG-M1-Strategy-Outcomes` is a historical, noncanonical plot and
`FIG-M2-Extreme-Tradeoff` is a historical rendering of the same MB1 endpoint,
so it must not be counted as additional evidence.

### 2.7 Current data status

| evidence | protocol | status |
|---|---|---|
| MB1 task-length scaling | 1--10 turns, 10 paired manifests/point, fixed turn width 16 and closure 4 | complete; 100 paired cases |
| MB2 chronology | 9 fault positions, 10 paired manifests/position, 10 turns | complete; 90 paired cases |
| MB3 dependency sweep | 21 values, 10 paired manifests/value, 5 turns | complete; 210 paired cases |
| Boundary crossover | derived from the same 400 MB1--MB3 paired manifests | complete; no additional cases |
| Supporting sensitivity | turn count, fault position, shape, interleaving | complete; no missing paired cells |

### 2.8 Real-agent trajectory incidence and case-study plan

**Question and scope.** How often does a real agent trajectory contain useful
work that correct turn-, temporal-, or branch-level recovery would discard
after one of its actual mutating steps becomes faulty? This is an
observational *opportunity* study. It cannot estimate the natural frequency of
agent faults or the realized savings of a recovery system. The YoloFS
290-report study classifies public misuse reports; our unit is an observed
agent execution, not an incident report or a synthetic manifest.

**Existing evidence and gap.** The current native SWE-agent pilot contains 20
successful source/replay trajectories and 638 turns. Six turns in three tasks
pass its strict gate of at least two independent stateful components. Its
counterfactual token weights are serialized tool-call payload sizes, not LLM
usage. The official 541-task experiment seeds a synthetic pre-crash trajectory
and randomized fault; its rows measure recovery under injection, not the
prevalence of this structure in unmodified agent behavior. Neither dataset
alone supports a claim that the opportunity is common.

**Cohort and capture.** Run the real agent once per official task from a clean
workspace, without fault injection, rollback, a seeded reference solution, or
recovery hints. This clean execution is the native trajectory; retain its
original agent turns and every tool step, including read-only and failed
steps. Run four independent source-system/benchmark strata: Codex on SWE-Bench
Lite, Codex on Terminal-Bench, SWE-agent on SWE-Bench Lite, and SWE-agent on
Terminal-Bench. Target all available 300 SWE-Bench Lite and 241 Terminal-Bench
task IDs in each source-system stratum, without selecting by observed
advantage. Freeze the model (Luna max for both agents), harness, ordinary
task prompt, and task revision before analysis. Retain every captured attempt
with its task ID, attempt index, and configuration provenance, including
partial and failed runs; repeated attempts must not be silently deduplicated.
Use existing clean runs when their native step records survive; otherwise
rerun clean with passive trace capture. The current `clean_recovery` prompt describes a recovery-context
control, while `no_fault` uses an ordinary task prompt but lacks the complete
transaction step export; neither mode's aggregate CSV alone is a native
dependency trace. Record task success separately, never use it to filter
recorded steps, and report missing logs, replay
mismatches, environment failures, unsupported effects, and duplicate attempts
in a flow table. Public SWE-agent/Claude traces are a separate source-system
stratum, never merged silently with new Codex/Luna trajectories. Do not infer
native dependencies from the official experiment's synthetic recovery
documents. Test failure, incomplete execution, commit failure, and capture
warnings remain labels, not analysis inclusion criteria.

Terminal-Bench clean trajectories must run the agent inside each task's
official Docker image, preserving image paths such as `/data`; verifier files
must not be placed in the agent-visible workspace. The final workspace must
then pass the official image's `run-tests.sh` before success is recorded.
Missing verifier dependencies or failed downloads are environment errors, not
task failures. Earlier host-projected clean prechecks are excluded from this
cohort. The current formal Terminal-Bench runner still executes the agent on
the host and therefore cannot be labeled an image-native agent run merely
because its final verdict uses Docker.

**Opportunity census.** Capture each real step's observed reads, writes,
deletes, negative lookups, object identity, turn, timestamp, and tool-result
provenance. For every actual write/delete step with later observed work,
counterfactually mark it faulty. Compute its observed dependency closure and
the valid steps removed by each correct coarse boundary. A survivor counts as
retention opportunity only when its filesystem and explicit context
dependencies are accounted for; unresolved effects are reported as uncertain
and never presented as proven savings. Freeze the candidate-step rule and policy
semantics before inspecting outcomes. Keep all zero-opportunity tasks and all
eligible fault sites; do not select a maximally favorable root per task for
the primary statistic. Separately characterize long dependencies by the
number of steps and turns spanned by each observed edge, the longest directed
dependency path per task, and how far an eligible root's dependent work
extends. A long chain alone is not evidence of recovery waste: count the
independent, useful steps interleaved with it and compare those steps with
what each coarse boundary would discard.

**Reported denominators and metrics.** Report unique task IDs, captured
attempts, recorded turns and steps, mutating roots, and attribution warnings
separately for all four agent-by-benchmark strata. Do not pool
Codex and SWE-agent turn-level measurements: a Codex `exec` is one CLI turn
containing multiple native tool events, while SWE-agent exposes its own model
turns. Primary prevalence is
the fraction of recorded tasks with at least one positive site, accompanied by
the fraction of eligible sites with positive waste; label the first an
*existence* metric, not a fault probability. Equal-weight tasks for aggregate
means and bootstrap intervals by task so long trajectories do not dominate.
Report zero mass, median/P90/P99 of discarded valid steps and serialized
tool-result token weight, and a same-turn versus cross-turn breakdown. Compare
Turn checkpoint, Temporal step, Whole branch, and Ideal oracle on identical roots;
exclude Root only from efficiency claims when it leaves invalid descendants.
Also report the distribution of edge spans and longest paths in steps and
turns, the fraction of tasks containing cross-turn dependencies, and waste
conditional on dependency span and interleaved independent work. Tool-result
token weight is a work proxy, never model or replay token usage.

**Validation and case replay.** Audit a preregistered sample of positive,
zero, and unknown classifications against the original syscall/effect logs;
report disagreement and fail closed on ambiguous dependencies. Before model
continuation, select cases from fixed strata (high, median, zero opportunity;
same-turn and cross-turn; both benchmarks), not by realized savings. Inject
the same fault at the selected real step for each policy and run the official
verifier plus survivor-file hashes and agent-context checks. Record all
successes, failures, environment exclusions, model tokens, replay tokens, and
wall time. Compare replayed cases only after checking that the same native
trajectory and fault schedule were used. The ideal boundary is an oracle
lower bound; implemented AgentTX results belong in Evaluation, not this
Motivation incidence claim.

**Outputs and acceptance.** Produce a frozen cohort manifest, source-trace
hashes, per-step effect/edge records, per-root policy counterfactuals, a
task-level summary including zeros, an exclusion table, and reproducible
positive/negative case timelines. A main-text incidence plot must show the
denominator and zero-opportunity mass; a separate case timeline may explain
the mechanism. Do not claim broad prevalence until both real-workload strata
have adequate usable coverage and the dependency audit passes. If incidence
is low or one suite lacks usable traces, report that result and narrow the
paper claim rather than changing the cohort or fault-site rule.

**Implementation route.** Add passive step/effect export to the official
clean task path before its workspace and session are destroyed, retaining the
ordinary `no_fault` task prompt and checking that tracing does not alter the
agent-visible workflow. Persist the full tool-call sequence, turn boundaries,
ledger/effect provenance, and source configuration; validate `fault_injected`
and `precrash_progress_seeded` are false. Reuse
`replay_sweagent_native_traces.py` only to replay an unchanged source action
sequence and export effect provenance; collect Codex native traces through
its task harness rather than reconstructing them from the injected official-run
CSV. Generalize
`analyze_intra_turn_traces.py` from its six-turn eligibility gate to a census
that retains every usable task, turn, and candidate root, including zeros.
Extend `bench_real_trace_counterfactual.py` to write the full per-root policy
table and uncertainty labels without plotting implemented causal recovery as
Motivation evidence. Keep the old 20-trace pilot immutable. Put the new cohort,
raw audit, exclusions, figures, and case-replay outputs under a separately
versioned `experiments/results/motivation/real_trajectory_audit/` directory;
never overwrite the controlled 400-manifest results.

**Execution started 2026-10-06.** The first cohort uses native SWE-agent
with Luna max on the ordinary fault-free task instruction. It records each
action through the in-process adapter; it does not inject a crash, seed a
reference patch, or give a recovery hint. The two completed preflight cases
(`django__django-10924` and `analyze-access-logs`) both show cross-turn
workspace-file dependencies, but neither has an observed independently
mutating survivor; they remain zero-opportunity examples. A preregistered
12-task phase-one sample, two tasks per short/medium/long stratum in each
benchmark, is in `cohort_phase1.json` under the run directory. Both agents
use these same task IDs, forming 24 agent-task cells across four strata.
Image-native outputs are separated by agent and suite under
`experiments/results/motivation/real_trajectory_audit/`; earlier
`clean_native_luna_max_20261006` host-projected Terminal-Bench records are
not part of the official-image cohort.
The separate 20-trace public SWE-agent/Claude replay is only a method
calibration stratum, not pooled with Luna. The current analysis counts
observed workspace-path dependencies and excludes tooling/cache paths; it
does not certify all other work as independent or convert step counts into
model-token savings. Codex's ordinary external transaction is one process
step. Its separate passive native capture observes Codex JSON tool events,
attributes syscall reads within each event interval, and fingerprints
workspace writes at event boundaries. Traces with overlapping or unsupported
mutating tool events or unattributed final writes are excluded; overlapping
read-only events are conservatively overattributed. Codex `web_search` is a
real step but an opaque context barrier, so work across it is never certified
independent. Codex is not treated
as one transaction step, nor are artificial model-turn boundaries inferred.
Each of the four strata is validated in its official Docker task image before
cohort analysis. Earlier Terminal-Bench host-projected traces remain excluded.
As of 2026-10-06, the frozen six-task pilot has six recorded native trajectories
in each of the four agent-by-benchmark strata (24 in total). The SWE-agent
Terminal-Bench set includes a submitted case whose official tests failed and
a case that ended with the agent's exit format after recording real steps.
Codex's SWE-Bench Requests case also failed official tests after a valid
39-step capture. All three traces are retained in the step census. A failed
official test never excludes recorded steps; verifier/environment errors leave
the success label unknown. Overlapping mutating Codex tool events make step
attribution uncertain and are retained with a capture warning. The earlier SWE-Bench
pilot verifier omitted the official test patch, so its success labels remain
unknown even though its six step traces are recorded. All pilot denominators,
duplicate attempts, and warnings are recorded in the four `analysis_*_phase1`
directories under the real-trajectory audit results.
After the 24-cell pilot, four persistent full-cohort captures were launched
from the remaining 294 SWE-Bench Lite and 235 Terminal-Bench tasks per agent.
They run in `agenttx_native_{swe,codex}_{swe,tb}` tmux sessions and write
`full_{sweagent,codex}_{swe,tb}_20261006.log` beside their result directories.
These captures are in progress, not completed evaluation results. The
SWE-Bench capture admits an exact Astropy image-build pin of `setuptools` to
`68.0.0` in `pyproject.toml`; all other source-content differences still fail
the image/base check.

## 3. Hard Constraints

- Repository: `/home/bfq/agenttx`
- Python: `/home/bfq/miniconda3/envs/agenttx/bin/python3.11`
- Do not run or plot `agenttx_causal` in motivation. Its implemented-system
  results belong in the later evaluation, not the opportunity study.
- Every policy in one paired cell receives the same materialized trace, fault
  root, dependency graph, token weights, and seed.
- Do not remove unfavorable points, failed cells, or dense-dependency points.
- A single extreme sparse case may be retained as an appendix regression test,
  but it is not main-text evidence and is not a substitute for the sweep.
- Crab's turn-boundary policy is represented only by `turn_checkpoint_proxy`; do not describe its
  numbers as measurements of the Crab artifact.

## 4. Dependency Value

For a single-root trace, let:

- `r` be the faulty operation;
- `S = {s | s occurs after r}` be the temporal suffix after the root;
- `C` be the transitive causal closure of `r`, including `r`.

Define:

```text
dependency_value = |(C - {r}) intersect S| / |S|
```

`dependency_value` is therefore the fraction of post-fault operations that
really depend on the fault:

- `0.0`: no later operation depends on the fault;
- `1.0`: every later operation depends on the fault;
- intermediate values: dependent and independent work coexist.

This definition is independent of branch length and does not move the fault root
to make a requested closure size feasible. The generator first fixes the total
operation count and `fault_position`, then rounds
`dependency_value * post_fault_operations` to obtain the dependent count.
Both requested and realized values must be written to every result row.

The old `closure_ratio = |C| / |E|` remains a compatibility field only. It
mixes fault position with dependency density and must not be used as the main
x-axis.

## 5. Recovery Policies

See the [bilingual terminology reference](docs/terminology.md) for canonical
Chinese/English names, policy IDs, and the separate experiment namespaces.

| canonical ID | figure label | rollback boundary | expected role |
|---|---|---|---|
| `no_recovery` | No recovery | none | negative correctness control |
| `root_only` | Root only | fault producer only | dependency-propagation ablation |
| `turn_checkpoint_proxy` | Turn checkpoint | every complete turn containing invalid effects | favorable turn-boundary baseline |
| `temporal_step` | Temporal step | fault root through end of the branch | temporal-suffix baseline |
| `whole_branch` | Whole branch | complete uncommitted branch | coarsest baseline |
| `ideal_oracle` | Ideal oracle | exact invalid closure | analytical upper bound |

These identifiers and labels are defined once in
`experiments/motivation_strategies.py`. Motivation figures plot all six
policies. End-to-end model experiments use a separate
namespace (`clean_recovery`, `crash_no_rollback`, `temporal_checkpoint`,
`whole_branch_abort`, and `causal`) and must not silently rename those modes
to microbenchmark policies.

`no_recovery` and `root_only` may be cheap but incorrect. Token or work
savings are compared only between policies that remove the complete invalid
closure and pass all correctness checks.

## 6. Primary Sweep

The headline experiment is a one-factor sweep over dependency density:

| factor | primary value(s) |
|---|---|
| turn count | 5 |
| operations per turn | 16 |
| fault position | 25% |
| dependency value | 0.00 through 1.00 in increments of 0.05 (21 points) |
| graph shape | layered |
| interleaving | alternating |
| seeds/cases per gradient | 0 through 9 (10 cases) |

These 21 dependency points are preregistered. Report all points, including
points where Ideal oracle ties a coarse baseline.

Primary plots:

1. `dependency_value` versus useful valid operations wasted.
2. `dependency_value` versus valid-token retention.
3. `dependency_value` versus ideal savings relative to temporal and turn
   checkpoint rollback.

Use one consistent line-chart style. Each policy is one line; show means with
95% paired bootstrap confidence intervals and retain the raw points.

## 7. Sensitivity Sweeps

Change one factor at a time from the primary configuration:

| sweep | values |
|---|---|
| turn count (task length) | 1 through 10 |
| fault position | 0.10, 0.25, 0.50, 0.75, 0.90 |
| graph shape | chain, fan-out, layered |
| interleaving | independent-before, alternating, independent-after |

Every sensitivity sweep retains the same 21 dependency values from `0.00`
through `1.00` in increments of `0.05`. Use 10 seeds/cases per gradient. Do
not form an unnecessary full Cartesian product of
all nuisance factors; the primary sweep and one-factor sensitivity sweeps are
the intended design.

## 8. Metrics

For all operations `E`, invalid closure `C`, valid operations `V = E - C`, and
policy rollback set `R_p`, record:

```text
valid_retention        = 1 - |R_p intersect V| / |V|
invalid_removal        = |R_p intersect C| / |C|
useful_steps_wasted    = |R_p intersect V|
useful_tokens_wasted   = sum(token_weight(s), s in R_p intersect V)
invalid_steps_retained = |C - R_p|
```

Also record requested and actual dependency value, fault position, closure
size and legacy closure ratio, exact rollback set, manifest hash, rollback
time, and all four correctness views: merged overlay, conversation, committed
host, and verifier.

Tokenizer-attributed work is not billed model usage. Any later model replay
must report prompt, completion, and total usage separately.

## 9. Correctness Gates

Before accepting the analytical sweep:

1. For every requested dependency value, assert the realized dependent count
   equals `round(dependency_value * suffix_size)` and the reported actual value
   equals that count divided by the suffix size.
2. Assert the generator realizes the requested dependency value exactly after
   documented discrete rounding.
3. Run physical-overlay cells across all five registered turn-count groups and
   representative dependency values. The physical gate uses six distinct
   manifests per group because it validates execution semantics rather than
   estimating a population mean.
4. For every physically executed policy, check the merged overlay,
   conversation active calls, committed host state, and verifier result.
5. Assert policies in a paired cell share the same manifest hash.

The fixed 128-operation sparse trace remains an appendix regression test. It
must not appear as a standalone main-text motivation result.

## 10. Breakdown

For each non-ideal policy, decompose the gap to `ideal_oracle` into invalid
work retained and additional valid work discarded. This is a policy-granularity
breakdown, not an AgentTX component breakdown.

## 11. Microbenchmark Scope Boundary

AgentTX causal effectiveness, ledger/query overhead, compact-context cost,
commit latency, RSS, and model usage belong in the later system evaluation.
They must not be mixed into this motivation dataset.

## 12. Execution

Run focused tests first:

```bash
cd /home/bfq/agenttx
export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx
PY=/home/bfq/miniconda3/envs/agenttx/bin/python3.11

"$PY" -m pytest -q tests/test_intra_turn_recovery.py
```

Run the deterministic dependency sweep:

```bash
"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 \
  --dependency-values 0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 \
    0.50 0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0 \
  --turn-counts 5 --widths 16 \
  --fault-positions 0.25 \
  --shapes layered \
  --interleavings alternating \
  --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark
```

Run MB1--MB2 with:

```bash
"$PY" experiments/scripts/bench_controlled_motivation.py \
  --output-dir experiments/results/motivation/controlled_case
```

Then run the one-factor sensitivity sweeps:

```bash
DEPENDENCY_VALUES=(
  0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 0.50
  0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0
)

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 1 2 3 4 5 6 7 8 9 10 --widths 16 --fault-positions 0.25 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/turn_count

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.10 0.25 0.50 0.75 0.90 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/fault_position

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 \
  --shapes chain fan-out layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/shape

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 --shapes layered \
  --interleavings independent-before alternating independent-after \
  --repeats 10 --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/interleaving
```

Never merge rows from an incompatible schema or an older closure-ratio-only
run.

## 13. Required Artifacts

```text
experiments/results/motivation/microbenchmark/config.json
experiments/results/motivation/microbenchmark/motivation_recovery_raw.csv
experiments/results/motivation/microbenchmark/motivation_recovery_summary.csv
experiments/results/motivation/microbenchmark/REPORT.md
experiments/results/motivation/controlled_case/config.json
experiments/results/motivation/controlled_case/controlled_recovery_raw.csv
experiments/results/motivation/controlled_case/controlled_recovery_summary.csv
motivation/FIG-Motivation-Dependency.pdf
motivation/FIG-Motivation-Dependency.png
motivation/FIG-Motivation-Coarse-Waste.pdf
motivation/FIG-Motivation-Coarse-Waste.png
motivation/FIG-Motivation-Suite.pdf
motivation/FIG-Motivation-Suite.png
motivation/FIG-Motivation-Boundary-Crossover.pdf
motivation/FIG-Motivation-Boundary-Crossover.png
motivation/FIG-Motivation-Sensitivity.pdf
motivation/FIG-Motivation-Sensitivity.png
```

The report must state the complete sweep grid, planned and completed paired
cells, correctness-gate status, all exclusions with reasons, and the Git commit
or dirty-worktree fingerprint used for the run.

## 14. External Artifact Baselines

The paper's recovery comparison must include real external artifacts where they
are available. Table 1 classifies semantics; it does not imply that every row is
runnable. Keep author-reported numbers separate from measurements produced on
our host.

| system | native boundary | artifact status | experiment status |
|---|---|---|---|
| try | one opaque command effect set | MIT; pinned locally | run now |
| Sandlock | one sandboxed process run | Apache-2.0; builds locally, but host kernel has Landlock disabled | run later on a Landlock-enabled host as a process-boundary/overhead baseline |
| YoloFS | session snapshot | GPL-2.0; pinned and mounted locally | run now |
| BranchFS | leaf branch | MIT; pinned and mounted locally | run now |
| Waypoint | filesystem + process checkpoint | Apache-2.0; pinned; CRIU 4.2.1 passes in the privileged evaluation container | run now, but report container results separately from bare-host latency |
| Crab | sandbox checkpoint | no official artifact located | qualitative only; never label a proxy as Crab |
| DeltaBox | sandbox checkpoint | authors state artifact is not public | qualitative only |

The common artifact-native experiment reuses the microbenchmark's materialized
multi-turn manifests. Every system receives the same initial workspace, turn
boundaries, dependency value, fault location, and crash notification. It then
uses only its native recovery operation: command/process discard for try and
Sandlock, `snapshot`/`travel` for YoloFS, leaf `abort` for BranchFS, checkpoint
`fork` for Waypoint, and dependency-selected recovery for AgentTX. Do not graft
AgentTX's dependency scheduler onto an external artifact.

Validate every recovered workspace with one shared oracle. Report invalid-state
removal, valid-state retention, recovery latency, end-to-end latency, setup
cost, storage amplification, and privilege/kernel requirements. A baseline that
cannot represent a selective survivor state should use its native coarse
boundary and record the valid work it discards. Environment failures are
reported separately and excluded from performance aggregates.

The immediate matched run set on this host is AgentTX, try, YoloFS, BranchFS,
and Waypoint. Waypoint runs in a privileged, network-disabled container whose
runtime directories are bind-mounted from host ext4; do not combine its latency
with bare-host latency without an explicitly matched container run. Sandlock
joins on a Landlock-enabled kernel. The adapter and raw results must record
the upstream Git commit, local patches, command line, host fingerprint, and
whether each cell exercised the external artifact rather than a policy proxy.

Paper results must use the complete 400-manifest Motivation population: all
100 MB1 task-length cases, 90 MB2 fault-position cases, and 210 MB3 dependency
cases. Smoke tests and favorable subsets are validation only. The artifact
runner reconstructs these cases with `--suite motivation400` and refuses to run
if any `trace_id`/`manifest_hash` differs from the registered Motivation CSVs.
The full command is:

```bash
PYTHONPATH=src:. /home/bfq/miniconda3/envs/agenttx/bin/python3.11 \
  experiments/scripts/bench_external_recovery_artifacts.py \
  --suite motivation400 \
  --backends agenttx session_try yolofs branchfs waypoint \
  --workers 6 --resume \
  --output-dir experiments/results/external_recovery_motivation400_20260922
```

# Part II: System Evaluation

## 15. System Microbenchmark: End-to-End Execution of Motivation Cases

This full-system microbenchmark executes the exact cases used by the analytical
Motivation sweep. It is not a new workload and is not a substitute for the
official real-world suites. It exercises a model, tool runner, dependency
capture, recovery, context reconstruction, repair, and task verification in
one run while retaining the same controlled dependency structures.

### 15.1 Workload

Use the canonical 400 MB1--MB3 manifest IDs without changing their turn count,
operation order, graph shape, dependency value, interleaving, fault root, or
expected survivor set. Materialize each manifest as an executable repository
task with a generated task description and verifier. The generated task must
preserve the manifest's semantics exactly; it may not add dependencies merely
to make the result more favorable.

Every policy receives the same initial snapshot, task text, model
configuration, fault schedule, and token budget for a given case ID. A pilot
may execute a preregistered subset for protocol debugging, but subset results
do not appear in the paper and cannot replace the complete 400-case
Microbenchmark.

### 15.2 Policies and repetitions

Run `clean_recovery`, `crash_no_rollback`, `turn_checkpoint`,
`temporal_checkpoint`, `whole_branch_abort`, `causal`, and `ideal_oracle`.
`ideal_oracle` may receive the exact faulty step and closure; every other crash
policy receives only the same crash notification and policy-specific recovery
boundary. `causal` must localize the fault through AgentTX's own evidence.

Use three repeats per case and policy for the complete 400-case system
microbenchmark.
The primary model and reasoning configuration must match the real-world
workload. Prompt templates must be token-matched apart from unavoidable policy
payloads; report those payload tokens separately.

### 15.3 Metrics and acceptance

Report task success, invalid-effect removal, valid-effect retention,
workspace--context agreement, total model tokens including replay, tool calls,
recovery latency, and end-to-end wall time. Efficiency comparisons use paired
successful cells; all-attempt success and cost remain visible beside them.

The system microbenchmark is complete only when all 400 Motivation manifest IDs
have full-system results, every policy has the required three repeats, no
environment failure is silently excluded, and each paired cell has an
identical manifest and crash schedule.

## 16. Real-World Workloads: SWE-Bench Lite and Terminal-Bench

SWE-Bench Lite and Terminal-Bench are the paper's real-world workloads. Do not
describe them as a synthetic microbenchmark and do not add a separate toy
"real-world" scenario.

### 16.1 Scope

Run the complete available suites: 300 SWE-Bench Lite instances and 241
Terminal-Bench instances. The primary paper run uses one repeat per task and
policy; five repeated full-suite runs are not required. A stratified pilot may
be repeated to estimate variance, but it cannot replace the complete run.

The primary path uses a live agent and each suite's official verifier. Gold
patches and oracle solutions are permitted only for environment validation or
an explicitly labeled oracle upper bound; they are not task-success results.
Terminal-Bench also requires the agent to execute inside the task's official
Docker image; running only its verifier there is insufficient. The current
formal host-projected Terminal-Bench rows are diagnostic, not comparable
real-world success results. Do not resume or publish them as a native-image
cohort. A new formal run must use the image-native agent path for every mode.

Freeze the same model, reasoning effort, original task prompt and crash schedule, task set,
wall timeout, verifier, and count-limit policy for all strategies and both
agent harnesses. Policy-specific recovery handoffs follow the explicitly registered
context treatment in Section 16.2; they are not required to have identical text.
Do not inherit per-task SWE-agent step/model-call caps from
the synthetic workload scale when Codex has no corresponding cap. An omitted
`--max-turns` means no count cap; any explicit cap belongs in `run_config.json`
and requires a separately matched Codex protocol. Keep incomplete cells and
infrastructure failures visible in the denominator rather than silently
treating an expected-cell count as completed data.

### 16.2 Recovery protocol

Each crash-enabled task must inject the same registered failure schedule across
policies without disclosing the faulty step. The agent is told only that a
crash has occurred. Coarse policies apply their declared boundary; AgentTX may
write its inferred localization to the recovery handoff after searching its
ledger; only `ideal_oracle` may directly disclose the exact failed step.

The current comparison uses exactly these six arms:

| Policy | Recovery context | Role |
| --- | --- | --- |
| `causal` | **compact** | AgentTX localization and selective recovery |
| `ideal_oracle` | **compact** | Knows the faulty steps and true invalid descendants before recovery; exact rollback without localization |
| `clean_recovery` | **compact** | No injected fault or recovery overhead; optimization upper reference |
| `crash_no_rollback` | **full** | No-rollback comparison control |
| `temporal_checkpoint` | **full** | Temporal-checkpoint comparison control |
| `whole_branch_abort` | **full** | Whole-branch comparison control |

Register and verify this mapping per worker and per result. Set the actual
worker environment before constructing prompts and loading native context;
a registration label alone does not enable compact. A context mismatch is an
infrastructure error and must stop new dispatches.
The comparison includes the context treatment; a rollback-only attribution
would require a separately registered context ablation.
Any additional `turn_checkpoint` arm requires separate registration when the
runner exposes a distinct multi-turn boundary.
All crash modes use the same fault count and positions. `clean_recovery` has no
injected crash and defines the fault-free optimization upper reference (baseline task cost). Ideal knows the true faulty steps and invalid closure before recovery. Compare recovery overhead relative to clean; independently sampled model token totals are not a deterministic ordering.
Both clean and ideal still use the live model to solve the task; neither receives
a gold solution. Small-sample totals below clean do not establish a better upper bound.

### 16.3 Metrics

Report official success, crash-recovery success, workspace--context agreement,
total tokens (`main + replay`), wall time, recovery time, tool calls, and
environment failures. Present SWE-Bench and Terminal-Bench separately before
their aggregate. Token comparisons must include success rate and paired-success
sample count; a failed low-token run is not a saving.

### 16.4 Current SWE-agent campaign execution requirements

These rules record the user's 2026-10-18 reset and launch instructions:

1. Run all 300 SWE-Bench Lite tasks across the six policies above, once per task
   and policy: **1800 full-suite cells**, with at most **eight concurrent model workers**.
2. For this reset, stop the old workers immediately, remove their residual
   evaluator containers, and delete the old monitored cohorts' results,
   trajectories, logs and runtime workspaces. Do not wait for old tasks to
   drain, archive those results, inherit them, or pool them into the new campaign.
   Preserve source code, pinned images, dependency caches and no-model environment
   proofs. This reset does not authorize deleting future failures automatically.
3. Use the existing six-arm live canary to verify actual contexts, valid usage
   records and functioning official evaluation. **Launch the full suite directly
   with eight workers, regardless of canary task success.** Preserve unsuccessful
   outcomes; never retry until all arms succeed. Canary outcomes do not fill
   full-suite coverage, and ideal has no extra serial launch gate.
4. Edit, prepare and run on the local machine `r760-ssd`. Keep code on the
   system disk and images, caches, results, logs and temporary files under
   `/mnt/data/agenttx-runtime`. Use the dedicated Docker socket
   `unix:///run/agenttx-docker/docker.sock`, reachable domestic mirrors, and
   pinned verifier images. A remote machine is only a reference-code source.
5. Freeze source and register the per-policy context map, model, effort, seed,
   task list, verifier and execution limits before launch. Count new results
   from zero. Report `main + replay` tokens, means and paired-success counts
   together with success rates and failures. Compare compact Causal with the
   registered full controls; report clean and ideal as reference arms.
6. Refresh progress/comparisons every minute and review runtime health and
   effects every half-hour. Keep new failures and timeouts visible; never retry
   based on task success or the size of a token advantage. Infrastructure errors
   stop new dispatches for diagnosis. Keep `agent.md` and `agent.zh-CN.md` aligned.

Full-suite launch and formal eligibility are separate: the registered hard-token-budget,
prompt-accounting and cross-harness gates still determine `formal_eligible`.

## 17. System Breakdown

Run the Breakdown on the same 400 Motivation manifests so every component sees
the same complete trajectory. Compare:

| configuration | removed mechanism | expected observable failure/cost |
|---|---|---|
| Full AgentTX | none | reference |
| No READ/NEGATIVE capture | automatic producer--consumer discovery | invalid descendants survive |
| Path-only identity | alias/object tracking | alias-related corruption survives or recovery fails closed |
| Temporal history only | object-aware selective reconstruction | valid interleaved versions are lost |
| Workspace-only rollback | unified context closure and rewind | stale tool results disagree with files |
| One-shot worker | persistent execution | same semantics, higher per-step latency |
| Full context handoff | compact recovery handoff | same semantics, larger prompt/token cost |

The first four rows explain correctness and retention. The final two explain
engineering cost. Do not combine a semantically incorrect ablation with latency
averages as if it were a performance optimization. Report success, invalid
removal, valid retention, context agreement, tokens, and wall time for every
row.

## 18. Appendix Correctness and Interrupted-Recovery Tests

Correctness evidence belongs in the appendix and artifact, with a compact
summary in the main text.

### 18.1 Semantic matrix

Cover ordinary reads/writes/deletes, negative lookup followed by creation,
same-path multi-version updates, directory ancestry, rename, symlink, supported
hard-link groups, fd-relative operations, multiple fault roots, repeated
rollback, reload, and the fail-closed identity boundary. For each case assert:

1. every selected filesystem effect disappears;
2. every supported independent effect remains;
3. every invalid tool-result span disappears;
4. surviving messages and files refer to the same active step set;
5. a second identical recovery is a no-op.

### 18.2 Interrupted-recovery experiment

This experiment targets interruption during rollback, not publication to the
user workspace. Inject `SIGKILL` at the following recovery phases:

1. after the unified recovery plan is recorded but before reconstruction;
2. after a fresh upper generation is built but before it becomes active;
3. immediately after workspace generation swap but before context rewrite;
4. after context rewrite but before metadata persistence;
5. after persistence but before the recovery response reaches the agent.

After each interruption, reload the same session and resume recovery. The only
acceptable observable states are the complete pre-recovery pair or the complete
post-recovery pair. A workspace/context mixture, resurrected invalid message,
lost independent effect, or duplicated recovery action is a failure. Run at
least 20 injections per phase and publish every phase result. This test is a
required gate before claiming crash-safe synchronized rollback.

## 19. Other Overheads

Measure costs not captured by task success or total wall time:

1. dependency tracing overhead by command class and trace volume;
2. history storage amplification versus repository size, changed bytes, and
   trajectory length;
3. ledger and message-index memory growth versus tool-step count;
4. closure planning and reconstruction latency versus closure size, affected
   path count, alias-group size, and number of fault roots;
5. context reconstruction time and prompt tokens for full versus compact
   handoff;
6. startup, teardown, reload, and persistent-worker fallback costs.

Report mean, p50, p95, and p99 where repeated measurements exist. Include RSS,
bytes written, snapshot bytes, metadata bytes, and recovery latency rather than
reporting only normalized percentages. Unsupported cells and environment
failures remain visible.

## 20. Global Execution Rules

- Freeze code, prompts, model, reasoning effort, task versions, and verifiers
  before a canonical run.
- Run order is analytical microbenchmark, system-microbenchmark pilot,
  complete system microbenchmark, real-world environment check, full
  SWE-Bench/Terminal-Bench, Breakdown,
  appendix correctness/interruption matrix, then Other Overheads.
- Every result records the Git revision and dirty diff hash, environment,
  manifest hash, model configuration, prompt hash, usage source, and exclusion
  reason.
- Environment failures are excluded from task-success denominators only when
  reported separately and rerun under the identical frozen configuration.
- Never select only favorable cases or combine results from incompatible code,
  prompt, or result schemas.
