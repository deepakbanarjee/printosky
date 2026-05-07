"""
pdf_bw_ui.py — Gradio web UI for the PDF B&W conversion tool.

Run:
    python tools/pdf_bw_ui.py

Then open: http://localhost:7860  (opens automatically in your browser)

Install once:
    pip install gradio
"""

import io
import os
import sys

import gradio as gr

# Pull in the conversion engine from the same directory
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
from pdf_bw import convert  # noqa: E402


# ---------------------------------------------------------------------------
# Conversion wrapper
# ---------------------------------------------------------------------------

def run_convert(pdf_path, dpi, sensitivity, window, tone_split, white_point):
    if pdf_path is None:
        return None, "⚠  Upload a PDF first."

    # Capture the console log that convert() prints
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    out_path = None
    try:
        out_path = convert(
            src=pdf_path,
            dpi=int(dpi),
            sensitivity=round(sensitivity, 3),
            window=int(window),
            tone_split=round(tone_split, 3),
            white_point=int(white_point),
        )
    except Exception as exc:
        sys.stdout = old_stdout
        return None, f"Error: {exc}"
    finally:
        sys.stdout = old_stdout

    # Strip carriage-return progress bar noise from the log
    raw = buf.getvalue()
    clean_lines = []
    for line in raw.replace('\r', '\n').split('\n'):
        s = line.strip()
        if s and not s.startswith('['):   # drop the [###...] bar lines
            clean_lines.append(s)
    log = '\n'.join(clean_lines)

    return out_path, log


# ---------------------------------------------------------------------------
# Preset helpers
# ---------------------------------------------------------------------------

PRESETS = {
    "Default (book scan)":   (200, 0.10, 40, 0.30, 220),
    "Faded / light ink":     (200, 0.14, 40, 0.30, 220),
    "Noisy / spotted paper": (200, 0.07, 40, 0.30, 200),
    "High-res (fine print)": (300, 0.10, 50, 0.30, 220),
}

def apply_preset(name):
    return PRESETS[name]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

CSS = """
footer { display: none !important; }
.convert-btn { margin-top: 14px; }
"""

with gr.Blocks(title="PDF to B&W Converter") as demo:

    gr.Markdown("# PDF → Clean B&W for Printing")
    gr.Markdown(
        "Upload a scanned book or document. Adjust sliders if needed, "
        "then click **Convert**. The cleaned PDF will appear ready to download."
    )

    # ── Quick presets ─────────────────────────────────────────────────────────
    with gr.Row():
        preset_dd = gr.Dropdown(
            choices=list(PRESETS.keys()),
            value="Default (book scan)",
            label="Quick preset",
            scale=2,
        )
        preset_btn = gr.Button("Apply preset", scale=1)

    gr.Markdown("---")

    with gr.Row():

        # ── Left column: controls ─────────────────────────────────────────────
        with gr.Column(scale=1):

            pdf_input = gr.File(
                label="PDF file",
                file_types=[".pdf"],
                type="filepath",
            )

            gr.Markdown("### Text pages")
            dpi_sl = gr.Slider(
                100, 400, value=200, step=10,
                label="DPI",
                info="Render resolution. 200 is right for most book scans. "
                     "Raise to 300 only for very small text (slower).",
            )
            sens_sl = gr.Slider(
                0.01, 0.30, value=0.10, step=0.01,
                label="Sensitivity",
                info="How aggressively the threshold picks up ink. "
                     "Raise if thin strokes disappear · Lower if paper shows as dots.",
            )
            win_sl = gr.Slider(
                10, 100, value=40, step=5,
                label="Window size (px)",
                info="Local neighbourhood size for adaptive thresholding. "
                     "Larger = smoother transitions; smaller = sharper fine detail.",
            )

            gr.Markdown("### Page classification")
            ts_sl = gr.Slider(
                0.10, 0.60, value=0.30, step=0.01,
                label="Tone split",
                info="Mid-tone ratio above which a page is treated as a photo or "
                     "illustration rather than text. Calibrated: text ~22%, images ~45%.",
            )

            gr.Markdown("### Image / illustration pages")
            wp_sl = gr.Slider(
                180, 255, value=220, step=5,
                label="White point clip",
                info="Image-page pixels brighter than this are forced to pure white. "
                     "Lower this if printed image pages still look grey.",
            )

            convert_btn = gr.Button(
                "Convert",
                variant="primary",
                size="lg",
                elem_classes="convert-btn",
            )

        # ── Right column: output ──────────────────────────────────────────────
        with gr.Column(scale=1):

            output_file = gr.File(label="Download B&W PDF")

            log_box = gr.Textbox(
                label="Conversion log",
                lines=12,
                interactive=False,
                placeholder="Log appears here after conversion…",
            )

            gr.Markdown("""
**Tuning guide**

| Symptom | Adjust |
|---------|--------|
| Thin strokes disappear | ↑ Sensitivity |
| Paper texture prints as dots | ↓ Sensitivity |
| Grey tint on photo pages | ↓ White point clip |
| Photo pages look washed out | ↑ White point clip |
| Wrong pages treated as photos | Adjust Tone split |
| Conversion too slow | ↓ DPI |
""")

    # ── Wire up ───────────────────────────────────────────────────────────────

    all_sliders = [dpi_sl, sens_sl, win_sl, ts_sl, wp_sl]

    preset_btn.click(
        fn=apply_preset,
        inputs=[preset_dd],
        outputs=all_sliders,
    )

    convert_btn.click(
        fn=run_convert,
        inputs=[pdf_input] + all_sliders,
        outputs=[output_file, log_box],
    )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dark_theme = gr.themes.Base(
        primary_hue=gr.themes.Color(
            c50="#e0f7ff", c100="#b3ecff", c200="#80dfff", c300="#4dd1ff",
            c400="#1ac7ff", c500="#00c8ff", c600="#00a3d6", c700="#007fad",
            c800="#005b84", c900="#00375b", c950="#001a2e",
        ),
        neutral_hue=gr.themes.Color(
            c50="#e0eaf5", c100="#b3c5d9", c200="#86a0bd", c300="#597ba1",
            c400="#3c5a7a", c500="#2a3f57", c600="#1e2a36", c700="#161d26",
            c800="#111820", c900="#0a0e13", c950="#060a0e",
        ),
        font=gr.themes.GoogleFont("IBM Plex Sans"),
        font_mono=gr.themes.GoogleFont("IBM Plex Mono"),
    ).set(
        body_background_fill="#0a0e13",
        block_background_fill="#161d26",
        block_border_color="#1e2a36",
        block_label_text_color="#5a7080",
        input_background_fill="#0a0e13",
        input_border_color="#1e2a36",
        button_primary_background_fill="#00c8ff",
        button_primary_text_color="#000000",
        slider_color="#00c8ff",
    )
    demo.launch(inbrowser=True, theme=dark_theme, css=CSS)
