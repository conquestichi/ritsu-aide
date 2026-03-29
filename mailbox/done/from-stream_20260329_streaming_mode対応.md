# streaming_modeフラグ対応
- 送信元: inga-stream
- 日付: 2026-03-29
- 種別: 要望書
- 緊急度: 高

## 概要
inga-streamの配信システムで、配信中に律の独り言・push・こがね通知を停止するためのstreaming_modeフラグが必要。

## 対応してほしいこと

### 1. shared_knowledge.py に system_flags テーブル + 関数追加
- `system_flags`テーブル（key TEXT PRIMARY KEY, value TEXT, updated_at TEXT）
- `system_flag_get(key)` / `system_flag_set(key, value)` 関数

### 2. ritsu_line.py の共有知識APIに `/api/shared-knowledge/system-flags` エンドポイント追加
- GET: フラグ値取得
- POST: フラグ値設定（既存トークン認証）

### 3. ritsu_line.py PushThread に streaming_mode チェック追加
- push送信前に `system_flag_get("streaming_mode")` → `off`以外ならスキップ

### 4. ritsu_v4.py MonologueThread / KoganeWatcherThread に同様のチェック
- API経由で確認（Windows側なのでDB直接参照不可）
- `GET http://160.251.167.44:9878/api/shared-knowledge/system-flags?key=streaming_mode`

## 影響範囲
streaming_mode未設定 or "off"の場合は現状と完全に同じ動作。既存テストに影響なし。

## 詳細
inga-stream リポの `docs/change_requests/ritsu_aide_streaming_mode.md` にコード例あり。
