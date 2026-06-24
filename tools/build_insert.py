# -*- coding: utf-8 -*-
"""Rebuild the Xtraa "Thank You" parcel insert with the new Printosky logo.

The original insert (right half of the despatch sheet) was a flat image baked
with the OLD plain-text "PRINTOSKY" logo. This rebuilds it as HTML using the
new Oxygen-O brand mark + real book-cover assets, then renders it to a crisp
PNG that gen_despatch_slips.py drops into the right panel of every slip.

Run:  python tools/build_insert.py
Output: tools/assets/xtraa_insert_v2.png   (panel aspect 137:210, rendered @3x)
"""
from __future__ import annotations

import base64
import os

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "website", "assets")
OUT_DIR = os.path.join(ROOT, "tools", "assets")
OUT_PNG = os.path.join(OUT_DIR, "xtraa_insert_v2.png")

# Confirmed Printosky logo (capital-P + Oxygen-O bowl) from the production SVG.
with open(os.path.join(ROOT, "brand-kit", "logo", "printosky-wordmark.svg"),
          encoding="utf-8") as _fh:
    LOGO_SVG = _fh.read()

# Insert panel matches the slip's right panel: 137mm x 210mm  ->  549 x 840 px.
W, H, SCALE = 549, 840, 3

ORANGE = "#F0571E"
NAVY = "#1B3F8B"
INK = "#17130F"
TEAL = "#1597C4"
DOT = "#182A3D"


def _uri(path: str) -> str:
    ext = "jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "png"
    with open(path, "rb") as fh:
        return "data:image/%s;base64,%s" % (ext, base64.b64encode(fh.read()).decode())


def _card(cover, tab, title, mrp, price, author):
    return (
        '<div class="card">'
        '<div class="cov"><img src="' + cover + '"></div>'
        '<div class="tab">' + tab + '</div>'
        '<div class="ttl">' + title + '</div>'
        '<div class="pr"><s>&#8377;' + str(mrp) + '</s> '
        '<b>&#8377;' + str(price) + '</b></div>'
        '<div class="au">' + author + '</div>'
        '</div>'
    )


def build_html() -> str:
    ml = _uri(os.path.join(ASSETS, "book-malayalam.jpg"))
    hi = _uri(os.path.join(ASSETS, "book-hindi.jpg"))
    en = _uri(os.path.join(ASSETS, "book-english.jpg"))
    cards = (
        _card(ml, "Malayalam", "Aksharamrutham", 250, 200, "Pradeep K K &amp; Dr. Divya M")
        + _card(hi, "Hindi", "Vidyamrut", 200, 150, "Pradeep K K")
        + _card(en, "English", "Easy English", 250, 200, "Pradeep K K")
    )
    css = """
*{margin:0;padding:0;box-sizing:border-box;}
html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
body{width:%(W)spx;height:%(H)spx;font-family:'DM Sans',Arial,sans-serif;
  color:%(INK)s;background:#fff;display:flex;flex-direction:column;overflow:hidden;}
.top{background:%(ORANGE)s;color:#fff;text-align:center;padding:14px 0 12px;}
.logo{display:flex;align-items:center;justify-content:center;}
.plaque{background:#fff;border-radius:8px;padding:8px 17px;display:inline-flex;align-items:center;}
.plaque svg{height:30px;width:auto;display:block;}
.tsub{font-size:10px;margin-top:7px;opacity:.95;letter-spacing:.3px;}
.mid{flex:1;padding:18px 30px 12px;display:flex;flex-direction:column;}
.heart{color:%(ORANGE)s;text-align:center;font-size:17px;}
h1{font-family:'Syne',sans-serif;font-weight:800;color:%(NAVY)s;text-align:center;
  font-size:27px;letter-spacing:-.6px;margin:5px 0 9px;}
.lead{text-align:center;color:#555;font-size:12.5px;line-height:1.5;
  max-width:330px;margin:0 auto 14px;}
.div{display:flex;align-items:center;gap:10px;margin:4px 6px 16px;}
.div .ln{flex:1;height:1px;background:#e7c9bb;}
.div .t{color:%(ORANGE)s;font-size:10px;font-weight:700;letter-spacing:1.6px;}
.cards{display:flex;gap:11px;}
.card{flex:1;text-align:center;}
.cov{border:1px solid #eee;border-radius:5px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.10);}
.cov img{width:100%%;height:148px;object-fit:cover;display:block;}
.tab{display:inline-block;background:%(NAVY)s;color:#fff;font-size:9px;font-weight:700;
  letter-spacing:.4px;padding:2px 11px;border-radius:99px;margin:9px 0 4px;}
.ttl{font-weight:700;font-size:14.5px;color:%(INK)s;}
.pr{font-size:13px;margin:3px 0 2px;}
.pr s{color:#9aa0a6;font-size:11px;}
.pr b{color:%(ORANGE)s;font-size:15px;}
.au{font-size:9px;font-weight:700;color:%(ORANGE)s;line-height:1.3;}
.offer{margin-top:16px;border:1.5px solid %(ORANGE)s;background:#fdf1ea;border-radius:10px;
  text-align:center;padding:9px 10px;}
.offer .o1{color:%(ORANGE)s;font-weight:800;font-size:14.5px;}
.offer .o2{color:#666;font-size:10px;margin-top:2px;}
.bot{background:%(ORANGE)s;color:#fff;text-align:center;padding:11px 0 12px;}
.bot .b1{font-weight:800;font-size:14px;letter-spacing:.2px;}
.bot .b2{font-size:9.5px;margin-top:3px;opacity:.95;}
""" % {"W": W, "H": H, "ORANGE": ORANGE, "NAVY": NAVY, "INK": INK}
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800'
        '&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">'
        "<style>" + css + "</style></head><body>"
        '<div class="top"><div class="logo"><span class="plaque">' + LOGO_SVG + '</span></div>'
        '<div class="tsub">printosky.com &nbsp;&middot;&nbsp; Thriprayar, Thrissur 680567</div></div>'
        '<div class="mid">'
        '<div class="heart">&#10084;</div>'
        '<h1>Thank You for Your Order!</h1>'
        '<div class="lead">We hope these books bring joy and confidence to your '
        'little learner. Printed with care in Kerala.</div>'
        '<div class="div"><span class="ln"></span>'
        '<span class="t">THE XTRAA LEARNING COLLECTION</span><span class="ln"></span></div>'
        '<div class="cards">' + cards + '</div>'
        '<div class="offer"><div class="o1">Get all 3 books &mdash; Only &#8377;549 '
        '<span style="font-weight:600;font-size:12px">(Save &#8377;101)</span></div>'
        '<div class="o2">Malayalam + Hindi + English &nbsp;&middot;&nbsp; + courier '
        '&nbsp;&middot;&nbsp; Order on WhatsApp</div></div>'
        '</div>'
        '<div class="bot"><div class="b1">Order on WhatsApp: 94957 06405</div>'
        '<div class="b2">printosky.com &nbsp;&middot;&nbsp; Fast delivery across Kerala</div></div>'
        "</body></html>"
    )


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    html_doc = build_html()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  device_scale_factor=SCALE)
        page = ctx.new_page()
        page.set_content(html_doc, wait_until="networkidle")
        page.wait_for_timeout(1500)  # let webfonts settle
        page.screenshot(path=OUT_PNG)
        browser.close()
    print("WROTE", OUT_PNG)


if __name__ == "__main__":
    main()
