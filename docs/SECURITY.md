# Printosky Security Status
_Last updated: 2026-04-29 — session 10_

---

## Current Posture (what's done)

| Fix | Detail | Commit |
|-----|--------|--------|
| PBKDF2+salt PIN hashing | 260k iterations, 16-byte random salt per user; zero-downtime: NULL salt = legacy SHA-256, auto-upgraded on next login | fa0ed1a |
| Constant-time comparison | `hmac.compare_digest()` in all PIN + admin password checks | fa0ed1a |
| Rate limiting /staff-login | 5 attempts/IP/60s → HTTP 429; in-memory, no external dep | fa0ed1a |
| Path traversal prevention | `os.path.basename()` on all incoming filenames before save | fa0ed1a |
| META_APP_SECRET guard | Empty secret logs ERROR and rejects all webhooks — no silent pass | e524e80 |
| Razorpay academic webhook | Fails closed when secret unset — logs error, returns 400 | e524e80 |
| Job ID race condition | threading.Lock + in-memory counter in `generate_job_id()` | e524e80 |
| Staff auth on academic POST | `_acad_auth_staff` guard on POST /academic/orders | e524e80 |

---

## Open Items

### 🔴 Must resolve before public launch

**~~SEC-OPEN-1: RAZORPAY_ACADEMIC_WEBHOOK_SECRET~~** — RESOLVED 2026-05-01
- Both webhooks now run on Vercel (`api/index.py`) and share the same `RAZORPAY_WEBHOOK_SECRET` env var. No separate `RAZORPAY_ACADEMIC_WEBHOOK_SECRET` needed.
- Print webhook handler (`_process_razorpay_payment`) at `POST /webhook/razorpay` — verifies HMAC with `RAZORPAY_WEBHOOK_SECRET`
- Academic webhook handler (`_handle_acad_razorpay_webhook`) at `POST /academic/razorpay-webhook` — verifies HMAC with the same `RAZORPAY_WEBHOOK_SECRET`
- Both are registered in Razorpay dashboard. Smoke-tested via bad-signature curl: academic returns 401, print returns 400 — both reject correctly.
- **Pending follow-up:** the WhatsApp print-job webhook in Razorpay dashboard still points at `https://pay.printosky.com/webhook/razorpay` (Cloudflare tunnel that's currently down — 502). Repoint to `https://printosky.vercel.app/webhook/razorpay` (same handler, same secret) — see `vault/infrastructure.md` Cloudflare cleanup section.

**SEC-OPEN-2: ADMIN_PASSWORD_HASH in Vercel**
- `api/index.py` reads this for `/admin/reset-pin` and `/admin/send`
- Verify it's set in Vercel dashboard (`https://vercel.com/deepakbanarjee/printosky/settings/environment-variables`)
- Generate value: `python -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"`

**SEC-OPEN-3: Supabase pin_salt migration**
- Run once in Supabase SQL Editor: `ALTER TABLE staff ADD COLUMN IF NOT EXISTS pin_salt TEXT;`
- Without this, cloud staff PIN changes will fail silently

### 🟠 High priority

**SEC-OPEN-4: Epson web panel default password**
- Epson at `192.168.55.202` reachable on store LAN with default `admin/admin`
- Fix: change via Epson web panel UI. 10 minutes.

**SEC-OPEN-5: STORE_TOKEN in localStorage**
- `website/admin.html` stores auth token in localStorage — XSS-accessible
- No Content-Security-Policy on Netlify
- Fix: move to httpOnly cookie + add CSP in `netlify.toml`

### 🟡 Medium priority

**SEC-OPEN-6: Supabase service_role key on store PC**
- `supabase_sync.py` uses service_role key (bypasses all RLS)
- If `.env` leaks → full DB admin access
- Fix: create scoped Supabase role with INSERT+UPDATE on specific tables only

**SEC-OPEN-7: Sequential academic project IDs are guessable**
- `PROJ-2026-001`, `PROJ-2026-002` ... combined with phone-based student auth = enumeration risk
- Fix: add random suffix or use UUIDs for student-facing IDs

---

## Already Fixed (reference)
- Staff credentials out of source code ✅
- Supabase RLS tightened ✅
- Timing attacks: `hmac.compare_digest()` everywhere ✅
- Empty META_APP_SECRET: logs error and rejects ✅
- Unauthenticated academic order creation: staff auth guard added ✅
- Razorpay academic webhook fails open: now fails closed ✅
