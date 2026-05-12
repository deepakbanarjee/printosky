"""
pdf_tools_server.py — Local tools server for staff PDF utilities.
Runs on store PC at port 3006.

Endpoints:
  GET  /status       — health check
  POST /bw-convert   — multipart PDF + params → returns converted PDF with stat headers

Start:
  python tools/pdf_tools_server.py
  (or double-click pdf_tools_server.bat)
"""

import io
import logging
import os
import sys
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

# Pull in the conversion engine from the same directory
_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from pdf_bw import convert  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pdf-tools")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# CORS — allow calls from Netlify and localhost (no flask-cors dependency)
# ---------------------------------------------------------------------------

ALLOWED_ORIGINS = {
    "https://printosky.com",
    "https://printosky.netlify.app",
}


@app.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS or origin.startswith("http://localhost"):
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/bw-convert", methods=["OPTIONS"])
@app.route("/status", methods=["OPTIONS"])
def preflight():
    return "", 200


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.route("/status")
def status():
    return jsonify({"ok": True, "service": "pdf-tools", "version": "1.0"})


@app.route("/bw-convert", methods=["POST"])
def bw_convert():
    if "pdf" not in request.files:
        return jsonify({"error": "No PDF file in request (field name must be 'pdf')"}), 400

    pdf_file = request.files["pdf"]
    fname = pdf_file.filename or "upload.pdf"
    if not fname.lower().endswith(".pdf"):
        return jsonify({"error": "Uploaded file must be a .pdf"}), 400

    # Parse params with clamped safe ranges
    try:
        dpi         = max(72,   min(600,  int(request.form.get("dpi",         200))))
        sensitivity = max(0.01, min(0.50, float(request.form.get("sensitivity", 0.10))))
        window      = max(5,    min(200,  int(request.form.get("window",       40))))
        tone_split  = max(0.05, min(0.90, float(request.form.get("tone_split",  0.30))))
        white_point = max(150,  min(255,  int(request.form.get("white_point",  220))))
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid parameter: {e}"}), 400

    log.info(
        "BW convert  file=%r  dpi=%d  sens=%.2f  win=%d  ts=%.2f  wp=%d",
        fname, dpi, sensitivity, window, tone_split, white_point,
    )

    # Work entirely in a temp directory so cleanup is guaranteed
    with tempfile.TemporaryDirectory(prefix="bw_") as tmpdir:
        src  = os.path.join(tmpdir, fname)
        stem = Path(fname).stem
        dst  = os.path.join(tmpdir, f"{stem}_BW.pdf")

        pdf_file.save(src)
        input_size = os.path.getsize(src)

        # Redirect stdout to suppress the progress bar in server logs
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            out_path = convert(
                src=src, dst=dst,
                dpi=dpi, sensitivity=sensitivity,
                window=window, tone_split=tone_split,
                white_point=white_point,
            )
        except Exception as exc:
            sys.stdout = old_stdout
            log.exception("Conversion failed")
            return jsonify({"error": str(exc)}), 500
        finally:
            sys.stdout = old_stdout

        # Parse stats from the captured log
        conv_log    = buf.getvalue()
        text_pages  = _parse_stat(conv_log, "Text pages")
        image_pages = _parse_stat(conv_log, "Image pages")
        elapsed     = _parse_elapsed(conv_log)
        output_size = os.path.getsize(out_path)

        log.info(
            "Done  text=%s  image=%s  %.0fs  %.1fMB→%.1fMB",
            text_pages, image_pages, elapsed,
            input_size / 1e6, output_size / 1e6,
        )

        # Read into memory before the temp dir is deleted
        with open(out_path, "rb") as f:
            pdf_bytes = f.read()

    out_name = f"{stem}_BW.pdf"
    resp = send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=out_name,
    )
    # Expose conversion stats to the browser
    resp.headers["X-Text-Pages"]  = str(text_pages)
    resp.headers["X-Image-Pages"] = str(image_pages)
    resp.headers["X-Elapsed-Sec"] = str(round(elapsed))
    resp.headers["X-Input-MB"]    = f"{input_size / 1e6:.1f}"
    resp.headers["X-Output-MB"]   = f"{output_size / 1e6:.1f}"
    resp.headers["Access-Control-Expose-Headers"] = (
        "X-Text-Pages, X-Image-Pages, X-Elapsed-Sec, X-Input-MB, X-Output-MB"
    )
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_stat(log_text: str, label: str) -> str:
    for line in log_text.splitlines():
        if label in line and ":" in line:
            return line.split(":")[-1].strip().split()[0]
    return "?"


def _parse_elapsed(log_text: str) -> float:
    for line in log_text.splitlines():
        if "Time" in line and ":" in line:
            try:
                return float(line.split(":")[-1].strip().rstrip("s"))
            except ValueError:
                pass
    return 0.0


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("TOOLS_PORT", 3006))
    log.info("PDF Tools Server  →  http://localhost:%d", port)
    log.info("Endpoints:  GET /status  |  POST /bw-convert")
    # threaded=False: convert() is CPU-heavy; serialise requests
    app.run(host="0.0.0.0", port=port, debug=False, threaded=False)
