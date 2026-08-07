# Long Agent workload matrix

Deterministic 64-tool-call trajectory; overhead repeats=1.
Phases: exploration -> modular refactor -> failing CI -> independent docs/config -> repair -> cleanup.

## Overhead

| mode | wall mean (s) | stdev (s) | ms/step | failures | host polluted | ledger steps | read effects |
|---|---:|---:|---:|---:|:---:|---:|---:|
| bare | 3.080787 | 0.0 | 48.137 | 2 | True |  |  |
| per_call_try | 17.145046 | 0.0 | 267.891 | 34 | False |  |  |
| shared_try | 16.427205 | 0.0 | 256.675 | 34 | False |  |  |
| shared_checkpoint | 20.388618 | 0.0 | 318.572 | 2 | False |  |  |
| agenttx_without_read_tracing | 20.563881 | 0.0 | 321.311 | 2 | False | 64 | 15 |
| agenttx_full | 27.993542 | 0.0 | 437.399 | 2 | False | 64 | 1051 |

## Recovery semantics

The recovery prefix stops before the repair. The expected state after causal rollback is:
faulty `lib/formatting.py` absent, derived `build/format-report.txt` absent, and the three independent docs/config files retained.

| mode | host polluted before recovery | causal retention correct | check rc | rollback targets | final rc | note |
|---|:---:|:---:|---:|---|---:|---|
| bare | True | False |  | `` | 0 | host is already modified before recovery; no causal rollback |
| per_call_try | False | False |  | `` |  | each call is isolated, so the prefix and independent edits never form one state |
| shared_try | False | False |  | `` |  | shared overlay preserves the prefix but recovery is whole-session discard |
| shared_checkpoint | False | False | 1 | `whole-session` |  | full checkpoint rollback removes independent docs/config along with the fault |
| agenttx_without_read_tracing | False | False | 1 | `[27]` | 0 | read tracing disabled: derived build/format-report.txt is retained |
| agenttx_full | False | True | 0 | `[27, 29, 30]` | 0 | read tracing links the derived artifact to the faulty formatter |

The comparison is a VM-local systems measurement, not a universal speed claim.
Per-call try is intentionally included as a continuity baseline; external systems remain outside this runnable matrix.
