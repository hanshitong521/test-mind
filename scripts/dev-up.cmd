@echo off
rem Double-click to bring up the TestMind local dev stack (docker-vm + MySQL + dashboard).
rem Idempotent: already-running components are left alone.
rem MySQL and the dashboard run as Windows services (Automatic), so a normal
rem boot already has them; this wrapper exists for docker-vm and for recovery.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev-up.ps1"
