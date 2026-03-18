-- PRINTOSKY SUPABASE SCHEMA
-- Run this once in Supabase → SQL Editor → New Query

-- Jobs table
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    store_id         TEXT NOT NULL DEFAULT 'OSP',
    received_at      TEXT,
    filename         TEXT,
    file_extension   TEXT,
    file_size_kb     REAL,
    source           TEXT,
    sender           TEXT,
    status           TEXT,
    customer_name    TEXT,
    service_type     TEXT,
    amount_quoted    REAL,
    amount_collected REAL,
    payment_mode     TEXT,
    completed_at     TEXT
);

-- Printer counters table
CREATE TABLE IF NOT EXISTS printer_counters (
    id            BIGSERIAL PRIMARY KEY,
    store_id      TEXT NOT NULL DEFAULT 'OSP',
    printer       TEXT NOT NULL,
    polled_at     TEXT NOT NULL,
    method        TEXT,
    total_pages   BIGINT,
    print_bw      BIGINT,
    copy_bw       BIGINT,
    print_colour  BIGINT,
    copy_colour   BIGINT,
    UNIQUE (store_id, printer, polled_at)
);

-- Daily summary table
CREATE TABLE IF NOT EXISTS daily_summary (
    store_id    TEXT NOT NULL DEFAULT 'OSP',
    date        TEXT NOT NULL,
    total_jobs  INTEGER,
    completed   INTEGER,
    pending     INTEGER,
    revenue     REAL,
    cash        REAL,
    upi         REAL,
    synced_at   TEXT,
    PRIMARY KEY (store_id, date)
);

-- Enable Row Level Security but allow public read via anon key
ALTER TABLE jobs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE printer_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_summary   ENABLE ROW LEVEL SECURITY;

-- Allow anon key to read (admin page uses this)
CREATE POLICY "anon read jobs"
    ON jobs FOR SELECT USING (true);

CREATE POLICY "anon read printer_counters"
    ON printer_counters FOR SELECT USING (true);

CREATE POLICY "anon read daily_summary"
    ON daily_summary FOR SELECT USING (true);

-- Allow anon key to write (watcher uses this)
CREATE POLICY "anon insert jobs"
    ON jobs FOR INSERT WITH CHECK (true);

CREATE POLICY "anon upsert jobs"
    ON jobs FOR UPDATE USING (true);

CREATE POLICY "anon insert printer_counters"
    ON printer_counters FOR INSERT WITH CHECK (true);

CREATE POLICY "anon upsert daily_summary"
    ON daily_summary FOR INSERT WITH CHECK (true);

CREATE POLICY "anon update daily_summary"
    ON daily_summary FOR UPDATE USING (true);

-- ── B2B Clients ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS b2b_clients (
    phone           TEXT PRIMARY KEY,
    company_name    TEXT NOT NULL,
    contact_name    TEXT,
    email           TEXT,
    discount_pct    REAL DEFAULT 0,
    credit_limit    REAL DEFAULT 0,
    balance_due     REAL DEFAULT 0,
    payment_mode    TEXT DEFAULT 'NEFT',
    gst_number      TEXT,
    address         TEXT,
    notes           TEXT,
    registered_at   TEXT,
    active          BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS b2b_payments (
    id           BIGSERIAL PRIMARY KEY,
    phone        TEXT,
    amount       REAL,
    mode         TEXT,
    reference    TEXT,
    paid_at      TEXT,
    notes        TEXT
);

-- Add new columns to jobs if not present
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS page_count     INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS filepath       TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS copies         INTEGER DEFAULT 1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finishing      TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS invoiced       BOOLEAN DEFAULT false;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS invoice_number TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS notes          TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS printer  TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS colour   TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS size     TEXT;

-- Printer supplies (ink/toner levels)
CREATE TABLE IF NOT EXISTS printer_supplies (
    id            BIGSERIAL PRIMARY KEY,
    store_id      TEXT NOT NULL DEFAULT 'OSP',
    polled_at     TEXT NOT NULL,
    printer       TEXT NOT NULL,
    supply_index  INTEGER NOT NULL,
    description   TEXT,
    max_capacity  INTEGER,
    current_level INTEGER,
    pct           REAL
);

ALTER TABLE printer_supplies ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon read printer_supplies"
    ON printer_supplies FOR SELECT USING (true);
CREATE POLICY "anon insert printer_supplies"
    ON printer_supplies FOR INSERT WITH CHECK (true);

-- RLS for new tables
ALTER TABLE b2b_clients  ENABLE ROW LEVEL SECURITY;
ALTER TABLE b2b_payments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon all b2b_clients"  ON b2b_clients  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "anon all b2b_payments" ON b2b_payments FOR ALL USING (true) WITH CHECK (true);

-- ── Rate Card ─────────────────────────────────────────────────────────────────
-- key   : unique rate identifier e.g. "a4_bw_single", "finishing_spiral", "delivery"
-- label : human-readable name shown in UI
-- price : current price in INR
-- category: "print" | "finishing" | "delivery"
-- staff_quote: for finishing items that need manual quoting
-- updated_at: ISO timestamp of last edit
CREATE TABLE IF NOT EXISTS rate_card (
    key          TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    price        REAL NOT NULL,
    category     TEXT NOT NULL DEFAULT 'print',
    staff_quote  BOOLEAN DEFAULT false,
    updated_at   TEXT
);

ALTER TABLE rate_card ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon all rate_card" ON rate_card FOR ALL USING (true) WITH CHECK (true);

-- Seed default rates (safe to re-run — does not overwrite existing values)
INSERT INTO rate_card (key, label, price, category, staff_quote) VALUES
    ('a4_bw_single',      'A4 B&W Single Side',     1.50,  'print',     false),
    ('a4_bw_double',      'A4 B&W Double Side',      2.50,  'print',     false),
    ('a4_col_single',     'A4 Colour Single Side',   8.00,  'print',     false),
    ('a4_col_double',     'A4 Colour Double Side',   14.00, 'print',     false),
    ('a3_bw_single',      'A3 B&W',                  5.00,  'print',     false),
    ('a3_col_single',     'A3 Colour Single',        20.00, 'print',     false),
    ('finishing_staple',  'Side Staple',             5.00,  'finishing', false),
    ('finishing_spiral',  'Spiral Binding (min)',    30.00, 'finishing', true),
    ('finishing_wiro',    'Wiro Binding (min)',       50.00, 'finishing', true),
    ('finishing_soft',    'Soft Binding (min)',       80.00, 'finishing', true),
    ('finishing_project', 'Project Binding (min)',   200.00,'finishing', true),
    ('finishing_hard',    'Hard Binding (min)',       150.00,'finishing', true),
    ('finishing_record',  'Record Binding',          150.00,'finishing', false),
    ('finishing_thesis',  'Thesis Binding',          500.00,'finishing', false),
    ('delivery',          'Delivery (flat)',          30.00, 'delivery',  false)
ON CONFLICT (key) DO NOTHING;

-- ── Supply change log ─────────────────────────────────────────────────────────
-- Auto-populated by printer_poller.py when a supply level jumps up (cartridge replaced)
CREATE TABLE IF NOT EXISTS supply_changes (
    id            BIGSERIAL PRIMARY KEY,
    store_id      TEXT NOT NULL DEFAULT 'OSP',
    changed_at    TEXT NOT NULL,
    printer       TEXT NOT NULL,
    supply_index  INTEGER NOT NULL,
    description   TEXT,
    level_before  INTEGER,
    level_after   INTEGER,
    pct_before    REAL,
    pct_after     REAL,
    UNIQUE (store_id, id)
);

ALTER TABLE supply_changes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon read supply_changes"  ON supply_changes FOR SELECT USING (true);
CREATE POLICY "anon insert supply_changes" ON supply_changes FOR INSERT WITH CHECK (true);

-- ── Konica Job Log ─────────────────────────────────────────────────────────────
-- Imported from CSV exported from Konica Bizhub admin panel
-- Import with: python konica_csv_importer.py path/to/file.csv
CREATE TABLE IF NOT EXISTS konica_jobs (
    id             BIGSERIAL PRIMARY KEY,
    store_id       TEXT NOT NULL DEFAULT 'OSP',
    job_number     INTEGER NOT NULL,
    job_type       TEXT,          -- Print | Copy | Scan
    user_name      TEXT,          -- staff name from printer login (ABC, OXYGEN, etc.)
    file_name      TEXT,
    result         TEXT,          -- No Error | Canceled
    num_pages      INTEGER,       -- pages in document
    pages_printed  INTEGER,       -- actual pages printed (num_pages × copies)
    mono_pages     INTEGER,
    color_pages    INTEGER,
    copies         INTEGER,
    job_date       TEXT,          -- ISO datetime (job reception)
    print_end_date TEXT,          -- ISO datetime (printing finished)
    paper_size     TEXT,          -- A4 | A3
    paper_type     TEXT,
    UNIQUE (store_id, job_number)
);

ALTER TABLE konica_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon read konica_jobs"   ON konica_jobs FOR SELECT USING (true);
CREATE POLICY "anon insert konica_jobs" ON konica_jobs FOR INSERT WITH CHECK (true);
CREATE POLICY "anon upsert konica_jobs" ON konica_jobs FOR UPDATE USING (true);

-- Index for fast date-range queries (MIS dashboard uses these)
CREATE INDEX IF NOT EXISTS idx_konica_jobs_date     ON konica_jobs (job_date);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_user     ON konica_jobs (user_name);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_type     ON konica_jobs (job_type);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_storeid  ON konica_jobs (store_id);

-- ── Partner Stores ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partners (
    store_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT DEFAULT 'Spoke',      -- 'Hub' | 'Spoke'
    contact     TEXT,
    phone       TEXT,
    location    TEXT,
    territory   TEXT,
    equipment   TEXT,
    commission  REAL DEFAULT 0,
    status      TEXT DEFAULT 'Active',
    notes       TEXT,
    joined_at   TEXT
);

ALTER TABLE partners ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='partners' AND policyname='anon all partners') THEN
    CREATE POLICY "anon all partners" ON partners FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- Seed Partner #1 — Oxygen Students Paradise
INSERT INTO partners (store_id, name, type, contact, phone, location, territory, equipment, commission, status, notes, joined_at)
VALUES (
  'OSP',
  'Oxygen Students Paradise',
  'Hub',
  'Deepak',
  '+91 94957 06405',
  'Thriprayar, Thrissur',
  'Thriprayar, Irinjalakuda, Kodungallur',
  'Konica Minolta bizhub PRO 1100, Epson WF-C21000',
  0,
  'Active',
  'Partner #1 — Hub store. System pilot location.',
  '2026-03-01'
) ON CONFLICT (store_id) DO NOTHING;
