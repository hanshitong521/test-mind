# TestMind local dev environment bootstrap (idempotent, service-first).
#
# Brings up the stack in dependency order, starting only what is NOT already up:
#   1) docker-vm         (VirtualBox headless) -> linux/amd64 engine on 127.0.0.1:2375
#   2) TestMindMySQL     (Windows service, Session 0, port 3307)
#   3) TestMindDashboard (Windows service, Session 0, port 8901)
#
# Why services and not bare processes: anything started with Start-Process from
# an agent session is reaped when that session ends (verified: a detached
# mysqld came up at 08:10:30 and was gone by 08:11:11 with empty stderr).
# Windows services run in Session 0 and survive both session end and logoff.
#
# Safe to run repeatedly. Logs to %TEMP%\testmind-devup.log.
#
# Run: powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1
$ErrorActionPreference = "Continue"
$log = Join-Path $env:TEMP "testmind-devup.log"
function Log($m) {
    ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) |
        Out-File -FilePath $log -Append -Encoding utf8
}

$VBoxManage = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"

function Test-Port([int]$Port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port)
        $c.Close()
        return $true
    } catch { return $false }
}

function Start-Svc([string]$Name, [int]$Port) {
    if (Test-Port $Port) { Log "$Name : port $Port already up"; return $true }
    $svc = Get-Service $Name -ErrorAction SilentlyContinue
    if (-not $svc) {
        Log "$Name : NOT INSTALLED (see scripts\dashboard-svc.py install / mysqld --install)"
        return $false
    }
    if ($svc.Status -ne "Running") {
        try { Start-Service $Name -ErrorAction Stop } catch { Log "$Name : start ERROR $_" }
    }
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port $Port) { Log "$Name : port $Port is up"; return $true }
    }
    Log "$Name : WARN port $Port not up after 15s (state=$((Get-Service $Name).Status))"
    return $false
}

Log "=== dev-up begin ==="

# 1) docker-vm ---------------------------------------------------------------
try {
    $running = (& $VBoxManage list runningvms 2>$null) -join "`n"
    if ($running -notmatch "docker-vm") {
        Log "starting docker-vm (headless)"
        & $VBoxManage startvm "docker-vm" --type headless 2>&1 | Out-Null
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Port 2375) { break }
        }
    } else { Log "docker-vm already running" }
} catch { Log "docker-vm ERROR: $_" }

# 2) MySQL (service) ---------------------------------------------------------
Start-Svc "TestMindMySQL" 3307 | Out-Null

# 3) dashboard (service) -----------------------------------------------------
Start-Svc "TestMindDashboard" 8901 | Out-Null

Log "=== dev-up done ==="
Log ("status: docker2375={0} mysql3307={1} dashboard8901={2}" -f `
     (Test-Port 2375), (Test-Port 3307), (Test-Port 8901))
