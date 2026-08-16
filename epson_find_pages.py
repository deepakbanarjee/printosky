"""
Find all available pages on Epson web UI by extracting links from known pages.
"""
import os
import requests, re, urllib3
urllib3.disable_warnings()

from store_config import get_store_config

# Read the IP from store config so this keeps working across printer swaps
# (WF-C21000 192.168.55.202 → EM-C8100 192.168.55.214, 2026-06-29).
IP    = os.environ.get("EPSON_IP") or get_store_config().printers.epson_ip
BASE  = f"https://{IP}"
USER  = os.environ.get("EPSON_USER", "Oxygen")
PASS  = os.environ.get("EPSON_PASS") or exit("Set EPSON_PASS in .env or environment before running")
TIMEOUT = 10

LOGIN_URL   = f"{BASE}/PRESENTATION/ADVANCED/PASSWORD/SET"
HISTORY_URL = f"{BASE}/PRESENTATION/ADVANCED/INFO_JOBHISTORY/TOP"

def login():
    s = requests.Session()
    s.verify = False
    r = s.post(LOGIN_URL, data={
        "SEL_SESSIONTYPE": "ADMIN",
        "INPUTT_USERNAME": USER,
        "INPUTT_PASSWORD": PASS,
        "access": "https",
    }, timeout=TIMEOUT, allow_redirects=True)
    if "EPSON_COOKIE_SESSION" not in s.cookies:
        print("LOGIN FAILED"); return None
    print("Login OK")
    return s

session = login()
if not session:
    exit(1)

# Get the page we land on after login
r = session.get(f"{BASE}/PRESENTATION/ADVANCED/PASSWORD/TOP", timeout=TIMEOUT)
print(f"\nPASSWORD/TOP: {r.status_code}")

# Try the main advanced top
for path in [
    "/PRESENTATION/ADVANCED/TOP",
    "/PRESENTATION/ADVANCED/PASSWORD/TOP",
    "/PRESENTATION/ADVANCED/INFO_JOBHISTORY/TOP",
    "/PRESENTATION/BASIC/TOP",
    "/PRESENTATION/TOP",
]:
    r = session.get(BASE + path, timeout=TIMEOUT)
    if r.status_code == 200:
        print(f"\n{'='*60}\nFOUND: {path}")
        # Extract all hrefs
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        links += re.findall(r'action=["\']([^"\']+)["\']', r.text)
        links = sorted(set(l for l in links if 'PRESENTATION' in l or l.startswith('/')))
        for l in links:
            print(f"  {l}")
        # Save HTML
        fname = path.replace("/", "_").strip("_") + ".html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"  Saved: {fname}")
    else:
        print(f"  {path} -> {r.status_code}")

# Also check what the login redirect page looks like
print("\n\nChecking login response redirect target...")
r2 = session.get(f"{BASE}/PRESENTATION/ADVANCED/INFO_JOBHISTORY/TOP", timeout=TIMEOUT)
links = re.findall(r'href=["\']([^"\']+)["\']', r2.text)
links += re.findall(r'action=["\']([^"\']+)["\']', r2.text)
links = sorted(set(l for l in links if l.startswith('/')))
print(f"Links on INFO_JOBHISTORY/TOP:")
for l in links:
    print(f"  {l}")
