# AgentTX on YoloFS

This directory contains the isolated YoloFS port. It imports the existing causal
ledger, recovery selection and conversation implementation without changing the
OverlayFS backend. YoloFS must be loaded on the machine running integration tests.

The completed migration and evidence are described in [PORTING_REPORT.md](PORTING_REPORT.md).
The final [RQ5 results](results/rq5-final/RESULTS.md) include all eight controls.

The storage API uses native YoloFS snapshots and commits. The recovery runtime
keeps an initial object index and immutable content deltas, so command boundaries
do not require native snapshots. Selected historical objects are restored through
the mounted filesystem; native journal replay, remount and commit see those same
changes. No custom tree is injected without a matching journal description.

Run with both this directory and the project's `src` directory on `PYTHONPATH`.

```python
from pathlib import Path
from agenttx_yolofs.runtime import YoloAgentTX

tx = YoloAgentTX.begin(Path('/path/to/project'), Path('/path/to/session'))
try:
    faulty = tx.call(['sh', '-c', 'printf wrong >a'])
    tx.call(['sh', '-c', 'printf retained >b'])
    tx.call(['sh', '-c', 'cat a >c'])
    tx.rollback_causal_from(faulty.step_id)  # restore a, remove c, keep b
    tx.commit()
finally:
    tx.close(destroy=True)
```

`call()` records tool/result spans as well as filesystem effects. The inherited
`run_tool()` and `run_verifier()` interfaces remain available for harnesses that
manage conversation spans themselves. `fork()` creates another real YoloFS mount;
the existing verifier-based recovery planner can probe it without changing its
parent session. `load()` resumes persisted history and completes an interrupted
recovery intent.

## Validation

The integration suite uses the real kernel filesystem and real shell commands:

```bash
PYTHONPATH="$PWD/ports/yolofs:$PWD/src" python3 ports/yolofs/tests/integration.py --output /tmp/yolofs-integration.json
```

Fifteen tests cover file and context dependencies, earlier valid versions within
one native generation, negative lookups, multiple roots, directory/file rename,
symlinks, permissions, timestamps, xattrs, background-process retention,
interrupted recovery, timeout cleanup, load/resume, isolated verifier probes,
native snapshot history, remount and publication. These tests do not use a model.

## RQ5

The RQ5 runner uses the original Rust workload bodies and the prior v14 storage
controls on the dedicated guest. `scripts/deploy_guest.py` copies the port and
parent Python modules into a separate deployment and verifies their SHA-256
hashes. The unchanged benchmark adapters are copied into `bench_support/`.

```bash
python3 ports/yolofs/scripts/deploy_guest.py
# On the dedicated guest:
python3 /opt/rq5/agenttx-yolofs-port/port/scripts/rq5.py --output /hostshare/yolofs-port-20261115/rq5-final --root /var/lib/agenttx-yolofs-port-final --repeats 10
# On the guest, after complete.json exists:
python3 /opt/rq5/agenttx-yolofs-port/port/scripts/analyze_rq5.py /hostshare/yolofs-port-20261115/rq5-final
```

Use new output and experiment-root directories for each replication.

RQ5 measures storage APIs, with the same separation from dependency capture and
fault localization as the previous experiment. Recovery semantics are verified
by the integration suite above. All eight controls execute unchanged file
creation/overwrite/rename/unlink workloads; four snapshot-capable controls also
execute explicit depth-1/10/30 snapshots. Native YoloFS and the port hold matching
persistent control FDs. All measured samples, phases and mount evidence are kept.

## Current implementation scope

The port preserves YoloFS's native operation support, including its lack of
hard-link creation. Publication commits the whole active session; partial
frontier publication is not implemented. Background processes are retained,
while process memory and existing descriptors are not selectively reconstructed.
Native snapshot/travel FD gates remain in place.

Recovery history currently walks workspace metadata after a command and hashes
changed files. This cost belongs to the recovery runtime and is separate from
the storage-only RQ5 measurements. Initial indexing and incremental immutable
objects provide logical recovery points without a full native snapshot for
every command. Metadata-only isolation follows the native filesystem; the port
does not add a separate kernel metadata implementation.
