"""Vercel Python serverless entrypoint for the pdf-editor backend.

Vercel auto-detects an ASGI `app` variable in `api/*.py` and serves it
through its Python runtime. We re-export the FastAPI app from
`backend/main.py` after putting that directory on sys.path.

Environment variables expected in Vercel:
  - PDF_STORAGE_BACKEND=supabase
  - SUPABASE_URL=<project url>
  - SUPABASE_SERVICE_KEY=<service role key>  (preferred)
      or SUPABASE_KEY=<anon key>
  - PDF_STORAGE_BUCKET=pdf-editor            (optional, default shown)
  - PDF_CORS_ALLOW_ORIGINS=https://pdf.printosky.com,https://printosky.com
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from main import app  # noqa: E402,F401
