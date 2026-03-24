# 律LINE bot + 共有知識DB — デプロイ手順

## 前提
- VPS: 160.251.167.44 (ConoHa)
- こがねLINE bot が /opt/inga-kogane/ で稼働中
- nginx でリバースプロキシ設定済み

---

## Step 0: LINE公式アカウント開設（律用）

1. https://developers.line.biz/ にログイン
2. 既存プロバイダー「因果律」を選択
3. 「新規チャネル作成」→ Messaging API
4. チャネル名: 律 (ritsu)
5. 以下を控える:
   - Channel Secret
   - Channel Access Token（長期トークン発行）
   - Your user ID（プロバイダー設定で確認）

---

## Step 1: VPSにファイル配置

```bash
ssh -i ~/.ssh/id_ed25519_ritsu root@160.251.167.44

# リポ更新
cd /opt/ritsu-aide  # なければ git clone
git pull

# 律LINE botディレクトリ作成
mkdir -p /opt/ritsu-line/data

# ファイルコピー
cp /opt/ritsu-aide/line/ritsu_line.py /opt/ritsu-line/
cp /opt/ritsu-aide/line/shared_knowledge.py /opt/ritsu-line/
cp /opt/ritsu-aide/line/migrate_to_shared_knowledge.py /opt/ritsu-line/
cp /opt/ritsu-aide/line/env.example /opt/ritsu-line/.env

# 共有知識モジュールをこがね側にもコピー
cp /opt/ritsu-aide/line/shared_knowledge.py /opt/inga-kogane/src/kogane/

# .env編集（APIキーとLINEトークン設定）
nano /opt/ritsu-line/.env

# 権限
chown -R inga:inga /opt/ritsu-line
mkdir -p /srv/ritsu-shared
chown inga:inga /srv/ritsu-shared
```

---

## Step 2: 共有知識DBマイグレーション

```bash
# こがねの既存knowledge → 共有DBにコピー
cd /opt/ritsu-line
/opt/inga-kogane/.venv/bin/python migrate_to_shared_knowledge.py
```

出力例:
```
[OK] 共有DB作成: /srv/ritsu-shared/shared_knowledge.sqlite
[INFO] こがねknowledge: 12件
[OK] コピー完了: 12件追加, 共有DB合計: 12件
```

---

## Step 3: こがね line_chat.py パッチ

こがねのknowledge関数を共有DBに切替える。**最小変更**。

`/opt/inga-kogane/src/kogane/line_chat.py` を編集:

### 3a. import追加（ファイル先頭のimport群の後に）

```python
from kogane.shared_knowledge import sk_init, sk_save, sk_get
```

### 3b. db_init() に共有DB初期化を追加

```python
def db_init():
    # ... 既存のコード（turns/summaries/knowledge テーブル作成）...
    conn.close()
    logger.info("Chat DB initialized: %s", CHAT_DB_PATH)
    # 共有知識DB初期化
    sk_init()
```

### 3c. db_save_knowledge() を変更

```python
def db_save_knowledge(content: str, category: str = "fact",
                      source: str = "auto", confidence: float = 0.8):
    # 共有DBに保存（source_persona="kogane"）
    sk_save(content, category=category, source=source,
            source_persona="kogane", confidence=confidence)
```

### 3d. db_get_knowledge() を変更

```python
def db_get_knowledge(limit: int = 50) -> list[dict]:
    # 共有DBから読む
    return sk_get(limit=limit)
```

### 3e. 確認

```bash
# こがねLINE再起動
systemctl restart kogane-line

# ログ確認
journalctl -u kogane-line -f
# → "Shared knowledge DB initialized" が出ればOK
```

**こがねの既存 kogane-chat.db の knowledge テーブルは残るが使わなくなる。**
データはStep 2で共有DBにコピー済み。安全。

---

## Step 4: 律LINE bot 起動

```bash
# systemd登録
cp /opt/ritsu-aide/line/ritsu-line.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ritsu-line
systemctl start ritsu-line

# ログ確認
journalctl -u ritsu-line -f
# → "律LINE会話サーバー起動 port=9878" が出ればOK
```

---

## Step 5: nginx設定

`/etc/nginx/sites-available/inga-quants.com` に追加:

```nginx
# 律 LINE Webhook
location /webhook/ritsu {
    proxy_pass http://127.0.0.1:9878;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

```bash
nginx -t && systemctl reload nginx
```

---

## Step 6: LINE Developers Webhook設定

1. LINE Developers → プロバイダー「因果律」→ チャネル「律」
2. Messaging API設定:
   - Webhook URL: `https://ingaquants.jp/webhook/ritsu`
   - Webhook利用: ON
   - 応答メッセージ: OFF（自前で返すため）
   - あいさつメッセージ: OFF
3. 「検証」ボタン → 200 OK確認

---

## Step 7: 動作確認

1. LINE公式アカウント「律」を友だち追加
2. 「こんにちは」と送信
3. 律が返信するか確認
4. VPSログ: `journalctl -u ritsu-line -f`

---

## トラブルシュート

### Webhook 404
- nginx設定にlocation /webhook/ritsu が入っているか確認
- `curl http://127.0.0.1:9878/health` → "ok" が返るか

### 署名検証失敗
- .envのRITSU_LINE_CHANNEL_SECRETがLINE DevelopersのChannel Secretと一致しているか

### Claude API エラー
- .envのANTHROPIC_API_KEYが正しいか
- `curl https://api.anthropic.com/v1/messages` にアクセスできるか

### 共有知識DBエラー
- `/srv/ritsu-shared/` のオーナーがingaか確認
- `ls -la /srv/ritsu-shared/shared_knowledge.sqlite`

---

## ファイル構成

```
/opt/ritsu-line/
├── ritsu_line.py              律LINE bot (メイン)
├── shared_knowledge.py        共有知識モジュール
├── migrate_to_shared_knowledge.py  マイグレーションスクリプト
├── .env                       環境変数
└── data/
    ├── ritsu-chat.db           律専用会話DB (自動生成)
    └── latest_ritsu_messages.json  HPキャッシュ (自動生成)

/srv/ritsu-shared/
└── shared_knowledge.sqlite    共有知識DB (律+こがね)

/opt/inga-kogane/src/kogane/
├── line_chat.py               こがねLINE bot (パッチ済み)
└── shared_knowledge.py        共有知識モジュール (コピー)

/etc/systemd/system/
├── ritsu-line.service          律LINE bot サービス
└── kogane-line.service         こがねLINE bot サービス (既存)
```
