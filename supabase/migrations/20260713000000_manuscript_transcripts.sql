-- Migration to create the manuscript_transcripts table for completely online workflow.
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query).

CREATE TABLE IF NOT EXISTS manuscript_transcripts (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    filename            TEXT UNIQUE NOT NULL,
    pdf_url             TEXT,                              -- URL of the PDF in storage
    total_pages         INTEGER NOT NULL DEFAULT 0,
    transcribed_pages   INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending',   -- 'pending', 'transcribing', 'completed', 'failed'
    mode                TEXT NOT NULL DEFAULT 'standard',  -- 'standard', 'urgent'
    content             TEXT,                              -- The transcribed text
    uploaded_by_store   TEXT NOT NULL,                     -- Store ID / PC ID that uploaded it
    created_at          TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Trigger to auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION _manuscript_transcripts_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS manuscript_transcripts_updated_at ON manuscript_transcripts;
CREATE TRIGGER manuscript_transcripts_updated_at
    BEFORE UPDATE ON manuscript_transcripts
    FOR EACH ROW EXECUTE FUNCTION _manuscript_transcripts_set_updated_at();

-- Enable RLS
ALTER TABLE manuscript_transcripts ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated roles and service_role to manage transcripts
CREATE POLICY "service_role_all_transcripts" ON manuscript_transcripts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_all_transcripts" ON manuscript_transcripts
    FOR ALL TO anon USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- Storage bucket and policies for 'manuscripts'
-- ---------------------------------------------------------------------------

-- Create the manuscripts bucket
INSERT INTO storage.buckets (id, name, public)
VALUES ('manuscripts', 'manuscripts', true)
ON CONFLICT (id) DO NOTHING;

-- Policies for public storage access
CREATE POLICY "Public Read Access" ON storage.objects
    FOR SELECT TO public USING (bucket_id = 'manuscripts');

CREATE POLICY "Anyone Can Upload" ON storage.objects
    FOR INSERT TO public WITH CHECK (bucket_id = 'manuscripts');

CREATE POLICY "Anyone Can Update" ON storage.objects
    FOR UPDATE TO public USING (bucket_id = 'manuscripts');

CREATE POLICY "Anyone Can Delete" ON storage.objects
    FOR DELETE TO public USING (bucket_id = 'manuscripts');
