# -*- coding: utf-8 -*-
"""Personalized Aksharamrutham poster for Divya teacher to share on WhatsApp.

One-off variant of build_poster.py: single book (Malayalam only), features
Divya as author/contact, sized for WhatsApp sharing (1080x1080 square).
"""
import base64, io, os
import qrcode

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "website", "assets")
DRDIVYAM_IMAGES = os.path.join(HERE, "..", "website", "drdivyam", "images")
DIVYA_BOOKS_URL = "https://printosky.com/divya.html"

def data_uri(path, fmt="JPEG"):
    with open(path, "rb") as f:
        b = f.read()
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(b).decode("ascii")

def qr_data_uri(url):
    img = qrcode.make(url, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

LOGO  = data_uri(os.path.join(HERE, "logo", "printosky-wordmark.png"), fmt="PNG")
COVER = data_uri(os.path.join(ASSETS, "book-malayalam.jpg"), fmt="JPEG")
DIVYA = data_uri(os.path.join(DRDIVYAM_IMAGES, "divya-portrait.jpg"), fmt="JPEG")
QR    = qr_data_uri(DIVYA_BOOKS_URL)

HTML = u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aksharamrutham — Divya's WhatsApp poster</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@500;600;700;800&family=Baloo+Chettan+2:wght@500;600;700;800&family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{--cream:#FFF6EA;--orange:#F26C21;--teal:#1C7C6B;--red:#C53A2B;--ink:#2B2620;--gold:#F4A93C;--mid:#7c7367;--white:#fff;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:#cfc7b8;display:flex;justify-content:center;align-items:center;padding:24px;font-family:'Baloo Chettan 2',sans-serif;}
  .poster{position:relative;width:1080px;height:1080px;background:var(--cream);overflow:hidden;border-radius:0;}
  .bg{position:absolute;inset:0;z-index:0;}
  .blob{position:absolute;border-radius:50%;}
  .b1{width:420px;height:420px;background:var(--orange);top:-180px;left:-140px;opacity:.14;}
  .b2{width:360px;height:360px;background:var(--teal);top:-140px;right:-120px;opacity:.14;}
  .b3{width:340px;height:340px;background:var(--gold);bottom:-160px;left:120px;opacity:.12;}
  .content{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;padding:22px 48px 28px;}

  header .brandrow{display:flex;align-items:center;gap:12px;}
  header img.logo{height:34px;}
  header .ptag{font-family:'Baloo 2',sans-serif;font-weight:600;color:var(--mid);font-size:15px;line-height:1.3;}

  .headline{text-align:center;margin-top:2px;}
  .headline .kicker{display:inline-block;background:var(--teal);color:#fff;font-weight:600;font-size:22px;padding:9px 20px;border-radius:100px;}
  .headline h1{font-weight:800;color:var(--red);font-size:64px;line-height:1.05;margin-top:14px;}
  .headline .sub{font-weight:600;color:var(--ink);font-size:34px;margin-top:12px;line-height:1.26;}
  .headline .sub2{font-size:21px;color:var(--mid);margin-top:14px;line-height:1.4;max-width:984px;margin-left:auto;margin-right:auto;}

  .coverwrap{display:flex;justify-content:center;margin-top:4px;}
  .coverbox{position:relative;width:305px;height:421px;border-radius:24px;overflow:hidden;box-shadow:0 14px 28px rgba(43,38,32,.22);border:5px solid var(--gold);transform:rotate(-1.5deg);}
  .coverbox img{width:100%;height:100%;object-fit:cover;display:block;}
  .coverbox .cap{position:absolute;left:0;right:0;bottom:0;background:rgba(43,38,32,.62);color:#fff;text-align:center;padding:10px 8px;font-size:15px;line-height:1.28;}
  .coverbox .cap b{font-size:18px;}

  .bottomrow{margin-top:auto;padding-top:4px;display:flex;align-items:stretch;gap:18px;}
  .bottomleft{flex:1;min-width:0;display:flex;flex-direction:column;gap:14px;}
  .priceblock{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;background:var(--white);border-radius:16px;padding:12px 16px;box-shadow:0 4px 0 rgba(43,38,32,.06);}
  .priceblock .offertag{font-family:'Baloo Chettan 2',sans-serif;display:inline-block;background:var(--red);color:#fff;font-weight:700;font-size:14px;padding:5px 12px;border-radius:100px;margin-bottom:8px;}
  .priceblock .row{display:flex;align-items:baseline;gap:11px;}
  .priceblock s{font-family:'Baloo Chettan 2',sans-serif;font-size:19px;color:var(--mid);}
  .priceblock .now{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:42px;color:var(--orange);}
  .priceblock .courier{font-family:'Baloo Chettan 2',sans-serif;font-weight:600;font-size:16px;color:var(--teal);margin-top:6px;}

  .cta{flex-shrink:0;background:var(--ink);border-radius:16px;padding:13px 16px;display:flex;align-items:center;gap:12px;}
  .cta .wa{width:38px;height:38px;border-radius:50%;background:#25D366;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .cta .wa svg{width:20px;height:20px;}
  .cta .txt .lbl{font-family:'Baloo Chettan 2',sans-serif;color:#d9cfc0;font-size:11px;font-weight:600;}
  .cta .txt .num{font-family:'Baloo 2',sans-serif;color:#fff;font-weight:700;font-size:18px;letter-spacing:.3px;}
  .divyabox{width:276px;height:276px;border-radius:24px;overflow:hidden;box-shadow:0 14px 28px rgba(43,38,32,.22);border:5px solid var(--gold);flex-shrink:0;}
  .divyabox img{width:100%;height:100%;object-fit:cover;object-position:center 20%;display:block;}
  .qrbox{width:276px;height:276px;border-radius:24px;background:var(--white);border:5px solid var(--gold);box-shadow:0 14px 28px rgba(43,38,32,.22);display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .qrbox img{width:86%;height:86%;object-fit:contain;}

  .credit{margin-top:4px;text-align:center;font-size:13px;color:var(--mid);line-height:1.5;}
  .credit b{color:var(--ink);}
</style></head>
<body>
  <div class="poster">
    <div class="bg"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div></div>
    <div class="content">
      <header>
        <div class="brandrow">
          <img class="logo" src="__LOGO__" alt="Printosky">
          <div class="ptag">printosky.com</div>
        </div>
        <div class="headline">
          <span class="kicker">ഭാഷയുടെ അടിസ്ഥാനം ശക്തമാക്കാം</span>
          <h1>അക്ഷരാമൃതം</h1>
          <div class="sub">അക്ഷരം മുതൽ വായന വരെ — കളിച്ചു പഠിക്കാം</div>
          <div class="sub2">അക്ഷരം തൊട്ട് വായന വരെയുള്ള അടിസ്ഥാന മലയാളം പഠന പുസ്തകം. ശബ്ദപഠനം, വർക്ക്ബുക്കുകൾ, എഴുത്ത് പരിശീലനം, ചിത്രകഥകൾ എന്നിവയിലൂടെ കുട്ടികളെ ആത്മവിശ്വാസത്തോടെ വായിക്കാൻ പ്രാപ്തരാക്കുന്നു.</div>
        </div>
      </header>

      <div class="coverwrap">
        <div class="coverbox">
          <img src="__COVER__" alt="അക്ഷരാമൃതം പുസ്തകം">
          <div class="cap">രചന :<br><b>ദിവ്യ എം</b></div>
        </div>
      </div>

      <div class="bottomrow">
        <div class="bottomleft">
          <div class="priceblock">
            <div class="offertag">✨ പ്രത്യേക ഓഫർ</div>
            <div class="row"><s>എം.ആർ.പി ₹250</s><span class="now">₹200</span></div>
            <div class="courier">+ Courier</div>
          </div>
          <div class="cta">
            <div class="wa"><svg viewBox="0 0 24 24" fill="#fff"><path d="M12.04 2c-5.46 0-9.9 4.44-9.9 9.9 0 1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.9-4.44 9.9-9.9S17.5 2 12.04 2zm5.8 14.03c-.24.68-1.4 1.3-1.93 1.36-.49.05-1.03.09-3.32-.7-2.8-.98-4.6-3.85-4.75-4.03-.14-.18-1.14-1.52-1.14-2.9 0-1.38.72-2.06.98-2.34.26-.28.56-.35.75-.35.19 0 .38 0 .54.01.17.01.4-.06.63.48.24.56.8 1.94.87 2.08.07.14.12.3.02.48-.09.18-.14.3-.28.45-.14.15-.29.34-.42.46-.14.13-.29.28-.12.55.16.28.72 1.19 1.55 1.93 1.06.95 1.96 1.24 2.24 1.38.28.14.44.12.6-.07.16-.19.68-.79.87-1.06.18-.28.37-.23.62-.14.26.09 1.62.76 1.9.9.28.14.46.21.53.33.07.12.07.68-.17 1.36z"/></svg></div>
            <div class="txt">
              <div class="lbl">വാട്സാപ്പിൽ ഓർഡർ ചെയ്യൂ</div>
              <div class="num">+91 94957 06405</div>
            </div>
          </div>
        </div>
        <div class="divyabox"><img src="__DIVYA__" alt="ഡോ. ദിവ്യ എം"></div>
        <div class="qrbox"><img src="__QR__" alt="QR to Divya's books page"></div>
      </div>

      <div class="credit">
        <b>പ്രിന്റോസ്കി</b> പ്രസിദ്ധീകരിച്ച് അച്ചടിച്ചത് · തൃശ്ശൂർ
      </div>
    </div>
  </div>
</body></html>
"""

def main():
    html = HTML.replace("__LOGO__", LOGO).replace("__DIVYA__", DIVYA).replace("__COVER__", COVER).replace("__QR__", QR)
    out = os.path.join(HERE, "poster-divya-aksharamrutham.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", out)

if __name__ == "__main__":
    main()
