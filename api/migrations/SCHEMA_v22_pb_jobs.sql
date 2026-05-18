-- Async job queue for project-builder v4 (Vision-pipeline-via-Inngest).
-- One row per submission. Status transitions:
--   pending -> processing -> done   (happy path)
--   pending -> processing -> error  (engine failure)
--   pending -> expired              (worker never picked it up within TTL)
--
-- Apply: Supabase SQL editor or apply_migration via MCP.

CREATE TABLE IF NOT EXISTS pb_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine          TEXT NOT NULL,                       -- 'format_fix_v4'
    status          TEXT NOT NULL DEFAULT 'pending',     -- pending|processing|done|error|expired
    university      TEXT,
    original_filename   TEXT,
    storage_path        TEXT,                            -- source file in Supabase storage
    render_kwargs       JSONB DEFAULT '{}'::jsonb,       -- page_numbers/header/footer/etc

    -- Progress reporting (optional, worker can update these mid-run)
    progress_pages_done   INT DEFAULT 0,
    progress_pages_total  INT DEFAULT 0,
    progress_stage        TEXT,                          -- e.g. 'rendering', 'vision', 'docx'

    -- Result (set when status=done)
    result_file_token   UUID,                            -- the previews-v4/<token>.docx
    result_download_url TEXT,
    result_meta         JSONB,                           -- {pages, cost_inr, page_kinds, etc}

    -- Error (set when status=error)
    error_message       TEXT,
    error_type          TEXT,

    -- Timing
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,

    -- Customer contact (optional, for WhatsApp ping when ready)
    contact_phone   TEXT
);

CREATE INDEX IF NOT EXISTS pb_jobs_status_idx ON pb_jobs (status);
CREATE INDEX IF NOT EXISTS pb_jobs_created_at_idx ON pb_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS pb_jobs_pending_oldest_first ON pb_jobs (created_at) WHERE status = 'pending';

ALTER TABLE pb_jobs ENABLE ROW LEVEL SECURITY;
-- No public policies = service-role only.

COMMENT ON TABLE pb_jobs IS
    'Async job queue for project-builder v4. Customer POSTs to create a job, '
    'a background worker (Inngest function) processes it, customer polls for '
    'status. Removes the 300s sync limit that v3 hits on large documents.';
