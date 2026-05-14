-- SCHEMA v20: Epson delta row deduplication + unique constraint
--
-- ALREADY APPLIED to live Supabase on 2026-05-12 via MCP apply_migration.
-- This file is committed for historical record so the migrations directory
-- matches the live database state. The `IF NOT EXISTS` clauses make it
-- safely idempotent — re-running is a no-op.
--
-- Numbering note: drafted as v19 in a worktree, but v19 was claimed
-- upstream by SCHEMA_v19_rls_gaps.sql before this file landed. Renumbered
-- to v20 (next available slot).
--
-- Root cause: before commit 6a1c98f, supabase_sync.py had no source='weblog'
-- filter, so all delta rows were upserted each sync run. Since delta rows
-- have job_number=NULL and the conflict key was (store_id, job_number),
-- NULL != NULL in SQL meant the conflict never fired — every sync inserted
-- fresh copies. Result: ~1,767 duplicates per unique SNMP reading
-- (173,232 rows for 187 unique readings).
--
-- Fix part 1: delete duplicates, keep the earliest id per unique SNMP interval.
-- Fix part 2: partial unique index so this can never happen again.

-- Step 1: delete duplicates
DELETE FROM epson_jobs
WHERE source = 'delta'
  AND id NOT IN (
    SELECT MIN(id)
    FROM epson_jobs
    WHERE source = 'delta'
    GROUP BY store_id, snmp_total_before, snmp_total_after
  );

-- Step 2: prevent recurrence
CREATE UNIQUE INDEX IF NOT EXISTS uix_epson_jobs_delta
ON epson_jobs (store_id, snmp_total_before, snmp_total_after)
WHERE source = 'delta';
