# ritsu-aide — プロジェクト進行メモ
> 最終更新: 2026-04-14

## 現在の状態
- **デスクトップ (ritsu_v4.py)**: Windows常駐稼働中。Prompt Caching適用済み。親密度prompt注入+デスクトップ会話時+1スコア加算。streaming_mode対応済み
- **LINE bot (ritsu_line.py)**: VPS稼働中 (port 9878)。親密度システム・PushThread・DailyDecayThread稼働中
- **共有知識DB**: /srv/ritsu-shared/shared_knowledge.sqlite（knowledge + intimacy + push_history + system_flags テーブル）
- **親密度**: ritsu score=41, phase=friend / kogane score=17, phase=secretary
- **inga-fact連携**: 平日06:35朝ブリーフィング（/api/fact/today取得→律が報告）
- **inga-stream連携**: streaming_modeフラグ経由でMonologue/KoganeWatcher/PushThread自動停止

## 直近の変更（最新5件）
- 2026-04-14: inga-stream連携完了 — ritsu_v4.pyにstreaming_modeキャッシュ+30秒sync loop追加、MonologueThread/KoganeWatcherThreadに早期return追加
- 2026-03-30: streaming_modeフラグ対応 — system_flagsテーブル+API+PushThread停止チェック
- 2026-03-30: health.json作成（監査マニフェスト）
- 2026-03-30: push自己履歴参照で重複話題防止 — own_recent_pushes注入
- 2026-03-28: Prompt Caching — urllib直接呼び出しにanthropic-betaヘッダー追加（コミット 29e50db）

## 残タスク
### 進行中
- [x] push重複話題修正 — 自分のpush履歴をプロンプトに注入（実装済み・デプロイ済み）
- [x] streaming_modeフラグ対応（VPS側: 実装済み・デプロイ済み）
- [x] streaming_modeフラグ対応（ritsu_v4.py側: MonologueThread/KoganeWatcherThread対応完了 2026-04-14）
- [x] health.json作成（実装済み・デプロイ済み）

### 待ち（データ蓄積・外部依存）
- [ ] Scheduleスロット平日19枠の動作確認
- [ ] inga-fact朝ブリーフィング動作確認（平日06:35）
- [ ] TTS末尾ぶつ切り修正の効果確認
- [ ] 配信開始時に律デスクトップが実際に発話停止するかの実機確認

### 将来
- [ ] 親密度Phase 4残タスク: 誕生日ボーナス、姉妹間情報伝達、既読スルー態度変化、相場大負け日慰めボーナス
- [ ] Tool Use設計・実装（天気/タイマー/株価/こがね確認/スクショVision）
- [ ] スクリーン覗き見（Claude Vision API）
- [ ] Lapwing VRM変換完了 → VMagicMirror 2体表示

## 注意事項
- ritsu_v4.pyは1ファイル原則（外部モジュール分割禁止）
- LINE bot (line/)はVPSデプロイ。CI/CDはline/変更時のみ発火
- keyboardライブラリ使用禁止（Win32 message pumpと競合）
- urllib直接呼び出し時は `anthropic-beta: prompt-caching-2024-07-31` ヘッダー必須
- PTT→TTS音声なし問題: 初回セッションで報告あり。再起動後は発生せず→要継続観察
