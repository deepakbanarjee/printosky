from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Preformatted, HRFlowable, Table, TableStyle, KeepTogether
)
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.colors import HexColor

OUTPUT = r"C:\printosky_watcher\Printosky_Architecture.pdf"

# ── Colours ──────────────────────────────────────────────────────────────────
CYAN    = HexColor("#0088AA")
DARK    = HexColor("#1A1A2E")
LGREY   = HexColor("#F4F4F4")
MGREY   = HexColor("#CCCCCC")
WHITE   = colors.white

# ── Footer canvas ─────────────────────────────────────────────────────────────
class FooterCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_footer(self, total):
        page = self._pageNumber
        self.saveState()
        self.setFont("Helvetica", 7)
        self.setFillColor(HexColor("#888888"))
        text = f"Printosky  \u00b7  Confidential  \u00b7  March 2026  \u00b7  Page {page} of {total}"
        self.drawCentredString(A4[0] / 2, 10 * mm, text)
        self.restoreState()

# ── Styles ─────────────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def S(name, parent="Normal", **kw):
    return ParagraphStyle(name, parent=base[parent], **kw)

TITLE   = S("Title2",   "Title",   fontSize=26, textColor=DARK,  leading=32, alignment=TA_CENTER)
SUBTITLE= S("Sub",      "Normal",  fontSize=11, textColor=CYAN,  leading=16, alignment=TA_CENTER)
STORE   = S("Store",    "Normal",  fontSize=9,  textColor=HexColor("#555555"), alignment=TA_CENTER)
H1      = S("H1",       "Heading1",fontSize=13, textColor=CYAN,  leading=18, spaceBefore=10, spaceAfter=4)
H2      = S("H2",       "Heading2",fontSize=10, textColor=DARK,  leading=14, spaceBefore=6,  spaceAfter=2, fontName="Helvetica-Bold")
BODY    = S("Body",     "Normal",  fontSize=8.5,leading=13, spaceAfter=4)
BULLET  = S("Bullet",   "Normal",  fontSize=8.5,leading=13, leftIndent=12, firstLineIndent=-8, spaceAfter=2)
MONO    = S("Mono",     "Code",    fontSize=6.8,leading=9,  fontName="Courier", backColor=LGREY, leftIndent=4, rightIndent=4)
PORT_H  = S("PortH",    "Normal",  fontSize=8,  textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_CENTER)
PORT_C  = S("PortC",    "Normal",  fontSize=8,  alignment=TA_CENTER)

def hr():
    return HRFlowable(width="100%", thickness=0.5, color=MGREY, spaceAfter=6, spaceBefore=6)

def h1(t):  return Paragraph(t, H1)
def h2(t):  return Paragraph(t, H2)
def p(t):   return Paragraph(t, BODY)
def b(t):   return Paragraph(f"\u2022  {t}", BULLET)
def sp(n=4):return Spacer(1, n)

# ── ASCII diagram ──────────────────────────────────────────────────────────────
DIAGRAM = r"""
+---------------------+
|   CUSTOMER          |
|   WhatsApp          |
|   (+91 XXXXXXXXXX)  |
+----------+----------+
  sends file|  \ sends text reply
            |   +------------------------------------+
            v                                        |
  +---------------------------------+               |
  |  whatsapp_capture/index.js      |<--------------+
  |  (WhatsApp Web listener)        |  text reply routed here first
  |                                 |
  |  - QR-linked to 8943232033      |
  |  - Saves file to hot folder     |
  |  - Writes .sender sidecar       |
  |  - :3001 (send msg out)         |
  |  - :3004 (send doc out)         |
  +---+----------------------------++
      |                            |
file  | saved to disk              | text msg forwarded
      |                            | POST localhost:3003/bot
      v                            v
C:\Printosky\            +------------------------+
Jobs\Incoming\  -------> |   whatsapp_bot.py      |
(hot folder)             |   Bot relay :3003       |
      |                  |                        |
      |                  |  8-step conversation:  |
      v                  |  size > colour >       |
+----------------+       |  layout > copies >     |
|  watcher.py    |<------+  finishing > delivery  |
|  (File Monitor)|  reads|                        |
|                |  jobs  |  - Repeat customer    |
| - watchdog     |        |    profile reuse       |
| - Creates      |        |  - Staff quote cmd     |
|   OSP-job-ID   |        +----+---+---------------+
| - Extracts     |             |   |
|   page count   |         calls|   calls
| - Batch timer  |             v   v
| - Boots all    |  +-----------+ +---------------------+
|   bg threads   |  | rate_card | | razorpay_           |
+--------+-------+  | .py       | | integration.py      |
         |          |           | |                     |
         |          | - Pricing | | - create_payment_   |
         |          | - Rates   | |   link()            |
         |          |   from    | | - verify_webhook()  |
         |          |   Supabase| | - LIVE keys         |
         |          +-----------+ +----------+----------+
         |                                   |
         |                      payment URL  | sent to customer
         |                      via :3001    |
         |                                   v
         |                       +-----------+----------+
         |                       |   RAZORPAY (external)|
         |                       |                      |
         |                       |  Customer pays  -->  |
         |                       |  fires webhook       |
         |                       +----------+-----------+
         |                                  |
         |                   POST to        |
         |               pay.printosky.com  |
         |               /webhook/razorpay  |
         |                                  v
         |                  +-------------------------------+
         |                  |  webhook_receiver.py  :3002   |
         |    marks Paid    |                               |
         |  <---------------+  - Verifies HMAC signature    |
         |                  |  - Updates job status         |
         |                  |  - Notifies customer via:3001 |
         |                  |  - Saves customer profile     |
         |                  +-------------------------------+
         |
         |  +----------------------------------------------+
         |  |  webhook_checker.py  (every 10 min)          |
         |  |  - Finds unpaid links >60 min old            |
         |  |  - Polls Razorpay API directly               |
         |  |  - Force-marks Paid if webhook lost          |
         |  +----------------------------------------------+
         |
         |  +----------------------------------------------+
         |  |  session_timeout.py  (every 60 sec)          |
         |  |  - Finds bot sessions idle >15 min           |
         |  |  - Sends customer follow-up                  |
         |  |  - Alerts staff to manual quote              |
         |  +----------------------------------------------+
         |
         v
+----------------------------------------------+
|             jobs.db  (SQLite)                |
|                                              |
|  jobs           job_batches   bot_sessions   |
|  customer_      staff         staff_sessions |
|  profiles       konica_jobs                  |
+------+-------+----------+--------------------+
       |       |          |
       v       v          v
+----------+ +----------+ +------------------+
| print_   | | supabase | | staff_setup.py   |
| server   | | _sync.py | | (one-time CLI)   |
| .py:3005 | |(5min sync)| |                  |
|          | |          | | - Seeds staff    |
| /print   | | Pushes:  | | - SHA256 PINs    |
| /login   | | jobs,    | +------------------+
| /logout  | | supply,  |
| /health  | | staff,   |
|          | | konica,  |
| SumatraPDF| | daily   |
+-----+----+ +-----+----+
      |             |
      v             v
+----------+  +----------------------------------+
| PRINTERS |  |  SUPABASE (Tokyo)                |
|          |  |  jobs | staff_sessions           |
| Konica   |  |  printer_supplies | konica_jobs  |
| bizhub   |  |  daily_summary                   |
| :9100    |  +------------------+---------------+
|          |                     |
| Epson    |                     | reads live data
| WF-C21000|                     v
+----------+    +---------------------------------+
                |  printosky.com  (Netlify)       |
                |                                 |
                | +----------+ +--------------+   |
                | |admin.html| |superadmin    |   |
                | |          | |.html         |   |
                | |Staff PIN | |Owner view    |   |
                | |Job queue | |Revenue,staff |   |
                | |Print btn | |Analytics     |   |
                | |POST:3005 | +--------------+   |
                | |(CF Tunnel| +----------+       |
                | |)         | |mis.html  |       |
                | +----------+ |Ink/toner |       |
                |              |% bars    |       |
                |              +----------+       |
                +---------------------------------+

 CLOUDFLARE TUNNEL (printosky-store)
  store.printosky.com  -->  localhost:3005
  pay.printosky.com    -->  localhost:3002
"""

# ── Port table ─────────────────────────────────────────────────────────────────
PORT_DATA = [
    [Paragraph("Port", PORT_H), Paragraph("Service", PORT_H),
     Paragraph("Direction", PORT_H), Paragraph("Who uses it", PORT_H)],
    [Paragraph("3001", PORT_C), Paragraph("Node: send WhatsApp message", PORT_C),
     Paragraph("Python → Node", PORT_C), Paragraph("watcher.py, whatsapp_bot.py, webhook_receiver.py", PORT_C)],
    [Paragraph("3002", PORT_C), Paragraph("Python: Razorpay webhook receiver", PORT_C),
     Paragraph("Internet → Python", PORT_C), Paragraph("webhook_receiver.py (via Cloudflare Tunnel)", PORT_C)],
    [Paragraph("3003", PORT_C), Paragraph("Python: bot relay server", PORT_C),
     Paragraph("Node → Python", PORT_C), Paragraph("index.js forwards customer replies here", PORT_C)],
    [Paragraph("3004", PORT_C), Paragraph("Node: send PDF/document", PORT_C),
     Paragraph("Python → Node", PORT_C), Paragraph("razorpay_integration.py sends invoice PDFs", PORT_C)],
    [Paragraph("3005", PORT_C), Paragraph("Python: print server + health", PORT_C),
     Paragraph("Dashboard → Python", PORT_C), Paragraph("admin.html triggers prints via store.printosky.com", PORT_C)],
]

PORT_STYLE = TableStyle([
    ("BACKGROUND",  (0,0), (-1,0), CYAN),
    ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGREY]),
    ("GRID",        (0,0), (-1,-1), 0.4, MGREY),
    ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING",  (0,0), (-1,-1), 4),
    ("BOTTOMPADDING",(0,0),(-1,-1), 4),
    ("LEFTPADDING", (0,0), (-1,-1), 5),
])

# ── Build story ────────────────────────────────────────────────────────────────
def build_story():
    story = []

    # ── Cover ──────────────────────────────────────────────────────────────────
    story += [
        Spacer(1, 40*mm),
        Paragraph("PRINTOSKY", TITLE),
        sp(6),
        Paragraph("System Architecture &amp; Component Reference", SUBTITLE),
        sp(10),
        Paragraph("Oxygen Globally · Thriprayar · Thrissur · Kerala", STORE),
        sp(4),
        Paragraph("March 2026  |  Confidential", STORE),
        Spacer(1, 20*mm),
        hr(),
        sp(10),
        p("This document describes the complete software architecture of the Printosky WhatsApp-to-print "
          "automation system. It covers every Python script, Node.js service, and HTML dashboard — "
          "their purpose, internal logic, and how they connect to each other."),
        PageBreak(),
    ]

    # ── Architecture Diagram ───────────────────────────────────────────────────
    story += [
        h1("System Architecture — Interconnection Diagram"),
        hr(),
        sp(4),
        Preformatted(DIAGRAM, MONO),
        PageBreak(),
    ]

    # ── Components ─────────────────────────────────────────────────────────────
    story += [h1("Component Reference"), hr()]

    # Node.js
    story += [
        KeepTogether([
            h2("whatsapp_capture/index.js  —  WhatsApp Listener"),
            p("The entry point for all customer interactions. Runs a headless Chromium browser via "
              "<i>whatsapp-web.js</i>, logged into the bot number (8943232033)."),
            b("When a <b>file arrives</b> (PDF, image, Word, etc.): saves it to "
              "<i>C:\\Printosky\\Jobs\\Incoming\\</i> with a timestamp filename and writes a "
              "<i>.sender</i> sidecar file containing the customer's phone number."),
            b("When a <b>text message arrives</b>: forwards it to the Python bot at "
              "<i>localhost:3003/bot</i>, receives replies, sends them back to the customer."),
            b("Listens on <b>port 3001</b> for Python to send WhatsApp messages out (POST /send)."),
            b("Listens on <b>port 3004</b> for Python to send PDF invoices out (POST /send-document)."),
        ]),
        sp(8),
    ]

    # watcher.py
    story += [
        KeepTogether([
            h2("watcher.py  —  The Brain"),
            p("Watches <i>C:\\Printosky\\Jobs\\Incoming\\</i> for new files using the <i>watchdog</i> library. "
              "This is the orchestrator — it boots all background threads at startup."),
            b("Reads the <i>.sender</i> sidecar to get the customer's phone number."),
            b("Creates a unique Job ID (e.g. OSP-20260317-0023) and extracts PDF page count."),
            b("Saves everything to SQLite <i>jobs.db</i>."),
            b("<b>Batch timer</b>: waits 30 s after last file, asks customer 'Sending more files?', "
              "waits another 30 s, then fires the bot conversation (T+60 s auto-fires if no reply)."),
            b("Boots all background threads: bot relay (:3003), session timeout checker, "
              "webhook checker, Supabase sync."),
        ]),
        sp(8),
    ]

    # whatsapp_bot.py
    story += [
        KeepTogether([
            h2("whatsapp_bot.py  —  The Conversation Engine"),
            p("Handles the multi-step WhatsApp conversation once the batch timer fires."),
            b("<b>Step 1 — Size:</b> A4, A3, or custom."),
            b("<b>Step 2 — Colour:</b> B&W, Colour, or Mixed (mixed triggers PDF colour scan)."),
            b("<b>Step 3 — Layout:</b> Single-side, Double-side, or Multi-up (2/4/6/9 per sheet)."),
            b("<b>Step 4 — Copies:</b> how many."),
            b("<b>Step 5 — Finishing:</b> staple, spiral, various binding types."),
            b("<b>Step 6 — Delivery:</b> pickup (free) or delivery (+Rs.30)."),
            b("After step 6: calls <i>rate_card.py</i> to price it, then "
              "<i>razorpay_integration.py</i> to generate a payment link, then sends link to customer."),
            b("<b>Repeat customers</b>: reuses saved profile from <i>customer_profiles</i> table."),
            b("<b>Staff override</b>: <i>quote OSP-xxx 250</i> command manually sets price."),
        ]),
        sp(8),
    ]

    # rate_card.py
    story += [
        KeepTogether([
            h2("rate_card.py  —  Pricing Engine"),
            p("Pure calculation logic. Takes (pages, size, colour, layout, copies, finishing, delivery) "
              "and returns a full cost breakdown."),
            b("Rates are loaded from Supabase at startup (falls back to hardcoded defaults)."),
            b("Handles multi-up sheet math (2-up = 2 pages per sheet = half the sheets)."),
            b("Some finishing types (spiral, wiro, binding) set <i>staff_quote_needed=True</i> — "
              "bot tells customer 'staff will confirm price'."),
        ]),
        sp(8),
    ]

    # razorpay_integration.py
    story += [
        KeepTogether([
            h2("razorpay_integration.py  —  Payment Links"),
            b("<b>create_payment_link()</b>: hits Razorpay API, creates an Rs.X link with 60-min expiry, "
              "returns the URL + link_id."),
            b("<b>verify_webhook()</b>: HMAC-SHA256 signature verification to confirm the webhook "
              "is genuinely from Razorpay."),
        ]),
        sp(8),
    ]

    # webhook_receiver.py
    story += [
        KeepTogether([
            h2("webhook_receiver.py  —  Payment Confirmation Server"),
            p("HTTP server on port 3002, exposed to the internet via Cloudflare Tunnel at "
              "<i>pay.printosky.com</i>."),
            b("Verifies Razorpay HMAC signature."),
            b("Finds the job (or batch) in SQLite, marks status to <b>Paid</b>, saves payment mode."),
            b("Sends customer a 'payment confirmed, job queued' WhatsApp via port 3001."),
            b("Alerts staff on console: <b>PAYMENT RECEIVED — PRINT NOW.</b>"),
            b("Saves customer profile for next visit."),
        ]),
        sp(8),
    ]

    # webhook_checker.py
    story += [
        KeepTogether([
            h2("webhook_checker.py  —  Payment Safety Net"),
            p("Background daemon running every 10 minutes."),
            b("Finds jobs where a payment link was sent >60 minutes ago but status is still unpaid."),
            b("Directly queries Razorpay API: 'was this paid?'"),
            b("If yes → force-marks Paid in DB and notifies customer."),
            b("If expired/abandoned → alerts staff to follow up."),
        ]),
        sp(8),
    ]

    # print_server.py
    story += [
        KeepTogether([
            h2("print_server.py  —  Print Executor"),
            p("HTTP server on port 3005, exposed at <i>store.printosky.com</i> via Cloudflare Tunnel."),
            b("<b>POST /print</b>: takes job_id + printer + settings → runs SumatraPDF silently."),
            b("<b>POST /staff-login</b>: validates 4-digit PIN (SHA256 hashed), opens a staff session."),
            b("<b>POST /staff-logout</b>: closes the session, records on-duty time."),
            b("<b>GET /active-staff</b>: returns which staff member is logged in on this PC."),
            b("<b>GET /health</b>: checks internet connectivity + printer reachability (port 9100)."),
            b("Printers: Konica bizhub (192.168.55.110) and Epson WF-C21000 (192.168.55.202)."),
        ]),
        sp(8),
    ]

    # supabase_sync.py
    story += [
        KeepTogether([
            h2("supabase_sync.py  —  Cloud Bridge"),
            p("Runs every 5 minutes in a background thread. Pushes local SQLite data to "
              "Supabase (Tokyo) so the remote dashboards can read live data."),
            b("Syncs: last 500 jobs, printer ink/toner levels, staff sessions, "
              "Konica machine job logs, daily revenue summary."),
        ]),
        sp(8),
    ]

    # session_timeout.py
    story += [
        KeepTogether([
            h2("session_timeout.py  —  Abandoned Conversation Handler"),
            b("Every 60 seconds: finds customers who started a bot conversation but haven't "
              "replied in >15 minutes."),
            b("Sends a follow-up WhatsApp to the customer."),
            b("Alerts staff with the <i>quote OSP-xxx &lt;amount&gt;</i> command option."),
        ]),
        sp(8),
    ]

    # staff_setup.py
    story += [
        KeepTogether([
            h2("staff_setup.py  —  Staff Management CLI"),
            p("One-time utility to seed and manage staff. Commands: <i>list</i>, "
              "<i>add &lt;name&gt; &lt;pin&gt;</i>, <i>reset-pin</i>, <i>deactivate</i>, <i>activate</i>."),
            b("Stores PINs as SHA256 hashes. PIN must be exactly 4 digits."),
            b("Default staff seeded with temporary PINs — reset via: python staff_setup.py reset-pin &lt;id&gt; &lt;new_pin&gt;"),
        ]),
        sp(8),
    ]

    story += [PageBreak()]

    # ── Dashboards ──────────────────────────────────────────────────────────────
    story += [h1("HTML Dashboards"), hr()]

    story += [
        KeepTogether([
            h2("admin.html  —  Staff Dashboard"),
            p("Dark theme (cyan accent). Hosted at <i>printosky.com/admin</i>. "
              "Password: Printosky@1234."),
            b("Staff enter their 4-digit PIN to log in and identify themselves."),
            b("Shows live job queue: RECEIVED → PAID → PRINTED → DONE."),
            b("Each job shows filename, customer phone, amount, payment status."),
            b("<b>Konica button</b>: calls <i>print_server.py /print</i> → sends file to Konica "
              "hot folder → button turns green (checkmark) to confirm."),
            b("Staff mark jobs Done after physically handing off to the customer."),
        ]),
        sp(8),

        KeepTogether([
            h2("mis.html  —  Supplies &amp; Metrics Dashboard"),
            p("Dark theme (amber accent). Hosted at <i>printosky.com/mis</i>. "
              "Password: Printosky@MIS2026."),
            b("Shows ink/toner % bars for Konica and Epson (data from Supabase)."),
            b("Today's job count, revenue, pending jobs."),
            b("Supply change log (when cartridges were replaced)."),
        ]),
        sp(8),

        KeepTogether([
            h2("superadmin.html  —  Owner Dashboard"),
            p("Light theme (teal accent). Hosted at <i>printosky.com/superadmin</i>. "
              "Password: Printosky@Super2026."),
            b("Multi-section: jobs history, revenue breakdown (cash/UPI/online), "
              "printer page counters, staff performance, customer analytics."),
            b("Full job table with date/status/payment filters."),
            b("Staff management: add, deactivate, PIN reset, session history."),
        ]),
        sp(8),

        PageBreak(),
    ]

    # ── Port Map ────────────────────────────────────────────────────────────────
    story += [
        h1("Port Map"),
        hr(),
        sp(4),
        Table(PORT_DATA,
              colWidths=[18*mm, 58*mm, 35*mm, 65*mm],
              style=PORT_STYLE),
        sp(14),
        h1("Data Flow Summary"),
        hr(),
        p("<b>1.</b> Customer sends PDF on WhatsApp."),
        p("<b>2.</b> <i>index.js</i> saves file to hot folder + writes <i>.sender</i> sidecar."),
        p("<b>3.</b> <i>watcher.py</i> detects file, creates OSP Job ID, starts 30 s batch timer."),
        p("<b>4.</b> Timer fires → <i>whatsapp_bot.py</i> starts 6-step conversation with customer."),
        p("<b>5.</b> Customer answers all steps → <i>rate_card.py</i> prices the job."),
        p("<b>6.</b> <i>razorpay_integration.py</i> creates a payment link → sent to customer."),
        p("<b>7.</b> Customer pays → Razorpay fires webhook to <i>pay.printosky.com</i>."),
        p("<b>8.</b> <i>webhook_receiver.py</i> verifies + marks job Paid → notifies customer &amp; staff."),
        p("<b>9.</b> Staff clicks Konica button on <i>admin.html</i> → "
          "<i>print_server.py</i> runs SumatraPDF."),
        p("<b>10.</b> <i>supabase_sync.py</i> pushes all data to cloud every 5 min → "
          "dashboards show live stats."),
    ]

    return story

# ── Run ────────────────────────────────────────────────────────────────────────
doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    leftMargin=15*mm, rightMargin=15*mm,
    topMargin=15*mm,  bottomMargin=20*mm,
    title="Printosky System Architecture",
    author="Printosky",
)
doc.build(build_story(), canvasmaker=FooterCanvas)
print(f"PDF saved: {OUTPUT}")
