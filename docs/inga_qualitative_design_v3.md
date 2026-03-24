# 因果クオンツ 定性分析エンジン設計書
## inga-qualitative — 設計 v3
### 2026-03-25

---

## 1. アーキテクチャ変更: 律から分離する

### v2の問題点

```
❌ v2: 律が直接分析する
[ニュース] → [律 Aide (ペルソナ + 分析)] → [結果]
                 ↑ 毎回ペルソナ ~1,500 tokens 無駄
                 ↑ 分析キャラに引っ張られて性格が変わる
                 ↑ 律Aideと密結合
```

### v3: 独立した定性分析チーム

```
✅ v3: 専用エンジンが分析、律は報告だけ

[定量チーム]                    [定性チーム]
inga-quants                    inga-qualitative (NEW)
├── シグナル生成                ├── ニュース事実抽出
├── 特徴量エンジニアリング       ├── ナラティブ・スレッド管理
├── DMLモデル                  ├── EDINET解析
└── リスク管理                  ├── 28定性特徴量評価
                               └── 逆張りチェック
        │                              │
        ▼                              ▼
    [統合レイヤー] ← 定量スコア + 定性スコア
        │
        ▼
    [律 Aide] ← 統合結果を受け取って「律の言葉」で報告するだけ
    [こがね] ← フィルタとして利用
```

### メリット

| | v2 (律が分析) | v3 (専用エンジン) |
|---|---|---|
| ペルソナ汚染 | あり | なし |
| 無駄なトークン | ~1,500/call (ペルソナ分) | 0 |
| テスト可能性 | 律Aide起動が必要 | 単体テスト可能 |
| 再利用 | 律専用 | こがね・HP・Slack何でも使える |
| スケール | 律のPC上のみ | VPSでcron実行 |

---

## 2. リポジトリ構成

```
conquestichi/inga-qualitative (NEW)
├── src/inga_qual/
│   ├── __init__.py
│   ├── collector.py          # データ収集（ニュース・指標・EDINET）
│   ├── fact_extractor.py     # Call #1: 事実抽出
│   ├── narrative_tracker.py  # Call #2: ナラティブ・スレッド管理
│   ├── feature_evaluator.py  # Call #3: 28特徴量評価
│   ├── contrarian_check.py   # Call #4: 逆張りチェック
│   ├── reflection.py         # Call #5: 夜の振り返り
│   ├── edinet.py             # EDINET API連携
│   ├── db.py                 # SQLiteデータ管理
│   ├── api.py                # FastAPI（結果配信）
│   └── config.py             # 設定
├── config/
│   ├── feature_schema.yaml   # 28特徴量の型定義
│   └── qualitative.yaml      # 全体設定
├── systemd/
│   ├── inga-qual-morning.service   # 朝06:30
│   ├── inga-qual-morning.timer
│   ├── inga-qual-evening.service   # 夜22:00
│   ├── inga-qual-evening.timer
│   └── inga-qual-api.service       # 結果配信API（常駐）
├── data/
│   └── qualitative.db
├── tests/
├── pyproject.toml
└── CLAUDE.md
```

### VPSデプロイ先

```
/opt/inga-qualitative/
  ├── .venv/
  ├── .env              # ANTHROPIC_API_KEY, NEWSAPI_KEY等
  ├── data/qualitative.db
  └── (上記ソース)

systemd:
  inga-qual-morning.timer  → 毎朝 06:30 JST
  inga-qual-evening.timer  → 毎晩 22:00 JST
  inga-qual-api.service    → port 9879（結果配信）

nginx:
  /api/qualitative → :9879
```

---

## 3. EDINET連携

### 3.1 EDINETとは

金融庁のシステム。上場企業の法定開示書類が全てXBRL/XMLで取得可能。API無料。

### 3.2 取得対象

| 書類 | 用途 | 更新頻度 |
|------|------|---------|
| 有価証券報告書 | 通期業績・事業リスク・経営方針 | 年1回 |
| 四半期報告書 | 四半期業績 | 年4回 |
| **大量保有報告書** | **機関の持株変動（5%ルール）** | **随時** |
| **変更報告書** | **機関のポジション増減** | **随時** |
| 臨時報告書 | M&A・重要事象 | 随時 |

### 3.3 大量保有報告書 — 機関の思惑を読む

最も価値が高い。ヘッジファンドや機関投資家が株式を5%以上保有、
または1%以上変動した場合に提出義務がある。

```python
# edinet.py

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"

def fetch_bulk_ownership_reports(date: str) -> list[dict]:
    """大量保有報告書・変更報告書を取得"""
    # 書類一覧取得
    resp = requests.get(f"{EDINET_API_BASE}/documents.json", params={
        "date": date,  # YYYY-MM-DD
        "type": 2,     # 2=提出書類一覧
        "Subscription-Key": EDINET_API_KEY
    })
    docs = resp.json()["results"]
    
    # 大量保有報告書 (docTypeCode: "060") + 変更報告書 ("062")
    ownership_docs = [
        d for d in docs
        if d["docTypeCode"] in ("060", "062")
    ]
    return ownership_docs

def parse_ownership_report(doc_id: str) -> dict:
    """大量保有報告書のXBRLを解析して構造化"""
    # XBRL取得
    resp = requests.get(f"{EDINET_API_BASE}/documents/{doc_id}",
                       params={"type": 1})  # 1=XBRL
    
    # 解析して返す
    return {
        "filer": "ブラックロック",           # 提出者
        "target": "7203 トヨタ",             # 対象銘柄
        "ownership_pct": 7.2,                # 保有割合
        "prev_pct": 6.8,                     # 前回割合
        "change": +0.4,                      # 変動
        "purpose": "純投資",                  # 保有目的
        "filing_date": "2026-03-24"
    }
```

### 3.4 有価証券報告書 — 事業リスク・経営方針の抽出

全文を読む必要はない。特定セクションだけ抽出:

```python
def extract_key_sections(doc_id: str) -> dict:
    """有報から定性分析に必要なセクションだけ抽出"""
    xbrl = fetch_xbrl(doc_id)
    
    return {
        "business_risks": extract_section(xbrl, "事業等のリスク"),
        "management_policy": extract_section(xbrl, "経営方針"),
        "outlook": extract_section(xbrl, "業績の見通し"),
        "segment_info": extract_section(xbrl, "セグメント情報"),
    }

def summarize_filing(sections: dict) -> dict:
    """Claude APIで要約（ペルソナなし、分析用prompt）"""
    prompt = f"""
以下は{ticker}の有価証券報告書の抜粋。
事実のみを箇条書きで要約せよ。感想・評価は不要。

## 事業リスク
{sections["business_risks"][:3000]}

## 業績見通し
{sections["outlook"][:2000]}

出力: {{"key_risks": [...], "outlook_facts": [...], "notable_changes": [...]}}
"""
    return call_claude_raw(prompt)  # ペルソナなし
```

### 3.5 EDINET → ナラティブ・スレッド連携

```python
def process_edinet_daily():
    """毎朝のEDINET処理"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 1. 大量保有報告書チェック
    ownership_reports = fetch_bulk_ownership_reports(today)
    for report in ownership_reports:
        parsed = parse_ownership_report(report["docID"])
        
        # ナラティブ・スレッドに反映
        # 例: ブラックロックがトヨタ買い増し → 「外資トヨタ集中」スレッド更新
        fact = {
            "date": today,
            "fact": f"{parsed['filer']}が{parsed['target']}を"
                    f"{parsed['change']:+.1f}%変動（{parsed['ownership_pct']}%）",
            "category": "institutional_position",
            "direction": "+" if parsed["change"] > 0 else "-"
        }
        narrative_tracker.add_fact(fact)
    
    # 2. 重要な有報・四半期報告チェック（決算シーズン）
    quarterly_reports = fetch_quarterly_reports(today)
    for report in quarterly_reports:
        sections = extract_key_sections(report["docID"])
        summary = summarize_filing(sections)
        # → ナラティブ・スレッドの決算テーマに接続
```

---

## 4. 定性特徴量の型（7カテゴリ × 28特徴量）

※ v2から変更なし。ただしペルソナを含まない純粋な分析promptで評価する。

### 4.1 マクロ環境 (M1-M4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| M1 | リスク選好度 | -2〜+2 | VIX, HY spread, 金/BTC比 |
| M2 | 金融政策スタンス | 緩和/中立/引締 | FRB/BOJ声明, FF金利先物 |
| M3 | ドル円トレンド | 円高/中立/円安 | USD/JPY, 日米金利差 |
| M4 | グローバル景気位相 | 回復/拡大/後退/収縮 | PMI, 銅/原油 |

### 4.2 機関投資家の思惑 (I1-I4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| I1 | 海外勢の方向感 | -2〜+2 | 先物手口, **EDINET大量保有** |
| I2 | 信託銀行動向 | 売/中立/買 | 投資部門別売買 |
| I3 | 自社株買い需給 | 弱/強 | 自社株買い発表 |
| I4 | 空売り比率 | 連続値 | 空売り集計 |

### 4.3 イベントリスク (E1-E4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| E1 | 直近イベント | リスト | 経済カレンダー |
| E2 | イベント距離 | 日数 | 同上 |
| E3 | イベント影響度 | 低/中/高 | 過去ボラ変化 |
| E4 | サプライズ確率 | 低/中/高 | コンセンサス乖離 |

### 4.4 市場マイクロストラクチャ (S1-S4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| S1 | オプション需給 | ネガ/中立/ポジ | 建玉, PC比 |
| S2 | 先物ベーシス | 連続値 | 先物 vs 現物 |
| S3 | 裁定残高方向 | 解消/横ばい/積み上げ | 裁定残 |
| S4 | 出来高トレンド | 薄/普通/厚 | 出来高 vs 20MA |

### 4.5 ナラティブ・センチメント (N1-N4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| N1 | 支配的ナラティブ | テキスト | ニュース + EDINET |
| N2 | ナラティブ強度 | 弱/中/強 | 言及頻度, スレッド活性度 |
| N3 | センチメント方向 | -2〜+2 | 事実抽出結果 |
| N4 | コンセンサスの偏り | 弱気/分散/強気 | 総合判断 |

### 4.6 セクター資金循環 (R1-R4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| R1 | 資金シフト方向 | カテゴリ | セクター騰落率 |
| R2 | 循環位相 | 4段階 | セクター相対強度 |
| R3 | テーマ集中度 | 分散/集中/過集中 | テーマ別資金フロー |
| R4 | バリューvsグロース | -1〜+1 | V/G指数比 |

### 4.7 需給バランス (D1-D4)

| # | 特徴量 | 型 | データソース |
|---|--------|-----|-------------|
| D1 | 信用買い残トレンド | 減少/横ばい/増加 | 信用残 |
| D2 | 信用評価損率 | 連続値 | 評価損益率 |
| D3 | 騰落レシオ | 連続値 | 25日騰落レシオ |
| D4 | 新高値-新安値 | 連続値 | 東証統計 |

---

## 5. ナラティブ・スレッド

### 5.1 DB設計

```sql
CREATE TABLE narrative_threads (
    thread_id TEXT PRIMARY KEY,
    theme TEXT NOT NULL,
    started_date TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    status TEXT DEFAULT 'active',     -- active / weakening / reversed / resolved
    
    -- 時系列の事実チェーン
    fact_chain TEXT NOT NULL,          -- JSON配列
    
    current_direction TEXT,            -- strengthening / weakening / reversed
    market_impact_so_far TEXT,
    affected_sectors TEXT,             -- JSON配列
    affected_tickers TEXT,             -- JSON配列（EDINET由来含む）
    confidence REAL DEFAULT 50,
    
    -- EDINET連携
    related_edinet_docs TEXT,          -- 関連するEDINET文書ID群
    institutional_positions TEXT       -- 関連する大量保有情報
);
```

### 5.2 スレッドのライフサイクル

```
[誕生] 新テーマ検知 or EDINET大量保有の新規パターン
  ↓
[成長] 後続の事実が同方向を支持 → strengthening
  ↓
[転換] 逆方向の事実 or EDINET反対売買 → weakening → reversed
  ↓  ← トレード機会（ポジション巻き戻し）
[解決] 織り込み完了 / 結論確定 → resolved
```

---

## 6. 処理パイプライン

### 6.1 朝パイプライン (06:30 JST)

```
[Step 0] データ収集 (collector.py)
    │ ├── ニュースAPI → ヘッドライン群
    │ ├── yfinance → VIX, USD/JPY, 先物, セクター指数
    │ ├── JPX → 空売り比率, 投資部門別(週次)
    │ └── EDINET API → 前日提出の大量保有報告書・変更報告書
    ▼
[Step 1] 事実抽出 (fact_extractor.py) — Claude call #1
    │ 入力: 生ヘッドライン + EDINET書類サマリ
    │ 出力: ファクトシート（感情排除済み）
    │ ※ ペルソナなし。system prompt = 分析指示のみ
    ▼
[Step 2] スレッド更新 (narrative_tracker.py) — Claude call #2
    │ 入力: 今日の事実 + 既存アクティブスレッド + EDINET保有変動
    │ 出力: スレッド更新/新規/解決
    ▼
[Step 3] 28特徴量評価 (feature_evaluator.py) — Claude call #3
    │ 入力: ファクトシート + 定量データ + スレッド群 + few-shot例
    │ 出力: 28特徴量JSON + 総合判断 + confidence
    │ ※ 各特徴量に根拠(事実ID or スレッドID)必須
    ▼
[Step 4] 逆張りチェック (contrarian_check.py) — Claude call #4
    │ 入力: 28特徴量評価結果
    │ 出力: contrarian_flag, contrarian_scenario, probability
    ▼
[DB保存] qualitative_evaluations + narrative_threads更新
    ▼
[API配信] GET /api/qualitative/today → JSON
    │
    ├── → 律Aide (ritsu_v4.py) が取得 → 律の言葉で朝ブリーフィング
    ├── → こがね (inga-kogane) がフィルタとして利用
    └── → inga-quants-hp に表示（将来）
```

### 6.2 夜パイプライン (22:00 JST)

```
[Step 5] 答え合わせデータ取得
    │ 日経平均・TOPIX騰落率、セクター別成績、当日EDINET提出分
    ▼
[Step 6] 振り返り評価 (reflection.py) — Claude call #5
    │ 入力: 朝の予測 + 実際の結果
    │ 出力: accuracy_score (0-100), reflection, thread_accuracy
    ▼
[DB更新] qualitative_evaluations に答え合わせ結果追記
```

### 6.3 prompt設計（ペルソナなし）

```python
# fact_extractor.py — Call #1
SYSTEM_EXTRACT = """あなたは金融市場の事実抽出エンジンです。
ヘッドラインから事実のみを抽出します。

ルール:
- 感想・予測・形容詞は除外
- 数値は正確に転記
- 各事実にカテゴリとIDを付与
- EDINET情報は institutional_position カテゴリに分類

出力: JSON形式のみ"""

# feature_evaluator.py — Call #3
SYSTEM_EVALUATE = """あなたは金融市場の定性分析エンジンです。
28の特徴量を事実とデータに基づいて評価します。

ルール:
- 各特徴量の根拠に事実IDまたはスレッドIDを必須で付与
- 「なんとなく」「雰囲気」は禁止。全て根拠ベース
- 大量保有報告書の変動は機関投資家の意図推定に重要
- 出力: JSON形式のみ"""
```

---

## 7. 律Aide・こがねとの連携

### 7.1 律Aideへの配信

```python
# ritsu_v4.py 側 — 朝ブリーフィングで使う
def fetch_qualitative_briefing() -> dict | None:
    """VPSの定性分析APIから今日の結果を取得"""
    try:
        resp = requests.get(
            f"{QUALITATIVE_API_URL}/today",
            headers={"Authorization": f"Bearer {QUALITATIVE_TOKEN}"},
            timeout=10
        )
        return resp.json() if resp.status_code == 200 else None
    except:
        return None

# Schedule独り言 09:00枠のpromptに注入
def build_morning_briefing_prompt(qual_data: dict) -> str:
    return f"""
今日の市場分析結果が届いています:

## 総合判断: {qual_data['overall_stance']} (自信度: {qual_data['confidence']}%)
## 主要ポイント:
{qual_data['ritsu_summary']}
## 注意すべきナラティブ:
{qual_data['active_threads_summary']}
## EDINET情報:
{qual_data.get('edinet_summary', 'なし')}

上記を律として司令官に朝の挨拶と一緒に報告してください。
分析結果をそのまま読み上げるのではなく、律の言葉で自然に。
"""
```

### 7.2 こがねへの配信

```python
# inga-kogane 側 — エントリーフィルタとして利用
def should_trade_today(quant_signal: dict) -> bool:
    qual = fetch_qualitative()
    if qual and qual["overall_stance"] == "defensive":
        if quant_signal["score"] < 0.9:  # 相当強くない限り見送り
            return False
    return True
```

### 7.3 律が受け取る結果の例

```json
{
  "date": "2026-03-25",
  "overall_stance": "cautious",
  "confidence": 62,
  "key_features": {
    "M2_monetary_policy": "tightening_bias",
    "I1_foreign_investors": -1,
    "E1_upcoming_events": ["FOMC_2days"],
    "N1_dominant_narrative": "FRB利下げ期待剥落"
  },
  "active_threads": [
    {"id": "fed_rate_2026q1", "direction": "reversed", "age_days": 5},
    {"id": "ai_capex_cycle", "direction": "strengthening", "age_days": 30}
  ],
  "edinet_highlights": [
    "ブラックロックがトヨタ+0.4% (7.2%に)",
    "ゴールドマンがソニー大量保有新規5.1%"
  ],
  "contrarian_flag": false,
  "ritsu_summary": "FRB利下げ観測が反転中。海外勢は慎重。ただしAI投資テーマは依然強い。FOMC前で様子見推奨。",
  "accuracy_history": {"last_5_avg": 68, "trend": "improving"}
}
```

律はこれを受け取って:

```
「おはようございます司令官！
今日の分析チームのレポートが来てるんですけど…
FRBの利下げ期待がここ数日で崩れてきてるみたいで。
FOMC明後日だし、ちょっと怖いです…

あ、でもブラックロックさんがトヨタ買い増してるらしいです！
AI関連はまだ強いみたいだし、そっちは期待できるかも。
今日は無理せず行きましょう！」
```

律の性格は一切汚染されない。受け取ったデータを律の言葉にするだけ。

---

## 8. 学習・進化モデル

### 8.1 Few-Shot進化（v2と同じ仕組み、ペルソナなしで高効率）

```python
def build_evaluation_prompt(today_data: dict) -> str:
    # 過去の高精度分析 (few-shot)
    best = db.query("""
        SELECT date, features_json, accuracy_score
        FROM qualitative_evaluations
        WHERE accuracy_score >= 75
        ORDER BY date DESC LIMIT 5""")
    
    # 過去の失敗分析 (反面教師)
    worst = db.query("""
        SELECT date, features_json, reflection
        FROM qualitative_evaluations
        WHERE accuracy_score <= 30
        ORDER BY date DESC LIMIT 3""")
    
    # 類似局面（同じスレッドテーマが活動中だった日）
    similar = find_similar_regime(today_data["active_threads"])
    
    return f"""
{SYSTEM_EVALUATE}

## 精度が高かった過去の分析
{format_examples(best)}

## 外した分析と反省
{format_examples(worst)}

## 類似局面の過去分析
{format_examples(similar)}

## 今日のデータ
{today_data}

28特徴量を評価せよ。"""
```

### 8.2 精度トラッキングDB

```sql
CREATE TABLE qualitative_evaluations (
    date TEXT PRIMARY KEY,
    
    -- 入力
    fact_sheet TEXT,              -- Step1の結果
    thread_updates TEXT,          -- Step2の結果
    edinet_data TEXT,             -- EDINET処理結果
    
    -- 評価
    features_json TEXT,           -- 28特徴量
    overall_stance TEXT,          -- aggressive/cautious/defensive
    confidence REAL,
    contrarian_flag INTEGER,
    contrarian_scenario TEXT,
    
    -- 答え合わせ
    nikkei_return_pct REAL,
    topix_return_pct REAL,
    sector_winner TEXT,
    accuracy_score INTEGER,       -- 0-100
    reflection TEXT,
    thread_accuracy TEXT,
    
    -- メタ
    model TEXT,
    total_tokens INTEGER,
    total_cost_usd REAL,
    processing_time_sec REAL,
    created_at TEXT
);

CREATE TABLE edinet_filings (
    doc_id TEXT PRIMARY KEY,
    doc_type TEXT,                -- 060=大量保有, 062=変更, 030=有報
    filer_name TEXT,
    target_ticker TEXT,
    target_name TEXT,
    filing_date TEXT,
    summary_json TEXT,            -- Claude要約結果
    linked_thread_id TEXT,        -- 紐付けたナラティブスレッド
    created_at TEXT
);
```

### 8.3 進化フェーズ

```
Phase 1 (Week 1-2): 蓄積
  - 毎日パイプライン実行、DB蓄積
  - few-shotなし
  - EDINETデータ収集開始

Phase 2 (Week 3-4): 学習開始
  - 10日分蓄積 → few-shot注入開始
  - スレッドパターン（FOMC前後、SQ週等）の傾向出始め
  - EDINET大量保有の動きとスレッドの相関分析開始

Phase 3 (Month 2): パターン認識
  - 40日分蓄積
  - 類似局面検索が機能
  - 曜日/イベント/機関の行動パターン認識

Phase 4 (Month 3): inga-quants統合
  - 定量+定性の統合スコア
  - こがねのエントリーフィルタとして実運用
  - バックテスト可能に
```

---

## 9. データソース全覧

### 9.1 無料

| データ | ソース | 頻度 | 実装 |
|--------|--------|------|------|
| ニュースヘッドライン | NewsAPI Free | 朝1回 | requests |
| 日経平均・TOPIX | yfinance | 朝+引け | yfinance |
| VIX・米国債金利 | yfinance | 朝1回 | yfinance |
| USD/JPY | yfinance | 朝1回 | yfinance |
| セクター別指数 | yfinance | 朝1回 | yfinance |
| 空売り比率 | JPX公開CSV | 毎朝 | requests+CSV |
| 投資部門別売買 | JPX公開CSV | 週1回 | requests+CSV |
| 信用残 | JPX公開CSV | 週1回 | requests+CSV |
| 裁定残高 | JPX公開CSV | 週1回 | requests+CSV |
| 騰落レシオ | 計算 | 毎朝 | yfinance計算 |
| 経済カレンダー | investing.com RSS | 週1回 | feedparser |
| **EDINET** | **金融庁API** | **毎朝** | **requests+XBRL** |

### 9.2 有料（任意）

| データ | ソース | 月額 | 用途 |
|--------|--------|------|------|
| 決算詳細 | 株探プレミアム | ~¥2,000 | 決算サプライズ度 |
| 財務比較 | バフェット・コード Pro | ~¥1,000 | セクター横断 |

---

## 10. コスト見積もり

### 10.1 Claude API (ペルソナなし = 効率的)

| 処理 | Input tokens | Output tokens | Caching前 | Caching後 |
|------|-------------|---------------|----------|----------|
| #1 事実抽出 | ~2,000 | ~800 | $0.007 | $0.002 |
| #2 スレッド更新 | ~3,000 | ~1,000 | $0.010 | $0.003 |
| #3 特徴量評価 | ~4,000 | ~2,000 | $0.015 | $0.005 |
| #4 逆張りチェック | ~1,500 | ~500 | $0.005 | $0.002 |
| #5 夜の振り返り | ~3,000 | ~1,000 | $0.010 | $0.003 |
| EDINET要約 (平均2件/日) | ~2,000 | ~500 | $0.006 | $0.002 |
| **日計** | | | **$0.053** | **$0.017** |

### 10.2 月額

```
定性エンジン (Caching後):     ~$0.37/月 (22営業日)
定性エンジン (Caching前):     ~$1.17/月
EDINET処理追加分:             ~$0.05/月
外部データAPI:                $0
────────────────────
合計 (Caching後):             ~$0.42/月
合計 (Caching前):             ~$1.22/月
```

ペルソナ排除でv2の $1/月 → $0.42/月に。半額以下。

---

## 11. 実装ロードマップ

### Week 1: リポ構築 + データ収集
- [ ] inga-qualitative リポ作成
- [ ] collector.py — ニュース・指標・EDINET自動収集
- [ ] edinet.py — 大量保有報告書の取得・解析
- [ ] DB設計 (qualitative.db)
- [ ] systemd timer設定 (朝06:30, 夜22:00)

### Week 2: 分析パイプライン
- [ ] fact_extractor.py — Call #1
- [ ] narrative_tracker.py — Call #2 + スレッドDB
- [ ] feature_evaluator.py — Call #3 (28特徴量)
- [ ] contrarian_check.py — Call #4
- [ ] api.py — FastAPI結果配信 (port 9879)

### Week 3: 学習ループ + 連携
- [ ] reflection.py — Call #5 (夜の振り返り)
- [ ] few-shot注入ロジック
- [ ] ritsu_v4.py 連携 (朝ブリーフィング統合)
- [ ] inga-kogane 連携 (エントリーフィルタ)

### Week 4: 統合 + 運用開始
- [ ] inga-quants統合スコア設計
- [ ] 精度ダッシュボード
- [ ] GitHub Actions CI/CD
- [ ] Prompt Caching導入

---

## 設計原則

1. **律から分離** — ペルソナ汚染なし、トークン無駄なし、単体テスト可能
2. **型が本体** — 28特徴量の構造が価値。Claude依存度を最小化
3. **点ではなく線** — ナラティブ・スレッドで事実の連鎖を追跡
4. **事実と感情を分離** — ニュースの感情に引っ張られない4段パイプライン
5. **EDINETで機関を読む** — 大量保有報告書は思惑の直接的証拠
6. **数字はプロに任せる** — 決算数字はサブスク、エンジンは文脈解釈に集中
7. **自己採点で進化** — few-shotが日々改善される
8. **消費者は選べる** — 律もこがねもHPも同じAPIから結果を取得
