# -*- coding: utf-8 -*-
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
import os

import os
OUT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)

ORANGE='#F0571E'; TEAL='#1597C4'; NAVY='#182A3D'; WHITE='#FFFFFF'

f = TTFont(os.path.join(os.path.dirname(os.path.abspath(__file__)),'Gabarito.ttf'))
instantiateVariableFont(f, {'wght':800}, inplace=True)
gs = f.getGlyphSet()
cmap = f.getBestCmap()

def draw_text(text, size, originX, baseY, letterspacing=0.0):
    s = size/1000.0
    spen = SVGPathPen(gs)
    bpen = BoundsPen(gs)
    ox = originX
    for ch in text:
        g = cmap[ord(ch)]
        glyph = gs[g]
        m = (s,0,0,-s,ox,baseY)
        glyph.draw(TransformPen(spen, m))
        glyph.draw(TransformPen(bpen, m))
        ox += glyph.width*s - letterspacing
    return spen.getCommands(), bpen.bounds, ox

def annulus(cx,cy,ro,ri):
    # even-odd ring
    return (f"M {cx-ro:.2f} {cy:.2f} a {ro:.2f} {ro:.2f} 0 1 0 {2*ro:.2f} 0 a {ro:.2f} {ro:.2f} 0 1 0 {-2*ro:.2f} 0 Z "
            f"M {cx-ri:.2f} {cy:.2f} a {ri:.2f} {ri:.2f} 0 1 0 {2*ri:.2f} 0 a {ri:.2f} {ri:.2f} 0 1 0 {-2*ri:.2f} 0 Z")

def icon_svg(dot_color):
    size=120; baseY=84.0; ox0=0.0
    d,(x0,y0,x1,y1),_ = draw_text('P', size, ox0, baseY)
    # ring geometry (matches approved widget: at size-120 P)
    cx,cy = 48.0,31.0
    clear_r=33.0; teal_o=27.0; teal_i=18.0; dot_r=9.0
    # combined extents
    minx=min(x0, cx-clear_r); maxx=max(x1, cx+clear_r)
    miny=min(y0, cy-clear_r); maxy=max(y1, cy+clear_r)
    w=maxx-minx; h=maxy-miny; side=max(w,h)
    pad=side*0.14; side+=2*pad
    vbx=minx-(side-w)/2; vby=miny-(side-h)/2
    vb=f"{vbx:.2f} {vby:.2f} {side:.2f} {side:.2f}"
    mrect=f'<rect x="{vbx-5:.2f}" y="{vby-5:.2f}" width="{side+10:.2f}" height="{side+10:.2f}" fill="white"/>'
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="Printosky">
  <defs>
    <mask id="bowl" maskUnits="userSpaceOnUse" x="{vbx-5:.2f}" y="{vby-5:.2f}" width="{side+10:.2f}" height="{side+10:.2f}">
      {mrect}
      <circle cx="{cx}" cy="{cy}" r="{clear_r}" fill="black"/>
    </mask>
  </defs>
  <path d="{d}" fill="{ORANGE}" mask="url(#bowl)"/>
  <path d="{annulus(cx,cy,teal_o,teal_i)}" fill="{TEAL}" fill-rule="evenodd"/>
  <circle cx="{cx}" cy="{cy}" r="{dot_r}" fill="{dot_color}"/>
</svg>
'''
    return svg

def wordmark_svg(dot_color):
    size=54; baseY=100.0; originX=10.0; ls=1.5
    d,(x0,y0,x1,y1),endx = draw_text('Printosky', size, originX, baseY, ls)
    # ring on leading P bowl: offsets relative to P origin, scaled from size-120 reference
    s=size/1000.0
    # at size120 ref: ring cx=48,cy(above baseline)=53, radii clear33 teal27/18 dot9 -> as fraction of size120
    fx=48/120.0; fyabove=53/120.0
    cx=originX+fx*size; cy=baseY-fyabove*size
    clear_r=33/120.0*size; teal_o=27/120.0*size; teal_i=18/120.0*size; dot_r=9/120.0*size
    minx=min(x0,cx-clear_r); maxx=max(x1,cx+clear_r)
    miny=min(y0,cy-clear_r); maxy=max(y1,baseY+0.5)  # baseline incl descender of y
    # include descender (y of 'y' goes below baseline)
    miny=min(miny,y0); maxy=max(maxy,y1)
    pad=10.0
    vbx=minx-pad; vby=miny-pad; w=(maxx-minx)+2*pad; h=(maxy-miny)+2*pad
    vb=f"{vbx:.2f} {vby:.2f} {w:.2f} {h:.2f}"
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="Printosky">
  <defs>
    <mask id="bowl" maskUnits="userSpaceOnUse" x="{vbx-5:.2f}" y="{vby-5:.2f}" width="{w+10:.2f}" height="{h+10:.2f}">
      <rect x="{vbx-5:.2f}" y="{vby-5:.2f}" width="{w+10:.2f}" height="{h+10:.2f}" fill="white"/>
      <circle cx="{cx:.2f}" cy="{cy:.2f}" r="{clear_r:.2f}" fill="black"/>
    </mask>
  </defs>
  <path d="{d}" fill="{ORANGE}" mask="url(#bowl)"/>
  <path d="{annulus(cx,cy,teal_o,teal_i)}" fill="{TEAL}" fill-rule="evenodd"/>
  <circle cx="{cx:.2f}" cy="{cy:.2f}" r="{dot_r:.2f}" fill="{dot_color}"/>
</svg>
'''
    return svg

open(OUT+'/printosky-icon.svg','w',encoding='utf-8').write(icon_svg(NAVY))
open(OUT+'/printosky-icon-reverse.svg','w',encoding='utf-8').write(icon_svg(WHITE))
open(OUT+'/printosky-wordmark.svg','w',encoding='utf-8').write(wordmark_svg(NAVY))
open(OUT+'/printosky-wordmark-reverse.svg','w',encoding='utf-8').write(wordmark_svg(WHITE))
print('wrote 4 svgs to', OUT)
for fn in ['printosky-icon.svg','printosky-wordmark.svg']:
    print('---',fn,'---'); print(open(OUT+'/'+fn,encoding='utf-8').read()[:600])
