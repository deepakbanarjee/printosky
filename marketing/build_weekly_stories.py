# -*- coding: utf-8 -*-
"""Generate 7 High-Impact 9:16 (1080x1920) Instagram Stories / WhatsApp Status Cards for the Week.

Generates:
  marketing/stories/mon_lab_rush.png
  marketing/stories/tue_color_hack.png
  marketing/stories/wed_poll.png
  marketing/stories/thu_vip_account.png
  marketing/stories/fri_ktu_checklist.png
  marketing/stories/sat_weekend_print.png
  marketing/stories/sun_hostel_prep.png

Brand Rule: 100% strictly Printosky (zero mention of Oxygen).
"""

import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES_DIR = os.path.join(HERE, "stories")
LOGO_SVG_PATH = os.path.join(os.path.dirname(HERE), "brand-kit", "logo", "printosky-wordmark-reverse.svg")

with open(LOGO_SVG_PATH, "r", encoding="utf-8") as f:
    OFFICIAL_LOGO_SVG = f.read().strip()
os.makedirs(STORIES_DIR, exist_ok=True)

WIDTH, HEIGHT = 1080, 1920  # 9:16 Instagram Story & WhatsApp Status standard

STORY_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800;900&family=Syne:wght@700;800;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #090D16;
    --card-bg: #131B2E;
    --orange: #FF5A1F;
    --amber: #FFB703;
    --cyan: #00F0FF;
    --text: #F8FAFC;
    --muted: #94A3B8;
    --border: rgba(255, 255, 255, 0.1);
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {width}px;
    height: {height}px;
    background: var(--bg);
    color: var(--text);
    font-family: 'Plus Jakarta Sans', sans-serif;
    overflow: hidden;
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 90px 70px 80px;
  }}

  /* Radial Ambient Glows */
  .glow-top {{
    position: absolute;
    width: 800px;
    height: 800px;
    background: radial-gradient(circle, rgba(255, 90, 31, 0.18) 0%, rgba(0,0,0,0) 70%);
    top: -250px;
    right: -200px;
    pointer-events: none;
  }}
  .glow-bottom {{
    position: absolute;
    width: 700px;
    height: 700px;
    background: radial-gradient(circle, rgba(0, 240, 255, 0.1) 0%, rgba(0,0,0,0) 70%);
    bottom: -200px;
    left: -150px;
    pointer-events: none;
  }}

  /* Top Bar */
  .top-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: relative;
    z-index: 2;
  }}
  .logo {{
    display: flex;
    align-items: center;
  }}
  .logo svg {{
    height: 75px;
    width: auto;
    display: block;
  }}
  .day-badge {{
    background: rgba(255, 90, 31, 0.15);
    border: 2px solid var(--orange);
    color: var(--orange);
    font-size: 20px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 12px 26px;
    border-radius: 999px;
  }}

  /* Main Body */
  .center-body {{
    position: relative;
    z-index: 2;
    margin: auto 0;
  }}
  .kicker {{
    color: var(--orange);
    font-size: 24px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-bottom: 18px;
  }}
  .title {{
    font-family: 'Syne', sans-serif;
    font-size: 74px;
    font-weight: 900;
    line-height: 1.08;
    letter-spacing: -2px;
    margin-bottom: 28px;
  }}
  .title em {{
    font-style: normal;
    color: var(--orange);
    background: linear-gradient(135deg, #FF5A1F 0%, #FFB703 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .lead {{
    font-size: 30px;
    color: var(--muted);
    line-height: 1.45;
    font-weight: 600;
    margin-bottom: 40px;
  }}

  .feature-box {{
    background: var(--card-bg);
    border: 1.5px solid var(--border);
    border-radius: 32px;
    padding: 44px;
    box-shadow: 0 20px 40px rgba(0,0,0,0.3);
  }}

  /* Bottom Bar */
  .bottom-bar {{
    position: relative;
    z-index: 2;
    display: flex;
    flex-direction: column;
    gap: 20px;
    border-top: 1px solid var(--border);
    padding-top: 30px;
  }}
  .cta-pill {{
    background: linear-gradient(135deg, #FF5A1F 0%, #FF7A00 100%);
    color: #FFF;
    text-align: center;
    padding: 22px 30px;
    border-radius: 999px;
    font-size: 26px;
    font-weight: 900;
    letter-spacing: 0.5px;
    box-shadow: 0 10px 30px rgba(255, 90, 31, 0.4);
  }}
  .sub-footer {{
    display: flex;
    justify-content: space-between;
    font-size: 20px;
    color: var(--muted);
    font-weight: 600;
  }}
  .sub-footer strong {{
    color: #FFF;
  }}
</style>
</head>
<body>
  <div class="glow-top"></div>
  <div class="glow-bottom"></div>

  <div class="top-bar">
    <div class="logo">{logo_svg}</div>
    <div class="day-badge">{day_badge}</div>
  </div>

  <div class="center-body">
    {body_content}
  </div>

  <div class="bottom-bar">
    <div class="cta-pill">{cta_button}</div>
    <div class="sub-footer">
      <span>📍 Thrissur & Nattika</span>
      <span>WhatsApp: <strong>9495 706 405</strong></span>
    </div>
  </div>
</body>
</html>
"""

WEEK_STORIES = [
    {
        "filename": "mon_lab_rush",
        "day_badge": "Monday Morning Rush",
        "cta_button": "Send PDF on WhatsApp Before 8:30 AM ⚡",
        "body": """
        <div class="kicker">Skip The Xerox Queue</div>
        <h1 class="title">Have a 9:00 AM <em>Lab Submission</em> Today?</h1>
        <p class="lead">Don't stand in a 20-person queue while your teacher marks attendance.</p>
        <div class="feature-box">
          <div style="font-size:28px; font-weight:800; color:var(--amber); margin-bottom:12px;">⚡ The WhatsApp Hack:</div>
          <div style="font-size:26px; line-height:1.4; color:#FFF;">
            Send your PDF while on the college bus. When you reach the Printosky counter, your printout is ready for 30-second pickup!
          </div>
        </div>
        """,
    },
    {
        "filename": "tue_color_hack",
        "day_badge": "Tuesday Campus Hack",
        "cta_button": "Check Your Savings: wa.me/919495706405 💰",
        "body": """
        <div class="kicker">Money Saver</div>
        <h1 class="title">Stop Paying <em>₹10 For A Page</em> With 1 Blue Link!</h1>
        <p class="lead">Typical shops charge full color rates for 500 words of black text just because of one Wikipedia link.</p>
        <div class="feature-box">
          <div style="font-size:30px; font-weight:900; color:#10B981; margin-bottom:10px;">Printosky Smart Slicing:</div>
          <div style="font-size:24px; color:#FFF; line-height:1.4;">
            We automatically inspect pages and charge B&W at ₹1.50–₹2.00. You only pay color rates for genuine graphs!
          </div>
        </div>
        """,
    },
    {
        "filename": "wed_midweek_poll",
        "day_badge": "Wednesday Check-In",
        "cta_button": "Tap Sticker to Vote 👆",
        "body": """
        <div class="kicker">Final Year Projects</div>
        <h1 class="title">Did Your Guide <em>Approve</em> Chapter 2 Yet?</h1>
        <p class="lead">Project season is kicking off across GEC Thrissur, VAST, St. Thomas & KAU.</p>
        <div class="feature-box" style="text-align:center;">
          <div style="font-size:36px; margin-bottom:16px;">📚 📝 ⏰</div>
          <div style="font-size:26px; color:#FFF; font-weight:700;">
            Drop your vote on the poll sticker! We'll send our free project report template to everyone who participates.
          </div>
        </div>
        """,
    },
    {
        "filename": "thu_vip_perks",
        "day_badge": "Thursday Perks",
        "cta_button": "Text 'ACCOUNT' to 9495 706 405 🚀",
        "body": """
        <div class="kicker">Student Accounts</div>
        <h1 class="title">Double-Sided B&W At <em>75 Paise</em> Per Page.</h1>
        <p class="lead">Printosky Student Account holders get our lowest bulk rates from sheet one.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.5;">
            ✅ <strong>₹1.50 / sheet</strong> for A4 B&W (DS = 75p/page)<br>
            ✅ <strong>₹8.00 flat</strong> for A4 Colour (from page 1)<br>
            ✅ <strong>Zero wait time:</strong> Fast-track counter cubby!
          </div>
        </div>
        """,
    },
    {
        "filename": "fri_ktu_checklist",
        "day_badge": "Friday Guide",
        "cta_button": "DM 'KTU' for Free Word & LaTeX Template 📄",
        "body": """
        <div class="kicker">KTU & Calicut Standards</div>
        <h1 class="title">Don't Get Your <em>Thesis Rejected</em> On Margins!</h1>
        <p class="lead">35% of first-print reports are rejected because the left binding eats 0.5 inches of text.</p>
        <div class="feature-box">
          <div style="font-size:28px; font-weight:800; color:var(--amber); margin-bottom:10px;">The Verified Standard:</div>
          <div style="font-size:24px; color:#FFF; line-height:1.4;">
            Left Margin: <strong>1.5 inches (38mm)</strong><br>
            Right, Top, Bottom: <strong>1.0 inch (25mm)</strong><br>
            Font: Times New Roman 12 / 1.5 spacing.
          </div>
        </div>
        """,
    },
    {
        "filename": "sat_weekend_print",
        "day_badge": "Saturday Production",
        "cta_button": "Pick Up at Counter in 30 Seconds ⏱️",
        "body": """
        <div class="kicker">Heavy Duty Production</div>
        <h1 class="title">65 Pages / Min <em>Konica Power</em>.</h1>
        <p class="lead">Printing 150-page seminar reports, lab manuals, and deluxe hardbound projects all weekend.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.4;">
            Deluxe Gold & Silver Foil Embossing • Hardbound binding • Spiral & Wiro binding in minutes.
          </div>
        </div>
        """,
    },
    {
        "filename": "sun_hostel_prep",
        "day_badge": "Sunday Night Prep",
        "cta_button": "Order on WhatsApp: 9495 706 405 📱",
        "body": """
        <div class="kicker">Hostel Night Scroll</div>
        <h1 class="title">Get Your Monday <em>Notes Ready</em> Tonight.</h1>
        <p class="lead">Relax in your room. Send your PDF to our WhatsApp bot before you sleep.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.4;">
            Your files will be printed and packed waiting for you on your way to college tomorrow morning. Zero rush.
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_mon_assignment_rush",
        "day_badge": "Monday Sep 14 · Rush",
        "cta_button": "Drop PDF on WhatsApp: 9495 706 405 ⚡",
        "body": """
        <div class="kicker">Monday Morning Crunch</div>
        <h1 class="title">Assignment Due In <em>15 Minutes</em>?</h1>
        <p class="lead">Don't stress standing behind 10 people at an offline Xerox shop.</p>
        <div class="feature-box">
          <div style="font-size:28px; font-weight:800; color:var(--amber); margin-bottom:12px;">⚡ The Instant Print Pass:</div>
          <div style="font-size:26px; line-height:1.4; color:#FFF;">
            Send your file on WhatsApp from the college bus. Walk into Printosky, grab your packet from the cubby, and make it to class before attendance!
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_tue_double_sided",
        "day_badge": "Tuesday Sep 15 · Hack",
        "cta_button": "Calculate Your Bill: wa.me/919495706405 💰",
        "body": """
        <div class="kicker">Smart Printing</div>
        <h1 class="title">Double-Sided <em>Saves 50%</em> Paper & Weight.</h1>
        <p class="lead">Why carry a 200-page brick when 100 sheets look cleaner and fit in your folder?</p>
        <div class="feature-box">
          <div style="font-size:30px; font-weight:900; color:#10B981; margin-bottom:10px;">Billed Per Sheet:</div>
          <div style="font-size:24px; color:#FFF; line-height:1.4;">
            At Printosky, double-sided B&W is billed per sheet — bringing your per-page cost down to just <strong>75 paise to ₹1.00</strong>!
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_wed_binding_guide",
        "day_badge": "Wednesday Sep 16 · Guide",
        "cta_button": "Swipe to Check Today's Post 📖",
        "body": """
        <div class="kicker">Department Requirements</div>
        <h1 class="title">Spiral or Hardbound: <em>Which One</em> Is Allowed?</h1>
        <p class="lead">Different departments have strict binding rules for Phase 1 vs Final Viva.</p>
        <div class="feature-box">
          <div style="font-size:24px; color:#FFF; line-height:1.5;">
            📑 <strong>Lab Records:</strong> 360° Lay-flat Spiral (₹30–₹50)<br>
            📘 <strong>Mini-Projects:</strong> Clean Thermal Paperback (₹60)<br>
            🎓 <strong>Final Thesis:</strong> Deluxe Gold Foil Hardbound (₹220–₹250)
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_thu_cad_drawings",
        "day_badge": "Thursday Sep 17 · Arch & CAD",
        "cta_button": "Send Drawing on WhatsApp 📐",
        "body": """
        <div class="kicker">Civil & Architecture</div>
        <h1 class="title">Crisp <em>A1 & A0 CAD</em> Blueprints.</h1>
        <p class="lead">Vector precision line weights, architectural elevations, and site layouts.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.4;">
            Ultra-sharp line clarity with zero ink bleed. Folded to standard A4 title-block format or rolled in tubes for review.
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_fri_thesis_speed",
        "day_badge": "Friday Sep 18 · Submission",
        "cta_button": "Order Project on WhatsApp: 9495 706 405 🚀",
        "body": """
        <div class="kicker">Next-Day Guarantee</div>
        <h1 class="title">Submit Tonight, <em>Collect Tomorrow</em> 9 AM.</h1>
        <p class="lead">No 4-hour wait at the binder's counter the night before your final submission.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.4;">
            Send your approved PDF on WhatsApp by 8:00 PM. We inspect margins, slice pages, press the hardbound cover, and have it ready at 9:00 AM.
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_sat_foil_emboss",
        "day_badge": "Saturday Sep 19 · Production",
        "cta_button": "DM 'BIND' for College Emblems ✨",
        "body": """
        <div class="kicker">Deluxe Craftsmanship</div>
        <h1 class="title">Hot-Stamped <em>Gold Foil</em> Lettering.</h1>
        <p class="lead">Nothing looks sharper in front of external viva examiners than mirror-finish gold embossing.</p>
        <div class="feature-box">
          <div style="font-size:24px; color:#FFF; line-height:1.4;">
            Deep black & royal navy leatherette boards with official KTU, Calicut, and University emblems stamped in metallic gold or silver.
          </div>
        </div>
        """,
    },
    {
        "filename": "w2_sun_whatsapp_quote",
        "day_badge": "Sunday Sep 20 · Night Prep",
        "cta_button": "Try The Bot: 9495 706 405 ⚡",
        "body": """
        <div class="kicker">2-Second Bot</div>
        <h1 class="title">Instant PDF Bill <em>Before You Pay</em> A Rupee.</h1>
        <p class="lead">No surprise bills at the billing counter. Complete transparency.</p>
        <div class="feature-box">
          <div style="font-size:26px; color:#FFF; line-height:1.4;">
            Drop any PDF to our WhatsApp bot. Within 2 seconds, it counts every page, slices B&W from color, and shows your itemized cost breakdown!
          </div>
        </div>
        """,
    },
]


def render_stories():
    print("================================================================")
    print(" PRINTOSKY 7-DAY STORY / STATUS CARD GENERATOR (1080x1920)")
    print("================================================================")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

        for s in WEEK_STORIES:
            html = STORY_TEMPLATE.format(
                width=WIDTH,
                height=HEIGHT,
                day_badge=s["day_badge"],
                body_content=s["body"],
                cta_button=s["cta_button"],
                logo_svg=OFFICIAL_LOGO_SVG,
            )
            html_path = os.path.join(STORIES_DIR, f"{s['filename']}.html")
            png_path = os.path.join(STORIES_DIR, f"{s['filename']}.png")

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

            page.goto("file:///" + html_path.replace("\\", "/"), wait_until="networkidle")
            page.screenshot(path=png_path)
            size_kb = os.path.getsize(png_path) / 1024
            print(f"  [OK] {s['day_badge']:<24} -> {png_path} ({size_kb:.0f} KB)")

        browser.close()
    print("[SUCCESS] All 7 weekly story cards rendered cleanly!")


if __name__ == "__main__":
    render_stories()
