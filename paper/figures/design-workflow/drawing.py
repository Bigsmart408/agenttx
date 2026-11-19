#!/usr/bin/env python3
"""Editable native PowerPoint figures + matching vector previews.
Run with python-pptx, cairosvg, Pillow, PyMuPDF. No raster artwork is used in the deck.
"""
from pathlib import Path
import math, html, json, ctypes, importlib.util
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.oxml.xmlchemy import OxmlElement
from PIL import ImageFont
import cairosvg
from cairosvg.surface import PDFSurface, cairo
import pymupdf

OUT=Path(__file__).resolve().parent
W,H=18,11.25
C=dict(blue='2E62E8', pale='F5F8FF', purple='7E4AC6', purpale='F8F4FF', red='D64B40', redpale='FFF4F1', green='669E47', greenpale='F1F8EB', black='202124', gray='69707A', light='D9DDE4', white='FFFFFF')
FONT='Times New Roman'
SVG_FONT='DejaVu Serif'
prs=Presentation(); prs.slide_width=Inches(W); prs.slide_height=Inches(H)
prs.core_properties.title='AgentTX — Causal Recovery Design'
prs.core_properties.subject='Editable design figures aligned with the paper causal workflow'
prs.core_properties.author='AgentTX'
issues=[]
fontpaths={False:'/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',True:'/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'}

# Prefer the same Times New Roman files in the deck and exported figures.
# STIX General remains a fallback on machines without the Microsoft fonts.
font_candidates=[]
times_dir=Path.home()/'.local/share/fonts/TimesNewRoman'
times_regular=times_dir/'Times.TTF'
times_bold=times_dir/'Timesbd.TTF'
if times_regular.exists() and times_bold.exists():
 SVG_FONT=FONT
 fontpaths={False:str(times_regular),True:str(times_bold)}
mpl=importlib.util.find_spec('matplotlib')
if mpl and mpl.origin:
 font_candidates.append(Path(mpl.origin).parent/'mpl-data/fonts/ttf/STIXGeneral.ttf')
font_candidates.extend((Path.home()/'.cache/uv/archive-v0').glob('*/matplotlib/mpl-data/fonts/ttf/STIXGeneral.ttf'))
for regular in ([] if SVG_FONT==FONT else font_candidates):
 bold=regular.with_name('STIXGeneralBol.ttf')
 if regular.exists() and bold.exists():
  try:
   fc=ctypes.CDLL('libfontconfig.so.1')
   fc.FcConfigAppFontAddFile.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
   fc.FcConfigAppFontAddFile.restype=ctypes.c_int
   loaded=all(fc.FcConfigAppFontAddFile(None,str(p).encode()) for p in (regular,bold))
   if loaded:
    SVG_FONT='STIXGeneral';fontpaths={False:str(regular),True:str(bold)}
    break
  except OSError:
   pass

def esc(s): return html.escape(str(s))
def rgb(c): return RGBColor.from_string(C.get(c,c))
def col(c): return '#'+C.get(c,c)

def connector_style(shape,color,dash,lw,arrow):
 shape.line.color.rgb=rgb(color);shape.line.width=Pt(lw)
 if dash:shape.line.dash_style=MSO_LINE_DASH_STYLE.DASH
 ln=shape.line._get_or_add_ln();ln.set('cap','rnd')
 ln.append(OxmlElement('a:round'))
 if arrow:
  e=OxmlElement('a:tailEnd');e.set('type','triangle');e.set('w','lg');e.set('len','lg');ln.append(e)

class PaperPDFSurface(PDFSurface):
 """Keep vector figures compatible with the paper's PDF 1.5 output."""
 def _create_surface(self,width,height):
  surface,width,height=super()._create_surface(width,height)
  surface.restrict_to_version(cairo.PDF_VERSION_1_5)
  return surface,width,height

class Canvas:
 def __init__(self,name):
  self.name=name;self.slide=prs.slides.add_slide(prs.slide_layouts[6]);self.svg=[];self.count=0
 def circle(self,cx,cy,r,stroke='red',fill='white',lw=1.2):
  shape=self.slide.shapes.add_shape(MSO_SHAPE.OVAL,Inches(cx-r),Inches(cy-r),Inches(2*r),Inches(2*r))
  shape.fill.solid();shape.fill.fore_color.rgb=rgb(fill)
  shape.line.color.rgb=rgb(stroke);shape.line.width=Pt(lw)
  self.svg.append(f'<circle cx="{cx*72}" cy="{cy*72}" r="{r*72}" fill="{col(fill)}" stroke="{col(stroke)}" stroke-width="{lw}"/>')
  self.count+=1
 def rect(self,x,y,w,h,stroke='blue',fill='white',dash=False,r=0,lw=1.35):
  s=self.slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if r else MSO_SHAPE.RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h))
  if r:
   s.adjustments[0]=.10
  s.fill.solid();s.fill.fore_color.rgb=rgb(fill);s.line.color.rgb=rgb(stroke);s.line.width=Pt(lw)
  if dash:s.line.dash_style=MSO_LINE_DASH_STYLE.DASH
  self.svg.append(f'<rect x="{x*72}" y="{y*72}" width="{w*72}" height="{h*72}" rx="{r*72}" fill="{col(fill)}" stroke="{col(stroke)}" stroke-width="{lw}"'+(' stroke-dasharray="7 4"' if dash else '')+'/>')
  self.count+=1
 def text(self,x,y,w,h,text,size=17,color='black',bold=False,align='left'):
  box=self.slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h));tf=box.text_frame
  tf.clear();tf.word_wrap=False;tf.margin_left=0;tf.margin_right=0;tf.margin_top=0;tf.margin_bottom=0
  tf.vertical_anchor=MSO_ANCHOR.TOP
  lines=text.split('\n');lineheight=size*1.19
  f=ImageFont.truetype(fontpaths[bold],round(size*4))
  for i,line in enumerate(lines):
   p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.text=line
   # The installed 2000 Times font has no empty-set glyph. Keep mathematical
   # symbols editable with the Office math font; use a matching local fallback.
   symbol=line.strip()=='∅'
   p.font.name='Cambria Math' if symbol else FONT
   p.font.size=Pt(size);p.font.bold=bold;p.font.color.rgb=rgb(color)
   p.space_before=Pt(0);p.space_after=Pt(0);p.line_spacing=Pt(lineheight)
   p.alignment={'left':PP_ALIGN.LEFT,'center':PP_ALIGN.CENTER,'right':PP_ALIGN.RIGHT}[align]
   actual=f.getlength(line)/4
   if actual>w*72+2:issues.append({'slide':self.name,'text':line,'width_pt':round(actual,2),'available_pt':w*72})
   anchor={'left':'start','center':'middle','right':'end'}[align];tx=(x+(w/2 if align=='center' else w if align=='right' else 0))*72
   ty=y*72+size*.94+i*lineheight
   family='DejaVu Sans' if symbol else SVG_FONT
   self.svg.append(f'<text x="{tx}" y="{ty}" font-family="{family}" font-size="{size}" font-weight="{"bold" if bold else "normal"}" fill="{col(color)}" text-anchor="{anchor}">{esc(line)}</text>')
  if len(lines)*lineheight>h*72+5:issues.append({'slide':self.name,'text':text,'height_pt':len(lines)*lineheight,'available_pt':h*72})
  self.count+=1
 def line(self,x1,y1,x2,y2,color='blue',dash=False,arrow=False,lw=1.6):
  s=self.slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x1),Inches(y1),Inches(x2),Inches(y2))
  connector_style(s,color,dash,lw,arrow)
  self.svg.append(f'<line x1="{x1*72}" y1="{y1*72}" x2="{x2*72}" y2="{y2*72}" stroke="{col(color)}" stroke-width="{lw}" stroke-linecap="round"'+(' stroke-dasharray="3.6 2.8"' if dash else '')+(f' marker-end="url(#{color})"' if arrow else '')+'/>')
  self.count+=1
 def path(self,points,color='blue',dash=False,lw=1.0,radius=.055,arrow=True):
  # One editable path keeps rounded elbows and the final arrowhead together.
  operations=[('M',[points[0]])]
  for prev,corner,nxt in zip(points,points[1:],points[2:]):
   incoming=math.dist(prev,corner);outgoing=math.dist(corner,nxt)
   r=min(radius,incoming/2,outgoing/2)
   before=tuple(c+(p-c)*r/incoming for p,c in zip(prev,corner))
   after=tuple(c+(n-c)*r/outgoing for n,c in zip(nxt,corner))
   operations.extend([('L',[before]),('Q',[corner,after])])
  operations.append(('L',[points[-1]]))
  coords=[(round(x*914400),round(y*914400)) for x,y in points]
  builder=self.slide.shapes.build_freeform(*coords[0])
  builder.add_line_segments(coords[1:],close=False)
  shape=builder.convert_to_shape();shape.fill.background()
  path=shape._element.spPr.custGeom.pathLst[0]
  for child in list(path):path.remove(child)
  left,top=min(x for x,y in coords),min(y for x,y in coords)
  svg_commands=[]
  for kind,positions in operations:
   element=OxmlElement({'M':'a:moveTo','L':'a:lnTo','Q':'a:quadBezTo'}[kind])
   for x,y in positions:
    pt=OxmlElement('a:pt');pt.set('x',str(round(x*914400)-left));pt.set('y',str(round(y*914400)-top));element.append(pt)
   path.append(element)
   svg_commands.append(kind+' '+' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in positions))
  connector_style(shape,color,dash,lw,arrow)
  self.svg.append(f'<path d="{" ".join(svg_commands)}" fill="none" stroke="{col(color)}" stroke-width="{lw}" stroke-linecap="round" stroke-linejoin="round"'+(' stroke-dasharray="3.6 2.8"' if dash else '')+(f' marker-end="url(#{color})"' if arrow else '')+'/>')
  self.count+=1
 def curved_path(self,points,color='black',dash=False,lw=1.6):
  """Turn a routed connector into tangent-continuous editable cubic curves.

  Route corners are control points, with joins halfway between them. This
  keeps the curve inside its routing corridor and preserves both port
  directions. Callers draw the fixed-size arrowhead separately.
  """
  points=list(points)
  if len(points)<3:
   raise ValueError('A curved route needs at least one control corner')
  operations=[('M',[points[0]])]
  for i,corner in enumerate(points[1:-1],start=1):
   end=points[-1] if i==len(points)-2 else tuple(
       (a+b)/2 for a,b in zip(corner,points[i+1]))
   operations.append(('C',[corner,corner,end]))
  coords=[(round(x*914400),round(y*914400)) for x,y in points]
  builder=self.slide.shapes.build_freeform(*coords[0])
  builder.add_line_segments(coords[1:],close=False)
  shape=builder.convert_to_shape();shape.fill.background()
  path=shape._element.spPr.custGeom.pathLst[0]
  for child in list(path):path.remove(child)
  left,top=min(x for x,y in coords),min(y for x,y in coords)
  commands=[]
  for kind,positions in operations:
   element=OxmlElement({'M':'a:moveTo','C':'a:cubicBezTo'}[kind])
   for x,y in positions:
    pt=OxmlElement('a:pt');pt.set('x',str(round(x*914400)-left));pt.set('y',str(round(y*914400)-top));element.append(pt)
   path.append(element)
   commands.append(kind+' '+' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in positions))
  connector_style(shape,color,dash,lw,False)
  self.svg.append(f'<path data-role="curved-connector" d="{" ".join(commands)}" fill="none" stroke="{col(color)}" stroke-width="{lw}" stroke-linecap="round"'+(' stroke-dasharray="3.6 2.8"' if dash else '')+'/>')
  self.count+=1
 def curve(self,start,control1,control2,end,color='black',dash=False,lw=1.35,arrow=True):
  # Trim the shaft at the arrow's base. For a shallow curve the terminal
  # derivative can turn sharply within the arrowhead itself; following that
  # derivative produces a visible hook. Use the final chord for the head and
  # export identical editable geometry to PPT and SVG instead.
  tip=end
  head=None
  if arrow:
   def mix(p,q,t):return tuple(a+(b-a)*t for a,b in zip(p,q))
   def split(t):
    a=mix(start,control1,t);b=mix(control1,control2,t);c=mix(control2,tip,t)
    ab=mix(a,b,t);bc=mix(b,c,t)
    return a,ab,mix(ab,bc,t)
   head_length=9*lw/72
   low,high=0.,1.
   for _ in range(48):
    t=(low+high)/2
    if math.dist(split(t)[2],tip)>head_length:low=t
    else:high=t
   control1,control2,end=split((low+high)/2)
   dx,dy=(tip[0]-end[0])/head_length,(tip[1]-end[1])/head_length
   half_width=3.5*lw/72
   head=[tip,(end[0]-dy*half_width,end[1]+dx*half_width),
             (end[0]+dy*half_width,end[1]-dx*half_width)]
  # The trimmed cubic and the solid head are native editable PowerPoint paths.
  points=[start,control1,control2,end]
  coords=[(round(x*914400),round(y*914400)) for x,y in points]
  builder=self.slide.shapes.build_freeform(*coords[0])
  builder.add_line_segments(coords[1:],close=False)
  shape=builder.convert_to_shape();shape.fill.background()
  path=shape._element.spPr.custGeom.pathLst[0]
  for child in list(path):path.remove(child)
  left,top=min(x for x,y in coords),min(y for x,y in coords)
  for tag,positions in [('a:moveTo',[start]),('a:cubicBezTo',[control1,control2,end])]:
   element=OxmlElement(tag)
   for x,y in positions:
    pt=OxmlElement('a:pt');pt.set('x',str(round(x*914400)-left));pt.set('y',str(round(y*914400)-top));element.append(pt)
   path.append(element)
  connector_style(shape,color,dash,lw,False)
  commands='M '+f'{start[0]*72:.4f},{start[1]*72:.4f}'+' C '+' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in [control1,control2,end])
  self.svg.append(f'<path d="{commands}" fill="none" stroke="{col(color)}" stroke-width="{lw}" stroke-linecap="round"'+(' stroke-dasharray="3.6 2.8"' if dash else '')+'/>' )
  self.count+=1
  if head:
   coords=[(round(x*914400),round(y*914400)) for x,y in head]
   builder=self.slide.shapes.build_freeform(*coords[0])
   builder.add_line_segments(coords[1:],close=True)
   triangle=builder.convert_to_shape()
   triangle.fill.solid();triangle.fill.fore_color.rgb=rgb(color)
   triangle.line.fill.background()
   self.svg.append(f'<polygon data-role="curve-arrowhead" points="'+
                   ' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in head)+
                   f'" fill="{col(color)}"/>')
   self.count+=1
 def smooth_arrow(self,start,segments,tip,color='black',dash=False,lw=1.35):
  # Complete the bend before the arrowhead, then enter the target normally.
  neck=segments[-1][-1]
  length=math.dist(neck,tip)
  head_length=9*lw/72
  if length<head_length+.025:
   raise ValueError('A smooth arrow needs a straight landing before its head')
  dx,dy=(tip[0]-neck[0])/length,(tip[1]-neck[1])/length
  previous=segments[-1][-2]
  tangent=(neck[0]-previous[0],neck[1]-previous[1])
  if abs(tangent[0]*dy-tangent[1]*dx)>.00001 or tangent[0]*dx+tangent[1]*dy<=0:
   raise ValueError('The final curve must be tangent to its landing')
  base=(tip[0]-head_length*dx,tip[1]-head_length*dy)
  half_width=3.5*lw/72
  head=[tip,(base[0]-dy*half_width,base[1]+dx*half_width),
            (base[0]+dy*half_width,base[1]-dx*half_width)]
  operations=[('M',[start])]+[('C',segment) for segment in segments]+[('L',[base])]
  points=[point for _,positions in operations for point in positions]
  coords=[(round(x*914400),round(y*914400)) for x,y in points]
  builder=self.slide.shapes.build_freeform(*coords[0])
  builder.add_line_segments(coords[1:],close=False)
  shape=builder.convert_to_shape();shape.fill.background()
  path=shape._element.spPr.custGeom.pathLst[0]
  for child in list(path):path.remove(child)
  left,top=min(x for x,y in coords),min(y for x,y in coords)
  commands=[]
  for kind,positions in operations:
   element=OxmlElement({'M':'a:moveTo','C':'a:cubicBezTo','L':'a:lnTo'}[kind])
   for x,y in positions:
    pt=OxmlElement('a:pt');pt.set('x',str(round(x*914400)-left));pt.set('y',str(round(y*914400)-top));element.append(pt)
   path.append(element)
   commands.append(kind+' '+' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in positions))
  connector_style(shape,color,dash,lw,False)
  self.svg.append(f'<path data-role="smooth-arrow-shaft" d="{" ".join(commands)}" fill="none" stroke="{col(color)}" stroke-width="{lw}" stroke-linecap="round"'+(' stroke-dasharray="3.6 2.8"' if dash else '')+'/>')
  coords=[(round(x*914400),round(y*914400)) for x,y in head]
  builder=self.slide.shapes.build_freeform(*coords[0])
  builder.add_line_segments(coords[1:],close=True)
  triangle=builder.convert_to_shape()
  triangle.fill.solid();triangle.fill.fore_color.rgb=rgb(color)
  triangle.line.fill.background()
  self.svg.append('<polygon data-role="curve-arrowhead" points="'+
                  ' '.join(f'{x*72:.4f},{y*72:.4f}' for x,y in head)+
                  f'" fill="{col(color)}"/>')
  self.count+=2
 def panel(self,x,y,w,h,n,title,color='blue'):
  self.rect(x,y,w,h,color,'purpale' if color=='purple' else 'pale',lw=1.6)
  self.text(x+.16,y+.13,w-.30,.36,f'{n}  {title}',19,color,True)
  self.line(x+.15,y+.62,x+w-.15,y+.62,color,lw=.8)
 def node(self,x,y,w,h,label,state='invalid',size=18):
  color='green' if state=='retain' else 'red' if state in ('candidate','invalid','failed') else 'blue'
  fill='greenpale' if state=='retain' else 'redpale' if state in ('candidate','invalid','failed') else 'white'
  self.rect(x,y,w,h,color,fill,state=='invalid',lw=1.7 if state in ('candidate','retain') else 1.2)
  self.text(x,y+.12,w,h-.12,label,size,color if state=='retain' else 'black',state=='candidate','center')
 def header(self,title,sub):
  self.text(.35,.20,17.3,.46,title,27,'black',True)
  self.text(.35,.78,17.3,.36,sub,16.5,'gray')
 def save(self,notes):
  self.slide.notes_slide.notes_text_frame.text=notes
  defs=''.join(f'<marker id="{k}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{col(k)}"/></marker>' for k in ['blue','purple','red','green','gray','black'])
  svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*72}" height="{H*72}" viewBox="0 0 {W*72} {H*72}"><defs>{defs}</defs><rect width="100%" height="100%" fill="white"/>'+''.join(self.svg)+'</svg>'
  (OUT/f'{self.name}.svg').write_text(svg)
  cairosvg.svg2png(bytestring=svg.encode(),write_to=str(OUT/f'{self.name}.png'),output_width=2160,output_height=round(2160*H/W))
  PaperPDFSurface.convert(bytestring=svg.encode(),write_to=str(OUT/f'{self.name}.pdf'))
  return self.count

