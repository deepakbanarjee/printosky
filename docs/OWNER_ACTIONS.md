# Owner Actions — "Your Turn" Checklist

Things only **you** can do (dashboard logins, DNS, hardware, GitHub/Vercel
secrets). Everything in the code backlog is done; these are the external steps.

> **Secrets:** where a step needs a secret, it's referenced by its `.env` key
> name (e.g. `RAZORPAY_WEBHOOK_SECRET`) — copy the value from `C:\printosky_watcher\.env`.
> Never paste secret values into this file or any commit.

Last updated: 2026-08-16

---

## 🔴 Do now — Supabase quota, hard deadline 2026-09-11

The org exceeded its free-tier quota in the previous billing cycle and
**projects will be restricted from 11 Sep 2026** if it stays over. Readings on
2026-08-16, four days into the 12 Aug – 12 Sep cycle: egress **2.903 / 5 GB**,
storage **0.722 / 1 GB**.

- [ ] **Restart the store-PC workers** — the poll-interval cuts shipped in #68
  (store_puller 15s→45s, transcription 10s→60s, academic 30s→90s) do nothing
  until the PC pulls and restarts. It has been running pre-08-12 code.
  → [STORE_SETUP_CHECKLIST.md](../STORE_SETUP_CHECKLIST.md) §A
- [ ] **Run the storage sweep** — `python tools/storage_cleanup.py` (dry run),
  then `--apply`. Writes a CSV manifest before deleting; never touches a file a
  job row references.
- [ ] **Decide on the paid plan** if the projection still exceeds 5 GB after
  both. Pro is $25/mo and lifts egress to 250 GB.

---

## 🔴 Also do now (1 minute each)

- [ ] **Revoke the temporary Vercel token**
  - https://vercel.com/account/tokens → delete the token named `printosky-env-fix`
  - It was created to scope preview env vars and appeared in a session; the scope
    change is permanent, so revoking does not undo anything.

- [ ] **Remove `VERCEL_DB`/`SUPABASE_DB_URL` line from `.env`** (if you added one)
  - Only if you pasted it during the schema task. Not required by anything at rest.

---

## 🟠 Razorpay go-live chain (unblocks online payments + reconciliation)

These are sequential — do them in order.

- [ ] **TASK-001 — Repoint the Razorpay webhook** *(~2 min)*
  - https://dashboard.razorpay.com → **Settings → Webhooks**
  - **URL:** `https://printosky.vercel.app/webhook/razorpay`
  - **Secret:** the value of `RAZORPAY_WEBHOOK_SECRET` in `.env`
  - **Events:** `payment.captured`, `payment.failed`, `order.paid`
  - *Why it matters:* **0 Razorpay webhooks have ever been processed** — until this
    is pointed at the live endpoint, no online payment is recorded.
  - *Verify:* after saving, Razorpay sends a test ping → run `python scripts/smoke.py`
    or check `processed_webhooks` for a `razorpay_*` row.

- [ ] **TASK-010 — Delete the `pay.printosky.com` DNS record** *(~2 min, after TASK-001)*
  - Cloudflare DNS → remove the `pay` subdomain / tunnel route.
  - Obsolete once Razorpay calls Vercel directly.

- [ ] **TASK-002 — Email Razorpay to enable Route** *(~10 min)*
  - dashboard → Support → Raise Ticket (or `support@razorpay.com`)
  - **Subject:** `Activate Razorpay Route on <RAZORPAY_KEY_ID value>`
  - **Body:** marketplace for printing services; need split payments between the
    platform and partner stores per transaction; KYC available on request.
  - *Only needed if/when you pursue the multi-store marketplace.* Approval takes
    5–15 business days, so start the clock early if expansion is near.

---

## 🟡 Meta WhatsApp

- [ ] **TASK-003 — Submit pickup templates** *(~30 min)*
  - Meta Business Manager → WhatsApp Manager → Message Templates → Create
  - Submit `pickup_ready_v1` and `pickup_completed_v1` — **category UTILITY**
    (not Marketing, or it gets rejected). Body text is in
    [`docs/whatsapp-templates.md`](whatsapp-templates.md).
  - Until approved, pickup notifications only fire inside the 24-hour window.

---

## 🟢 Activate the monitoring we built (optional but recommended)

- [ ] **Add GitHub secret `SUPABASE_DB_URL`** → turns on the live **schema-drift** CI check
  - GitHub repo → Settings → Secrets and variables → Actions → New secret
  - Value: Supabase → Settings → Database → Connection string (URI / session pooler)
  - Until added, the drift workflow skips cleanly; the manifest is still unit-tested.

- [ ] **Add GitHub secret `UPTIME_NOTIFY_SECRET`** → turns on **WhatsApp alert** when the smoke test fails
  - Same value as the `UPTIME_NOTIFY_SECRET` Vercel env var.
  - Without it, a failed smoke run still emails you via GitHub.

- [ ] **UptimeRobot (free) — 3 monitors, every 5 min** *(TASK-008)*
  - `https://printosky.vercel.app/api/health`
  - `https://printosky.com/`
  - `https://printosky.com/admin`

- [ ] **Vercel dashboard notifications** *(TASK-008)*
  - Project → Settings → Notifications → enable "Deployment Failed" + "Error rate".

---

## 🔧 Hardware

- [ ] **TASK-004 — Verify & replace Epson cartridges** *(on-site)*
  - Check levels (ink alerts fire via `printer_poller.py`), replace as needed.
  - Note: ink alerts have been silent since 2026-08-12 because `watcher.py` —
    which starts the poller thread — has been down. Don't read "no alerts" as
    "levels fine" until the workers are back up.

The full on-site list is [STORE_SETUP_CHECKLIST.md](../STORE_SETUP_CHECKLIST.md).

---

## ✅ Already done (no action needed) — for reference

- Vercel preview env scoping (TASK-007), webhook RLS lock (TASK-024),
  `/health` routing + daily-activity cron (TASK-008), env pre-flight check
  (TASK-015), schema-drift checker + refreshed manifest (TASK-016), production
  smoke test + 6-hourly cron (TASK-017).
- Bug fixes: image-upload 500, mobile chat blank, OTP delivery, `/auth /notes
  /account` routing, lambda bundling, iOS form-zoom.

## ⏸️ Deferred (do not build yet)

- **TASK-020 — Razorpay→bank reconciliation cron.** Premature: 0 Razorpay
  payments recorded, no payment IDs stored, cloud DB holds ~₹7 of revenue (real
  revenue is cash in the store-PC SQLite). Revisit **after** TASK-001 is live and
  Razorpay is an actual, ID-tracked channel.
