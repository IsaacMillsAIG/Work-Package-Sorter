# ============================================================
#   Work Package Sorter — Silent Installer
#   Downloads and sets up everything automatically.
# ============================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ── CONFIGURATION — update these before sharing ─────────────
$GITHUB_USER   = "IsaacMillsAIG"        # e.g. "isaacm"
$GITHUB_REPO   = "Work-Package-Sorter"         # your repo name
$GITHUB_BRANCH = "main"
$INSTALL_DIR   = "$env:LOCALAPPDATA\WorkPackageSorter"
$APP_NAME      = "Work Package Sorter"
# ────────────────────────────────────────────────────────────

$REPO_ZIP_URL  = "https://github.com/$GITHUB_USER/$GITHUB_REPO/archive/refs/heads/$GITHUB_BRANCH.zip"
$REPO_FOLDER   = "$GITHUB_REPO-$GITHUB_BRANCH"

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}

function Test-Command($cmd) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

Clear-Host
Write-Host "============================================================" -ForegroundColor Blue
Write-Host "  $APP_NAME — Installer" -ForegroundColor Blue
Write-Host "============================================================" -ForegroundColor Blue
Write-Host ""
Write-Host "This will download and install the app automatically."
Write-Host "It may take a few minutes on first run."
Write-Host ""

# ── Step 1: Check/Install Node.js ────────────────────────────
Write-Step "1/5" "Checking Node.js..."
if (-not (Test-Command "node")) {
    Write-Host "  Node.js not found — downloading installer..." -ForegroundColor Yellow
    $nodeMsi = "$env:TEMP\node_installer.msi"
    # Get latest LTS version
    $nodeVersion = (Invoke-RestMethod "https://nodejs.org/dist/index.json" | 
        Where-Object { $_.lts } | Select-Object -First 1).version
    $nodeUrl = "https://nodejs.org/dist/$nodeVersion/node-$nodeVersion-x64.msi"
    Write-Host "  Downloading Node.js $nodeVersion..."
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeMsi
    Write-Host "  Installing Node.js (this takes ~1 minute)..."
    Start-Process msiexec.exe -ArgumentList "/i `"$nodeMsi`" /qn /norestart" -Wait
    Remove-Item $nodeMsi -Force
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Write-Host "  Node.js installed." -ForegroundColor Green
} else {
    $nodeVer = (node --version)
    Write-Host "  Node.js $nodeVer already installed." -ForegroundColor Green
}

# ── Step 2: Check/Install Python ─────────────────────────────
Write-Step "2/5" "Checking Python..."
$pythonOk = (Test-Command "py") -or (Test-Command "python")
if (-not $pythonOk) {
    Write-Host "  Python not found — downloading installer..." -ForegroundColor Yellow
    $pyUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
    $pyInstaller = "$env:TEMP\python_installer.exe"
    Write-Host "  Downloading Python 3.12..."
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller
    Write-Host "  Installing Python silently..."
    Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1" -Wait
    Remove-Item $pyInstaller -Force
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Write-Host "  Python installed." -ForegroundColor Green
} else {
    Write-Host "  Python already installed." -ForegroundColor Green
}

# ── Step 3: Download app from GitHub ─────────────────────────
Write-Step "3/5" "Downloading $APP_NAME from GitHub..."
if (Test-Path $INSTALL_DIR) {
    Write-Host "  Removing previous installation..."
    Remove-Item $INSTALL_DIR -Recurse -Force
}
New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null

$zipPath = "$env:TEMP\wps_source.zip"
Write-Host "  Downloading from $REPO_ZIP_URL..."
Invoke-WebRequest -Uri $REPO_ZIP_URL -OutFile $zipPath
Write-Host "  Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $env:TEMP -Force
Remove-Item $zipPath -Force

# Move extracted folder to install dir
$extractedPath = "$env:TEMP\$REPO_FOLDER"
Copy-Item "$extractedPath\*" -Destination $INSTALL_DIR -Recurse -Force
Remove-Item $extractedPath -Recurse -Force
Write-Host "  Downloaded." -ForegroundColor Green

# ── Step 4: Build sorter.exe from Python script ───────────────
Write-Step "4/5" "Building sorter engine (first time only — takes ~2 minutes)..."
Set-Location $INSTALL_DIR

# Install Python dependencies + PyInstaller
Write-Host "  Installing Python packages..."
$pipCmd = if (Test-Command "py") { "py -m pip" } else { "python -m pip" }
Invoke-Expression "$pipCmd install pyinstaller pdfplumber pypdf pyyaml --quiet"

# Compile to exe
Write-Host "  Compiling work_package_sorter.py..."
$pyExe = if (Test-Command "py") { "py" } else { "python" }
& $pyExe -m PyInstaller --onefile --name sorter --distpath $INSTALL_DIR `
    --workpath "$INSTALL_DIR\build_tmp" --specpath "$INSTALL_DIR\build_tmp" `
    --noconfirm "$INSTALL_DIR\work_package_sorter.py" 2>&1 | Out-Null

if (-not (Test-Path "$INSTALL_DIR\sorter.exe")) {
    Write-Host "ERROR: Failed to build sorter.exe" -ForegroundColor Red
    pause; exit 1
}
# Clean up build artifacts
if (Test-Path "$INSTALL_DIR\build_tmp") { Remove-Item "$INSTALL_DIR\build_tmp" -Recurse -Force }
if (Test-Path "$INSTALL_DIR\work_package_sorter.py") { } # keep it for reference
Write-Host "  sorter.exe ready." -ForegroundColor Green

# ── Step 5: Build Electron app ────────────────────────────────
Write-Step "5/5" "Building app..."
Set-Location $INSTALL_DIR

Write-Host "  Installing Node dependencies..."
& npm install --silent 2>&1 | Out-Null

Write-Host "  Building app (takes ~1 minute)..."
& npm run build 2>&1 | Out-Null

$exePath = "$INSTALL_DIR\dist\win-unpacked\$APP_NAME.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "ERROR: App build failed." -ForegroundColor Red
    pause; exit 1
}
Write-Host "  App built." -ForegroundColor Green

# ── Create desktop shortcut ───────────────────────────────────
$shortcutPath = "$env:USERPROFILE\Desktop\$APP_NAME.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath
$shortcut.Description = "Steel Fabrication Drawing Work Package Sorter"
$shortcut.Save()

# ── Done ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "  A shortcut has been added to your Desktop." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# Launch the app
$launch = Read-Host "Launch Work Package Sorter now? (Y/N)"
if ($launch -match "^[Yy]") {
    Start-Process $exePath
}
