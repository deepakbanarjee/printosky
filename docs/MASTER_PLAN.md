# Printosky — Master Operational Plan
**Created:** 2026-03-29
**Scope:** Full store workflow standardisation, job tracking, DTP, PDF intelligence, customer experience, staff training

---

## What We Have Now (Sprints 1–7)

| Component | Status |
|-----------|--------|
| WhatsApp file capture + 6-step bot flow | ✅ Done |
| Rate calculation engine (rate_card.py) | ✅ Done |
| Razorpay payment links + webhooks | ✅ Done |
| Print server — Konica (B&W) + Epson (Colour) | ✅ Done |
| Admin panel — job list, print panel, staff login | ✅ Done |
| Staff PIN auth + session tracking | ✅ Done |
| B2B client management | ✅ Done |
| Supabase cloud sync | ✅ Done |
| Konica job log CSV import | ✅ Done |
| Floating print panel with saved specs | ✅ Done |
| Outsourced vendor workflow (project/record/lam) | ✅ Done |

---

## The Complete Customer Journey (Target State)

```
ONLINE                                    WALK-IN
  │                                          │
  ▼                                          ▼
File received via WhatsApp           Staff copies file to hot folder
  │                                  (USB / Google Drive / email)
  ▼                                          │
Bot asks 6 questions                 Staff creates walk-in job
(size, colour, layout,               in admin panel — same questions
 copies, finishing, delivery)               │
  │                                          │
  ▼                                          ▼
PDF colour page detection (auto)    PDF colour page detection (auto)
Staff shown: "Pages 1,5,12 are      Staff confirms/adjusts with customer
colour — confirm with customer"              │
  │                                          │
  ▼                                          ▼
Quote generated → Payment link      Quote shown → Cash/UPI at counter
  │                                          │
  ▼                                          ▼
                    JOB ENTERS TRACKING PIPELINE
                              │
              ┌───────────────┴───────────────┐
              │                               │
         Print-only                    Print + Binding
              │                               │
              ▼                               ▼
     Check: Mixed pages?           Check: Mixed pages?
     ┌────────┴────────┐           ┌────────┴────────┐
    No               Yes          No               Yes
     │                │            │                │
     ▼                ▼            ▼                ▼
  Single job     Split job:    Single job     Split job:
  → Konica or    Option A:          → Konica or    Option A:
    Epson        BW→Konica            Epson        BW→Konica
                 Col→Epson                         Col→Epson
                 Option B:                         Option B:
                 All→Epson                         All→Epson
                 (staff selects)                   (staff selects)
              │                               │
              ▼                               ▼
        Printing done            COVER ALWAYS CREATED (non-spiral/wiro)
              │                  Staff selects cover pages from doc
              │                  OR creates new cover in DTP editor
              │                  Cover printed → saved to Covers/DATE/
              │                               │
              │                         Pages + Cover ready
              │                               │
              ▼                               ▼
        Notify staff               Binding type?
              │               ┌──────────────┴──────────────┐
              │          In-house                       Outsourced
              │    (staple/spiral/wiro/              (project/record/
              │     thermal/lam_sheet)                lam_roll/lam_cover)
              │               │                            │
              │               ▼                            ▼
              │        Binding done               Staff dispatches pages
              │        (staff clicks)             + cover to vendor
              │               │                  [Vendor run — daily]
              │               │                  Job tracked at vendor
              │               │                  Staff collects from vendor
              │               │                            │
              │               └──────────────┬─────────────┘
              │                              │
              │               Cover lamination needed?
              │               ┌─────────────┴─────────────┐
              │              No                           Yes
              │               │                 Sent to lam vendor
              │               │                 Separate cost (quoted)
              │               │                 Collected from vendor
              │               │                           │
              └───────────────┴───────────────────────────┘
                              │
                   Multi-component? Ask customer:
                   "Collect together or separately?"
                   Together → wait for all    Separately → release each
                              │                when ready
                        Job → READY
                              │
                    WhatsApp notification sent
                    "Your job OSP-XXXX is ready for collection!"
                              │
                    Customer collects → Staff clicks "Collected"
                              │
                    [B2B client: no review]
                    [No phone: no review]
                    30 min later → Bot sends review request
                    ⭐ 1-5 star
                    4-5 ⭐ → Google Maps link + 10% discount code
                    1-3 ⭐ → "What went wrong?" → staff notified
```

---

## Additional Notes from Owner (2026-03-29)

### Walk-in File Source Tagging
Every walk-in job must record WHERE the file came from:
- `USB` — customer brought on USB drive
- `Email` — customer emailed the file
- `Google Drive` — customer shared a Drive link
- `WhatsApp` — sent on WhatsApp (already tagged)
- `Hot folder` — staff copied manually
This is stored on the job record and visible in the job list / reports.

### Photocopying Service (New Service Type)
The store does photocopying of physical documents (not printing from PDF).
- **Tracking goal:** know how many copies were made, on which machine, by which staff
- **Primary method:** Pull data from Konica or Epson machine (SNMP/web admin — already being explored in printer_poller.py)
- **Fallback:** If machine data not available, staff enters manually: number of sheets copied, B&W or colour
- **Billing:** Same rate as print (per sheet)
- **Service type tag:** `copy` (distinct from `print`)

### Mixed Print UI — Page Thumbnail Selector
Replace text-based colour page input with a visual thumbnail panel:
- Admin panel renders a thumbnail strip of all PDF pages
- Pages auto-detected as colour are highlighted with an **orange/amber border**
- Pages detected as B&W shown with a **grey border**
- Staff can click any thumbnail to toggle: colour ↔ B&W (override detection)
- Selected state clearly visible — no ambiguity
- Locked after staff confirms → sub-jobs created

### Print Layout — Visual Selector (not dropdowns)
The layout selector in the print panel must show **visual diagrams** of how the document will print:

| Option | Visual shown |
|--------|-------------|
| Single side | Single rectangle per page, all facing same direction |
| Double side | Pages shown in pairs — front face + back face coupled |
| 2-up | Two small page thumbnails side-by-side on one sheet |
| 4-up | Four thumbnails in a 2×2 grid on one sheet |
| Booklet | Pages shown folded, booklet-style |

Staff or customer can look at the visual and immediately understand what they're selecting. No guessing from text labels.

### Collation Correction — Epson WFC 21000 Only
Pages come out in correct sequence ONLY when printed on **Epson WFC 21000**.
- **Epson WFC 21000** (colour printer) → sequence preserved ✓
- **Konica** (B&W) → sequence depends on Konica model; assume manual collation needed
- For **mixed jobs** (split between Konica + Epson):
  - Colour pages → Epson → in sequence ✓
  - B&W pages → Konica → may need sorting
  - Admin panel must show a **collation warning** when a mixed job is split to both printers:
    *"⚠️ B&W pages will print on Konica — staff must collate with colour pages from Epson"*

### Cover Page — Full Revised Spec

#### Source priority
1. **Staff selects from the document** — for any binding type, staff can pick any page(s) from the uploaded PDF as the cover. No extra charge. No extra details needed.
2. **Staff creates new cover** — only if no suitable cover exists in the file. Charged at Rs.100 (front+back) or Rs.150 (front+back+spine).

#### What staff selects
- **Front cover** — always (default, required for all binding types)
- **Back cover** — optional
- **Spine** — optional (only relevant for project / record / thick soft binding)

#### Cover sheet size options
Staff selects ONE of three standard sizes for the cover sheet:
| Option | Size |
|--------|------|
| A | 13" × 19" |
| B | 12" × 19" |
| C | 12" × 18" |
- Only one size active at a time
- Each size has manual adjustment fields (margins, bleed, offset)
- Settings can be **saved as named presets** and reloaded for future jobs
- Example preset: "Project Binding Standard" → 13×19, margin 5mm

#### PDFSnake Replica for Cover Sheet Creation
Cover sheet layout and assembly will use a **PDFSnake-inspired module** (`cover_composer.py`):
- Page extraction from source PDF (pick front/back/spine pages)
- Imposition onto the selected cover sheet size (13×19 / 12×19 / 12×18)
- Margin, bleed, offset controls (saved as presets)
- Spine width auto-calculated from page count × paper thickness
- Output: press-ready cover PDF → saved to `Covers/DATE/` folder
- Sent as a separate print job (not merged with document)

### Cover output
- Auto-generated or selected cover PDF is saved to: `C:\Printosky\Jobs\Covers\YYYY-MM-DD\{job_id}_cover.pdf`
- Cover is a **separate print job** — not merged with the main document
- Admin panel shows a **"Print Cover"** button distinct from "Print Document"
- If the cover needs **lamination after printing**: staff marks "Laminate cover" checkbox on the job
  - This creates a lamination step in the job workflow
  - Flagged separately from document lamination

---

## Permutation Decisions (2026-03-29 — all scenarios locked)

### Printer Routing Rules (Final)
| Scenario | Decision |
|----------|----------|
| All B&W pages | → Konica only |
| All Colour pages | → Epson only |
| Mixed pages, split print | → Option A: B&W to Konica, Colour to Epson |
| Mixed pages, all colour pricing OK | → Option B: All to Epson (staff/customer choice) |
| Staff selects Option A or B | Shown as two visual buttons before printing |
| Option A selected | Paper match warning + collation warning shown |
| Option B selected | No warning — single printer, in sequence |

### Cover Rules (Final)
| Binding type | Cover required? | Notes |
|---|---|---|
| None / Staple | ❌ No | No cover needed |
| Spiral | ❌ No | Clear plastic front optional, not tracked |
| Wiro | ❌ No | Same as spiral |
| Soft binding | ✅ Always | Front + back. Plain back sheet supplied by shop |
| Project binding | ✅ Always | Front + optional back + optional spine |
| Record binding | ✅ Always | A3 folded cover |
| Thermal binding | ✅ Always | Front cover sheet |
| Any outsourced binding | Cover printed HERE, sent with pages to vendor |

**Cover always printed at shop — never by vendor.**

### Cover Lamination (Final)
- Document sheet lamination (up to A3): **in-house**, Rs.60/sheet
- Cover lamination: **outsourced to vendor**, separate cost not in price list — staff gets quote from vendor and enters manually on the job

### DTP Rules (Final)
| Service | Includes | Billing |
|---------|---------|---------|
| DTP (regular files) | Draft + fair copy | Per page (Rs.40/50/60) |
| DTP (CV) | Draft on request + final | Per page — same rate |
| Editing | Modify existing document | Per hour Rs.250 pro-rata |
| Graph | Create chart/graph | Per graph — rate TBD |
| All DTP/editing | Price same with or without print | Print billed separately if required |

**Graph tool:** Currently Excel. Moving to in-app tool (to be built). Rate per graph to be set by owner later — system will have the field ready.

**Digital delivery** (no print): file delivered via WhatsApp or email. No extra charge beyond DTP rate.

### Multi-component Collection (Final)
When components have different completion times (e.g. component A in-house done, component B at vendor):
- System asks customer (via WhatsApp or staff at counter): *"Would you like to collect each part as it's ready, or wait and collect everything together?"*
- **Together**: job stays in `Waiting` until all components done → single `Ready` notification
- **Separately**: each component gets its own `Ready` notification when done → customer collects in multiple trips
- Stored as `collection_preference` on the job: `'together'` | `'separately'`

### Vendor Logistics (Final)
- Items always delivered and collected by **our staff**
- **Daily schedule**: one regular run per day (drop off + pick up)
- **Urgent**: scheduled immediately — separate run same day
- System shows: *Today's vendor run list* — all jobs to drop/collect, grouped by vendor
- Admin panel has a **"Vendor Run"** view: checkable list, staff marks each item dropped/collected

### B2B Portal (Final)
- B2B clients get a **self-service web portal** (separate from staff admin panel)
- They can: upload files, select all print options (same choices as bot flow), submit job, view job status
- No WhatsApp review request for B2B clients
- B2B jobs billed to their account — no Razorpay needed

### Discount Code Redemption (Final)
| Channel | How applied |
|---------|------------|
| WhatsApp (online) | Bot detects same phone → auto-applies on next order |
| Walk-in | Admin panel has "Redeem Code" field on job creation — staff enters code |
| Discount value | 10% off — surcharge structure TBD (field built, value added later) |

### Urgent Jobs (Final)
- Surcharge value: TBD by owner (field built, value added later)
- Urgent flag: set by customer in bot flow OR by staff on any job
- Visual: urgent jobs shown with red badge at top of admin job list
- Vendor run: urgent outsourced jobs trigger immediate separate vendor run

---

## Clarified Business Rules (from owner Q&A)

### Cover Page Charging
| Scenario | Charge |
|----------|--------|
| Customer's file includes cover pages | Included in binding — no extra charge |
| Basic cover created by staff (front only) | Rs.40–60 DTP rate (1 page) |
| Front + back cover created | Rs.100 flat |
| Front + back + spine created | Rs.150 flat |
- Covers are created file-by-file — nothing is pre-printed
- Cover details collected AFTER payment
- If customer's file has a cover page, system uses it — staff extracts it from the PDF

### DTP Rates (confirmed)
| Language | Rate per page |
|----------|--------------|
| English | Rs.40 |
| Malayalam | Rs.50 |
| Hindi | Rs.60 |

### Editing Rate
- **Rs.250 per hour** (pro-rata — billed to the minute, shown as fraction of hour)

### Payment — Multi-component Jobs
- 1 job with N components (e.g. 3 files, each spiral bound)
- Each component has individual spec + binding rate (pages differ → rates differ)
- **Single combined payment link** for the whole job
- Calculation shown per component in breakdown, total at bottom

### Vendor Management
- Fixed vendors for standard binding (stored in system with name + WhatsApp + typical turnaround)
- Ad-hoc vendors for special jobs
- System tracks: dispatched to whom, when, expected return, actual return
- Vendor scheduling: staff can see all jobs currently at vendor and expected collection dates

### Walk-in Customers Without Phone
- Phone is optional
- No phone → cash/UPI only, no WhatsApp features activated
- Staff **can manually trigger** WhatsApp features (receipt, notification, review) if customer provides number later
- Job still fully tracked in system — just no WhatsApp automation

### Receipt
- WhatsApp receipt only (no receipt printer yet — hardware pending)
- Receipt sent after payment confirmed or after cash/UPI marked by staff

### Review Discount
- 4–5 stars → 10% off next job
- No expiry on the discount code
- Code auto-applied when same customer's phone places next order

---

## Phase 1 — PDF Colour Intelligence
**Goal:** Auto-detect colour pages in any uploaded PDF
**Complexity:** Medium

### What it does
- On every file upload (WhatsApp or USB drop), scan PDF using PyMuPDF
- Identify which pages contain colour (non-grayscale) content
- Store as `colour_page_map` JSON on the job: `{"colour_pages": [1, 5, 12], "total": 40}`
- Admin panel shows: *"⚠️ Colour detected on pages 1, 5, 12 — confirm with customer"*
- Staff can override (mark all B&W, or mark additional pages)

### How colour detection works
```python
# Using PyMuPDF (fitz)
import fitz

def detect_colour_pages(pdf_path):
    doc = fitz.open(pdf_path)
    colour_pages = []
    for page_num, page in enumerate(doc, 1):
        # Check drawings, images, text for non-grayscale colorspace
        colour_found = any(
            item for item in page.get_drawings()
            if item["color"] and not _is_gray(item["color"])
        )
        if not colour_found:
            # Check images on page
            for img in page.get_images():
                cs = doc.extract_image(img[0])["colorspace"]
                if cs > 1:  # >1 component = colour
                    colour_found = True
                    break
        if colour_found:
            colour_pages.append(page_num)
    return colour_pages
```

### Mixed job splitting
When a job has both B&W and colour pages:
1. System creates 2 **sub-jobs** under the parent job
2. `sub_job_bw`: pages not in colour_pages → sent to Konica
3. `sub_job_col`: pages in colour_pages → sent to Epson
4. Print order preserved — pages come out in sequence naturally
5. Parent job status = `Printing` until BOTH sub-jobs complete
6. No manual collating needed — Epson prints colour pages, Konica prints B&W, staff lays them in order

### DB changes
```sql
ALTER TABLE jobs ADD COLUMN colour_page_map TEXT;   -- JSON: {"colour": [1,5,12], "bw": [2,3,4,...]}
ALTER TABLE jobs ADD COLUMN colour_confirmed INTEGER DEFAULT 0;  -- staff confirmed the split
ALTER TABLE jobs ADD COLUMN parent_job_id TEXT;     -- for sub-jobs
ALTER TABLE jobs ADD COLUMN is_sub_job INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN sub_job_type TEXT;      -- 'bw' | 'col'
ALTER TABLE jobs ADD COLUMN collation_warning INTEGER DEFAULT 0; -- mixed split to 2 printers
ALTER TABLE jobs ADD COLUMN file_source TEXT;       -- 'whatsapp'|'usb'|'email'|'gdrive'|'hotfolder'
```

---

## Phase 2 — Cover Page Auto-Generation
**Goal:** Auto-generate correct cover pages for each binding type
**Complexity:** Medium
**Library:** reportlab (already installed for B2B invoices)

### Cover types by binding

| Binding | Cover source | What's generated if no cover in file |
|---------|-------------|--------------------------------------|
| Spiral / Wiro | From customer file or none | Nothing — just the print pages |
| Soft binding | From customer file | Front + back — Rs.100 |
| Project binding | From customer file | Front + spine + back — Rs.150 |
| Record binding (A3) | From customer file | A3 folded front/back — Rs.100 |
| Thermal | From customer file | Front cover page — DTP rate |
| Lamination on cover | Flag on existing cover | No new page — just flagged for lam |

### Cover generation flow
1. Customer pays for job
2. **After payment**: bot/staff asks — "Does your file include a cover page? (Yes / No)"
3. **If Yes**: staff extracts cover from PDF (using pikepdf page extraction)
4. **If No**: staff creates cover in DTP editor → billed at cover rate (Rs.100 / Rs.150)
5. For project binding: staff enters title, name, subject, institution, year, spine text
6. `cover_generator.py` builds PDF from reportlab template
7. Cover PDF merged with print file using pikepdf
8. Final merged PDF sent to printer

### DB changes
```sql
ALTER TABLE jobs ADD COLUMN cover_data TEXT;  -- JSON: title, name, subject etc.
ALTER TABLE jobs ADD COLUMN cover_pdf_path TEXT;
ALTER TABLE jobs ADD COLUMN laminate_cover INTEGER DEFAULT 0;
```

---

## Phase 1b — Visual Print UI (Thumbnail Selector + Layout Diagrams)
**Goal:** Replace all text dropdowns for print spec selection with visual representations
**Complexity:** Medium (frontend only — no backend change)

### PDF Page Thumbnail Strip
- Admin panel renders thumbnails of all pages (using pdf.js in browser, or server-side with PyMuPDF)
- Thumbnail colour coding:
  - **Orange border** = detected as colour
  - **Grey border** = detected as B&W
  - **Blue border** = staff manually overridden
- Click a thumbnail to toggle its colour classification
- "Confirm colour selection" button locks it in

### Layout Visual Selector
Replace the layout dropdown with clickable illustrated cards:

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  [▯] [▯]   │  │  [▯|▯]     │  │  [▯▯]      │  │  [▯▯▯▯]   │
│  [▯] [▯]   │  │  [▯|▯]     │  │  [▯▯]      │  │  [▯▯▯▯]   │
│  Single     │  │  Double     │  │   2-up      │  │    4-up     │
│  Side       │  │  Side       │  │             │  │             │
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
```

- Each card shows a miniature diagram of how pages will be arranged on the sheet
- Selected card is highlighted
- Hovering shows a tooltip: "Each page printed on one side of a sheet"

### Paper Match Warning for Split Jobs
When a job is split between Konica and Epson:
- Admin panel shows: *"⚠️ Ensure both Konica and Epson are loaded with the same paper (size + type) before printing."*
- Staff must acknowledge this before the print buttons are enabled
- This is a staff responsibility — system reminds, does not enforce

### Collation Warning
When a job is split between Konica (B&W) and Epson (Colour), show:
```
⚠️  Mixed job — B&W pages → Konica, Colour pages → Epson
    Epson (WFC 21000) outputs in sequence.
    Konica output will need manual collation with colour pages.
    Confirm before printing.  [Yes, I understand]
```

---

## Phase 1c — Photocopying Service (Copy Tracking)
**Goal:** Track all photocopying done at the store, attributed to staff
**Complexity:** Low-Medium

### Data sources (in priority order)
1. **Konica SNMP / web admin** — already probed in printer_poller.py. Attempt to pull copy counter separately from print counter. If Konica provides per-job copy logs, import them.
2. **Epson web interface** — same approach for colour copies.
3. **Manual staff entry** — fallback. Staff enters at end of each copy job: sheets copied, B&W or colour, customer name (optional).

### Copy job in admin panel
- "New Copy Job" button (separate from print jobs)
- Fields: sheets, colour/BW, copies (number of sets), paper size, customer phone (optional)
- Cost auto-calculated at print rate (same per-sheet rates)
- Payment: cash/UPI at counter
- Job tracked with staff_id and timestamp

### DB changes
```sql
ALTER TABLE jobs ADD COLUMN service_type TEXT DEFAULT 'print';
-- service_type values: 'print' | 'copy' | 'editing' | 'dtp' | 'graph' | 'scanning' | 'mixed'
```

---

## Phase 3 — Walk-in Job Creation
**Goal:** Walk-in customers get the same system tracking as WhatsApp customers
**Complexity:** Low-Medium

### Admin panel changes
- **"+ New Job"** button at top of job list
- Opens modal with:
  - File upload (drag-drop or browse)
  - Customer phone (optional — for WhatsApp notification)
  - Customer name
  - Same spec fields as print panel (size, colour, layout, copies, finishing)
  - Payment method: Cash / UPI (no Razorpay needed)
- On submit: job created in DB, PDF colour detection runs, job appears in list

### USB / hot folder drop
- Staff copies file from USB to `C:\Printosky\Jobs\Incoming\`
- Watcher auto-detects (already working)
- Job appears in admin panel with status `Received`
- Staff opens job, enters specs, marks as paid (cash/UPI)
- Same pipeline as online jobs from here

### Walk-in job ID format
Same: `OSP-YYYYMMDD-XXXX` — no distinction needed

---

## Phase 4 — Full Job Lifecycle Tracking
**Goal:** Every action in the store is timestamped, attributed to a staff member
**Complexity:** Medium

### New status machine

```
Received → Quoted → Paid → Printing → PrintDone → Binding →
Lamination → Ready → Collected
```

For DTP/Editing jobs:
```
Received → InProgress → Review → Ready → Collected
```

### job_events table (new)
```sql
CREATE TABLE job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    staff_id    TEXT,              -- who performed the action
    action      TEXT NOT NULL,     -- e.g. 'status_change', 'print_sent', 'binding_sent'
    from_status TEXT,
    to_status   TEXT,
    notes       TEXT,
    duration_sec INTEGER,          -- time since previous event (calculated)
    created_at  TEXT DEFAULT (datetime('now'))
);
```

### What gets tracked automatically
| Event | Tracked | Staff attributed |
|-------|---------|-----------------|
| File received | ✅ Auto | — |
| Quote sent | ✅ Auto | — |
| Payment received | ✅ Auto | — |
| Print job sent | ✅ Auto | Logged-in staff |
| Binding dispatched | ✅ Staff click | Logged-in staff |
| Binding returned | ✅ Staff click | Logged-in staff |
| Lamination dispatched | ✅ Staff click | Logged-in staff |
| Job marked Ready | ✅ Auto/Staff | Logged-in staff |
| Job Collected | ✅ Staff click | Logged-in staff |
| Review received | ✅ Auto | — |

### Admin panel additions
- Each job row shows **current status** with colour badge
- Clicking a job shows full **event timeline** (who did what, when, how long each stage took)
- **Staff dashboard** (MIS): jobs completed per staff, avg time per stage, per shift

---

## Phase 5 — DTP / Editing / Graph Tracking
**Goal:** Non-print work is billed and tracked accurately
**Complexity:** Medium-High

### Service types

| Service | Billing unit | Rate |
|---------|-------------|------|
| Print | Per sheet | rate_card.py |
| Editing | Per hour (pro-rata) | Rs.250/hr |
| DTP | Per page | Rs.40 (EN), Rs.50 (ML), Rs.60 (HI) |
| Graph | Per graph | Staff quotes |
| Cover creation (front+back) | Flat | Rs.100 |
| Cover creation (front+back+spine) | Flat | Rs.150 |
| Scanning | Per sheet (tiered) | Already in rate_card.py |

### DTP editor in admin panel
**Recommended library: TipTap** (tiptap.dev)
- Open source, MIT license
- Embeds in any web page (vanilla JS or React)
- Full Unicode — Malayalam, Hindi, English render natively using browser fonts
- Add Google Fonts: `Noto Sans Malayalam`, `Noto Sans Devanagari` for correct rendering
- Supports: bold, italic, tables, lists, images, headers
- Start simple (Phase 1 = timer only), grow later (Phase 2 = full layout)

### Timer-based tracking
```
Staff opens DTP job → clicks [▶ Start Work]
Timer shows: 00:14:32 (running)
Staff pauses for break → clicks [⏸ Pause]
Staff resumes → clicks [▶ Resume]
Staff completes → clicks [✓ Done]
System calculates: total_minutes → billing_hours (ceil to 15-min slots)
Staff enters: pages completed / graphs created
Quote auto-calculated, sent to customer or added to invoice
```

### DB changes
```sql
ALTER TABLE jobs ADD COLUMN service_type TEXT DEFAULT 'print';
-- service_type: 'print' | 'editing' | 'dtp' | 'graph' | 'scanning' | 'mixed'

CREATE TABLE work_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    staff_id    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    paused_at   TEXT,
    resumed_at  TEXT,
    ended_at    TEXT,
    total_sec   INTEGER,           -- calculated on end
    notes       TEXT
);

ALTER TABLE jobs ADD COLUMN dtp_pages INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN graph_count INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN editing_minutes INTEGER DEFAULT 0;
```

---

## Phase 6 — Customer Experience Standardisation
**Goal:** Every customer gets the same excellent experience, every time
**Complexity:** Low-Medium

### Online customer journey (WhatsApp) — already mostly done
- [x] File received → auto-acknowledged
- [x] 6-step quote flow
- [x] Payment link sent
- [x] (NEW) Job ready notification: "Your job OSP-XXXX is ready! Come collect at Printosky."
- [x] (NEW) Review request 30 min after collection

### Walk-in customer journey — to build
- Staff follows on-screen checklist (SOP mode in admin panel)
- Checklist per job type ensures nothing is missed

### Review system
**Flow:**
1. Staff marks job as `Collected`
2. System waits 30 minutes
3. Bot sends: *"Thank you for visiting Printosky! How was your experience today? Reply 1–5 ⭐"*
4. Customer replies with a number:
   - **4 or 5** → "Thank you! 🌟 Tap here to leave us a Google review: [link] — You've earned a 10% discount on your next job!"
   - **1, 2 or 3** → "We're sorry to hear that. Could you tell us what went wrong? Your feedback helps us improve."
   - Customer replies with complaint → staff notified on WhatsApp immediately

**Discount system:**
- On 4–5 star review: generate unique discount code (e.g. `THANK-8X3K`)
- Store in `discount_codes` table linked to customer phone
- On next order: bot checks for unused discount → auto-applies

```sql
CREATE TABLE job_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    phone       TEXT,
    rating      INTEGER,           -- 1-5
    feedback    TEXT,              -- for low ratings
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE discount_codes (
    code        TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    pct_off     INTEGER DEFAULT 10,
    source      TEXT DEFAULT 'review',  -- 'review' | 'manual'
    used        INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
```

---

## Phase 7 — Staff Training System
**Goal:** Any new staff member can be trained in 2 hours and work independently
**Complexity:** Low

### Training materials (to generate)
1. **Quick Reference Card** (1 A4 page, laminated at counter)
   - Job status colours
   - What to click for each scenario
   - When to call the manager

2. **SOP per job type** (in-app + printable)
   - Print only
   - Print + binding
   - Print + binding + lamination
   - DTP / Editing
   - Walk-in cash job
   - B2B client job

3. **Training mode in admin panel**
   - Toggle "Training Mode" — creates fake jobs
   - Staff practices full flow without touching real data
   - Trainer can review completed training exercises

### Staff SOP — Print Job (example checklist in admin panel)
```
□ Check file opens correctly (no corruption)
□ Confirm page count with customer
□ Check colour detection results — confirm with customer
□ Select correct paper type and size
□ Set copies and finishing
□ Confirm quote with customer
□ Collect payment (UPI / cash / B2B account)
□ Send to correct printer (Konica = B&W, Epson = Colour)
□ Monitor print — check first page before full run
□ Bind / finish (if required)
□ Mark Ready → customer notified automatically
□ Hand over to customer → Mark Collected
```

---

## Implementation Sequence

| Sprint | Phase | Key deliverables |
|--------|-------|-----------------|
| Sprint 8 | Phase 4 (partial) | job_events table, status machine, timeline view in admin |
| Sprint 8 | Phase 3 | Walk-in job creation modal, USB hot folder |
| Sprint 9 | Phase 1 | PDF colour detection, colour_page_map, mixed job split display |
| Sprint 9 | Phase 2 | Cover page auto-generation (soft + project binding first) |
| Sprint 10 | Phase 5 | Work session timer, DTP job type, TipTap integration |
| Sprint 10 | Phase 6 (partial) | Job ready notification, review flow, discount codes |
| Sprint 11 | Phase 6 (complete) | Full SOP checklists, training mode |
| Sprint 12 | Phase 7 | Staff training materials, quick reference cards |

---

## Database Migration Summary

```sql
-- SCHEMA_v5_migration.sql

-- Phase 1: PDF colour intelligence
ALTER TABLE jobs ADD COLUMN colour_page_map TEXT;
ALTER TABLE jobs ADD COLUMN parent_job_id TEXT;
ALTER TABLE jobs ADD COLUMN is_sub_job INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN sub_job_type TEXT;

-- Phase 2: Cover generation
ALTER TABLE jobs ADD COLUMN cover_source TEXT;          -- 'from_doc' | 'created'
ALTER TABLE jobs ADD COLUMN cover_page_refs TEXT;       -- JSON: {"front":1,"back":42}
ALTER TABLE jobs ADD COLUMN cover_data TEXT;            -- JSON: title, name (only if created)
ALTER TABLE jobs ADD COLUMN cover_pdf_path TEXT;        -- saved to Covers/DATE/job_id_cover.pdf
ALTER TABLE jobs ADD COLUMN cover_sheet_size TEXT;      -- '13x19' | '12x19' | '12x18'
ALTER TABLE jobs ADD COLUMN cover_has_spine INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN cover_has_back INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN laminate_cover INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN cover_charge REAL DEFAULT 0;

CREATE TABLE IF NOT EXISTS cover_size_presets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    size       TEXT NOT NULL,
    margin_mm  REAL DEFAULT 5,
    bleed_mm   REAL DEFAULT 0,
    offset_x   REAL DEFAULT 0,
    offset_y   REAL DEFAULT 0,
    is_default INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Walk-in source + copy service
ALTER TABLE jobs ADD COLUMN file_source TEXT;           -- 'whatsapp'|'usb'|'email'|'gdrive'|'hotfolder'
ALTER TABLE jobs ADD COLUMN collation_warning INTEGER DEFAULT 0;

-- Phase 4: Job lifecycle
CREATE TABLE IF NOT EXISTS job_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    staff_id     TEXT,
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    notes        TEXT,
    duration_sec INTEGER,
    created_at   TEXT DEFAULT (datetime('now'))
);

-- Phase 5: DTP/editing
ALTER TABLE jobs ADD COLUMN service_type TEXT DEFAULT 'print';
ALTER TABLE jobs ADD COLUMN dtp_pages INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN graph_count INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN editing_minutes INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS work_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    staff_id    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    paused_at   TEXT,
    ended_at    TEXT,
    total_sec   INTEGER,
    notes       TEXT
);

-- Phase 6: Reviews + discounts
CREATE TABLE IF NOT EXISTS job_reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    phone      TEXT,
    rating     INTEGER,
    feedback   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS discount_codes (
    code       TEXT PRIMARY KEY,
    phone      TEXT NOT NULL,
    pct_off    INTEGER DEFAULT 10,
    source     TEXT DEFAULT 'review',
    used       INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## Key Technical Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| PDF colour detection | PyMuPDF (fitz) | Fast, no rendering needed, detects colorspace from PDF structure |
| Cover page generation | reportlab | Already installed (B2B invoices), flexible templates |
| PDF merging (cover + content) | pikepdf | Lightweight, fast page manipulation |
| DTP editor | TipTap | Open source, embeds in web, full Unicode, Malayalam/Hindi/English |
| Mixed print collation | Print in page order | Epson + Konica print colour/B&W pages respectively; sequence is preserved |
| Walk-in payment | Cash/UPI recorded in admin | No Razorpay needed for counter sales |
| Review filtering | In-app 1-5 first | Prevents bad reviews going public; captures feedback internally |

---

## Critical Blockers to Resolve First
*(Before any new sprint work begins)*

| # | Action | Where |
|---|--------|-------|
| C1 | Run SCHEMA_v3 migration | Supabase SQL Editor |
| C2 | Fix Oxygen PC server URL → `192.168.55.212:3005` | Reset localStorage on Oxygen PC |

---

*Plan saved: C:\py\printosky\docs\MASTER_PLAN.md*
*Last updated: 2026-03-29 — v3 (all permutations locked, ready for implementation)*
