#!/usr/bin/env python3
"""Additional editable design figures using the existing diagram style."""
from __future__ import annotations

import json
import sys

sys.dont_write_bytecode = True

import generate_diagrams as g


def dependency_closure():
    s=g.canvas('dependency-closure-visual',6.98)
    v=g.DetailStyle(s)
    # Expose the input/output connections to the overview without repeating
    # filesystem layers, object-version tables, or context projection.
    v.node(.24,.10,3.82,.44,'File accesses / changes')
    v.node(4.54,.10,3.82,.44,'Tool results / arguments')
    v.arrow([(2.15,.54),(2.15,.97)])
    v.arrow([(6.45,.54),(6.45,.97)])

    v.frame(.10,.97,8.40,4.28,'Select and validate S',2,color='blue')
    v.node(.28,1.72,2.45,.38,'User file: f')
    v.node(3.10,1.72,2.20,.38,'Since C0')
    v.node(6.00,1.72,2.10,.38,'B = {E3, E4}',state='root')
    v.arrow([(2.73,1.91),(3.10,1.91)])
    v.arrow([(5.30,1.91),(6.00,1.91)])

    nodes={'E3':(.44,2.75,.72,.36),'E4':(2.64,2.75,.72,.36),
           'E6':(4.84,2.75,.72,.36),'E7':(2.64,3.59,.72,.36),
           'E8':(4.84,3.59,.72,.36),'E5':(7.11,3.16,.72,.36)}
    for name,(x,y,w,h) in nodes.items():
        v.node(x,y,w,h,name,state='root' if name in g.EXAMPLE['file_roots'] else
               'retained' if name=='E5' else 'selected')
    file_edges=g.EXAMPLE['filesystem_edges']
    for a,b in file_edges:
        x,y,w,h=nodes[a];xx,yy,ww,hh=nodes[b]
        if (a,b)==('E3','E6'):
            v.arrow([(x+w/2,y),(x+w/2,2.44),(xx+ww/2,2.44),(xx+ww/2,yy)],
                    dash=True,role='file')
        else:
            v.arrow([(x+w,y+h/2),(xx,yy+hh/2)],dash=True,role='file')
    v.arrow([(.80,3.11),(.80,3.77),(2.64,3.77)],color='red',role='result')
    for x,y,value in [(1.48,3.28,'f1'),(3.68,3.28,'f2'),(3.68,4.16,'p1')]:
        v.label(x,y,.74,value,note=True)
    v.label(.98,4.16,1.58,'report.json',note=True,color='red')
    s.line(6.38,2.44,6.38,4.27,'gray',lw=.40,arrow=False)
    v.label(6.62,2.75,1.64,'Independent',note=True)
    v.label(6.62,3.93,1.64,'Keep g1',note=True,color='green')
    v.node(.28,4.62,5.42,.40,g.SELECTED_LABEL,state='selected')
    s.rect(6.19,4.62,2.06,.40,'blue','white',dash=True,lw=.75)
    v.label(6.215,4.82,2.01,'Residue audit',color='blue')
    v.arrow([(5.70,4.82),(6.19,4.82)])

    # The audited plan hands one frozen S to both recovery components.
    s.line(6.92,5.25,7.32,5.25,'white',lw=2.5,arrow=False)
    s.curved_path([(7.22,5.02),(7.22,5.50),(4.30,5.50),(4.30,5.70)],
                  'black',lw=v.FLOW_PT)
    v.label(7.36,5.39,.66,'clean',note=True,align='left')
    v.label(.64,5.50,2.90,'Apply audited S',note=True)
    v.node(.24,6.05,3.82,.44,'Restore filesystem',state='restored')
    v.node(4.54,6.05,3.82,.44,'Sync agent context',state='restored')
    v.arrow([(4.30,5.70),(2.15,5.70),(2.15,6.05)])
    v.arrow([(4.30,5.70),(6.45,5.70),(6.45,6.05)])
    for x,color,dash,label in [(1.00,'black',False,'Flow'),
                               (3.75,'black',True,'File'),
                               (6.35,'red',False,'Result')]:
        v.arrow([(x,6.77),(x+.53,6.77)],color=color,dash=dash,role='legend')
        v.label(x+.69,6.77,1.22,label,note=True,align='left')
    rows=[['E3','E4'],['E3','E4','E6','E7'],
          ['E3','E4','E6','E7','E8']]
    notes = '''The diagram expands step 2 (Select S) of the overview. File
access/change records and tool-result/argument records enter the controller.
The user reports file f, with reference checkpoint C0 before E3.
The controller selects both later mutations E3 and E4 as roots. E3 writes
f1 and returns report.json, which E7 copies into an argument. E4 reads f1
and overwrites f with f2;
E6 reads f2 and deletes h. E5 independently writes g1 between E4 and E6.
File edges shown are E3->E4, E4->E6, E7->E8, and E3->E6 for overlapping
f history. The result edge E3->E7 crosses the two dependency channels.
Starting from file roots E3 and E4,
file expansion adds E6, result expansion adds E7, and the next file
expansion adds E8. The fixed point is S={E3,E4,E6,E7,E8}; E5 is preserved.
The report does not identify a faulty step. Joint forward expansion uses
both graphs. An isolated audit checks residual commands, file state, and
visible results before model continuation. A clean audit supplies the same
frozen S to filesystem restoration and agent-context synchronization.
The dashed blue node denotes the isolated residue audit.
Filesystem layers, object-version tables, and context projection remain
in the other design figures.
Solid black lines show workflow; dashed black lines show file dependencies;
solid red lines show result reuse. Native PPT and vector exports match.
'''
    return g.finish(s, 'AgentTX-Dependency-Closure.pptx',
                    'AgentTX-Dependency-Closure.pdf', notes, dict(
                        title='From execution evidence to a rollback set',
                        overview_steps=[2],
                        evidence={'file': ['E3', 'f1', 'E4'],
                                  'result': ['E3', 'report.json', 'E7']},
                        filesystem_edges=[list(pair) for pair in file_edges],
                        file_edge_objects=g.EXAMPLE['file_edge_objects'],
                        message_edges=g.EXAMPLE['message_edges'],
                        expansion_sets=rows,
                        recovery_input='user file report', reported_files=['f'],
                        reference_checkpoint='C0 before E3', file_roots=g.EXAMPLE['file_roots'],
                        validation='frozen file-state and command/result residue audit',
                        task_test_used_for_selection=False,
                        selected_steps=g.EXAMPLE['selected_steps'],
                        retained_steps=g.EXAMPLE['retained_steps'],
                        simplification=dict(
                            removed=['OverlayFS before/after state',
                                     'standalone result-reuse chain',
                                     'duplicated expansion-stage boxes'],
                            preserved=['file report and post-checkpoint mutations',
                                       'all file and result edges',
                                       'joint closure and independent E5'],
                            connections=['file accesses/changes and result/argument inputs',
                                         'isolated residue audit',
                                         'shared S to filesystem and context recovery']),
                        routing_clearance=dict(dependency_bypass_to_root_row_inches=.34),
                        **v.report()))


def context_alignment():
    s = g.canvas('context-alignment-visual', 5.72)
    s.rect(.10, .10, 8.40, .62, 'black', 'white', lw=.85)
    g.heading(s, .28, .23, 3.20, 'Shared invalidation')
    g.text(s, 3.82, .23, 4.28, g.SELECTED_LABEL, 21, 'red',
           bold=True)
    g.line(s, (4.30, .77), (4.30, .82), kind='flow', arrow=False)
    g.curve(s, (4.30,.82), (3.30,.82), (1.60,.82), (1.60,.98), kind='flow')
    g.curve(s, (4.30,.82), (4.95,.82), (6.00,.82), (6.00,.98), kind='flow')

    g.panel(s, .10, 1.04, 3.00, 2.60, 'Filesystem', 1,
            primary=True)
    g.text(s, .32, 1.78, 2.56, 'Restore f, h, p', 19)
    for x, value in zip((.35, 1.30, 2.25), ('f0', 'h0', '∅')):
        g.box(s, x, 2.17, .62, .35, value, state='restored', size=20)
    g.text(s, .33, 2.74, .73, 'Keep', 19, align='left')
    g.box(s, 1.64, 2.71, .80, .35, 'g1', state='retained', size=20)
    g.text(s, .32, 3.20, 2.56, 'Recheck', 19)

    g.panel(s, 3.50, 1.04, 5.00, 2.60, 'Calls + results', 2,
            primary=True)
    turns = g.EXAMPLE['turns']
    for i, turn in enumerate(turns):
        y = 1.87 + .54 * i
        g.text(s, 3.72, y+.025, .75, f'Turn {i+1}', 18,
               align='left')
        for j, name in enumerate(turn):
            g.box(s, 4.52 + .94*j, y, .85, .37, f'{name}/r{name[1:]}',
                  state='selected' if name in g.EXAMPLE['selected_steps']
                  else 'retained', size=18.5)

    g.line(s, (1.60, 3.70), (1.60, 4.00), kind='flow')
    g.line(s, (6.00, 3.70), (6.00, 4.00), kind='flow')
    g.panel(s, .10, 4.06, 8.40, 1.10, 'Next model request', 3,
            primary=True)
    for x, w, value, state in [(.32, 1.85, 'System + task', 'normal'),
                                (2.38, 2.45, 'E1/r1, E2/r2', 'retained'),
                                (5.07, 1.48, 'E5/r5', 'retained'),
                                (6.80, 1.40, 'Notice', 'normal')]:
        g.box(s, x, 4.72, w, .34, value, state=state, size=19)
    g.legend(s, 5.34, restored=True)
    notes = '''The trajectory's turns are (E1,E2,E3), (E4), (E5,E6,E7,E8).
E3 writes f1 and returns a filename. E4 overwrites f with f2. Independent E5
writes g1. E6 reads f2 and deletes h. E7 reuses E3's filename to create p1;
E8 reads p1. S={E3,E4,E6,E7,E8} drives filesystem and context recovery.
The restored filesystem contains f0,g1,h0,absent(p). Projection retains
E1/E2 in the first mixed turn and E5 in the third, omitting the second turn.
Each displayed history cell pairs a call with its result. The next request
includes system/task, E1/r1,E2/r2,E5/r5 and a recovery notice. The original
audit history remains available. This asset remains outside the main paper;
the overview shows the same mixed-turn projection.
'''
    return g.finish(s, 'AgentTX-Context-Alignment.pptx',
                    'AgentTX-Context-Alignment.pdf', notes, dict(
                        title='Filesystem and agent-context alignment',
                        selected_steps=g.EXAMPLE['selected_steps'],
                        retained_steps=g.EXAMPLE['retained_steps'],
                        illustrative_turns=[list(turn) for turn in turns],
                        restored_objects=['f', 'h', 'p'],
                        retained_objects=['g'],
                        next_request=['system/task', 'E1/r1', 'E2/r2',
                                      'E5/r5', 'recovery notice'],
                        audit_history_preserved=True))


if __name__ == '__main__':
    reports = [dependency_closure(), context_alignment()]
    print(json.dumps([dict(title=r['title'], native_shapes=r['native_shapes'],
                           text_fit_issues=r['text_fit_issues'],
                           bounds_issues=r['bounds_issues']) for r in reports],
                     indent=2))
