"""
RETIRED 2026-05-12 — Thermal receipt printer endpoint.

Extracted from print_server.py (RECEIPT_PRINTER constant + handle_print_receipt
function + the POST /print-receipt route registration in the request handler).

WHY RETIRED (per vault feature-graveyard-triage-2026-05.md):
  - RECEIPT_PRINTER was always None -- no hardware was ever connected.
  - handle_print_receipt() returned {"ok": False, "error": "..."} on every
    call. The python-escpos integration was commented out.
  - Every call site was dead. Removing it shrinks print_server.py by 33
    lines without behaviour change.

HOW TO REVIVE (when thermal printer hardware is actually ordered):
  1. `pip install python-escpos`.
  2. Plug in the printer, find its USB vendor/product IDs (lsusb on Linux,
     Device Manager on Windows, or `python -m escpos.detect`).
  3. Copy this file's `handle_print_receipt` + the constant back into
     print_server.py with RECEIPT_PRINTER = {"vendor": 0xXXXX, "product": 0xXXXX}.
  4. Restore the `/print-receipt` route handler block:
         if path == "/print-receipt":
             body = self._read_body()
             self._json(200, handle_print_receipt(body))
             return
  5. Uncomment the escpos lines inside handle_print_receipt and adjust
     the receipt format (PRINTOSKY header, job_id, amount, store address,
     UPI QR if relevant, etc.).
  6. Wire admin.html and/or the bot's post-payment confirmation step to
     POST /print-receipt with {"job_id": "OSP-..."}.
"""
import logging


# ── A9: Print receipt (thermal printer stub) ──────────────────────────────────

RECEIPT_PRINTER = None  # Set to {"vendor": 0xXXXX, "product": 0xXXXX} when hardware arrives


def handle_print_receipt(body: dict, _db_factory=None) -> dict:
    """
    POST /print-receipt
    Fire thermal receipt printer. Currently a stub -- returns not-configured if no hardware.

    `_db_factory` is the dependency-injection seam: when reviving this code,
    pass `lambda: _db()` from print_server.py so the function can look up the
    job row. The original code called `_db()` directly because it was inside
    print_server.py.
    """
    if RECEIPT_PRINTER is None:
        return {"ok": False, "error": "Receipt printer not configured"}

    job_id = body.get("job_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    try:
        if _db_factory is None:
            return {"ok": False, "error": "no db factory provided (retired-mode)"}
        conn = _db_factory()
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {"ok": False, "error": "Job not found"}

        # When hardware arrives: format receipt and send via python-escpos
        # from escpos.printer import Usb
        # p = Usb(RECEIPT_PRINTER["vendor"], RECEIPT_PRINTER["product"])
        # p.text(f"PRINTOSKY\n{job_id}\n...")
        # p.cut()

        logging.info("Receipt printer: job %s (hardware not yet connected)", job_id)
        return {"ok": True, "note": "Hardware not connected yet"}

    except Exception as e:
        return {"ok": False, "error": str(e)}
