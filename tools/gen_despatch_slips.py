# -*- coding: utf-8 -*-
"""Generate Xtraa despatch slips (A4 landscape, one order/page).

Reproduces the despatch-slip template that was first produced in the Claude.ai
chat (Printosky_Order_Sheets_Malayalam.pdf), with the new Printosky logo
(Oxygen-O mark) swapped in for the old plain "PRINTOSKY" wordmark.

  * Left panel  : cut-out courier slip (FROM / TO / contents / courier+AWB).
  * Right panel : the "Thank You + 3-book upsell" insert, reused verbatim as a
                  single image extracted from the source PDF.

Run:  python tools/gen_despatch_slips.py
Output: Downloads/Xtraa-Despatch-Slips-2026-06-13.html  (open -> Print -> PDF)

Order rows below are a frozen snapshot of the Supabase `book_orders` query
(status = confirmed) taken on 2026-06-13. Edit ORDERS to regenerate.
"""
from __future__ import annotations

import base64
import html
import os
import re

# Rebuilt Thank-You insert (new Printosky logo). Run tools/build_insert.py to
# regenerate this PNG after any branding/price change.
INSERT_PNG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets", "xtraa_insert_v2.png")
OUT = os.path.join(os.path.expanduser("~"), "Downloads",
                   "Xtraa-Despatch-Slips-2026-06-17.html")

# Confirmed Printosky logo (capital-P + Oxygen-O bowl), inlined from the
# production SVG so the slip stays self-contained and brand-exact.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "brand-kit", "logo", "printosky-wordmark.svg"),
          encoding="utf-8") as _fh:
    LOGO_SVG = _fh.read()

# Fields: (order_code, name, address, phone10, contents, value, paid)
# Snapshot of Supabase book_orders WHERE status='confirmed', taken 2026-06-17 IST
# (15 orders still awaiting despatch; FIFO, oldest order first). The Jun-13 batch
# was marked `dispatched` via DTDC on 2026-06-17 and is therefore gone from here.
# Three rows carry manual fixes for upstream intake bugs (see inline NOTEs).
ORDERS = [
    ("XTR-20260604-8659A127", "Drishya Chinjus",
     "DRISHYA KS\nKALLINGAL (HO)\nELAD (PO)\nCHERUKARA (VIA)\nMALAPPURAM (DIS)\nPIN  679340",
     "9207549808", "Hindi x 1, English x 1", 425, False),
    ("XTR-20260604-57D1D254", "MUKUNDAN EV",
     "Mukundan. E V\nEzhuthachan Veettil(H)\nManalaya. P. O\nPerintalmanna.\nMalappuram. Dt\n679357",
     "8075286705", "Malayalam x 1, English x 1", 475, False),
    ("XTR-20260604-5D089A22", "Shimna Prajeesh",
     "Shimna KN\nKailas house\nCheekkilode post\nAtholy (via)\nCalicut\n673315",
     "9745565218", "Hindi x 1", 225, False),
    ("XTR-20260604-C91ACCCA", "Deepa P S",
     "Deepa ps\nKausthubham\nChelembrapaadam\nPo chelembra\nIdimuzhikkal\nMalappuram dt\n673634",
     "9847220820", "Malayalam x 10", 2367, True),
    ("XTR-20260605-605D7A16", "Jisna Sreejith",
     "Flat No : 905, B block, Canndale Apartment, Bellard Road, Kannur\n670001",
     "9846009923", "Hindi x 1", 225, False),
    # NOTE: intake bug dumped the whole address into the name field (address was
    # empty). Reconstructed below from that text -- VERIFY before shipping.
    ("XTR-20260606-195E1F86", "Mahija K",
     "Sathgamaya\nPallippoyil P.O\nMowenchery\nKannur\n670613",
     "9846743413", "Malayalam x 1", 275, False),
    ("XTR-20260606-48CD3BA5", "Rajeev U R",
     "Kizhakke ambali (h)\nMalayamma (p.o)\nNIT, 673601, Kozhikode",
     "9847203614", "English x 1", 275, False),
    ("XTR-20260610-B5965EAB", "Sreeya P G",
     "Sreeya P G\nBVRA 76\nK K line, Petta\nPoonithura P O\nErnakulam 682038",
     "9497800740", "Malayalam x 1, Hindi x 1, English x 1", 664, False),
    ("XTR-20260613-AE18FF95", "Faseela VP",
     "KMMAUP School\nCherucode\nChathangottupuram (Po) 679328\nWandoor\nMalappuram Dt",
     "8848965368", "Malayalam x 1, Hindi x 1, English x 1", 664, False),
    ("XTR-20260613-B5C08CE9", "THUSHAR .K",
     "Sreepadmam\nUruvachal(PO)\nBavottupara\nMattannur\nKannur\n670707-PIN",
     "8547627550", "Hindi x 1, English x 1", 425, True),
    ("XTR-20260613-77048FCB", "Shameela",
     "Karuvadan(H)\nChelacode, Urangattiri( PO ), Areekode, Malappuram,\n673639",
     "9961396093", "Malayalam x 1, Hindi x 1, English x 1", 664, True),
    ("XTR-20260613-4CC92121", "Bincy C. N",
     "MANDATHRA (house )\nTHALAM ROAD\nNEAR AKG CLUB \nP. O MATHILAKAM \n680685",
     "9072412373", "Malayalam x 1, English x 1", 475, True),
    # NOTE: only a house name on file (no PO / pincode) and DB grand_total=0 though
    # Rs.664 was paid. NOT SHIPPABLE until the full address is collected.
    ("XTR-20260613-9B83F4ED", "Aswathi Arun",
     "Tharappel House\n[ INCOMPLETE ADDRESS - confirm PO + pincode before shipping ]",
     "9446972574", "Malayalam x 1, Hindi x 1, English x 1", 664, True),
    # NOTE: intake bug stored the name as "qty_1"; real name "Thomas T P" recovered
    # from the address.
    ("XTR-20260614-1EA5910A", "Thomas T P",
     "Thomas T P\nTHEREEPARAMBIL (H)\nCHAKKALAPARAMBIL LANE\nL F C ROAD, KALOOR\nERNAKULAM\nCOCHIN - 682017",
     "9496197033", "Malayalam x 1, Hindi x 1, English x 1", 664, True),
    ("XTR-20260616-7C7D921E", "Resmi R S",
     "Chaithanya\nThannimoodu\nKaringannoor .P.O\nPin :691516\nKollam",
     "8078303837", "Malayalam x 2, Hindi x 1, English x 1", 905, True),
]


def _first_tok(s: str) -> str:
    m = re.findall(r"[A-Za-z]+", s)
    return m[0].lower() if m else ""


def _clean(name: str, addr: str):
    """Drop junk lines; promote an address-embedded name to the bold header."""
    name = "".join(c for c in name if ord(c) < 128).strip().rstrip(",").strip()
    lines = []
    for ln in (x.strip() for x in addr.split("\n")):
        if not ln or ln == ".":
            continue
        if len(re.sub(r"\D", "", ln)) >= 10 and not re.search(r"[A-Za-z]{3,}", ln):
            continue  # a bare phone line
        lines.append(ln)
    if lines and _first_tok(lines[0]) and _first_tok(lines[0]) == _first_tok(name):
        name, lines = lines[0].rstrip(","), lines[1:]
    return name, lines


def _phone(p: str) -> str:
    p = re.sub(r"\D", "", p)
    return "+91 %s %s" % (p[:5], p[5:]) if len(p) == 10 else p


def _insert_uri() -> str:
    with open(INSERT_PNG, "rb") as fh:
        return "data:image/png;base64,%s" % base64.b64encode(fh.read()).decode()


def _slip(order) -> str:
    code, rawname, rawaddr, phone, contents, value, paid = order
    name, lines = _clean(rawname, rawaddr)
    addr_html = "<br>".join(html.escape(l) for l in lines)
    paid_html = ' <span class="paid">PAID</span>' if paid else ""
    return (
        '<div class="slip"><div class="left">'
        '<div class="hdr"><div class="logo"><span class="plaque">' + LOGO_SVG + '</span></div>'
        '<div class="doc"><div class="dt">DESPATCH SLIP &mdash; Xtraa Books</div>'
        '<div class="code">' + html.escape(code) + '</div></div></div>'
        '<div class="body"><div class="lbl">FROM</div>'
        '<div class="from">Printosky<br>Thriprayar<br>Thrissur, Kerala - 680567'
        '<br>+91 94957 06405<br>printosky.com</div>'
        '<div class="tobox"><span class="totab">TO</span>'
        '<div class="toname">' + html.escape(name) + '</div>'
        '<div class="toaddr">' + addr_html + '</div>'
        '<div class="toph">Ph: ' + html.escape(_phone(phone)) + '</div></div>'
        '<div class="foot"><div class="fcol">'
        '<div class="fc">Contents: <b>' + html.escape(contents) + '</b></div>'
        '<div class="fc">Order Value: <b>&#8377;' + str(value) + '</b>' + paid_html + '</div>'
        '</div>'
        '<div class="fw"><div>Courier: <span class="ln"></span></div>'
        '<div>AWB No: <span class="ln"></span></div></div>'
        '</div>'   # close .foot
        '</div>'   # close .body
        '</div>'   # close .left
        '<div class="right"><span class="cut">&#9986; cut here</span></div>'
        '</div>'   # close .slip
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
.plaque svg{height:26px;width:auto;display:block;}
.doc{text-align:right;line-height:1.5;}
.doc .dt{font-size:11px;font-weight:600;letter-spacing:.5px;opacity:.95;}
.doc .code{font-family:monospace;font-size:12px;margin-top:2px;}
.body{flex:1;padding:11mm 9mm 8mm;display:flex;flex-direction:column;}
.lbl{color:#F0571E;font-weight:700;font-size:12px;letter-spacing:2px;margin-bottom:3mm;}
.from{font-size:15px;line-height:1.55;font-weight:600;}
.tobox{border:2px solid #F0571E;border-radius:7px;padding:6mm 6mm 5mm;
  margin:9mm 0;position:relative;}
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
  background-image:url('__INSERT__');background-size:contain;
  background-repeat:no-repeat;background-position:center;}
.cut{position:absolute;left:-19px;top:50%;transform:translateY(-50%) rotate(-90deg);
  font-size:10px;color:#F0571E;letter-spacing:2px;white-space:nowrap;background:#fff;padding:0 4px;}
@page{size:A4 landscape;margin:0;}
@media screen{.slip{margin:14px auto;box-shadow:0 3px 14px rgba(0,0,0,.18);}}
.bar{max-width:297mm;margin:14px auto 0;font-size:13px;}
.bar button{font:600 14px 'DM Sans';padding:10px 24px;border:none;border-radius:7px;
  background:#17130F;color:#fff;cursor:pointer;margin-right:10px;}
@media print{.bar{display:none;}body{background:#fff;}}
"""


def main() -> None:
    css = CSS.replace("__INSERT__", _insert_uri())
    slips = "\n".join(_slip(o) for o in ORDERS)
    doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<title>Xtraa Despatch Slips &mdash; 17 Jun 2026</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800'
        '&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">'
        "<style>" + css + "</style></head><body>"
        '<div class="bar"><button onclick="window.print()">'
        '&#128424;  Print / Save as PDF &mdash; A4 Landscape</button>'
        "15 slips &middot; remaining confirmed orders, oldest first &middot; 17 Jun 2026</div>"
        + slips + "</body></html>"
    )
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("WROTE", OUT)
    print("bytes", len(doc), "| orders", len(ORDERS))


if __name__ == "__main__":
    main()
