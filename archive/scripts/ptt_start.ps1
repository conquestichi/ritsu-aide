#requires -version 3
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::Expect100Continue = $false

$StateDir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$ToolDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -Force -ItemType Directory $StateDir | Out-Null

$Script = Join-Path $ToolDir "ritsu_ptt_rec.py"
if (!(Test-Path $Script)) { throw "missing recorder script: $Script" }

$PidFile  = Join-Path $StateDir "ptt_rec.pid"
$OutLog   = Join-Path $StateDir "ptt_rec.out.log"
$ErrLog   = Join-Path $StateDir "ptt_rec.err.log"
$WavFile  = Join-Path $StateDir "ptt.wav"
$StopFile = Join-Path $StateDir "ptt.stop"

# already running?
if (Test-Path $PidFile) {
  try {
    $pid = [int](Get-Content -Raw $PidFile)
    if (Get-Process -Id $pid -ErrorAction SilentlyContinue) { exit 0 }
  } catch {}
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# clean artifacts
Remove-Item $StopFile -Force -ErrorAction SilentlyContinue
Remove-Item $WavFile  -Force -ErrorAction SilentlyContinue

# python path
$py = $null
try { $py = (Get-Command python -ErrorAction Stop).Source } catch {}
if ([string]::IsNullOrWhiteSpace($py)) { $py = "C:\Users\conqu\AppData\Local\Programs\Python\Python313\python.exe" }

# start recorder (needs args: --wav --stop)
$args = @("-u", $Script, "--wav", $WavFile, "--stop", $StopFile, "--sr", "16000", "--ch", "1")
$proc = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $ToolDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
[IO.File]::WriteAllText($PidFile, [string]$proc.Id, (New-Object System.Text.UTF8Encoding($false)))
