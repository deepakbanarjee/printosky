-- SCHEMA_v35: print_spec on jobs table — stores full fidelity auto-print settings
--
-- Adds a print_spec JSONB column to public.jobs so that the store PC's
-- store_puller can retrieve the exact print settings (sides, layout, nup, colour_pages)
-- chosen by the customer at order time.

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS print_spec jsonb;

COMMENT ON COLUMN public.jobs.print_spec IS
    'JSONB representing the customer-provided print specification: {sides, layout, nup, colour_pages, paper_size, orientation, copies}';
