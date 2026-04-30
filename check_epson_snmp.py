"""Check current Epson SNMP colour counter values in DB and live."""
import sqlite3

DB = r"C:\Printosky\Data\jobs.db"
conn = sqlite3.connect(DB)

print("=== Last 5 Epson SNMP readings ===")
rows = conn.execute("""
    SELECT polled_at, total_pages, print_bw, print_colour, raw_data
    FROM printer_counters
    WHERE printer = 'epson'
    ORDER BY polled_at DESC LIMIT 5
""").fetchall()
for r in rows:
    print(f"  {r[0]}  total={r[1]}  bw={r[2]}  colour={r[3]}")
    print(f"    raw: {r[4]}")

conn.close()

# Now try live SNMP for known and candidate OIDs
print("\n=== Live SNMP probe ===")
import asyncio

async def snmp_get_async(ip, oid, community="public"):
    try:
        from pysnmp.hlapi.asyncio import (
            get_cmd, SnmpEngine, CommunityData,
            UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity,
        )
        engine = SnmpEngine()
        transport = await UdpTransportTarget.create((ip, 161), timeout=3, retries=1)
        errInd, errSts, _, varBinds = await get_cmd(
            engine, CommunityData(community), transport,
            ContextData(), ObjectType(ObjectIdentity(oid))
        )
        engine.closeDispatcher()
        if errInd or errSts:
            return f"ERROR: {errInd or errSts}"
        return str(varBinds[0][1])
    except Exception as e:
        return f"EXCEPTION: {e}"

async def probe_all():
    EPSON_IP = "192.168.55.202"
    oids = {
        "total_pages":       "1.3.6.1.2.1.43.10.2.1.4.1.1",
        "A4_all":            "1.3.6.1.4.1.1248.1.2.2.6.1.1.4.1.2",
        "colour_6.4.4.1.1":  "1.3.6.1.4.1.1248.1.2.2.6.4.1.4.1.1",
        "colour_6.4.4.1.2":  "1.3.6.1.4.1.1248.1.2.2.6.4.1.4.1.2",
        "oid_18.1":          "1.3.6.1.4.1.1248.1.2.2.3.1.1.18.1",
        "oid_6.4.6.1.1":     "1.3.6.1.4.1.1248.1.2.2.6.4.1.6.1.1",
        "oid_6.4.6.1.2":     "1.3.6.1.4.1.1248.1.2.2.6.4.1.6.1.2",
    }
    for name, oid in oids.items():
        val = await snmp_get_async(EPSON_IP, oid)
        print(f"  {name}: {val}")

asyncio.run(probe_all())
