"""
Scrape Epson WF-C21000 web UI for colour/mono page counters.
Tries several candidate URLs after login and dumps their content.
Run once to find which page has the usage statistics.
"""
import requests, re, urllib3
urllib3.disable_warnings()

IP       = "192.168.55.202"
BASE     = f"https://{IP}"
USER     = "Oxygen"
PASS     = "Oxygen@1234"
TIMEOUT  = 10

LOGIN_URL   = f"{BASE}/PRESENTATION/ADVANCED/PASSWORD/SET"
HISTORY_URL = f"{BASE}/PRESENTATION/ADVANCED/INFO_JOBHISTORY/TOP"

CANDIDATES = [
    "/PRESENTATION/ADVANCED/INFO_PRINTERUSAGE/TOP",
    "/PRESENTATION/ADVANCED/INFO_PRINTERCOUNTERS/TOP",
    "/PRESENTATION/ADVANCED/INFO_PRINTERSTATUS/TOP",
    "/PRESENTATION/ADVANCED/INFO_PRINTERINFO/TOP",
    "/PRESENTATION/ADVANCED/PRINTERINFO/TOP",
    "/PRESENTATION/ADVANCED/STATUS/TOP",
    "/PRESENTATION/ADVANCED/MENULIST/TOP",
    "/PRESENTATION/ADVANCED/INFO_COPY/TOP",
]

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
        print("LOGIN FAILED")
        return None
    print("Login OK")
    return s

def scrape_page(session, path):
    url = BASE + path
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None, r.status_code
        return r.text, 200
    except Exception as e:
        return None, str(e)

def extract_numbers(html):
    """Pull all label:value pairs where value looks numeric."""
    # Strip tags, find anything that looks like "Label ... number"
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    # Find sequences with numbers > 100 (likely page counters)
    hits = re.findall(r'([A-Za-z][^\d]{0,40}?)\b(\d{3,})\b', text)
    return [(label.strip()[-60:], int(num)) for label, num in hits if int(num) > 100]

session = login()
if not session:
    exit(1)

print()
for path in CANDIDATES:
    html, status = scrape_page(session, path)
    if html is None:
        print(f"  {path}  -> {status}")
        continue
    numbers = extract_numbers(html)
    if not numbers:
        print(f"  {path}  -> 200, no numeric data")
        continue
    print(f"\n{'='*60}")
    print(f"  {path}  -> 200")
    print(f"  Numbers found:")
    for label, val in numbers[:30]:
        print(f"    {val:>10,}  | {label}")
    # Also save raw HTML for this page
    fname = path.replace("/", "_").strip("_") + ".html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Raw HTML saved: {fname}")

print("\nDone.")
