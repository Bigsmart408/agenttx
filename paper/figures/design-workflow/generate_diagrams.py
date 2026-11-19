#!/usr/bin/env python3
"""Native PPT diagrams and matching vectors, styled after YoloFS (SOSP '26)."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile

sys.dont_write_bytecode = True

from PIL import Image
from pptx import Presentation
from pptx.util import Inches
import drawing as d


ROOT = Path(__file__).resolve().parents[3]
OUT = d.OUT
WIDTH = 8.6
BODY = 19
SMALL = 18.5
# Line meaning is shared by the native slides and vector exports.
LINE_STYLES = dict(
    flow=dict(color='black', lw=1.35, dash=False),
    file=dict(color='black', lw=1.35, dash=True),
    result=dict(color='red', lw=1.35, dash=False),
    mapping=dict(color='gray', lw=.60, dash=False),
    divider=dict(color='gray', lw=.40, dash=False),
)
EXAMPLE = dict(
    name='shared-eight-call-trajectory',
    steps={
        'E1': 'inspect the baseline',
        'E2': 'inspect the baseline',
        'E3': 'write f1 and return report.json',
        'E4': 'read f1 and overwrite f with f2',
        'E5': 'independently write g1 between affected calls',
        'E6': 'read f2 and delete h',
        'E7': 'reuse report.json and create p1 at that path',
        'E8': 'read p1',
    },
    reported_files=['f'], reference_checkpoint='C0 before E3',
    file_roots=['E3','E4'], selected_steps=['E3','E4','E6','E7','E8'],
    retained_steps=['E1','E2','E5'],
    filesystem_edges=[['E3','E4'],['E4','E6'],['E7','E8'],['E3','E6']],
    file_edge_objects=['f1','f2','p1','f history'], message_edges=[['E3','E7']],
    objects=['f','g','h','p'], lower=['f0','g0','h0','∅'],
    upper_before=['f2','g1','WH','p1'], upper_after=['∅','g1','∅','∅'],
    merged_before=['f2','g1','∅','p1'], merged_after=['f0','g1','h0','∅'],
    before_images=[['f','E3'],['h','E6'],['p','E7']],
    turns=[['E1','E2','E3'],['E4'],['E5','E6','E7','E8']],
)
SELECTED_LABEL = 'S = {E3, E4, E6–E8}'
d.C.update(black='000000', gray='707070', light='B0B0B0', white='FFFFFF',
           blue='0066CC', pale='F3F7FC', purple='000000', purpale='F5F5F5',
           green='008000', greenpale='E6FFE6', red='8C0000', redpale='FFEBEB')
REFERENCE = dict(
    paper="Don't Let AI Agents YOLO Your Files: Information and Control in Agent-Native Filesystems",
    venue="SOSP 2026", version="arXiv:2604.13536v5", figures=[1, 2, 7],
    url='https://arxiv.org/pdf/2604.13536v5',
    use='Thin black structure, compact aligned blocks, circled operations, semantic red/green/blue accents.')


def canvas(name, height):
    d.W, d.H = WIDTH, height
    d.prs = Presentation()
    d.prs.slide_width, d.prs.slide_height = Inches(d.W), Inches(d.H)
    d.prs.core_properties.author = 'Lancet'
    d.prs.core_properties.subject = 'Dependency selection and synchronized filesystem/context recovery'
    d.issues.clear()
    return d.Canvas(name)


def text(s, x, y, w, value, size=BODY, color='black', *, h=.33, bold=False, align='center'):
    s.text(x, y, w, h, value, size, color, bold=bold, align=align)


def box(s, x, y, w, h, value='', *, state='normal', size=BODY, bold=False, lw=None):
    stroke, fill, dashed = {
        'normal': ('black', 'white', False),
        'retained': ('green', 'greenpale', False),
        'selected': ('red', 'redpale', True),
        'root': ('red', 'redpale', False),
        'restored': ('blue', 'white', False),
        'empty': ('light', 'white', True),
    }[state]
    if lw is None:
        lw = 1.6 if state == 'root' else .65
    s.rect(x, y, w, h, stroke, fill, dash=dashed, lw=lw)
    if value:
        th = size * 1.19 * (value.count('\n') + 1) / 72
        text(s, x+.025, y+(h-th)/2, w-.05, value, size,
             'red' if state in {'selected', 'root'} else
             'green' if state == 'retained' else 'blue' if state == 'restored' else
             'gray' if state == 'empty' else 'black', h=th+.015, bold=bold)


def line(s, p, q, *, kind=None, color=None, dash=None, arrow=True, lw=None):
    kind = kind or ('divider' if not arrow else 'result' if dash else 'file')
    style = dict(LINE_STYLES[kind])
    style.update({k: v for k, v in dict(color=color, dash=dash, lw=lw).items()
                  if v is not None})
    s.line(*p, *q, style['color'], dash=style['dash'], arrow=arrow, lw=style['lw'])


def route(s, points, *, kind=None, color=None, dash=None, lw=None, radius=.055):
    kind = kind or ('result' if dash else 'file')
    style = dict(LINE_STYLES[kind])
    style.update({k: v for k, v in dict(color=color, dash=dash, lw=lw).items()
                  if v is not None})
    s.path(points, style['color'], dash=style['dash'], lw=style['lw'], radius=radius)


def curve(s, start, control1, control2, end, *, kind='flow'):
    style = LINE_STYLES[kind]
    s.curve(start, control1, control2, end, style['color'],
            dash=style['dash'], lw=style['lw'])


def smooth_arrow(s, start, segments, tip, *, kind='flow'):
    style = LINE_STYLES[kind]
    s.smooth_arrow(start, segments, tip, style['color'],
                   dash=style['dash'], lw=style['lw'])


def heading(s, x, y, w, value, number=None, size=21):
    if number is not None:
        s.circle(x+.13, y+.155, .13, 'black', 'black', lw=.8)
        text(s, x+.01, y+.008, .24, str(number), 17.5, 'white', h=.30)
        x += .39
        w -= .39
    text(s, x, y, w, value, size, h=.36, bold=True, align='left')


def edge(s, nodes, a, b, *, result=False, vertical=False):
    x, y, w, h = nodes[a]
    xx, yy, ww, hh = nodes[b]
    start = (x+w/2, y+h+.045) if vertical else (x+w+.045, y+h/2)
    end = (xx+ww/2, yy-.045) if vertical else (xx-.045, yy+hh/2)
    line(s, start, end, kind='result' if result else 'file')


def panel(s, x, y, w, h, value, number, *, primary=False, dashed=False):
    s.rect(x, y, w, h, 'black' if primary and not dashed else 'gray', 'white',
           dash=dashed, lw=.85 if primary and not dashed else .60)
    line(s, (x+.14, y+.52), (x+w-.14, y+.52), kind='divider', arrow=False)
    heading(s, x+.14, y+.10, w-.28, value, number)


def legend(s, y, *, restored=False):
    entries = [('selected','Selected'), ('retained','Retained')]
    if restored:
        entries.append(('restored','Restored'))
    positions = [1.03,3.77,6.39] if restored else [2.00,4.85]
    for x,(state,label) in zip(positions,entries):
        box(s, x, y+.035, .21, .21, state=state)
        text(s, x+.34, y, 1.60, label, SMALL, align='left')


def line_legend(s, y):
    for x, kind, label in [(1.00, 'flow', 'Flow'),
                            (3.75, 'file', 'File'),
                            (6.35, 'result', 'Result')]:
        line(s, (x, y+.15), (x+.53, y+.15), kind=kind)
        text(s, x+.69, y, 1.22, label, SMALL, align='left')


class DetailStyle:
    """Compact detail panels with the overview's type and arrow geometry."""
    NODE_PT=21
    NOTE_PT=18
    FLOW_PT=1.60
    HEAD_LENGTH_PT=9.0
    HEAD_WIDTH_PT=7.2

    def __init__(self,s):
        self.s=s
        self.labels=[]
        self.arrows=[]

    def label(self,x,cy,w,value,*,note=False,color='black',bold=False,align='center'):
        from PIL import ImageFont
        size=self.NOTE_PT if note else self.NODE_PT
        font=ImageFont.truetype(d.fontpaths[bold],round(size*4))
        bounds=font.getbbox(value if value.isdigit() else 'Ag',anchor='ls')
        baseline=cy-(bounds[1]+bounds[3])/8/72
        text(self.s,x,baseline-size*.94/72,w,value,size,color,
             h=size*1.23/72,bold=bold,align=align)
        self.labels.append(dict(text=value,size_pt=size,bold=bold,
            family='Cambria Math' if value=='∅' else d.FONT))

    def frame(self,x,y,w,h,title,number,*,color='gray',dash=False):
        self.s.rect(x,y,w,h,color,'white',dash=dash,lw=.65)
        heading_color='black' if color=='gray' else color
        if number is not None:
            self.s.circle(x+.28,y+.28,.125,heading_color,heading_color,lw=.60)
            self.label(x+.155,y+.28,.25,str(number),note=True,color='white')
        inset=.52 if number is not None else .16
        self.label(x+inset,y+.28,w-inset-.16,title,color=heading_color,bold=True,align='left')
        self.s.line(x+.17,y+.57,x+w-.17,y+.57,'gray',lw=.40,arrow=False)

    def node(self,x,y,w,h,value,*,state='normal'):
        color={'normal':'black','muted':'gray','empty':'light','root':'red','selected':'red',
               'retained':'green','restored':'blue'}[state]
        self.s.rect(x,y,w,h,color,'white',dash=state=='selected',
                    lw=1.60 if state=='root' else .75)
        if value:
            self.label(x+.025,y+h/2,w-.05,value,color='gray' if state=='empty' else color)

    def arrow(self,points,*,color='black',dash=False,role='flow'):
        from math import hypot
        points=list(points)
        tip=points[-1]
        dx,dy=tip[0]-points[-2][0],tip[1]-points[-2][1]
        length=hypot(dx,dy)
        head_length=self.HEAD_LENGTH_PT/72
        if length<=head_length+.02:
            raise ValueError('Connector needs room for the standard arrowhead')
        ux,uy=dx/length,dy/length
        base=(tip[0]-ux*head_length,tip[1]-uy*head_length)
        self.arrows.append(dict(role=role,color=color,dash=dash,points=list(points),
            shaft_pt=self.FLOW_PT,head_length_pt=self.HEAD_LENGTH_PT,
            head_width_pt=self.HEAD_WIDTH_PT))
        points[-1]=base
        if len(points)==2:
            self.s.line(*points[0],*points[1],color,lw=self.FLOW_PT,
                        dash=dash,arrow=False)
        else:
            self.s.curved_path(points,color,lw=self.FLOW_PT,dash=dash)
        half_width=self.HEAD_WIDTH_PT/72/2
        vertices=[tip,(base[0]-uy*half_width,base[1]+ux*half_width),
                      (base[0]+uy*half_width,base[1]-ux*half_width)]
        coords=[(round(x*914400),round(y*914400)) for x,y in vertices]
        builder=self.s.slide.shapes.build_freeform(*coords[0])
        builder.add_line_segments(coords[1:],close=True)
        head=builder.convert_to_shape()
        head.fill.solid();head.fill.fore_color.rgb=d.rgb(color)
        head.line.fill.background()
        head.name='Detail arrowhead '+str(len(self.arrows))
        self.s.svg.append('<polygon data-role="detail-arrowhead" points="'
            +' '.join(f'{x*72:.5f},{y*72:.5f}' for x,y in vertices)
            +'" fill="'+d.col(color)+'"/>')
        self.s.count+=1

    def report(self):
        return dict(
            typography=dict(family=d.FONT,node_pt=self.NODE_PT,
                annotation_pt=self.NOTE_PT,heading_pt=self.NODE_PT,
                bold_headings_only=True,math_symbol_font='Cambria Math',labels=self.labels),
            connectors=dict(shaft_pt=self.FLOW_PT,head_length_pt=self.HEAD_LENGTH_PT,
                head_width_pt=self.HEAD_WIDTH_PT,routed_geometry='smooth cubic Bezier',
                gap_inches=0,arrows=self.arrows),
            line_styles=dict(
                flow=dict(color='black',lw=self.FLOW_PT,dash=False),
                file=dict(color='black',lw=self.FLOW_PT,dash=True),
                result=dict(color='red',lw=self.FLOW_PT,dash=False)),
            line_semantics=dict(
                solid_black_arrow='workflow',dashed_black_arrow='file dependency',
                solid_red_arrow='tool-result reuse',
                dashed_red_node='selected effect or context to withdraw',
                dashed_frame='isolated residue audit',
                solid_frame='normal stage or live application',
                empty_node='absence symbol with a solid gray outline'),
            paper_include_width_fraction=1.0,
            primary_font_at_actual_paper_width_pt=round(self.NODE_PT*3.35/WIDTH,2))


def finish(s, ppt_name, pdf_name, notes, report):
    # Geometry and actual Times glyph widths are checked before writing assets.
    bounds = []
    for shape in s.slide.shapes:
        x, y, w, h = [v / 914400 for v in
                      (shape.left, shape.top, shape.width, shape.height)]
        if min(x, y) < -.002 or x+w > d.W+.002 or y+h > d.H+.002:
            bounds.append(dict(name=shape.name, bounds=[x, y, w, h]))
    if d.issues or bounds:
        raise RuntimeError(json.dumps(dict(text=d.issues, bounds=bounds), indent=2))
    d.prs.core_properties.title = report['title']
    s.save(notes)
    ppt_path = OUT / ppt_name
    d.prs.save(ppt_path)
    # Keep the shell/Office thumbnail consistent with the editable slide.
    preview = Image.open(OUT/f'{s.name}.png').convert('RGB')
    preview.thumbnail((640, 640))
    thumbnail = BytesIO()
    preview.save(thumbnail, format='JPEG', quality=92)
    temporary = ppt_path.with_suffix('.tmp.pptx')
    with ZipFile(ppt_path) as source, ZipFile(temporary, 'w') as target:
        for entry in source.infolist():
            target.writestr(entry, thumbnail.getvalue()
                            if entry.filename == 'docProps/thumbnail.jpeg'
                            else source.read(entry.filename))
    temporary.replace(ppt_path)
    (OUT/f'{s.name}.pdf').replace(OUT/pdf_name)
    report.update(native_shapes=s.count, text_fit_issues=list(d.issues),
                  bounds_issues=bounds, canvas_inches=[d.W, d.H],
                  font=d.FONT, preview_font=d.SVG_FONT, reference=REFERENCE,
                  palette=dict(d.C), line_styles=report.get('line_styles',LINE_STYLES),
                  running_example=EXAMPLE, paper_width_inches=3.35,
                  body_font_at_paper_width_pt=report.get('primary_font_at_actual_paper_width_pt',
                      round(BODY*3.35/d.W, 2)),
                  text_words=sum(len(shape.text.split()) for shape in s.slide.shapes
                                 if shape.has_text_frame),
                  text_characters=sum(len(shape.text) for shape in s.slide.shapes
                                      if shape.has_text_frame))
    (OUT/f'layout-check-{s.name}.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def overview():
    s = canvas('overview-visual', 4.48)
    # Shared grid: every major box/slab is .70 high; all three rows have
    # identical centers, top edges and text baselines across both columns.
    node_h=.70
    rows=[1.20,2.30,3.40]
    left_x,left_w=.62,2.60
    right_x,right_edge=3.93,8.35
    select_w=apply_w=2.00
    apply_x=right_edge-apply_w
    from PIL import ImageFont
    from math import hypot

    # Two type sizes, with weight reserved for the two group headings.
    # Fixed arrow geometry is shared by straight and routed connections.
    NODE_PT=21
    NOTE_PT=18
    FLOW_PT=1.60
    HEAD_LENGTH_PT=9.0
    HEAD_WIDTH_PT=7.2
    type_roles=[]
    arrow_geometry=[]
    connection_geometry=[]
    call_center=left_x+left_w/2
    select_center=right_x+select_w/2
    apply_center=apply_x+apply_w/2
    # Ports lie on the actual node outline, including the .14-inch slant
    # of each layer. Independent inputs and outputs use separate ports.
    ports={
        'tool-bottom': (call_center,.60),
        'merged-top': (call_center,rows[0]),
        'merged-left-upper': (left_x,rows[0]+node_h/2+.13),
        'merged-left-lower': (left_x,rows[0]+node_h/2-.13),
        'merged-right': (left_x+left_w,rows[0]+node_h/2),
        'upper-left': (left_x+.07,rows[1]+node_h/2),
        'upper-right': (left_x+left_w-.07,rows[1]+node_h/2),
        'lower-left': (left_x+.07,rows[2]+node_h/2),
        'record-left': (right_x,rows[0]+node_h/2),
        'record-bottom': (select_center,rows[0]+node_h),
        'select-top': (select_center,rows[1]),
        'select-right': (right_x+select_w,rows[1]+node_h/2),
        'apply-left': (apply_x,rows[1]+node_h/2),
        'apply-bottom': (apply_center,rows[1]+node_h),
        'apply-restore': (apply_x+.44,rows[1]+node_h),
        'context-top': (apply_center,rows[2]),
    }

    def connect(source,target,*,via=(),color='black',arrow=True,lw=None):
        points=[ports[source],*via,ports[target]]
        stroke=FLOW_PT if arrow or lw is None else lw
        connection_geometry.append(dict(source=source,target=target,
            points=list(points),arrow=arrow,color=color,shaft_pt=stroke))
        if arrow:
            tip=points[-1]
            dx,dy=tip[0]-points[-2][0],tip[1]-points[-2][1]
            length=hypot(dx,dy)
            head_length=HEAD_LENGTH_PT/72
            if length <= head_length+.02:
                raise ValueError('Connector needs room for the standard arrowhead')
            ux,uy=dx/length,dy/length
            base=(tip[0]-ux*head_length,tip[1]-uy*head_length)
            points[-1]=base
        if len(points)==2:
            s.line(*points[0],*points[1],color,lw=stroke,arrow=False)
        else:
            s.curved_path(points,color,lw=stroke)
        if not arrow:
            return
        half_width=HEAD_WIDTH_PT/72/2
        vertices=[tip,(base[0]-uy*half_width,base[1]+ux*half_width),
                       (base[0]+uy*half_width,base[1]-ux*half_width)]
        coords=[(round(x*914400),round(y*914400)) for x,y in vertices]
        builder=s.slide.shapes.build_freeform(*coords[0])
        builder.add_line_segments(coords[1:],close=True)
        head=builder.convert_to_shape()
        head.fill.solid();head.fill.fore_color.rgb=d.rgb(color)
        head.line.fill.background()
        head.name='Overview arrowhead '+str(len(arrow_geometry)+1)
        s.svg.append('<polygon data-role="overview-arrowhead" points="'
                     +' '.join(f'{x*72:.5f},{y*72:.5f}' for x,y in vertices)
                     +'" fill="'+d.col(color)+'"/>')
        s.count+=1
        arrow_geometry.append(dict(source=source,target=target,
            tail=ports[source],color=color,tip=tip,base=base,
            length_pt=HEAD_LENGTH_PT,width_pt=HEAD_WIDTH_PT,shaft_pt=stroke))

    def baseline(cy,size,bold=False,sample='Ag'):
        font=ImageFont.truetype(d.fontpaths[bold],round(size*4))
        bounds=font.getbbox(sample,anchor='ls')
        return cy-(bounds[1]+bounds[3])/8/72

    def label(x,cy,w,value,size=NODE_PT,color='black',bold=False,align='center',base=None):
        base=baseline(cy,size,bold) if base is None else base
        y=base-size*.94/72
        text(s,x,y,w,value,size,color,h=size*1.23/72,bold=bold,align=align)
        type_roles.append(dict(text=value,size_pt=size,bold=bold,family=d.FONT))

    def operation(x,y,w,value,number,*,color='black'):
        cy=y+node_h/2
        s.rect(x,y,w,node_h,color,'white',lw=.75)
        s.circle(x+.245,cy,.125,color,color,lw=.6)
        label(x+.12,cy,.25,str(number),NOTE_PT,'white',
              base=baseline(cy,NOTE_PT,sample=str(number)))
        label(x+.48,cy,w-.55,value,NODE_PT,color,align='left',
              base=baseline(cy,NODE_PT))

    def layer(x,y,w,label_text,*,writable):
        # Native slabs share the exact bounding box of the merged-view node.
        h=node_h
        vertices=[(x+.14,y),(x+w,y),(x+w-.14,y+h),(x,y+h)]
        coords=[(round(a*914400),round(b*914400)) for a,b in vertices]
        builder=s.slide.shapes.build_freeform(*coords[0])
        builder.add_line_segments(coords[1:],close=True)
        shape=builder.convert_to_shape()
        shape.fill.solid()
        shape.fill.fore_color.rgb=d.rgb('purpale' if writable else 'E8E8E8')
        shape.line.color.rgb=d.rgb('gray')
        from pptx.util import Pt
        shape.line.width=Pt(.75)
        s.svg.append('<polygon points="'+' '.join(f'{a*72},{b*72}' for a,b in vertices)
                     +'" fill="'+d.col('purpale' if writable else 'E8E8E8')
                     +'" stroke="'+d.col('gray')
                     +'" stroke-width="0.75"/>')
        s.count+=1
        label(x+.17,y+h/2,w-.34,label_text)

    boundary_openings=[]
    def boundary(x,y,w,h,color,*,dash=False,openings=None):
        # Interrupt group outlines at connector passages. These are actual
        # gaps in the editable/vector border, not white strokes over arrows.
        openings=openings or {}
        s.rect(x,y,w,h,'white','white',lw=0)
        sides={'top':(x,x+w,y,True),'bottom':(x,x+w,y+h,True),
               'left':(y,y+h,x,False),'right':(y,y+h,x+w,False)}
        for side,(start,end,fixed,horizontal) in sides.items():
            cursor=start
            for center in sorted(openings.get(side,[])):
                lo,hi=center-.10,center+.10
                if lo>cursor:
                    p,q=((cursor,fixed),(lo,fixed)) if horizontal else ((fixed,cursor),(fixed,lo))
                    s.line(*p,*q,color,lw=.65,dash=dash,arrow=False)
                boundary_openings.append(dict(color=color,side=side,
                    center=(center,fixed) if horizontal else (fixed,center),width_inches=.20))
                cursor=hi
            if cursor<end:
                p,q=((cursor,fixed),(end,fixed)) if horizontal else ((fixed,cursor),(fixed,end))
                s.line(*p,*q,color,lw=.65,dash=dash,arrow=False)
    boundary(.15,.74,3.30,3.58,'gray',
             openings={'top':[call_center],'right':[1.55,2.65]})
    boundary(3.75,.74,4.75,3.58,'blue',dash=True,
             openings={'left':[1.55,3.18]})
    label(right_x,.96,4.20,'Lancet additions',NODE_PT,'blue',bold=True,align='left')
    label(.32,.96,2.95,'OverlayFS',NODE_PT,bold=True,align='left')
    s.rect(left_x,.10,left_w,.50,'black','white',r=.045,lw=.75)
    label(left_x+.10,.35,left_w-.20,'Tool calls')
    s.rect(left_x,rows[0],left_w,node_h,'black','white',lw=.75)
    label(left_x+.10,rows[0]+node_h/2,left_w-.20,'Merged view')
    layer(left_x,rows[1],left_w,'Upper U (RW)',writable=True)
    layer(left_x,rows[2],left_w,'Lower L (RO)',writable=False)
    connect('tool-bottom','merged-top')
    # Layer composition is separate from the blue recovery path.
    connect('upper-left','merged-left-upper',
            via=[(.40,rows[1]+node_h/2),(.40,ports['merged-left-upper'][1])],color='gray')
    connect('lower-left','merged-left-lower',
            via=[(.27,rows[2]+node_h/2),(.27,ports['merged-left-lower'][1])],color='gray')

    operation(right_x,rows[0],right_edge-right_x,'Record',1)
    # These are capabilities added by Lancet, not OverlayFS primitives.
    # Two dependency channels remain distinct and object versions are explicit.
    record_cy=rows[0]+node_h/2
    for x,w,first,second in [(5.54,.76,'File','edges'),
                              (6.43,.76,'Result','edges'),
                              (7.32,.91,'Object','versions')]:
        label(x,record_cy-.14,w,first,NOTE_PT,'gray')
        label(x,record_cy+.14,w,second,NOTE_PT,'gray')
    for x in (5.45,6.35,7.24):
        line(s,(x,rows[0]+.12),(x,rows[0]+node_h-.12),kind='divider',arrow=False)
    connect('merged-right','record-left')

    operation(right_x,rows[1],select_w,'Select S',2)
    operation(apply_x,rows[1],apply_w,'Apply S',3,color='blue')
    connect('record-bottom','select-top')
    connect('select-right','apply-left')

    # Separate output ports prevent the restore route from sharing the
    # context shaft. The return route occupies the middle of the group gap.
    connect('apply-bottom','context-top',color='blue')
    connect('apply-restore','upper-right',
            via=[(ports['apply-restore'][0],3.18),(3.60,3.18),(3.60,rows[1]+node_h/2)],
            color='blue')
    label(right_x,3.47,2.00,'Selective restore',NOTE_PT,'blue')
    label(apply_center+.14,3.21,.70,'Sync',NOTE_PT,'blue',align='left')
    s.rect(apply_x,rows[2],apply_w,node_h,'black','white',lw=.75)
    label(apply_x+.08,rows[2]+node_h/2,apply_w-.16,'Agent context',
          base=baseline(rows[2]+node_h/2,NODE_PT))

    notes = """The solid gray boundary contains the existing OverlayFS merged
view, writable upper U and read-only lower L. The dashed blue boundary
contains Lancet additions around that filesystem, not new OverlayFS kernel
primitives. Step 1 records file-access/mutation dependencies, tool-result
reuse dependencies and historical object versions. The file and result
histories share step identifiers. Step 2 selects dependency-closed rollback
sets from a user-reported file and its post-checkpoint mutations. An isolated
residue audit checks file state and active call/result records. Step 3
restores only selected filesystem objects and synchronizes retained
call/result pairs with the same S. Independent updates and observations
remain. Only U is restored; L is read-only. The blue branch names object
restoration and context synchronization explicitly. Version storage remains
outside the mounted OverlayFS layers; it is not an additional mounted layer.
The following two figures expand the same three numbered steps. Shared row
boundaries and typography keep both parts aligned. All artwork is editable
native PowerPoint geometry, exported as vectors to PDF and SVG. Typography
uses Times New Roman at 21 pt for nodes and group headings, and 18 pt for
annotations and step numbers. Only group headings are bold. Every arrow
has a 1.60 pt shaft and a fixed 9.0 by 7.2 pt triangular head, drawn with
the same native geometry in PPT and SVG rather than marker-scaled heads.
Connections meet node outlines without gaps, and layer ports follow the
slanted edges. Upper/lower composition and the two recovery outputs use
separate routes and ports. Group borders open only where connections pass.
"""
    report = finish(s, 'Lancet overview.pptx', 'Lancet-Overview.pdf', notes, dict(
        title='Lancet additions to an OverlayFS execution view',
        diagram_scope='existing OverlayFS versus added dependency, version and recovery capabilities',
        shown_call_example=False,
        execution_filesystem=dict(type='OverlayFS',tool_process_view='merged',
                                  upper='writable U',lower='read-only L'),
        layer_relationship='U and L jointly provide the merged view',
        filesystem_application='restore selected upper entries; preserve lower baseline',
        recovery_steps=['1 Record','2 Select S','3 Apply S'],
        step_details={'1':'dependency-closure-visual',
                      '2':['dependency-closure-visual','design-visual'],
                      '3':'design-visual'},
        synchronized_outputs=['upper state','agent context'],
        verification_details='Select S includes isolated plan validation; expanded in detailed figures',
        added_capabilities=['file dependency capture','tool-result dependency capture',
            'historical object versions','dependency-closed plan selection and validation',
            'selective object restoration','synchronized agent context'],
        extension_boundary='dashed blue Lancet additions frame',
        overlayfs_kernel_modified=False,history_is_mounted_layer=False,
        legend_required=False,
        alignment_grid=dict(row_tops=rows,node_height=node_h,
            left_edges=[left_x,right_x,apply_x],
            left_node_width=left_w,control_node_width=apply_w,
            record_right_edge=right_edge,apply_right_edge=right_edge,
            context_right_edge=right_edge,shared_text_baselines=True),
        overview_colors=dict(overlayfs='black and gray',extension_boundary='blue dashed',applied_recovery='blue'),
        typography=dict(family=d.FONT,node_pt=NODE_PT,annotation_pt=NOTE_PT,
                        heading_pt=NODE_PT,bold_headings_only=True,labels=type_roles),
        connectors=dict(shaft_pt=FLOW_PT,head_length_pt=HEAD_LENGTH_PT,
            head_width_pt=HEAD_WIDTH_PT,routed_geometry='smooth cubic Bezier',
            gap_inches=0,head_geometry='explicit matching native PPT and SVG triangles',
            ports=ports,connections=connection_geometry,arrows=arrow_geometry,
            group_boundary_openings=boundary_openings,shared_connector_segments=False),
        paper_include_width_fraction=1.0,
        primary_font_at_actual_paper_width_pt=round(NODE_PT*3.35/d.W,2)))
    for dest in [ROOT/'paper/img', ROOT/'motivation']:
        shutil.copyfile(OUT/'Lancet-Overview.pdf', dest/'FIG-Lancet-Overview.pdf')
        shutil.copyfile(OUT/'overview-visual.png', dest/'FIG-Lancet-Overview.png')
    return report

def design():
    s=canvas('design-visual',8.30)
    v=DetailStyle(s)
    # Keep the original OverlayFS states, checkpoint matrix and recovery stages.
    v.label(.10,.28,8.40,'OverlayFS',bold=True,align='left')
    s.line(.10,.53,8.50,.53,'gray',lw=.40,arrow=False)
    lower=EXAMPLE['lower']
    states=[dict(x=.10,title='Current',upper=EXAMPLE['upper_before'],
                 merged=EXAMPLE['merged_before'],selected={0,2,3},visible=[]),
            dict(x=4.65,title='Recovered',upper=EXAMPLE['upper_after'],
                 merged=EXAMPLE['merged_after'],selected=set(),visible=[])]
    for state in states:
        x=state['x']
        s.rect(x,.70,3.85,1.53,'gray','white',lw=.65)
        v.label(x+.14,.97,3.57,state['title'],bold=True,align='left')
        for kind,label,y in [('merged','Merged',1.22),('upper','Upper U',1.75)]:
            v.label(x+.07,y+.175,.94,label,note=True,align='left')
            for j,value in enumerate(state[kind]):
                cell_state='empty' if value=='∅' else 'normal'
                if j==1 and value!='∅':cell_state='retained'
                if j in state['selected']:cell_state='selected'
                if state['title']=='Recovered' and kind=='merged' and j in {0,2,3}:
                    cell_state='restored'
                v.node(x+1.06+j*.68,y,.57,.35,value,state=cell_state)
        for j,value in enumerate(state['upper']):
            if value!='∅':
                cx=x+1.06+j*.68+.285
                s.line(cx,1.75,cx,1.57,'gray',lw=.60,arrow=False)
        s.line(x+1.925,2.23,x+1.925,2.34,'gray',lw=.60,arrow=False)
    v.arrow([(3.95,1.58),(4.65,1.58)])
    s.rect(.10,2.34,8.40,.45,'gray','white',lw=.65)
    v.label(.24,2.565,1.30,'Lower L',align='left')
    for j,value in enumerate(lower):
        v.node(2.96+j*.70,2.415,.58,.30,value,
               state='empty' if value=='∅' else 'normal')

    v.frame(.10,3.05,2.95,2.35,'Select S',2)
    v.node(.27,4.01,2.60,.38,SELECTED_LABEL,state='selected')
    v.node(1.20,4.56,.75,.36,'E5',state='retained')
    v.frame(3.65,3.05,4.85,2.35,'Choose checkpoints',None)
    v.arrow([(3.05,4.20),(3.65,4.20)])
    v.label(3.87,3.85,1.24,'Checkpoint',note=True,align='left')
    columns=[('f',5.13),('g',5.91),('h',6.69),('p',7.47)]
    for obj,x in columns:
        v.label(x,3.85,.58,obj)
    checkpoints=[dict(id='V0',before='E3',view=['f0','g0','h0','∅']),
               dict(id='V1',before='E6',view=['f2','g1','h0','∅']),
               dict(id='V2',before='E7',view=['f2','g1','∅','∅'])]
    selected_versions={'f':'V0','h':'V1','p':'V2'}
    for row,checkpoint in enumerate(checkpoints):
        y=4.04+row*.43
        v.label(4.23,y+.15,.70,checkpoint['id'],note=True,align='left')
        for (obj,x),value in zip(columns,checkpoint['view']):
            selected=selected_versions.get(obj)==checkpoint['id']
            v.node(x,y,.58,.30,value,state='restored' if selected else
                   'empty' if value=='∅' else 'normal')
    v.arrow([(6.07,5.40),(6.07,5.73),(1.575,5.73),(1.575,6.11)])
    v.frame(.10,6.11,2.95,1.60,'Validate',None,color='blue',dash=True)
    v.label(.32,7.05,2.51,'Residue audit')
    v.frame(3.65,6.11,4.85,1.60,'Apply S',3,color='blue')
    v.arrow([(3.05,7.03),(3.65,7.03)])
    v.label(3.08,6.77,.53,'pass',note=True)
    v.label(3.87,6.935,.43,'FS',align='left')
    for x,value in [(4.50,'f0'),(5.15,'h0'),(5.80,'∅')]:
        v.node(x,6.77,.57,.33,value,state='restored')
    v.node(7.42,6.77,.65,.33,'g1',state='retained')
    v.label(3.87,7.425,1.04,'Context',align='left')
    v.node(5.10,7.26,1.20,.33,'drop S',state='selected')
    v.node(6.58,7.26,1.55,.33,'E5/r5',state='retained')
    for x,state,label in [(1.03,'selected','Selected'),(3.77,'retained','Retained'),
                           (6.39,'restored','Restored')]:
        v.node(x,7.965,.21,.21,'',state=state)
        v.label(x+.34,8.095,1.60,label,note=True,align='left')
    notes = '''E1/E2 inspect the baseline. E3 writes f1 and returns report.json;
E4 reads f1 and overwrites f with f2. E5 independently writes g1, between
E4 and E6. E6 reads f2 and deletes h. E7 reuses the returned filename to
create p1; E8 reads p1. The user reports f, with C0 before E3. Its writes
E3 and E4 seed closure S={E3,E4,E6,E7,E8}, while
E1,E2,E5 remain active. The current upper is f2,g1,WH(h),p1 above lower
f0,g0,h0,absent(p). Recovery retains current g1 and restores f,h,p.
Checkpoint rows include all four paths: V0 before E3 has f0,g0,h0,absent(p);
V1 before E6 has f2,g1,h0,absent(p); V2 before E7 has f2,g1,absent(h),absent(p).
No row contains f0 together with g1. Blue cells select f/V0,h/V1,p/V2;
g is retained from the current upper, not copied from a selected row.
The selected historical upper entries are absent in this example. Removing
f2,WH(h),p1 reveals lower f0,h0 and restores p's absence. A passing isolated
residue audit precedes live restoration. The same frozen S withdraws the selected call/result
pairs and preserves E5/r5 from the mixed turn (E5,E6,E7,E8).
Black solid arrows show flow; saved rows are merged states, not mounted layers.
Step numbers match the overview: 2 Select S includes version planning and
validation; 3 Apply S updates the filesystem and context.
The selected entries are applied to the existing upperdir. History lives
outside the mount. The native PPT and PDF share editable/vector coordinates.
'''
    report = finish(s, 'Lancet-Design-Workflow.pptx', 'Lancet-Design-Workflow.pdf', notes, dict(
        title='Lancet: checkpoint selection and selective filesystem reconstruction',
        overview_steps=[2,3],
        baseline=lower,
        states=[dict(title=q['title'], upper=q['upper'], merged=q['merged']) for q in states],
        selected_steps=EXAMPLE['selected_steps'], retained_steps=EXAMPLE['retained_steps'],
        filesystem_edges=EXAMPLE['filesystem_edges'], message_edges=EXAMPLE['message_edges'],
        shown_dependency_edges=False,
        checkpoint_views=checkpoints, selected_checkpoint_versions=selected_versions,
        checkpoint_view_semantics='merged view over the shared lower baseline',
        restoration_base='current filesystem state',
        selection_rule='latest compatible checkpoint before the first selected mutation per path group',
        before_images=[dict(object=obj, before=step, prior_upper=None)
                       for obj,step in EXAMPLE['before_images']],
        history_is_mounted_layer=False, baseline_commit=False, atomic_probe_swap=False,
        synchronized_outputs=['live upperdir','agent context'],
        routing_clearance=dict(checkpoint_row_pitch_inches=.43,
            checkpoint_cell_vertical_gap_inches=.13,upper_cell_horizontal_gap_inches=.11),
        original_layout_preserved=True, **v.report()))
    shutil.copyfile(OUT/'layout-check-design-visual.json', OUT/'layout-check-visual.json')
    return report


def main():
    reports = [overview(), design()]
    print(json.dumps([dict(title=r['title'], native_shapes=r['native_shapes'],
                           text_fit_issues=r['text_fit_issues'], bounds_issues=r['bounds_issues'],
                           body_font_at_paper_width_pt=r['body_font_at_paper_width_pt'])
                      for r in reports], indent=2))


if __name__ == '__main__':
    main()
