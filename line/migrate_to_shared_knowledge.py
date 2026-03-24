#!/usr/bin/env python3
"""migrate_to_shared_knowledge.py — こがね既存knowledge → 共有DB移行.

手順:
  1. 共有DB作成 (/srv/ritsu-shared/shared_knowledge.sqlite)
  2. kogane-chat.db の knowledge を共有DBにコピー
  3. 件数確認

使い方:
  python3 /opt/ritsu-line/migrate_to_shared_knowledge.py

注意:
  - kogane-chat.db は壊さない（読み取りのみ）
  - 共有DBへの書き込みのみ
  - 重複チェックあり（何度実行しても安全）
"""
import sqlite3
import time
from pathlib import Path

KOGANE_CHAT_DB = Path("/opt/inga-kogane/data/kogane-chat.db")
SHARED_DB = Path("/srv/ritsu-shared/shared_knowledge.sqlite")


def main():
    # 1. 共有DB作成
    SHARED_DB.parent.mkdir(parents=True, exist_ok=True)
    shared = sqlite3.connect(str(SHARED_DB))
    shared.execute("PRAGMA journal_mode=WAL")
    shared.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'auto',
            source_persona TEXT NOT NULL DEFAULT 'unknown',
            confidence REAL NOT NULL DEFAULT 0.8,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
    """)
    print(f"[OK] 共有DB作成: {SHARED_DB}")

    # 2. kogane knowledge → 共有DB
    if not KOGANE_CHAT_DB.exists():
        print(f"[SKIP] kogane-chat.db が見つからない: {KOGANE_CHAT_DB}")
        shared.close()
        return

    kogane = sqlite3.connect(str(KOGANE_CHAT_DB))
    rows = kogane.execute(
        "SELECT category, content, source, confidence, is_active, created_at, updated_at "
        "FROM knowledge WHERE is_active=1"
    ).fetchall()
    kogane.close()
    print(f"[INFO] こがねknowledge: {len(rows)}件")

    copied = 0
    for r in rows:
        category, content, source, confidence, is_active, created_at, updated_at = r
        # 重複チェック
        existing = shared.execute(
            "SELECT id FROM knowledge WHERE content=? AND is_active=1",
            (content,)).fetchone()
        if existing:
            continue
        shared.execute(
            "INSERT INTO knowledge "
            "(category, content, source, source_persona, confidence, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (category, content, source, "kogane", confidence, is_active, created_at, updated_at))
        copied += 1

    shared.commit()

    # 3. 確認
    total = shared.execute("SELECT COUNT(*) FROM knowledge WHERE is_active=1").fetchone()[0]
    shared.close()
    print(f"[OK] コピー完了: {copied}件追加, 共有DB合計: {total}件")
    print()
    print("次のステップ:")
    print("  1. こがねのline_chat.pyにshared_knowledge.pyをコピー:")
    print("     cp /opt/ritsu-line/shared_knowledge.py /opt/inga-kogane/src/kogane/")
    print("  2. こがねのline_chat.pyにパッチ適用（knowledge関数を共有DBに切替）")
    print("  3. systemctl restart kogane-line")


if __name__ == "__main__":
    main()
