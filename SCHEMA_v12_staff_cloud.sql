-- SCHEMA v12 — Staff table in Supabase
-- Enables Vercel-side PIN management (no store PC needed)
-- Run in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS staff (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    pin_hash   TEXT NOT NULL,
    active     INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS: service role only (Vercel uses service key)
ALTER TABLE staff ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'staff' AND policyname = 'service_role only'
  ) THEN
    CREATE POLICY "service_role only" ON staff USING (true);
  END IF;
END $$;

-- Seed default staff with temporary PINs (reset immediately after seeding)
-- PIN 1001-1005 are placeholders — use /admin/reset-pin to change them
INSERT INTO staff (id, name, pin_hash) VALUES
    ('priya',  'Priya',  encode(digest('1001', 'sha256'), 'hex')),
    ('revana', 'Revana', encode(digest('1002', 'sha256'), 'hex')),
    ('bini',   'Bini',   encode(digest('1003', 'sha256'), 'hex')),
    ('anu',    'Anu',    encode(digest('1004', 'sha256'), 'hex')),
    ('deepak', 'Deepak', encode(digest('1005', 'sha256'), 'hex'))
ON CONFLICT (id) DO NOTHING;
