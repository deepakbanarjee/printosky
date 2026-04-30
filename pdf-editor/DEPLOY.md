# pdf.printosky.com — Deploy Guide

Two independent deploys: **Vercel** (Python serverless API) + **Netlify** (static React frontend). Frontend proxies `/upload`, `/split`, `/save`, `/pdf/*` to Vercel so the browser never sees the backend URL (no CORS).

---

## 1. Supabase — one-time bucket setup

In the existing Supabase project (the same one `db_cloud.py` uses):

1. Storage → Create bucket `pdf-editor`.
2. Visibility: **Private** (bucket requires auth; backend uses the service-role key).
3. (Optional) Lifecycle rule: delete objects older than 24 h. Supabase has no native TTL, so schedule a Supabase Edge Function or Vercel cron that calls `storage.from_('pdf-editor').list()` + `.remove()` for objects older than 1 day.

---

## 2. Vercel — backend

1. Vercel dashboard → **Add New Project** → Import the `printosky` repo.
2. Set **Root Directory** to `pdf-editor`. (Critical — Vercel will only see `pdf-editor/api/index.py`, `pdf-editor/vercel.json`, `pdf-editor/requirements.txt`.)
3. Framework preset: **Other**. Build command / output dir: leave empty.
4. Environment variables (Production + Preview):

```
PDF_STORAGE_BACKEND      = supabase
PDF_STORAGE_BUCKET       = pdf-editor
SUPABASE_URL             = <your supabase project url>
SUPABASE_SERVICE_KEY     = <supabase service_role key>
PDF_CORS_ALLOW_ORIGINS   = https://pdf.printosky.com
```

5. Deploy. Note the assigned URL, e.g. `pdf-editor-abcd.vercel.app` — this becomes `PDF_EDITOR_VERCEL_HOST`.
6. Smoke-test: `curl https://<vercel-url>/` → `{"message":"PDF Editor Backend is running"}`

---

## 3. Netlify — frontend

1. **Add new site** → Import from Git → same repo.
2. **Base directory:** `pdf-editor/frontend`.
3. Build command and publish dir come from `netlify.toml`.
4. Edit `pdf-editor/frontend/netlify.toml` — replace every `PDF_EDITOR_VERCEL_HOST` placeholder with the Vercel host from step 2.5. Commit.
5. Domain → add custom domain `pdf.printosky.com`. Netlify provides DNS instructions (CNAME).
6. Deploy. Netlify runs `npm ci && npm run build` in `frontend/` and publishes `frontend/dist/`.

---

## 4. DNS — pdf.printosky.com

Point `pdf` CNAME at Netlify (per the Netlify domain setup wizard). Netlify handles TLS automatically.

---

## 5. Verify end-to-end

From a browser at `https://pdf.printosky.com`:
1. Upload a <20 MB, <50-page PDF. Expect pages to render.
2. Split → download the split PDF.
3. Save after edits → download the edited PDF.

Check Supabase Storage → `pdf-editor` bucket should contain `{uuid}.pdf` objects.

---

## Constraints (v1)

- **Max upload:** 20 MB, 50 pages (client-enforced in `validateUpload.ts`).
- **Max request time:** 60 s per function (Vercel `maxDuration`). Complex splits at 300 DPI on large PDFs may exceed this — retry with smaller input.
- **No OCR** — `pytesseract` is not in the Vercel runtime. Scan-only PDFs return empty `blocks[]` per page; editor remains functional for vector PDFs.
- **Rate limiting:** not yet implemented. Add Vercel Edge Middleware + Upstash before opening to public traffic.

---

## Rollback

Each Vercel/Netlify deploy is immutable. Use the dashboard "Rollback to previous deploy" if something breaks.
