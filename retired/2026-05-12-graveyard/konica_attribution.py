"""
RETIRED 2026-05-12 — Konica job-to-staff attribution.

Extracted from print_server.py (KONICA_USER_PC_MAP dict + attribute_konica_jobs
function). The companion call site in supabase_sync.py was also removed.

WHY RETIRED (per vault feature-graveyard-triage-2026-05.md):
  - 0 of 4507 konica_jobs were ever attributed (0% success rate).
  - Four root causes:
      1. KONICA_USER_PC_MAP uses uppercase "NIRMAL" but Konica reports
         "Nirmal" (46 jobs) -- case mismatch.
      2. staff_sessions has zero rows since 2026-04-01; the JOIN that
         attribute_konica_jobs runs depends on overlapping sessions.
      3. ~30% of konica_jobs have empty/NULL user_name (1344 jobs) --
         unattributable by design.
      4. The function may not have been invoked from a running
         supabase_sync loop in months -- evidence inconclusive.

HOW TO REVIVE:
  1. Resolve the staff_sessions silence first (PIN login flow broken
     since Apr 1 -- separate investigation).
  2. Fix the case sensitivity: lowercase keys + .lower() on lookup, or
     fold the data via UPDATE konica_jobs SET user_name = UPPER(user_name).
  3. Copy this file back to repo root, restore the call from
     supabase_sync.py, and ensure supabase_sync.py is actually running
     in production.
  4. After 1 week, query:
         SELECT COUNT(*) FROM konica_jobs
         WHERE attributed_to IS NOT NULL AND job_date > '2026-05-12';
     to verify attribution is working.
"""
import sqlite3
import logging

# ── Konica Windows username → PC identifier mapping ───────────────────────────
# PC1 = Priya/Deepak/Anu  |  PC2 = Revana  |  PC3 = rarely used (Nirmal)
KONICA_USER_PC_MAP = {
    # Current Windows usernames (as they appear in Konica job log)
    "ABC":        "PC1",   # Priya / Deepak / Anu
    "OXYGEN":     "PC2",   # Revana
    "NIRMAL":     "PC3",   # rarely used  -- case mismatch with real data!
    # Future — after Windows computer names are renamed
    "OXYGEN PC1": "PC1",
    "OXYGEN PC2": "PC2",
    "OXYGEN PC3": "PC3",
}


def attribute_konica_jobs(db_path: str):
    """Attribute unattributed konica_jobs to staff via active session at print time."""
    conn = sqlite3.connect(db_path)
    unattr = conn.execute(
        "SELECT job_number, user_name, job_date FROM konica_jobs WHERE attributed_to IS NULL"
    ).fetchall()
    updated = 0
    for job_number, user_name, job_date in unattr:
        pc_id = KONICA_USER_PC_MAP.get(user_name)
        if not pc_id or not job_date:
            continue
        row = conn.execute("""
            SELECT staff_id FROM staff_sessions
            WHERE pc_id=? AND login_at <= ?
              AND (logout_at IS NULL OR logout_at >= ?)
            ORDER BY login_at DESC LIMIT 1
        """, (pc_id, job_date, job_date)).fetchone()
        if row:
            conn.execute(
                "UPDATE konica_jobs SET attributed_to=? WHERE job_number=?",
                (row[0], job_number)
            )
            updated += 1
    if updated:
        conn.commit()
        logging.info("Attributed %d konica_jobs to staff", updated)
    conn.close()
