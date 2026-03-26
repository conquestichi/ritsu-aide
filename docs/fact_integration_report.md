# ritsu-aide × inga-fact 連携実装完了報告書

**報告日**: 2026-03-26
**報告元**: ritsu-aide (conquestichi/ritsu-aide)
**報告先**: inga-fact (conquestichi/inga-fact)

---

## 1. 実装概要

要望書 `ritsu_aide_fact_integration_request.md` に基づき、ritsu_v4.py に
inga-fact 朝ブリーフィング機能を実装しました。

### 実装内容

| 項目 | 状態 | 備考 |
|------|------|------|
| 環境変数 (RITSU_FACT_*) | ✅ 実装済 | .env / env.example 両方 |
| _fetch_fact_briefing() 関数 | ✅ 実装済 | セクション6直前に配置 |
| _try_schedule __FACT_BRIEFING__ 分岐 | ✅ 実装済 | ハイブリッド版に統合 |
| monologue_schedule.json 06:35スロット | ✅ 追加済 | 平日のみ (weekdays: 0-4) |
| エラーハンドリング | ✅ 実装済 | 404/stale/接続失敗に対応 |

---

## 2. 呼び出し仕様

### エンドポイント

```
GET http://160.251.167.44:9879/api/fact/today
Authorization: Bearer {RITSU_FACT_API_TOKEN}
```

### 呼び出しタイミング

- 平日 06:35 ± 2分（tolerance=120秒）
- MonologueThread._try_schedule() から発火
- monologue_schedule.json の `"prompt": "__FACT_BRIEFING__"` で制御

### 処理フロー

```
06:35 → _try_schedule() → __FACT_BRIEFING__ 検出
  → _fetch_fact_briefing()
    → GET /api/fact/today (timeout=15s)
    → レスポンスから要約テキスト構築
    → _call_claude_monologue() で律の口調に変換 (3-5文)
    → on_speak(text, "think") → GUI表示 + TTS再生
```

---

## 3. レスポンス利用フィールド

| フィールド | 用途 |
|-----------|------|
| overall_stance | 総合判断 |
| confidence | 確信度 |
| features.N1 | 支配的ナラティブ |
| features.N3 | センチメント方向 |
| pre_calculated.M3 | ドル円 |
| pre_calculated.S3 | 裁定残高 |
| pre_calculated.S4 | 出来高 |
| active_threads (上位3件) | 主要ナラティブスレッド |
| events_upcoming (5日以内) | 直近イベント |
| contrarian.flag | 逆張りフラグ |
| accuracy_history.last_5_avg | 直近精度 |
| meta.stale | データ鮮度警告 |

---

## 4. エラーハンドリング

| 状況 | 挙動 |
|------|------|
| API接続失敗 | log.warning → ブリーフィングスキップ (non-fatal) |
| HTTP 404 (未評価) | log.info → スキップ |
| HTTP 4xx/5xx | log.warning → スキップ |
| meta.stale=true | 律が「データが古い可能性がある」旨を付記 |
| Claude API失敗 | _call_claude_monologue 内で吸収 → 空文字返却 |

いずれの場合もritsu_v4.pyのプロセスは継続します。

---

## 5. inga-fact側への要望・確認事項

### 確認済み（問題なし）

- `/api/fact/today` のレスポンス形式は要望書通り
- Bearer認証トークン設定済み
- 朝パイプライン 06:30開始 → 06:35取得で十分

### 要望

1. **APIレスポンスの安定性**: `active_threads`, `events_upcoming` が空配列の場合も
   正常レスポンス（200）を返してください（現状問題なし、念のため）
2. **staleフラグの精度**: 前日以前のデータしかない場合に `stale=true` を確実に
   設定してください（ritsu側はこのフラグで注意喚起を出します）
3. **将来的な拡張**: `edinet_highlights` は現時点では未使用ですが、
   律の朝ブリーフィングに「大量保有報告」等を含める拡張を検討中です

---

## 6. テスト方針

- ritsu_v4.py起動後、06:35にログ `Schedule FACT briefing firing: 06:35` を確認
- `_fetch_fact_briefing` が正常にテキスト生成することをログ確認
- API未到達時のフォールバック（スキップ）確認

### 手動テスト用コマンド（Windows PowerShell）

```powershell
python -c "
import os; os.chdir('C:/Users/conqu/Desktop/ritsu-aide')
exec(open('ritsu_v4.py', encoding='utf-8').read().split('# 3. Singleton')[0])
text = _fetch_fact_briefing()
print('Result:', text)
"
```

※ シングルトンガード前までロードしてFACT関数だけ呼ぶ簡易テスト

---

## 7. コミット情報

- リポジトリ: conquestichi/ritsu-aide
- コミット: （patch_fact_briefing.py適用後にcommit予定）
- 変更ファイル: `ritsu_v4.py`, `monologue_schedule.json`, `env.example`
