# -*- coding: utf-8 -*-
"""Branded courier-slip HTML for the admin dispatch flow.

Server-side port of tools/gen_despatch_slips.py: A4-landscape, one order/page,
left = cut-out courier slip (FROM / TO / contents / courier + AWB), right = the
Thank-You parcel insert. Assets are referenced by URL from the Netlify site
(netlify.toml publish="website"), so nothing needs bundling into the Vercel
function. Pure functions — no DB, no network — so it is easy to test.
"""
from __future__ import annotations

import html as _html
import re

SITE = "https://printosky.com"
LOGO_URL   = f"{SITE}/assets/printosky-wordmark.svg"
INSERT_URL = f"{SITE}/assets/xtraa-thank-you-insert.png"

# Human-facing book names for the courier slip "Contents" line.
BOOK_NAMES = {
    "malayalam": "Malayalam",
    "hindi":     "Hindi",
    "english":   "English",
}


def _contents(items: dict) -> str:
    parts = [f"{BOOK_NAMES.get(k, k.title())} x {int(v)}"
             for k, v in (items or {}).items() if v]
    return ", ".join(parts) or "—"


def _phone(p: str) -> str:
    p = re.sub(r"\D", "", p or "")
    if len(p) == 12 and p.startswith("91"):
        p = p[2:]
    return "+91 %s %s" % (p[:5], p[5:]) if len(p) == 10 else (("+" + p) if p else "—")


def _first_tok(s: str) -> str:
    m = re.findall(r"[A-Za-z]+", s or "")
    return m[0].lower() if m else ""


def _clean(name: str, addr: str):
    """Drop junk lines; promote an address-embedded name to the bold header.

    Ported from tools/gen_despatch_slips.py — the Anu intake parser sometimes
    stores the name inside the address or leaves stray phone-only lines.
    """
    name = (name or "").strip().rstrip(",").strip()
    lines = []
    for ln in (x.strip() for x in (addr or "").split("\n")):
        if not ln or ln == ".":
            continue
        if len(re.sub(r"\D", "", ln)) >= 10 and not re.search(r"[A-Za-z]{3,}", ln):
            continue  # a bare phone-only line
        lines.append(ln)
    if lines and _first_tok(lines[0]) and _first_tok(lines[0]) == _first_tok(name):
        name, lines = lines[0].rstrip(","), lines[1:]
    return name or "—", lines


def _slip(order: dict) -> str:
    code     = order.get("order_code", "")
    contents = _contents(order.get("items") or {})
    value    = float(order.get("grand_total") or 0)
    paid     = value > 0 and float(order.get("amount_paid") or 0) >= value
    name, lines = _clean(order.get("name", ""), order.get("address", "") or "")
    phone     = order.get("phone") or order.get("contact_phone") or ""
    addr_html = "<br>".join(_html.escape(l) for l in lines) or "&mdash;"
    paid_html = ' <span class="paid">PAID</span>' if paid else ""
    return (
        '<div class="slip"><div class="left">'
        '<div class="hdr"><div class="logo">'
        f'<span class="plaque"><img src="{LOGO_URL}" alt="Printosky"></span></div>'
        '<div class="doc"><div class="dt">DESPATCH SLIP &mdash; Printosky Books</div>'
        f'<div class="code">{_html.escape(code)}</div></div></div>'
        '<div class="body"><div class="lbl">FROM</div>'
        '<div class="from">Printosky<br>Thriprayar<br>Thrissur, Kerala - 680567'
        '<br>+91 94957 06405<br>printosky.com</div>'
        '<div class="tobox"><span class="totab">TO</span>'
        f'<div class="toname">{_html.escape(name)}</div>'
        f'<div class="toaddr">{addr_html}</div>'
        f'<div class="toph">Ph: {_html.escape(_phone(phone))}</div></div>'
        '<div class="foot"><div class="fcol">'
        f'<div class="fc">Contents: <b>{_html.escape(contents)}</b></div>'
        f'<div class="fc">Order Value: <b>&#8377;{value:.0f}</b>{paid_html}</div>'
        '</div>'
        '<div class="fw"><div>Courier: <span class="ln"></span></div>'
        '<div>AWB No: <span class="ln"></span></div></div>'
        '</div></div></div>'   # close foot, body, AND left (left close was missing)
        f'<div class="right" style="background-image:url(\'{INSERT_URL}\')">'
        '<span class="cut">&#9986; cut here</span></div>'
        '</div>'   # close slip
    )


CSS = """
*{margin:0;padding:0;box-sizing:border-box;}
html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
body{font-family:'DM Sans',Arial,sans-serif;color:#17130F;background:#cfcabf;}
.slip{width:297mm;height:210mm;display:flex;background:#fff;overflow:hidden;
  page-break-after:always;break-after:page;}
.left{flex:0 0 160mm;height:210mm;display:flex;flex-direction:column;overflow:hidden;}
.hdr{background:#F0571E;color:#fff;padding:9mm 9mm 8mm;display:flex;
  justify-content:space-between;align-items:center;}
.logo{display:flex;align-items:center;}
.plaque{background:#fff;border-radius:7px;padding:7px 13px;display:inline-flex;align-items:center;}
.plaque img{height:26px;width:auto;display:block;}
.doc{text-align:right;line-height:1.5;}
.doc .dt{font-size:11px;font-weight:600;letter-spacing:.5px;opacity:.95;}
.doc .code{font-family:monospace;font-size:12px;margin-top:2px;}
.body{flex:1;padding:11mm 9mm 8mm;display:flex;flex-direction:column;}
.lbl{color:#F0571E;font-weight:700;font-size:12px;letter-spacing:2px;margin-bottom:3mm;}
.from{font-size:15px;line-height:1.55;font-weight:600;}
.tobox{border:2px solid #F0571E;border-radius:7px;padding:6mm 6mm 5mm;margin:9mm 0;position:relative;}
.totab{position:absolute;top:-9px;left:16px;background:#F0571E;color:#fff;
  font-size:11px;font-weight:700;letter-spacing:1px;padding:1px 10px;border-radius:3px;}
.toname{font-weight:700;font-size:20px;margin-bottom:2mm;}
.toaddr{font-size:15px;line-height:1.6;}
.toph{font-size:15px;margin-top:2mm;font-weight:600;}
.foot{margin-top:auto;border-top:1px dashed #c7c2b8;padding-top:5mm;
  display:flex;justify-content:space-between;align-items:flex-end;}
.fc{font-size:14px;margin-bottom:2mm;}
.paid{background:#1a7c1a;color:#fff;font-size:11px;font-weight:700;
  padding:1px 7px;border-radius:3px;letter-spacing:.5px;}
.fw{font-size:13px;text-align:right;line-height:2.1;color:#555;}
.fw .ln{display:inline-block;width:36mm;border-bottom:1px solid #999;margin-left:4px;}
.right{flex:0 0 137mm;height:210mm;position:relative;border-left:2px dashed #F0571E;
  background-size:contain;background-repeat:no-repeat;background-position:center;}
.cut{position:absolute;left:-19px;top:50%;transform:translateY(-50%) rotate(-90deg);
  font-size:10px;color:#F0571E;letter-spacing:2px;white-space:nowrap;background:#fff;padding:0 4px;}
@page{size:A4 landscape;margin:0;}
@media screen{.slip{margin:14px auto;box-shadow:0 3px 14px rgba(0,0,0,.18);}}
.bar{max-width:297mm;margin:14px auto 0;font-size:13px;}
.bar button{font:600 14px 'DM Sans';padding:10px 24px;border:none;border-radius:7px;
  background:#17130F;color:#fff;cursor:pointer;}
@media print{.bar{display:none;}body{background:#fff;}}
"""


def build_courier_slips(orders: list, generated: str = "") -> str:
    """Self-contained HTML page: one branded A4-landscape courier slip per order
    (left = courier slip, right = Thank-You insert). Open -> Print -> PDF.
    """
    if not orders:
        body = ('<p style="font:15px Arial;padding:24px">'
                'No confirmed orders pending dispatch.</p>')
    else:
        bar = ('<div class="bar"><button onclick="window.print()">'
               '&#128424;  Print / Save as PDF &mdash; A4 Landscape</button> '
               f'{len(orders)} slip{"s" if len(orders) != 1 else ""}'
               + (f' &middot; {_html.escape(generated)}' if generated else '')
               + '</div>')
        body = bar + "\n".join(_slip(o) for o in orders)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<title>Printosky Courier Slips</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700'
        '&display=swap" rel="stylesheet">'
        f'<style>{CSS}</style></head><body>{body}</body></html>'
    )
