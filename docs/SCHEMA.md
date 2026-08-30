# Supabase Schema — Printosky + OSP-Academics

**Roadmap reference:** [`roadmap-2026-05.md`](../) TASK-019.
**Project:** `mlhuwlnwwwxdnqafelko` (printosky.com).
**Generated:** 2026-05-12 from live `information_schema`.

This document is the canonical schema reference for the shared Supabase database used by `printosky` (this repo) and `osp-academics` (sibling repo at `C:/PY/osp-academics/`). It lists every table, who owns it, and the columns + types as they exist in production *right now*.

---

## How to use this doc

1. **Before adding a column** anywhere in either repo, find the table below. Confirm the owner. Edit the doc in the same PR as the migration. Drift between this doc and live Supabase should be caught by `scripts/check_schema.py` (TASK-016).
2. **Before reading from a table** in either repo, check the owner column. Don't read columns owned by the other product without coordinating; ownership encodes who has the freedom to break the column.
3. **Convention:** the `printosky` repo owns this file. PRs from `osp-academics` that add columns must include the migration SQL in `printosky/api/migrations/` and an update here.

---

## Quick stats

| | |
|---|---|
| Tables | 34 |
| Views | 2 (`epson_daily`, `konica_daily`) |
| Foreign keys | **1** (`referral_credits.referrer_code → referrers.code`) |
| Tables without RLS | **2** (both 18 Aug incident backups) — see [Security gaps](#security-gaps) |
| Owning products | printosky (30), osp-academics (1), shared (3) |

The schema is heavily denormalized — only one FK exists in the whole database. Cross-table integrity is enforced at the application layer (or not at all). Worth documenting per-table; not worth a mass FK-introduction project.

---

## Owner legend

- 🟦 **printosky** — owned by this repo. Migrations land in `api/migrations/`. Read/write from `db_cloud.py` and `api/index.py`.
- 🟪 **osp-academics** — owned by the sibling repo. Migrations should land there but historically have landed here.
- 🟧 **shared** — both products write. Schema changes require sync across both repos.

---

## Tables

### Print job pipeline 🟦

#### `jobs` 🟦
Single print-job records. Primary key for everything print-related.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `job_id` | text | NO | — | **PK** — `OSP-YYYYMMDD-NNNN` |
| `store_id` | text | NO | `'OSP'` | partition key (multi-store) |
| `received_at` | text | YES | — | ISO-8601 string (legacy from SQLite) |
| `filename` | text | YES | — | original filename |
| `file_extension` | text | YES | — | |
| `file_size_kb` | real | YES | — | |
| `source` | text | YES | — | `whatsapp` / `walk-in` / `b2b` |
| `sender` | text | YES | — | customer phone |
| `status` | text | YES | — | `Pending` / `Paid` / `Printed` / `Delivered` |
| `customer_name` | text | YES | — | |
| `service_type` | text | YES | — | |
| `amount_quoted` | real | YES | — | ₹ |
| `amount_collected` | real | YES | — | ₹ |
| `payment_mode` | text | YES | — | `upi` / `cash` / `razorpay` |
| `completed_at` | text | YES | — | |
| `page_count` | integer | YES | `0` | |
| `filepath` | text | YES | — | local hot-folder path |
| `copies` | integer | YES | `1` | |
| `finishing` | text | YES | — | `staple` / `spiral` / `thermal` / `lam_*` / etc. |
| `invoiced` | boolean | YES | `false` | |
| `invoice_number` | text | YES | — | |
| `notes` | text | YES | — | staff annotations |
| `razorpay_payment_id` | text | YES | — | |
| `printer` | text | YES | — | `Konica` / `Epson` |
| `colour` | text | YES | — | `bw` / `colour` / `mixed` |
| `size` | text | YES | — | `A4` / `A3` / `Legal` / ... |
| `printed_by` | text | YES | — | staff id |
| `file_source` | text | YES | — | |
| `colour_page_map` | text | YES | — | JSON: which pages are colour |
| `colour_confirmed` | integer | YES | `0` | bool-as-int (legacy) |
| `parent_job_id` | text | YES | — | for sub-jobs |
| `is_sub_job` | integer | YES | `0` | bool-as-int |
| `sub_job_type` | text | YES | — | |
| `collation_warning` | integer | YES | `0` | bool-as-int |
| `dtp_pages` | integer | YES | `0` | DTP work |
| `graph_count` | integer | YES | `0` | |
| `editing_minutes` | integer | YES | `0` | |
| `file_url` | text | YES | — | Supabase Storage URL |
| `assigned_store_id` | text | YES | — | **multistore routing** (TASK-006) |
| `pickup_code` | text | YES | — | unique 7-char code (multistore) |
| `pickup_ready_at` | timestamptz | YES | — | |
| `delivered_at` | timestamptz | YES | — | |
| `take_rate_amount` | real | YES | — | platform fee for partner stores |
| `route_transfer_id` | text | YES | — | Razorpay Route transfer id |

#### `job_batches` 🟦
Multi-job WhatsApp orders that share a single payment link.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `batch_id` | text | NO | — | **PK** |
| `phone` | text | YES | — | |
| `job_ids` | text | YES | — | comma-separated `job_id` list |
| `status` | text | YES | `'pending'` | |
| `total_amount` | real | YES | — | ₹ |
| `razorpay_link_id` | text | YES | — | |
| `link_sent_at` | text | YES | — | |

#### `job_events` 🟦
Status-transition audit log per job.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `job_id` | text | NO | — | |
| `staff_id` | text | YES | — | who triggered |
| `action` | text | NO | — | |
| `from_status` | text | YES | — | |
| `to_status` | text | YES | — | |
| `notes` | text | YES | — | |
| `duration_sec` | integer | YES | — | |
| `created_at` | timestamptz | YES | `now()` | |

#### `job_reviews` 🟦
Post-job rating / feedback collected via WhatsApp.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `job_id` | text | NO | — | |
| `phone` | text | YES | — | |
| `rating` | integer | YES | — | 1–5 |
| `feedback` | text | YES | — | |
| `review_sent` | integer | YES | `0` | bool-as-int |
| `created_at` | timestamptz | YES | `now()` | |

#### `konica_jobs` 🟦
Raw Konica printer job log (one row per Konica-side job).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `store_id` | text | NO | `'OSP'` | |
| `job_number` | integer | NO | — | Konica's internal job # |
| `job_type` | text | YES | — | `Print` / `Copy` / `Scan` / `Fax` |
| `user_name` | text | YES | — | Windows username (see KONICA_USER_PC_MAP) |
| `file_name` | text | YES | — | |
| `result` | text | YES | — | `OK` / error |
| `num_pages` | integer | YES | — | requested |
| `pages_printed` | integer | YES | — | actual |
| `mono_pages` | integer | YES | — | |
| `color_pages` | integer | YES | — | (US spelling here, intentional — Konica's XML) |
| `copies` | integer | YES | — | |
| `job_date` | text | YES | — | `YYYY/MM/DD HH:MM:SS` (Konica format) |
| `print_end_date` | text | YES | — | |
| `paper_size` | text | YES | — | |
| `paper_type` | text | YES | — | |
| `attributed_to` | text | YES | — | staff_id; populated by `attribute_konica_jobs()` (currently 0/4507 — see [[feature-graveyard-triage-2026-05]]) |

#### `epson_jobs` 🟦
Raw Epson printer job log. Heavier than Konica (173k rows from delta-polling).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `store_id` | text | NO | `'OSP'` | |
| `source` | text | NO | `'delta'` | `delta` (SNMP) / `webui` |
| `job_number` | text | YES | — | |
| `job_type` | text | YES | — | |
| `user_name` | text | YES | — | |
| `file_name` | text | YES | — | |
| `result` | text | YES | — | |
| `pages_printed` | integer | YES | — | |
| `mono_pages` | integer | YES | — | |
| `color_pages` | integer | YES | — | |
| `copies` | integer | YES | — | |
| `paper_size` | text | YES | — | |
| `job_date` | text | YES | — | |
| `print_end_date` | text | YES | — | |
| `snmp_total_before` | bigint | YES | — | total counter before this job |
| `snmp_total_after` | bigint | YES | — | total counter after |
| `delta_pages` | integer | YES | — | derived (after − before) |
| `attributed_job_id` | text | YES | — | link to `jobs.job_id` if matched |
| `imported_at` | text | YES | — | |

#### `daily_summary` 🟦
Per-day, per-store roll-up. Written by an end-of-day cron.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `store_id` | text | NO | `'OSP'` | **PK part 1** |
| `date` | text | NO | — | **PK part 2** — `YYYY-MM-DD` |
| `total_jobs` | integer | YES | — | |
| `completed` | integer | YES | — | |
| `pending` | integer | YES | — | |
| `revenue` | real | YES | — | ₹ |
| `cash` | real | YES | — | ₹ |
| `upi` | real | YES | — | ₹ |
| `synced_at` | text | YES | — | |

#### `routing_decisions` 🟦
Multistore routing engine decisions (one per dispatched job).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `job_id` | text | NO | — | |
| `decided_at` | timestamptz | NO | `now()` | |
| `eligible_stores` | jsonb | NO | — | list of `store_id` |
| `scores_json` | jsonb | NO | — | per-store score breakdown |
| `chosen_store_id` | text | NO | — | |
| `reason` | text | YES | — | human-readable |
| `reroute_count` | integer | NO | `0` | |
| `notes` | text | YES | — | |

#### `partners` 🟦
Multistore partner-store registry.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `store_id` | text | NO | — | **PK** |
| `name` | text | NO | — | |
| `type` | text | YES | `'Spoke'` | `Hub` / `Spoke` |
| `contact` | text | YES | — | |
| `phone` | text | YES | — | |
| `location` | text | YES | — | |
| `territory` | text | YES | — | |
| `equipment` | text | YES | — | |
| `commission` | real | YES | `0` | % |
| `status` | text | YES | `'Active'` | |
| `notes` | text | YES | — | |
| `joined_at` | text | YES | — | |
| `kyc_status` | text | YES | `'pending'` | |
| `capabilities_json` | jsonb | YES | `'{}'` | what jobs this store can fulfil |
| `capacity_jobs_per_day` | integer | YES | `0` | |
| `pickup_address` | text | YES | — | |
| `pickup_hours_json` | jsonb | YES | `'{}'` | |
| `geo_lat` | double precision | YES | — | |
| `geo_lng` | double precision | YES | — | |
| `take_rate_pct` | real | YES | `10.0` | platform %, default 10 |
| `route_account_id` | text | YES | — | Razorpay Route sub-merchant id (TASK-002) |
| `dispatch_whatsapp` | text | YES | — | partner staff WhatsApp number |
| `display_pickup_label` | text | YES | — | e.g. "Oxygen Thrissur" |

#### `processed_webhooks` 🟦
Webhook idempotency dedupe table (TASK-013).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `event_id` | text | NO | — | **PK** — wamid / razorpay event id |
| `handler` | text | NO | — | `meta` / `razorpay_print` / `razorpay_acad` |
| `received_at` | timestamptz | NO | `now()` | |
| `result` | jsonb | YES | — | optional handler return |

---

### WhatsApp bot 🟦

#### `bot_sessions` 🟦
State-machine state per customer phone.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `phone` | text | NO | — | **PK** — `91XXXXXXXXXX` |
| `job_id` | text | YES | — | current draft job |
| `step` | text | YES | — | state machine step |
| `size` / `colour` / `layout` / `multiup_per` / `multiup_sided` | text | YES | — | per-step selections |
| `copies` | integer | YES | — | |
| `finishing` | text | YES | — | |
| `delivery` | integer | YES | `0` | bool-as-int |
| `page_count` | integer | YES | `0` | |
| `batch_id` / `current_job_index` / `jobs_json` | mixed | YES | — | batch mode |
| `saved_json` / `job_settings_json` | text | YES | `'{}'` | session blobs |
| `updated_at` | text | YES | — | legacy text timestamp |
| `prev_step` | text | YES | — | for back-button |
| `referral_code` | text | YES | — | captured `ref_CODE` in greeting |
| `needs_human` | boolean | NO | `false` | **TASK-009** — `help` keyword flag |
| `last_help_request_at` | timestamptz | YES | — | **TASK-009** — most recent help request |

#### `conversation_log` 🟦
Inbound + outbound message history.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `phone` | text | NO | — | |
| `direction` | text | NO | — | `inbound` / `outbound` |
| `message_type` | text | NO | `'text'` | `text` / `image` / `document` / `audio` / `video` |
| `body` | text | YES | — | text body or caption |
| `filename` | text | YES | — | for media |
| `job_id` | text | YES | — | link to `jobs.job_id` |
| `created_at` | timestamptz | NO | `now()` | |
| `media_url` | text | YES | — | Supabase Storage URL for media |

#### `whatsapp_contacts` 🟦
Phone → name map, last-seen timestamp.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `phone` | text | NO | — | **PK** |
| `name` | text | YES | — | from Meta profile.name |
| `last_seen_at` | timestamptz | YES | — | last inbound message time |
| `created_at` | timestamptz | NO | `now()` | |

#### `customer_profiles` 🟦
Last-used selections per phone — pre-fills bot prompts.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `phone` | text | NO | — | **PK** |
| `last_size` / `last_colour` / `last_layout` / `last_finishing` | text | YES | — | |
| `last_copies` | integer | YES | — | |
| `last_delivery` | integer | YES | `0` | bool-as-int |
| `updated_at` | text | YES | — | |

---

### Printer hardware 🟦

#### `printer_counters` 🟦
Page-count snapshots over time. Konica + Epson.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `store_id` | text | NO | `'OSP'` | |
| `printer` | text | NO | — | **lowercase** — `konica` / `epson` |
| `polled_at` | text | NO | — | ISO-8601 string |
| `method` | text | YES | — | `snmp` / `webui` |
| `total_pages` / `print_bw` / `copy_bw` / `print_colour` / `copy_colour` | bigint | YES | — | |

#### `printer_supplies` 🟦
Toner/ink level snapshots. Konica + Epson, ~50k rows polling continuously.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `store_id` | text | NO | `'OSP'` | |
| `polled_at` | text | NO | — | |
| `printer` | text | NO | — | **lowercase** — `konica` / `epson` |
| `supply_index` | integer | NO | — | per-printer supply slot |
| `description` | text | YES | — | `Ink Cyan` / `Toner Black` / etc. |
| `max_capacity` | integer | YES | — | |
| `current_level` | integer | YES | — | |
| `pct` | real | YES | — | computed `current_level / max_capacity * 100` |

#### `supply_changes` 🟦
Detected toner/ink replacement events (level jumped up).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `store_id` | text | NO | `'OSP'` | |
| `changed_at` | text | NO | — | |
| `printer` | text | NO | — | |
| `supply_index` | integer | NO | — | |
| `description` | text | YES | — | |
| `level_before` / `level_after` | integer | YES | — | |
| `pct_before` / `pct_after` | real | YES | — | |

---

### Staff / operations 🟦

#### `staff` 🟦
Staff member registry.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | text | NO | — | **PK** — lowercase name |
| `name` | text | NO | — | |
| `pin_hash` | text | NO | — | PBKDF2 (commit `7340794`) |
| `pin_salt` | text | YES | — | required for new PINs; legacy SHA-256 has `NULL` salt |
| `active` | integer | YES | `1` | bool-as-int |
| `created_at` | timestamptz | YES | `now()` | |

#### `staff_sessions` 🟦
Staff login history with idle-logout flag.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | — | **PK** |
| `staff_id` | text | YES | — | |
| `pc_id` | text | YES | — | `PC1` / `PC2` / `PC3` |
| `store_id` | text | YES | — | |
| `login_at` | timestamptz | YES | — | |
| `logout_at` | timestamptz | YES | — | NULL = still logged in |
| `idle_logout` | boolean | YES | `false` | set by `session_timeout.py` |

#### `work_sessions` 🟦
Per-job time tracking (start/pause/resume/end).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `job_id` | text | NO | — | |
| `staff_id` | text | NO | — | |
| `started_at` / `paused_at` / `resumed_at` / `ended_at` | text | YES | — | |
| `total_sec` | integer | YES | — | |
| `paused_sec` | integer | YES | `0` | |
| `notes` | text | YES | — | |
| `created_at` | timestamptz | YES | `now()` | |

#### `rate_card` 🟦
Pricing config. **Mirror of `rate_card.py`** — kept in sync manually. Loaded on print_server boot.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `key` | text | NO | — | **PK** — e.g. `a4_bw_single` |
| `label` | text | NO | — | human label |
| `price` | real | NO | — | ₹ |
| `category` | text | NO | `'print'` | `print` / `finishing` / `dtp` / ... |
| `staff_quote` | boolean | YES | `false` | true = outsourced, needs staff intervention |
| `updated_at` | text | YES | — | |

---

### Marketing 🟦

#### `discount_codes` 🟦
Review-redemption discount codes.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `code` | text | NO | — | **PK** |
| `phone` | text | NO | — | |
| `pct_off` | integer | YES | `10` | |
| `source` | text | YES | `'review'` | |
| `used` | integer | YES | `0` | bool-as-int |
| `created_at` | timestamptz | YES | `now()` | |

#### `referrers` 🟦
Referral campaign codes (e.g. shared with influencers).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `code` | text | NO | — | **PK** |
| `label` | text | NO | — | display name |
| `platform` | text | YES | `'whatsapp'` | |
| `total_orders` | integer | YES | `0` | |
| `total_credited` | integer | YES | `0` | ₹ |
| `created_at` | timestamptz | YES | `now()` | |
| `credit_amount` | integer | NO | `20` | ₹ per referred order |

#### `referral_credits` 🟦
Earned credits per referral-attributed order.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `referrer_code` | text | NO | — | **FK → `referrers.code`** *(only FK in the database)* |
| `customer_phone` | text | NO | — | |
| `order_id` | text | NO | — | |
| `amount_inr` | integer | NO | `20` | |
| `created_at` | timestamptz | YES | `now()` | |
| `redeemed_at` | timestamptz | YES | — | NULL = unredeemed |
| `redeemed_order_id` | text | YES | — | |
| `redeemed_by` | text | YES | — | staff_id |

---

### Academic / Project Builder

#### `academic_orders` 🟪 (osp-academics)
Owned by the sibling `osp-academics` repo. The Phase 1 / Phase 2 / docx generation pipeline reads + writes this. **Schema changes here must be coordinated with osp-academics's `academic_pipeline_worker.py`.**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `project_id` | text | NO | — | **PK** — `PRJ-…` |
| `customer_name` / `whatsapp_phone` / `course` / `topic` | text | NO | — | |
| `study_area` | text | YES | — | |
| `sample_size` | integer | YES | `100` | |
| `tables_json` | text | YES | — | survey table designs |
| `status` | text | NO | `'order_received'` | state machine |
| `advance_paid` / `balance_paid` | boolean | YES | `false` | |
| `advance_amount` / `balance_amount` | numeric | YES | `500` | ₹ |
| `razorpay_advance_link` / `razorpay_balance_link` | text | YES | — | |
| `phase1_docx_path` / `phase2_docx_path` | text | YES | — | Supabase Storage |
| `drive_url` | text | YES | — | Google Drive backup |
| `payment_mode` | text | YES | — | |
| `college` / `department` / `semester` / `year` / `guide_name` / `guide_designation` / `hod_name` / `register_number` | text | YES | — | cover-page metadata |
| `revision_note` | text | YES | — | |
| `created_at` / `updated_at` | timestamptz | YES | `now()` | |
| `store_id` | text | NO | `'OSP'` | |

#### `project_builder_orders` 🟧 (shared)
Self-serve Project Builder orders. Written by `printosky/api/index.py` (Razorpay webhook handler); read by `osp-academics` (format-fixer pipeline). **No RLS** — see [Security gaps](#security-gaps).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | text | NO | — | **PK** |
| `created_at` | timestamptz | YES | `now()` | |
| `tier` | text | YES | — | service tier |
| `university` / `whatsapp_phone` / `student_name` | text | YES | — | |
| `razorpay_order_id` / `razorpay_payment_id` | text | YES | — | |
| `amount_inr` | integer | YES | — | |
| `storage_path` / `download_url` | text | YES | — | Supabase Storage |
| `status` | text | YES | `'paid'` | |
| `expires_at` | timestamptz | YES | — | download link expiry |

---

### DTP / manuscript transcription 🟦

#### `manuscript_transcripts` 🟦
The handwritten-manuscript OCR queue behind `website/dtp.html`. A staff upload inserts a row at `status='pending'` with the PDF in the `manuscripts` storage bucket; `tools/cloud_transcription_worker.py` on the DTP PC claims it (`pending` → `transcribing`), transcribes page by page with Gemini, and writes `content` + `confidence_data` back after every page so the console can follow along live and a crashed run resumes where it stopped.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | NO | `gen_random_uuid()` | **PK** |
| `filename` | text | NO | — | UNIQUE; also the storage object name |
| `pdf_url` | text | YES | — | public URL in the `manuscripts` bucket |
| `total_pages` / `transcribed_pages` | integer | NO | `0` | resume point for a crashed run |
| `status` | text | NO | `'pending'` | `pending` → `transcribing` → `completed` \| `failed` |
| `mode` | text | NO | `'standard'` | `urgent` picks the bigger Gemini model |
| `content` | text | YES | — | the transcript, appended per page |
| `confidence_data` | jsonb | NO | `'[]'` | per-word OCR confidence `[{word, confidence, flagged, page}]`, read by the low-confidence reviewer in `website/dtp.html`. **Currently always empty** — no Gemini 3.x model supports logprobs, and a trial of asking the model to self-report doubt produced 1 tag in 57 pages (above the flag threshold at that). Column and reviewer kept; nothing populates them |
| `uploaded_by_store` | text | NO | — | store/PC that uploaded it |
| `created_at` / `updated_at` | timestamptz | NO | `now()` | `updated_at` maintained by trigger |

Nothing retries a row that reaches `failed` — the worker only ever claims `pending` and resumes `transcribing` — so a failure here alerts through `ops_watchdog` (`transcription_worker.job`) and is re-queued by hand from the console. `confidence_data` was written by the worker for ten days before the column existed: every write 400'd and the job was marked failed. Add the column **in the same PR as the code that writes it**.

---

#### `transcript_corrections` 🟦
What staff actually fixed. `website/dtp.html` has always let them edit a transcript and save, but the save PATCHes `content` in place — the model's original text was overwritten and lost, so there was no record of how much rework the OCR creates or which words it gets wrong repeatedly. One row per corrected **page**, written by the console after the transcript save lands.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | NO | `gen_random_uuid()` | **PK** |
| `transcript_id` | uuid | NO | — | `manuscript_transcripts.id` |
| `filename` | text | NO | — | denormalised for reading without a join |
| `page` | integer | NO | — | 1-based, matches the `=== PAGE n ===` markers |
| `before_text` / `after_text` | text | NO | — | full page text, model's version and human's |
| `corrected_by` / `store_id` | text | YES | — | store identity; the console has no per-staff login |
| `created_at` | timestamptz | NO | `now()` | |

Stored raw and uninterpreted on purpose. Pulling word-level `before → after` pairs out of these needs Malayalam chillu normalisation (`ൺ` and `ണ്‍` are visually identical, different codepoints — a naive diff reads every one as a correction), and that belongs in Python where it is testable, next to the rule deciding which corrections recur often enough to teach the model. Both are Stage 2, deliberately left undesigned until there are real corrections to design against.

---

#### `book_feedback` 🟦
Star rating and comment left against a book order. Written by the WhatsApp book bot.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | identity | **PK** |
| `order_code` | text | NO | — | the `book_orders` row this rates |
| `phone` | text | YES | — | |
| `rating` | smallint | YES | — | |
| `comment` | text | YES | — | |
| `created_at` / `updated_at` | timestamptz | NO | `now()` | |

#### `book_returns` 🟦
A returned or replaced book order, and how the money was settled. Note the settlement columns are the record of who owes whom after courier costs both ways — `settlement_direction` (`none`/`refund`/`collect`), `settlement_amount`, `settlement_status`.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | identity | **PK** |
| `return_code` | text | NO | — | |
| `order_code` | text | NO | — | the order being returned |
| `phone` / `name` | text | YES | — | |
| `returned_items` | jsonb | NO | `'{}'` | |
| `reason` / `condition` / `notes` | text | YES | — | |
| `resolution` | text | NO | `'replacement'` | |
| `replacement_order_code` | text | YES | — | the new order, when replaced |
| `replacement_items` | jsonb | YES | — | |
| `price_delta` | numeric | NO | `0` | |
| `inward_courier` / `outward_courier` | numeric | NO | `0` | ₹ each way |
| `courier_borne_by` | text | NO | `'customer'` | |
| `settlement_direction` | text | NO | `'none'` | |
| `settlement_amount` | numeric | NO | `0` | |
| `settlement_mode` / `settlement_note` | text | YES | — | |
| `settlement_status` | text | NO | `'none'` | |
| `status` | text | NO | `'requested'` | |
| `created_by` | text | YES | — | |
| `created_at` / `updated_at` | timestamptz | NO | `now()` | |

#### `contact_notes` 🟦
Free-text staff notes against a WhatsApp contact, shown in the admin console.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `phone` | text | NO | — | |
| `note` | text | NO | — | |
| `created_by` | text | YES | — | |
| `created_at` | timestamptz | NO | `now()` | |

#### `store_role_leases` 🟦
Which box currently owns a coordinated role for a store — the lease that stops two PCs at one store both polling the printers. See [MULTI_BOX.md](MULTI_BOX.md); `device_lease.hold()` acquires and renews it every cycle.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `store_id` / `role` | text | NO | — | **PK** (composite) |
| `owner_device` | text | YES | — | `store_devices.device_id` holding it |
| `acquired_at` / `expires_at` | timestamptz | YES | — | an expired lease is free to take |
| `updated_at` | timestamptz | NO | `now()` | |

---


### B2B

#### `b2b_clients` 🟦
Corporate accounts. Status of the feature itself: see [[feature-graveyard-triage-2026-05]] — owner decision pending whether to keep, extend, or decommission.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `phone` | text | NO | — | **PK** |
| `company_name` | text | NO | — | |
| `contact_name` / `email` / `gst_number` / `address` / `notes` | text | YES | — | |
| `discount_pct` | real | YES | `0` | |
| `credit_limit` / `balance_due` | real | YES | `0` | |
| `payment_mode` | text | YES | `'NEFT'` | |
| `registered_at` | text | YES | — | |
| `active` | boolean | YES | `true` | |

#### `b2b_payments` 🟦
B2B payment ledger.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | **PK** |
| `phone` | text | YES | — | |
| `amount` | real | YES | — | ₹ |
| `mode` | text | YES | — | `cash` / `upi` / `cheque` / `neft` |
| `reference` | text | YES | — | cheque/UTR number |
| `paid_at` | text | YES | — | |
| `notes` | text | YES | — | |

---

## Views

### `epson_daily` (VIEW) 🟦
Per-day aggregate over `epson_jobs`.

`day` (date) · `store_id` · `job_count` · `total_pages` · `mono_pages` · `colour_pages`

### `konica_daily` (VIEW) 🟦
Per-day aggregate over `konica_jobs`.

`day` (date) · `store_id` · `job_count` · `total_pages` · `mono_pages` · `colour_pages` · `total_copies`

---

## Security gaps

Checked against `pg_class.relrowsecurity` on 2026-08-29. `project_builder_orders`, `referrers` and `referral_credits` were listed here as RLS-disabled; **all three now have RLS enabled** and the risk described below no longer applies to them.

**Two tables still have RLS disabled**, and both are incident leftovers rather than product tables:

| Table | Status | Risk |
|---|---|---|
| `backup_20260818_nattika_counters` | **RLS disabled** | 339 rows of Nattika printer counters, snapshotted by hand during the 18 Aug incident. Readable with the anon key. |
| `backup_20260818_nattika_epson_jobs` | **RLS disabled** | 811 rows of Nattika Epson job history — filenames and user names among them. Readable with the anon key. |

**Recommended action:** drop both once the incident data is confirmed no longer needed, and remove them from `ignored_tables` in `config/schema_manifest.yaml` at the same time. They are listed there so the drift check is not permanently red over two temporary tables; that listing is a deliberate exception, not an endorsement.

---

## Conventions

1. **Timestamp columns** are inconsistent — some are `timestamp with time zone` (preferred, newer), some are `text` storing ISO-8601 strings (legacy from when these were SQLite columns). New columns should use `timestamptz`. Don't rewrite the old ones without a coordinated migration.
2. **Booleans-as-int** (`0`/`1` integer columns instead of `boolean`) appear in older tables (`jobs.invoiced` is boolean but `jobs.colour_confirmed`, `jobs.is_sub_job`, etc. are integers). Legacy SQLite-compat artifact. New columns must use `boolean`.
3. **Printer name casing:** the `printer` column in `printer_counters`, `printer_supplies`, `supply_changes` uses **lowercase** (`konica` / `epson`). The `jobs.printer` column uses **TitleCase** (`Konica` / `Epson`). Don't unify without checking every read site — `mis.html` filters on the lowercase form.
4. **`store_id`** defaults to `'OSP'` (the Oxygen Students Paradise store). Multistore work (TASK-006, PHASE-E) lights up additional values from the `partners` table.
5. **PKs**: 4 tables use natural keys (`bot_sessions.phone`, `whatsapp_contacts.phone`, `jobs.job_id`, `partners.store_id`). The rest use `bigint` surrogate ids.
6. **No FKs except one.** Application code enforces referential integrity. Be careful when deleting rows from "parent" tables (`jobs`, `staff`, `partners`) — children won't cascade.

---

## Coordinating schema changes between repos

The `osp-academics` repo writes to `academic_orders` and `project_builder_orders`. New columns there should land via:

1. PR to `printosky` adding the SQL migration to `api/migrations/SCHEMA_vNN_*.sql` and updating this file.
2. Same PR (or a follow-up before deploy) to `osp-academics` updating the worker code.
3. Apply the migration to live Supabase before merging the code PR — both repos read the schema at module load (cloud mode in `db_cloud.py`), so the column must exist before the new code runs.

The schema-integrity check at `scripts/check_schema.py` (TASK-016, on `feature/task-016` branch) will catch drift if either repo forgets the doc update.

---

See also: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`SECURITY.md`](SECURITY.md) · [vault `roadmap-2026-05.md`](../) · [vault `feature-graveyard-triage-2026-05.md`](../)
