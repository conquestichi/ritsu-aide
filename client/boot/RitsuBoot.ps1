param(
  [switch]$Start,
  [switch]$Stop,
  [switch]$Status
)

$ErrorActionPreference = "SilentlyContinue"
$TTS_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$LOGDIR  = Join-Path $env:LOCALAPPDATA "RitsuWorker"
New-Item -ItemType Directory -Force $LOGDIR | Out-Null

$RunLog = Join-Path $LOGDIR "latest.log"

function Log([string]$msg){
  ("[{0}] {1}" -f (Get-Date -Format s), $msg) | Add-Content -Encoding utf8 $RunLog
}

function Find-Procs(){
  $patterns = @(
    'run_ritsu_worker\.cmd',
    'ritsu_tunnel',
    'tts_hotkey\.ahk',
    'stt_once\.py',
    'tts_speak\.py'
  )
  Get-CimInstance Win32_Process | Where-Object {
    $cl = $_.CommandLine
    $cl -and ($patterns | Where-Object { $cl -match $_ } | Select-Object -First 1)
  }
}

function Stop-Ritsu(){
  Log "STOP requested"
  Find-Procs | ForEach-Object {
    try { $_ | Invoke-CimMethod -MethodName Terminate | Out-Null } catch {}
  }
}

function Wait-VoiceVox([int]$sec=8){
  $url="http://127.0.0.1:50021/speakers"
  $t0=Get-Date
  while(((Get-Date)-$t0).TotalSeconds -lt $sec){
    try{
      $code = (Invoke-WebRequest $url -TimeoutSec 2).StatusCode
      if($code -eq 200){ Log "VOICEVOX ready"; return $true }
    }catch{}
    Start-Sleep -Milliseconds 400
  }
  Log "VOICEVOX not ready (continue)"
  return $false
}

function Start-AHK(){
  $ahkExe = (Get-Command "AutoHotkey64.exe" -ErrorAction SilentlyContinue).Source
  if(-not $ahkExe){ $ahkExe = "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" }
  $ahkScript = Join-Path $TTS_DIR "tts_hotkey.ahk"

  if(-not (Test-Path $ahkScript)){
    Log "missing: $ahkScript"
    return
  }

  $running = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    ($_.CommandLine -match "AutoHotkey64\.exe") -and
    ($_.CommandLine -match [regex]::Escape($ahkScript))
  } | Select-Object -First 1

  if($running){
    Log "AHK already running"
  }else{
    Start-Process -FilePath $ahkExe -ArgumentList "`"$ahkScript`"" -WindowStyle Hidden
    Log "AHK started: $ahkScript"
  }
}

function Show-Status(){
  "=== Ritsu local processes ==="
  $p = Find-Procs | Select-Object ProcessId,Name,CommandLine
  if($p){ $p | Format-Table -AutoSize } else { "No related process" }

  "=== VOICEVOX ==="
  try{ (Invoke-WebRequest "http://127.0.0.1:50021/speakers" -TimeoutSec 2).StatusCode }catch{ "VOICEVOX not reachable" }
}

if($Stop){ Stop-Ritsu; exit 0 }
if($Status){ Show-Status; exit 0 }

# default = Start
Log "START requested"
# 多重起動を潰してから、仕様どおり「VOICEVOX待ち→AHK常駐」
Stop-Ritsu
Wait-VoiceVox | Out-Null
Start-AHK
exit 0
