# install/bootstrap.ps1
# =================================================================
# Printosky new-store installer (Windows / PowerShell 5.1+).
#
# Run from the repo root (e.g. C:\printosky_watcher) as the store
# operator account (NOT admin — admin elevation is only needed for the
# optional autostart step at the end):
#
#     powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
#
# Idempotent — safe to re-run on a partially-configured PC. Each
# section detects existing state and skips work that's already done.
#
# This script handles LOCAL store-PC setup only. It assumes Printosky
# HQ has already configured the shared backend (Supabase, Vercel,
# Meta WhatsApp Cloud API, Razorpay merchant account, Cloudflare
# tunnel). See install\INSTALL.md for the full picture.
# =================================================================

$ErrorActionPreference = "Stop"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot

function Write-Header($t) {
    Write-Host ""
    Write-Host ("-" * 60) -ForegroundColor DarkCyan
    Write-Host " $t" -ForegroundColor Cyan
    Write-Host ("-" * 60) -ForegroundColor DarkCyan
}

function Write-OK($m)    { Write-Host "  [ok]  $m" -ForegroundColor Green }
function Write-Skip($m)  { Write-Host "  [--]  $m" -ForegroundColor DarkGray }
function Write-Warn($m)  { Write-Host "  [!!]  $m" -ForegroundColor Yellow }
function Write-Fail($m)  { Write-Host "  [XX]  $m" -ForegroundColor Red }

function Ask($prompt, $default = $null) {
    if ($default) {
        $answer = Read-Host "$prompt [$default]"
        if ([string]::IsNullOrWhiteSpace($answer)) { return $default } else { return $answer }
    } else {
        return Read-Host $prompt
    }
}

function Ask-YesNo($prompt, $defaultYes = $true) {
    $hint = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$prompt $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $defaultYes }
    return $answer -match '^[yY]'
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "    PRINTOSKY  -  new-store installer" -ForegroundColor Cyan
Write-Host "    Repo root: $REPO_ROOT" -ForegroundColor DarkCyan
Write-Host "==========================================================" -ForegroundColor Cyan

# -----------------------------------------------------------------
# 1. Prerequisites
# -----------------------------------------------------------------
Write-Header "1. Prerequisites"

try {
    $pyVer = & python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "python not on PATH" }
    Write-OK "Python: $pyVer"
} catch {
    Write-Fail "Python not found on PATH."
    Write-Host "        Install Python 3.13+ from https://python.org/downloads" -ForegroundColor Yellow
    Write-Host "        IMPORTANT: tick 'Add Python to PATH' during install." -ForegroundColor Yellow
    exit 1
}

try {
    & python -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip not callable" }
    Write-OK "pip available"
} catch {
    Write-Fail "pip not working. Run: python -m ensurepip --upgrade"
    exit 1
}

try {
    $nodeVer = & node --version 2>&1
    if ($LASTEXITCODE -eq 0) { Write-OK "Node.js: $nodeVer" } else { throw "no node" }
} catch {
    Write-Warn "Node.js not on PATH (only needed for legacy WhatsApp Web bot - skip if using Meta Cloud API)."
}

try {
    $null = Invoke-WebRequest -Uri "https://www.google.com" -UseBasicParsing -TimeoutSec 5
    Write-OK "Internet reachable"
} catch {
    Write-Warn "Internet check failed - pip install and SumatraPDF download will fail without it."
}

# -----------------------------------------------------------------
# 2. Local folders
# -----------------------------------------------------------------
Write-Header "2. Local folders"

$folders = @("C:\Printosky\Jobs\Incoming", "C:\Printosky\Jobs\Archive", "C:\Printosky\Data")
foreach ($d in $folders) {
    if (Test-Path $d) {
        Write-Skip "$d already exists"
    } else {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Write-OK "created $d"
    }
}

# -----------------------------------------------------------------
# 3. SumatraPDF portable binary
# -----------------------------------------------------------------
Write-Header "3. SumatraPDF (silent PDF dispatch)"

$sumatraDest = Join-Path $REPO_ROOT "SumatraPDF.exe"
if (Test-Path $sumatraDest) {
    $sz = [math]::Round((Get-Item $sumatraDest).Length / 1MB, 1)
    Write-Skip "SumatraPDF already present at $sumatraDest ($sz MB)"
} else {
    if (Ask-YesNo "Download SumatraPDF 3.6.1 portable (~20 MB) to repo root?") {
        $url = "https://www.sumatrapdfreader.org/dl/rel/3.6.1/SumatraPDF-3.6.1-64.zip"
        $zip = Join-Path $env:TEMP "sumatra_install.zip"
        $tmp = Join-Path $env:TEMP "sumatra_extract"
        Write-Host "        Downloading from $url ..." -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $exe = Get-ChildItem -Path $tmp -Filter "*.exe" -Recurse | Select-Object -First 1
        Copy-Item -Path $exe.FullName -Destination $sumatraDest -Force
        Remove-Item -Force $zip
        Remove-Item -Recurse -Force $tmp
        Write-OK "installed $sumatraDest"
    } else {
        Write-Warn "Skipped - SumatraPDF must be at one of these paths for print dispatch to work:"
        Write-Host "          $sumatraDest"
        Write-Host "          C:\printosky_watcher\SumatraPDF.exe"
        Write-Host "          C:\Program Files\SumatraPDF\SumatraPDF.exe"
    }
}

# -----------------------------------------------------------------
# 4. Python dependencies
# -----------------------------------------------------------------
Write-Header "4. Python dependencies"

$reqFile = Join-Path $REPO_ROOT "requirements.txt"
if (Test-Path $reqFile) {
    Write-Host "        running: pip install -r requirements.txt" -ForegroundColor DarkGray
    & python -m pip install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) { Write-OK "requirements installed" } else { Write-Fail "pip install failed" }
} else {
    Write-Fail "requirements.txt not found at $reqFile"
    exit 1
}

# -----------------------------------------------------------------
# 5. Per-store config (store_config.json)
# -----------------------------------------------------------------
Write-Header "5. Per-store config (store_config.json)"

$cfgPath = Join-Path $REPO_ROOT "store_config.json"
$cfgExamplePath = Join-Path $REPO_ROOT "store_config.example.json"

if (Test-Path $cfgPath) {
    $existing = Get-Content $cfgPath -Raw
    Write-Skip "store_config.json exists. Current store_id:"
    if ($existing -match '"store_id"\s*:\s*"([^"]+)"') { Write-Host "        $($Matches[1])" }
    if (-not (Ask-YesNo "Overwrite it?" $false)) {
        Write-Skip "keeping existing config"
    } else {
        Remove-Item -Force $cfgPath
    }
}

if (-not (Test-Path $cfgPath)) {
    Write-Host ""
    Write-Host "  Provide values for the new store:" -ForegroundColor DarkCyan
    $storeId   = (Ask "    store_id (short uppercase, e.g. OSP, TVM, KOC)").ToUpper()
    $storeName = Ask "    store_name (human readable)"
    $konicaIp  = Ask "    Konica printer IP" "192.168.55.110"
    $epsonIp   = Ask "    Epson printer IP"  "192.168.55.202"
    $hotFolder = Ask "    hot folder for incoming files" "C:\Printosky\Jobs\Incoming"
    $dbPath    = Ask "    SQLite jobs.db path"             "C:\Printosky\Data\jobs.db"
    $konicaQ   = Ask "    Windows printer queue name for Konica" "KONICA MINOLTA 1100 PS"
    $epsonQ    = Ask "    Windows printer queue name for Epson"  "WF-C21000 Series(Network)"

    $cfg = [ordered]@{
        store_id   = $storeId
        store_name = $storeName
        printers   = [ordered]@{ konica_ip = $konicaIp; epson_ip = $epsonIp }
        hot_folder = $hotFolder
        db_path    = $dbPath
        printer_queue_names = [ordered]@{ konica = $konicaQ; epson = $epsonQ }
    }
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $cfgPath -Encoding UTF8
    Write-OK "wrote $cfgPath"
}

# -----------------------------------------------------------------
# 6. Environment secrets (.env)
# -----------------------------------------------------------------
Write-Header "6. Environment secrets (.env)"

$envPath = Join-Path $REPO_ROOT ".env"
$envExamplePath = Join-Path $REPO_ROOT ".env.example"

if (Test-Path $envPath) {
    Write-Skip ".env already exists - not overwriting."
    Write-Host "        Verify these per-store keys are set correctly:" -ForegroundColor DarkGray
    Write-Host "          STORE_TOKEN          (unique per store, random hex)"
    Write-Host "          STORE_WHATSAPP_PHONE (this store's number)"
    Write-Host "          EPSON_USER, EPSON_PASS (this Epson's web admin creds)"
} elseif (Test-Path $envExamplePath) {
    Copy-Item -Path $envExamplePath -Destination $envPath
    $newTok = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
    (Get-Content $envPath) -replace '^STORE_TOKEN=.*', "STORE_TOKEN=$newTok" | Set-Content $envPath
    Write-OK "wrote $envPath from .env.example"
    Write-OK "auto-generated fresh STORE_TOKEN (64-hex random)"
    Write-Warn "STILL TO DO: open .env and fill in the shared values from Printosky HQ:"
    Write-Host "          META_* (4 keys), SUPABASE_* (4 keys), ANTHROPIC_API_KEY,"
    Write-Host "          ADMIN_PBKDF2_*, SUPERADMIN/STORE/MIS_SHA256_HASH, EPSON_PASS"
} else {
    Write-Fail ".env.example not found - can't seed .env. Cannot continue."
    exit 1
}

# -----------------------------------------------------------------
# 7. Bootstrap SQLite schema
# -----------------------------------------------------------------
Write-Header "7. Bootstrap jobs.db schema"

$bootstrapDb = Join-Path $PSScriptRoot "bootstrap_db.py"
if (-not (Test-Path $bootstrapDb)) {
    Write-Fail "bootstrap_db.py not found at $bootstrapDb"
    exit 1
}
& python $bootstrapDb
if ($LASTEXITCODE -ne 0) { Write-Warn "bootstrap_db.py returned non-zero" } else { Write-OK "schema applied" }

# -----------------------------------------------------------------
# 8. Staff seed (optional)
# -----------------------------------------------------------------
Write-Header "8. Staff PINs (optional)"

$staffSetup = Join-Path $REPO_ROOT "staff_setup.py"
if (Test-Path $staffSetup) {
    if (Ask-YesNo "Seed default staff (Priya/Revana/Bini/Anu/Deepak)? You can re-PIN later via API.") {
        Push-Location $REPO_ROOT
        & python staff_setup.py seed
        Pop-Location
        Write-OK "staff seeded (default PINs - reset before going live)"
    } else { Write-Skip "skipped" }
} else { Write-Warn "staff_setup.py not found - skip" }

# -----------------------------------------------------------------
# 9. Verify
# -----------------------------------------------------------------
Write-Header "9. Verify"

Push-Location $REPO_ROOT
& python -c "from store_config import get_store_config; c = get_store_config(); print(f'    store_id={c.store_id}  db_path={c.db_path}')"
if ($LASTEXITCODE -eq 0) { Write-OK "store_config loads cleanly" } else { Write-Fail "store_config failed to load" }
Pop-Location

$sumatraPaths = @(
    "C:\printosky_watcher\SumatraPDF.exe",
    $sumatraDest,
    "C:\Program Files\SumatraPDF\SumatraPDF.exe",
    "C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe"
)
$sumatraFound = $sumatraPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($sumatraFound) {
    Write-OK "SumatraPDF resolvable at: $sumatraFound"
} else {
    Write-Warn "SumatraPDF NOT findable on any expected path - print dispatch will fail"
}

$queues = (Get-Printer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$cfgRaw = Get-Content $cfgPath -Raw | ConvertFrom-Json
foreach ($p in @("konica", "epson")) {
    $qName = $cfgRaw.printer_queue_names.$p
    if ($queues -contains $qName) {
        Write-OK "Windows print queue for $p found: '$qName'"
    } else {
        Write-Warn "Print queue for $p NOT found: '$qName' - verify in Windows Devices & Printers"
    }
}

foreach ($p in @("konica", "epson")) {
    $ip = $cfgRaw.printers."${p}_ip"
    if ($ip -eq "127.0.0.1") {
        Write-Skip "$p IP is loopback (test store)"
        continue
    }
    $reach = Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue
    if ($reach) { Write-OK "$p printer reachable at $ip" } else { Write-Warn "$p printer NOT reachable at $ip" }
}

# -----------------------------------------------------------------
# 10. Done - next steps
# -----------------------------------------------------------------
Write-Header "10. Done - next steps"

Write-Host @"
  -> Open .env and fill in the shared keys from Printosky HQ
     (META_*, SUPABASE_*, ANTHROPIC_API_KEY, ADMIN_* / *_SHA256_HASH, EPSON_PASS).

  -> Start the services:
        Double-click  START_PRINTOSKY.bat   (or run from a terminal)

  -> Set up autostart on PC boot (one-time, requires admin):
        Right-click   SETUP_AUTOSTART.bat   ->   Run as administrator

  -> Reset staff PINs from temporary defaults - see STORE_SETUP_CHECKLIST.md
     section E.

  -> If this store also needs the Cloudflare tunnel for inbound webhooks,
     run SETUP_NAMED_TUNNEL.bat after the WhatsApp/Razorpay accounts are wired.
"@ -ForegroundColor Gray

Write-Host ""
Write-Host "Installer finished." -ForegroundColor Cyan
Write-Host ""
