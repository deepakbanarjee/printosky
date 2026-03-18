"""
epson_snmp_discover.py
Walks the Epson WF-C21000 SNMP tree and prints all OIDs
that return numeric values. Run once to find the correct
colour/BW counter OIDs.

Usage:  python epson_snmp_discover.py
"""
import asyncio
import sys

EPSON_IP       = "192.168.55.201"
SNMP_COMMUNITY = "public"
SNMP_TIMEOUT   = 5

WALK_ROOTS = [
    "1.3.6.1.2.1.43",               # Printer MIB (prtMIB) — supplies, counters
    "1.3.6.1.4.1.1248.1.2.2",       # Epson enterprise counters
    "1.3.6.1.4.1.1248.1.2.2.44",    # Epson WF-C series counters
]

async def snmp_walk_root(root_oid):
    results = []
    try:
        from pysnmp.hlapi.asyncio import (
            next_cmd, SnmpEngine, CommunityData,
            UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity,
        )
        engine    = SnmpEngine()
        transport = await UdpTransportTarget.create(
            (EPSON_IP, 161), timeout=SNMP_TIMEOUT, retries=1
        )
        current_oid = root_oid
        for _ in range(600):
            errInd, errStat, _, varBinds = await next_cmd(
                engine,
                CommunityData(SNMP_COMMUNITY, mpModel=0),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(current_oid)),
            )
            if errInd or errStat:
                break
            for vb in varBinds:
                oid_str = str(vb[0])
                val_str = str(vb[1])
                if not oid_str.startswith(root_oid):
                    return results
                current_oid = oid_str
                try:
                    results.append((oid_str, int(val_str)))
                except ValueError:
                    pass
    except ImportError as e:
        print(f"Import error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Walk error ({root_oid}): {e}")
    return results


OUTPUT_FILE = "epson_snmp_results.txt"

async def main():
    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out(f"Discovering Epson WF-C21000 SNMP OIDs at {EPSON_IP} ...")
    out()
    all_results = []
    for root in WALK_ROOTS:
        out(f"Walking {root} ...")
        res = await snmp_walk_root(root)
        out(f"  -> {len(res)} numeric OIDs")
        all_results.extend(res)

    if not all_results:
        out("\nNo numeric OIDs found. Check IP and SNMP community.")
    else:
        counter_candidates = [(o, v) for o, v in all_results if v > 100]

        out(f"\n{'-'*70}")
        out(f"{'OID':<58} {'VALUE':>10}")
        out(f"{'-'*70}")
        for oid, val in sorted(counter_candidates, key=lambda x: -x[1]):
            out(f"{oid:<58} {val:>10,}")

        out(f"\nTotal numeric OIDs found : {len(all_results)}")
        out(f"Counter candidates (>100): {len(counter_candidates)}")
        out("\nLook for OIDs whose values sum to (or equal) the total page count (~910,112 for Epson).")

        out(f"\n{'-'*70}")
        out("ALL numeric OIDs (including small values):")
        out(f"{'-'*70}")
        for oid, val in all_results:
            out(f"{oid:<58} {val:>10,}")

    # Save to file so results aren't lost if terminal closes
    import os
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nResults saved to: {out_path}")
    input("\nPress Enter to close...")


if __name__ == "__main__":
    asyncio.run(main())
