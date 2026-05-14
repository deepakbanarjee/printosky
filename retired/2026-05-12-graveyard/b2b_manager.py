"""
PRINTOSKY B2B CLIENT MANAGER
==============================
Handles B2B client registration, per-client pricing,
job tracking, and monthly invoice generation (PDF via WhatsApp).

B2B clients bypass the retail bot flow entirely.
Staff registers clients once; system handles everything after.

Staff commands:
  b2b add <phone> "<company>" "<contact>" <discount%>
  b2b list
  b2b jobs <phone>
  b2b credit <phone> <amount>
  b2b paid <phone> <amount> <NEFT|IMPS|CHEQUE|CASH>
  invoice <phone>               → generate + send PDF invoice via WhatsApp
  invoice <phone> preview       → generate PDF only, don't send
"""

import os
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger("b2b_manager")

DB_PATH = os.environ.get("PRINTOSKY_DB", r"C:\Printosky\Data\jobs.db")

# ── DB setup ──────────────────────────────────────────────────────────────────
def setup_b2b_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS b2b_clients (
            phone           TEXT PRIMARY KEY,
            company_name    TEXT NOT NULL,
            contact_name    TEXT,
            email           TEXT,
            discount_pct    REAL DEFAULT 0,
            credit_limit    REAL DEFAULT 0,
            balance_due     REAL DEFAULT 0,
            payment_mode    TEXT DEFAULT 'NEFT',
            gst_number      TEXT,
            address         TEXT,
            notes           TEXT,
            registered_at   TEXT,
            active          INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS b2b_payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            phone           TEXT,
            amount          REAL,
            mode            TEXT,
            reference       TEXT,
            paid_at         TEXT,
            notes           TEXT
        );
    """)
    conn.commit()
    conn.close()

# ── Client lookup ─────────────────────────────────────────────────────────────
def get_b2b_client(db_path: str, phone: str) -> dict:
    """Return B2B client dict if registered and active, else None."""
    clean = phone.replace("@c.us", "").replace("+", "").replace(" ", "")
    # Try with and without country code
    variants = [clean, clean[2:] if clean.startswith("91") else "91" + clean]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for v in variants:
        row = conn.execute(
            "SELECT * FROM b2b_clients WHERE phone=? AND active=1", (v,)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)
    conn.close()
    return None

def is_b2b(db_path: str, phone: str) -> bool:
    return get_b2b_client(db_path, phone) is not None

# ── Register / update client ──────────────────────────────────────────────────
def register_b2b_client(db_path: str, phone: str, company: str,
                         contact: str = "", discount_pct: float = 0.0) -> str:
    clean = phone.replace("+", "").replace(" ", "")
    conn = sqlite3.connect(db_path)
    existing = conn.execute(
        "SELECT company_name FROM b2b_clients WHERE phone=?", (clean,)
    ).fetchone()
    conn.execute("""
        INSERT INTO b2b_clients (phone, company_name, contact_name, discount_pct, registered_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(phone) DO UPDATE SET
        company_name=excluded.company_name,
        contact_name=excluded.contact_name,
        discount_pct=excluded.discount_pct,
        active=1
    """, (clean, company, contact, discount_pct))
    conn.commit()
    conn.close()
    action = "updated" if existing else "registered"
    logger.info(f"B2B client {action}: {clean} — {company} ({discount_pct}% discount)")
    return f"✅ B2B client {action}: *{company}* ({contact})\nPhone: {clean}\nDiscount: {discount_pct}%"

def set_credit_limit(db_path: str, phone: str, limit: float) -> str:
    clean = phone.replace("+", "").replace(" ", "")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE b2b_clients SET credit_limit=? WHERE phone=?", (limit, clean))
    conn.commit()
    conn.close()
    return f"✅ Credit limit set to ₹{limit:.2f} for {clean}"

def record_payment(db_path: str, phone: str, amount: float, mode: str, reference: str = "") -> str:
    clean = phone.replace("+", "").replace(" ", "")
    conn = sqlite3.connect(db_path)
    client = conn.execute(
        "SELECT company_name, balance_due FROM b2b_clients WHERE phone=?", (clean,)
    ).fetchone()
    if not client:
        conn.close()
        return f"❌ No B2B client found for {clean}"
    new_balance = max(0, (client[1] or 0) - amount)
    conn.execute(
        "UPDATE b2b_clients SET balance_due=? WHERE phone=?", (new_balance, clean)
    )
    conn.execute("""
        INSERT INTO b2b_payments (phone, amount, mode, reference, paid_at)
        VALUES (?, ?, ?, ?, datetime('now'))
    """, (clean, amount, mode.upper(), reference))
    conn.commit()
    conn.close()
    logger.info(f"B2B payment recorded: {clean} ₹{amount} {mode}")
    return (
        f"✅ Payment recorded\n"
        f"Company: *{client[0]}*\n"
        f"Amount: ₹{amount:.2f} via {mode.upper()}\n"
        f"Remaining balance: ₹{new_balance:.2f}"
    )

# ── List clients ──────────────────────────────────────────────────────────────
def list_b2b_clients(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT phone, company_name, contact_name, discount_pct, balance_due, credit_limit
        FROM b2b_clients WHERE active=1
        ORDER BY company_name
    """).fetchall()
    conn.close()
    if not rows:
        return "No B2B clients registered yet.\nAdd one: b2b add <phone> \"Company\" \"Contact\" <discount%>"
    lines = [f"📋 *B2B Clients ({len(rows)})*\n"]
    for r in rows:
        phone, company, contact, disc, bal, limit = r
        status = f"₹{bal:.0f} due" if bal and bal > 0 else "clear"
        lines.append(f"• *{company}* ({contact})\n  📞 {phone} | {disc}% off | {status}")
    return "\n".join(lines)

# ── Jobs for a client ─────────────────────────────────────────────────────────
def get_b2b_jobs(db_path: str, phone: str, unpaid_only: bool = False) -> list:
    clean = phone.replace("+", "").replace(" ", "")
    variants = [clean, clean[2:] if clean.startswith("91") else "91" + clean]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for v in variants:
        q = "SELECT * FROM jobs WHERE sender=?"
        args = [v]
        if unpaid_only:
            q += " AND (invoiced IS NULL OR invoiced=0)"
        q += " ORDER BY received_at"
        rows = conn.execute(q, args).fetchall()
        if rows:
            conn.close()
            return [dict(r) for r in rows]
    conn.close()
    return []

def print_b2b_jobs(db_path: str, phone: str) -> str:
    jobs = get_b2b_jobs(db_path, phone)
    client = get_b2b_client(db_path, phone)
    if not jobs:
        return f"No jobs found for {phone}"
    company = client["company_name"] if client else phone
    lines = [f"📋 *Jobs for {company}* ({len(jobs)} total)\n"]
    total = 0
    for j in jobs:
        amt = j.get("amount_collected") or 0
        total += amt
        inv = "✅" if j.get("invoiced") else "⏳"
        lines.append(
            f"{inv} {j['job_id']} | {j['filename'][:25]} | "
            f"₹{amt:.0f} | {j['status']} | {j['received_at'][:10]}"
        )
    lines.append(f"\n*Total: ₹{total:.2f}*")
    return "\n".join(lines)

# ── Invoice PDF generator ─────────────────────────────────────────────────────
def generate_invoice_pdf(db_path: str, phone: str, output_path: str = None) -> str:
    """
    Generate a professional invoice PDF for all uninvoiced jobs for this client.
    Returns path to generated PDF, or raises on error.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

    client = get_b2b_client(db_path, phone)
    if not client:
        raise ValueError(f"No B2B client found for {phone}")

    jobs = get_b2b_jobs(db_path, phone, unpaid_only=True)
    if not jobs:
        raise ValueError(f"No uninvoiced jobs found for {client['company_name']}")

    # ── Invoice number ──────────────────────────────────────────────────────
    now        = datetime.now()
    inv_number = f"INV-{now.strftime('%Y%m')}-{phone[-4:]}"

    # ── Output path ─────────────────────────────────────────────────────────
    if not output_path:
        safe = client["company_name"].replace(" ", "_")[:20]
        output_path = os.path.join(
            os.path.dirname(db_path),
            f"Invoice_{safe}_{now.strftime('%Y%m%d')}.pdf"
        )

    # ── Colours ─────────────────────────────────────────────────────────────
    TEAL  = colors.HexColor("#1A6B8A")
    DARK  = colors.HexColor("#1C2833")
    GREY  = colors.HexColor("#7F8C8D")
    LIGHT = colors.HexColor("#EBF5FB")
    WHITE = colors.white

    # ── Styles ───────────────────────────────────────────────────────────────
    def style(name, **kw):
        base = dict(fontName="Helvetica", fontSize=10, textColor=DARK, leading=14)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S = {
        "brand":    style("brand",   fontName="Helvetica-Bold", fontSize=22, textColor=TEAL),
        "h1":       style("h1",      fontName="Helvetica-Bold", fontSize=14, textColor=DARK),
        "label":    style("label",   fontName="Helvetica-Bold", fontSize=9,  textColor=GREY),
        "value":    style("value",   fontSize=10, textColor=DARK),
        "small":    style("small",   fontSize=8,  textColor=GREY),
        "right":    style("right",   alignment=TA_RIGHT),
        "center":   style("center",  alignment=TA_CENTER),
        "footer":   style("footer",  fontSize=8, textColor=GREY, alignment=TA_CENTER),
        "total_lbl":style("tlbl",    fontName="Helvetica-Bold", fontSize=11, textColor=WHITE),
        "total_val":style("tval",    fontName="Helvetica-Bold", fontSize=13, textColor=WHITE, alignment=TA_RIGHT),
    }

    # ── Document ──────────────────────────────────────────────────────────────
    doc   = SimpleDocTemplate(output_path, pagesize=A4,
                               leftMargin=15*mm, rightMargin=15*mm,
                               topMargin=12*mm, bottomMargin=12*mm)
    story = []
    W     = A4[0] - 30*mm   # usable width

    # Header row: brand left, invoice details right
    disc      = client.get("discount_pct", 0)
    header_data = [[
        Paragraph("🖨️ PRINTOSKY", S["brand"]),
        Table([
            [Paragraph("INVOICE", style("inv", fontName="Helvetica-Bold", fontSize=18, textColor=TEAL, alignment=TA_RIGHT))],
            [Paragraph(f"<b>{inv_number}</b>", style("inv2", alignment=TA_RIGHT, textColor=DARK))],
            [Paragraph(f"Date: {now.strftime('%d %B %Y')}", style("inv3", alignment=TA_RIGHT, fontSize=9, textColor=GREY))],
        ], colWidths=[W * 0.5], style=TableStyle([("ALIGN", (0,0), (-1,-1), "RIGHT")])),
    ]]
    story.append(Table(header_data, colWidths=[W*0.5, W*0.5],
        style=TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0)])))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=2, color=TEAL))
    story.append(Spacer(1, 4*mm))

    # From / To
    addr_data = [[
        Table([
            [Paragraph("FROM", S["label"])],
            [Paragraph("<b>Oxygen Globally</b>", style("from1", fontName="Helvetica-Bold"))],
            [Paragraph("Thriprayar, Thrissur, Kerala", S["small"])],
            [Paragraph("printosky.com | hello@printosky.com", S["small"])],
            [Paragraph("+91 94957 06405", S["small"])],
        ], colWidths=[W*0.45],
           style=TableStyle([("LEFTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),1)])),

        Table([
            [Paragraph("BILL TO", S["label"])],
            [Paragraph(f"<b>{client['company_name']}</b>", style("to1", fontName="Helvetica-Bold"))],
            [Paragraph(client.get("contact_name") or "", S["small"])],
            [Paragraph(client.get("address") or "", S["small"])],
            [Paragraph(f"GST: {client.get('gst_number') or 'N/A'}", S["small"])],
        ], colWidths=[W*0.45],
           style=TableStyle([("LEFTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),1)])),
    ]]
    story.append(Table(addr_data, colWidths=[W*0.5, W*0.5],
        style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)])))
    story.append(Spacer(1, 5*mm))

    # Jobs table
    col_w = [W*0.14, W*0.34, W*0.10, W*0.10, W*0.10, W*0.12, W*0.10]
    t_headers = [
        Paragraph("<b>Job ID</b>",    style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9)),
        Paragraph("<b>Description</b>",style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9)),
        Paragraph("<b>Date</b>",      style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9, alignment=TA_CENTER)),
        Paragraph("<b>Pages</b>",     style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9, alignment=TA_CENTER)),
        Paragraph("<b>Copies</b>",    style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9, alignment=TA_CENTER)),
        Paragraph("<b>Rate</b>",      style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT)),
        Paragraph("<b>Amount</b>",    style("th", textColor=WHITE, fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT)),
    ]
    t_rows  = [t_headers]
    subtotal = 0

    for i, j in enumerate(jobs):
        amt      = j.get("amount_collected") or 0
        pages    = j.get("page_count") or "—"
        copies   = j.get("copies") or 1
        filename = (j.get("filename") or "")[:35]
        date_str = (j.get("received_at") or "")[:10]
        rate_str = f"₹{amt/max(pages if isinstance(pages,int) else 1, 1):.1f}" if isinstance(pages, int) and pages else "—"
        subtotal += amt
        bg = colors.HexColor("#F2F9FC") if i % 2 == 0 else WHITE
        t_rows.append([
            Paragraph(j["job_id"],  style(f"r{i}a", fontSize=8, textColor=TEAL)),
            Paragraph(filename,     style(f"r{i}b", fontSize=8)),
            Paragraph(date_str,     style(f"r{i}c", fontSize=8, alignment=TA_CENTER)),
            Paragraph(str(pages),   style(f"r{i}d", fontSize=8, alignment=TA_CENTER)),
            Paragraph(str(copies),  style(f"r{i}e", fontSize=8, alignment=TA_CENTER)),
            Paragraph(rate_str,     style(f"r{i}f", fontSize=8, alignment=TA_RIGHT)),
            Paragraph(f"₹{amt:.2f}",style(f"r{i}g", fontSize=8, alignment=TA_RIGHT)),
        ])

    jobs_table = Table(t_rows, colWidths=col_w, repeatRows=1)
    jobs_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  TEAL),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#F2F9FC"), WHITE]),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#BDC3C7")),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(jobs_table)
    story.append(Spacer(1, 4*mm))

    # Totals block
    discount_amt = round(subtotal * disc / 100, 2) if disc else 0
    prev_balance = client.get("balance_due") or 0
    grand_total  = subtotal - discount_amt + prev_balance

    totals_data = []
    totals_data.append(["Subtotal", f"₹{subtotal:.2f}"])
    if discount_amt:
        totals_data.append([f"Discount ({disc}%)", f"-₹{discount_amt:.2f}"])
    if prev_balance > 0:
        totals_data.append(["Previous balance", f"₹{prev_balance:.2f}"])
    totals_data.append(["TOTAL DUE", f"₹{grand_total:.2f}"])

    def totals_row(label, value, is_total=False):
        fs   = 11 if is_total else 9
        bold = "Helvetica-Bold" if is_total else "Helvetica"
        col  = TEAL if is_total else DARK
        return [
            Paragraph(label, style(f"tl{label}", fontName=bold, fontSize=fs, textColor=col, alignment=TA_RIGHT)),
            Paragraph(value, style(f"tv{label}", fontName=bold, fontSize=fs, textColor=col, alignment=TA_RIGHT)),
        ]

    t_rows2 = [totals_row(r[0], r[1], r[0]=="TOTAL DUE") for r in totals_data]
    totals_table = Table(t_rows2, colWidths=[W*0.75, W*0.25])
    totals_table.setStyle(TableStyle([
        ("LINEABOVE",   (0,-1), (-1,-1), 1.5, TEAL),
        ("TOPPADDING",  (0,0),  (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("RIGHTPADDING",(1,0),  (1,-1),  0),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 6*mm))

    # Payment instructions
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#BDC3C7")))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("<b>Payment Instructions</b>", style("pi_h", fontName="Helvetica-Bold", fontSize=9, textColor=TEAL)))
    story.append(Paragraph(
        "Please transfer to Oxygen Globally via NEFT/IMPS. "
        "Share transaction reference to +91 9446903907 after payment.",
        style("pi_b", fontSize=8, textColor=GREY)
    ))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(
        "Thank you for your business! 🙏  —  Printosky / Oxygen Globally, Thriprayar, Thrissur",
        S["footer"]
    ))

    doc.build(story)
    logger.info(f"Invoice generated: {output_path} — {len(jobs)} jobs, ₹{grand_total:.2f}")
    return output_path, grand_total, len(jobs), inv_number


# ── Mark jobs as invoiced ─────────────────────────────────────────────────────
def mark_jobs_invoiced(db_path: str, phone: str, inv_number: str):
    clean = phone.replace("+", "").replace(" ", "")
    variants = [clean, clean[2:] if clean.startswith("91") else "91" + clean]
    conn = sqlite3.connect(db_path)
    for v in variants:
        conn.execute("""
            UPDATE jobs SET invoiced=1, invoice_number=?
            WHERE sender=? AND (invoiced IS NULL OR invoiced=0)
        """, (inv_number, v))
    # Update balance_due on client
    conn.execute("""
        UPDATE b2b_clients SET balance_due = (
            SELECT COALESCE(SUM(amount_collected), 0)
            FROM jobs WHERE sender IN (?, ?) AND (invoiced IS NULL OR invoiced=0)
        ) WHERE phone IN (?, ?)
    """, variants + variants)
    conn.commit()
    conn.close()
