# inga-fact Phase 0: プロジェクトメモ5枚作成 指示書
## 2026-03-25

---

## 目的

この指示書は、Claude Projectルーム「因果ファクト (inga-fact)」で
5枚のプロジェクトメモを作成するための精密な仕様書である。

5枚が揃ったらPhase分けして開発に入る。

---

## 前提知識（新しいProjectルームに伝えるべき文脈）

### 因果クオンツ・ファミリーの全体像

```
[inga-quants]      テクニカル分析エンジン（既存・稼働中）
                   価格・出来高パターンからシグナル生成
                   DML(Double Machine Learning)モデル
                   VPS: /opt/inga-quants/
                   
[inga-fact]        ファクト分析エンジン（これから作る）
                   テクニカル以外の全事実を扱う
                   EDINET, ニュース, 需給, マクロ
                   VPS: /opt/inga-fact/ (予定)

[inga-kogane]      自動売買エンジン + こがねLINE bot（既存・稼働中）
                   quants/factのシグナルを消費してトレード実行
                   VPS: /opt/inga-kogane/

[ritsu-aide]       律AIアシスタント（既存・稼働中）
                   factの分析結果を律の言葉で司令官に報告
                   Windows: ritsu_v4.py

[inga-ritsu-pao]   律X投稿+YouTube配信パイプライン（既存・稼働中）

[inga-quants-hp]   公開サイト ingaquants.jp（既存・稼働中）
                   Next.js, factの結果も将来表示予定
```

### inga-factの位置づけ

投資判断に必要な情報を4分類した時:

```
A. 価格が語ること（テクニカル）      → inga-quants（既存）
B. 企業が語ること（ファンダメンタル）  → inga-fact/fundamental
C. 市場参加者が語ること（需給）       → inga-fact/flow
D. 世界が語ること（マクロ・ナラティブ）→ inga-fact/narrative
```

B+C+D = 「テクニカル以外の全事実」= inga-fact の守備範囲。
1リポジトリ内の3モジュール構成。

### VPS環境

- サーバー: 160.251.167.44 (Ubuntu 24.04)
- Python: 3.12+
- デプロイ: GitHub Actions → SSH
- 他サービスとの通信: localhost API (nginx リバースプロキシ)
- DB: SQLite (WALモード)
- Claude API: anthropic SDK, Prompt Caching対応

### 共通設計原則（全因果プロジェクト共通）

1. 設定は.env（ハードコード禁止）
2. .env / *.sqlite は .gitignore（絶対にcommitしない）
3. systemd でプロセス管理
4. テストは pytest
5. Claude APIはペルソナなし（分析専用prompt）
6. Prompt Cachingで変わらない部分はキャッシュ

---

## 5枚のプロジェクトメモ仕様

### ■ メモ1: 全体アーキテクチャ + 重要情報
**ファイル名**: `inga_fact_memo_1_architecture.md`

**このメモの役割**: 
他の4枚の土台。リポ構成、DB設計、API設計、デプロイ構成、
他システムとの連携インタフェース、環境情報を網羅する。
開発中に何度も参照される「地図」。

**必須コンテンツ**:

1. **リポジトリ構成** — ディレクトリツリー全体像
   ```
   inga-fact/
   ├── src/inga_fact/
   │   ├── fundamental/
   │   ├── flow/
   │   ├── narrative/
   │   ├── shared/        # 共通基盤
   │   ├── evaluator.py   # 28特徴量統合評価
   │   ├── api.py          # FastAPI配信
   │   └── config.py
   ├── config/
   ├── systemd/
   ├── tests/
   └── data/
   ```

2. **DB設計** — 全テーブルのCREATE文とリレーション
   - qualitative_evaluations（日次評価結果）
   - narrative_threads（ナラティブスレッド）
   - edinet_filings（EDINET書類キャッシュ）
   - fact_archive（事実アーカイブ）
   - accuracy_log（精度追跡）
   各テーブルのカラム、型、制約を完全に定義する。

3. **API設計** — FastAPIエンドポイント一覧
   - GET /api/fact/today — 今日の評価結果
   - GET /api/fact/threads — アクティブなナラティブスレッド
   - GET /api/fact/history?days=N — 過去N日の評価
   - GET /api/fact/accuracy — 精度推移
   - GET /api/fact/edinet/recent — 直近のEDINET重要書類
   各エンドポイントのリクエスト/レスポンスJSONスキーマを定義。
   認証方式（Bearer token）。

4. **他システム連携インタフェース**
   - inga-quants → inga-fact: なし（独立）
   - inga-fact → inga-quants: 将来の統合スコアAPI設計（予約）
   - inga-fact → ritsu-aide: 朝ブリーフィング用データ取得API
   - inga-fact → inga-kogane: エントリーフィルタ用API
   - inga-fact → inga-quants-hp: HP表示用API（将来）
   各連携のデータフォーマット、呼び出しタイミング、
   認証方式を定義。

5. **デプロイ構成**
   - VPSパス: /opt/inga-fact/
   - systemdサービス/タイマー一覧
     - inga-fact-morning.timer (06:30 JST)
     - inga-fact-evening.timer (22:00 JST)
     - inga-fact-api.service (常駐, port 9879)
   - nginx設定
   - GitHub Actions CI/CDワークフロー
   - .envの環境変数一覧（キー名・用途・デフォルト値）

6. **28特徴量スキーマ** — feature_schema.yaml の完全仕様
   28特徴量それぞれの:
   - ID (M1, M2, ... D4)
   - 名前
   - 型（数値範囲 or カテゴリ値）
   - 必須/任意
   - 根拠フィールドの形式
   出力JSONの完全なサンプルを付ける。

7. **環境情報**
   - VPS接続情報
   - 既存ポート使用状況（9876:こがねadmin, 9877:こがねLINE, 9878:律LINE, 9879:fact-api）
   - GitHub PAT（リポ作成用）
   - EDINET APIキー取得方法

---

### ■ メモ2: fundamental モジュール
**ファイル名**: `inga_fact_memo_2_fundamental.md`

**このメモの役割**:
企業の数字を扱うモジュールの完全仕様。
EDINET財務データのパース、決算処理、バリュエーション計算。

**必須コンテンツ**:

1. **モジュール構成**
   ```
   fundamental/
   ├── edinet_financial.py   # 有報・四半期報の財務諸表パース
   ├── earnings.py           # 決算サプライズ判定
   └── valuation.py          # PER/PBR/ROE等の計算
   ```

2. **EDINET財務データ処理**
   - 対象書類: 有価証券報告書(docTypeCode:030), 四半期報告書(043)
   - XBRL/iXBRLからの財務数値抽出方法
   - 抽出する財務項目一覧:
     売上高、営業利益、経常利益、純利益、総資産、純資産、
     自己資本比率、営業CF、有利子負債、配当、セグメント別等
   - タクソノミ（jp-gaap / ifrs / us-gaap）の差異吸収方法
   - パース結果のJSONスキーマ
   - 保存先テーブル設計（edinet_financials）

3. **決算サプライズ処理**
   - コンセンサス予想の取得方法（サブスクに任せる箇所の明示）
   - サプライズ度の定義（実績 vs 予想の乖離率）
   - 上方修正/下方修正の検知ロジック
   - 決算集中期（4月, 7月, 10月, 1月）の処理負荷見積もり
   - 出力: ナラティブモジュールへの決算ファクト送出フォーマット

4. **バリュエーション計算**
   - 計算対象: PER, PBR, ROE, 配当利回り, EV/EBITDA
   - 株価データソース（yfinance）
   - セクター別中央値との比較
   - 出力フォーマット

5. **特徴量への接続**
   - fundamentalモジュールが直接生成する特徴量はない
   - inga-quantsの特徴量パイプラインへのデータ供給方法
   - ナラティブモジュールへのファクト供給フォーマット
     （例: 「トヨタQ3営業利益+15%、コンセンサス比+8%サプライズ」→事実として渡す）

6. **処理タイミング**
   - 有報: 提出日翌朝の06:30バッチで処理
   - 四半期報: 同上
   - 決算速報: 15:30以降のTDnet連携（将来）
   - バリュエーション更新: 毎朝06:30

7. **コスト**
   - EDINET API: 無料
   - Claude API使用: 有報の事業リスク/見通し要約のみ（1件~$0.003）
   - 決算集中期のピーク処理見積もり

---

### ■ メモ3: flow モジュール
**ファイル名**: `inga_fact_memo_3_flow.md`

**このメモの役割**:
市場参加者のポジション・需給を追跡するモジュールの完全仕様。
「誰が何をしているか」の事実ベース分析。

**必須コンテンツ**:

1. **モジュール構成**
   ```
   flow/
   ├── edinet_ownership.py    # 大量保有報告書・変更報告書
   ├── short_selling.py       # 空売り比率（日次）
   ├── margin.py              # 信用残（週次）
   ├── arbitrage.py           # 裁定残高（週次）
   └── sector_flow.py         # 投資部門別売買（週次）
   ```

2. **EDINET大量保有報告書処理**（最重要）
   - 対象書類: 大量保有報告書(060), 変更報告書(062)
   - XBRLから抽出する情報:
     提出者名、対象銘柄、保有割合(%)、前回割合、変動幅、
     保有目的（純投資/経営参加/政策保有）、共同保有者
   - 提出者の分類:
     外資系ファンド / 国内機関 / 事業会社 / 個人大株主
   - 保有変動のシグナル化:
     +1%以上の増加 = 強い買いシグナル
     新規5%超え = 注目
     減少 = 利確 or 撤退
   - ナラティブスレッドへの接続方法
   - 保存先テーブル（edinet_ownership）

3. **空売り比率**
   - ソース: JPX日次公開CSV
   - URL、取得方法、パースロジック
   - 時系列保存（daily_short_ratio テーブル）
   - 異常値検知（40%超え、45%超え等の閾値）

4. **信用残**
   - ソース: JPX週次公開
   - 信用買い残、信用売り残、信用倍率
   - 評価損益率の計算
   - 時系列保存と傾向判定ロジック

5. **裁定残高**
   - ソース: JPX週次公開
   - 裁定買い残、裁定売り残
   - 増減トレンドの判定

6. **投資部門別売買**
   - ソース: JPX週次公開
   - 海外投資家、信託銀行、個人、事業法人
   - 各部門の方向性判定ロジック
   - 過去N週との比較

7. **特徴量への接続**
   - I1（海外勢の方向感）← sector_flow + edinet_ownership
   - I2（信託銀行動向）← sector_flow
   - I3（自社株買い需給）← edinet_ownership（事業会社の自社取得）
   - I4（空売り比率）← short_selling
   - S3（裁定残高方向）← arbitrage
   - D1（信用買い残トレンド）← margin
   - D2（信用評価損率）← margin
   各特徴量とデータソースの対応を明示。

8. **処理タイミング**
   - 空売り比率: 毎朝06:30（日次データ）
   - EDINET大量保有: 毎朝06:30（前日提出分）
   - 信用残/裁定残/投資部門別: 毎週金曜公開 → 翌営業日06:30
   - 週次データの「前週比」計算方法

9. **コスト**
   - 全データ: 無料
   - Claude API使用: なし（数値処理のみ）

---

### ■ メモ4: narrative モジュール
**ファイル名**: `inga_fact_memo_4_narrative.md`

**このメモの役割**:
市場を取り巻く環境・物語を追跡するモジュールの完全仕様。
ニュース処理、ナラティブスレッド管理、マクロ指標、
Claude APIパイプラインの詳細設計。
唯一Claude APIを多用するモジュール。

**必須コンテンツ**:

1. **モジュール構成**
   ```
   narrative/
   ├── news_collector.py      # ニュース取得
   ├── fact_extractor.py      # Call #1: 事実抽出
   ├── thread_tracker.py      # Call #2: ナラティブスレッド管理
   ├── macro_indicators.py    # VIX/為替/金利等の定量指標取得
   ├── event_calendar.py      # 経済カレンダー
   └── contrarian.py          # Call #4: 逆張りチェック
   ```

2. **ニュース取得**
   - NewsAPI (Free tier: 100req/日)
   - 取得カテゴリ: business, general（日本+グローバル）
   - 取得件数: 朝1回、最大50件
   - 代替ソース検討: RSS (NHK, 日経等)
   - ヘッドライン保存テーブル（news_headlines）

3. **4段パイプライン詳細設計**

   **Call #1: 事実抽出 (fact_extractor.py)**
   - system prompt全文（ペルソナなし、分析指示のみ）
   - 入力フォーマット: 生ヘッドライン + EDINET書類サマリ
   - 出力JSONスキーマ:
     ```json
     {"facts": [
       {"id": "F1", "what": "...", "category": "monetary_policy|earnings|geopolitical|...", 
        "region": "JP|US|EU|CN|...", "source": "..."}
     ]}
     ```
   - カテゴリ体系の完全定義（10-15個程度）
   - 除外ルール: 感想、予測、形容詞、重複の排除方法
   - 想定Input/Output tokens

   **Call #2: ナラティブスレッド更新 (thread_tracker.py)**
   - system prompt全文
   - 入力: 今日の事実 + 既存アクティブスレッド + EDINET保有変動 + fundamental決算ファクト
   - 出力JSONスキーマ:
     ```json
     {"updated_threads": [...], "new_threads": [...], "resolved_threads": [...]}
     ```
   - スレッドのライフサイクルルール:
     - 作成条件: 新テーマの事実2件以上 or EDINET大量保有新規
     - 更新: fact_chainに追加、direction再評価
     - 解決: 3営業日更新なし or 明示的解決
     - 反転検知: direction逆転時のフラグ（最重要シグナル）
   - アクティブスレッド数の上限（10-15本程度）
   - 想定Input/Output tokens

   **Call #3: 28特徴量評価 (evaluator.py — メモ5で詳細)**
   - narrativeモジュールからの入力データフォーマットのみ記載

   **Call #4: 逆張りチェック (contrarian.py)**
   - system prompt全文
   - 入力: 28特徴量の評価結果
   - 出力:
     ```json
     {"contrarian_flag": true/false, "consensus_direction": "...",
      "contrarian_scenario": "...", "probability": 0-100,
      "reasoning": "..."}
     ```
   - 逆張りフラグの閾値定義
   - 想定Input/Output tokens

4. **マクロ指標取得**
   - yfinance経由:
     ^N225（日経平均）, ^GSPC（S&P500）, ^VIX, ^TNX（米10年債）,
     USDJPY=X, 原油(CL=F), 金(GC=F), 銅(HG=F), BTC-USD
   - 取得タイミング: 朝06:30
   - 計算項目: 前日比、5日移動平均比、20日移動平均比
   - 保存テーブル（daily_macro_indicators）

5. **経済カレンダー**
   - ソース: investing.com RSS or 手動YAML管理
   - 管理項目: イベント名、日時、重要度(低/中/高)、
     影響対象(FX/株/債券)、コンセンサス予想
   - FOMC/BOJ/SQ/雇用統計/CPI 等のメジャーイベント
   - 直近イベントまでの日数自動計算

6. **Prompt Caching設計**
   - キャッシュ対象（毎回変わらない部分）:
     system prompt + 特徴量スキーマ定義 + few-shot例
   - 非キャッシュ対象（毎回変わる部分）:
     今日のニュース、指標データ、スレッド現状
   - anthropic SDK での cache_control 実装方法
   - 想定キャッシュヒット率とコスト削減効果

7. **特徴量への接続**
   - M1（リスク選好度）← macro_indicators (VIX, 金/BTC比)
   - M2（金融政策スタンス）← fact_extractor + thread_tracker
   - M3（ドル円トレンド）← macro_indicators
   - M4（グローバル景気位相）← macro_indicators + fact_extractor
   - E1-E4（イベントリスク）← event_calendar
   - N1-N4（ナラティブ）← thread_tracker
   - S1（オプション需給）← 将来拡張
   - S2（先物ベーシス）← macro_indicators
   - S4（出来高トレンド）← macro_indicators
   - R1-R4（セクター循環）← macro_indicators + thread_tracker

8. **コスト詳細**
   - Call別のInput/Output tokens見積もり
   - Caching前/後の1日あたりコスト
   - 月額コスト（営業日22日ベース）
   - EDINETピーク時（決算集中期）の追加コスト

---

### ■ メモ5: 統合・学習・監査・出力
**ファイル名**: `inga_fact_memo_5_integration.md`

**このメモの役割**:
3モジュールの出力を統合して28特徴量を評価し、
学習ループで精度を上げ、結果を配信するレイヤーの完全仕様。

**必須コンテンツ**:

1. **統合評価 (evaluator.py) — Call #3**
   - system prompt全文（ペルソナなし）
   - 入力データの統合方法:
     narrative → ファクトシート + スレッド群 + マクロ指標
     flow → 需給データ (空売り, 信用残, 投資部門別, EDINET保有)
     fundamental → 決算ファクト (サプライズ度, ガイダンス変更)
   - 28特徴量の出力JSONスキーマ（完全版サンプル付き）
   - 各特徴量の根拠(fact_id / thread_id / data_source)必須ルール
   - 総合判断: aggressive / cautious / defensive
   - confidence: 0-100
   - 想定Input/Output tokens

2. **日次パイプライン統合フロー**
   ```
   06:30 朝パイプライン
     Step 0: collector（全データ収集）
     Step 1: fact_extractor（事実抽出）
     Step 2: thread_tracker（スレッド更新）
     Step 3: evaluator（28特徴量評価）
     Step 4: contrarian（逆張りチェック）
     → DB保存 → API配信
   
   22:00 夜パイプライン
     Step 5: 答え合わせデータ取得
     Step 6: reflection（振り返り評価）
     → DB更新
   ```
   各Stepのエラーハンドリング方針:
   - 前段が失敗した場合の挙動
   - Claude API障害時のフォールバック
   - データ未取得時の部分評価ルール

3. **学習・進化メカニズム**
   
   **Few-Shot選択アルゴリズム:**
   - 高精度分析の選出条件（accuracy_score >= 75、直近N件）
   - 失敗分析の選出条件（accuracy_score <= 30、直近N件）
   - 類似局面検索ロジック:
     同じナラティブスレッドが活動中だった日
     同じイベント直前だった日
     同じマクロレジーム(M1-M4が近い値)だった日
   - Few-Shotの注入位置（system prompt末尾 vs user message内）
   - 注入するトークン数の上限管理

   **精度スコアリング (Call #5: reflection.py):**
   - system prompt全文
   - 入力: 朝の予測(features_json, overall_stance) + 実際の結果(騰落率, セクター成績)
   - 採点基準:
     overall_stance が方向を当てた: +40点
     上位3特徴量の根拠が正しかった: +30点
     ナラティブスレッドの読みが当たった: +20点
     逆張りフラグの適切性: +10点
   - 出力:
     ```json
     {"accuracy_score": 72, "reflection": "...",
      "thread_accuracy": {"fed_rate": "correct", "ai_capex": "wrong_timing"},
      "lesson_learned": "信託銀行の動きを過小評価した"}
     ```

   **進化フェーズ管理:**
   - Phase 1 (蓄積期): データ件数 < 10 → few-shotなし
   - Phase 2 (学習期): 10 <= 件数 < 40 → few-shot開始
   - Phase 3 (安定期): 件数 >= 40 → 類似局面検索も稼働
   - フェーズは自動判定（DB件数ベース）

4. **監査機能**
   
   **精度ダッシュボード:**
   - 日次精度スコア推移
   - 週次平均精度
   - 特徴量別の的中率（どの特徴量が当たりやすいか）
   - スレッド別の精度（どのテーマの分析が得意/苦手か）
   - 逆張りフラグの実績（フラグ発動時の翌日騰落率）

   **異常検知:**
   - 5日連続 accuracy < 40 → アラート
   - スレッドが20本超え → 自動アーカイブ促進
   - 同じfew-shot例が30日以上使われ続けている → 入替え推奨

   **コスト追跡:**
   - 日次のtotal_tokens, total_cost_usd を記録
   - 月次サマリ自動生成
   - Prompt Cachingのヒット率モニタリング

5. **出力・配信**

   **API設計詳細 (api.py):**
   - FastAPI実装パターン
   - 認証: Bearer token（.envで管理）
   - レスポンスフォーマット:
     ```json
     {
       "date": "2026-03-25",
       "overall_stance": "cautious",
       "confidence": 62,
       "features": { "M1": {"value": -1, "evidence": "F3,T2"}, ... },
       "active_threads": [...],
       "contrarian": {"flag": false, "scenario": "..."},
       "edinet_highlights": [...],
       "accuracy_history": {"last_5_avg": 68, "trend": "improving"},
       "meta": {"model": "claude-sonnet-4", "cost_usd": 0.018}
     }
     ```

   **消費者別のデータ加工:**
   - ritsu-aide向け: 上記JSONそのまま。律が受け取って言葉にする
   - inga-kogane向け: overall_stance + confidence + event_riskのみ（軽量）
   - inga-quants-hp向け: 全データ + 可視化用のメタ情報（将来）

   **データ保持ポリシー:**
   - qualitative_evaluations: 永久保存（学習に使う）
   - narrative_threads: resolved後90日で物理削除
   - edinet_filings: 永久保存
   - news_headlines: 30日で削除（生データは不要）
   - daily_macro_indicators: 永久保存

6. **inga-quants統合（Phase 4以降）**
   - 統合スコア = α × 定量 + (1-α) × 定性
   - αの動的調整ルール
   - 統合APIのインタフェース設計（予約）
   - バックテスト方法（過去の定量シグナル×定性フィルタの効果検証）

---

## プロジェクトルーム設定方法

### Claudeプロジェクト名
`因果ファクト (inga-fact)`

### プロジェクトファイルとして登録するもの
1. この指示書 (`inga_fact_phase0_instructions.md`) — 作成ガイド
2. v3設計書 (`inga_qualitative_design_v3.md`) — 元の設計思想
3. 完成した5枚のメモ — 順次追加

### Custom Instructions（プロジェクト説明）に書くこと
```
因果ファクト(inga-fact)は因果クオンツ・ファミリーの定性分析エンジン。
テクニカル以外の全事実（ファンダメンタル・需給・マクロ・ナラティブ）を扱う。
EDINET, ニュースAPI, JPX統計を入力とし、28の定性特徴量を毎朝評価する。

リポ: conquestichi/inga-fact (未作成)
VPS: 160.251.167.44, /opt/inga-fact/
Stack: Python, FastAPI, SQLite, Claude API (Sonnet), systemd

プロジェクトファイルの5枚のメモが設計の全て。
開発はこの5枚に基づいてPhase分けで進める。
```

---

## 作業手順

### Step 1: プロジェクトルーム作成
1. Claude.ai → Projects → New Project
2. 名前: `因果ファクト (inga-fact)`
3. Custom Instructions に上記テキスト設定
4. この指示書とv3設計書をプロジェクトファイルに追加

### Step 2: メモ5枚を順番に作成
1. 「メモ1を作成して」→ レビュー → 確定
2. 「メモ2を作成して」→ レビュー → 確定
3. 「メモ3を作成して」→ レビュー → 確定
4. 「メモ4を作成して」→ レビュー → 確定
5. 「メモ5を作成して」→ レビュー → 確定

各メモはこの指示書の「必須コンテンツ」を全て含むこと。
不明点があれば指示書内の前提知識を参照するか、質問すること。

### Step 3: Phase分け
5枚揃ったら、メモ5のロードマップをベースにPhase分けを決定。
Phase 1から開発開始。
