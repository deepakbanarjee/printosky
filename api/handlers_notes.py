"""handlers_notes.py — WhatsApp bot flow for Student Notes Marketplace.

Students upload PDF notes via WhatsApp. This module handles:
  - Upload trigger detection ("upload notes", "sell notes", "share notes")
  - Multi-step conversation: PDF → title → category → subject → confirm
  - PDF page counting (pdfplumber, already in requirements.txt)
  - Note code generation (NOTE-YYYYMMDD-XXXX)
  - "print note NOTE-XXXX" trigger for customers wanting to print someone's notes

Mirrors the pattern in book_bot.py. Keep the two in sync when changing
shared helpers (_send_text, _send_buttons, _send_list).
"""
from __future__ import annotations

import io
import logging
import random
import string
from datetime import datetime, timezone

import pdfplumber

logger = logging.getLogger(__name__)

# ── step names ────────────────────────────────────────────────────────────────
_NOTE_STEPS: frozenset[str] = frozenset({
    "note_await_pdf",
    "note_title",
    "note_category",
    "note_subject",
    "note_confirm",
})

# ── category display labels ───────────────────────────────────────────────────
_CATEGORIES: dict[str, str] = {
    "kerala_university":    "Kerala University",
    "mg_university":        "MG University",
    "calicut_university":   "Calicut University",
    "cusat":                "CUSAT",
    "entrance_exam":        "Entrance Exam",
}

# ── trigger keywords ──────────────────────────────────────────────────────────
_UPLOAD_TRIGGERS: tuple[str, ...] = (
    "upload notes",
    "sell notes",
    "share notes",
    "upload note",
    "sell note",
    "share note",
    "upload my notes",
    "sell my notes",
    "share my notes",
)


# ── public API ────────────────────────────────────────────────────────────────

def is_notes_trigger(text: str) -> bool:
    """Return True if the text is a notes-upload trigger keyword."""
    lower = text.strip().lower()
    return any(lower == t or lower.startswith(t) for t in _UPLOAD_TRIGGERS)


def is_print_note_trigger(text: str) -> str | None:
    """Return note_code if message matches 'print note NOTE-XXXX', else None."""
    lower = text.strip().lower()
    if lower.startswith("print note "):
        parts = text.strip().split()
        if len(parts) >= 3:
            candidate = parts[2].upper()
            if candidate.startswith("NOTE-"):
                return candidate
    return None


def maybe_handle_notes(phone: str, text: str, name: str) -> list | None:
    """Entry point from _handle_text. Returns WA reply list or None.

    None means "this message is not for the notes flow — let normal routing
    continue." An empty list means "handled, send nothing."
    """
    from db_cloud import get_session, save_session  # lazy import avoids circular

    session = get_session(phone)
    step = (session or {}).get("step", "")

    # ── in-progress flow ──────────────────────────────────────────────────────
    if step in _NOTE_STEPS:
        return _advance_flow(phone, text, name, session)

    # ── fresh trigger ─────────────────────────────────────────────────────────
    if is_notes_trigger(text):
        save_session(phone, {"step": "note_await_pdf", "name": name})
        return [
            _send_text(
                phone,
                f"Hi {name}! Send me your notes PDF and I'll list it for others to print.\n\n"
                "You earn *10 paise store credit* for every page someone prints "
                "(e.g. 50-page notes = ₹5 credit per copy printed)\n"
                "Notes stay under your name. Admin reviews before publishing.\n\n"
                "*Please send the PDF now.*",
            )
        ]

    return None


def handle_notes_pdf(phone: str, pdf_bytes: bytes, filename: str) -> list:
    """Called from _handle_media when session.step == 'note_await_pdf'.

    Counts pages, uploads to private bucket, stores note_code in session,
    then advances to title step.
    """
    from db_cloud import get_session, save_session, upload_note_pdf

    session = get_session(phone) or {}
    name = session.get("name", "there")

    pages = _count_pdf_pages(pdf_bytes)
    if pages == 0:
        return [_send_text(phone, "I couldn't read that PDF. Please send a valid PDF file.")]

    note_code = _gen_note_code()
    storage_path = upload_note_pdf(note_code, pdf_bytes)
    if not storage_path:
        return [_send_text(phone, "Upload failed. Please try again in a moment.")]

    save_session(phone, {
        "step": "note_title",
        "name": name,
        "note_code": note_code,
        "note_pages": pages,
        "note_storage_path": storage_path,
    })

    return [
        _send_text(
            phone,
            f"Got your PDF! *{pages} pages* uploaded.\n\n"
            "What should I call these notes? Give me a short title, e.g.:\n"
            "_\"B.Com Part 2 — Accounting Notes\"_",
        )
    ]


# ── internal flow steps ───────────────────────────────────────────────────────

def _advance_flow(phone: str, text: str, name: str, session: dict) -> list:
    """Advance the multi-step upload flow based on current step."""
    from db_cloud import save_session

    step = session.get("step", "")
    text = text.strip()

    if step == "note_await_pdf":
        return [_send_text(phone, "Please *send the PDF file* — I'm waiting for it.")]

    if step == "note_title":
        if len(text) < 5:
            return [_send_text(phone, "Please give a more descriptive title (at least 5 characters).")]
        save_session(phone, {**session, "step": "note_category", "note_title": text})
        return [_ask_category(phone)]

    if step == "note_category":
        chosen = _match_category(text)
        if not chosen:
            return [_ask_category(phone, invalid=True)]
        save_session(phone, {**session, "step": "note_subject", "note_category": chosen})
        return [_send_text(phone, "What subject are these notes for? (e.g. _\"Financial Accounting\"_)")]

    if step == "note_subject":
        if len(text) < 3:
            return [_send_text(phone, "Please enter a subject name (at least 3 characters).")]
        save_session(phone, {**session, "step": "note_confirm", "note_subject": text})
        return [_confirm_card(phone, session, text)]

    if step == "note_confirm":
        lower = text.lower()
        if lower in ("yes", "confirm", "ok", "submit", "send", "yes send"):
            return _submit_note(phone, session)
        if lower in ("no", "cancel", "back", "edit"):
            from db_cloud import save_session as _sv
            _sv(phone, {})
            return [_send_text(phone, "No problem! Type _\"upload notes\"_ to start over.")]
        return [_send_text(phone, "Please reply *Yes* to submit or *No* to cancel.")]

    return []


def _submit_note(phone: str, session: dict) -> list:
    """Persist the note row and notify the uploader."""
    from db_cloud import create_note, save_session

    note_code = session.get("note_code", "")
    title = session.get("note_title", "")
    category = session.get("note_category", "")
    subject = session.get("note_subject", "")
    pages = int(session.get("note_pages", 0))
    storage_path = session.get("note_storage_path", "")
    name = session.get("name", "")

    result = create_note(
        note_code=note_code,
        uploader_phone=phone,
        title=title,
        category=category,
        subject=subject,
        page_count=pages,
        storage_path=storage_path,
        attests=True,
    )
    if not result:
        return [_send_text(phone, "Something went wrong saving your notes. Please try again.")]

    save_session(phone, {})

    cat_label = _CATEGORIES.get(category, category)
    potential_rs = pages * 10 / 100   # paise → rupees
    return [
        _send_text(
            phone,
            f"*{title}* submitted for review!\n\n"
            f"{pages} pages · {cat_label} · {subject}\n"
            f"Code: *{note_code}*\n\n"
            f"You'll earn *₹{potential_rs:.2f} store credit* for every copy printed.\n\n"
            "Admin reviews within a few hours. We'll message you once it's live!",
        )
    ]


# ── UI helpers ────────────────────────────────────────────────────────────────

def _ask_category(phone: str, invalid: bool = False) -> dict:
    prefix = "I didn't recognise that. " if invalid else ""
    rows = [{"id": k, "title": v, "description": ""} for k, v in _CATEGORIES.items()]
    return _send_list(
        phone,
        header=f"{prefix}Which university or exam?",
        body="Choose one:",
        button_text="Select",
        rows=rows,
    )


def _confirm_card(phone: str, session: dict, subject: str) -> dict:
    title = session.get("note_title", "")
    category = session.get("note_category", "")
    pages = session.get("note_pages", 0)
    cat_label = _CATEGORIES.get(category, category)
    potential_rs = int(pages) * 10 / 100
    msg = (
        "*Review your submission:*\n\n"
        f"Title: {title}\n"
        f"University: {cat_label}\n"
        f"Subject: {subject}\n"
        f"Pages: {pages}\n"
        f"Potential credit per copy printed: *₹{potential_rs:.2f}*\n\n"
        "Reply *Yes* to submit or *No* to cancel."
    )
    return _send_buttons(phone, msg, [("yes_submit", "Yes, Submit"), ("no_cancel", "Cancel")])


# ── WA message builders (mirror book_bot.py pattern) ─────────────────────────

def _send_text(phone: str, body: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": body},
    }


def _send_buttons(phone: str, body: str, buttons: list[tuple[str, str]]) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": label}}
                    for bid, label in buttons
                ]
            },
        },
    }


def _send_list(
    phone: str,
    header: str,
    body: str,
    button_text: str,
    rows: list[dict],
) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": [{"title": "Options", "rows": rows}],
            },
        },
    }


# ── utilities ─────────────────────────────────────────────────────────────────

def _gen_note_code() -> str:
    """Generate NOTE-YYYYMMDD-XXXX (random uppercase alphanumeric suffix)."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"NOTE-{date_part}-{suffix}"


def _count_pdf_pages(content: bytes) -> int:
    """Return page count of a PDF. Returns 0 on error."""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return len(pdf.pages)
    except Exception as exc:
        logger.error("_count_pdf_pages error: %s", exc)
        return 0


def _match_category(text: str) -> str | None:
    """Match user reply to a valid category key. Accepts key, label, or substring."""
    lower = text.strip().lower()
    if not lower:
        return None
    # exact match on key or id from interactive list reply
    if lower in _CATEGORIES:
        return lower
    # label match
    for k, v in _CATEGORIES.items():
        if lower == v.lower():
            return k
    # partial match (user types "kerala" or "cusat") — require at least 3 chars
    if len(lower) >= 3:
        for k, v in _CATEGORIES.items():
            if lower in k or lower in v.lower():
                return k
    return None
