# Complete multi-length runtime comparison

All motivation-sweep modes plus the AgentTX series on the same equal-step
mixed workload at 32/64/96/128 calls (2 repeats per cell).  This is the
dataset behind evaluation Table `tab:runtime` (64-call slice) and
`tab:scaling` (multi-step sweep) in `paper/main.tex`.

| mode | 32 (ms/step) | 64 (ms/step) | 96 (ms/step) | 128 (ms/step) |
|---|---:|---:|---:|---:|
| bare | 8.554 | 8.384 | 8.11 | 8.036 |
| try | 1659.92 | 1684.921 | 1670.777 | 1664.205 |
| YoloFS | 1636.353 | 1659.688 | 1638.833 | 1631.07 |
| BranchFS | 1663.411 | 1644.857 | 1649.61 | 1664.29 |
| Crab | 127.82 | 74.554 | 56.488 | 47.301 |
| DeltaBox | 129.558 | 75.495 | 56.849 | 47.629 |
| agenttx_without_read_tracing | 122.395 | 72.801 | 56.109 | 48.719 |
| agenttx_full | 133.539 | 90.941 | 72.922 | 65.063 |

Existing-system rows (bare/try/YoloFS/BranchFS/Crab/DeltaBox) are the
`motivation_existing_scaling.csv` rows; AgentTX rows were measured with
`motivation/bench_existing_systems.py --modes agenttx_without_read_tracing agenttx_full`.
