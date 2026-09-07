# -*- coding: utf-8 -*-
"""Print-ready 300 DPI A4 Campus Posters for Printosky 30 km Outreach.

Generates:
1. marketing/posters/poster-free-thesis-referral.png (.html)
   - "Print your Thesis for ₹0" / 100% Store Credit Referral System
2. marketing/posters/poster-campus-delivery.png (.html)
   - "Skip the Xerox Queue" / Campus & Hostel Delivery across 30 km

Author: Antigravity / Marketing Manager
"""

import base64
import io
import os
import qrcode
from qrcode.constants import ERROR_CORRECT_H

HERE = os.path.dirname(os.path.abspath(__file__))
POSTERS_DIR = os.path.join(HERE, "posters")
os.makedirs(POSTERS_DIR, exist_ok=True)

# QR targets
QR_REFERRAL = "https://wa.me/919495706405?text=MY+CREDITS"
QR_ORDER_DELIVERY = "https://wa.me/919495706405?text=Hi+Printosky+I+need+a+campus+delivery"

# Brand Palette
INK = "#0F172A"
ORANGE = "#F0571E"
AMBER = "#FFB703"

PAGE_W, PAGE_H = 1240, 1754  # A4 @ 300 DPI rendered at 2x


def _png_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def make_qr_uri(url, color=INK):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return _png_uri(qr.make_image(fill_color=color, back_color="white").convert("RGB"))


QR_REF_URI = make_qr_uri(QR_REFERRAL, INK)
QR_DELIV_URI = make_qr_uri(QR_ORDER_DELIVERY, INK)

HTML_TEMPLATE = u"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Syne:wght@700;800;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #0F172A;
    --orange: #F0571E;
    --amber: #FFB703;
    --paper: #FFFFFF;
    --light-gray: #F1F5F9;
    --mid: #475569;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ background: #e2e8f0; font-family: 'Plus Jakarta Sans', sans-serif; }}

  .page {{
    width: 1240px;
    height: 1754px;
    position: relative;
    overflow: hidden;
    background: #FFFFFF;
    padding: 80px 70px 60px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}

  /* Top & Bottom Accent Bars */
  .top-bar {{
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 16px;
    background: linear-gradient(90deg, var(--orange) 0%, #E63946 50%, var(--amber) 100%);
  }}

  /* Header Section */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid var(--light-gray);
    padding-bottom: 28px;
  }}
  .logo-text {{
    font-family: 'Syne', sans-serif;
    font-weight: 900;
    font-size: 44px;
    color: var(--ink);
    letter-spacing: -1.5px;
  }}
  .logo-text span {{ color: var(--orange); }}
  .tagline-badge {{
    background: var(--light-gray);
    color: var(--mid);
    padding: 8px 18px;
    border-radius: 100px;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
  }}

  /* Hero Headline */
  .hero {{
    margin-top: 40px;
    text-align: center;
  }}
  .kicker {{
    display: inline-block;
    background: #FFFBEB;
    color: #B45309;
    border: 2px solid #FDE68A;
    padding: 8px 24px;
    border-radius: 100px;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 24px;
  }}
  .headline {{
    font-family: 'Syne', sans-serif;
    font-weight: 900;
    font-size: 78px;
    line-height: 1.04;
    letter-spacing: -3px;
    color: var(--ink);
  }}
  .headline em {{
    font-style: normal;
    color: var(--orange);
  }}
  .subhead {{
    margin-top: 24px;
    font-size: 26px;
    color: var(--mid);
    line-height: 1.4;
    max-width: 960px;
    margin-left: auto;
    margin-right: auto;
  }}

  /* Core Value Grid */
  .features-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin: 40px 0;
  }}
  .feature-card {{
    background: var(--light-gray);
    border: 2px solid #E2E8F0;
    border-radius: 20px;
    padding: 26px 28px;
    display: flex;
    align-items: flex-start;
    gap: 20px;
  }}
  .feature-icon {{
    font-size: 38px;
    line-height: 1;
    background: #FFFFFF;
    padding: 14px;
    border-radius: 16px;
    border: 1px solid #CBD5E1;
    flex-shrink: 0;
  }}
  .feature-content h4 {{
    font-family: 'Syne', sans-serif;
    font-size: 24px;
    font-weight: 800;
    color: var(--ink);
    margin-bottom: 6px;
  }}
  .feature-content p {{
    font-size: 18px;
    color: var(--mid);
    line-height: 1.35;
  }}

  /* QR Action Banner */
  .action-box {{
    background: #0F172A;
    border-radius: 28px;
    padding: 36px 44px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 20px 40px rgba(15, 23, 42, 0.18);
  }}
  .action-text {{
    color: #FFFFFF;
    max-width: 620px;
  }}
  .action-text .step-badge {{
    display: inline-block;
    background: var(--orange);
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 6px 16px;
    border-radius: 6px;
    margin-bottom: 14px;
  }}
  .action-text h3 {{
    font-family: 'Syne', sans-serif;
    font-size: 40px;
    font-weight: 800;
    line-height: 1.15;
    margin-bottom: 12px;
  }}
  .action-text p {{
    font-size: 20px;
    color: #94A3B8;
    line-height: 1.4;
  }}
  .action-text .wa-number {{
    display: inline-block;
    margin-top: 14px;
    font-size: 26px;
    font-weight: 800;
    color: #38BDF8;
  }}

  .qr-frame {{
    background: #FFFFFF;
    padding: 16px;
    border-radius: 22px;
    border: 4px solid var(--amber);
    box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    flex-shrink: 0;
  }}
  .qr-frame img {{
    display: block;
    width: 250px;
    height: 250px;
  }}
  .qr-caption {{
    text-align: center;
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--ink);
    margin-top: 8px;
  }}

  /* Footer */
  .footer {{
    border-top: 2px solid var(--light-gray);
    padding-top: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 19px;
    color: var(--mid);
    font-weight: 600;
  }}
  .footer-left strong {{
    color: var(--ink);
  }}
  .footer-right {{
    color: var(--orange);
    font-weight: 800;
    font-size: 22px;
  }}
</style>
</head>
<body>

<div class="page">
  <div class="top-bar"></div>

  <!-- Header -->
  <div class="header">
    <div class="logo-text">Print<span>osky</span></div>
    <div class="tagline-badge">30 km Campus Network &bull; Kerala</div>
  </div>

  <!-- Hero -->
  <div class="hero">
    <div class="kicker">{kicker}</div>
    <h1 class="headline">{headline}</h1>
    <p class="subhead">{subhead}</p>
  </div>

  <!-- Feature Cards -->
  <div class="features-grid">
    {features_html}
  </div>

  <!-- QR Action Box -->
  <div class="action-box">
    <div class="action-text">
      <div class="step-badge">{action_badge}</div>
      <h3>{action_title}</h3>
      <p>{action_desc}</p>
      <div class="wa-number">&bull; WhatsApp: +91 94957 06405</div>
    </div>
    <div class="qr-frame">
      <img src="{qr_uri}" alt="Scan QR Code">
      <div class="qr-caption">Scan to WhatsApp</div>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div class="footer-left">
      <strong>Hubs:</strong> Thriprayar (Oxygen Students Paradise) &bull; Nattika (Opp. SN College)
    </div>
    <div class="footer-right">
      printosky.com
    </div>
  </div>
</div>

</body>
</html>
"""

REFERRAL_FEATURES = """
    <div class="feature-card">
      <div class="feature-icon">💰</div>
      <div class="feature-content">
        <h4>₹20 Per Classmate</h4>
        <p>Earn ₹20 store credit on every friend who places an order using your link.</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🎓</div>
      <div class="feature-content">
        <h4>100% Free Project Printing</h4>
        <p>Refer 15-20 classmates and cover your entire semester notes and final thesis for ₹0!</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🎨</div>
      <div class="feature-content">
        <h4>Smart Color Slicing</h4>
        <p>Pay color rates only on color pages. Never pay full color for mixed documents.</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🛵</div>
      <div class="feature-content">
        <h4>Direct Campus Delivery</h4>
        <p>Daily batch delivery to college gates & hostels across a 30 km radius.</p>
      </div>
    </div>
"""

DELIVERY_FEATURES = """
    <div class="feature-card">
      <div class="feature-icon">⚡</div>
      <div class="feature-content">
        <h4>Skip the Xerox Line</h4>
        <p>Upload PDF from your hostel bed or classroom. No standing in crowded lines.</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">📖</div>
      <div class="feature-content">
        <h4>Certified Hard Binding</h4>
        <p>KTU & Calicut Univ spec golden foil embossing, soft binding, and spiral packs.</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🛵</div>
      <div class="feature-content">
        <h4>Campus & Hostel Drops</h4>
        <p>Orders pooled to ₹150 get FREE scheduled delivery right to your college/hostel.</p>
      </div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🎁</div>
      <div class="feature-content">
        <h4>₹20 Credit on Every Invite</h4>
        <p>Share with classmates and get your own prints covered completely free.</p>
      </div>
    </div>
"""

POSTERS_CONFIG = [
    {
        "stem": "poster-free-thesis-referral",
        "title": "Printosky — Free Thesis Referral Poster",
        "kicker": "100% Store Credit Rewards",
        "headline": "Print your Thesis for <em>₹0</em>.",
        "subhead": "Earn ₹20 store credit every time a classmate prints with Printosky. Share your link & unlock 100% free semester printing!",
        "features_html": REFERRAL_FEATURES,
        "action_badge": "Instant Activation",
        "action_title": "Get Your Personal Share Link",
        "action_desc": "Scan the code and text 'MY CREDITS' to our WhatsApp bot. Share with classmates in 10 seconds.",
        "qr_uri": QR_REF_URI,
    },
    {
        "stem": "poster-campus-delivery",
        "title": "Printosky — Campus Delivery Poster",
        "kicker": "30 KM Radius Campus Service",
        "headline": "Skip the Xerox <em>Shop Queue</em>.",
        "subhead": "Send your files on WhatsApp or Web. We print, bind and deliver to your college or hostel across Thrissur.",
        "features_html": DELIVERY_FEATURES,
        "action_badge": "Instant Quote & Print",
        "action_title": "Send Your File on WhatsApp",
        "action_desc": "Send your PDF & requirements to get an automated instant quote and same-day campus drop.",
        "qr_uri": QR_DELIV_URI,
    },
]


def render_png(html_path, png_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": PAGE_W, "height": PAGE_H}, device_scale_factor=2
        )
        page.goto("file:///" + html_path.replace("\\", "/"), wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.query_selector(".page").screenshot(path=png_path)
        browser.close()


def main():
    print("Generating Print-Ready Campus Posters...")
    for cfg in POSTERS_CONFIG:
        html_content = HTML_TEMPLATE.format(
            title=cfg["title"],
            kicker=cfg["kicker"],
            headline=cfg["headline"],
            subhead=cfg["subhead"],
            features_html=cfg["features_html"],
            action_badge=cfg["action_badge"],
            action_title=cfg["action_title"],
            action_desc=cfg["action_desc"],
            qr_uri=cfg["qr_uri"],
        )
        out_html = os.path.join(POSTERS_DIR, cfg["stem"] + ".html")
        out_png = os.path.join(POSTERS_DIR, cfg["stem"] + ".png")

        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"Rendering {cfg['stem']}.png at 300 DPI (2480x3508 px)...")
        render_png(out_html, out_png)
        print(f"[OK] Saved: {out_png} ({os.path.getsize(out_png) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
