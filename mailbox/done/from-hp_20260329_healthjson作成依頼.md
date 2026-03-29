# health.json作成依頼
- 送信元: inga-quants-hp
- 日付: 2026-03-29
- 種別: 要望書
- 緊急度: 低

## 概要
監査システム(health_audit.py)がhealth.jsonマニフェストを読み取ってサービス状態を自動検証する。
当リポにはまだhealth.jsonが存在しないため、監査対象に入っていない。

## 対応してほしいこと
health.jsonを作成してリポ直下に配置。参考: inga-kogane/health.json

最低限の項目:
- ポート死活（常駐サービスがあれば）
- systemdユニット確認
- 重要ファイルの鮮度チェック

## 備考
他4リポ（hp, quants, fact, kogane）は整備済み。
