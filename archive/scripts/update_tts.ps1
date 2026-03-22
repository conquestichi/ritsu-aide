# update_tts.ps1 - Download fixed canonical files from GitHub to tts/
# Usage: powershell -ExecutionPolicy Bypass -File update_tts.ps1

$TTS = "C:\Users\conqu\tts"
$BASE = "https://raw.githubusercontent.com/conquestichi/ritsu-aide/main"

$files = @(
    @{url="$BASE/client/boot/run_ritsu_worker.ps1"; dest="run_ritsu_worker.ps1"},
    @{url="$BASE/client/ui/ptt_process.ps1"; dest="ptt_process.ps1"},
    @{url="$BASE/client/ui/ptt_start.ps1"; dest="ptt_start.ps1"},
    @{url="$BASE/client/core/ritsu_worker.py"; dest="ritsu_worker_notify.py"},
    @{url="$BASE/client/vmc/vmc_send_pyosc.py"; dest="vmc_send_pyosc.py"},
    @{url="$BASE/client/tts/ritsu_ptt_rec.py"; dest="ritsu_ptt_rec.py"},
    @{url="$BASE/client/core/ritsu_sender.cmd"; dest="ritsu_sender.cmd"}
)

Write-Host "=== Updating tts/ from GitHub ==="
foreach ($f in $files) {
    $dest = Join-Path $TTS $f.dest
    try {
        Invoke-WebRequest -Uri $f.url -OutFile $dest -UseBasicParsing
        Write-Host "  OK: $($f.dest)"
    } catch {
        Write-Host "  FAIL: $($f.dest) - $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Updated files:"
foreach ($f in $files) {
    $dest = Join-Path $TTS $f.dest
    if (Test-Path $dest) {
        $size = (Get-Item $dest).Length
        Write-Host ("  {0} ({1} bytes)" -f $f.dest, $size)
    }
}
Write-Host ""
Write-Host "Next: run_ritsu_worker.cmd start"
