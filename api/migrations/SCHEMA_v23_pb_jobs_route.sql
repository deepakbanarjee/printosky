-- Add the analyzer's chosen engine route to pb_jobs.
-- Set at format-job-create by analyzer.analyze():
--   v2_structured -> DOCX with heading styles; read structure directly
--   v4_vision     -> PDF or unstructured DOCX; Claude Vision pipeline
--
-- Applied via apply_migration (MCP) as v23_pb_jobs_route.

ALTER TABLE pb_jobs ADD COLUMN IF NOT EXISTS route TEXT;

COMMENT ON COLUMN pb_jobs.route IS
    'Engine route chosen by analyzer.analyze(): v2_structured (DOCX with '
    'heading styles -> read structure) | v4_vision (PDF/unstructured -> '
    'Claude Vision). Set at format-job-create.';
