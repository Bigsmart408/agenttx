# RQ5 storage backends

Regenerate both single-column figures from the repository root:

```bash
python motivation/plot_rq5_backends.py
```

The input is `ports/yolofs/results/rq5-final/summary.json`, from the completed
520-session campaign. Each plotted group contains all ten measured repetitions.
Bars show mean complete storage-workflow time (`lifecycle_ns`); error bars are
95% bootstrap intervals with 10,000 resamples and a fixed seed.
`figure-data.json` records every plotted mean, interval, and the input SHA-256.

- OverlayFS: Ext4, OverlayFS, and Lancet/OverlayFS for file commits;
  OverlayFS and Lancet/OverlayFS for checkpoint workflows.
- YoloFS: Ext4, YoloFS, and Lancet/YoloFS for file commits;
  YoloFS and Lancet/YoloFS for checkpoint workflows.

Both OverlayFS variants enable metacopy and redirect_dir. Both YoloFS variants
use native checkpoints through in-process ioctl calls with persistent control FDs.
Commit tests use 10,000 files of 4 KiB without intermediate checkpoints. Checkpoint
tests overwrite ten seed files per generation with 100 target files and explicitly
request 1, 10, or 30 checkpoints. The timed workflow includes initial-file preparation,
setup, operations, publication, and session cleanup; validation is outside timing.
These are storage API measurements; they do not time dependency tracking,
per-command history capture, or selective recovery. Ext4 has no checkpoint bar.

PDF/PNG figures are copied to `paper/img/` for inclusion in the paper.
The original campaign, including the try controls, remains in
`ports/yolofs/results/rq5-final/`.
