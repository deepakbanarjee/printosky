-- Add student rates and extra discount columns to jobs table
-- Allows tracking of student rate (2 Rs/sheet) and extra discount (1.50 Rs/sheet) flags

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_student integer DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS extra_discount integer DEFAULT 0;

-- Create index for filtering by discount status
CREATE INDEX IF NOT EXISTS idx_jobs_is_student ON jobs(is_student);
CREATE INDEX IF NOT EXISTS idx_jobs_extra_discount ON jobs(extra_discount);
