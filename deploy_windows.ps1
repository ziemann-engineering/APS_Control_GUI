param(
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = 'Stop'

$REPO_URL  = 'https://github.com/veloyage/Python-Software.git'
$REPO_ZIP  = 'https://github.com/veloyage/Python-Software/archive/refs/heads/main.zip'

# ---------------------------------------------------------------------------
# 1. Clone or update the repository
# ---------------------------------------------------------------------------
if (Get-Command git -ErrorAction SilentlyContinue) {
    if (Test-Path -LiteralPath '.git') {
        Write-Host 'Git repository found — pulling latest changes...'
        & git pull --ff-only
    }
    else {
        Write-Host 'Cloning repository...'
        & git clone $REPO_URL .
    }
}
else {
    Write-Host 'Git not found — falling back to ZIP download...'
    $zipFile = Join-Path $env:TEMP 'aps_software.zip'
    $extractDir = Join-Path $env:TEMP 'aps_software_zip'

    Invoke-WebRequest -Uri $REPO_ZIP -OutFile $zipFile -UseBasicParsing
    Expand-Archive -Path $zipFile -DestinationPath $extractDir -Force

    # The ZIP contains a single top-level folder; copy its contents here
    $inner = Get-ChildItem -LiteralPath $extractDir -Directory | Select-Object -First 1
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $PWD -Recurse -Force

    Remove-Item $zipFile, $extractDir -Recurse -Force
    Write-Host 'ZIP extraction complete.'
}

# ---------------------------------------------------------------------------
# 2. Python version check (requires 3.8+)
# ---------------------------------------------------------------------------
$pyCmd = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$pyVersion = & $pyCmd -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))' 2>&1
if (-not ($pyVersion -match '^(3\.(8|9|[1-9][0-9]))')) {
    Write-Error "Python 3.8 or later is required (found: $pyVersion)."
    exit 1
}
Write-Host "Python $pyVersion detected."

# ---------------------------------------------------------------------------
# 3. Create or reuse the virtual environment
# ---------------------------------------------------------------------------
$pythonExe = Join-Path $VenvPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'Creating virtual environment...'
    & $pyCmd -m venv $VenvPath
}

# ---------------------------------------------------------------------------
# 4. Install / update Python dependencies
# ---------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath 'requirements.txt')) {
    Write-Error 'requirements.txt not found in the current directory.'
    exit 1
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

# ---------------------------------------------------------------------------
# 5. Smoke-test key imports
# ---------------------------------------------------------------------------
Write-Host 'Verifying key packages...'
& $pythonExe -c 'import PyQt5; import pyqtgraph; import pymeasure; import pyvisa' 2>&1 | Tee-Object -Variable importResult
if ($LASTEXITCODE -ne 0) {
    Write-Error "Package verification failed. See output above."
    exit 1
}

Write-Host ''
Write-Host 'Windows deployment complete.'
Write-Host "Activate the environment with: .\$VenvPath\Scripts\Activate.ps1"
Write-Host "Launch with:                  .\$VenvPath\Scripts\python.exe 'APS GUI.py'"