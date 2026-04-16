-- SCHEMA v14 — epson_jobs unique constraint for Supabase upsert
-- Run in: Supabase SQL Editor (project: mlhuwlnwwwxdnqafelko)
--
-- Fixes: 42P10 "there is no unique or exclusion constraint matching the ON CONFLICT specification"
-- Cause: supabase_sync.py upserts epson_jobs with on_conflict="store_id,id" but
--        Supabase table has no UNIQUE constraint on (store_id, id).
--
-- NOTE: The TASKS_2026-04-13.md suggestion of UNIQUE (job_id) is wrong —
--       epson_jobs has no job_id column. The correct columns are store_id + id.

ALTER TABLE epson_jobs
    ADD CONSTRAINT epson_jobs_store_id_id_key UNIQUE (store_id, id);
