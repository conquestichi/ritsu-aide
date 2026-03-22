# migrate_windows.ps1 - Ritsu Windows directory consolidation
# Usage: powershell -ExecutionPolicy Bypass -File migrate_windows.ps1 [-DryRun]

param([switch]$DryRun)

$TTS = "C:\Users\conqu\tts"
$TOOLS = "C:\tools\ritsu"
$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$ARC = "$TTS\_archive_$TS"

# Canonical files (keep these in tts/)
$keep = @(
    "ritsu_worker_notify.py"
    "ritsu_send_to_file.ps1"
    "ritsu_v2_client.py"
    "ritsu_call.ps1"
    "tts_speak.py"
    "stt_once.py"
    "voicevox_speak.ps1"
    "tts_hotkey.ahk"
    "ptt_process.ps1"
    "ptt_start.ps1"
    "ptt_stop.ps1"
    "vmc_expr_map.json"
    "vmc_smoke_test.py"
    "run_ritsu_worker.cmd"
    "start_ritsu_worker.cmd"
    "RitsuBoot.ps1"
    "RitsuStatus.cmd"
    "RitsuStop.cmd"
    "RitsuRestart.cmd"
    ".ritsu_worker.env"
    "migrate_windows.ps1"
)

Write-Host "=== Ritsu Windows Migration ==="
Write-Host "TTS: $TTS"
Write-Host "Archive: $ARC"
if ($DryRun) { Write-Host "*** DRY RUN ***" }

# Count
$allFiles = Get-ChildItem $TTS -File
Write-Host "Current files in tts: $($allFiles.Count)"

# Create archive dir
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $ARC | Out-Null
}

# Archive non-canonical files
$archived = 0
foreach ($f in $allFiles) {
    if ($keep -contains $f.Name) {
        Write-Host "  KEEP: $($f.Name)"
        continue
    }
    if ($DryRun) {
        Write-Host "  [DRY] ARCHIVE: $($f.Name)"
    } else {
        Move-Item $f.FullName (Join-Path $ARC $f.Name) -Force
        Write-Host "  ARCHIVE: $($f.Name)"
    }
    $archived++
}

# Archive subdirectories (except _archive*)
$dirs = Get-ChildItem $TTS -Directory | Where-Object { $_.Name -notmatch "^_archive" }
foreach ($d in $dirs) {
    if ($DryRun) {
        Write-Host "  [DRY] ARCHIVE dir: $($d.Name)/"
    } else {
        Move-Item $d.FullName (Join-Path $ARC $d.Name) -Force -ErrorAction SilentlyContinue
        Write-Host "  ARCHIVE dir: $($d.Name)/"
    }
}

# Archive C:\tools\ritsu if exists
if (Test-Path $TOOLS) {
    $tArc = Join-Path $ARC "_tools_ritsu"
    if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $tArc | Out-Null }
    foreach ($f in (Get-ChildItem $TOOLS -File)) {
        if ($DryRun) {
            Write-Host "  [DRY] ARCHIVE(tools): $($f.Name)"
        } else {
            Copy-Item $f.FullName (Join-Path $tArc $f.Name) -Force
            Write-Host "  ARCHIVE(tools): $($f.Name)"
        }
        $archived++
    }
}

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Archived: $archived files"
Write-Host "Remaining in tts/:"
Get-ChildItem $TTS -File | ForEach-Object { Write-Host "  $($_.Name)" }
Write-Host ""
Write-Host "Rollback: Move-Item '$ARC\*' '$TTS' -Force"
