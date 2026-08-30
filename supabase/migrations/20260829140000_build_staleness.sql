-- See when a store PC stopped taking updates, and remember what we alerted.
--
-- OSP ran code from 21 August for eight days with no alert. AUTO_UPDATE.bat
-- does report a failed pull to ops_watchdog as `store_pc.boot_update` — but
-- only if it runs, and on OSP it never did: that box is started with
-- START_PRINTOSKY.bat, which contains no git at all. A PC without the boot
-- chain reports nothing, so nothing alerts. Silence by construction.
--
-- The cloud can already see it: every box reports its running build to
-- store_devices.app_version. What was missing is (a) how long it has been on
-- that build, and (b) somewhere to dedup the alert.

-- (a) When did this box's build last change?
ALTER TABLE store_devices
    ADD COLUMN IF NOT EXISTS app_version_since TIMESTAMPTZ;

-- Stamped by the database rather than the client: heartbeat() runs on every
-- agent cycle on every box, and making it read the previous row first just to
-- compare would add a query per cycle to the same Supabase egress budget the
-- 900s poll intervals exist to protect.
CREATE OR REPLACE FUNCTION _store_devices_stamp_version_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.app_version IS DISTINCT FROM OLD.app_version THEN
        NEW.app_version_since = NOW();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS store_devices_version_change ON store_devices;
CREATE TRIGGER store_devices_version_change
    BEFORE UPDATE ON store_devices
    FOR EACH ROW EXECUTE FUNCTION _store_devices_stamp_version_change();

-- Existing rows have no history. Seed them to NOW(), not to first_seen: we do
-- not know when a box took the build it is on, and first_seen would claim it
-- has been there since the device was registered — 233 hours for every box on
-- the day this shipped, which would have alerted the whole fleet on the first
-- cron run. Starting the clock now is the honest baseline; a genuinely stuck
-- box crosses the threshold a day and a half later anyway.
UPDATE store_devices
   SET app_version_since = NOW()
 WHERE app_version_since IS NULL;

-- (b) Which stale build did we last alert about for this store? Dedup lives
-- here for the same reason the counters-stale alert reuses `state`: one alert
-- per outage, not one every time the 6-hourly cron runs.
ALTER TABLE store_pc_status
    ADD COLUMN IF NOT EXISTS stale_build_alerted TEXT;
