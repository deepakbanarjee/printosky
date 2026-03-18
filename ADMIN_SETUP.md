# Printosky Admin Page — Setup Guide

## What you'll have when done:
- **printosky.com/admin** — live dashboard, password protected
- Syncs from store PC every 5 minutes via Supabase
- Shows jobs, printer counters, daily revenue from anywhere

---

## Step 1: Create Supabase Project (5 min)

1. Go to **https://supabase.com** → Sign up (free)
2. Click **New Project**
3. Name: `printosky`
4. Database Password: (save this somewhere)
5. Region: **Southeast Asia (Singapore)**
6. Create Project → wait ~2 min

---

## Step 2: Create Database Tables (2 min)

1. In Supabase → left sidebar → **SQL Editor** → **New Query**
2. Paste the entire contents of `SCHEMA.sql` (in your C:\printosky_watcher\ folder)
3. Click **Run** (green button)
4. You should see "Success" — no errors

---

## Step 3: Get Your API Keys (1 min)

1. In Supabase → **Settings** (gear icon) → **API**
2. Copy **Project URL** (looks like `https://abcdefgh.supabase.co`)
3. Copy **anon public** key (long string starting with `eyJ...`)

---

## Step 4: Configure the Watcher (2 min)

Open `C:\printosky_watcher\supabase_sync.py` in Notepad.

Find these lines near the top:
```
SUPABASE_URL = ""
SUPABASE_KEY = ""
```

Fill them in:
```
SUPABASE_URL = "https://your-project-id.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

Save the file.

---

## Step 5: Configure the Admin Page (3 min)

### 5a: Choose your admin password

Open a browser and go to: **https://emn178.github.io/online-tools/sha256.html**

Type your chosen password → copy the SHA-256 hash (64 characters).

### 5b: Edit admin.html

Open the `admin.html` file. Find these 3 lines:
```javascript
const SUPABASE_URL = "";
const SUPABASE_KEY = "";
const ADMIN_HASH   = "";
```

Fill in same URL and key as Step 4, plus your SHA-256 hash:
```javascript
const SUPABASE_URL = "https://your-project-id.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...";
const ADMIN_HASH   = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4...";  // SHA-256 of your password
```

---

## Step 6: Deploy to Netlify (2 min)

1. Zip your entire `printosky-site-seo` folder (including the new `admin.html`)
2. Go to **Netlify** → your site → **Deploys** → drag and drop the zip
3. Done — admin page is live at `https://guileless-hamster-f89a51.netlify.app/admin`

Once printosky.com DNS is fully pointing to Netlify: **https://printosky.com/admin**

---

## Step 7: Restart the Watcher

On store PC:
1. Close the watcher window (or press Ctrl+C)
2. Double-click `START_PRINTOSKY.bat`

You should see:
```
Supabase sync started — pushing every 300s to https://xxxx.supabase.co
Supabase sync thread launched
```

---

## Verification

1. On store PC: run `done OSP-20260312-0001 15 cash` (or any existing job)
2. Wait 5 minutes (or trigger a manual sync from the watcher)
3. Open `printosky.com/admin` → login → you should see the job

---

## Security Notes

- The admin password is never sent to any server — checked in-browser via SHA-256
- The Supabase anon key is public-read only (like any web API)
- Row Level Security is enabled — only SELECT is allowed via anon key
- Never share the Supabase service_role key

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Sync error" on admin page | Check SUPABASE_URL and SUPABASE_KEY in admin.html |
| No data appears | Check watcher is running; check supabase_sync.py has correct keys |
| "Incorrect password" | Recheck SHA-256 hash — must match exactly |
| Watcher shows "Supabase not configured" | Edit supabase_sync.py and add the keys |
