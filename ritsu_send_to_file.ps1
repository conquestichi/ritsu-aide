param(
  [string]$Text = "",
  [string]$ConversationId = "win"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::Expect100Continue = $false

$u8 = New-Object System.Text.UTF8Encoding($false)

function Write-U8([string]$Path, [string]$Text) {
  $dir = Split-Path -Parent $Path
  if ($dir) { New-Item -Force -ItemType Directory $dir | Out-Null }
  [System.IO.File]::WriteAllText($Path, $Text, $u8)
}
function Add-U8([string]$Path, [string]$Line) {
  $dir = Split-Path -Parent $Path
  if ($dir) { New-Item -Force -ItemType Directory $dir | Out-Null }
  [System.IO.File]::AppendAllText($Path, ($Line + "`r`n"), $u8)
}
function Read-Trim([string]$Path) {
  if (Test-Path $Path) { return (Get-Content -Raw $Path).Trim() }
  return ""
}
function Env-Trim([string]$Name) {
  $v = [System.Environment]::GetEnvironmentVariable($Name)
  if ($null -eq $v) { return "" }
  return $v.ToString().Trim()
}

$StateDir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$SendPath  = Join-Path $StateDir "send_text.txt"
$ReplyPath = Join-Path $StateDir "reply_text.txt"
$LogPath   = Join-Path $StateDir "sender_live.log"
New-Item -Force -ItemType Directory $StateDir | Out-Null

# clean log (任意：今回の結果だけ見たい)
if (Test-Path $LogPath) { Remove-Item $LogPath -Force }

$BaseUrl = Read-Trim (Join-Path $env:USERPROFILE ".ritsu\ritsu_url.txt")
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = "http://127.0.0.1:18181" }
$BaseUrl = $BaseUrl.TrimEnd("/")

$Tok = Read-Trim (Join-Path $env:USERPROFILE ".ritsu\ritsu_token.txt")
if ([string]::IsNullOrWhiteSpace($Tok)) { $Tok = Env-Trim "RITSU_TOKEN" }
if ([string]::IsNullOrWhiteSpace($Tok)) { $Tok = Env-Trim "RITSU_API_KEY" }
if ([string]::IsNullOrWhiteSpace($Tok)) { $Tok = Env-Trim "INKARITSU_API_KEY" }

if ([string]::IsNullOrWhiteSpace($Tok)) {
  Write-U8 $ReplyPath "ERR: missing token (.ritsu\ritsu_token.txt or env:RITSU_TOKEN)"
  exit 2
}

if ([string]::IsNullOrWhiteSpace($Text)) {
  if (!(Test-Path $SendPath)) {
    Write-U8 $ReplyPath "ERR: send_text.txt missing"
    exit 2
  }
  $Text = Get-Content -Raw -Encoding UTF8 $SendPath
}

$Headers = @{
  "Authorization" = "Bearer $Tok"
  "x-api-key"     = "$Tok"
  "Accept"        = "application/json"
}

$Endpoints = @("/assistant/v2", "/assistant/text")

$payload = @{ conversation_id = $ConversationId; text = $Text }
$bodyJson  = ($payload | ConvertTo-Json -Compress)
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyJson)

$last = ""
foreach ($ep in $Endpoints) {
  $uri = "$BaseUrl$ep"
  try {
    Add-U8 $LogPath ("[TRY] " + $uri + " body=" + $bodyJson)

    $res = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $uri `
      -Headers $Headers -ContentType "application/json; charset=utf-8" `
      -Body $bodyBytes -TimeoutSec 20

    $txt = ($res.Content | Out-String).Trim()
    $reply = $txt
    try {
      $o = $txt | ConvertFrom-Json
      if ($o -and ($o.PSObject.Properties.Name -contains "reply_text")) { $reply = [string]$o.reply_text }
    } catch {}

    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = "OK (empty response)" }

    Write-U8 $ReplyPath $reply
    Add-U8 $LogPath ("[OK]  " + $uri)
    exit 0
  }
  catch {
    $e = $_
    $code = "NA"; $rb = ""
    try {
      $r = $e.Exception.Response
      if ($r) {
        $code = [int]$r.StatusCode
        $s = $r.GetResponseStream()
        if ($s) { $sr = New-Object System.IO.StreamReader($s); $rb = $sr.ReadToEnd(); $sr.Close() }
      }
    } catch {}
    $last = "HTTP=$code uri=$uri err=$($e.Exception.Message) respBody=$rb"
    Add-U8 $LogPath ("[NG]  " + $last)
  }
}

Write-U8 $ReplyPath ("ERR: endpoints failed. " + $last)
exit 1