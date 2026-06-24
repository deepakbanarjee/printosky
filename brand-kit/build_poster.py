# -*- coding: utf-8 -*-
"""Builds the Xtraa books campaign in 3 formats from shared embedded assets:
  poster.html            A4 portrait + 0.25in bleed (1315x1829)  [print]
  poster-instagram.html  1080x1080 square                        [Instagram feed]
  poster-story.html      1080x1920                               [WhatsApp status / IG story]
Real Xtraa logo + Printosky wordmark, multilingual alphabet watermark, QR for payment."""
import base64, io, os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "assets")

def data_uri(path, max_w, fmt="JPEG", quality=82):
    im = Image.open(path)
    im = im.convert("RGBA") if fmt == "PNG" else im.convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height*max_w/im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "PNG":
        im.save(buf, "PNG", optimize=True); mime="image/png"
    else:
        im.save(buf, "JPEG", quality=quality, optimize=True); mime="image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(buf.getvalue()).decode())

LOGO = data_uri(os.path.join(A,"xtraa-logo.png"),    500, fmt="PNG")
ML   = data_uri(os.path.join(A,"book-malayalam.jpg"),760)
HI   = data_uri(os.path.join(A,"book-hindi.jpg"),    760)
EN   = data_uri(os.path.join(A,"book-english.jpg"),  760)
QR   = data_uri(os.path.join(A,"books-qr.png"),      620, fmt="PNG")   # -> https://printosky.com/books

HEAD = u"""<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@500;600;700;800&family=Baloo+Chettan+2:wght@500;600;700;800&family=Poppins:wght@400;500;600&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>"""

VARS = u"""
  :root{--cream:#FFF6EA;--orange:#F26C21;--teal:#1C7C6B;--red:#C53A2B;--ink:#2B2620;--gold:#F4A93C;--mid:#7c7367;--white:#fff;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:#cfc7b8;font-family:'Poppins',sans-serif;display:flex;flex-direction:column;align-items:center;padding:34px;gap:18px;}
  .toolbar{max-width:96vw;background:var(--ink);color:#fff;border-radius:12px;padding:14px 20px;display:flex;gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap;}
  .toolbar span{font-size:13px;color:#d9cfc0;}.toolbar b{color:#fff;}
  .toolbar button{font-family:'Poppins';font-weight:600;font-size:13px;background:var(--orange);color:#fff;border:0;padding:10px 18px;border-radius:8px;cursor:pointer;}
  .frame{max-width:96vw;} .frame .scaler{transform-origin:top left;}
  .syne{font-family:'Syne',sans-serif;}
  .poster{position:relative;overflow:hidden;background:var(--cream);}
  .bg{position:absolute;inset:0;z-index:0;overflow:hidden;}
  .blob{position:absolute;border-radius:50%;}
  .alpha{position:absolute;font-weight:800;line-height:1;user-select:none;}
  .content{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;}
  header{display:flex;justify-content:space-between;align-items:flex-start;}
  header .left{display:flex;flex-direction:column;align-items:flex-start;}
  header .left .logo{width:auto;display:block;}
  header .left .tag{font-weight:600;color:var(--teal);}
  header .right{display:flex;flex-direction:column;align-items:flex-end;text-align:right;}
  header .right .pwm{font-family:'Syne',sans-serif;font-weight:800;color:var(--ink);line-height:1;}
  header .right .pwm .pd{color:var(--orange);}
  header .right .ptag{font-weight:600;color:var(--mid);}
  .hero{display:flex;flex-direction:column;align-items:flex-start;}
  .hero .kicker{font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--orange);background:rgba(242,108,33,.12);border-radius:40px;}
  .hero h1{font-family:'Baloo Chettan 2';font-weight:800;color:var(--red);padding-bottom:.04em;white-space:nowrap;}
  .hero .en{font-family:'Baloo 2';font-weight:800;letter-spacing:-1px;color:var(--ink);}
  .hero .en em{font-style:normal;color:var(--teal);}
  .hero .sub{font-family:'Baloo Chettan 2';font-weight:500;color:var(--ink);}
  .hero .sub2{font-weight:500;color:var(--mid);}
  .books{display:flex;justify-content:center;}
  .book{display:flex;flex-direction:column;align-items:center;}
  .book .cov{border-radius:14px;overflow:hidden;box-shadow:0 14px 32px rgba(43,38,32,.22);background:#fff;}
  .book .cov img{width:100%;height:100%;object-fit:cover;display:block;}
  .book .label{font-family:'Baloo Chettan 2';font-weight:700;color:var(--ink);line-height:1.25;text-align:center;}
  .book .lang{font-weight:600;color:var(--orange);letter-spacing:1px;text-transform:uppercase;}
  .book .bprice{display:flex;align-items:baseline;justify-content:center;font-family:'Baloo 2';}
  .book .bprice s{color:var(--mid);font-weight:600;}
  .book .bprice .now{color:var(--orange);font-weight:800;}
  .chips{display:flex;flex-wrap:wrap;justify-content:center;}
  .chip{font-weight:600;color:var(--teal);border:2px solid var(--teal);border-radius:40px;background:rgba(255,246,234,.7);}
  .cta{background:var(--teal);border-radius:22px;display:flex;align-items:center;}
  .cta .qrbox{background:#fff;border-radius:16px;flex:0 0 auto;}
  .cta .qrbox img{display:block;}
  .cta .txt{display:flex;flex-direction:column;color:#fff;}
  .cta .offertag{align-self:flex-start;background:var(--gold);color:#3a2400;font-family:'Baloo 2';font-weight:800;border-radius:30px;}
  .cta .scan-ml{font-family:'Baloo Chettan 2';font-weight:700;padding-bottom:.04em;}
  .cta .price{font-family:'Baloo 2';font-weight:800;color:var(--gold);}
  .cta .note{font-weight:600;color:#cdeee6;}
  .cta .contact{font-weight:600;color:#eafaf5;}
  .cta.noqr{justify-content:center;text-align:center;}
  .cta.noqr .txt{align-items:center;}
  .cta.noqr .offertag{align-self:center;}
  .credit{text-align:center;font-weight:500;color:var(--mid);}
  .credit b{color:var(--ink);} .credit .mlc{font-family:'Baloo Chettan 2';}
  .credit .osp{display:block;margin-top:5px;opacity:.6;font-size:.82em;letter-spacing:.5px;}
  [contenteditable]{outline:none;border-radius:6px;}
  [contenteditable]:hover{box-shadow:0 0 0 2px rgba(242,108,33,.3);}
  [contenteditable]:focus{box-shadow:0 0 0 2px var(--orange);}
"""

STYLE_A4 = u"""
  .poster{width:1315px;height:1829px;}
  .b1{width:560px;height:560px;background:var(--orange);top:-250px;left:-170px;opacity:.16;}
  .b2{width:470px;height:470px;background:var(--teal);top:-200px;right:-160px;opacity:.16;}
  .b3{width:400px;height:400px;background:var(--gold);bottom:300px;left:-200px;opacity:.12;}
  .content{padding:92px;justify-content:center;gap:36px;}
  header .left{gap:10px;} header .left .logo{height:62px;} header .left .tag{font-size:18px;}
  header .right{gap:8px;} header .right .pwm{font-size:58px;letter-spacing:-2.5px;} header .right .ptag{font-size:19px;}
  .hero{gap:8px;}
  .hero .kicker{font-size:20px;padding:9px 18px;}
  .hero h1{font-size:82px;line-height:1.16;}
  .hero .en{font-size:60px;line-height:1.05;}
  .hero .sub{font-size:30px;line-height:1.5;}
  .hero .sub2{font-size:26px;line-height:1.3;}
  .books{gap:36px;}
  .book{gap:10px;width:338px;}
  .book .cov{width:338px;height:478px;}
  .book .label{font-size:30px;} .book .lang{font-size:18px;margin-top:-6px;}
  .book .bprice{gap:12px;margin-top:2px;} .book .bprice s{font-size:26px;} .book .bprice .now{font-size:38px;}
  .chips{gap:14px;} .chip{font-size:22px;padding:10px 24px;}
  .cta{padding:36px 44px;gap:40px;}
  .cta .qrbox{padding:18px;} .cta .qrbox img{width:206px;height:206px;}
  .cta .txt{gap:5px;}
  .cta .offertag{font-size:22px;padding:5px 16px;}
  .cta .scan-ml{font-size:36px;line-height:1.3;margin-top:4px;}
  .cta .price{font-size:40px;line-height:1.15;}
  .cta .note{font-size:22px;line-height:1.2;} .cta .contact{font-size:22px;margin-top:4px;}
  .credit{font-size:27px;line-height:1.5;} .credit .mlc{font-size:23px;}
"""

STYLE_SQ = u"""
  .poster{width:1080px;height:1080px;}
  .b1{width:380px;height:380px;background:var(--orange);top:-160px;left:-120px;opacity:.15;}
  .b2{width:320px;height:320px;background:var(--teal);top:-130px;right:-110px;opacity:.15;}
  .b3{width:300px;height:300px;background:var(--gold);bottom:-120px;left:40%;opacity:.12;}
  .content{padding:54px;justify-content:space-between;gap:14px;}
  header .left{gap:6px;} header .left .logo{height:44px;} header .left .tag{font-size:15px;}
  header .right{gap:5px;} header .right .pwm{font-size:40px;letter-spacing:-2px;} header .right .ptag{font-size:14px;}
  .hero{gap:5px;}
  .hero .kicker{font-size:15px;padding:6px 14px;letter-spacing:1.5px;}
  .hero h1{font-size:50px;line-height:1.1;}
  .hero .en{font-size:34px;line-height:1.05;}
  .hero .sub{display:none;} .hero .sub2{display:none;}
  .books{gap:22px;}
  .book{gap:6px;width:184px;}
  .book .cov{width:184px;height:260px;}
  .book .label{font-size:20px;} .book .lang{font-size:13px;margin-top:-2px;}
  .book .bprice{gap:8px;margin-top:2px;} .book .bprice s{font-size:17px;} .book .bprice .now{font-size:27px;}
  .chips{display:none;}
  .cta{padding:22px 26px;gap:24px;}
  .cta .qrbox{padding:12px;} .cta .qrbox img{width:148px;height:148px;}
  .cta .txt{gap:3px;}
  .cta .offertag{font-size:15px;padding:4px 12px;}
  .cta .scan-ml{font-size:25px;line-height:1.25;margin-top:3px;}
  .cta .price{font-size:30px;line-height:1.1;}
  .cta .note{font-size:15px;line-height:1.2;} .cta .contact{font-size:15px;margin-top:2px;}
  .credit{font-size:19px;line-height:1.4;} .credit .mlc{font-size:16px;}
"""

STYLE_ST = u"""
  .poster{width:1080px;height:1920px;}
  .b1{width:460px;height:460px;background:var(--orange);top:-200px;left:-150px;opacity:.15;}
  .b2{width:380px;height:380px;background:var(--teal);top:-160px;right:-130px;opacity:.15;}
  .b3{width:360px;height:360px;background:var(--gold);bottom:260px;left:-170px;opacity:.12;}
  .content{padding:80px;justify-content:center;gap:40px;}
  header .left{gap:8px;} header .left .logo{height:56px;} header .left .tag{font-size:17px;}
  header .right{gap:6px;} header .right .pwm{font-size:50px;letter-spacing:-2.5px;} header .right .ptag{font-size:16px;}
  .hero{gap:8px;}
  .hero .kicker{font-size:18px;padding:8px 16px;}
  .hero h1{font-size:74px;line-height:1.16;}
  .hero .en{font-size:52px;line-height:1.05;}
  .hero .sub{font-size:27px;line-height:1.45;}
  .hero .sub2{font-size:24px;line-height:1.3;}
  .books{gap:24px;}
  .book{gap:9px;width:288px;}
  .book .cov{width:288px;height:407px;}
  .book .label{font-size:27px;} .book .lang{font-size:16px;margin-top:-4px;}
  .book .bprice{gap:10px;margin-top:2px;} .book .bprice s{font-size:22px;} .book .bprice .now{font-size:34px;}
  .chips{gap:12px;} .chip{font-size:19px;padding:8px 20px;}
  .cta{padding:30px 38px;gap:32px;}
  .cta .qrbox{padding:16px;} .cta .qrbox img{width:184px;height:184px;}
  .cta .txt{gap:5px;}
  .cta .offertag{font-size:19px;padding:5px 14px;}
  .cta .scan-ml{font-size:32px;line-height:1.25;margin-top:3px;}
  .cta .price{font-size:36px;line-height:1.12;}
  .cta .note{font-size:19px;line-height:1.2;} .cta .contact{font-size:19px;margin-top:3px;}
  .credit{font-size:23px;line-height:1.5;} .credit .mlc{font-size:19px;}
"""

CONTENT = u"""
      <header>
        <div class="left">
          <img class="logo" src="__LOGO__" alt="Xtraa">
          <div class="tag" contenteditable spellcheck="false">Your personal learning buddy</div>
        </div>
        <div class="right">
          <div class="pwm">Printosky<span class="pd">.</span></div>
          <div class="ptag" contenteditable spellcheck="false">Print it. Perfect. Fast.</div>
        </div>
      </header>

      <div class="hero">
        <div class="kicker" contenteditable spellcheck="false">Basics, done right</div>
        <h1 contenteditable spellcheck="false">ഭാഷയുടെ അടിസ്ഥാനം<br>ബലപ്പെടുത്തൂ</h1>
        <div class="en" contenteditable spellcheck="false">Set the basics <em>right.</em></div>
        <div class="sub" contenteditable spellcheck="false">അക്ഷരം മുതൽ വായന വരെ — കളിച്ചു പഠിക്കാം</div>
        <div class="sub2" contenteditable spellcheck="false">The foundation book for three Languages — Malayalam, Hindi &amp; English.</div>
      </div>

      <div class="books">
        <div class="book">
          <div class="cov"><img src="__ML__" alt="Aksharamrutham"></div>
          <div class="label" contenteditable spellcheck="false">അക്ഷരാമൃതം</div>
          <div class="lang">Malayalam</div>
          <div class="bprice"><s>₹250</s><span class="now">₹200</span></div>
        </div>
        <div class="book">
          <div class="cov"><img src="__HI__" alt="Vidyamrutam"></div>
          <div class="label" contenteditable spellcheck="false">വിദ്യാമൃതം</div>
          <div class="lang">Hindi</div>
          <div class="bprice"><s>₹200</s><span class="now">₹150</span></div>
        </div>
        <div class="book">
          <div class="cov"><img src="__EN__" alt="Easy English"></div>
          <div class="label" contenteditable spellcheck="false">ഈസി ഇംഗ്ലീഷ്</div>
          <div class="lang">English</div>
          <div class="bprice"><s>₹250</s><span class="now">₹200</span></div>
        </div>
      </div>

      <div class="chips">
        <span class="chip">Phonics</span><span class="chip">Sight Words</span>
        <span class="chip">Trace &amp; Write</span><span class="chip">Picture Stories</span>
      </div>

      <div class="cta">
        <div class="qrbox"><img src="__QR__" alt="Payment QR"></div>
        <div class="txt">
          <div class="offertag">✨ Special offer</div>
          <div class="scan-ml" contenteditable spellcheck="false">സ്കാൻ ചെയ്ത് ഓർഡർ ചെയ്യൂ</div>
          <div class="price" contenteditable spellcheck="false">Set of 3 — only ₹549</div>
          <div class="note" contenteditable spellcheck="false">Scan to order · printosky.com/books · Courier extra</div>
          <div class="contact" contenteditable spellcheck="false">WhatsApp 94957 06405 · printosky.com</div>
        </div>
      </div>

      <div class="credit">
        <span><b>Published by Xtraa</b> · Printed &amp; marketed by <b>Printosky</b></span><br>
        <span class="mlc">പ്രസിദ്ധീകരണം എക്സ്ട്രാ · അച്ചടിയും വിതരണവും പ്രിന്റോസ്കി</span>
        <span class="osp">Oxygen Students Paradise · Thrissur</span>
      </div>
"""

def page(style, W, H, N, dlname, blurb, noqr=False):
    content = CONTENT
    if noqr:
        content = (content
            .replace('<div class="qrbox"><img src="__QR__" alt="Payment QR"></div>', '')
            .replace('class="cta"', 'class="cta noqr"')
            .replace('സ്കാൻ ചെയ്ത് ഓർഡർ ചെയ്യൂ', 'വാട്ട്സ്ആപ്പിൽ ഓർഡർ ചെയ്യൂ')
            .replace('Scan to order · printosky.com/books · Courier extra',
                     'Order at printosky.com/books · Courier charges extra'))
    js = u"""
  function seeded(s){return function(){s|=0;s=s+0x6D2B79F5|0;var t=Math.imul(s^s>>>15,1|s);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
  function buildAlpha(){
    const box=document.getElementById('alpha'); if(!box) return;
    const ml=['അ','ആ','ഇ','ഈ','ഉ','ക','ഖ','ഗ','ച','ജ','ട','ണ','ത','ദ','ന','പ','മ','യ','ര','ല','വ','ശ','സ','ഹ','ള','ഴ','റ'];
    const hi=['अ','आ','इ','ई','उ','क','ख','ग','घ','च','ज','ट','ड','त','द','न','प','ब','म','य','र','ल','व','श','स','ह'];
    const en=['A','B','C','D','E','F','G','K','M','R','S','T','a','b','c','e','g','k','m','r','s','t','Q','Z'];
    const cols=['#F26C21','#1C7C6B','#F4A93C','#C53A2B','#2B2620'];
    const rnd=seeded(20260602); const W=__W__,H=__H__,N=__N__; let html='';
    for(let i=0;i<N;i++){
      const set=[ml,hi,en][Math.floor(rnd()*3)]; const ch=set[Math.floor(rnd()*set.length)];
      const isML=set===ml; const size=24+Math.floor(rnd()*__MAXS__);
      const x=Math.floor(rnd()*(W+60))-30; const y=Math.floor(rnd()*(H+60))-30;
      const rot=Math.floor(rnd()*50)-25; const col=cols[Math.floor(rnd()*cols.length)];
      const op=(0.05+rnd()*0.05).toFixed(3); const fam=isML?'Baloo Chettan 2':'Baloo 2';
      html+='<span class="alpha" style="left:'+x+'px;top:'+y+'px;font-size:'+size+'px;color:'+col+';opacity:'+op+';transform:rotate('+rot+'deg);font-family:\\''+fam+'\\',sans-serif;">'+ch+'</span>';
    }
    box.innerHTML=html;
  }
  buildAlpha();
  function fit(){const f=document.querySelector('.frame');const s=Math.min(1,f.clientWidth/__W__);document.getElementById('scaler').style.transform='scale('+s+')';f.style.height=(__H__*s)+'px';}
  window.addEventListener('resize',fit); fit();
  async function renderPoster(scale){
    const p=document.getElementById('poster'); await document.fonts.ready;
    const c=p.cloneNode(true); c.style.transform='none';c.style.position='fixed';c.style.left='0';c.style.top='-30000px';c.style.margin='0';
    document.body.appendChild(c); await document.fonts.ready;
    const cv=await html2canvas(c,{scale:scale,backgroundColor:'#FFF6EA',useCORS:true,logging:false,width:__W__,height:__H__});
    c.remove(); return cv;
  }
  async function dl(){const cv=await renderPoster(2);const a=document.createElement('a');a.download='__DLNAME__';a.href=cv.toDataURL('image/png');a.click();}
""".replace("__W__",str(W)).replace("__H__",str(H)).replace("__N__",str(N)).replace("__MAXS__",str(int(H*0.05))).replace("__DLNAME__",dlname)

    html = (u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Xtraa — """+blurb+u"""</title>
"""+HEAD+u"""<style>"""+VARS+style+u"""</style></head><body>
<div class="toolbar"><span>&#9999; <b>Click any text to edit</b>. """+blurb+u""". Then download PNG.</span>
<button onclick="dl()">&#11015; Download PNG</button></div>
<div class="frame"><div class="scaler" id="scaler"><div class="poster" id="poster">
<div class="bg"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div><div id="alpha"></div></div>
<div class="content">"""+content+u"""</div></div></div></div>
<script>"""+js+u"""</script></body></html>""")
    return (html.replace("__LOGO__",LOGO).replace("__ML__",ML).replace("__HI__",HI)
                .replace("__EN__",EN).replace("__QR__",QR))

builds = [
    ("poster.html",            STYLE_A4, 1315, 1829, 64, "xtraa-books-poster.png", "A4 print + 0.25in bleed",            False),
    ("poster-instagram.html",  STYLE_SQ, 1080, 1080, 40, "xtraa-instagram.png",    "Instagram square 1080x1080 (no bleed)", False),
    ("poster-story.html",      STYLE_ST, 1080, 1920, 58, "xtraa-story.png",        "Story 1080x1920 (no bleed)",        False),
    ("poster-whatsapp.html",   STYLE_ST, 1080, 1920, 58, "xtraa-whatsapp.png",     "WhatsApp status 1080x1920 (no QR)", True),
]
for fn, st, w, h, n, dln, blurb, noqr in builds:
    out = os.path.join(HERE, fn)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page(st, w, h, n, dln, blurb, noqr))
    print("wrote", fn, "(%.0f KB)" % (os.path.getsize(out)/1024))
