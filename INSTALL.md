# PRINTOSKY JOB TRACKER — INSTALL GUIDE
## Oxygen Students Paradise (Partner #1)

---

## What This System Does

Every file that arrives at the store is automatically logged — no staff action needed.
Files are tracked the moment they land in the hot folder.

The system captures:
- Filename, file type, file size
- Exact time received
- Auto-generated Job ID (e.g. OSP-20260311-0042)
- Source (Hot Folder / WhatsApp / Email — Phase 2)

Staff only need to add: customer name, amount, payment mode.

---

## Step 1 — Install Python (if not already installed)

1. Open browser → go to https://python.org/downloads
2. Download Python 3.11 (Windows)
3. Run installer — **IMPORTANT: tick "Add Python to PATH"** before clicking Install
4. Verify: open Command Prompt, type `python --version` — should show Python 3.11.x

---

## Step 2 — Copy Files to Store PC

Copy the entire `printosky_watcher` folder to:
```
C:\Printosky\
```
So the structure is:
```
C:\Printosky\
  watcher.py
  dashboard.py
  START_PRINTOSKY.bat
  SETUP_AUTOSTART.bat
  requirements.txt
  INSTALL.md  (this file)
```

---

## Step 3 — Install Python Packages

1. Open Command Prompt (Win + R → type `cmd` → Enter)
2. Run:
```
pip install watchdog gspread google-auth google-auth-oauthlib websockets requests pysnmp
```
3. Wait for installation to finish.

---

## Step 4 — First Run

1. Double-click `START_PRINTOSKY.bat`
2. Two things happen:
   - A browser window opens: **http://localhost:5000** — this is the dashboard
   - A terminal window opens — this is the watcher (keep it running)

3. The following folders are created automatically:
   - `C:\Printosky\Jobs\Incoming\` ← **staff drop all files here**
   - `C:\Printosky\Jobs\Archive\`
   - `C:\Printosky\Data\` (database lives here)

---

## Step 5 — Test It

1. Copy any PDF or Word file into `C:\Printosky\Jobs\Incoming\`
2. Watch the terminal — it should immediately show:
```
═══════════════════════════════════════════════════════
  NEW JOB REGISTERED: OSP-20260311-0001
  File   : your_test_file.pdf
  Size   : 245.3 KB
  Time   : 2026-03-11 10:23:45
  Source : Hot Folder
═══════════════════════════════════════════════════════
```
3. Refresh the browser dashboard — the job appears there too.

---

## Step 6 — Auto-Start on PC Boot (Do This Once)

So the system starts automatically when the PC is turned on:

1. Right-click `SETUP_AUTOSTART.bat` → **Run as Administrator**
2. Done. From now on, Printosky starts every time the PC boots.

---

## Step 7 — Staff Workflow (How to Use)

### When a customer arrives:
1. Receive file via WhatsApp → save to `C:\Printosky\Jobs\Incoming\`
2. OR: copy from pen drive to `C:\Printosky\Jobs\Incoming\`
3. System auto-logs the file with a Job ID immediately

### When job is complete and payment collected:
In the terminal window, type:
```
done OSP-20260311-0001 150 Cash
```
(Job ID, amount collected, Cash or UPI)

### To see all pending jobs:
```
pending
```

### To see today's summary:
```
report
```

---

## Step 8 — Google Sheets Setup (Owner Remote Access)

So the owner can see live data from anywhere:

### 8a — Create the Google Sheet
1. Go to https://sheets.google.com
2. Create a new sheet named: **Printosky Job Tracker**
3. Rename the first tab: **Job Log**

### 8b — Create a Service Account (one-time setup)
1. Go to https://console.cloud.google.com
2. Create a new project named "Printosky"
3. Enable: Google Sheets API + Google Drive API
4. Go to IAM → Service Accounts → Create Service Account
5. Name it "printosky-tracker"
6. Download the JSON key file
7. Rename it to `credentials.json`
8. Copy `credentials.json` into `C:\Printosky\`

### 8c — Share the Sheet with the Service Account
1. Open the Google Sheet
2. Click Share
3. Add the service account email (from the JSON file — it looks like `printosky-tracker@printosky.iam.gserviceaccount.com`)
4. Give it Editor access

### 8d — Update the path in watcher.py
Open `watcher.py` in Notepad. Find this line:
```python
GSHEETS_CREDENTIALS_FILE = "credentials.json"
```
Change to:
```python
GSHEETS_CREDENTIALS_FILE = r"C:\Printosky\credentials.json"
```

From now on, every new job is automatically added to the Google Sheet.
Share the Google Sheet with investors — they have view-only access.

---

## Access Control Levels

| Who | What They See | How |
|-----|---------------|-----|
| Staff at store | Dashboard on `http://localhost:5000` | Browser on store PC |
| Owner (remote) | Live Google Sheet + dashboard via TeamViewer | Google Sheets link / remote desktop |
| Investors | Google Sheet (view-only, no editing) | Shared Google Sheet link |

---

## Phase 2 — Coming Next

Once Phase 1 (hot folder) is running smoothly:

- **WhatsApp auto-capture**: files sent to 80896 99436 auto-saved to hot folder
- **Printer log polling**: Konica and Epson page counts auto-logged
- **Gmail capture**: files emailed to print2oxygen@gmail.com auto-saved to hot folder

---

## Troubleshooting

**"Python not found"**: Re-install Python with "Add to PATH" ticked.

**Dashboard not opening**: Check if something else is using port 5000. Open Command Prompt and run: `netstat -ano | findstr 5000`

**Files not being detected**: Make sure the file is dropped directly into `C:\Printosky\Jobs\Incoming\` and not a subfolder.

**Google Sheets not syncing**: Check credentials.json path. Check that the sheet is shared with the service account email.
