# -*- coding: utf-8 -*-
"""Announcement poster (no price/CTA) crediting Divya's 8/9/10 question-bank +
answer-key resource. Uses Printosky's standard brand system (cream + blobs,
gold-bordered photo box, kicker/headline typography) — matches the visual
language of build_poster_divya.py. Sized for WhatsApp sharing (1080x1080).
"""
import base64, os

HERE = os.path.dirname(os.path.abspath(__file__))
DRDIVYAM_IMAGES = os.path.join(HERE, "..", "website", "drdivyam", "images")

def data_uri(path, fmt="JPEG"):
    with open(path, "rb") as f:
        b = f.read()
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(b).decode("ascii")

LOGO  = data_uri(os.path.join(HERE, "logo", "printosky-wordmark.png"), fmt="PNG")
DIVYA = data_uri(os.path.join(DRDIVYAM_IMAGES, "divya-portrait.jpg"), fmt="JPEG")

HTML = u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Divya — 8/9/10 question bank &amp; answer key poster</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@500;600;700;800&family=Baloo+Chettan+2:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--cream:#FFF6EA;--orange:#F26C21;--teal:#1C7C6B;--red:#C53A2B;--ink:#2B2620;--gold:#F4A93C;--mid:#7c7367;--white:#fff;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:#cfc7b8;display:flex;justify-content:center;align-items:center;padding:24px;font-family:'Baloo Chettan 2',sans-serif;}
  .poster{position:relative;width:1080px;height:1080px;background:var(--cream);overflow:hidden;}
  .bg{position:absolute;inset:0;z-index:0;}
  .blob{position:absolute;border-radius:50%;}
  .b1{width:420px;height:420px;background:var(--orange);top:-180px;left:-140px;opacity:.14;}
  .b2{width:360px;height:360px;background:var(--teal);top:-140px;right:-120px;opacity:.14;}
  .b3{width:340px;height:340px;background:var(--gold);bottom:-160px;left:120px;opacity:.12;}
  .content{position:relative;z-index:1;height:100%;display:grid;grid-template-rows:auto 1fr auto;padding:44px 48px 32px;}

  header{width:100%;display:flex;justify-content:space-between;align-items:center;}
  header img.logo{height:38px;}
  header .ptag{font-family:'Baloo 2',sans-serif;font-weight:600;color:var(--mid);font-size:15px;text-align:right;line-height:1.3;}

  .middle{display:flex;flex-direction:column;align-items:center;justify-content:center;}

  .box{position:relative;width:380px;height:460px;border-radius:22px;overflow:hidden;box-shadow:0 14px 28px rgba(43,38,32,.22);border:4px solid var(--gold);flex-shrink:0;}
  .box img{width:100%;height:100%;object-fit:cover;object-position:center 18%;display:block;}

  .headline{margin-top:30px;text-align:center;}
  .headline .kicker{display:inline-block;background:var(--teal);color:#fff;font-weight:700;font-size:30px;padding:8px 22px;border-radius:100px;}
  .headline .desc{font-weight:700;color:var(--ink);font-size:30px;line-height:1.5;margin-top:16px;max-width:860px;}

  .by{margin-top:18px;text-align:center;color:var(--mid);font-weight:600;font-size:18px;}

  .credit{margin-top:20px;background:var(--white);border-radius:18px;padding:22px 40px;box-shadow:0 4px 0 rgba(43,38,32,.06);text-align:center;}
  .credit .name{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:50px;color:var(--red);}
  .credit .org{font-weight:700;font-size:26px;color:var(--ink);margin-top:6px;}
  .credit .place{font-weight:700;font-size:26px;color:var(--ink);}

  .footer{text-align:center;font-size:13px;color:var(--mid);line-height:1.5;}
  .footer b{color:var(--ink);}
</style></head>
<body>
  <div class="poster">
    <div class="bg"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div></div>
    <div class="content">
      <header>
        <img class="logo" src="__LOGO__" alt="Printosky">
        <div class="ptag">printosky.com</div>
      </header>

      <div class="middle">
        <div class="box"><img src="__DIVYA__" alt="ദിവ്യ എം"></div>

        <div class="headline">
          <span class="kicker">8 , 9 ,10</span>
          <div class="desc">കേരളപാഠാവലി &amp; അടിസ്ഥാനപാഠാവലി എന്നിവയെ ആസ്പദമാക്കി നിർമ്മിച്ച ചോദ്യപേപ്പറുകളും അതിന്‍റെ ഉത്തരസൂചികയും</div>
        </div>

        <div class="by">തയ്യാറാക്കിയത്</div>

        <div class="credit">
          <div class="name">ദിവ്യ എം</div>
          <div class="org">വി.ബി. എച്ച് .എസ്. എസ്</div>
          <div class="place">തൃശ്ശൂർ</div>
        </div>
      </div>

      <div class="footer">
        <b>പ്രിന്റോസ്കി</b> അച്ചടിച്ചത് · തൃശ്ശൂർ
      </div>
    </div>
  </div>
</body></html>
"""

def main():
    html = HTML.replace("__LOGO__", LOGO).replace("__DIVYA__", DIVYA)
    out = os.path.join(HERE, "poster-divya-question-bank.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", out)

if __name__ == "__main__":
    main()
