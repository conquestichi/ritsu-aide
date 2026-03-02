# 律 長期記憶モジュール デプロイ手順

## 概要

```
変更前:                          変更後:
turns (直近32件で消滅)           turns (直近32件) ← 変更なし
memory.json (静的)               memory.json (静的) ← 変更なし
                                 + summaries (自動要約、永続)
                                 + knowledge (学習した知識、永続)
```

## VPSでの作業

### 1. ファイル配置

```bash
cd /opt/agents/ritsu

# バックアップ
cp app.py app.py.bak_$(date +%Y%m%d_%H%M%S)

# GitHubから取得（推奨）
curl -sL https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/server/ritsu_memory.py -o ritsu_memory.py
curl -sL https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/server/app.py -o app.py

# 確認
ls -la app.py ritsu_memory.py
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

### 2. 依存パッケージ確認

追加の依存パッケージはなし（sqlite3, json, re は標準ライブラリ）。
gpt-4o-mini は要約・知識抽出に使用（既存のOpenAI APIキーで動作）。

### 3. 環境変数（オプション）

`.env` または systemd drop-in に追加可能（全てデフォルト値あり、設定しなくても動く）:

```bash
# /etc/systemd/system/ritsu.service.d/memory.conf
[Service]
Environment="RITSU_SUMMARIZE_EVERY=8"
Environment="RITSU_MAX_KNOWLEDGE=200"
Environment="RITSU_SUMMARY_MODEL=gpt-4o-mini"
```

### 4. 再起動

```bash
sudo systemctl daemon-reload
sudo systemctl restart ritsu.service

# 確認
curl -fsS http://127.0.0.1:8181/ready && echo " OK"
journalctl -u ritsu.service --no-pager -n 20 | grep BOOT
```

期待される出力:
```
[BOOT] ritsu_memory loaded
[BOOT] memory_router included
[BOOT] worker_actions router included
```

### 5. 動作テスト

```bash
# トークン読み込み
set -a; source /opt/agents/ritsu/.env; set +a
TOK="$RITSU_BEARER_TOKEN"

# 通常会話（従来通り動くことを確認）
curl -sS -X POST http://127.0.0.1:8181/assistant/text \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOK" \
  -d '{"conversation_id":"test","text":"ping"}' | python3 -m json.tool

# 記憶コマンドテスト
curl -sS -X POST http://127.0.0.1:8181/assistant/text \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOK" \
  -d '{"conversation_id":"test","text":"覚えて: 好きな色は青"}' | python3 -m json.tool

# 記憶確認
curl -sS http://127.0.0.1:8181/memory/knowledge \
  -H "Authorization: Bearer $TOK" | python3 -m json.tool

# 記憶ステータス
curl -sS http://127.0.0.1:8181/memory/status \
  -H "Authorization: Bearer $TOK" | python3 -m json.tool

# 記憶一覧（会話経由）
curl -sS -X POST http://127.0.0.1:8181/assistant/text \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOK" \
  -d '{"conversation_id":"test","text":"記憶一覧"}' | python3 -m json.tool
```

### 6. ロールバック（問題発生時）

```bash
# 旧app.pyに戻す
cd /opt/agents/ritsu
cp app.py.bak_YYYYMMDD_HHMMSS app.py
sudo systemctl restart ritsu.service

# ritsu_memory.pyが読み込めなくても、app.pyは
# MEMORY_ENABLED=False で従来通り動作する
```

## 記憶の仕組み

### 自動要約（8往復ごと）
```
会話1〜8  → 要約1生成
会話9〜16 → 要約2生成
...
→ 次の会話では system prompt に「過去の要約」として注入
→ 32ターン制限を超えた文脈が保持される
```

### 知識抽出（要約と同時）
```
会話の中で検出: "毎週金曜にジムに行く"
→ knowledge テーブルに保存:
   category=preference, content="毎週金曜にジムに行く", source=auto

次の会話で system prompt に注入:
  【ユーザーについて知っていること】
  - [好み・設定] 毎週金曜にジムに行く
```

### 明示記憶
```
ユーザー: "覚えて: 来週の月曜は休み"
律: "了解、覚えた。（記憶ID: 42）"

→ knowledge テーブルに保存:
   category=memo, content="来週の月曜は休み", source=user, confidence=1.0
```
