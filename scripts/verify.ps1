# scripts/verify.ps1 — 一键验证：自测 + 红包 e2e 闭环 + MCP 全链冒烟 + runner 探测
# 用法: powershell -ExecutionPolicy Bypass -File scripts\verify.ps1   (在 testmind/ 目录)
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot -Parent)
$fail = @()

Write-Host "== 1/5 unit + red team =="
python -m unittest discover -s tests 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { $fail += "unit" } else { Write-Host "unit+redteam: OK" }

Write-Host "== 2/5 red-packet e2e closed loop =="
python examples/red-packet/e2e.py 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { $fail += "e2e" } else { Write-Host "e2e: FINAL=PASS (red-packet closed loop incl. boundary/negative/state/fault/concurrency + schemathesis)" }

Write-Host "== 3/5 MCP full-chain smoke (run_pipeline) =="
python scripts/mcp_smoke.py 2>$null | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { $fail += "mcp" } else { Write-Host "MCP: OK" }

Write-Host "== 4/5 runner availability =="
python -c "from testmind import core; import json; print(json.dumps(core.scan_runners(), indent=1, ensure_ascii=False))"

Write-Host "== 5/5 full power check (Karate real run + Docker-provisioned MySQL) =="
python scripts/full_power_check.py 2>$null | Select-Object -Last 12
if ($LASTEXITCODE -ne 0) { $fail += "fullpower" } else { Write-Host "full-power: OK" }

if ($fail) { Write-Host "VERIFY = FAIL: $($fail -join ', ')"; exit 1 }
Write-Host "VERIFY = PASS"
