#!/usr/bin/env python3
"""Editable Ext4/XFS implementation diagram using the existing design style."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_diagrams as g


def main():
    s = g.canvas('implementation-visual', 2.42)
    v = g.DetailStyle(s)
    v.node(.24, .08, 8.12, .50, 'Lancet',
           state='restored')
    v.node(.24, .94, 8.12, .50, 'Linux OverlayFS')
    v.arrow([(4.30, .58), (4.30, .94)])
    v.node(.24, 1.86, 3.82, .48, 'Ext4: file copy')
    v.node(4.54, 1.86, 3.82, .48, 'XFS: reflink')
    v.arrow([(2.15, 1.44), (2.15, 1.86)])
    v.arrow([(6.45, 1.44), (6.45, 1.86)])

    report = v.report()
    report.update(title='Ext4 and XFS implementations',
                  controller='shared user-space controller over unmodified OverlayFS',
                  ext4='Ordinary file copying.',
                  xfs='Reflink file cloning.',
                  simplification='Four boxes show shared recovery logic and two storage backends.')
    g.finish(s, 'Lancet-Implementation.pptx', 'Lancet-Implementation.pdf',
             'A shared user-space recovery controller uses unmodified Linux '
             'OverlayFS over either Ext4 or XFS. Ext4 uses ordinary file copies, '
             'and XFS uses reflink. Solid arrows show the shared control path; '
             'the dependency graph, rollback selection, and agent integration '
             'remain in the shared controller.', report)


if __name__ == '__main__':
    main()
