param(
  [ValidateSet("doctor","start","stop","hotkey_start","hotkey_stop")]
  [string]$Mode = "doctor"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Ensure-Dir([string]$p){
  New-Item -ItemType Directory -Force -Path $p | Out-Null
}

function Load-Env {
  $p1 = Join-Path $ScriptDir ".ritsu_worker.env"
  $p2 = Join-Path $ScriptDir "ritsu_worker.env"
  $path = if(Test-Path $p1){ $p1 } elseif(Test-Path $p2){ $p2 } else { throw "env not found: .ritsu_worker.env / ritsu_worker.env" }

  $raw = Get-Content -Raw -Encoding UTF8 $path
  foreach($line in ($raw -split "`r?`n")){
    if($line -match '^\s*$'){ continue }
    if($line -match '^\s*#'){ continue }
    if($line -match '^\s*([^=]+?)\s*=\s*(.*)\s*$'){
      $k = $matches[1].Trim()
      $v = $matches[2]
      [System.Environment]::SetEnvironmentVariable($k,$v,"Process")
    }
  }
  return $path
}

function Stop-ByCommandLineRegex([string]$re){
  Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match $re) } |
    ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
}

function MaskLen([string]$s){
  if(-not $s){ return "(empty)" }
  return ("len=" + $s.Length)
}

function Doctor {
  $envPath = Load-Env
  $logdir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
  Ensure-Dir $logdir

  $base = $env:RITSU_BASE_URL
  if(-not $base){ $base = "http://127.0.0.1:8181" }

  $pyWorker = Join-Path $ScriptDir "ritsu_worker_notify.py"

  "== DOCTOR ==" | Out-Host
  ("env: " + $envPath) | Out-Host
  ("base: " + $base) | Out-Host
  ("token: " + (MaskLen $env:RITSU_BEARER_TOKEN)) | Out-Host
  ("worker_id: " + $env:RITSU_WORKER_ID) | Out-Host
  ("py_worker: " + (Test-Path $pyWorker) + " (" + $pyWorker + ")") | Out-Host
  ("vmc_sender: " + $env:RITSU_VMC_SENDER) | Out-Host
  ("vmc_map: " + $env:RITSU_VMC_MAP_PATH) | Out-Host

  try {
    $r = Invoke-WebRequest ($base.TrimEnd("/") + "/ready") -TimeoutSec 3 -UseBasicParsing
    ("ready: " + $r.Content) | Out-Host
  } catch {
    ("ready: ERR " + $_.Exception.Message) | Out-Host
  }
}

function Start-Worker {
  $envPath = Load-Env
  $logdir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
  Ensure-Dir $logdir

  $base = $env:RITSU_BASE_URL
  if(-not $base){ $base = "http://127.0.0.1:8181" }
  $workerId = $env:RITSU_WORKER_ID
  if(-not $workerId){ $workerId = "gpc1" }

  $ts  = Get-Date -Format "yyyyMMdd_HHmmss"
  $out = Join-Path $logdir ("worker_live_{0}.log" -f $ts)
  $err = Join-Path $logdir ("worker_live_{0}.err.log" -f $ts)

  # Python worker (the canonical 1325-line version)
  $pyWorker = Join-Path $ScriptDir "ritsu_worker_notify.py"

  # Find python
  $py = $null
  try { $py = (Get-Command python -ErrorAction Stop).Source } catch {}
  if ([string]::IsNullOrWhiteSpace($py)) { $py = "C:\Users\conqu\AppData\Local\Programs\Python\Python313\python.exe" }

  try {
    Set-Content -Encoding UTF8 $out ("[BOOT] worker(py) base={0} id={1}" -f $base,$workerId)
    Set-Content -Encoding UTF8 $err ""

    if(-not (Test-Path $pyWorker)){
      Add-Content -Encoding UTF8 $err ("[ERR] missing: {0}" -f $pyWorker)
      throw "worker script missing: $pyWorker"
    }

    $arg = @("-u", $pyWorker)
    $p = Start-Process -FilePath $py -ArgumentList $arg -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err

    Start-Sleep -Milliseconds 400
    if($p.HasExited){
      Add-Content -Encoding UTF8 $err ("`n[ERR] exited immediately. exitCode={0}" -f $p.ExitCode)
      throw "worker exited immediately (see $err)"
    }

    ("OK worker_start pid=" + $p.Id) | Out-Host
    ("logs: " + $out) | Out-Host
    ("errs: " + $err) | Out-Host
  }
  catch {
    Add-Content -Encoding UTF8 $err ("`n[EXC] " + $_.ToString())
    throw
  }
}

function Stop-Worker {
  Load-Env | Out-Null
  Stop-ByCommandLineRegex 'ritsu_worker_notify\.py|ritsu_worker\.ps1'
  "OK worker_stop" | Out-Host
}

function HotkeyStart {
  Load-Env | Out-Null
  $ahk = $env:RITSU_AHK_EXE
  if(-not $ahk){ throw "RITSU_AHK_EXE missing in env" }
  $script = Join-Path $ScriptDir "tts_hotkey.ahk"
  if(-not (Test-Path $script)){ throw "missing: $script" }
  Start-Process -FilePath $ahk -ArgumentList @($script) | Out-Null
  "OK hotkey_start" | Out-Host
}

function HotkeyStop {
  Stop-ByCommandLineRegex 'tts_hotkey\.ahk'
  "OK hotkey_stop" | Out-Host
}

switch($Mode){
  "doctor"      { Doctor; break }
  "start"       { Start-Worker; break }
  "stop"        { Stop-Worker; break }
  "hotkey_start"{ HotkeyStart; break }
  "hotkey_stop" { HotkeyStop; break }
}
