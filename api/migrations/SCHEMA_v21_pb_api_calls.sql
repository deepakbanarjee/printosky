-- Per-call telemetry for project-builder format engines (v1, v2, v3).
-- Tracks tokens, cost, latency, errors per Claude API call.
-- Designed for cost analysis, slow-page diagnosis, and per-customer
-- usage reporting.
--
-- Apply: psql via Supabase SQL editor or `supabase db push`.

CREATE TABLE IF NOT EXISTS pb_api_calls (
    id              BIGSERIAL PRIMARY KEY,
    job_token       UUID NOT NULL,
    engine          TEXT NOT NULL,          -- 'format_fix_v1', 'format_fix_v2', 'format_fix_v3'
    page_no         INT,                    -- 1-indexed; NULL for non-page-bound calls (e.g. v1 detect-chapters)
    model           TEXT,                   -- 'claude-sonnet-4-5', 'claude-opus-4-5', etc. NULL for v2 (no API call).
    input_tokens          INT NOT NULL DEFAULT 0,
    output_tokens         INT NOT NULL DEFAULT 0,
    cache_read_tokens     INT NOT NULL DEFAULT 0,
    cache_write_tokens    INT NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(10, 6) NOT NULL DEFAULT 0,
    cost_inr        NUMERIC(10, 4) NOT NULL DEFAULT 0,
    elapsed_ms      INT,                    -- per-call latency
    error           TEXT,                   -- NULL on success
    university      TEXT,                   -- 'ktu', 'ku', 'calicut', etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pb_api_calls_job_token_idx ON pb_api_calls (job_token);
CREATE INDEX IF NOT EXISTS pb_api_calls_created_at_idx ON pb_api_calls (created_at DESC);
CREATE INDEX IF NOT EXISTS pb_api_calls_engine_idx ON pb_api_calls (engine);

-- RLS: service-role only (this is internal telemetry, no customer access).
ALTER TABLE pb_api_calls ENABLE ROW LEVEL SECURITY;
-- No policies = only service role can read/write. Matches other internal tables.

-- Roll-up view: one row per job. For "what did this document cost?"
CREATE OR REPLACE VIEW pb_api_calls_by_job AS
SELECT
    job_token,
    engine,
    university,
    MIN(created_at)            AS started_at,
    MAX(created_at)            AS finished_at,
    COUNT(*)                   AS call_count,
    SUM(input_tokens)          AS total_input_tokens,
    SUM(output_tokens)         AS total_output_tokens,
    SUM(cache_read_tokens)     AS total_cache_read_tokens,
    SUM(cache_write_tokens)    AS total_cache_write_tokens,
    SUM(cost_usd)              AS total_cost_usd,
    SUM(cost_inr)              AS total_cost_inr,
    MAX(elapsed_ms)            AS slowest_call_ms,
    SUM(elapsed_ms)            AS total_engine_ms,
    COUNT(*) FILTER (WHERE error IS NOT NULL) AS error_count
FROM pb_api_calls
GROUP BY job_token, engine, university;

COMMENT ON TABLE pb_api_calls IS
    'Per-Claude-API-call telemetry for project-builder format engines. '
    'One row per outbound Claude call. v2 emits a single row with cost=0 '
    'per job (no API calls, but useful for engine-usage analytics).';
