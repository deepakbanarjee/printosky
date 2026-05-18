"""Vercel serverless function exposing /api/inngest for Inngest sync + execution.

This file is a SEPARATE Vercel function from api/index.py. The split is
deliberate:
  - api/index.py: raw http.server BaseHTTPRequestHandler for all the
    existing REST endpoints (legacy, well-tested, do not refactor)
  - api/inngest.py: Flask app required by inngest.flask.serve adapter

Both functions can import format_fix_v3/* without conflict; Vercel
bundles each function independently.

Deploy 2A (this commit): plumbing only. Defines the Inngest client,
registers a single 'ping' function so the Inngest dashboard can discover
the app, and serves the /api/inngest endpoint.

Deploy 2B (next commit): real engine wiring -- function decodes the
event payload, runs the v3 engine, updates pb_jobs row.
"""
from __future__ import annotations

import os

import flask
import inngest
import inngest.flask


# Client: reads INNGEST_EVENT_KEY + INNGEST_SIGNING_KEY from env vars
# in production. The is_production flag controls whether the SDK talks
# to Inngest Cloud (prod) or the local Dev Server (dev).
inngest_client = inngest.Inngest(
    app_id="printosky",
    is_production=os.getenv("INNGEST_DEV") is None,
)


@inngest_client.create_function(
    fn_id="ping",
    trigger=inngest.TriggerEvent(event="pb/ping"),
)
def ping(ctx: inngest.ContextSync) -> dict:
    """Sanity-check function. Sending a 'pb/ping' event triggers this
    and returns {ok: True}. Use from the Inngest dashboard 'Send event'
    button to verify Vercel <-> Inngest wiring works."""
    return {"ok": True, "echo": ctx.event.data}


# Flask app exposing /api/inngest for sync + execution
app = flask.Flask(__name__)

inngest.flask.serve(
    app,
    inngest_client,
    [ping],
)
