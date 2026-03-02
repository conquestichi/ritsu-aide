param(
  [string]$Text = "",
  [string]$TextFile = "",
  [int]$Speaker = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::Expect100Continue = $false

$base = "http://127.0.0.1:50021"
$stateDir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$outWav = Join-Path $stateDir "tts_last.wav"
New-Item -Force -ItemType Directory $stateDir | Out-Null

$u8 = New-Object System.Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($Text) -and -not [string]::IsNullOrWhiteSpace($TextFile) -and (Test-Path $TextFile)) {
  $Text = [IO.File]::ReadAllText($TextFile, $u8)
}
if ($null -eq $Text) { $Text = "" }
$Text = $Text.Trim()
if ([string]::IsNullOrWhiteSpace($Text)) { exit 0 }

$encText = [Uri]::EscapeDataString($Text)
$aqUrl = "$base/audio_query?text=$encText&speaker=$Speaker"
$aq = Invoke-RestMethod -Method Post -Uri $aqUrl -TimeoutSec 10

$js = ($aq | ConvertTo-Json -Depth 20 -Compress)
$bytes = [Text.Encoding]::UTF8.GetBytes($js)
$sUrl = "$base/synthesis?speaker=$Speaker"

Invoke-WebRequest -Method Post -Uri $sUrl -ContentType "application/json; charset=utf-8" -Body $bytes -OutFile $outWav -TimeoutSec 20 | Out-Null

Add-Type -AssemblyName System.Windows.Forms | Out-Null
$sp = New-Object System.Media.SoundPlayer($outWav)
$sp.PlaySync()