-- Add the confidence_data column the OCR-confidence feature has been writing
-- since 1ee8f25 ("feat(dtp): add OCR confidence extraction, PDF bounding box
-- overlay, and low-confidence word reviewer", 2026-08-18).
--
-- The code shipped without this migration, so every page-1 write in
-- tools/cloud_transcription_worker.py has been rejected by PostgREST since
-- that commit:
--
--     Could not find the 'confidence_data' column of 'manuscript_transcripts'
--     in the schema cache
--
-- which the worker caught and turned into status='failed'. Both manuscripts
-- uploaded on 2026-08-28 died that way at 0 pages transcribed. website/dtp.html
-- reads the same column (`fileObj.confidence_data || []`) for the
-- low-confidence word reviewer, so it has been rendering an empty overlay.
--
-- One row per flagged word: {word, confidence, flagged, page}.
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query).

ALTER TABLE manuscript_transcripts
    ADD COLUMN IF NOT EXISTS confidence_data JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN manuscript_transcripts.confidence_data IS
    'Per-word OCR confidence from the Gemini logprobs: [{word, confidence, flagged, page}]. Written by tools/cloud_transcription_worker.py, read by website/dtp.html.';
