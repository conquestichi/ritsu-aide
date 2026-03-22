# 律 Aide V4 — PTT録音問題 診断レポート

## 環境
- OS: Windows 11
- Python: 3.13
- sounddevice: 0.5.5 (PortAudio backend)
- マイク: DualSense Wireless Controller (Bluetooth) — デバイス #1 (既定)
- 他マイク: Realtek HD Audio Mic (#36)

## 症状
XButton2 (マウスサイドボタン) でPTT録音を開始するが、**音声データが一切取得できない**。

## テスト済みパターンと結果

| # | 方式 | 呼出元 | 結果 |
|---|------|--------|------|
| 1 | `sd.rec()+sd.wait()` 2秒固定 | main()から直接 (起動時mic test) | ✅ peak=32766 |
| 2 | `sd.rec()+sd.wait()` 3秒固定 | daemon thread (XButton2 release) | ✅ peak=28624 (1回だけ成功) |
| 3 | `sd.rec()+sd.wait()` 5秒固定 | daemon thread (XButton2 press) | ❌ sd.wait()が返らない/ハング |
| 4 | `sd.rec()` → poll → `sd.stop()` | daemon thread | ❌ 全て0 (sd.stop()がバッファをクリア) |
| 5 | `sd.InputStream` callback 16kHz | daemon thread | ❌ callbacks=0 |
| 6 | `sd.InputStream` callback 44100Hz | daemon thread | ❌ callbacks=0 |
| 7 | `sd.InputStream` blocking read | daemon thread | ❌ PaError -9999 "Blocking API not supported" |
| 8 | `sd.rec()+sd.wait()` 0.5s chunk loop | daemon thread | ❌ 0 chunks / ハング |
| 9 | pyaudio `stream.read()` | daemon thread | ❌ ハング |
| 10 | Realtek #36 全パターン | daemon thread | ❌ peak=0 (Realtekマイク自体が無音) |

## 重要な事実
1. **起動直後の `sd.rec()+sd.wait()` は成功する** (peak=32766)
2. **GUI起動後・ホットキースレッド起動後は `sd.rec()` がハングまたは無音になる**
3. V3 (旧バージョン) では同じDualSenseで正常にPTT録音ができていた
4. V3は `sd.InputStream` + callback + 16kHz + `soundfile` で録音していた

## 推定原因
### 仮説A: PortAudioとWin32メッセージループの競合
- ホットキースレッドで `WH_MOUSE_LL` + `GetMessageW` ループが走っている
- PortAudioの内部コールバックスレッドがメッセージポンプと競合している可能性
- 根拠: 起動直後(ホットキー未起動)のsd.rec()は成功、起動後は失敗

### 仮説B: tkinter mainloopとPortAudioの競合
- tkinterのmainloopが走っている状態でPortAudioのコールバックが阻害される
- 根拠: mic testはmainloop開始前/直後に動いた

### 仮説C: DualSense Bluetooth固有の問題
- DualSense BT音声プロファイルがPortAudioの特定呼出しパターンでデッドロックする
- 根拠: 全てのストリーミング方式(callback, blocking read, pyaudio)が失敗

## V3との差異 (要確認)
- V3使用時のデフォルトマイクがDualSenseだったか確認が必要
- V3は VPS + OpenAI Whisper API (ネットワーク送信) — ローカルSTTではなかった
- V3の `sd.InputStream` は16kHz — DualSenseは16kHzをサイレントに無視する可能性

## 切り分けに必要なテスト
1. **ホットキースレッドなしで録音テスト** — hotkey起動前にsd.rec()
2. **tkinter mainloopなしで録音テスト** — GUIなしでPTTだけ
3. **Windowsの既定マイクをRealtekに変更して再テスト**
4. **V3をそのまま起動してPTTが動くか確認** (環境差の確認)

## 提案する解決策
1. **ホットキースレッド起動前にPortAudioを初期化** — 起動時にダミー録音で初期化
2. **既定マイクをRealtek等の有線マイクに変更**
3. **録音をメインスレッド(tkinter)からafter()で実行** — daemon threadではなく
4. **ffmpegやWASAPI直接呼出し等、PortAudio以外のバックエンドを使用**
