<#
.SYNOPSIS
    Install Planning Brain for the current user. No Python, no Rust, no network.

.DESCRIPTION
    Copies the built command into %LOCALAPPDATA%\Programs\PlanningBrain, puts it
    on the user's PATH, and adds a Start Menu shortcut.

    **Why this exists.** The desktop bundle needs a Rust toolchain to compile the
    Tauri shell, and docs/status.md records that the two-installer path has never
    completed a green run. This installs the same engine as a command, on a
    machine that has neither Rust nor Python.

    Per-user on purpose: no administrator rights, nothing written outside the
    user's own profile, and uninstalling is deleting one folder. A planner
    evaluating a tool should not need their IT department first.

    It never touches the planning database. That lives where docs/install.md
    says it does, and uninstalling deliberately leaves it there -- the promise is
    that your data is in a file you can see, copy and delete yourself.

.PARAMETER Source
    The built tree, normally build\dist\planbrain-cli.

.PARAMETER Uninstall
    Remove the program folder, the PATH entry and the shortcut. Leaves data.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [string] $Source,
    [switch] $Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$AppName   = 'Planning Brain'
$Target    = Join-Path $env:LOCALAPPDATA 'Programs\PlanningBrain'
$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$Shortcut  = Join-Path $StartMenu "$AppName.lnk"

function Remove-FromUserPath {
    param([string] $Directory)
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $current) { return }
    # Split and rebuild rather than string-replace: a substring match would
    # corrupt a different entry that merely starts with the same characters.
    $kept = $current.Split(';') | Where-Object { $_ -and ($_.TrimEnd('\') -ne $Directory.TrimEnd('\')) }
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
}

if ($Uninstall) {
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
        Write-Host "Removed $Target"
    } else {
        Write-Host "Nothing installed at $Target"
    }
    if (Test-Path -LiteralPath $Shortcut) { Remove-Item -LiteralPath $Shortcut -Force }
    Remove-FromUserPath -Directory $Target
    Write-Host ""
    Write-Host "Uninstalled. Your planning database was NOT removed -- it is yours."
    Write-Host "Run 'planbrain where' before uninstalling if you want its path."
    exit 0
}

if (-not $Source) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
    $Source = Join-Path $repoRoot 'build\dist\planbrain-cli'
}
if (-not (Test-Path -LiteralPath $Source)) {
    throw "No build found at $Source. Build it first:`n" +
          "    pyinstaller packaging\cli.spec --distpath build\dist --noconfirm"
}
$exe = Join-Path $Source 'planbrain.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    # A directory that exists but holds no program is the failure most likely to
    # be mistaken for success, so it is named rather than left to the copy.
    throw "$Source exists but contains no planbrain.exe, so there is nothing to install."
}

Write-Host "Installing $AppName for $env:USERNAME"
Write-Host "  from $Source"
Write-Host "  to   $Target"

if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force

$installed = Join-Path $Target 'planbrain.exe'
if (-not (Test-Path -LiteralPath $installed)) {
    throw "The copy finished but $installed is missing. Nothing was added to PATH."
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$already = $userPath -and ($userPath.Split(';') | Where-Object { $_.TrimEnd('\') -eq $Target.TrimEnd('\') })
if (-not $already) {
    $updated = if ($userPath) { "$userPath;$Target" } else { $Target }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
    Write-Host "  added to your PATH"
} else {
    Write-Host "  already on your PATH"
}

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($Shortcut)
$link.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$link.Arguments = "-NoExit -Command `"Write-Host 'Planning Brain. Try: planbrain demo, planbrain plan, planbrain orders'`""
$link.WorkingDirectory = $Target
$link.Description = 'Supply chain planning that runs on your own machine'
$link.Save()
Write-Host "  Start Menu shortcut created"

# Prove the installed copy runs, here, now. An installer that reports success
# without starting the program is how a broken build reaches a user.
Write-Host ""
Write-Host "Checking the installed copy actually runs..."
$check = & $installed where 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Installed, but '$installed where' exited $LASTEXITCODE and said: $check"
}
Write-Host $check

Write-Host ""
Write-Host "Done. Open a NEW terminal (PATH changes do not reach open ones), then:"
Write-Host "    planbrain demo      build the worked example"
Write-Host "    planbrain plan      run the plan"
Write-Host "    planbrain orders    what to order"
Write-Host ""
Write-Host "To remove it later:  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall"
