# Store Setup Checklist
## Printosky — Oxygen Students Paradise, Thriprayar

Tasks that require physical access to the store PC or the store phone.
Check each item off as it's completed.

---

## A. WhatsApp Business Coexistence
> Lets staff read and reply to customer WhatsApp messages on the store phone
> while the Cloud API bot handles automation.

- [ ] **Install WhatsApp Business App** on the store phone (SIM: 9495706405)
  - Download from Play Store: "WhatsApp Business"
  - Sign in with the 9495706405 number
  - When prompted about existing WhatsApp — choose "Use as WhatsApp Business"
  - This enables coexistence: phone + Cloud API both receive messages simultaneously
- [ ] **Verify coexistence is active** — send a test message to 9495706405 from another phone
  - Message should appear both on the store phone AND trigger the Vercel webhook
  - If only one side receives it, check Meta Business Manager → WhatsApp → Settings → Coexistence

---

## B. Security Fixes (CRITICAL — do before going fully live)

### B1. META_APP_SECRET — CRITICAL (any internet user can forge webhooks without this)
- [ ] Go to: https://developers.facebook.com → Apps → OSP (App ID: 922193850568204)
  - Settings → Basic → App Secret → click "Show" → copy the value
- [ ] Open Vercel dashboard: https://vercel.com → printosky project → Settings → Environment Variables
  - Add variable: `META_APP_SECRET` = (the copied secret)
  - Set for: Production, Preview, Development
  - Save
- [ ] Run `vercel --prod` from `C:\PY\printosky` to pick up the new env var
- [ ] In `api/index.py` line 291, change:
  ```python
  if META_APP_SECRET and not _verify_meta_sig(body, sig):
  ```
  to:
  ```python
  if not _verify_meta_sig(body, sig):
  ```
  Then redeploy with `vercel --prod`
- [ ] Verify: send a WhatsApp message → Vercel logs should show NO "signature verification failed"

### B2. Rotate webhook verify token (currently hardcoded default)
- [ ] Generate a new random token (e.g. run: `python -c "import secrets; print(secrets.token_hex(20))"`)
- [ ] In Vercel env vars: update `META_WEBHOOK_VERIFY_TOKEN` to the new token
- [ ] In Meta Business Manager → WhatsApp → Configuration → Webhook → Edit
  - Update "Verify Token" to the new token
- [ ] Redeploy with `vercel --prod` and re-verify the webhook in Meta dashboard

### B3. Epson printer — change default password
- [ ] Open browser on store PC: http://192.168.55.201
  - Log in with current: admin / admin
  - Settings → Security → Change Password → set a strong password
- [ ] Update the new password in `epson_jobs_fetcher.py` (currently hardcoded as `admin`)
  - Better: move to `.env` file and load via `os.environ`

### B4. Rotate passwords in make_arch_pdf.py
- [ ] Check if `Printosky@1234`, `Printosky@MIS2026`, `Printosky@Super2026` are real credentials
- [ ] If yes: rotate them and update `.env` (never store plaintext passwords in source files)

---

## C. Store PC — watcher.py Configuration
> The store PC auto-downloads files from Supabase Storage and feeds them
> to the print queue. This needs to be tested end-to-end.

- [ ] **Confirm `printer_poller.py` is running** and reaching the Konica at 192.168.55.110
  - Run: `python printer_poller.py` — should show ink/toner levels without error
- [ ] **Confirm `epson_jobs_fetcher.py`** can reach http://192.168.55.201
- [ ] **Test Supabase file download** — send a PDF via WhatsApp, confirm it appears
  in `C:\Printosky\Jobs\Incoming\` on the store PC within ~60 seconds
- [ ] **Run database migration** — if `jobs.db` exists, run:
  ```
  python -c "import sqlite3; c=sqlite3.connect('C:/Printosky/Data/jobs.db'); c.execute('PRAGMA table_info(jobs)'); print([r[1] for r in c.fetchall()])"
  ```
  and confirm `file_url` column exists (added in SCHEMA_v3)

---

## D. CloudFlare Tunnel (Razorpay webhook delivery to store PC)
- [ ] Confirm tunnel is running: open `http://localhost:3002/health` from store PC — should return OK
- [ ] Confirm public URL is still pointing to store PC:
  - Razorpay dashboard → Settings → Webhooks → check the URL ends in `.trycloudflare.com` or your custom tunnel domain
- [ ] If tunnel URL changed: update Razorpay webhook URL in dashboard

---

## E. Admin Dashboard
- [ ] Open https://printosky.vercel.app (or Netlify URL) → confirm "Conversations" tab appears
- [ ] Click Conversations → should load inbox (will be empty until first message after SCHEMA_v11 is applied)
- [ ] Send a test WhatsApp message → refresh Conversations tab → message should appear

---

## Notes
- Deploy command (run from `C:\PY\printosky`): `vercel --prod`
- Supabase project: mlhuwlnwwwxdnqafelko
- Meta App ID: 922193850568204
- Store PC server: http://localhost:3005 (print_server.py)
- Webhook receiver: http://localhost:3002 (webhook_receiver.py)
