# -*- coding: utf-8 -*-
"""Instagram 4:5 (1080x1350) Automated Carousel Generator for Printosky.

Renders modern, high-contrast, Gen Z campus carousels using HTML/CSS + Playwright.
All pricing is 100% matched to Printosky's authoritative rate_card.py:
- A4 B&W Student: ₹2.0/sheet (≤100 sheets) | ₹1.5/sheet (>100 sheets)
- Double-sided B&W billed per sheet (effectively ₹1.00 to ₹0.75 per page!)
- A4 Colour: Tiered ₹10 (≤30), ₹9 (31–50), ₹8 (>50)
- Project Hard Binding (Gold/Silver): ₹250 | Standard: ₹220
- Special Perks for Ad Customers & Account Holders: ₹20 welcome credit + tier-1 rates from sheet 1.

Usage:
  python marketing/build_instagram_carousels.py --deck all
  python marketing/build_instagram_carousels.py --deck color-scam
  python marketing/build_instagram_carousels.py --deck account-perks
  python marketing/build_instagram_carousels.py --deck ktu-checklist
"""

import argparse
import os
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "carousels")
LOGO_SVG_PATH = os.path.join(os.path.dirname(HERE), "brand-kit", "logo", "printosky-wordmark-reverse.svg")

with open(LOGO_SVG_PATH, "r", encoding="utf-8") as f:
    OFFICIAL_LOGO_SVG = f.read().strip()
os.makedirs(OUT_DIR, exist_ok=True)

WIDTH, HEIGHT = 1080, 1350

SLIDE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800;900&family=Syne:wght@700;800;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #090D16;
    --card-bg: #131B2E;
    --orange: #FF5A1F;
    --amber: #FFB703;
    --cyan: #00F0FF;
    --text: #F8FAFC;
    --muted: #94A3B8;
    --border: rgba(255, 255, 255, 0.08);
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
    padding: 70px 60px 60px;
  }}

  /* Background Glow */
  .glow-top {{
    position: absolute;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(255, 90, 31, 0.15) 0%, rgba(0,0,0,0) 70%);
    top: -200px;
    right: -150px;
    pointer-events: none;
  }}
  .glow-bottom {{
    position: absolute;
    width: 500px;
    height: 500px;
    background: radial-gradient(circle, rgba(0, 240, 255, 0.08) 0%, rgba(0,0,0,0) 70%);
    bottom: -150px;
    left: -100px;
    pointer-events: none;
  }}

  /* Header */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: relative;
    z-index: 2;
  }}
  .brand-tag {{
    display: flex;
    align-items: center;
    gap: 16px;
  }}
  .brand-logo {{
    display: flex;
    align-items: center;
  }}
  .brand-logo svg {{
    height: 54px;
    width: auto;
    display: block;
  }}
  .brand-pill {{
    background: rgba(255, 90, 31, 0.15);
    border: 1.5px solid rgba(255, 90, 31, 0.5);
    color: var(--orange);
    font-size: 15px;
    font-weight: 800;
    text-transform: uppercase;
    padding: 8px 18px;
    border-radius: 999px;
    letter-spacing: 0.5px;
  }}
  .slide-counter {{
    font-size: 20px;
    font-weight: 800;
    color: var(--muted);
    background: rgba(255, 255, 255, 0.05);
    padding: 6px 18px;
    border-radius: 20px;
    border: 1px solid var(--border);
  }}

  /* Content Body */
  .body {{
    position: relative;
    z-index: 2;
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    margin: 40px 0;
  }}
  .kicker {{
    color: var(--orange);
    font-weight: 800;
    font-size: 18px;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 16px;
  }}
  .title {{
    font-family: 'Syne', sans-serif;
    font-size: 56px;
    font-weight: 900;
    line-height: 1.12;
    letter-spacing: -1.5px;
    margin-bottom: 24px;
  }}
  .title em {{
    font-style: normal;
    color: var(--orange);
    background: linear-gradient(135deg, #FF5A1F 0%, #FFB703 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .lead {{
    font-size: 24px;
    color: var(--muted);
    line-height: 1.45;
    margin-bottom: 30px;
    font-weight: 500;
  }}

  /* Card Grid */
  .card-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 36px;
    margin-bottom: 20px;
  }}
  .highlight-card {{
    background: linear-gradient(145deg, #182238 0%, #0F172A 100%);
    border: 1px solid rgba(255, 90, 31, 0.3);
    border-radius: 24px;
    padding: 36px;
  }}

  /* Footer */
  .footer {{
    position: relative;
    z-index: 2;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid var(--border);
    padding-top: 24px;
  }}
  .footer-left {{
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--muted);
    font-size: 17px;
    font-weight: 600;
  }}
  .footer-cta {{
    color: #FFF;
    font-weight: 800;
    font-size: 18px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .footer-cta span {{
    color: var(--orange);
  }}
</style>
</head>
<body>
  <div class="glow-top"></div>
  <div class="glow-bottom"></div>

  <div class="header">
    <div class="brand-tag">
      <div class="brand-logo">{logo_svg}</div>
      <div class="brand-pill">{tag}</div>
    </div>
    <div class="slide-counter">{slide_num} / {total_slides}</div>
  </div>

  <div class="body">
    {content_html}
  </div>

  <div class="footer">
    <div class="footer-left">
      <span>📍 Printosky | Thrissur & Nattika</span>
    </div>
    <div class="footer-cta">
      {cta_text}
    </div>
  </div>
</body>
</html>
"""

DECKS = {
    "color-scam": {
        "folder": "color-slicing-scam",
        "tag": "Campus Rate Card",
        "slides": [
            {
                "content": """
                <div class="kicker">Official Rate Card Match</div>
                <h1 class="title">How Xerox Shops <em>Tax</em> Your Project Report.</h1>
                <p class="lead">Why your 100-page B.Tech thesis cost ₹1,000 at typical shops — and how Printosky's official rate card saves you ₹700+.</p>
                <div class="card-box" style="display:flex; justify-content:space-around; text-align:center;">
                  <div>
                    <div style="font-size:42px; font-weight:900; color:#EF4444;">₹1,000</div>
                    <div style="color:var(--muted); font-size:16px; margin-top:4px;">Typical Shop (All Color)</div>
                  </div>
                  <div style="border-left:1px solid var(--border);"></div>
                  <div>
                    <div style="font-size:42px; font-weight:900; color:#10B981;">₹290</div>
                    <div style="color:var(--muted); font-size:16px; margin-top:4px;">Printosky Student Rate</div>
                  </div>
                </div>
                """,
                "cta": "Swipe to see why →",
            },
            {
                "content": """
                <div class="kicker">The Hidden Trap</div>
                <h1 class="title">The <em>"One Blue Word"</em> Rule.</h1>
                <p class="lead">In a 100-page report, 85 pages are pure black-and-white text. But because 15 pages have color graphs and a few lines have blue links:</p>
                <div class="card-box">
                  <div style="color:#EF4444; font-weight:800; font-size:20px; margin-bottom:8px;">❌ Typical Campus Xerox Shop:</div>
                  <div style="font-size:22px; line-height:1.4;">"Color detected — entire document printed on color machine." 100 sheets × ₹10 = <strong>₹1,000</strong>.</div>
                </div>
                <div class="card-box" style="border-color:rgba(16,185,129,0.3);">
                  <div style="color:#10B981; font-weight:800; font-size:20px; margin-bottom:8px;">✅ Printosky Smart Slicing:</div>
                  <div style="font-size:22px; line-height:1.4;">B&W pages route to Konica monochrome. Only true color sheets route to Epson color. Zero manual sorting.</div>
                </div>
                """,
                "cta": "Official rates breakdown →",
            },
            {
                "content": """
                <div class="kicker">Official Price Card</div>
                <h1 class="title">Printosky <em>Verified</em> Rates.</h1>
                <p class="lead">No hidden markups. Pure transparency per our store rate card:</p>
                <div class="highlight-card" style="margin-bottom:16px; padding:24px 32px;">
                  <div style="font-size:22px; font-weight:800; color:var(--amber); margin-bottom:4px;">A4 B&W Student Rate:</div>
                  <div style="font-size:20px;"><strong>₹2.0 / sheet</strong> (≤100 sheets) | <strong>₹1.5 / sheet</strong> (>100 sheets)</div>
                  <div style="color:var(--muted); font-size:16px; margin-top:4px;">💡 Double-sided B&W is billed per sheet — effectively <strong>₹1.00 / page!</strong></div>
                </div>
                <div class="highlight-card" style="padding:24px 32px;">
                  <div style="font-size:22px; font-weight:800; color:#10B981; margin-bottom:4px;">A4 True Colour Rate:</div>
                  <div style="font-size:20px;"><strong>₹10</strong> (≤30) | <strong>₹9</strong> (31–50) | <strong>₹8</strong> (>50 sheets)</div>
                  <div style="color:var(--muted); font-size:16px; margin-top:4px;">Only the pages with genuine graphs are charged as color.</div>
                </div>
                """,
                "cta": "See real math →",
            },
            {
                "content": """
                <div class="kicker">100-Page Project Report</div>
                <h1 class="title">The <em>Real Bill</em> Comparison.</h1>
                <div class="card-box" style="padding:26px 32px;">
                  <div style="font-size:18px; color:var(--muted); margin-bottom:12px;">Example: 85 B&W text pages + 15 Color graph pages</div>
                  
                  <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border); font-size:20px;">
                    <span>Other shops (all color @ ₹10):</span>
                    <span style="font-weight:800; color:#EF4444;">₹1,000</span>
                  </div>
                  <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border); font-size:20px;">
                    <span>Printosky B&W (85 × ₹2.0 student):</span>
                    <span style="font-weight:700;">₹170</span>
                  </div>
                  <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border); font-size:20px;">
                    <span>Printosky Color (15 × ₹8 bulk):</span>
                    <span style="font-weight:700;">₹120</span>
                  </div>
                  <div style="display:flex; justify-content:space-between; padding:14px 0 2px; font-size:24px; font-weight:900;">
                    <span style="color:#10B981;">Total at Printosky:</span>
                    <span style="color:#10B981;">₹290</span>
                  </div>
                </div>
                <div style="text-align:center; font-size:26px; font-weight:900; color:var(--amber);">You save ₹710 on one report. 💰</div>
                """,
                "cta": "Special perks next →",
            },
            {
                "content": """
                <div class="kicker">Exclusive Campus Deal</div>
                <h1 class="title">Special Rates for <em>Ad & Account</em> Customers.</h1>
                <p class="lead">Coming from our Instagram Ad or have a student account with us? Unlock VIP campus rates:</p>
                <div class="highlight-card" style="padding:30px; margin-bottom:16px;">
                  <div style="font-size:22px; font-weight:900; color:var(--cyan); margin-bottom:6px;">✨ ₹20 Welcome Store Credit</div>
                  <div style="color:var(--muted); font-size:18px;">Instant ₹20 credit auto-applied when ordering through our Instagram link.</div>
                </div>
                <div class="highlight-card" style="padding:30px;">
                  <div style="font-size:22px; font-weight:900; color:#10B981; margin-bottom:6px;">✨ Account Holders: Top Tier from Sheet 1</div>
                  <div style="color:var(--muted); font-size:18px;">Skip thresholds: get ₹1.5/sheet B&W & ₹8 color rates from page one + priority queue!</div>
                </div>
                """,
                "cta": "Tap Link in Bio 🔗",
            },
        ],
    },
    "account-perks": {
        "folder": "account-holder-perks",
        "tag": "VIP Campus Access",
        "slides": [
            {
                "content": """
                <div class="kicker">Student Accounts</div>
                <h1 class="title">Why 2,000+ Students Have a <em>Printosky Account</em>.</h1>
                <p class="lead">Never pay standard counter rates again. If you print lab records, assignments, or project reports regularly — open a free account.</p>
                <div class="card-box" style="text-align:center;">
                  <div style="font-size:28px; font-weight:900; color:var(--amber); margin-bottom:8px;">Zero Registration Fee • Zero Minimum Balance</div>
                  <div style="color:var(--muted); font-size:20px;">Active on WhatsApp instantly across 100+ Thrissur colleges.</div>
                </div>
                """,
                "cta": "Swipe to see perks →",
            },
            {
                "content": """
                <div class="kicker">Perk #1: Locked-in Slashed Rates</div>
                <h1 class="title">Lowest <em>B&W & Color</em> Pricing.</h1>
                <p class="lead">Account customers automatically qualify for our highest bulk discount tiers, even on small print runs:</p>
                <div class="highlight-card" style="margin-bottom:16px;">
                  <div style="font-size:22px; font-weight:800; color:var(--cyan); margin-bottom:4px;">📄 A4 B&W at ₹1.50 / sheet</div>
                  <div style="font-size:18px; color:var(--muted);">Print single side or double-sided. DS costs you just 75 paise per page!</div>
                </div>
                <div class="highlight-card">
                  <div style="font-size:22px; font-weight:800; color:#10B981; margin-bottom:4px;">🎨 A4 Colour at ₹8.00 flat</div>
                  <div style="font-size:18px; color:var(--muted);">No 30-sheet threshold needed. Deep color graphs at our lowest production rate.</div>
                </div>
                """,
                "cta": "Perk #2 next →",
            },
            {
                "content": """
                <div class="kicker">Perk #2: Queue Priority</div>
                <h1 class="title">Counter <em>Skip-The-Line</em> Pass.</h1>
                <p class="lead">Exam morning? 30 people waiting at the Xerox counter? Not your problem.</p>
                <div class="card-box">
                  <div style="font-size:22px; font-weight:800; color:var(--amber); margin-bottom:8px;">⚡ WhatsApp Pre-Print Engine</div>
                  <div style="font-size:20px; color:var(--muted); line-height:1.4;">Drop your PDF to our WhatsApp bot while on the college bus. When you reach the Printosky counter, your packet is printed, stapled, and waiting in your pickup cubby.</div>
                </div>
                """,
                "cta": "Perk #3 next →",
            },
            {
                "content": """
                <div class="kicker">Perk #3: Project & Thesis Discounts</div>
                <h1 class="title">Hardbound & Gold Foil <em>Specials</em>.</h1>
                <p class="lead">Final year project submission season is when traditional shops charge up to ₹500 for binding alone.</p>
                <div class="highlight-card" style="margin-bottom:16px;">
                  <div style="font-size:22px; font-weight:800; color:#10B981; margin-bottom:4px;">📘 Standard Hardbound: ₹220</div>
                  <div style="font-size:18px; color:var(--muted);">White, pink, blue, green with college emblem and spine lettering.</div>
                </div>
                <div class="highlight-card">
                  <div style="font-size:22px; font-weight:800; color:var(--amber); margin-bottom:4px;">✨ Deluxe Gold/Silver Foil Emboss: ₹250</div>
                  <div style="font-size:18px; color:var(--muted);">Account holders get free spine margin verification before binding!</div>
                </div>
                """,
                "cta": "How to activate →",
            },
            {
                "content": """
                <div class="kicker">10-Second Setup</div>
                <h1 class="title">Activate Your Account on <em>WhatsApp</em>.</h1>
                <p class="lead">No forms. No physical paper. Text our bot to link your student phone number and claim your ₹20 credit.</p>
                <div class="highlight-card" style="text-align:center; padding:36px 20px;">
                  <div style="font-size:24px; font-weight:900; margin-bottom:12px; color:var(--orange);">Text 'ACCOUNT' to 9495 706 405</div>
                  <div style="font-size:20px; margin-bottom:20px;">Or tap the link in bio @printosky_official to claim your ₹20 credit instantly!</div>
                  <div style="display:inline-block; background:var(--orange); color:#FFF; font-size:18px; font-weight:800; padding:12px 30px; border-radius:999px;">
                    Start Saving Now 🚀
                  </div>
                </div>
                """,
                "cta": "Save this post 🔖",
            },
        ],
    },
    "ktu-checklist": {
        "folder": "ktu-submission-checklist",
        "tag": "KTU & Calicut Guide",
        "slides": [
            {
                "content": """
                <div class="kicker">Final Submission Guide</div>
                <h1 class="title">4 Formatting Errors That Get Your <em>Report Rejected</em>.</h1>
                <p class="lead">Don't let your project guide make you re-print 120 pages on submission day. Check these 4 things before you hit print.</p>
                <div class="card-box" style="display:flex; align-items:center; gap:20px;">
                  <div style="font-size:48px;">⚠️</div>
                  <div style="font-size:20px; line-height:1.4;">Guides reject over 35% of first-print reports due to simple margin & binding allowance mistakes.</div>
                </div>
                """,
                "cta": "Swipe for Rule #1 →",
            },
            {
                "content": """
                <div class="kicker">Error #1: Binding Allowance</div>
                <h1 class="title">The <em>"Hidden Left Margin"</em> Trap.</h1>
                <p class="lead">When a report is hardbound or spiral bound, the binding eats <strong>0.5 inches (12.7 mm)</strong> of your paper on the left side.</p>
                <div class="card-box">
                  <div style="font-size:22px; font-weight:800; color:#EF4444; margin-bottom:8px;">❌ What happens with 1.0" Left Margin:</div>
                  <div style="font-size:20px; color:var(--muted);">After binding, words start disappearing into the spine fold. Examiners literally cannot read the first 2 words of every line.</div>
                </div>
                <div class="highlight-card">
                  <div style="font-size:22px; font-weight:800; color:#10B981; margin-bottom:4px;">✅ The KTU Verified Standard:</div>
                  <div style="font-size:20px;">Left Margin: <strong>1.5 inches (38 mm)</strong> | Right, Top, Bottom: <strong>1.0 inch (25 mm)</strong>.</div>
                </div>
                """,
                "cta": "Error #2 next →",
            },
            {
                "content": """
                <div class="kicker">Error #2: Page Numbering</div>
                <h1 class="title">Romans vs <em>Arabic Numbers</em>.</h1>
                <p class="lead">Certificate, Declaration, Abstract, and Table of Contents must NOT be numbered as page 1, 2, 3.</p>
                <div class="card-box" style="font-size:20px; line-height:1.6;">
                  <div style="margin-bottom:10px;">📄 <strong>Preliminary Pages:</strong> Lowercase Roman numerals (i, ii, iii, iv) centered at bottom.</div>
                  <div style="margin-bottom:10px;">📄 <strong>Title Page:</strong> Counts as (i) but has NO printed number.</div>
                  <div>📄 <strong>Chapter 1 onwards:</strong> Arabic numerals (1, 2, 3...) starting from page 1.</div>
                </div>
                """,
                "cta": "Error #3 next →",
            },
            {
                "content": """
                <div class="kicker">Error #3: Image DPI</div>
                <h1 class="title">Blurry Diagrams & <em>Smudged Circuits</em>.</h1>
                <p class="lead">Screens look sharp at 72 DPI. Print requires <strong>300 DPI minimum</strong>. A flowchart that looks fine on your MacBook will print pixelated on paper.</p>
                <div class="highlight-card">
                  <div style="font-size:20px; line-height:1.5;">
                    💡 <strong>Pro-Tip:</strong> Export all system architectures, MATLAB plots, and circuit schematics as <strong>vector PDF or SVG</strong> before placing into Word or LaTeX. They will print pin-sharp at any zoom.
                  </div>
                </div>
                """,
                "cta": "Get the Free Template →",
            },
            {
                "content": """
                <div class="kicker">Skip the Headache</div>
                <h1 class="title">Download the <em>KTU Verified</em> Template.</h1>
                <p class="lead">We've pre-configured margins, fonts (Times New Roman 12 / 1.5 line spacing), Roman page numbering, and title page hierarchy.</p>
                <div class="highlight-card" style="text-align:center; padding:36px 20px;">
                  <div style="font-size:24px; font-weight:900; margin-bottom:12px; color:var(--amber);">Want the Word & LaTeX Template?</div>
                  <div style="font-size:20px; margin-bottom:20px;">Comment <strong>"KTU"</strong> on this post and our bot will DM you the instant download link!</div>
                  <div style="font-size:16px; color:var(--muted);">Printosky | Thrissur & Nattika</div>
                </div>
                """,
                "cta": "Share with project team 🔖",
            },
        ],
    },
    "binding-guide": {
        "folder": "thesis-binding-guide",
        "tag": "Campus Binding Guide",
        "slides": [
            {
                "content": """
                <div class="kicker">Official College Guide</div>
                <h1 class="title">Spiral vs Wiro vs <em>Hardbound</em>: What Do You Need?</h1>
                <p class="lead">Don't spend ₹250 on a hardbound report if your department only asked for spiral — and don't get your final thesis rejected for submitting a wire bind.</p>
                <div class="card-box" style="display:flex; justify-content:space-around; text-align:center;">
                  <div>
                    <div style="font-size:36px; font-weight:900; color:var(--amber);">₹30–₹50</div>
                    <div style="color:var(--muted); font-size:16px; margin-top:4px;">Spiral / Wiro (Labs)</div>
                  </div>
                  <div style="border-left:1px solid var(--border);"></div>
                  <div>
                    <div style="font-size:36px; font-weight:900; color:#10B981;">₹220–₹250</div>
                    <div style="color:var(--muted); font-size:16px; margin-top:4px;">Deluxe Hardbound (Thesis)</div>
                  </div>
                </div>
                """,
                "cta": "Swipe for breakdown →",
            },
            {
                "content": """
                <div class="kicker">Option 1: Daily Submissions</div>
                <h1 class="title">Plastic Spiral & <em>Twin-Loop Wiro</em> Binding.</h1>
                <p class="lead"><strong>Starting at ₹30 (up to 50 sheets).</strong></p>
                <div class="card-box">
                  <div style="font-size:22px; font-weight:800; color:var(--orange); margin-bottom:8px;">📌 Best For:</div>
                  <div style="font-size:20px; color:#FFF; line-height:1.4; margin-bottom:14px;">• Weekly Lab Records & Rough Drafts<br>• Assignment packets & Seminar Handouts</div>
                  <div style="font-size:18px; color:var(--muted);">Lays 360° flat on lab benches. Clear protective OHP cover on front + 300 GSM leatherette back.</div>
                </div>
                """,
                "cta": "Next: Soft Thermal Binding →",
            },
            {
                "content": """
                <div class="kicker">Option 2: Interim Reports</div>
                <h1 class="title">Soft Tape & <em>Thermal Book</em> Binding.</h1>
                <p class="lead"><strong>Flat ₹60 per copy.</strong></p>
                <div class="card-box">
                  <div style="font-size:22px; font-weight:800; color:var(--cyan); margin-bottom:8px;">📌 Best For:</div>
                  <div style="font-size:20px; color:#FFF; line-height:1.4; margin-bottom:14px;">• Mini-Projects (Phase 1 Submissions)<br>• Internship Diaries & Case Studies</div>
                  <div style="font-size:18px; color:var(--muted);">Gives a clean, square paperback spine without the weight or cost of full hardbound. Perfect for guide approvals.</div>
                </div>
                """,
                "cta": "Next: Final Thesis Standard →",
            },
            {
                "content": """
                <div class="kicker">Option 3: The KTU & Calicut Standard</div>
                <h1 class="title">Deluxe Hardbound With <em>Gold Foil</em> Emboss.</h1>
                <p class="lead"><strong>₹220 (Standard) / ₹250 (Deluxe Gold Foil Stamped).</strong></p>
                <div class="card-box">
                  <div style="font-size:22px; font-weight:800; color:var(--amber); margin-bottom:8px;">📌 The Final Submission Standard:</div>
                  <div style="font-size:20px; color:#FFF; line-height:1.4; margin-bottom:14px;">• Official B.Tech / M.Tech / MBA Final Thesis<br>• Library Archive Copies & Department Submissions</div>
                  <div style="font-size:18px; color:var(--muted);">2.5mm heavy greyboard, deep black / royal navy leatherette finish, hot-stamped gold college crest & title lettering that never peels.</div>
                </div>
                """,
                "cta": "Next-Day Guarantee →",
            },
            {
                "content": """
                <div class="kicker">Zero Waiting At The Shop</div>
                <h1 class="title">Send on WhatsApp, <em>Pick Up Next Day</em>.</h1>
                <p class="lead">Don't spend 4 hours waiting at the binder's shop the night before viva.</p>
                <div class="highlight-card" style="text-align:center; padding:36px 20px;">
                  <div style="font-size:24px; font-weight:900; margin-bottom:12px; color:var(--amber);">Get Exact Project Quote in 2 Seconds</div>
                  <div style="font-size:20px; margin-bottom:20px;">Comment <strong>"BIND"</strong> or send your PDF to WhatsApp: <strong>9495 706 405</strong></div>
                  <div style="font-size:16px; color:var(--muted);">Printosky | Thrissur & Nattika</div>
                </div>
                """,
                "cta": "Save for project submission 🔖",
            },
        ],
    },
}


def render_carousel_deck(deck_key):
    cfg = DECKS.get(deck_key)
    if not cfg:
        print(f"Unknown deck: {deck_key}")
        return

    folder_path = os.path.join(OUT_DIR, cfg["folder"])
    os.makedirs(folder_path, exist_ok=True)

    total = len(cfg["slides"])
    print(f"\nRendering Carousel: {cfg['folder']} ({total} slides @ 1080x1350)...")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

        for idx, slide in enumerate(cfg["slides"], 1):
            html = SLIDE_TEMPLATE.format(
                width=WIDTH,
                height=HEIGHT,
                tag=cfg["tag"],
                slide_num=idx,
                total_slides=total,
                content_html=slide["content"],
                cta_text=slide["cta"],
                logo_svg=OFFICIAL_LOGO_SVG,
            )
            temp_html = os.path.join(folder_path, f"slide_{idx}.html")
            png_out = os.path.join(folder_path, f"slide_{idx}.png")

            with open(temp_html, "w", encoding="utf-8") as f:
                f.write(html)

            page.goto("file:///" + temp_html.replace("\\", "/"), wait_until="networkidle")
            page.screenshot(path=png_out)
            size_kb = os.path.getsize(png_out) / 1024
            print(f"  [OK] Slide {idx}/{total} -> {png_out} ({size_kb:.0f} KB)")

        browser.close()
    print(f"[SUCCESS] Deck '{cfg['folder']}' complete! Ready to post on Instagram.")


def main():
    parser = argparse.ArgumentParser(description="Instagram Carousel Generator for Printosky")
    parser.add_argument("--deck", type=str, default="all", help="Deck name (color-scam, account-perks, ktu-checklist, or all)")
    args = parser.parse_args()

    if args.deck == "all":
        for k in DECKS.keys():
            render_carousel_deck(k)
    else:
        render_carousel_deck(args.deck)


if __name__ == "__main__":
    main()
