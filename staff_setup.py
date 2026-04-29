"""
staff_setup.py — Printosky Staff Management CLI
Usage:
  python staff_setup.py              → seed default staff (first run)
  python staff_setup.py list         → list all staff
  python staff_setup.py add <name> <pin>
  python staff_setup.py reset-pin <id> <new_pin>
  python staff_setup.py deactivate <id>
  python staff_setup.py activate <id>
"""

import hashlib
import os
import sqlite3
import sys
from datetime import datetime

if sys.platform == "win32":
    DB_PATH = r"C:\Printosky\Data\jobs.db"
else:
    DB_PATH = os.path.join(os.path.expanduser("~"), "Printosky", "Data", "jobs.db")

DEFAULT_STAFF = [
    ("priya",   "Priya",   "1001"),
    ("revana",  "Revana",  "1002"),
    ("bini",    "Bini",    "1003"),
    ("anu",     "Anu",     "1004"),
    ("deepak",  "Deepak",  "1005"),
]


import secrets as _secrets

_PBKDF2_ITERATIONS = 260_000

def sha256(pin: str) -> str:
    """Legacy — kept for reference only. Do not use for new hashes."""
    return hashlib.sha256(pin.encode()).hexdigest()

def pbkdf2_hash(pin: str) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) using PBKDF2-HMAC-SHA256."""
    salt = _secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return h, salt


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            pin_hash TEXT NOT NULL, pin_salt TEXT, active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE staff ADD COLUMN pin_salt TEXT")
        conn.commit()
    except Exception:
        pass
    return conn


def cmd_seed():
    conn = get_conn()
    seeded = []
    skipped = []
    for sid, name, pin in DEFAULT_STAFF:
        try:
            new_hash, new_salt = pbkdf2_hash(pin)
            conn.execute(
                "INSERT INTO staff (id, name, pin_hash, pin_salt) VALUES (?,?,?,?)",
                (sid, name, new_hash, new_salt)
            )
            seeded.append((name, pin))
        except sqlite3.IntegrityError:
            skipped.append(name)
    conn.commit()
    conn.close()
    if seeded:
        print("\nStaff seeded:")
        print(f"  {'Name':<12} {'ID'}")
        print(f"  {'-'*25}")
        for name, pin in seeded:
            print(f"  {name:<12} {name.lower()}")
        print("\n  Default PINs are set. Reset immediately: python staff_setup.py reset-pin <id> <new_pin>")
    if skipped:
        print(f"\nAlready existed (skipped): {', '.join(skipped)}")


def cmd_list():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, active, created_at FROM staff ORDER BY name"
    ).fetchall()
    conn.close()
    if not rows:
        print("No staff found. Run: python staff_setup.py  (to seed defaults)")
        return
    print(f"\n  {'ID':<12} {'Name':<12} {'Status':<10} Created")
    print(f"  {'-'*50}")
    for sid, name, active, created in rows:
        status = "Active" if active else "Inactive"
        print(f"  {sid:<12} {name:<12} {status:<10} {created or ''}")


def cmd_add(name: str, pin: str):
    if not pin.isdigit() or len(pin) != 4:
        print("PIN must be exactly 4 digits.")
        sys.exit(1)
    sid = name.lower().strip()
    conn = get_conn()
    try:
        new_hash, new_salt = pbkdf2_hash(pin)
        conn.execute(
            "INSERT INTO staff (id, name, pin_hash, pin_salt) VALUES (?,?,?,?)",
            (sid, name.strip(), new_hash, new_salt)
        )
        conn.commit()
        print(f"Added: {name} (id={sid}, PIN={pin})")
    except sqlite3.IntegrityError:
        print(f"ID '{sid}' already exists. Use reset-pin to change PIN.")
    conn.close()


def cmd_reset_pin(sid: str, new_pin: str):
    if not new_pin.isdigit() or len(new_pin) != 4:
        print("PIN must be exactly 4 digits.")
        sys.exit(1)
    conn = get_conn()
    new_hash, new_salt = pbkdf2_hash(new_pin)
    rows = conn.execute(
        "UPDATE staff SET pin_hash=?, pin_salt=? WHERE id=?",
        (new_hash, new_salt, sid)
    ).rowcount
    conn.commit()
    conn.close()
    if rows:
        print(f"PIN updated for '{sid}' → new PIN: {new_pin}")
    else:
        print(f"Staff ID '{sid}' not found.")


def cmd_deactivate(sid: str):
    conn = get_conn()
    rows = conn.execute("UPDATE staff SET active=0 WHERE id=?", (sid,)).rowcount
    conn.commit()
    conn.close()
    print(f"Deactivated '{sid}'." if rows else f"Staff ID '{sid}' not found.")


def cmd_activate(sid: str):
    conn = get_conn()
    rows = conn.execute("UPDATE staff SET active=1 WHERE id=?", (sid,)).rowcount
    conn.commit()
    conn.close()
    print(f"Activated '{sid}'." if rows else f"Staff ID '{sid}' not found.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        cmd_seed()
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "add" and len(args) == 3:
        cmd_add(args[1], args[2])
    elif args[0] == "reset-pin" and len(args) == 3:
        cmd_reset_pin(args[1], args[2])
    elif args[0] == "deactivate" and len(args) == 2:
        cmd_deactivate(args[1])
    elif args[0] == "activate" and len(args) == 2:
        cmd_activate(args[1])
    else:
        print(__doc__)
