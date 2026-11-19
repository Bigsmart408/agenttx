#!/usr/bin/env python3
"""Motivation example, using the same native/vector style as the design figures."""
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / "design-workflow"))
import generate_diagrams as style


def draw():
    style.OUT = style.d.OUT = OUT
    s = style.canvas("execution-vs-dependency-draft", 3.20)

    # Turn headings group the six calls without adding another filled panel.
    for x, label in [(1.56, "Turn 1"), (4.30, "Turn 2"), (7.04, "Turn 3")]:
        style.text(s, x-1.20, .08, 2.40, label, 21, bold=True)
        style.line(s, (x-1.20, .53), (x+1.20, .53), arrow=False, lw=.45)
    # The single boundary removes the affected calls and all valid calls alike.
    s.rect(.10, .76, 8.40, 1.87, "black", "white", dash=True, lw=1.0)
    for x in [2.93, 5.67]:
        style.line(s, (x, 1.56), (x, 2.55), color="light",
                   dash=True, arrow=False, lw=.55)
    calls = ["write A", "write B", "write C", "read A", "write D", "write E"]
    for i, label in enumerate(calls):
        x = .305 + 1.37*i
        state = "candidate" if i == 0 else "selected" if i == 3 else "retained"
        style.box(s, x, 1.70, 1.14, .44, label, state=state, size=19,
                  bold=i == 0)
        status = "fault" if i == 0 else "affected" if i == 3 else "valid"
        style.text(s, x-.03, 2.24, 1.20, status, 18.5,
                   "red" if i in {0, 3} else "green")

    style.text(s, .90, .92, 4.06, "Error propagates through A", 18.5)
    style.route(s, [(.875, 1.64), (.875, 1.35), (4.985, 1.35),
                    (4.985, 1.64)], lw=.85)
    style.text(s, .20, 2.80, 8.20, "Roll back all 6 calls", 20)

    notes = """Six calls execute across three turns. The first call writes an
incorrect value to A, and the fourth call reads that value. The other four
calls write independent files B, C, D and E. Red marks the faulty call and
its dependent read; green marks valid independent work. The dashed whole-
execution rollback boundary discards all six calls, including those four
valid writes. Shared drawing primitives keep typography, semantic colors,
node borders, rounded paths and arrowheads aligned with the overview and
implementation figures. Every object in the PPT is native and editable.
"""
    report = style.finish(
        s, "Lancet-Motivation-Rollback-Example.pptx",
        "FIG-Motivation-Rollback-Example.pdf", notes,
        dict(title="Whole-execution rollback discards independent work",
             calls=calls, turns=[[0, 1], [2, 3], [4, 5]],
             faulty_calls=[0], affected_calls=[0, 3],
             independent_calls=[1, 2, 4, 5], rollback_calls=list(range(6)),
             filesystem_edges=[[0, 3]],
             shared_style="../design-workflow/generate_diagrams.py"))

    # Preserve the original source-directory preview names and paper entry point.
    shutil.copyfile(OUT/"FIG-Motivation-Rollback-Example.pdf",
                    OUT/"execution-vs-dependency-draft.pdf")
    for ext, source in [("pdf", "FIG-Motivation-Rollback-Example.pdf"),
                        ("png", "execution-vs-dependency-draft.png")]:
        shutil.copyfile(OUT/source,
                        OUT.parents[1]/"img"/f"FIG-Motivation-Rollback-Example.{ext}")
    print(f"Figure 1: {report['native_shapes']} editable objects; "
          f"{report['body_font_at_paper_width_pt']} pt body at column width; "
          "no text-fit or bounds issues.")


if __name__ == "__main__":
    draw()
