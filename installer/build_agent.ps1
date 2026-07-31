#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the ClassroomOS agent into a standalone Windows executable.

.DESCRIPTION
    Produces installer\dist\ClassroomOSAgent.exe, which runs on a client PC
    with no Python installed. Deploy it with:

        .\install_agent.ps1 -ConsoleIP "..." -SharedSecret "..." -UseExe

    Why the long --hidden-import list:
    the agent loads everything under agent\handlers\ through
    importlib.import_module() so that a missing optional dependency disables
    one feature instead of stopping the service. PyInstaller's static analysis
    cannot see those imports, so each handler has to be named explicitly or the
    built .exe starts fine and then reports "<x> handler unavailable" for
    everything.

    The same binary also hosts the in-session UI helper: when launched with
    --ui-host it re-executes as agent_ui (see agent\agent_main.py), which is why
    no second executable is needed.

.PARAMETER Clean
    Remove build\, dist\ and the generated .spec before building.

.PARAMETER OneDir
    Build a folder instead of a single file. Starts faster and is easier to
    debug; prefer it if antivirus quarantines the one-file build.

.EXAMPLE
    .\build_agent.ps1 -Clean
#>

param(
    [switch]$Clean,
    [switch]$OneDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$Root      = Split-Path $ScriptDir -Parent
$AgentDir  = Join-Path $Root "agent"
$SharedDir = Join-Path $Root "shared"
$WorkDir   = Join-Path $ScriptDir "build"
$DistDir   = Join-Path $ScriptDir "dist"
$ExeName   = "ClassroomOSAgent"

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  ClassroomOS Agent Build"              -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check PyInstaller ────────────────────────────────────────────────
Write-Host "[1/4] Checking PyInstaller..." -ForegroundColor Yellow
python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Not found — installing..." -ForegroundColor Gray
    python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not install PyInstaller." }
}
$pyiVersion = (python -m PyInstaller --version)
Write-Host "    PyInstaller $pyiVersion" -ForegroundColor Green

# ── Step 2: Clean ────────────────────────────────────────────────────────────
if ($Clean) {
    Write-Host "[2/4] Cleaning previous build..." -ForegroundColor Yellow
    foreach ($p in @($WorkDir, $DistDir)) {
        if (Test-Path $p) { Remove-Item $p -Recurse -Force }
    }
    Get-ChildItem $ScriptDir -Filter "*.spec" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    Write-Host "    Cleaned" -ForegroundColor Green
} else {
    Write-Host "[2/4] Skipping clean (pass -Clean to force)" -ForegroundColor Gray
}

# ── Step 3: Build ────────────────────────────────────────────────────────────
Write-Host "[3/4] Building $ExeName.exe..." -ForegroundColor Yellow

# Every module under agent\handlers\ is imported dynamically at runtime.
$handlers = Get-ChildItem (Join-Path $AgentDir "handlers") -Filter "*.py" |
    Where-Object { $_.BaseName -ne "__init__" } |
    ForEach-Object { "handlers.$($_.BaseName)" }

Write-Host "    Bundling handlers: $($handlers -join ', ')" -ForegroundColor Gray

$pyiArgs = @(
    "--name", $ExeName,
    "--noconfirm",
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $ScriptDir,
    # agent\ and shared\ must both be importable inside the bundle.
    "--paths", $AgentDir,
    "--paths", $SharedDir
)

if ($OneDir) { $pyiArgs += "--onedir" } else { $pyiArgs += "--onefile" }

# Dynamically loaded handlers.
foreach ($h in $handlers) { $pyiArgs += @("--hidden-import", $h) }

# Imported behind runtime conditions or only on the service path.
foreach ($m in @(
    "agent_ui", "ui_bridge", "ui_commands", "scheduler", "scheduling",
    "session", "handler_loader", "protocol",
    "win32timezone",            # pywin32 needs this at service start
    "win32serviceutil", "win32service", "win32event", "servicemanager"
)) { $pyiArgs += @("--hidden-import", $m) }

$pyiArgs += (Join-Path $AgentDir "agent_main.py")

python -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller build failed." }

# ── Step 4: Verify ───────────────────────────────────────────────────────────
Write-Host "[4/4] Verifying output..." -ForegroundColor Yellow

$ExePath = if ($OneDir) {
    Join-Path $DistDir "$ExeName\$ExeName.exe"
} else {
    Join-Path $DistDir "$ExeName.exe"
}

if (-not (Test-Path $ExePath)) {
    Write-Error "Build reported success but $ExePath is missing."
}

$sizeMb = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
Write-Host "    $ExePath ($sizeMb MB)" -ForegroundColor Green

# Ship a default config next to the executable so the installer can overwrite
# just the secret rather than inventing the whole file.
$TemplateConfig = Join-Path $AgentDir "config.json"
if (Test-Path $TemplateConfig) {
    Copy-Item $TemplateConfig (Join-Path $DistDir "config.json") -Force
}

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Build complete!" -ForegroundColor Green
Write-Host "  Executable: $ExePath" -ForegroundColor Gray
Write-Host ""
Write-Host "  Next: test it on a clean Windows VM with no Python:" -ForegroundColor Gray
Write-Host "    $ExeName.exe            # run in the foreground" -ForegroundColor DarkGray
Write-Host "    .\install_agent.ps1 -ConsoleIP <ip> -SharedSecret <secret> -UseExe" -ForegroundColor DarkGray
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""
