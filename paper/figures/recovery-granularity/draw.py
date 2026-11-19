#!/usr/bin/env python3
"""One Figure-1-style trajectory with distinct recovery-scope colors and lines."""
from pathlib import Path
import json
import re
import shutil
import sys

sys.dont_write_bytecode = True
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / "design-workflow"))
import generate_diagrams as style
from pptx.enum.dml import MSO_LINE_DASH_STYLE


SCOPES = {
    "effects": dict(label="Selected effects", pattern="1.2 3.0",
                    color="7A3E9D", ppt=MSO_LINE_DASH_STYLE.SQUARE_DOT),
    "temporal": dict(label="Temporal checkpoint", pattern="4 3",
                     color="D55E00", ppt=MSO_LINE_DASH_STYLE.DASH),
    "turn": dict(label="Turn checkpoint", pattern="7 3 1.2 3",
                 color="0072B2", ppt=MSO_LINE_DASH_STYLE.DASH_DOT),
    "branch": dict(label="Whole branch", pattern="11 4",
                   color="475569", ppt=MSO_LINE_DASH_STYLE.LONG_DASH),
}
SCOPE_LINE_WIDTH = 2.0
CALL_LINE_WIDTH = 1.1
FAULT_LINE_WIDTH = 2.0
TURN_RULE_LINE_WIDTH = .8
DEPENDENCY_LINE_WIDTH = 1.4


def set_pattern(s, kind):
    scope = SCOPES[kind]
    s.slide.shapes[-1].line.dash_style = scope["ppt"]
    s.svg[-1] = re.sub(r' stroke-dasharray="[^"]*"', "", s.svg[-1])
    s.svg[-1] = s.svg[-1].replace(
        "/>", f' stroke-dasharray="{scope["pattern"]}"/>')


def boundary(s, kind, x, y, w, h):
    if kind == "effects":
        # Leave a gap around the dependency stem so it does not cross the outline.
        stem_x, gap = 4.88, .10
        s.path([(stem_x-gap, y), (x, y), (x, y+h),
                (x+w, y+h), (x+w, y), (stem_x+gap, y)],
               SCOPES[kind]["color"], dash=True, lw=SCOPE_LINE_WIDTH,
               radius=0, arrow=False)
    else:
        s.rect(x, y, w, h, SCOPES[kind]["color"], "white", dash=True,
               lw=SCOPE_LINE_WIDTH)
    set_pattern(s, kind)


def legend(s, kind, x, y):
    style.line(s, (x, y+.15), (x+.57, y+.15), color=SCOPES[kind]["color"],
               dash=True, arrow=False, lw=SCOPE_LINE_WIDTH)
    set_pattern(s, kind)
    style.text(s, x+.73, y, 3.05, SCOPES[kind]["label"], 20,
               align="left")


def draw():
    style.OUT = style.d.OUT = OUT
    s = style.canvas("recovery-granularity", 4.02)
    positions = [.32 + 1.35*i for i in range(6)]
    for left, label in [(1.505, "Turn 1"), (4.205, "Turn 2"),
                        (6.905, "Turn 3")]:
        style.text(s, left-1.20, .08, 2.40, label, 21, bold=True)
        style.line(s, (left-1.20, .53), (left+1.20, .53),
                   arrow=False, lw=TURN_RULE_LINE_WIDTH)

    # Each scope has its own color and dash pattern on the same trajectory.
    boundary(s, "branch", .10, .70, 8.40, 1.97)
    boundary(s, "turn", 2.82, .88, 5.56, 1.61)
    boundary(s, "temporal", 4.15, 1.06, 4.11, 1.25)
    boundary(s, "effects", 4.25, 1.50, 1.26, .63)

    calls = ["write B", "write C", "write D", "write A", "write E", "read A"]
    for i, (x, label) in enumerate(zip(positions, calls)):
        state = "candidate" if i == 3 else "selected" if i == 5 else "retained"
        style.box(s, x, 1.61, 1.02, .44, label, state=state,
                  size=19, bold=i == 3,
                  lw=FAULT_LINE_WIDTH if i == 3 else CALL_LINE_WIDTH)
        status = "fault" if i == 3 else "affected" if i == 5 else "valid"
        style.text(s, x-.08, 2.77, 1.18, status, 18.5,
                   "red" if i in {3, 5} else "green")
    style.route(s, [(4.88, 1.55), (4.88, 1.25), (7.58, 1.25),
                    (7.58, 1.55)], kind="flow", lw=DEPENDENCY_LINE_WIDTH)

    legend(s, "effects", .60, 3.14)
    legend(s, "temporal", 4.55, 3.14)
    legend(s, "turn", .60, 3.60)
    legend(s, "branch", 4.55, 3.60)

    report = dict(
        title="Recovery granularity on one six-call trajectory",
        calls=calls, turns=[[0, 1], [2, 3], [4, 5]],
        faulty_calls=[3], affected_calls=[3, 5],
        independent_calls=[0, 1, 2, 4], filesystem_edges=[[3, 5]],
        scope_calls=dict(effects=[3], temporal=[3, 4, 5],
                         turn=[2, 3, 4, 5], branch=list(range(6))),
        scope_patterns={key: value["pattern"] for key, value in SCOPES.items()},
        scope_colors={key: "#"+value["color"] for key, value in SCOPES.items()},
        scope_line_width_pt=SCOPE_LINE_WIDTH,
        call_line_width_pt=CALL_LINE_WIDTH,
        fault_line_width_pt=FAULT_LINE_WIDTH,
        turn_rule_line_width_pt=TURN_RULE_LINE_WIDTH,
        dependency_line_width_pt=DEPENDENCY_LINE_WIDTH,
        shared_style="../design-workflow/generate_diagrams.py",
    )
    notes = (
        "Six calls across three turns. write A is faulty, read A is affected, "
        "and all other calls are independent. Four colored outline styles show "
        "selected invocation effects, the suffix after a temporal checkpoint, "
        "the suffix after a turn-boundary checkpoint, and whole-branch abort. "
        "Selected-effects control does not automatically withdraw read A. "
        "The fault is placed inside Turn 2 to make all four scopes distinct."
    )
    result = style.finish(s, "AgentTX-Recovery-Granularity.pptx",
                          "FIG-Recovery-Granularity.pdf", notes, report)
    for ext in ("svg", "png"):
        shutil.copyfile(OUT/f"recovery-granularity.{ext}",
                        OUT/f"FIG-Recovery-Granularity.{ext}")
    for ext in ("pdf", "svg", "png"):
        shutil.copyfile(OUT/f"FIG-Recovery-Granularity.{ext}",
                        OUT.parents[1]/"img"/f"FIG-Recovery-Granularity.{ext}")
    (OUT/"figure-data.json").write_text(json.dumps(report, indent=2)+"\n")
    (OUT/"preview.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Recovery granularity</title>'
        '<style>body{margin:24px;background:white}img{display:block;'
        'width:min(100%,1200px);height:auto;margin:auto}</style>'
        '<img src="FIG-Recovery-Granularity.svg" alt="One tool-call sequence '
        'with four recovery-scope outlines">\n')
    print(f"Generated one trajectory with four outlines; "
          f"{result['native_shapes']} editable objects; no layout issues.")


if __name__ == "__main__":
    draw()
