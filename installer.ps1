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

$REPO_ZIP_URL = "https://github.com/$GITHUB_USER/$GITHUB_REPO/archive/refs/heads/$GITHUB_BRANCH.zip"
$REPO_FOLDER  = "$GITHUB_REPO-$GITHUB_BRANCH"
 
function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}
 
function Test-Cmd($cmd) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $result = Get-Command $cmd 2>$null
    $ErrorActionPreference = $old
    return ($result -ne $null)
}
 
function Run-Cmd {
    param($exe, $argList)
    $p = Start-Process -FilePath $exe -ArgumentList $argList -Wait -PassThru -NoNewWindow
    return $p.ExitCode
}
 
Clear-Host
Write-Host "============================================================"
Write-Host "  $APP_NAME - Installer"
Write-Host "============================================================"
Write-Host ""
Write-Host "This will download and install the app automatically."
Write-Host "It may take a few minutes on first run."
Write-Host ""
 
# Step 1: Node.js
Write-Step "1/5" "Checking Node.js..."
if (-not (Test-Cmd "node")) {
    Write-Host "  Node.js not found - downloading..." -ForegroundColor Yellow
    $nodeMsi = "$env:TEMP\node_installer.msi"
    $indexJson = Invoke-RestMethod "https://nodejs.org/dist/index.json"
    $nodeVersion = ($indexJson | Where-Object { $_.lts } | Select-Object -First 1).version
    $nodeUrl = "https://nodejs.org/dist/$nodeVersion/node-$nodeVersion-x64.msi"
    Write-Host "  Downloading Node.js $nodeVersion..."
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeMsi
    Write-Host "  Installing Node.js..."
    Run-Cmd "msiexec.exe" "/i `"$nodeMsi`" /qn /norestart" | Out-Null
    Remove-Item $nodeMsi -Force -ErrorAction SilentlyContinue
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-Host "  Node.js installed." -ForegroundColor Green
} else {
    $v = & node --version
    Write-Host "  Node.js $v found." -ForegroundColor Green
}
 
# Step 2: Python
Write-Step "2/5" "Checking Python..."
$hasPy = (Test-Cmd "py") -or (Test-Cmd "python")
if (-not $hasPy) {
    Write-Host "  Python not found - downloading..." -ForegroundColor Yellow
    $pyUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
    $pyExe = "$env:TEMP\python_installer.exe"
    Write-Host "  Downloading Python 3.12..."
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyExe
    Write-Host "  Installing Python..."
    Run-Cmd $pyExe "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1" | Out-Null
    Remove-Item $pyExe -Force -ErrorAction SilentlyContinue
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-Host "  Python installed." -ForegroundColor Green
} else {
    Write-Host "  Python found." -ForegroundColor Green
}
 
# Find python executable
$pyExePath = ""
if (Test-Cmd "py") {
    $pyExePath = (Get-Command "py").Source
} elseif (Test-Cmd "python") {
    $pyExePath = (Get-Command "python").Source
}
 
# Step 3: Download from GitHub
Write-Step "3/5" "Downloading app from GitHub..."
if (Test-Path $INSTALL_DIR) {
    Write-Host "  Removing previous install..."
    Remove-Item $INSTALL_DIR -Recurse -Force
}
New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
 
$zipPath = "$env:TEMP\wps_source.zip"
Write-Host "  Downloading from GitHub..."
Invoke-WebRequest -Uri $REPO_ZIP_URL -OutFile $zipPath
Write-Host "  Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $env:TEMP -Force
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
 
$extractedPath = "$env:TEMP\$REPO_FOLDER"
Copy-Item "$extractedPath\*" -Destination $INSTALL_DIR -Recurse -Force
Remove-Item $extractedPath -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  Downloaded." -ForegroundColor Green
 
# Step 4: Build sorter.exe
Write-Step "4/5" "Building sorter engine (takes 2-3 minutes on first run)..."
Set-Location $INSTALL_DIR
 
Write-Host "  Installing Python packages..."
$pipArgs = "-m pip install pyinstaller pdfplumber pypdf pyyaml --quiet"
$exitCode = Run-Cmd $pyExePath $pipArgs
if ($exitCode -ne 0) {
    Write-Host "  pip install failed (exit $exitCode) - retrying without --quiet..."
    Run-Cmd $pyExePath "-m pip install pyinstaller pdfplumber pypdf pyyaml" | Out-Null
}
 
Write-Host "  Compiling sorter..."
$pyiArgs = "-m PyInstaller --onefile --name sorter --distpath `"$INSTALL_DIR`" --workpath `"$INSTALL_DIR\build_tmp`" --specpath `"$INSTALL_DIR\build_tmp`" --noconfirm `"$INSTALL_DIR\work_package_sorter.py`""
Run-Cmd $pyExePath $pyiArgs | Out-Null
 
if (-not (Test-Path "$INSTALL_DIR\sorter.exe")) {
    Write-Host "  ERROR: Failed to build sorter.exe" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
 
if (Test-Path "$INSTALL_DIR\build_tmp") {
    Remove-Item "$INSTALL_DIR\build_tmp" -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "  sorter.exe ready." -ForegroundColor Green
 
# Step 5: Build Electron app
Write-Step "5/5" "Building app..."
Set-Location $INSTALL_DIR
 
Write-Host "  Installing Node dependencies..."
$npmPath = (Get-Command "npm" -ErrorAction SilentlyContinue).Source
if (-not $npmPath) {
    $npmPath = "$env:ProgramFiles
odejs
pm.cmd"
}
cmd /c "cd /d `"$INSTALL_DIR`" && npm install" | Out-Null
 
Write-Host "  Building Electron app..."
cmd /c "cd /d `"$INSTALL_DIR`" && npm run build" | Out-Null
 
$exePath = "$INSTALL_DIR\dist\win-unpacked\$APP_NAME.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "  ERROR: App build failed." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  App built." -ForegroundColor Green
 
# Desktop shortcut
$shortcutPath = "$env:USERPROFILE\Desktop\$APP_NAME.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath
$shortcut.Description = "Steel Fabrication Drawing Work Package Sorter"
$shortcut.Save()
 
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "  A shortcut has been added to your Desktop." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
 
$launch = Read-Host "Launch Work Package Sorter now? (Y/N)"
if ($launch -match "^[Yy]") {
    Start-Process $exePath
}