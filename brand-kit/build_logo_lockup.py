# -*- coding: utf-8 -*-
"""Printosky wordmark locked up with the Nattika store's phone number beneath it.

Sized for real-world print: the wordmark stands 1 cm tall and the number 0.5 cm
tall. The page is authored in cm and shot at 300 DPI (device_scale_factor =
300/96), and the PNG is stamped with 300-DPI metadata so it drops into a
document at true physical size.

Renders twice:
    logo-nattika.png        transparent background (overlay on anything)
    logo-nattika-white.png  white background (safe for print / documents)

The wordmark is embedded from logo/printosky-wordmark.svg (vector), so the mark
stays crisp. The number is set in DM Sans — plain, unambiguous digits.
"""
import base64, os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_SVG = os.path.join(HERE, "logo", "printosky-wordmark.svg")

INK = "#182A3D"
ORANGE = "#F0571E"
NUMBER = "+91 94469 03907"

DPI = 300
CSS_PX_PER_INCH = 96.0
SCALE = DPI / CSS_PX_PER_INCH        # device_scale_factor to reach 300 DPI

LOGO_H_CM = 1.372                    # element box; SVG viewBox has ~27% vertical
                                     # slack, so this lands the *visible* mark at
                                     # a true 1.0 cm (verified by measuring ink)
NUM_FONT_CM = 0.70                   # tuned so the digits stand ~0.5 cm tall
GLYPH_CM = 0.46                      # WhatsApp glyph, matched to digit height


def svg_uri(path):
    with open(path, "rb") as f:
        return "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode("ascii")


LOGO = svg_uri(LOGO_SVG)

PHONE_ICON = (
    '<svg viewBox="0 0 24 24" fill="%s"><path d="M12.04 2c-5.46 0-9.9 4.44-9.9 9.9 0 '
    '1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.9-4.44 '
    '9.9-9.9S17.5 2 12.04 2zm5.8 14.03c-.24.68-1.4 1.3-1.93 1.36-.49.05-1.03.09-3.32-.7-2.8-'
    '.98-4.6-3.85-4.75-4.03-.14-.18-1.14-1.52-1.14-2.9 0-1.38.72-2.06.98-2.34.26-.28.56-.35.75-'
    '.35.19 0 .38 0 .54.01.17.01.4-.06.63.48.24.56.8 1.94.87 2.08.07.14.12.3.02.48-.09.18-.14.3-'
    '.28.45-.14.15-.29.34-.42.46-.14.13-.29.28-.12.55.16.28.72 1.19 1.55 1.93 1.06.95 1.96 1.24 '
    '2.24 1.38.28.14.44.12.6-.07.16-.19.68-.79.87-1.06.18-.28.37-.23.62-.14.26.09 1.62.76 1.9.9.28'
    '.14.46.21.53.33.07.12.07.68-.17 1.36z"/></svg>' % ORANGE
)

HTML = u"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@700&display=swap" rel="stylesheet">
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  body{__BODY_BG__}

  .lockup{
    display:inline-flex;flex-direction:column;align-items:center;
    padding:0.32cm 0.4cm;
  }
  .logo{height:__LOGO_H__cm;width:auto;display:block;}

  .contact{display:flex;align-items:center;gap:0.16cm;margin-top:0.30cm;}
  .contact svg{width:__GLYPH__cm;height:__GLYPH__cm;flex:none;}
  .num{
    font-family:'DM Sans',Segoe UI,sans-serif;font-weight:700;
    font-size:__NUM_FONT__cm;letter-spacing:0.2px;color:__INK__;
    white-space:nowrap;line-height:1;
  }
</style></head><body>

  <div class="lockup">
    <img class="logo" src="__LOGO__" alt="Printosky">
    <div class="contact">__PHONE__<span class="num">__NUMBER__</span></div>
  </div>

</body></html>
"""


def _fill(body_bg):
    return (HTML.replace("__BODY_BG__", body_bg)
                .replace("__INK__", INK)
                .replace("__LOGO_H__", "%.3f" % LOGO_H_CM)
                .replace("__NUM_FONT__", "%.3f" % NUM_FONT_CM)
                .replace("__GLYPH__", "%.3f" % GLYPH_CM)
                .replace("__LOGO__", LOGO)
                .replace("__PHONE__", PHONE_ICON)
                .replace("__NUMBER__", NUMBER))


def render(html_str, png_path, transparent):
    from playwright.sync_api import sync_playwright

    tmp = png_path + ".html"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_str)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=SCALE)
        page.goto("file:///" + tmp.replace("\\", "/"), wait_until="networkidle")
        page.wait_for_timeout(1000)          # let the webfont settle
        page.query_selector(".lockup").screenshot(path=png_path, omit_background=transparent)
        browser.close()
    os.remove(tmp)
    # stamp physical resolution so the file places at 1 cm / 0.5 cm, not screen size
    im = Image.open(png_path)
    im.save(png_path, dpi=(DPI, DPI))


def main():
    render(_fill("background:transparent;"),
           os.path.join(HERE, "logo-nattika.png"), transparent=True)
    render(_fill("background:#fff;"),
           os.path.join(HERE, "logo-nattika-white.png"), transparent=False)
    for name in ("logo-nattika.png", "logo-nattika-white.png"):
        p = os.path.join(HERE, name)
        w, h = Image.open(p).size
        print("%-26s %5d x %-5d px  (%.2f x %.2f cm @ %d DPI)  %5.0f KB"
              % (name, w, h, w / DPI * 2.54, h / DPI * 2.54, DPI, os.path.getsize(p) / 1024))


if __name__ == "__main__":
    main()
