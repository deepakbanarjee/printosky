-- SCHEMA v6: Epson job log table
-- Run this in Supabase SQL Editor (project: mlhuwlnwwwxdnqafelko)
-- after SCHEMA_v5_migration.sql

CREATE TABLE IF NOT EXISTS epson_jobs (
    id                 BIGSERIAL PRIMARY KEY,
    store_id           TEXT    NOT NULL DEFAULT 'OSP',
    source             TEXT    NOT NULL DEFAULT 'delta',
    job_number         TEXT,
    job_type           TEXT,
    user_name          TEXT,
    file_name          TEXT,
    result             TEXT,
    pages_printed      INTEGER,
    mono_pages         INTEGER,
    color_pages        INTEGER,
    copies             INTEGER,
    paper_size         TEXT,
    job_date           TEXT,
    print_end_date     TEXT,
    snmp_total_before  BIGINT,
    snmp_total_after   BIGINT,
    delta_pages        INTEGER,
    attributed_job_id  TEXT,
    imported_at        TEXT
);

ALTER TABLE epson_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "auth read epson_jobs"
    ON epson_jobs FOR SELECT
    USING (auth.role() = 'authenticated');

CREATE INDEX IF NOT EXISTS idx_epson_jobs_date     ON epson_jobs (job_date);
CREATE INDEX IF NOT EXISTS idx_epson_jobs_jobid    ON epson_jobs (attributed_job_id);
CREATE INDEX IF NOT EXISTS idx_epson_jobs_storeid  ON epson_jobs (store_id);
CREATE INDEX IF NOT EXISTS idx_epson_jobs_source   ON epson_jobs (source);

-- Daily aggregation views for MIS dashboard
CREATE OR REPLACE VIEW konica_daily AS
SELECT
    DATE(job_date)      AS day,
    store_id,
    COUNT(*)            AS job_count,
    SUM(pages_printed)  AS total_pages,
    SUM(mono_pages)     AS mono_pages,
    SUM(color_pages)    AS colour_pages,
    SUM(copies)         AS total_copies
FROM konica_jobs
WHERE result = 'No Error'
GROUP BY DATE(job_date), store_id;

CREATE OR REPLACE VIEW epson_daily AS
SELECT
    DATE(job_date)                                              AS day,
    store_id,
    COUNT(*)                                                    AS job_count,
    COALESCE(SUM(pages_printed), SUM(delta_pages))             AS total_pages,
    SUM(mono_pages)                                             AS mono_pages,
    SUM(color_pages)                                            AS colour_pages
FROM epson_jobs
GROUP BY DATE(job_date), store_id;
