# install/bootstrap.ps1
# =================================================================
# Printosky new-store installer (Windows / PowerShell 5.1+).
#
# Goal: no manual work after the installer finishes. Operator only
# answers physical-location questions (printer IPs, queue names,
# WhatsApp number, Epson admin password). Everything else is
# auto-generated or copied from an HQ secrets file.
#
# Run from the repo root (e.g. C:\printosky_watcher):
#
#     powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
#
# Idempotent - safe to re-run. Existing store_config.json / .env are
# only overwritten with explicit consent.
# =================================================================

$ErrorActionPreference = "Stop"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot

# Force UTF-8 for any Python subprocesses we spawn — Windows cp1252 default
# breaks on any non-ASCII byte in stdout/stderr (e.g. arrows in log lines).
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"

function Write-Header($t) {
    Write-Host ""
    Write-Host ("-" * 62) -ForegroundColor DarkCyan
    Write-Host " $t" -ForegroundColor Cyan
    Write-Host ("-" * 62) -ForegroundColor DarkCyan
}
function Write-OK($m)   { Write-Host "  [ok]  $m" -ForegroundColor Green }
function Write-Skip($m) { Write-Host "  [--]  $m" -ForegroundColor DarkGray }
function Write-Warn($m) { Write-Host "  [!!]  $m" -ForegroundColor Yellow }
function Write-Fail($m) { Write-Host "  [XX]  $m" -ForegroundColor Red }
function Write-Info($m) { Write-Host "        $m" -ForegroundColor DarkGray }

function Ask($prompt, $default = $null) {
    if ($default) {
        $a = Read-Host "$prompt [$default]"
        if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a }
    }
    return Read-Host $prompt
}
function Ask-YesNo($prompt, $defaultYes = $true) {
    $hint = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    $a = Read-Host "$prompt $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $defaultYes }
    return $a -match '^[yY]'
}

function New-RandomHex([int]$bytes = 32) {
    $b = New-Object byte[] $bytes
    (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b)
    return ($b | ForEach-Object { '{0:x2}' -f $_ }) -join ''
}
function New-RandomPin {
    # 6-digit numeric PIN (cryptographic randomness)
    $b = New-Object byte[] 6
    (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b)
    return ($b | ForEach-Object { ($_ % 10).ToString() }) -join ''
}
function Suggest-StoreId([string]$name) {
    $stop = @('and','the','of','a','an','for','&')
    $words = $name -split '\W+' | Where-Object {
        $_ -and ($stop -notcontains $_.ToLower())
    }
    $initials = ($words | ForEach-Object { $_.Substring(0,1) }) -join ''
    if ($initials.Length -ge 3) {
        return $initials.ToUpper().Substring(0, [Math]::Min(4, $initials.Length))
    }
    $first = ($name -split '\W+' | Where-Object { $_ })[0]
    if ($first) {
        $pad = $first.PadRight(3,'X')
        return $pad.ToUpper().Substring(0, 3)
    }
    return "NEW"
}
function Update-EnvLine([string]$path, [string]$key, [string]$value) {
    $found = $false
    $lines = Get-Content $path | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($key))=") {
            $found = $true
            "$key=$value"
        } else { $_ }
    }
    if (-not $found) { $lines += "$key=$value" }
    Set-Content -Path $path -Value $lines -Encoding UTF8
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "    PRINTOSKY  -  new-store installer (fully automated)" -ForegroundColor Cyan
Write-Host "    Repo root: $REPO_ROOT" -ForegroundColor DarkCyan
Write-Host "==============================================================" -ForegroundColor Cyan

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
    if ($LASTEXITCODE -ne 0) { throw "pip" }
    Write-OK "pip available"
} catch {
    Write-Fail "pip not working. Run: python -m ensurepip --upgrade"; exit 1
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
foreach ($d in @("C:\Printosky\Jobs\Incoming", "C:\Printosky\Jobs\Archive", "C:\Printosky\Data")) {
    if (Test-Path $d) { Write-Skip "$d already exists" }
    else { New-Item -ItemType Directory -Force -Path $d | Out-Null; Write-OK "created $d" }
}

# -----------------------------------------------------------------
# 3. SumatraPDF (always install if missing)
# -----------------------------------------------------------------
Write-Header "3. SumatraPDF (silent PDF dispatch)"
$sumatraDest = Join-Path $REPO_ROOT "SumatraPDF.exe"
if (Test-Path $sumatraDest) {
    $sz = [math]::Round((Get-Item $sumatraDest).Length / 1MB, 1)
    Write-Skip "already present ($sz MB)"
} else {
    $url = "https://www.sumatrapdfreader.org/dl/rel/3.6.1/SumatraPDF-3.6.1-64.zip"
    $zip = Join-Path $env:TEMP "sumatra_install.zip"
    $tmp = Join-Path $env:TEMP "sumatra_extract"
    Write-Info "Downloading SumatraPDF 3.6.1 portable from $url ..."
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $exe = Get-ChildItem -Path $tmp -Filter "*.exe" -Recurse | Select-Object -First 1
    Copy-Item -Path $exe.FullName -Destination $sumatraDest -Force
    Remove-Item -Force $zip; Remove-Item -Recurse -Force $tmp
    Write-OK "installed $sumatraDest"
}

# -----------------------------------------------------------------
# 4. Python dependencies
# -----------------------------------------------------------------
Write-Header "4. Python dependencies"
$reqFile = Join-Path $REPO_ROOT "requirements.txt"
if (Test-Path $reqFile) {
    Write-Info "running: pip install -r requirements.txt"
    & python -m pip install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) { Write-OK "requirements installed" } else { Write-Fail "pip install failed" }
} else {
    Write-Fail "requirements.txt not found at $reqFile"; exit 1
}

# -----------------------------------------------------------------
# 5. Detect HQ secrets source
# -----------------------------------------------------------------
Write-Header "5. HQ secrets source"
$envPath = Join-Path $REPO_ROOT ".env"
$hqCandidates = @(
    (Join-Path $REPO_ROOT "hq-secrets.env"),     # explicit HQ-provided file
    $envPath,                                     # already populated here
    "C:\printosky\.env",
    "C:\printosky_watcher\.env",
    "C:\PY\printosky\.env"
)
$hqSource = $hqCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
if (-not $hqSource) {
    Write-Fail "No HQ secrets source found. Checked:"
    foreach ($p in $hqCandidates) { Write-Host "          $p" -ForegroundColor DarkGray }
    Write-Host ""
    Write-Host "        Resolution: get a populated .env file from Printosky HQ" -ForegroundColor Yellow
    Write-Host "                    (contains META_*, SUPABASE_*, ANTHROPIC_API_KEY, hashes)." -ForegroundColor Yellow
    Write-Host "                    Save it as $REPO_ROOT\hq-secrets.env then re-run." -ForegroundColor Yellow
    exit 1
}
Write-OK "using $hqSource"
$sample = Get-Content $hqSource -Raw
$expected = @('META_APP_SECRET','SUPABASE_URL','SUPABASE_SERVICE_KEY','ANTHROPIC_API_KEY','ADMIN_PBKDF2_HASH')
$missing = $expected | Where-Object { $sample -notmatch "(?m)^$_=" }
if ($missing) {
    Write-Warn "Source is missing keys: $($missing -join ', ')"
    Write-Warn "Installer will continue but those keys will be blank in the new .env."
}

# -----------------------------------------------------------------
# 6. Location inputs (the only manual data)
# -----------------------------------------------------------------
Write-Header "6. Store location details (the only manual entry)"
$storeName = Ask "    Store name (e.g. 'Printosky Trivandrum')"
$suggested = Suggest-StoreId $storeName
$storeId = (Ask "    store_id (short code)" $suggested).ToUpper()
$city      = Ask "    City / location"      "Thrissur"
$konicaIp  = Ask "    Konica printer IP"     "192.168.55.110"
$epsonIp   = Ask "    Epson printer IP"      "192.168.55.202"
$konicaQ   = Ask "    Konica Windows print queue name" "KONICA MINOLTA 1100 PS"
$epsonQ    = Ask "    Epson Windows print queue name"  "WF-C21000 Series(Network)"
$waPhone   = Ask "    Store WhatsApp number (with country code, no +)" "919495706405"
$epsonUser = Ask "    Epson web admin username"        "Oxygen"
$epsonPass = Ask "    Epson web admin password (LAN-only printer)"
$hotFolder = "C:\Printosky\Jobs\Incoming"
$dbPath    = "C:\Printosky\Data\jobs.db"

# -----------------------------------------------------------------
# 7. Write store_config.json
# -----------------------------------------------------------------
Write-Header "7. store_config.json"
$cfgPath = Join-Path $REPO_ROOT "store_config.json"
$writeCfg = $true
if (Test-Path $cfgPath) {
    $existing = Get-Content $cfgPath -Raw
    if ($existing -match '"store_id"\s*:\s*"([^"]+)"') {
        Write-Skip "exists with store_id=$($Matches[1])"
    }
    $writeCfg = Ask-YesNo "Overwrite store_config.json with new $storeId values?" $false
}
if ($writeCfg) {
    $cfg = [ordered]@{
        store_id   = $storeId
        store_name = "$storeName, $city"
        printers   = [ordered]@{ konica_ip = $konicaIp; epson_ip = $epsonIp }
        hot_folder = $hotFolder
        db_path    = $dbPath
        printer_queue_names = [ordered]@{ konica = $konicaQ; epson = $epsonQ }
    }
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $cfgPath -Encoding UTF8
    Write-OK "wrote $cfgPath"
} else {
    Write-Skip "keeping existing store_config.json"
}

# -----------------------------------------------------------------
# 8. Build .env (auto-generated; HQ secrets copied as-is)
# -----------------------------------------------------------------
Write-Header "8. .env (auto-generated)"
# Resolve to absolute paths so a self-overwrite check works reliably
$hqSourceAbs = (Resolve-Path $hqSource).Path
$envPathAbs  = if (Test-Path $envPath) { (Resolve-Path $envPath).Path } else { $envPath }
$sourceIsSelf = $hqSourceAbs -eq $envPathAbs

$writeEnv = $true
if (Test-Path $envPath) {
    if ($sourceIsSelf) {
        Write-Skip "$envPath is also the HQ secrets source (in-place update mode)"
        $writeEnv = Ask-YesNo "Apply per-store overrides (STORE_TOKEN, STORE_WHATSAPP_PHONE, EPSON_*) to existing .env?" $false
    } else {
        Write-Skip "$envPath exists"
        $writeEnv = Ask-YesNo "Overwrite .env with new $storeId values?" $false
    }
}
if ($writeEnv) {
    if (-not $sourceIsSelf) {
        Copy-Item -Path $hqSource -Destination $envPath -Force
    }
    Update-EnvLine $envPath "STORE_TOKEN"          (New-RandomHex 32)
    Update-EnvLine $envPath "STORE_WHATSAPP_PHONE" $waPhone
    Update-EnvLine $envPath "EPSON_USER"           $epsonUser
    Update-EnvLine $envPath "EPSON_PASS"           $epsonPass
    if ($sourceIsSelf) {
        Write-OK "updated $envPath (STORE_TOKEN rotated, per-store keys set)"
    } else {
        Write-OK "wrote $envPath (STORE_TOKEN auto-generated, per-store keys set)"
    }
} else {
    Write-Skip "keeping existing .env"
}

# -----------------------------------------------------------------
# 9. Bootstrap SQLite schema
# -----------------------------------------------------------------
Write-Header "9. Bootstrap jobs.db schema"
$bootstrapDb = Join-Path $PSScriptRoot "bootstrap_db.py"
if (-not (Test-Path $bootstrapDb)) {
    Write-Fail "bootstrap_db.py not found at $bootstrapDb"; exit 1
}
Push-Location $REPO_ROOT
& python $bootstrapDb
$dbExit = $LASTEXITCODE
Pop-Location
if ($dbExit -eq 0) { Write-OK "schema applied" } else { Write-Warn "bootstrap_db.py returned non-zero" }

# -----------------------------------------------------------------
# 10. Staff: seed + auto-rotate PINs to random 6-digit values
# -----------------------------------------------------------------
Write-Header "10. Staff PINs (auto-generated)"
$staffSetup = Join-Path $REPO_ROOT "staff_setup.py"
if (-not (Test-Path $staffSetup)) {
    Write-Warn "staff_setup.py not found - skip"
} else {
    Push-Location $REPO_ROOT
    & python staff_setup.py 2>&1 | Out-Null   # seed defaults (idempotent)
    $defaultIds = @("priya","revana","bini","anu","deepak")
    $pinsFile = Join-Path $REPO_ROOT ".staff_pins_first_login.txt"
    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $banner = @(
        "# Printosky staff PINs - first login",
        "# Generated $now for store_id=$storeId",
        "# Keep this file safe. Hand each PIN to its owner privately.",
        "# Delete this file once everyone has logged in and changed their PIN.",
        ""
    )
    $banner | Set-Content -Path $pinsFile -Encoding UTF8
    foreach ($id in $defaultIds) {
        $pin = New-RandomPin
        & python staff_setup.py reset-pin $id $pin 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            "$id : $pin" | Add-Content -Path $pinsFile
        } else {
            "$id : (reset failed)" | Add-Content -Path $pinsFile
        }
    }
    Pop-Location
    Write-OK "5 staff PINs auto-generated"
    Write-Info "PINs saved to $pinsFile (gitignored)"
    Write-Info "Hand each PIN to its owner privately, then delete the file."
}

# -----------------------------------------------------------------
# 11. Autostart on PC boot
# -----------------------------------------------------------------
Write-Header "11. Autostart on PC boot"
$startBat = Join-Path $REPO_ROOT "START_PRINTOSKY.bat"
if (-not (Test-Path $startBat)) {
    Write-Warn "START_PRINTOSKY.bat not found - skip"
} else {
    $startupDir = [Environment]::GetFolderPath('Startup')
    $shortcut = Join-Path $startupDir "Printosky.lnk"
    if (Test-Path $shortcut) {
        Write-Skip "Startup shortcut already in place: $shortcut"
    } else {
        try {
            $wshell = New-Object -ComObject WScript.Shell
            $sc = $wshell.CreateShortcut($shortcut)
            $sc.TargetPath = $startBat
            $sc.WorkingDirectory = $REPO_ROOT
            $sc.WindowStyle = 7   # minimized
            $sc.Save()
            Write-OK "created $shortcut (user-scope autostart; no admin needed)"
        } catch {
            Write-Warn "Could not create Startup shortcut: $_"
            Write-Info "Fallback: right-click SETUP_AUTOSTART.bat -> Run as administrator"
        }
    }
}

# -----------------------------------------------------------------
# 12. Verify
# -----------------------------------------------------------------
Write-Header "12. Verify"
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
if ($sumatraFound) { Write-OK "SumatraPDF resolvable at: $sumatraFound" } else { Write-Warn "SumatraPDF NOT findable" }

$queues = (Get-Printer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$cfgRaw = Get-Content $cfgPath -Raw | ConvertFrom-Json
foreach ($p in @("konica", "epson")) {
    $qName = $cfgRaw.printer_queue_names.$p
    if ($queues -contains $qName) { Write-OK "Windows print queue for $p found: '$qName'" }
    else { Write-Warn "Print queue for $p NOT found: '$qName'" }
}
foreach ($p in @("konica", "epson")) {
    $ip = $cfgRaw.printers."${p}_ip"
    if ($ip -eq "127.0.0.1") { Write-Skip "$p IP is loopback (test store)"; continue }
    $reach = Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue
    if ($reach) { Write-OK "$p reachable at $ip" } else { Write-Warn "$p NOT reachable at $ip" }
}

# -----------------------------------------------------------------
# 13. Summary
# -----------------------------------------------------------------
Write-Header "13. Done"
Write-Host @"

  Installation complete for store_id = $storeId

  Generated files:
    $cfgPath
    $envPath
    $REPO_ROOT\.staff_pins_first_login.txt   (hand PINs out, then delete)

  Autostart:
    user Startup folder -> Printosky.lnk (services launch on next PC boot)

  To start the services NOW without rebooting:
    Double-click  $startBat

  Nothing else to fill in - the installer wrote everything.

"@ -ForegroundColor Gray
