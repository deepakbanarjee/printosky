# -*- coding: utf-8 -*-
"""Xtraa book campaign — branded customer status report PDF.

Data snapshot: live Supabase `book_orders` pulled 2026-06-12 08:55 IST.
Output: xtraa-book-campaign-status-2026-06-12.pdf at repo root.
"""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "xtraa-book-campaign-status-2026-06-12.pdf")
LOGO = os.path.join(ROOT, "brand-kit", "assets", "xtraa-logo.png")

# ---- brand palette (brand-kit/brand-sheet.html) ----
INK = colors.HexColor("#0D1117")
CREAM = colors.HexColor("#F5F1EB")
BLUE = colors.HexColor("#1B3F8B")
ORANGE = colors.HexColor("#E8500A")
GRAY = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F0EDE7")
GREEN = colors.HexColor("#1E8E4A")
RED = colors.HexColor("#B3362B")
RULE = colors.HexColor("#D9D4CB")
ZEBRA = colors.HexColor("#F7F5F0")

# ---- fonts: Segoe UI has the rupee glyph; Helvetica does not ----
WINFONTS = r"C:\Windows\Fonts"
try:
    pdfmetrics.registerFont(TTFont("Segoe", os.path.join(WINFONTS, "segoeui.ttf")))
    pdfmetrics.registerFont(TTFont("Segoe-Bold", os.path.join(WINFONTS, "segoeuib.ttf")))
    pdfmetrics.registerFont(TTFont("Segoe-Light", os.path.join(WINFONTS, "segoeuil.ttf")))
    F, FB, FL = "Segoe", "Segoe-Bold", "Segoe-Light"
    RS = "₹"
except Exception:
    F, FB, FL = "Helvetica", "Helvetica-Bold", "Helvetica"
    RS = "Rs."

# ---- data: (name, phone, books, total, date) ----
CONFIRMED = [
    ("Drishya Chinjus", "919207549808", "1 Vidyamrut + 1 Easy English", 425, "Jun 4"),
    ("Dr. T. K. Dhanya", "918593938918", "1 Aksharamrutham", 275, "Jun 4"),
    ("Mukundan EV", "918075286705", "1 Aksharamrutham + 1 Easy English", 475, "Jun 4"),
    ("Preethy Jinu", "919447281029", "1 Aksharamrutham", 275, "Jun 4"),
    ("Shimna Prajeesh", "919745565218", "1 Vidyamrut", 225, "Jun 4"),
    ("Dhanya P", "919061473972", "1 Aksharamrutham", 275, "Jun 4"),
    ("Jisna Sreejith", "919846009923", "1 Vidyamrut", 225, "Jun 5"),
    ("Vrinda", "919947315258", "1 Aksharamrutham", 275, "Jun 5"),
    ("Rajeev U R", "919847203614", "1 Easy English", 275, "Jun 6"),
    ("Suraja S Raj", "918281266967", "2 Aksharamrutham", 475, "Jun 6"),
    ("Sreeya P G", "919497800740", "1 each of all three titles", 664, "Jun 10"),
    ("Vijisha PP", "919747700316", "1 Aksharamrutham  (paid in full)", 275, "Jun 11"),
]
REVIEW = [
    ("Mayaja V", "919847841463", "1 each of all three titles", 624, "Jun 4"),
    ("Deepa P S", "919847220820", "10 Aksharamrutham", 2367, "Jun 5"),
]
AWAITING = [
    ("Shiji K", "919495306903", "1 Aksharamrutham", 275, "Jun 4"),
    ("Beena K Thomas", "919745772643", "1 each of all three titles", 624, "Jun 4"),
]
LEADS = [
    ("Fr. Andrews Varghese Thoma", "919447781475", "—", "Jun 4"),
    ("Shajicc", "919947234986", "—", "Jun 4"),
    ("Usha Suresh", "919656362669", "—", "Jun 4"),
    ("Sherly EK", "919048080706", "—", "Jun 5"),
    ("Mahija K  (Kannur — address captured)", "919846743413", "1 Aksharamrutham", "Jun 6"),
    ("showonetouch", "919846851118", "—", "Jun 6"),
    ("(no name)", "917034074829", "—", "Jun 6"),
    ("Jeff", "918921940844", "—", "Jun 6"),
    ("(no name)", "919497576546", "—", "Jun 6"),
    ("Emy Angel", "918281266967", "—", "Jun 6"),
    ("Logos Goongoon Lucky Omit", "919961790025", "—", "Jun 8"),
    ("Santhosh Mathew", "919072452808", "—", "Jun 8"),
    ("(no name)", "918921962354", "All three titles quoted (" + RS + "664)", "Jun 11"),
]
CANCELLED = [
    ("Deepak", "918943232033", "10 ML + 5 HI + 5 EN", "internal test"),
    ("Divya", "919526738641", "—", "coordinator herself"),
    ("Devayani Banarjee", "919947688696", "3 + 3 + 3", "test, duplicated twice"),
    ("Oxygen Students Paradise", "918089699436", "0 ML", "self-test"),
    ("Deepak Banarjee", "918943232033", "1 ML", "internal test"),
]

CONF_TOTAL = sum(r[3] for r in CONFIRMED)      # 4,139
REV_TOTAL = sum(r[3] for r in REVIEW)          # 2,991
AWAIT_TOTAL = sum(r[3] for r in AWAITING)      # 899
PIPELINE = CONF_TOTAL + REV_TOTAL + AWAIT_TOTAL


def rs(n):
    return RS + f"{n:,}"


# ---- styles ----
def ps(name, **kw):
    base = dict(fontName=F, fontSize=9.5, leading=13, textColor=INK)
    base.update(kw)
    return ParagraphStyle(name, **base)

S_CELL = ps("cell", fontSize=9, leading=12)
S_CELL_GRAY = ps("cellg", fontSize=8.5, leading=12, textColor=GRAY)
S_SECTION = ps("section", fontName=FB, fontSize=13, leading=16)
S_SECTION_SUB = ps("sectionsub", fontSize=9, leading=12, textColor=GRAY)
S_NOTE = ps("note", fontSize=9.5, leading=14)


def section_header(title, color, count_label, sub):
    head = Table(
        [["", Paragraph(f"{title}  <font color='#6B7280' size='9'>— {count_label}</font>",
                        S_SECTION)]],
        colWidths=[4*mm, None], rowHeights=[8*mm])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    out = [Spacer(1, 6*mm), head]
    if sub:
        out.append(Spacer(1, 1.5*mm))
        out.append(Paragraph(sub, S_SECTION_SUB))
    out.append(Spacer(1, 1.5*mm))
    return out


def order_table(rows, accent, money=True):
    header = ["#", "Customer", "Phone", "Books", "Total", "Date"] if money else \
             ["#", "Customer", "Phone", "Books chosen", "First contact"]
    data = [header]
    for i, r in enumerate(rows, 1):
        if money:
            name, phone, books, total, date = r
            data.append([str(i), Paragraph(name, S_CELL), Paragraph(phone, S_CELL_GRAY),
                         Paragraph(books, S_CELL), rs(total), date])
        else:
            name, phone, books, date = r
            data.append([str(i), Paragraph(name, S_CELL), Paragraph(phone, S_CELL_GRAY),
                         Paragraph(books, S_CELL), date])
    widths = [9*mm, 42*mm, 28*mm, 62*mm, 19*mm, 16*mm] if money else \
             [9*mm, 56*mm, 30*mm, 60*mm, 21*mm]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), FB),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), CREAM),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, accent),
        ("FONTNAME", (0, 1), (-1, -1), F),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
        ("TEXTCOLOR", (-1, 1), (-1, -1), GRAY),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if money:
        style += [("ALIGN", (4, 0), (4, -1), "RIGHT"),
                  ("FONTNAME", (4, 1), (4, -1), FB),
                  ("FONTSIZE", (5, 1), (5, -1), 8.5)]
    for row in range(1, len(data)):
        if row % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def stat_cards():
    def card(big, color, label, sub):
        return [Paragraph(f"<font name='{FB}' size='20' color='{color}'>{big}</font>", S_CELL),
                Paragraph(f"<font name='{FB}' size='9'>{label}</font>", S_CELL),
                Paragraph(f"<font size='8.5' color='#6B7280'>{sub}</font>", S_CELL)]
    cells = [
        card("12", "#1E8E4A", "Confirmed", "17 books · " + rs(CONF_TOTAL)),
        card("2", "#E8500A", "Payment in review", "13 books · " + rs(REV_TOTAL)),
        card("2", "#1B3F8B", "Awaiting payment", "4 books · " + rs(AWAIT_TOTAL)),
        card("13", "#6B7280", "Warm leads", "chat started, no order"),
    ]
    grid = [[c[0] for c in cells], [c[1] for c in cells], [c[2] for c in cells]]
    t = Table(grid, colWidths=[44*mm] * 4, rowHeights=[11*mm, 5.5*mm, 5.5*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
        ("LINEAFTER", (0, 0), (2, -1), 2, colors.white),
        ("LINEABOVE", (0, 0), (0, 0), 2.5, GREEN),
        ("LINEABOVE", (1, 0), (1, 0), 2.5, ORANGE),
        ("LINEABOVE", (2, 0), (2, 0), 2.5, BLUE),
        ("LINEABOVE", (3, 0), (3, 0), 2.5, GRAY),
    ]))
    return t


# ---- page furniture ----
PAGE_W, PAGE_H = A4
M = 17 * mm


def draw_header(canv, doc):
    first = (canv.getPageNumber() == 1)
    band_h = 34 * mm if first else 14 * mm
    canv.saveState()
    canv.setFillColor(INK)
    canv.rect(0, PAGE_H - band_h, PAGE_W, band_h, fill=1, stroke=0)
    canv.setFillColor(ORANGE)
    canv.rect(0, PAGE_H - band_h - 1.2 * mm, PAGE_W, 1.2 * mm, fill=1, stroke=0)
    if first:
        canv.setFillColor(ORANGE)
        canv.setFont(FB, 9)
        canv.drawString(M, PAGE_H - 11 * mm, "XTRAA  ×  OXYGEN STUDENTS PARADISE")
        canv.setFillColor(CREAM)
        canv.setFont(FB, 21)
        canv.drawString(M, PAGE_H - 19.5 * mm, "Book Campaign — Customer Status")
        canv.setFont(FL, 10)
        canv.setFillColor(colors.HexColor("#B9B2A6"))
        canv.drawString(M, PAGE_H - 26 * mm,
                        "Aksharamrutham (Malayalam)  ·  Vidyamrut (Hindi)  ·  Easy English")
        canv.setFont(F, 9)
        canv.drawRightString(PAGE_W - M, PAGE_H - 11 * mm, "Live data · 12 June 2026, 8:55 AM IST")
        if os.path.exists(LOGO):
            try:
                canv.drawImage(LOGO, PAGE_W - M - 26 * mm, PAGE_H - 30 * mm,
                               width=26 * mm, height=13 * mm,
                               preserveAspectRatio=True, mask="auto")
            except Exception:
                pass
    else:
        canv.setFillColor(CREAM)
        canv.setFont(FB, 10)
        canv.drawString(M, PAGE_H - 9 * mm, "Xtraa Book Campaign — Customer Status")
        canv.setFillColor(colors.HexColor("#B9B2A6"))
        canv.setFont(F, 8.5)
        canv.drawRightString(PAGE_W - M, PAGE_H - 9 * mm, "12 June 2026")
    # footer
    canv.setStrokeColor(RULE)
    canv.setLineWidth(0.6)
    canv.line(M, 13 * mm, PAGE_W - M, 13 * mm)
    canv.setFont(F, 8)
    canv.setFillColor(GRAY)
    canv.drawString(M, 9 * mm, "Oxygen Students Paradise · Thrissur · printosky.com · wa.me/919495706405")
    canv.drawRightString(PAGE_W - M, 9 * mm, f"Page {canv.getPageNumber()}")
    canv.restoreState()


def build():
    doc = BaseDocTemplate(OUT, pagesize=A4, title="Xtraa Book Campaign — Customer Status",
                          author="Oxygen Students Paradise")
    frame_first = Frame(M, 16 * mm, PAGE_W - 2 * M, PAGE_H - 36 * mm - 20 * mm, id="f1")
    frame_rest = Frame(M, 16 * mm, PAGE_W - 2 * M, PAGE_H - 16 * mm - 20 * mm, id="f2")
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[frame_first], onPage=draw_header),
        PageTemplate(id="rest", frames=[frame_rest], onPage=draw_header),
    ])

    story = [NextPageTemplate("rest"), Spacer(1, 2 * mm)]

    story.append(stat_cards())
    story.append(Spacer(1, 4 * mm))
    pipe = Table([[Paragraph(
        f"<font name='{FB}'>Pipeline value: {rs(PIPELINE)}</font>"
        f"<font color='#6B7280'>  across 16 orders · 34 books — every order came through "
        f"Divya teacher's Xtraa network (commission {RS}50/book applies)</font>", S_NOTE)]],
        colWidths=[PAGE_W - 2 * M])
    pipe.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CREAM),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, ORANGE),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(pipe)

    story += section_header("Confirmed orders", GREEN, "12 customers · " + rs(CONF_TOTAL),
                            "Approved and ready to fulfil. None have a dispatch recorded yet.")
    story.append(order_table(CONFIRMED, GREEN))

    story.append(KeepTogether(
        section_header("Payment screenshot in review", ORANGE, "2 customers · " + rs(REV_TOTAL),
                       "Proof submitted, waiting for verifier approval since Jun 4–5.")
        + [order_table(REVIEW, ORANGE)]))

    story.append(KeepTogether(
        section_header("Awaiting payment", BLUE, "2 customers · " + rs(AWAIT_TOTAL),
                       "Order placed, no payment yet — a WhatsApp nudge may close these.")
        + [order_table(AWAITING, BLUE)]))

    story += section_header("Warm leads — chat started, no order", GRAY, "13 contacts",
                            "Opened the WhatsApp flow but never completed an order. Worth one follow-up.")
    story.append(order_table(LEADS, GRAY, money=False))

    story += section_header("Cancelled", RED, "5 customers (6 records)",
                            "Internal tests and the coordinator's own entry — excluded from all totals.")
    canc = [["#", "Customer", "Phone", "Books", "Note"]]
    for i, (n, p, b, note) in enumerate(CANCELLED, 1):
        canc.append([str(i), Paragraph(n, S_CELL), Paragraph(p, S_CELL_GRAY),
                     Paragraph(b, S_CELL), Paragraph(note, S_CELL_GRAY)])
    tc = Table(canc, colWidths=[9*mm, 48*mm, 30*mm, 45*mm, 44*mm], repeatRows=1)
    tc.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), FB),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), CREAM),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, RED),
        ("FONTNAME", (0, 1), (-1, -1), F),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
    ]))
    story.append(tc)

    action_block = section_header("Action needed", ORANGE, "3 items", None)
    actions = [
        f"<font name='{FB}'>1.</font>  Approve or reject the two payment screenshots pending since "
        f"Jun 4–5 (Mayaja V {rs(624)}, Deepa P S {rs(2367)}). Verify Deepa P S is not a leftover "
        f"intake test before approving.",
        f"<font name='{FB}'>2.</font>  Dispatch the 12 confirmed orders — none have courier/tracking "
        f"recorded, and the oldest are 8 days old.",
        f"<font name='{FB}'>3.</font>  Nudge Shiji K and Beena K Thomas ({rs(AWAIT_TOTAL)} unpaid) and "
        f"the 13 warm leads with a single follow-up message.",
    ]
    for a in actions:
        action_block.append(Paragraph(a, ps("act" + str(len(a)), fontSize=9.5, leading=15, spaceAfter=4)))
    story.append(KeepTogether(action_block))

    doc.build(story)
    print("written:", OUT)


if __name__ == "__main__":
    build()
