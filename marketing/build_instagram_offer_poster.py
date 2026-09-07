# -*- coding: utf-8 -*-
"""Print-ready 300 DPI A4 In-Store Offer Poster for Printosky.

"Follow & Like our post to get ₹20 OFF"
Generates:
- marketing/posters/poster-instagram-20off.html
- marketing/posters/poster-instagram-20off.png (2480x3508 px @ 300 DPI)

Brand Rule: 100% strictly Printosky (zero mention of Oxygen).
"""

import base64
import io
import os
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
POSTERS_DIR = os.path.join(HERE, "posters")
os.makedirs(POSTERS_DIR, exist_ok=True)

LOGO_SVG_PATH = os.path.join(os.path.dirname(HERE), "brand-kit", "logo", "printosky-wordmark.svg")
with open(LOGO_SVG_PATH, "r", encoding="utf-8") as f:
    OFFICIAL_LOGO_SVG = f.read().strip()

TARGET_POST_URL = "https://www.instagram.com/p/Dc24ngGjDYa/"

PAGE_W, PAGE_H = 1240, 1754  # A4 ratio


def make_qr_uri(url, color="#0F172A"):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=color, back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


QR_DATA_URI = make_qr_uri(TARGET_POST_URL, "#0F172A")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Printosky — Follow & Like ₹20 OFF Poster</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&family=Syne:wght@700;800;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #0F172A;
    --orange: #FF5A1F;
    --amber: #FFB703;
    --paper: #FFFFFF;
    --light-gray: #F8FAFC;
    --border: #E2E8F0;
    --mid: #475569;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    background: #E2E8F0;
    font-family: 'Plus Jakarta Sans', sans-serif;
    color: var(--ink);
    display: flex;
    justify-content: center;
    align-items: center;
  }}

  .page {{
    width: {page_w}px;
    height: {page_h}px;
    background: var(--paper);
    padding: 70px 80px 60px;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}

  /* Background Accent Gradient */
  .glow-circle {{
    position: absolute;
    width: 800px;
    height: 800px;
    background: radial-gradient(circle, rgba(255, 90, 31, 0.08) 0%, rgba(255,255,255,0) 70%);
    top: -200px;
    right: -200px;
    pointer-events: none;
  }}

  /* Header */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid var(--border);
    padding-bottom: 24px;
    position: relative;
    z-index: 2;
  }}
  .logo {{
    display: flex;
    align-items: center;
  }}
  .logo svg {{
    height: 90px;
    width: auto;
    display: block;
  }}
  .badge {{
    background: rgba(255, 90, 31, 0.12);
    border: 2.5px solid var(--orange);
    color: var(--orange);
    font-size: 20px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    padding: 14px 28px;
    border-radius: 999px;
  }}

  /* Hero Section */
  .hero {{
    text-align: center;
    margin: 30px 0 20px;
    position: relative;
    z-index: 2;
  }}
  .kicker {{
    font-size: 22px;
    font-weight: 800;
    color: var(--orange);
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-bottom: 12px;
  }}
  .headline {{
    font-family: 'Syne', sans-serif;
    font-size: 78px;
    font-weight: 900;
    line-height: 1.05;
    letter-spacing: -2px;
    color: var(--ink);
    margin-bottom: 18px;
  }}
  .headline span.off {{
    color: var(--orange);
    background: linear-gradient(135deg, #FF5A1F 0%, #FFB703 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .subhead {{
    font-size: 26px;
    color: var(--mid);
    max-width: 900px;
    margin: 0 auto;
    line-height: 1.4;
    font-weight: 600;
  }}

  /* QR Box & Steps Grid */
  .offer-container {{
    display: flex;
    gap: 40px;
    background: var(--light-gray);
    border: 3px solid var(--ink);
    border-radius: 36px;
    padding: 50px 60px;
    align-items: center;
    box-shadow: 0 20px 40px rgba(15, 23, 42, 0.06);
    position: relative;
    z-index: 2;
  }}

  .qr-side {{
    flex-shrink: 0;
    text-align: center;
    background: #FFF;
    padding: 24px;
    border-radius: 28px;
    border: 2px solid var(--border);
    box-shadow: 0 10px 25px rgba(0,0,0,0.05);
  }}
  .qr-image {{
    width: 320px;
    height: 320px;
    display: block;
    border-radius: 16px;
  }}
  .qr-caption {{
    margin-top: 14px;
    font-size: 16px;
    font-weight: 800;
    color: var(--ink);
    text-transform: uppercase;
    letter-spacing: 1px;
  }}

  /* Steps */
  .steps-side {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 22px;
  }}
  .step-item {{
    display: flex;
    align-items: flex-start;
    gap: 20px;
  }}
  .step-num {{
    width: 52px;
    height: 52px;
    border-radius: 16px;
    background: var(--ink);
    color: #FFF;
    font-size: 24px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }}
  .step-item:first-child .step-num {{
    background: var(--orange);
  }}
  .step-text {{
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .step-title {{
    font-size: 25px;
    font-weight: 800;
    color: var(--ink);
    margin-bottom: 4px;
  }}
  .step-desc {{
    font-size: 19px;
    color: var(--mid);
    line-height: 1.35;
    font-weight: 500;
  }}

  /* Callout Banner */
  .callout-banner {{
    background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
    color: #FFF;
    border-radius: 24px;
    padding: 24px 36px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: relative;
    z-index: 2;
  }}
  .callout-title {{
    font-size: 24px;
    font-weight: 800;
  }}
  .callout-title span {{
    color: var(--amber);
  }}
  .callout-tag {{
    font-size: 18px;
    color: #94A3B8;
  }}

  /* Footer */
  .footer {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 2px solid var(--border);
    padding-top: 24px;
    position: relative;
    z-index: 2;
  }}
  .footer-store {{
    font-size: 20px;
    font-weight: 800;
    color: var(--ink);
  }}
  .footer-store span {{
    color: var(--mid);
    font-weight: 500;
  }}
  .footer-handle {{
    font-size: 22px;
    font-weight: 900;
    color: var(--orange);
  }}
</style>
</head>
<body>
  <div class="page">
    <div class="glow-circle"></div>

    <div class="header">
      <div class="logo">{logo_svg}</div>
      <div class="badge">⚡ Instant Counter Offer</div>
    </div>

    <div class="hero">
      <div class="kicker">Student Special</div>
      <h1 class="headline">Follow & Like<br>To Get <span class="off">₹20 OFF</span></h1>
      <p class="subhead">Take out your phone, scan below, and get ₹20 deducted from your printout bill right now!</p>
    </div>

    <div class="offer-container">
      <div class="qr-side">
        <img class="qr-image" src="{qr_data_uri}" alt="Scan to Instagram">
        <div class="qr-caption">📷 Scan Camera or Lens</div>
      </div>

      <div class="steps-side">
        <div class="step-item">
          <div class="step-num">1</div>
          <div class="step-text">
            <div class="step-title">Scan the QR Code</div>
            <div class="step-desc">Opens our latest post on Instagram directly in your app.</div>
          </div>
        </div>

        <div class="step-item">
          <div class="step-num">2</div>
          <div class="step-text">
            <div class="step-title">Hit "Follow" & Like Post</div>
            <div class="step-desc">Follow <strong>@printosky_official</strong> and tap ❤️ on the post.</div>
          </div>
        </div>

        <div class="step-item">
          <div class="step-num">3</div>
          <div class="step-text">
            <div class="step-title">Show Counter Staff</div>
            <div class="step-desc">Show your screen to staff and get <strong>₹20 OFF</strong> today's bill!</div>
          </div>
        </div>
      </div>
    </div>

    <div class="callout-banner">
      <div class="callout-title">
        Skip The Queue Next Time: <span>Send PDF to WhatsApp</span>
      </div>
      <div class="callout-tag">
        WhatsApp: <strong>9495 706 405</strong>
      </div>
    </div>

    <div class="footer">
      <div class="footer-store">
        Printosky <span>· Thrissur & Nattika · 100+ Campuses</span>
      </div>
      <div class="footer-handle">
        @printosky_official
      </div>
    </div>
  </div>
</body>
</html>
"""


def main():
    print("================================================================")
    print(" PRINTOSKY INSTAGRAM COUNTER OFFER POSTER GENERATOR")
    print("================================================================")
    print(f"Target URL: {TARGET_POST_URL}")

    html_content = HTML_TEMPLATE.format(
        page_w=PAGE_W,
        page_h=PAGE_H,
        qr_data_uri=QR_DATA_URI,
        logo_svg=OFFICIAL_LOGO_SVG,
    )

    out_html = os.path.join(POSTERS_DIR, "poster-instagram-20off.html")
    out_png = os.path.join(POSTERS_DIR, "poster-instagram-20off.png")

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[OK] Saved HTML: {out_html}")

    print("Rendering 300 DPI print-ready PNG via Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": PAGE_W, "height": PAGE_H}, device_scale_factor=2)
        page.goto("file:///" + out_html.replace("\\", "/"), wait_until="networkidle")
        page.wait_for_timeout(1000)
        page.query_selector(".page").screenshot(path=out_png)
        browser.close()

    size_kb = os.path.getsize(out_png) / 1024
    print(f"[SUCCESS] High-res Print Poster saved: {out_png} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
