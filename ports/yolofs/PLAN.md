# YoloFS migration acceptance and evidence

The requested outcome is an isolated subdirectory containing a working YoloFS
port that can selectively roll back and complete the RQ5 experiment.

| Requirement | Evidence |
|---|---|
| Isolated implementation; original backend preserved | `agenttx_yolofs/`; parent runtime is imported and subclassed |
| Real YoloFS execution | Integration tests use mounted native kernel filesystem and traced shell commands |
| Select affected calls and retain independent filesystem work | File/read, negative-lookup, multiple-root and same-generation integration cases |
| Synchronize agent context with the restored filesystem | Joint file/context closure and active-span checks |
| Work across native snapshots and subsequent execution | Snapshot-boundary, resume, remount and continuation cases |
| Preserve the restored state during commit | Native commit followed by host contents, metadata and xattr checks |
| Verifier-based candidate validation uses YoloFS | Independent real mounts in `fork()` and successful inherited recovery planning |
| Commands do not imply a native snapshot | Native journal assertion in the joint dependency case |
| RQ5 original workloads and controls | `scripts/rq5.py`, unchanged `bench_support/`, run manifest and mount records |
| All planned RQ5 runs and histories verified | `complete.json`, all session records, and `scripts/analyze_rq5.py` |
| Reproducible results retained | Source hashes, raw result hashes, complete distributions and report |

The migration was validated on the same Ext4 guest used by the existing RQ5
campaign. It does not change the main paper or replace previous experiment data.

Completed evidence: [15/15 integration tests](results/integration-v7.json),
[520/520 final RQ5 sessions](results/rq5-final/complete.json),
[full measurement report](results/rq5-final/RESULTS.md), and
[source/process/mount verification](results/final-verification.json).
