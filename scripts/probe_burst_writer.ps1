$ErrorActionPreference = 'SilentlyContinue'

Write-Output "=== is process-creation auditing (4688) even enabled? ==="
$pol = auditpol /get /subcategory:"{0CCE921B-69AE-11D9-BED3-505054503030}" /csv 2>$null
if ($pol) { $pol | Select-Object -Skip 1 | ForEach-Object { "  $_" } }

Write-Output ""
Write-Output "=== Security log 4688 events 21:45-21:55 on 2026-09-07 ==="
$ev = Get-WinEvent -FilterHashtable @{
    LogName   = 'Security'
    Id        = 4688
    StartTime = [datetime]'2026-09-07 21:45:00'
    EndTime   = [datetime]'2026-09-07 21:55:00'
} -MaxEvents 50
if (-not $ev) {
    Write-Output "  (none - auditing off or events rolled off)"
}
else {
    foreach ($e in $ev) {
        $np = ($e.Properties[5]).Value
        $cp = ($e.Properties[13]).Value
        Write-Output ("  {0}  new={1}  creator={2}" -f $e.TimeCreated.ToString('HH:mm:ss'), $np, $cp)
    }
}

Write-Output ""
Write-Output "=== Security log oldest/newest retained event ==="
$all = Get-WinEvent -LogName Security -MaxEvents 1
if ($all) { Write-Output ("  newest: {0}" -f $all.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')) }
$log = Get-WinEvent -ListLog Security
if ($log) {
    Write-Output ("  record count: {0}   oldest (approx): {1}" -f $log.RecordCount, $log.OldestRecordNumber)
}

Write-Output ""
Write-Output "=== any log/session artifact touched 21:49:00-21:50:30 outside E:\workA ==="
$roots = @(
    "$env:USERPROFILE\.qoder-cn",
    "$env:USERPROFILE\.cursor",
    "$env:LOCALAPPDATA\Temp"
)
foreach ($r in $roots) {
    if (-not (Test-Path $r)) { continue }
    $hits = Get-ChildItem -Path $r -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.LastWriteTime -ge [datetime]'2026-09-07 21:49:00' -and
            $_.LastWriteTime -le [datetime]'2026-09-07 21:50:30'
        } | Select-Object -First 12
    if ($hits) {
        Write-Output "  -- $r"
        foreach ($h in $hits) {
            Write-Output ("     {0}  {1,9}B  {2}" -f $h.LastWriteTime.ToString('HH:mm:ss.fff'), $h.Length, $h.FullName)
        }
    }
    else { Write-Output "  -- $r : none" }
}
