#requires -version 3
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$StateDir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$PidFile  = Join-Path $StateDir "ptt_rec.pid"
$StopFile = Join-Path $StateDir "ptt.stop"

# signal stop (create stop file)
New-Item -ItemType File -Force -Path $StopFile | Out-Null

# wait/kill by pid
if (Test-Path $PidFile) {
  try {
    $pid = [int](Get-Content -Raw $PidFile)
    $p = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($p) {
      try { Wait-Process -Id $pid -Timeout 5 -ErrorAction SilentlyContinue } catch {}
      $p = Get-Process -Id $pid -ErrorAction SilentlyContinue
      if ($p) { Stop-Process -Id $pid -Force }
    }
  } catch {}
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

#### PTT_PROCESS_HOOK ####
$st = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$lock = Join-Path $st "ptt_process.lock"
$running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ptt_process\.ps1' }
if (-not $running -and -not (Test-Path $lock)) {
  Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "ptt_process.ps1")) | Out-Null
}
#### /PTT_PROCESS_HOOK ####
