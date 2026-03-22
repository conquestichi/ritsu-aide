param(
  [string]$Text = "",
  [string]$InPath = "",
  [string]$OutPath = ""
)

function Load-EnvFile {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return }
  Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith("#") -or $line.StartsWith(";")) { return }
    if ($line.Length -ge 3 -and $line.Substring(0,3).ToUpper() -eq "REM") { return }
    $m = [regex]::Match($line, '^(?<k>[^=]+)=(?<v>.*)$')
    if ($m.Success) {
      $k = $m.Groups['k'].Value.Trim()
      $v = $m.Groups['v'].Value
      if ($k) { Set-Item -Path ("Env:\" + $k) -Value $v }
    }
  }
}

$root = $PSScriptRoot
$envFile = Join-Path $root ".ritsu_worker.env"
if (-not (Test-Path $envFile)) { $envFile = Join-Path $root "ritsu_worker.env" }
Load-EnvFile -Path $envFile

if (-not $env:RITSU_BASE_URL) { $env:RITSU_BASE_URL = "http://127.0.0.1:8181" }

if (-not $InPath)  { $InPath  = Join-Path $root "ritsu_in.txt" }
if (-not $OutPath) { $OutPath = Join-Path $root "ritsu_out.txt" }

# Text fallback: file -> param
if ((-not $Text) -and (Test-Path $InPath)) {
  $Text = (Get-Content $InPath -Raw)
}

if (-not $Text) {
  $msg = '{"error":"missing text"}'
  [IO.File]::WriteAllText($OutPath, $msg, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host $msg
  exit 2
}

# write input (for debug)
[IO.File]::WriteAllText($InPath, $Text, (New-Object System.Text.UTF8Encoding($false)))

$headers = @{}
if ($env:RITSU_BEARER_TOKEN) { $headers["Authorization"] = "Bearer $($env:RITSU_BEARER_TOKEN)" }

$bodyObj = @{ text = $Text }
$body = ($bodyObj | ConvertTo-Json -Compress -Depth 8)

$url = ($env:RITSU_BASE_URL.TrimEnd("/") + "/assistant/v2")

try {
  $res = Invoke-WebRequest -Method POST -Uri $url -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 60
  $out = $res.Content
} catch {
  $out = ('{"error":"request failed","message":' + (($_.Exception.Message | ConvertTo-Json -Compress)) + '}')
}

[IO.File]::WriteAllText($OutPath, $out, (New-Object System.Text.UTF8Encoding($false)))
Write-Host $out
exit 0