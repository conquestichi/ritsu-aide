#requires -version 3
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::Expect100Continue = $false

$BaseDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir = Join-Path $env:LOCALAPPDATA "RitsuWorker"
$ToolDir  = $BaseDir
New-Item -Force -ItemType Directory $StateDir | Out-Null

$WavFile   = Join-Path $StateDir "ptt.wav"
$AmpWav    = Join-Path $StateDir "ptt_amp.wav"
$TextFile  = Join-Path $StateDir "ptt_text.txt"
$ReplyPtt  = Join-Path $StateDir "reply_ptt.txt"
$SendText  = Join-Path $StateDir "send_text.txt"
$ReplyText = Join-Path $StateDir "reply_text.txt"
$LogFile   = Join-Path $StateDir "ptt_process.log"
$LockFile  = Join-Path $StateDir "ptt_process.lock"

function Log([string]$m){
  $ts=(Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  [IO.File]::AppendAllText($LogFile,"[$ts] $m`r`n",(New-Object System.Text.UTF8Encoding($false)))
}
function ReadSafe([string]$p){
  if([string]::IsNullOrWhiteSpace($p)) {return ""}
  if(!(Test-Path $p)) {return ""}
  $s = Get-Content -Raw $p -ErrorAction SilentlyContinue
  if($null -eq $s){return ""}
  return ($s.ToString()).Trim()
}

# lock (avoid double-run)
if (Test-Path $LockFile) {
  try {
    $age = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($age.TotalSeconds -lt 30) { Log "LOCK_SKIP"; exit 0 }
  } catch {}
}
New-Item -ItemType File -Force -Path $LockFile | Out-Null

try {
  if(!(Test-Path $WavFile)) { Log "NO_WAV"; exit 0 }
  $len=(Get-Item $WavFile).Length
  Log ("WAV_OK len=" + $len)
  if ($len -lt 4000) { Log "WAV_TOO_SMALL"; exit 0 }

  # python path
  $py=$null
  try { $py=(Get-Command python -ErrorAction Stop).Source } catch {}
  if([string]::IsNullOrWhiteSpace($py)) { $py="C:\Users\conqu\AppData\Local\Programs\Python\Python313\python.exe" }

  $tmpPy = Join-Path $StateDir "ptt_transcribe_tmp.py"
  $pyOut = Join-Path $StateDir "ptt_transcribe.out.txt"
  $pyErr = Join-Path $StateDir "ptt_transcribe.err.txt"
  Remove-Item $tmpPy,$pyOut,$pyErr,$AmpWav -Force -ErrorAction SilentlyContinue

  # build python code (NO here-string pitfalls)
  $pyLines = @(
    'import os, sys, wave, struct',
    'wav_in = sys.argv[1]',
    'wav_out = sys.argv[2]',
    'lang = os.environ.get("RITSU_LANG","ja")',
    'model = os.environ.get("RITSU_WHISPER_MODEL","tiny")',
    '',
    'def normalize(in_path, out_path, target_peak=12000):',
    '    with wave.open(in_path,"rb") as w:',
    '        ch=w.getnchannels(); sw=w.getsampwidth(); fr=w.getframerate(); n=w.getnframes()',
    '        data=w.readframes(n)',
    '    if sw!=2:',
    '        with wave.open(out_path,"wb") as o:',
    '            o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(fr); o.writeframes(data)',
    '        return 0, 1.0',
    '    s=struct.unpack("<%dh"%(n*ch), data)',
    '    peak=max(abs(x) for x in s) if s else 0',
    '    if peak<=0:',
    '        with wave.open(out_path,"wb") as o:',
    '            o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(fr); o.writeframes(data)',
    '        return 0, 1.0',
    '    gain = float(target_peak)/float(peak) if peak<target_peak else 1.0',
    '    if gain>30.0: gain=30.0',
    '    out=[]',
    '    for x in s:',
    '        y=int(x*gain)',
    '        if y>32767: y=32767',
    '        if y<-32768: y=-32768',
    '        out.append(y)',
    '    out_bytes=struct.pack("<%dh"%(len(out)), *out)',
    '    with wave.open(out_path,"wb") as o:',
    '        o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(fr); o.writeframes(out_bytes)',
    '    return peak, gain',
    '',
    'peak,gain = normalize(wav_in, wav_out)',
    'print(f"__GAIN__ peak={peak} gain={gain}", file=sys.stderr)',
    '',
    'def die(msg):',
    '    sys.stderr.write(msg+"\\n")',
    '    print("")',
    '    sys.exit(0)',
    '',
    'try:',
    '    from faster_whisper import WhisperModel',
    '    m = WhisperModel(model, device="cpu", compute_type="int8")',
    '    segments, info = m.transcribe(wav_out, language=lang, beam_size=5, vad_filter=False)',
    '    text = "".join([s.text for s in segments]).strip()',
    '    print(text)',
    '    sys.exit(0)',
    'except Exception as e:',
    '    pass',
    '',
    'try:',
    '    import whisper',
    '    m = whisper.load_model(model)',
    '    r = m.transcribe(wav_out, language=lang)',
    '    print((r.get("text","") or "").strip())',
    '    sys.exit(0)',
    'except Exception as e:',
    '    die("NO_STT_BACKEND: "+str(e))'
  )
  $pyCode = ($pyLines -join "`n")
  [IO.File]::WriteAllText($tmpPy, $pyCode, (New-Object System.Text.UTF8Encoding($false)))

  Log ("STT_START model=" + $env:RITSU_WHISPER_MODEL)
  $p = Start-Process -FilePath $py -ArgumentList @("-u",$tmpPy,$WavFile,$AmpWav) -WorkingDirectory $ToolDir -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $pyOut -RedirectStandardError $pyErr
  Log ("STT_EXIT code=" + $p.ExitCode)

  $sttErr = ReadSafe $pyErr
  $text   = ReadSafe $pyOut
  if([string]::IsNullOrWhiteSpace($text)) {
    Log ("STT_EMPTY err=" + $sttErr)
    [IO.File]::WriteAllText($TextFile,"",(New-Object System.Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($ReplyPtt,("ERR: STT empty. "+$sttErr),(New-Object System.Text.UTF8Encoding($false)))
    Log "DONE"
    exit 0
  }

  [IO.File]::WriteAllText($TextFile,$text,(New-Object System.Text.UTF8Encoding($false)))
  Log ("STT_OK text_len=" + $text.Length)

  # send
  [IO.File]::WriteAllText($SendText,$text,(New-Object System.Text.UTF8Encoding($false)))
  $sender = Join-Path $BaseDir "ritsu_sender.cmd"
  if(!(Test-Path $sender)) {
    Log "NO_SENDER_CMD"
    [IO.File]::WriteAllText($ReplyPtt,$text,(New-Object System.Text.UTF8Encoding($false)))
  } else {
    Log "SEND_START"
    cmd /c "`"$sender`"" | Out-Null
    if(Test-Path $ReplyText) {
      Copy-Item $ReplyText $ReplyPtt -Force
      Log "SEND_OK reply_ptt_updated"
    } else {
      [IO.File]::WriteAllText($ReplyPtt,"ERR: no reply_text.txt",(New-Object System.Text.UTF8Encoding($false)))
      Log "SEND_NG no reply_text.txt"
    }
  }

  # speak (always log + out/err)
  try {
    $speak = Join-Path $BaseDir "voicevox_speak.ps1"
    $outLog = Join-Path $StateDir "voicevox_speak.out.log"
    $errLog = Join-Path $StateDir "voicevox_speak.err.log"
    if ((Test-Path $speak) -and (Test-Path $ReplyPtt)) {
      Log "SPEAK_START"
      try { Remove-Item $outLog,$errLog -Force -ErrorAction SilentlyContinue } catch {}
      $p2 = Start-Process powershell -PassThru -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$speak,"-TextFile",$ReplyPtt)
      Log ("SPEAK_SPAWN pid=" + $p2.Id)
    } else {
      Log "SPEAK_SKIP missing speak or reply_ptt"
    }
  } catch {
    try { Log ("SPEAK_FAIL " + $_.Exception.Message) } catch {}
  }

  Log "DONE"
} finally {
  Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}
