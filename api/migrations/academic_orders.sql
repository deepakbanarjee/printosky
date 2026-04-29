-- Academic orders table for Printosky cloud deployment.
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query).

CREATE TABLE IF NOT EXISTS academic_orders (
    project_id              TEXT PRIMARY KEY,
    customer_name           TEXT NOT NULL,
    whatsapp_phone          TEXT NOT NULL,
    course                  TEXT NOT NULL,
    topic                   TEXT NOT NULL,
    study_area              TEXT,
    sample_size             INTEGER DEFAULT 100,
    tables_json             TEXT,
    status                  TEXT NOT NULL DEFAULT 'order_received',
    advance_paid            BOOLEAN DEFAULT FALSE,
    balance_paid            BOOLEAN DEFAULT FALSE,
    advance_amount          NUMERIC(10,2) DEFAULT 500,
    balance_amount          NUMERIC(10,2) DEFAULT 500,
    razorpay_advance_link   TEXT,
    razorpay_balance_link   TEXT,
    phase1_docx_path        TEXT,
    phase2_docx_path        TEXT,
    drive_url               TEXT,
    payment_mode            TEXT,
    college                 TEXT,
    department              TEXT,
    semester                TEXT,
    year                    TEXT,
    guide_name              TEXT,
    guide_designation       TEXT,
    hod_name                TEXT,
    register_number         TEXT,
    revision_note           TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at on every row change.
CREATE OR REPLACE FUNCTION _academic_orders_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS academic_orders_updated_at ON academic_orders;
CREATE TRIGGER academic_orders_updated_at
    BEFORE UPDATE ON academic_orders
    FOR EACH ROW EXECUTE FUNCTION _academic_orders_set_updated_at();

-- RLS: only service_role (Vercel function) may read/write.
ALTER TABLE academic_orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON academic_orders
    FOR ALL TO service_role USING (true) WITH CHECK (true);
