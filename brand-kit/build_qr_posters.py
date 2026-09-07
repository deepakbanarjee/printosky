# -*- coding: utf-8 -*-
"""Counter posters carrying the shop QRs, in three cuts:

    poster-qr-duo.png        both codes side by side
    poster-qr-pay.png        PhonePe / UPI payment only
    poster-qr-whatsapp.png   WhatsApp ordering only

Print-ready A4 portrait — HTML is authored at 1240x1754 CSS px and rendered at
2x, giving 2480x3508 px (A4 @ 300 DPI).

Sources:
  assets/phonepe-pay-qr.jpg  upi://pay?pa=9072034907-3@ibl&pn=ANU K J
  assets/whatsapp-qr.jpg     https://wa.me/message/ZDEFIURHNAPXA1?src=qr

The payment QR is never re-encoded — only its two flat tones are remapped, so
the module grid is bit-for-bit the original. The WhatsApp QR is re-drawn at
print resolution from its decoded link, because the source file is 407px wide
and would go soft on paper.
"""
import base64, io, os

import numpy as np
import qrcode
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

WHATSAPP_URL = "https://wa.me/message/ZDEFIURHNAPXA1?src=qr"

# brand
INK = "#182A3D"
ORANGE = "#F0571E"
# PhonePe
PP_PURPLE = "#5F259F"        # the QR panel
PP_DEEP = "#3B1266"          # the card behind it
# WhatsApp
WA_TEAL = "#075E54"          # card; 7.67:1 against white text
WA_GREEN = "#25D366"         # accents only — too light to carry white text

PAGE_W, PAGE_H = 1240, 1754  # A4 portrait in CSS px; rendered at 2x -> 300 DPI


def data_uri(path, mime):
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def _png_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def pay_qr_uri():
    """Recolour the PhonePe QR from black/white to purple/white.

    Only the two tones are remapped — every module keeps its position and
    polarity, so the encoded payload is untouched. Verified to still decode.
    """
    rgb = np.array(Image.open(os.path.join(ASSETS, "phonepe-pay-qr.jpg")).convert("RGB"))
    dark = rgb.mean(axis=2) < 128
    out = np.empty_like(rgb)
    out[dark] = [int(PP_PURPLE[i:i + 2], 16) for i in (1, 3, 5)]
    out[~dark] = [255, 255, 255]
    return _png_uri(Image.fromarray(out))


def whatsapp_qr_uri(url=WHATSAPP_URL):
    """Draw a wa.me QR at print size with high error correction."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return _png_uri(qr.make_image(fill_color=INK, back_color="white").convert("RGB"))


# Thrissur uses the central WhatsApp Business short-link; the Nattika store's own
# number is WhatsApp-enabled, so its QR is a plain wa.me/<number> deep link — the
# printed number and the code then open the same chat.
NATTIKA_WA_URL = "https://wa.me/919446903907"

LOGO = data_uri(os.path.join(HERE, "logo", "printosky-wordmark.png"), "image/png")
PAY_QR = pay_qr_uri()
WA_QR = whatsapp_qr_uri()
NATTIKA_WA_QR = whatsapp_qr_uri(NATTIKA_WA_URL)

CARD_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="5" width="20" height="14" rx="2.5"/><path d="M2 10h20"/>'
    '<path d="M6 15h4"/></svg>' % ORANGE
)
WA_ICON = (
    '<svg viewBox="0 0 24 24" fill="%s"><path d="M12.04 2c-5.46 0-9.9 4.44-9.9 9.9 0 '
    '1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.9-4.44 '
    '9.9-9.9S17.5 2 12.04 2zm5.8 14.03c-.24.68-1.4 1.3-1.93 1.36-.49.05-1.03.09-3.32-.7-2.8-'
    '.98-4.6-3.85-4.75-4.03-.14-.18-1.14-1.52-1.14-2.9 0-1.38.72-2.06.98-2.34.26-.28.56-.35.75-'
    '.35.19 0 .38 0 .54.01.17.01.4-.06.63.48.24.56.8 1.94.87 2.08.07.14.12.3.02.48-.09.18-.14.3-'
    '.28.45-.14.15-.29.34-.42.46-.14.13-.29.28-.12.55.16.28.72 1.19 1.55 1.93 1.06.95 1.96 1.24 '
    '2.24 1.38.28.14.44.12.6-.07.16-.19.68-.79.87-1.06.18-.28.37-.23.62-.14.26.09 1.62.76 1.9.9.28'
    '.14.46.21.53.33.07.12.07.68-.17 1.36z"/></svg>' % WA_GREEN
)

BRACKETS = ('<span class="b tl"></span><span class="b tr"></span>'
            '<span class="b bl"></span><span class="b br"></span>')

TILE = u"""
    <div class="tile {cls}">
      <div class="kicker">{icon}{kicker}</div>
      <div class="qrwrap">{brackets}<img src="{qr}" alt="{alt}"></div>
      <div class="big">{big}</div>
      <div class="small">{small}</div>
    </div>"""


def pay_tile():
    return TILE.format(cls="pay", icon=CARD_ICON, kicker="Scan to Pay", brackets=BRACKETS,
                       qr="__PAY_QR__", alt="PhonePe UPI payment QR", big="Any UPI app",
                       small="PhonePe &middot; Google Pay &middot; Paytm<br>BHIM &middot; your bank app")


def wa_tile():
    return TILE.format(cls="order", icon=WA_ICON, kicker="Scan to Order", brackets=BRACKETS,
                       qr="__WA_QR__", alt="WhatsApp ordering QR", big="+91 94957 06405",
                       small="Send your file on WhatsApp<br>&mdash; we quote &amp; print")


def nattika_wa_tile():
    return TILE.format(cls="order", icon=WA_ICON, kicker="Scan to Order", brackets=BRACKETS,
                       qr="__NAT_WA_QR__", alt="WhatsApp ordering QR (Nattika)", big="+91 94469 03907",
                       small="Send your file on WhatsApp<br>&mdash; we quote &amp; print")


# variant -> (headline, subhead, tiles html, layout class, output stem, place)
VARIANTS = {
    "duo": (
        "Scan to <em>Pay</em>.<br>Scan to <em>Order</em>.",
        "Point your phone camera at a code below &mdash; no app download, no typing.",
        pay_tile() + wa_tile(), "two", "poster-qr-duo", "Thrissur",
    ),
    "pay": (
        "Scan to <em>Pay</em>.",
        "Point your phone camera at the code &mdash; works with every UPI app.",
        pay_tile(), "one", "poster-qr-pay", "Thrissur",
    ),
    "whatsapp": (
        "Scan to <em>Order</em>.",
        "Point your phone camera at the code &mdash; the chat opens itself.",
        wa_tile(), "one", "poster-qr-whatsapp", "Thrissur",
    ),
    "nattika": (
        "Scan to <em>Order</em>.",
        "Point your phone camera at the code &mdash; the chat opens itself.",
        nattika_wa_tile(), "one", "poster-qr-nattika", "Nattika",
    ),
}

HTML = u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Printosky &mdash; QR poster</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#182A3D; --orange:#F0571E;
    --pp-purple:#5F259F; --pp-deep:#3B1266;
    --wa-teal:#075E54; --wa-green:#25D366;
    --paper:#F7F3ED; --mid:#6B7280;
  }
  *{margin:0;padding:0;box-sizing:border-box;}
  html,body{background:#cfc7b8;}
  body{font-family:'DM Sans',Segoe UI,sans-serif;}

  .page{
    width:1240px;height:1754px;position:relative;overflow:hidden;
    background-color:var(--paper);
    background-image:radial-gradient(circle, rgba(24,42,61,.055) 2px, transparent 2.1px);
    background-size:40px 40px;
    padding:104px 88px 0;
    display:flex;flex-direction:column;align-items:center;
  }

  /* brand rules, top and bottom */
  .bar{position:absolute;left:0;right:0;height:18px;background:var(--ink);z-index:2;}
  .bar.top{top:0;} .bar.bot{bottom:0;}
  .bar::after{content:"";position:absolute;top:0;bottom:0;width:300px;background:var(--orange);}
  .bar.top::after{right:0;} .bar.bot::after{left:0;}

  /* ---------- masthead ---------- */
  /* logo asset ships on white; multiply drops it onto the cream paper */
  .logo{width:640px;display:block;mix-blend-mode:multiply;}
  .eyebrow{
    margin-top:22px;font-weight:600;font-size:22px;letter-spacing:5.5px;
    text-transform:uppercase;color:var(--mid);text-align:center;
  }
  .headline{
    margin-top:52px;font-family:'Syne',sans-serif;font-weight:800;
    font-size:82px;line-height:1.02;letter-spacing:-2.5px;
    color:var(--ink);text-align:center;
  }
  .headline em{font-style:normal;color:var(--orange);}
  .subhead{margin-top:22px;font-size:27px;color:var(--mid);text-align:center;max-width:840px;line-height:1.45;}

  /* ---------- QR tiles ---------- */
  .tiles{margin-top:60px;display:flex;gap:34px;width:100%;justify-content:center;}
  .tile{
    min-width:0;border-radius:30px;padding:40px 34px 34px;
    display:flex;flex-direction:column;align-items:center;
    box-shadow:0 26px 60px rgba(24,42,61,.18);
  }
  .tiles.two .tile{flex:1;}
  .tiles.one .tile{width:700px;padding:52px 44px 44px;}

  .tile.pay{background:var(--pp-deep);}
  .tile.order{background:var(--wa-teal);}

  .tile .kicker{
    display:flex;align-items:center;gap:12px;color:#fff;
    font-weight:700;font-size:23px;letter-spacing:4px;text-transform:uppercase;
  }
  .tiles.one .tile .kicker{font-size:26px;}
  .tile .kicker svg{width:30px;height:30px;flex:none;}

  /* Both wraps land on one square footprint so paired tiles stay in lockstep.
     The PhonePe artwork carries its own thick quiet zone, so it is scaled up and
     given less padding — that makes the two code grids read at the same size. */
  .qrwrap{position:relative;margin-top:26px;border-radius:22px;}
  .tile.pay   .qrwrap{background:var(--pp-purple);padding:11px;}
  .tile.order .qrwrap{background:#fff;padding:24px;}
  .tiles.two .tile.pay   .qrwrap img{display:block;width:408px;height:408px;}
  .tiles.two .tile.order .qrwrap img{display:block;width:382px;height:382px;}
  .tiles.one .tile.pay   .qrwrap img{display:block;width:538px;height:538px;}
  .tiles.one .tile.order .qrwrap img{display:block;width:512px;height:512px;}

  /* orange scan brackets */
  .qrwrap .b{position:absolute;width:44px;height:44px;border-color:var(--orange);border-style:solid;border-width:0;}
  .qrwrap .b.tl{top:8px;left:8px;border-top-width:7px;border-left-width:7px;border-top-left-radius:16px;}
  .qrwrap .b.tr{top:8px;right:8px;border-top-width:7px;border-right-width:7px;border-top-right-radius:16px;}
  .qrwrap .b.bl{bottom:8px;left:8px;border-bottom-width:7px;border-left-width:7px;border-bottom-left-radius:16px;}
  .qrwrap .b.br{bottom:8px;right:8px;border-bottom-width:7px;border-right-width:7px;border-bottom-right-radius:16px;}

  .tile .big{
    margin-top:28px;font-family:'Syne',sans-serif;font-weight:800;
    font-size:34px;letter-spacing:-1.2px;white-space:nowrap;color:#fff;
  }
  .tiles.one .tile .big{font-size:46px;margin-top:34px;}
  .tile .small{margin-top:10px;font-size:22px;text-align:center;line-height:1.4;color:rgba(255,255,255,.66);}
  .tiles.one .tile .small{font-size:25px;}

  /* ---------- footer ---------- */
  .foot{
    margin-top:auto;width:100%;padding:34px 0 46px;
    border-top:3px solid rgba(24,42,61,.12);
    display:flex;align-items:center;justify-content:space-between;
  }
  .foot .where{font-family:'Syne',sans-serif;font-weight:700;font-size:31px;color:var(--ink);letter-spacing:-.5px;}
  .foot .where span{display:block;font-family:'DM Sans';font-weight:500;font-size:22px;color:var(--mid);letter-spacing:.4px;margin-top:6px;}
  .foot .site{font-weight:700;font-size:30px;color:var(--orange);letter-spacing:.4px;}
</style></head><body>

<div class="page">
  <div class="bar top"></div><div class="bar bot"></div>

  <img class="logo" src="__LOGO__" alt="Printosky">
  <div class="eyebrow">Print &middot; Bind &middot; Publish &nbsp;&middot;&nbsp; __PLACE__</div>

  <div class="headline">__HEADLINE__</div>
  <div class="subhead">__SUBHEAD__</div>

  <div class="tiles __LAYOUT__">__TILES__</div>

  <div class="foot">
    <div class="where">__PLACE__, Kerala<span>Walk in, or send it on WhatsApp</span></div>
    <div class="site">printosky.com</div>
  </div>
</div>

</body></html>
"""


def render_png(html_path, png_path):
    """Shoot the .page element at 2x, same approach as tools/build_insert.py."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": PAGE_W, "height": PAGE_H}, device_scale_factor=2
        )
        page.goto("file:///" + html_path.replace("\\", "/"), wait_until="networkidle")
        page.wait_for_timeout(1200)          # let webfonts settle before the shot
        page.query_selector(".page").screenshot(path=png_path)
        browser.close()


def build(variant):
    headline, subhead, tiles, layout, stem, place = VARIANTS[variant]
    html = (HTML.replace("__HEADLINE__", headline)
                .replace("__SUBHEAD__", subhead)
                .replace("__LAYOUT__", layout)
                .replace("__PLACE__", place)
                .replace("__TILES__", tiles)
                .replace("__LOGO__", LOGO)
                .replace("__PAY_QR__", PAY_QR)
                .replace("__NAT_WA_QR__", NATTIKA_WA_QR)
                .replace("__WA_QR__", WA_QR))
    out_html = os.path.join(HERE, stem + ".html")
    out_png = os.path.join(HERE, stem + ".png")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    render_png(out_html, out_png)
    print("%-22s %6.0f KB html   %6.0f KB png"
          % (stem, os.path.getsize(out_html) / 1024, os.path.getsize(out_png) / 1024))


def main():
    for variant in VARIANTS:
        build(variant)


if __name__ == "__main__":
    main()
