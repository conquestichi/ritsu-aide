# ritsu-aide × inga-fact 連携要望書
## 朝ブリーフィング機能追加

**作成日**: 2026-03-26
**対象リポ**: conquestichi/ritsu-aide
**依存**: inga-fact API（VPS 160.251.167.44:9879、実装済み・稼働中）

---

## 1. 概要

ritsu_v4.py の MonologueThread に「朝ブリーフィング」機能を追加する。
平日 06:35（inga-fact朝パイプライン完了直後）に inga-fact API から
当日の定性評価を取得し、律が市場概況を報告する。

---

## 2. inga-fact API 仕様（実装済み）

### エンドポイント

```
GET http://160.251.167.44:9879/api/fact/today
Authorization: Bearer _n1OnwZ4yZ8vR_DmHncegL-CTSTc6JL_HBZ7wvPNDiI
```

### レスポンス例

```json
{
  "date": "2026-03-26",
  "overall_stance": "cautious",
  "confidence": 62,
  "features": {
    "M1": {"value": -1, "evidence": ["F3", "T2"], "label": "リスク選好度"},
    "N1": {"value": "FRB利下げ期待剥落", "evidence": ["T2"], "label": "支配的ナラティブ"},
    "N3": {"value": -1, "evidence": ["T2", "F1"], "label": "センチメント方向"},
    "I1": {"value": -1, "evidence": ["E_S100XXXX"], "label": "海外勢の方向感"},
    "E1": {"value": ["FOMC", "米雇用統計"], "evidence": ["CAL"], "label": "直近イベント"},
    "E2": {"value": 2, "evidence": ["CAL"], "label": "イベント距離"}
  },
  "pre_calculated": {
    "M3": {"value": "yen_weak", "logic": "5日MA=155.2 vs 20日MA=153.8"},
    "S3": {"value": "unwinding", "logic": "5日変化-3.2%"},
    "S4": {"value": "thin", "logic": "ratio=0.72"}
  },
  "active_threads": [
    {"thread_id": "T2", "theme": "FRB利下げ期待剥落", "direction": "strengthening", "confidence": 75, "age_days": 5},
    {"thread_id": "T5", "theme": "半導体規制強化", "direction": "weakening", "confidence": 45, "age_days": 12}
  ],
  "contrarian": {"flag": false, "scenario": null},
  "events_upcoming": [
    {"event": "FOMC", "date": "2026-03-28", "days_until": 2, "importance": "high"},
    {"event": "米雇用統計", "date": "2026-04-03", "days_until": 8, "importance": "high"}
  ],
  "accuracy_history": {"last_5_avg": 58.4},
  "meta": {"model": "claude-sonnet-4", "stale": false, "degraded": false}
}
```

### ヘルスチェック（認証不要）

```
GET http://160.251.167.44:9879/api/fact/health
→ {"status": "ok", "last_evaluation": "2026-03-26", "active_threads": 5}
```

---

## 3. 実装仕様

### 3.1 環境変数追加（.env）

```env
RITSU_FACT_ENABLE=1
RITSU_FACT_API_URL=http://160.251.167.44:9879/api/fact/today
RITSU_FACT_API_TOKEN=_n1OnwZ4yZ8vR_DmHncegL-CTSTc6JL_HBZ7wvPNDiI
```

### 3.2 Configuration セクション（既存パターンに合わせる）

```python
FACT_ENABLE = env_int("RITSU_FACT_ENABLE", 0)
FACT_API_URL = env("RITSU_FACT_API_URL", "http://160.251.167.44:9879/api/fact/today")
FACT_API_TOKEN = env("RITSU_FACT_API_TOKEN", "")
```

### 3.3 monologue_schedule.json にスロット追加

```json
{
  "time": "06:35",
  "prompt": "__FACT_BRIEFING__",
  "weekdays": [0,1,2,3,4]
}
```

`__FACT_BRIEFING__` は特殊トークン。MonologueThread._try_schedule() で検出したら
通常のClaude APIコールではなく `_fetch_and_brief_fact()` を呼ぶ。

### 3.4 ブリーフィング取得・生成関数

```python
def _fetch_fact_briefing() -> str | None:
    """inga-fact APIから定性評価を取得し、律のブリーフィング用テキストを生成。"""
    if not FACT_ENABLE or not FACT_API_URL:
        return None
    import requests as req
    try:
        headers = {}
        if FACT_API_TOKEN:
            headers["Authorization"] = f"Bearer {FACT_API_TOKEN}"
        resp = req.get(FACT_API_URL, headers=headers, timeout=15)
        if resp.status_code != 200:
            log.warning("Fact API HTTP %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        log.warning("Fact API error: %s", e)
        return None

    # Claude APIで律の口調に変換
    stance = data.get("overall_stance", "cautious")
    confidence = data.get("confidence", 0)
    threads = data.get("active_threads", [])
    events = data.get("events_upcoming", [])
    contrarian = data.get("contrarian", {})
    accuracy = data.get("accuracy_history", {})
    features = data.get("features", {})
    pre_calc = data.get("pre_calculated", {})
    meta = data.get("meta", {})

    # 主要情報を要約テキスト化
    top_threads = threads[:3]
    thread_lines = []
    for t in top_threads:
        thread_lines.append(f"  - {t['theme']}（{t['direction']}, 確信度{t['confidence']}）")
    
    near_events = [e for e in events if e.get("days_until", 99) <= 5]
    event_lines = [f"  - {e['event']}（{e['days_until']}日後, {e['importance']}）" for e in near_events]

    narrative = features.get("N1", {}).get("value", "不明")
    sentiment = features.get("N3", {}).get("value", 0)
    
    summary = (
        f"日付: {data.get('date')}\n"
        f"総合判断: {stance}（確信度{confidence}）\n"
        f"支配的ナラティブ: {narrative}\n"
        f"センチメント: {sentiment}\n"
        f"ドル円: {pre_calc.get('M3', {}).get('value', '不明')}\n"
        f"裁定残高: {pre_calc.get('S3', {}).get('value', '不明')}\n"
        f"出来高: {pre_calc.get('S4', {}).get('value', '不明')}\n"
        f"主要スレッド:\n" + "\n".join(thread_lines) + "\n"
        f"直近イベント:\n" + "\n".join(event_lines) + "\n"
        f"逆張りフラグ: {'発動中' if contrarian.get('flag') else 'なし'}\n"
        f"直近5日精度: {accuracy.get('last_5_avg', '未集計')}\n"
        f"stale: {meta.get('stale', False)}"
    )

    # 律の口調で変換
    prompt = (
        f"以下はinga-factの今朝の市場定性評価です。"
        f"これを{PERSONA_CALL_USER}への朝ブリーフィングとして、"
        f"律の口調で簡潔に報告してください（3-5文）。"
        f"重要なポイントだけ。数値の羅列は不要。\n\n{summary}"
    )
    result = _call_claude_monologue(prompt)
    return result.get("reply_text")
```

### 3.5 MonologueThread._try_schedule() の修正

既存の `_try_schedule()` メソッド内で `__FACT_BRIEFING__` を検出する分岐を追加:

```python
# 既存コードの prompt 処理部分に追加
prompt = slot.get("prompt", "独り言を一言")
if prompt == "__FACT_BRIEFING__":
    text = _fetch_fact_briefing()
    if text:
        self.on_speak(text, "think")
    self._fired_schedule_slots.add(slot_time)
    self._last_monologue_time = time.time()
    continue  # 通常のClaude API呼び出しをスキップ
```

---

## 4. エラーハンドリング

- API接続失敗 → ログ出力のみ、ブリーフィングはスキップ（non-fatal）
- stale=true → 律が「今朝のデータは古い可能性があります」と付記
- 404（まだ当日評価がない） → スキップ

---

## 5. テスト方針

- `_fetch_fact_briefing()` の単体テスト（APIモック）
- `__FACT_BRIEFING__` 検出の分岐テスト
- API接続失敗時のフォールバック確認

---

## 6. 注意事項

- ritsu_v4.py の1ファイル原則は維持
- requests は既に import 済み（KoganeWatcherThread内）
- `_call_claude_monologue()` は既存の独り言用API呼び出し関数をそのまま使用
- inga-fact APIのタイミング: 朝パイプラインは06:30開始、通常1-3分で完了。
  06:35のスケジュールなら完了後に取得できる。万一未完了なら404が返る。
