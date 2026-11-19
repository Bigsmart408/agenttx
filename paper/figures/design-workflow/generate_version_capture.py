#!/usr/bin/env python3
"""Editable version-history and OverlayFS checkpoint diagram."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_diagrams as g


def main():
    s = g.canvas('version-capture-visual', 6.10)
    v = g.DetailStyle(s)
    v.frame(.10, .08, 8.40, 2.22, 'Object history via changed entries', 1)
    entries = [('Base', 'L', 'f0, g0, h0'),
               ('E3', 'D3', 'f1'), ('E4', 'D4', 'f2'),
               ('E5', 'D5', 'g1'), ('E6', 'D6', 'WH(h)')]
    xs = [.28, 1.94, 3.60, 5.26, 6.92]
    for x, (call, identifier, value) in zip(xs, entries):
        v.label(x, .96, 1.40, call, note=True)
        v.node(x+.04, 1.14, 1.32, .40, identifier)
        v.node(x+.04, 1.88, 1.32, .34, value,
               state='retained' if call == 'E5' else 'normal')
        v.arrow([(x+.70, 1.54), (x+.70, 1.88)], dash=True, role='file')
    for left, right in zip(xs, xs[1:]):
        v.arrow([(left+1.36, 1.34), (right+.04, 1.34)])

    v.frame(.10, 2.55, 8.40, 2.90, 'Explicit checkpoints via retained mounts', 2)
    v.label(.31, 3.45, 2.50, 'After E5', note=True)
    v.node(.31, 3.75, 2.50, .43, 'U0: f2, g1')
    v.node(.31, 4.38, 2.50, .40, 'Lower L')
    v.label(3.16, 3.69, 1.34, 'RO + pin', note=True)
    v.arrow([(2.81, 3.965), (4.91, 3.965)])
    v.label(2.86, 4.22, 2.00, 'Detach + mount', note=True)
    v.label(4.91, 3.45, 3.14, 'Continue in merged view', note=True)
    v.node(4.91, 3.75, 3.14, .43, 'U1: empty (RW)', state='restored')
    v.node(4.91, 4.40, 3.14, .43, 'U0: f2, g1 (RO)')
    v.node(4.91, 4.98, 3.14, .34, 'Lower L')
    v.node(.31, 4.96, 2.50, .36, 'Checkpoint C1')
    v.arrow([(2.81, 5.14), (3.54, 5.14), (3.54, 4.615), (4.91, 4.615)],
            dash=True, role='file')

    v.label(.18, 5.83, 8.24, 'Release retained mounts at commit / close', note=True)
    report = v.report()
    report.update(title='Incremental object history and retained-mount checkpoints',
                  history=['base L', 'D3=f1', 'D4=f2', 'D5=g1', 'D6=WH(h)'],
                  native_checkpoints_per_call=False,
                  explicit_checkpoint='freeze U0, retain RO mount, continue with empty U1',
                  release='commit or close', kernel_changes=False,
                  routing_clearance=dict(history_to_value_inches=.34,
                      history_column_gap_inches=.34,
                      checkpoint_to_lower_inches=.18,
                      mount_annotations_separate_from_association=True))
    g.finish(s, 'Lancet-Version-Capture.pptx', 'Lancet-Version-Capture.pdf',
             'Object history uses the shared E3/E4/E5/E6 example. Parent references '
             'and changed entries retain versions without cloning full history '
             'directories. Independent E5 supplies g1. Explicit storage checkpoints '
             'freeze U0 and retain the old read-only mount before detaching it. '
             'A fresh U1 continues execution above U0 and L. All references are '
             'released at commit/close. Solid arrows show flow and parent links; '
             'dashed arrows associate file state with history or checkpoint records.', report)


if __name__ == '__main__':
    main()
