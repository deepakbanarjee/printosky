# HANDOFF: Instagram Expansion & Marketing Automation Engine

**Date:** September 7, 2026  
**Status:** Live & Production-Ready  
**Instagram Handle:** `@printosky_official` (ID: `17841468855448471`)  
**Linked Facebook Page:** `Printosky.com` (ID: `999350999936953`)  

---

## 1. Executive Summary

Over the past sessions, we established, automated, and scaled the public marketing presence of **Printosky** on Instagram. We transitioned from an empty 3-follower account into an active student brand with 2 live carousel posts, 20 followers, 7,161+ unique accounts reached via Meta ads, an automated Comment-to-DM engine, 14 pre-rendered daily stories (spanning 2 weeks), 2 upcoming carousel decks, and an in-store counter QR poster.

---

## 2. Hard Invariants & Core Rules (DO NOT BREAK)

1. **Strict Brand Rule (Hard Rule, Non-Negotiable):**
   * The customer-facing brand is strictly **Printosky**.
   * **NEVER** mention "Oxygen" or "Oxygen Students Paradise" in any public posts, captions, marketing assets, carousels, stories, or customer-facing messages.
   * This rule is stamped permanently in [`.agents/AGENTS.md`](file:///d:/PY/printosky/.agents/AGENTS.md).

2. **Official Rate Card Contract (`rate_card.py`):**
   * **Rates are billed per SHEET, not per page.**
   * A4 B&W Student Rate: ₹2.00/sheet (down to ₹1.50/sheet for bulk >100 sheets or Account holders).
   * Double-Sided (DS) B&W is billed per sheet — bringing page cost down to **75 paise to ₹1.00 per page**.
   * A4 Colour: Tiered starting at ₹8.00–₹10.00/sheet (slicing plain B&W text from genuine color graphs automatically).
   * Binding: Standard Hardbound is ₹220; Deluxe Foil Stamped Hardbound is ₹250; Soft Thermal Paperback is ₹60; Spiral/Wiro is ₹30–₹50.
   * 100-page project comparison: ₹1,000 elsewhere vs ₹290 at Printosky (saving ₹710).

3. **Security & Secrets:**
   * Never echo or hardcode API tokens, app secrets, or passwords in chat or files. All credentials reside strictly in `.env`.

---

## 3. Meta Credentials & API Setup

* **Permanent Access Token:** Stored in `.env` as `INSTAGRAM_PAGE_ACCESS_TOKEN`.
  * Verified token type: `SYSTEM_USER` under Meta App `Printosky Automation` (`1650967860001560`).
  * Token expiration: `expires_at: 0` (**Permanent / Never Expires**).
  * Scopes granted: `pages_show_list`, `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_messages`, `pages_read_engagement`, `instagram_manage_insights`.
* **Meta Page ID:** `999350999936953` (`Printosky.com`)
* **Instagram Business ID:** `17841468855448471` (`@printosky_official`)

---

## 4. Codebase Architecture & Key Files

```
d:\PY\printosky\
├── marketing\
│   ├── instagram_autopilot.py          # Instagram Graph API CLI (status, posts, publish)
│   ├── instagram_comment_bot.py        # 24/7 Automated Comment-to-DM Engine
│   ├── build_instagram_carousels.py    # Playwright 1080x1350px 4:5 carousel generator
│   ├── build_weekly_stories.py         # Playwright 1080x1920px 9:16 story/status generator
│   ├── build_instagram_offer_poster.py # 300 DPI A4 In-store QR counter poster generator
│   ├── carousels\                      # Rendered 1080x1350 PNG carousel slides
│   │   ├── color-slicing-scam\         # Deck #1 (5 Slides - LIVE)
│   │   ├── account-holder-perks\       # Deck #2 (5 Slides - LIVE)
│   │   ├── ktu-submission-checklist\   # Deck #3 (5 Slides - Scheduled Sep 11)
│   │   └── thesis-binding-guide\       # Deck #4 (5 Slides - Scheduled Sep 16)
│   ├── stories\                        # 14 Rendered 1080x1920 Story Cards (Sep 7 – Sep 20)
│   │   ├── mon_lab_rush.png to sun_hostel_prep.png (Week 1)
│   │   └── w2_mon_assignment_rush.png to w2_sun_whatsapp_quote.png (Week 2)
│   └── posters\
│       ├── poster-instagram-20off.html
│       └── poster-instagram-20off.png  # 300 DPI A4 Counter Poster
├── brand-kit\
│   └── logo\                           # Official Gabarito + teal ring mark (SVG + PNG)
└── website\assets\                     # Synchronized latest brand marks and favicons
```

---

## 5. Automated Comment-to-DM Engine (`marketing/instagram_comment_bot.py`)

Whenever a user leaves a comment matching any keyword, the bot replies publicly and sends an instant private DM to their inbox:

| Trigger | Keywords | Action & Offer Sent in DM |
|:---|:---|:---|
| **`"KTU"`** | `ktu`, `template`, `format`, `margin`, `calicut` | Verified KTU/Calicut Word & LaTeX template link + ₹20 print credit |
| **`"SKIP"` / `"PRINT"`** | `skip`, `print`, `queue`, `xerox`, `order`, `credit` | Instant WhatsApp pre-print link with pre-applied ₹20 credit |
| **`"ACCOUNT"`** | `account`, `vip`, `student`, `discount`, `perks` | Student VIP Account setup link (₹1.50 B&W / ₹8 Color from sheet 1) |
| **`"BIND"`** | `bind`, `binding`, `hardbound`, `spiral`, `foil` | Spiral (₹30) vs Thermal (₹60) vs Hardbound (₹220) price guide |
| **`"CALC"`** | `calc`, `rate`, `cost`, `price`, `slicing` | Color slicing rate calculator link |

* **How to Run on Store PC:**
  ```powershell
  python marketing/instagram_comment_bot.py --watch
  ```
  *(Deduplication is stored in `marketing/data/processed_comments.json` to prevent double-replying).*

---

## 6. Live Grid Status & Performance

* **Live Post #1 (Published Sep 4):**  
  Link: `https://www.instagram.com/p/Dc24ngGjDYa/`  
  Topic: *How Xerox Shops Tax Your Project Report (Color Slicing Scam)*
* **Live Post #2 (Published Sep 6):**  
  Link: `https://www.instagram.com/p/Dc74tSEEz3n/`  
  Topic: *Why 2,000+ Students Have a Printosky Account (75 paise/page perks)*
* **Performance Metrics:**
  * Total unique accounts reached: **7,161+ people** (ad + organic).
  * Followers: Grew from **3 ➔ 20 followers** (+566% growth).
  * Post #2 Interactions: First 2 likes and organic saves recorded.

---

## 7. Next 14 Days Editorial Schedule (Sep 7 – Sep 20, 2026)

The user is away for the next 1–2 weeks. Everything below is rendered and ready to be scheduled in **[Meta Business Suite Planner](https://business.facebook.com/latest/planner)**:

### Week 1 (Sep 7 – Sep 13):
* **Daily Stories (8:00 AM / 8:30 PM):** `marketing/stories/mon_lab_rush.png` through `sun_hostel_prep.png`.
* **Feed Post #3 (Friday, Sep 11 at 6:00 PM):**  
  * Asset: `marketing/carousels/ktu-submission-checklist/` (5 Slides).  
  * Hook: *35% of reports get rejected on margins. Comment "KTU" for verified template.*

### Week 2 (Sep 14 – Sep 20):
* **Daily Stories:** `marketing/stories/w2_mon_assignment_rush.png` through `w2_sun_whatsapp_quote.png`.
* **Feed Post #4 (Wednesday, Sep 16 at 6:00 PM):**  
  * Asset: `marketing/carousels/thesis-binding-guide/` (5 Slides).  
  * Hook: *Spiral vs Hardbound: What does your department actually require? Comment "BIND" for rates.*

---

## 8. Quick Commands for Incoming Agent

* **Check live account stats & followers:**
  ```powershell
  python marketing/instagram_autopilot.py --status
  ```
* **Check recent posts & engagement:**
  ```powershell
  python marketing/instagram_autopilot.py --recent-posts
  ```
* **Poll comments & send pending DMs:**
  ```powershell
  python marketing/instagram_comment_bot.py --poll
  ```
* **Re-render any carousel deck:**
  ```powershell
  python marketing/build_instagram_carousels.py --deck [color-scam|account-perks|ktu-checklist|binding-guide|all]
  ```
* **Re-render all 14 stories:**
  ```powershell
  python marketing/build_weekly_stories.py
  ```
* **Re-render the A4 offer poster:**
  ```powershell
  python marketing/build_instagram_offer_poster.py
  ```
