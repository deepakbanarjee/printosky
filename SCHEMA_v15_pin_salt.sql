-- SCHEMA v15: Add pin_salt column to staff table for PBKDF2+salt PIN hashing.
--
-- Apply in Supabase SQL Editor.
-- SQLite (store PC): handled automatically by init_staff_tables() in print_server.py
--                    using: ALTER TABLE staff ADD COLUMN pin_salt TEXT
--
-- Migration is additive and backwards-compatible:
--   pin_salt IS NULL  → legacy SHA-256 hash, auto-upgraded to PBKDF2 on next login
--   pin_salt IS TEXT  → PBKDF2-HMAC-SHA256 hash with this hex salt

ALTER TABLE staff ADD COLUMN IF NOT EXISTS pin_salt TEXT;

COMMENT ON COLUMN staff.pin_salt IS
  'Hex-encoded 16-byte salt for PBKDF2-HMAC-SHA256 PIN hash. NULL means legacy SHA-256 (no salt) — upgraded automatically on next successful login.';
