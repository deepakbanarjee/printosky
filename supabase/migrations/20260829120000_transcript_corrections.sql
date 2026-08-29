-- Stage 1 of the transcription feedback loop: record what staff actually fix.
--
-- website/dtp.html has always let staff edit a transcript and save it, but the
-- save PATCHes `content` in place — the model's original text is overwritten
-- and gone. So there has never been any record of how much rework the OCR
-- creates, or of which words it gets wrong again and again.
--
-- One row per corrected PAGE, holding the full before and after text. The
-- before/after pair is stored raw and uninterpreted on purpose: word-level
-- extraction needs Malayalam chillu normalisation (ൺ and ണ്‍ are visually
-- identical but different codepoints, and a naive diff would read every one as
-- a correction), and that belongs in Python where it can be tested — alongside
-- the promotion rule that decides which corrections are systematic enough to
-- teach the model. Both are Stage 2, deliberately not designed until there is
-- a body of real corrections to design against.
--
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query).

CREATE TABLE IF NOT EXISTS transcript_corrections (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    transcript_id  UUID NOT NULL,                     -- manuscript_transcripts.id
    filename       TEXT NOT NULL,
    page           INTEGER NOT NULL,
    before_text    TEXT NOT NULL,                     -- what the model produced
    after_text     TEXT NOT NULL,                     -- what the human saved
    corrected_by   TEXT,                              -- store/staff identity, best effort
    store_id       TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Stage 2 reads by transcript (what did this job cost in rework?) and by date
-- (what is the model getting wrong lately?).
CREATE INDEX IF NOT EXISTS transcript_corrections_transcript_idx
    ON transcript_corrections (transcript_id);
CREATE INDEX IF NOT EXISTS transcript_corrections_created_idx
    ON transcript_corrections (created_at DESC);

ALTER TABLE transcript_corrections ENABLE ROW LEVEL SECURITY;

-- Mirrors the policies on manuscript_transcripts: the DTP console writes with
-- the anon key, the worker reads with service_role. Note this inherits that
-- table's weak posture — anyone who can reach the console can write here.
CREATE POLICY "service_role_all_corrections" ON transcript_corrections
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_all_corrections" ON transcript_corrections
    FOR ALL TO anon USING (true) WITH CHECK (true);
