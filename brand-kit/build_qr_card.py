# -*- coding: utf-8 -*-
"""Shareable Printosky payment QR card (1080x1080) for WhatsApp.
Printosky logo (top) -> QR (center) -> Oxygen Students Paradise (subtle, bottom),
evenly spaced, with editorial design accents. Embeds assets/payment-qr.jpg base64."""
import base64, io, os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "assets")

def data_uri_png(path, max_w):
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height*max_w/im.width)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

QR = data_uri_png(os.path.join(A, "sib-qr.png"), 700)   # v2: South Indian Bank UPI bhqr.2323001A@sib

HTML = u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Printosky — Payment QR</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
  :root{--ink:#0D1117;--paper:#F5F1EB;--blue:#1B3F8B;--accent:#E8500A;--mid:#6B7280;--white:#fff;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:#cfc7b8;font-family:'DM Sans',sans-serif;display:flex;flex-direction:column;align-items:center;padding:34px;gap:18px;}
  .toolbar{max-width:96vw;background:var(--ink);color:#fff;border-radius:12px;padding:14px 20px;display:flex;gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap;}
  .toolbar span{font-size:13px;color:#c9d2dc;}
  .toolbar button{font-family:'DM Sans';font-weight:600;font-size:13px;background:var(--accent);color:#fff;border:0;padding:10px 18px;border-radius:8px;cursor:pointer;}
  .frame{max-width:96vw;} .frame .scaler{transform-origin:top left;}

  .card{width:1080px;height:1080px;position:relative;overflow:hidden;
    padding:90px 80px;display:flex;flex-direction:column;align-items:center;justify-content:space-evenly;
    background-color:var(--paper);
    background-image:radial-gradient(circle, rgba(13,17,23,.05) 1.7px, transparent 1.8px);
    background-size:36px 36px;}
  /* twin accent bars top & bottom (in-flow-safe: full-width children, no flex collapse) */
  .bar{position:absolute;left:0;right:0;height:13px;z-index:2;background:var(--blue);}
  .bar.top{top:0;} .bar.bot{bottom:0;}
  .bar::after{content:"";position:absolute;top:0;bottom:0;width:250px;background:var(--accent);}
  .bar.top::after{right:0;} .bar.bot::after{left:0;}

  .card>.head,.card>.mid,.card>.foot{position:relative;z-index:3;}

  .head{display:flex;flex-direction:column;align-items:center;gap:12px;}
  .wm{font-family:'Syne',sans-serif;font-weight:800;font-size:72px;letter-spacing:-3.5px;color:var(--ink);line-height:1;}
  .wm .d{color:var(--accent);}
  .tag{font-weight:600;font-size:22px;color:var(--mid);letter-spacing:.3px;}
  .arule{width:60px;height:5px;border-radius:4px;background:var(--accent);margin-top:4px;}

  .mid{display:flex;flex-direction:column;align-items:center;gap:22px;}
  .scanlab{font-weight:700;font-size:22px;letter-spacing:6px;text-transform:uppercase;color:var(--blue);}
  .qrframe{position:relative;background:var(--white);border-radius:26px;box-shadow:0 22px 56px rgba(13,17,23,.13);padding:34px;}
  .qrframe img{display:block;width:468px;height:468px;}
  /* orange scan-corner brackets */
  .qrframe .b{position:absolute;width:46px;height:46px;}
  .qrframe .b.tl{top:16px;left:16px;border-top:6px solid var(--accent);border-left:6px solid var(--accent);border-top-left-radius:14px;}
  .qrframe .b.tr{top:16px;right:16px;border-top:6px solid var(--accent);border-right:6px solid var(--accent);border-top-right-radius:14px;}
  .qrframe .b.bl{bottom:16px;left:16px;border-bottom:6px solid var(--accent);border-left:6px solid var(--accent);border-bottom-left-radius:14px;}
  .qrframe .b.br{bottom:16px;right:16px;border-bottom:6px solid var(--accent);border-right:6px solid var(--accent);border-bottom-right-radius:14px;}

  .foot{display:flex;flex-direction:column;align-items:center;gap:12px;}
  .foot .sep{display:flex;align-items:center;gap:12px;}
  .foot .sep .ln{width:60px;height:2px;background:var(--mid);opacity:.4;}
  .foot .sep .dt{width:7px;height:7px;border-radius:50%;background:var(--accent);opacity:.8;}
  .osp{font-size:22px;color:var(--mid);opacity:.7;letter-spacing:.6px;text-align:center;}

  [contenteditable]{outline:none;border-radius:6px;}
  [contenteditable]:hover{box-shadow:0 0 0 2px rgba(232,80,10,.3);}
  [contenteditable]:focus{box-shadow:0 0 0 2px var(--accent);}
</style></head><body>
<div class="toolbar"><span>&#9999; Click text to edit. Printosky payment QR for WhatsApp (1080&#215;1080). Then download PNG.</span>
<button onclick="dl()">&#11015; Download PNG</button></div>
<div class="frame"><div class="scaler" id="scaler"><div class="card" id="card">
  <div class="bar top"></div><div class="bar bot"></div>

  <div class="head">
    <div class="wm">Printosky<span class="d">.</span></div>
    <div class="tag" contenteditable spellcheck="false">Print it. Perfect. Fast.</div>
    <div class="arule"></div>
  </div>

  <div class="mid">
    <div class="scanlab" contenteditable spellcheck="false">Scan to Pay</div>
    <div class="qrframe">
      <span class="b tl"></span><span class="b tr"></span><span class="b bl"></span><span class="b br"></span>
      <img src="__QR__" alt="Printosky UPI payment QR">
    </div>
  </div>

  <div class="foot">
    <div class="sep"><span class="ln"></span><span class="dt"></span><span class="ln"></span></div>
    <div class="osp" contenteditable spellcheck="false">Oxygen Students Paradise · Thrissur</div>
  </div>
</div></div></div>
<script>
  function fit(){const f=document.querySelector('.frame');const s=Math.min(1,f.clientWidth/1080);document.getElementById('scaler').style.transform='scale('+s+')';f.style.height=(1080*s)+'px';}
  window.addEventListener('resize',fit); fit();
  async function renderCard(scale){
    const p=document.getElementById('card'); await document.fonts.ready;
    const c=p.cloneNode(true); c.style.transform='none';c.style.position='fixed';c.style.left='0';c.style.top='-30000px';c.style.margin='0';
    document.body.appendChild(c); await document.fonts.ready;
    const cv=await html2canvas(c,{scale:scale,backgroundColor:'#F5F1EB',useCORS:true,logging:false,width:1080,height:1080});
    c.remove(); return cv;
  }
  async function dl(){const cv=await renderCard(2);const a=document.createElement('a');a.download='printosky-payment-qr-v2.png';a.href=cv.toDataURL('image/png');a.click();}
</script></body></html>"""

final = HTML.replace("__QR__", QR)
# working copy in brand-kit + reusable copy in the project marketing folder
targets = [
    os.path.join(HERE, "payment-qr-card-v2.html"),
    os.path.join(HERE, "..", "marketing", "printosky-payment-qr", "printosky-payment-qr-v2.html"),
]
for out in targets:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(final)
    print("wrote", os.path.normpath(out), "(%.0f KB)" % (os.path.getsize(out)/1024))
