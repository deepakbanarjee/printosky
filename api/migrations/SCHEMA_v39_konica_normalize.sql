-- SCHEMA v39 — one canonical field shape for konica_jobs
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent: a second run rewrites
-- nothing. Rewrites only rows that are already in the wrong shape; a row the
-- fetcher wrote after this ships is untouched.
--
-- WHY
-- ---
-- konica_jobs has had two writers that never agreed:
--
--   column      konica_csv_importer (Feb-Mar 2026)   SOAP fetcher (Apr 2026 ->)
--   ----------  -----------------------------------  --------------------------
--   job_type    Print / Copy / Scan                  PRINT / COPY / SCAN
--   result      No Error / Canceled / Error          OK / USERCANCEL / UNKNOWNERROR
--   job_date    2026-03-16 09:46:14                  2026/09/02 09:18:59
--   paper_size  Legal                                LEGAL
--
-- Measured on this database, 2026-09-02, 14,864 rows:
--
--   * MIS filters result=eq."No Error", so from 2026-04-13 it matched only the
--     1,980 rows the retired CSV importer wrote. The Konica Job Details panel
--     and Staff Performance have been showing February-March data for five
--     months while looking perfectly plausible.
--   * job_date is compared as a string. '/' (0x2F) sorts above '-' (0x2D), so
--     all 12,864 slash-dated rows pass every window filter -- today, week,
--     month and year were the same query.
--   * renderKJPeriod() buckets on job_type === 'Print' / 'Copy', so those
--     12,864 rows counted as neither.
--
-- The store PCs fix their own copy of this table from konica_jobs_fetcher
-- (_normalise_once), because nothing runs fix_db.py for them. This file is the
-- cloud half.

BEGIN;

-- job_type: Print | Copy | Scan. An unrecognised value is LEFT ALONE, never
-- title-cased into a type it is not -- konica_normalize alerts on those.
UPDATE public.konica_jobs
   SET job_type = initcap(lower(job_type))
 WHERE job_type IS NOT NULL
   AND lower(job_type) IN ('print', 'copy', 'scan')
   AND job_type <> initcap(lower(job_type));

-- result: the vocabulary every console already reads.
UPDATE public.konica_jobs
   SET result = CASE upper(result)
                  WHEN 'OK'           THEN 'No Error'
                  WHEN 'NOERROR'      THEN 'No Error'
                  WHEN 'USERCANCEL'   THEN 'Canceled'
                  WHEN 'CANCEL'       THEN 'Canceled'
                  WHEN 'CANCELLED'    THEN 'Canceled'
                  WHEN 'UNKNOWNERROR' THEN 'Error'
                END
 WHERE result IS NOT NULL
   AND upper(result) IN ('OK', 'NOERROR', 'USERCANCEL', 'CANCEL',
                         'CANCELLED', 'UNKNOWNERROR');

-- job_date / print_end_date: YYYY/MM/DD HH:MM:SS -> YYYY-MM-DD HH:MM:SS, so a
-- string comparison orders them and a date filter means what it says.
UPDATE public.konica_jobs
   SET job_date = replace(substring(job_date from 1 for 10), '/', '-')
                  || substring(job_date from 11)
 WHERE job_date ~ '^\d{4}/\d{2}/\d{2}';

UPDATE public.konica_jobs
   SET print_end_date = replace(substring(print_end_date from 1 for 10), '/', '-')
                        || substring(print_end_date from 11)
 WHERE print_end_date ~ '^\d{4}/\d{2}/\d{2}';

-- paper_size: one bucket per size, and one absent bucket for "the machine did
-- not say" instead of three that look like three different sizes.
UPDATE public.konica_jobs
   SET paper_size = upper(paper_size)
 WHERE paper_size IS NOT NULL
   AND btrim(paper_size) <> ''
   AND upper(btrim(paper_size)) NOT IN ('UNKNOWN', 'NONE', '-')
   AND paper_size <> upper(btrim(paper_size));

UPDATE public.konica_jobs
   SET paper_size = NULL
 WHERE paper_size IS NOT NULL
   AND upper(btrim(paper_size)) IN ('', 'UNKNOWN', 'NONE', '-');

COMMIT;

-- Verify: every one of these should come back with a single canonical value
-- per row, and no slash-dated rows at all.
--
--   SELECT job_type, count(*) FROM public.konica_jobs GROUP BY 1 ORDER BY 2 DESC;
--   SELECT result,   count(*) FROM public.konica_jobs GROUP BY 1 ORDER BY 2 DESC;
--   SELECT count(*) FROM public.konica_jobs WHERE job_date LIKE '____/%';   -- 0
