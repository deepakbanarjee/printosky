"""
PRINTOSKY PRINTER POLLER — Phase 3
====================================
Polls Konica Bizhub Pro 1100 and Epson WF-C21000 for page counters.
Stores readings in SQLite. Dashboard picks them up automatically.

Konica: Uses /wcd/system_device.xml (HTTP) with SNMP fallback
Epson:  Uses SNMP (standard RFC 3805 printer MIB)

Runs automatically — imported and started by watcher.py.
Polls every POLL_INTERVAL_SECONDS (default: 5 minutes).

KONICA SNMP OIDs (bizhub, enterprise 18334):
  Total pages:  1.3.6.1.4.1.18334.1.1.1.5.7.2.1.1.0
  Print B&W:    1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.2
  Copy B&W:     1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.1
  Print Colour: 1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.2  (returns None — Pro 1100 is B&W only)
  Copy Colour:  1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.1  (returns None — Pro 1100 is B&W only)

EPSON SNMP OIDs (confirmed via epson_snmp_discover.py 2026-03-15):
  Total pages:  1.3.6.1.2.1.43.10.2.1.4.1.1   (standard prtMarkerLifeCount)
  A4 prints:    1.3.6.1.4.1.1248.1.2.2.6.1.1.4.1.2  (~B&W, sum of .4.1.* = total)
  Colour pages: derived as total − A4_print (~12,623 of 910,112 are non-A4 / colour)
  Supplies:     1.3.6.1.2.1.43.11.1.1.8.1.{idx} (max) / .9.1.{idx} (level)
                5 ink supplies found, levels: 82%, 0%, 6%, 17%, 1%
"""

import time
import sqlite3
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

# ── Config ─────────────────────────────────────────────────────────────────────
KONICA_IP            = "192.168.55.110"
EPSON_IP             = "192.168.55.201"
POLL_INTERVAL        = 300          # seconds (5 minutes)
SNMP_COMMUNITY       = "public"
SNMP_TIMEOUT         = 3            # seconds
HTTP_TIMEOUT         = 5            # seconds

# Konica web admin credentials (leave blank if no password set)
KONICA_USER          = "Admin"      # default Konica admin username
KONICA_PASS          = ""           # default is blank — set if changed

# Konica XML endpoint (confirmed working)
KONICA_XML_URL       = f"http://{KONICA_IP}/wcd/system_device.xml"
KONICA_LOGIN_URL     = f"http://{KONICA_IP}/wcd/index.html"
KONICA_COUNTER_URL   = f"http://{KONICA_IP}/wcd/counters.xml"  # alternate endpoint

# SNMP OIDs
OID_KONICA_TOTAL     = "1.3.6.1.4.1.18334.1.1.1.5.7.2.1.1.0"
OID_KONICA_PRINT_BW  = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.2"
OID_KONICA_COPY_BW   = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.1"
OID_KONICA_PRINT_COL = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.2"
OID_KONICA_COPY_COL  = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.2.1"
OID_EPSON_TOTAL      = "1.3.6.1.2.1.43.10.2.1.4.1.1"
# Epson WF-C21000 vendor OIDs — confirmed via epson_snmp_discover.py 2026-03-15
# 6.1.1.4.1.X = print pages by media type; .4.1.2 = A4 (all sizes sum = total)
# Colour/mono split not exposed directly; colour derived as total − A4_print
OID_EPSON_PRINT_MONO = "1.3.6.1.4.1.1248.1.2.2.6.1.1.4.1.2"   # A4 prints ≈ B&W

logger = logging.getLogger("printer_poller")


# ── DB setup ───────────────────────────────────────────────────────────────────

def init_printer_tables(conn):
    """Create printer_counters table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS printer_counters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at    TEXT NOT NULL,
            printer      TEXT NOT NULL,   -- 'konica' or 'epson'
            method       TEXT NOT NULL,   -- 'xml' or 'snmp'
            total_pages  INTEGER,
            print_bw     INTEGER,
            copy_bw      INTEGER,
            print_colour INTEGER,
            copy_colour  INTEGER,
            raw_data     TEXT            -- full XML or SNMP dump for debugging
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS printer_supplies (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at     TEXT NOT NULL,
            printer       TEXT NOT NULL,
            supply_index  INTEGER NOT NULL,
            description   TEXT,
            max_capacity  INTEGER,
            current_level INTEGER,
            pct           REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS supply_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at    TEXT NOT NULL,
            printer       TEXT NOT NULL,
            supply_index  INTEGER NOT NULL,
            description   TEXT,
            level_before  INTEGER,
            level_after   INTEGER,
            pct_before    REAL,
            pct_after     REAL
        )
    """)
    conn.commit()
    logger.info("printer_counters + printer_supplies + supply_changes tables ready")


# ── Konica: HTTP XML ──────────────────────────────────────────────────────────

def poll_konica_xml():
    """
    Fetch counter XML from Konica Bizhub.
    
    The Konica /wcd/ API requires a session cookie obtained by logging in first.
    Flow: POST login → get session cookie → GET system_device.xml with cookie.
    Falls back to alternate XML endpoints if primary fails.
    Returns dict with counter values, or None on complete failure.
    """
    session = requests.Session()
    
    # Step 1: Authenticate and get session cookie
    try:
        login_data = {
            "func":     "PSL_LP_SLOGIN",
            "S_LoginName": KONICA_USER,
            "S_Password": KONICA_PASS,
            "S_Permissions": "",
        }
        session.post(KONICA_LOGIN_URL, data=login_data, timeout=HTTP_TIMEOUT)
    except Exception as e:
        logger.debug(f"Konica login attempt: {e}")
        # Continue anyway — some firmwares allow unauthenticated XML reads

    # Step 2: Try primary counter endpoint, then alternates
    urls_to_try = [KONICA_XML_URL, KONICA_COUNTER_URL]
    
    for url in urls_to_try:
        try:
            r = session.get(url, timeout=HTTP_TIMEOUT)
            # Detect auth redirect / timeout page
            if "a_timeout" in r.url or "login" in r.url.lower():
                logger.debug(f"Konica {url} → redirected to auth page")
                continue
            if r.status_code != 200:
                continue
            raw = r.text
            if not raw.strip().startswith("<"):
                logger.debug(f"Konica {url} returned non-XML content")
                continue
            
            root = ET.fromstring(raw)
            counters = {}
            for elem in root.iter():
                tag = (elem.tag or "").lower()
                text = (elem.text or "").strip()
                if not text.isdigit():
                    continue
                val = int(text)
                if "total" in tag and "counter" in tag:
                    counters.setdefault("total_pages", val)
                elif "print" in tag and ("bw" in tag or "black" in tag or "mono" in tag):
                    counters.setdefault("print_bw", val)
                elif "copy" in tag and ("bw" in tag or "black" in tag or "mono" in tag):
                    counters.setdefault("copy_bw", val)
                elif "print" in tag and "col" in tag:
                    counters.setdefault("print_colour", val)
                elif "copy" in tag and "col" in tag:
                    counters.setdefault("copy_colour", val)

            if not counters:
                for elem in root.iter():
                    tag = (elem.tag or "").lower()
                    text = (elem.text or "").strip()
                    if text.isdigit() and ("count" in tag or "total" in tag or "page" in tag):
                        counters["total_pages"] = int(text)
                        break

            if counters:
                logger.info(f"Konica XML OK ({url}): {counters}")
                return {"method": "xml", "raw": raw[:2000], **counters}

            logger.debug(f"Konica {url}: no counter fields found in XML")

        except ET.ParseError:
            logger.debug(f"Konica {url}: XML parse error")
        except Exception as e:
            logger.debug(f"Konica {url}: {e}")

    logger.warning("Konica XML: all endpoints failed or returned no counters — will use SNMP")
    return None


# ── SNMP helper ───────────────────────────────────────────────────────────────

def snmp_get(ip, oid):
    """
    SNMP GET using pysnmp 7.x asyncio API wrapped in asyncio.run().
    Returns integer value or None.
    """
    import asyncio

    async def _get():
        try:
            from pysnmp.hlapi.asyncio import (
                get_cmd, SnmpEngine, CommunityData,
                UdpTransportTarget, ContextData,
                ObjectType, ObjectIdentity,
            )
            transport = await UdpTransportTarget.create(
                (ip, 161), timeout=SNMP_TIMEOUT, retries=1
            )
            errInd, errStat, _, varBinds = await get_cmd(
                SnmpEngine(),
                CommunityData(SNMP_COMMUNITY, mpModel=1),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            if errInd or errStat:
                logger.debug(f"SNMP {ip} {oid}: {errInd or errStat}")
                return None
            for vb in varBinds:
                val = str(vb[1])
                try:
                    return int(val)
                except ValueError:
                    return None
            return None
        except ImportError:
            logger.warning("pysnmp not installed — install with: pip install pysnmp")
            return None
        except Exception as e:
            logger.debug(f"SNMP {ip} {oid}: {e}")
            return None

    try:
        return asyncio.run(_get())
    except RuntimeError:
        # Already inside an event loop — use new loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_get())
        finally:
            loop.close()


# ── Konica: SNMP fallback ─────────────────────────────────────────────────────

def poll_konica_snmp():
    """Poll Konica via SNMP. Used if XML method fails or returns no data."""
    total    = snmp_get(KONICA_IP, OID_KONICA_TOTAL)
    print_bw = snmp_get(KONICA_IP, OID_KONICA_PRINT_BW)
    copy_bw  = snmp_get(KONICA_IP, OID_KONICA_COPY_BW)
    print_col= snmp_get(KONICA_IP, OID_KONICA_PRINT_COL)
    copy_col = snmp_get(KONICA_IP, OID_KONICA_COPY_COL)

    if total is None and print_bw is None:
        logger.warning("Konica SNMP returned no data")
        return None

    result = {
        "method":       "snmp",
        "total_pages":  total,
        "print_bw":     print_bw,
        "copy_bw":      copy_bw,
        "print_colour": print_col,
        "copy_colour":  copy_col,
        "raw":          f"SNMP: total={total} print_bw={print_bw} copy_bw={copy_bw}"
    }
    logger.info(f"Konica SNMP OK: {result}")
    return result


# ── Epson: SNMP ───────────────────────────────────────────────────────────────

def poll_epson_snmp():
    """
    Poll Epson WF-C21000 via SNMP.
    - total_pages : standard prtMarkerLifeCount (confirmed 910,112)
    - print_bw    : A4 print pages via vendor OID (897,489 — majority are B&W)
    - print_colour: derived as total − print_bw (~12,623 colour/non-A4 pages)
    """
    total      = snmp_get(EPSON_IP, OID_EPSON_TOTAL)
    print_mono = snmp_get(EPSON_IP, OID_EPSON_PRINT_MONO)

    if total is None:
        logger.warning("Epson SNMP returned no data")
        return None

    # Colour = total − A4_print (pages that are not standard A4, i.e. colour/A3/other)
    print_col = None
    if total is not None and print_mono is not None and total >= print_mono:
        print_col = total - print_mono

    result = {
        "method":        "snmp",
        "total_pages":   total,
        "print_colour":  print_col,
        "copy_colour":   None,
        "print_bw":      print_mono,
        "copy_bw":       None,
        "raw":           f"SNMP: total={total} a4_print={print_mono} colour_derived={print_col}"
    }
    logger.info(f"Epson SNMP OK: {result}")
    return result


# ── Save to DB ────────────────────────────────────────────────────────────────

def save_reading(conn, printer, data):
    if not data:
        return
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.execute("""
        INSERT INTO printer_counters
            (polled_at, printer, method, total_pages,
             print_bw, copy_bw, print_colour, copy_colour, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        printer,
        data.get("method"),
        data.get("total_pages"),
        data.get("print_bw"),
        data.get("copy_bw"),
        data.get("print_colour"),
        data.get("copy_colour"),
        data.get("raw", "")[:2000],
    ))
    conn.commit()
    logger.info(f"Saved {printer} counter: total={data.get('total_pages')} via {data.get('method')}")


# ── Supply level polling (standard printer MIB 1.3.6.1.2.1.43.11) ─────────────

# Hardcoded supply names per printer (index → label)
# WF-C21000 confirmed layout (SNMP walk 2026-03-16, colorant indices 43.11.1.1.3):
#   colorant indices: 1,1,2,3,4 → supplies 1&2 both Black; 3=Cyan, 4=Magenta, 5=Yellow
#   idx 1: Black 1 (K)   80%
#   idx 2: Black 2 (K)    0%  — EMPTY, needs replacement
#   idx 3: Cyan (C)        2%  — CRITICAL
#   idx 4: Magenta (M)    14%  — was wrongly labelled Maintenance Box
#   idx 5: Yellow (Y)     97%  — was 1%; confirmed replaced 2026-03-16
#   Maintenance Box is NOT in SNMP standard supply table (only visible on printer display)
SUPPLY_LABELS = {
    "konica": {1: "Toner Black", 2: "Drum Black"},
    "epson":  {1: "Ink Black 1 (K)", 2: "Ink Black 2 (K)",
               3: "Ink Cyan (C)",    4: "Ink Magenta (M)",
               5: "Ink Yellow (Y)"},
}

def poll_supplies(ip, printer_key):
    """
    Poll ink/toner supply levels via standard printer MIB.
    Returns list of dicts: [{supply_index, description, max_capacity, current_level, pct}]
    OIDs:
      Max capacity : 1.3.6.1.2.1.43.11.1.1.8.1.{index}
      Current level: 1.3.6.1.2.1.43.11.1.1.9.1.{index}
    Values of -2 = unknown max, -3 = level OK but no count.
    """
    labels  = SUPPLY_LABELS.get(printer_key, {})
    results = []
    for idx in range(1, 11):
        max_cap = snmp_get(ip, f"1.3.6.1.2.1.43.11.1.1.8.1.{idx}")
        level   = snmp_get(ip, f"1.3.6.1.2.1.43.11.1.1.9.1.{idx}")
        if max_cap is None and level is None:
            break  # no more supplies at this index
        label = labels.get(idx, f"Supply {idx}")
        pct   = None
        if max_cap and max_cap > 0 and level is not None and level >= 0:
            pct = round(level / max_cap * 100, 1)
        results.append({
            "supply_index":  idx,
            "description":   label,
            "max_capacity":  max_cap,
            "current_level": level,
            "pct":           pct,
        })
    if results:
        logger.info(f"{printer_key} supplies: {[(r['description'], r['pct']) for r in results]}")
    else:
        logger.warning(f"{printer_key} supplies: no data from SNMP")
    return results


def save_supplies(conn, printer, supplies):
    if not supplies:
        return
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    for s in supplies:
        # Detect cartridge replacement: level jumped up significantly vs last reading
        prev = conn.execute("""
            SELECT current_level, pct FROM printer_supplies
            WHERE printer=? AND supply_index=?
            ORDER BY polled_at DESC LIMIT 1
        """, (printer, s["supply_index"])).fetchone()

        if (prev and prev[0] is not None and s["current_level"] is not None
                and s["current_level"] >= 0 and s["current_level"] > (prev[0] + 10)):
            conn.execute("""
                INSERT INTO supply_changes
                    (changed_at, printer, supply_index, description,
                     level_before, level_after, pct_before, pct_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (now, printer, s["supply_index"], s["description"],
                  prev[0], s["current_level"], prev[1], s["pct"]))
            logger.info(f"Cartridge change detected: {printer} {s['description']} "
                        f"level {prev[0]}→{s['current_level']} "
                        f"({prev[1]}%→{s['pct']}%)")

        conn.execute("""
            INSERT INTO printer_supplies
                (polled_at, printer, supply_index, description, max_capacity, current_level, pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now, printer, s["supply_index"], s["description"],
              s["max_capacity"], s["current_level"], s["pct"]))
    conn.commit()
    logger.info(f"Saved {len(supplies)} supply readings for {printer}")


# ── Ink alert thresholds ──────────────────────────────────────────────────────

INK_ALERT_PCT = 10   # alert when level drops to or below this %


def _send_ink_alerts(printer: str, supplies: list, conn) -> None:
    """
    Send a WhatsApp staff alert when an ink/toner level crosses a threshold.
    Fires once on the crossing poll (prev > threshold, current <= threshold).
    0% gets its own EMPTY alert distinct from the LOW alert.
    """
    alerts = []
    for s in supplies:
        pct = s.get("pct")
        if pct is None:
            continue

        # Two most-recent rows: [0] = just inserted, [1] = previous reading
        rows = conn.execute("""
            SELECT pct FROM printer_supplies
            WHERE printer=? AND supply_index=?
            ORDER BY polled_at DESC LIMIT 2
        """, (printer, s["supply_index"])).fetchall()
        prev_pct = rows[1][0] if len(rows) >= 2 else None

        label = s["description"]
        if pct == 0 and (prev_pct is None or prev_pct > 0):
            alerts.append(f"🔴 {label}: EMPTY (0%) — replace immediately")
        elif 0 < pct <= INK_ALERT_PCT and (prev_pct is None or prev_pct > INK_ALERT_PCT):
            alerts.append(f"🟡 {label}: LOW ({pct}%) — order replacement soon")

    if alerts:
        from whatsapp_notify import send_staff_alert
        printer_name = "Epson WF-C21000" if printer == "epson" else "Konica Bizhub"
        msg = f"🖨️ *{printer_name} ink alert*\n\n" + "\n".join(alerts)
        send_staff_alert(msg)
        logger.warning(f"Ink alert sent for {printer}: {alerts}")


# ── Main poll loop ────────────────────────────────────────────────────────────

def poll_once(db_path):
    """Run one poll cycle for both printers."""
    conn = sqlite3.connect(db_path)
    init_printer_tables(conn)

    # Konica: try XML first, fall back to SNMP
    konica_data = poll_konica_xml()
    if not konica_data or konica_data.get("total_pages") is None:
        logger.info("Konica XML gave no counters — trying SNMP fallback")
        konica_data = poll_konica_snmp()
    save_reading(conn, "konica", konica_data)
    konica_supplies = poll_supplies(KONICA_IP, "konica")
    save_supplies(conn, "konica", konica_supplies)
    _send_ink_alerts("konica", konica_supplies, conn)

    # Epson: SNMP only
    epson_data = poll_epson_snmp()
    save_reading(conn, "epson", epson_data)
    epson_supplies = poll_supplies(EPSON_IP, "epson")
    save_supplies(conn, "epson", epson_supplies)
    _send_ink_alerts("epson", epson_supplies, conn)

    conn.close()


def start_poller(db_path, interval=POLL_INTERVAL):
    """
    Start the printer poller in a background daemon thread.
    Called from watcher.py on startup.
    """
    def loop():
        logger.info(f"Printer poller started — polling every {interval}s")
        logger.info(f"  Konica: http://{KONICA_IP}/wcd/system_device.xml")
        logger.info(f"  Epson:  SNMP {EPSON_IP}")
        while True:
            try:
                poll_once(db_path)
            except Exception as e:
                logger.error(f"Poller error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="PrinterPoller")
    t.start()
    logger.info("Printer poller thread launched")
    return t


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s"
    )
    db = os.path.join(os.path.expanduser("~"), "Printosky", "Data", "jobs.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    print(f"\nRunning single poll — DB: {db}\n")
    poll_once(db)
    print("\nDone. Check printer_counters table in jobs.db")
