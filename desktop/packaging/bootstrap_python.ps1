# Installs a supported CPython when the machine has none.
#
# installer.py cannot do this: it is itself a Python program, so it can never
# bootstrap the interpreter it already runs on. On a clean Windows 11 there is
# no Python at all - only a zero-byte Microsoft Store stub named python.exe -
# and the `min` build's whole promise is "run INSTALL.bat once". This script is
# what makes that promise true.
#
# Prints the resulting interpreter path as the ONLY stdout line, so INSTALL.bat
# can capture it with `for /f`. Progress goes to STDERR on purpose: `for /f`
# swallows stdout, and with Write-Host the recipient stared at a frozen console
# for the whole download with no sign anything was happening.
[CmdletBinding()]
param(
    # python.org keeps every release, so this URL stays valid; 3.11 is the version
    # both READMEs recommend and the one the pinned stack is built against.
    [string] $Version = "3.11.9",
    # Report what is already here and stop. The self-test runs this: probing must
    # never be able to hang or to install anything as a side effect.
    [switch] $ProbeOnly
)

$ErrorActionPreference = "Stop"

function Say([string] $Message) { [Console]::Error.WriteLine($Message) }
# installer.py's own MIN_PYTHON/MAX_PYTHON. Anything outside is refused there
# anyway, so installing it would only move the failure one step later.
$MIN = [Version]"3.9"
$MAX = [Version]"3.13"      # exclusive: 3.12.x is the highest supported

function Get-Candidates {
    # Pairs, not strings: an absolute path can contain spaces, and splitting a
    # command line on " " would tear it in half.
    $list = @(
        @{ Exe = "py"; Args = @("-3.11") }, @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.10") }, @{ Exe = "py"; Args = @("-3.9") },
        @{ Exe = "python"; Args = @() }, @{ Exe = "python3"; Args = @() }
    )
    # PATH alone is not enough. PrependPath only updates the registry, so every
    # process started BEFORE the install - including explorer.exe, and therefore
    # the console a user opens next - still has the old PATH and sees nothing.
    # Running INSTALL.bat a second time then offered to install Python again.
    # Look where a per-user install actually puts it, and at what we recorded.
    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python")) {
        if (Test-Path $root) {
            foreach ($d in Get-ChildItem $root -Directory -ErrorAction SilentlyContinue) {
                $exe = Join-Path $d.FullName "python.exe"
                if (Test-Path $exe) { $list += @{ Exe = $exe; Args = @() } }
            }
        }
    }
    $recorded = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "config\interpreter.txt"
    if (Test-Path $recorded) {
        $p = (Get-Content $recorded -Raw).Trim()
        if ($p -and (Test-Path $p)) { $list += @{ Exe = $p; Args = @() } }
    }
    return $list
}

function Find-Supported {
    $seen = [System.Collections.Generic.List[string]]::new()
    $probe = "import sys;print(sys.version_info[0],sys.version_info[1],sys.executable,sep='|')"
    foreach ($cand in Get-Candidates) {
        $exe = $cand.Exe
        $rest = $cand.Args
        try {
            # </dev/null equivalent: nothing here may ever block on input.
            $out = "" | & $exe @rest -c $probe 2>$null
        } catch { continue }
        if (-not $out) { continue }
        $f = "$out".Trim().Split("|")
        if ($f.Count -lt 3) { continue }
        $v = [Version]"$($f[0]).$($f[1])"
        if ($v -ge $MIN -and $v -lt $MAX) { return $f[2] }
        $seen.Add("$v")
    }
    if ($seen.Count) { Say "  Found Python $($seen -join ', ') - not supported here." }
    return $null
}

$found = Find-Supported
if ($found) { Write-Output $found; exit 0 }
if ($ProbeOnly) { exit 1 }

Say "  Installing Python $Version ..."

# Straight from python.org, deliberately NOT via winget: with its output
# redirected (which `for /f` forces) winget sat for 8 minutes on a clean VM
# without installing anything or printing a reason. This path has no first-run
# source sync, no interactivity, and we verify the download ourselves.
#
# The check is the Authenticode signature rather than a hardcoded hash: it also
# covers a mirror or a tampered proxy, and it does not go stale when a new
# 3.11.x is published.
$url = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
$exe = Join-Path $env:TEMP "python-$Version-amd64.exe"
Say "  downloading $url"
# The progress bar makes Invoke-WebRequest an order of magnitude slower and it
# renders as noise once stderr is the progress channel.
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing -TimeoutSec 600
Say "  downloaded $([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB"

$sig = Get-AuthenticodeSignature $exe
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Python Software Foundation") {
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    throw "Downloaded installer is not signed by the Python Software Foundation (status: $($sig.Status)). Nothing was installed."
}

Say "  signature OK ($($sig.SignerCertificate.Subject.Split(',')[0])), running the installer"
# Everything here must stay per-user. InstallLauncherAllUsers defaults to 1, which
# puts py.exe in C:\Windows and needs admin: under /quiet the elevation request has
# nowhere to appear, so the installer waited on an invisible consent.exe forever -
# three of them were queued up on the test VM before this was found.
$p = Start-Process $exe -PassThru -ArgumentList @(
    "/quiet", "InstallAllUsers=0", "PrependPath=1",
    "Include_launcher=1", "InstallLauncherAllUsers=0",
    "Include_pip=1", "Include_test=0"
)
try {
    # And a hard stop, so a future prompt-behind-the-curtain is a clear error
    # rather than a console that never comes back.
    $p | Wait-Process -Timeout 900 -ErrorAction Stop
} catch {
    try { $p.Kill() } catch { }
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    throw "The Python installer did not finish within 15 minutes. Install Python 3.11 from python.org by hand and run INSTALL.bat again."
}
Remove-Item $exe -Force -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0) { throw "Python installer exited with code $($p.ExitCode)." }
Say "  installed, locating the interpreter"

$found = Find-Supported
if (-not $found) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $candidate) { $found = $candidate }
}
if (-not $found) { throw "Python was installed but could not be located afterwards." }
Write-Output $found
exit 0
