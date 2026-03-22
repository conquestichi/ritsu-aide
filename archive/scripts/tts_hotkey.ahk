#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
SetWorkingDir A_ScriptDir

; ---- paths
stateDir   := EnvGet("LOCALAPPDATA") "\RitsuWorker"
DirCreate stateDir
sendPath   := stateDir "\send_text.txt"
replyPath  := stateDir "\reply_text.txt"

senderCmd  := A_ScriptDir "\ritsu_sender.cmd"
speakPs    := A_ScriptDir "\voicevox_speak.ps1"

pttStartPs := A_ScriptDir "\ptt_start.ps1"
pttStopPs  := A_ScriptDir "\ptt_stop.ps1"

; ---- GUI (mini window)
miniVisible := false
mini := Gui("+AlwaysOnTop +ToolWindow", "Ritsu 小窓")
mini.SetFont("s10", "Segoe UI")
mini.MarginX := 10, mini.MarginY := 10
mini.AddText("w420", "Enter=送信 / Esc=閉じる")
inp := mini.AddEdit("w420 r3 vInp")
btn := mini.AddButton("w90", "送信")
btn.OnEvent("Click", (*) => DoSend())
out := mini.AddEdit("w420 r6 ReadOnly -Wrap vOut")
mini.OnEvent("Close", (*) => HideMini())
mini.OnEvent("Escape", (*) => HideMini())

ToggleMini() {
  global mini, miniVisible, inp
  if (miniVisible) {
    HideMini()
  } else {
    mini.Show("AutoSize")
    miniVisible := true
    inp.Focus()
  }
}
HideMini() {
  global mini, miniVisible
  mini.Hide()
  miniVisible := false
}

DoSend(*) {
  global inp, out, sendPath, replyPath, senderCmd, speakPs
  txt := Trim(inp.Value)
  if (txt = "")
    return

  try FileDelete(sendPath)
  FileAppend(txt, sendPath, "UTF-8-RAW")

  if !FileExist(senderCmd) {
    out.Value := "ERR: ritsu_sender.cmd not found"
    return
  }

  RunWait('"' senderCmd '"', A_ScriptDir, "Hide")

  if FileExist(replyPath) {
    rep := FileRead(replyPath, "UTF-8")
    out.Value := rep
    if FileExist(speakPs) {
      Run('powershell -NoProfile -ExecutionPolicy Bypass -File "' speakPs '" -TextFile "' replyPath '"', , "Hide")
    }
  } else {
    out.Value := "ERR: reply_text.txt not found"
  }
}

; ---- PTT (hold XButton2)
pttOn := false

PTTStart() {
  global pttStartPs, pttOn
  if (pttOn)
    return
  if FileExist(pttStartPs) {
    Run('powershell -NoProfile -ExecutionPolicy Bypass -File "' pttStartPs '"', A_ScriptDir, "Hide")
    pttOn := true
  }
}

PTTStop() {
  global pttStopPs, pttOn
  if (!pttOn)
    return
  if FileExist(pttStopPs) {
    Run('powershell -NoProfile -ExecutionPolicy Bypass -File "' pttStopPs '"', A_ScriptDir, "Hide")
    pttOn := false
  }
}

; ---- hotkeys
XButton1::ToggleMini()

F10::{
  global miniVisible
  if (miniVisible)
    DoSend()
  else
    ToggleMini()
}

XButton2::PTTStart()
XButton2 Up::PTTStop()

F12::KeyHistory

#HotIf WinActive("ahk_id " mini.Hwnd)
Enter::DoSend()
Esc::HideMini()
#HotIf