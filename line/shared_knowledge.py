"""shared_knowledge.py — 律/こがね共有知識DB.

/srv/ritsu-shared/shared_knowledge.sqlite に知識を読み書きする。
turns/summariesは各キャラの個別DBに持つ。知識だけ共有。

使い方:
  from shared_knowledge import sk_init, sk_save, sk_get, sk_deactivate
"""
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger("shared_knowledge")

SHARED_DB_PATH = Path("/srv/ritsu-shared/shared_knowledge.sqlite")
MAX_KNOWLEDGE = 200

_sk_lock = threading.Lock()


def _sk_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SHARED_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sk_init():
    """共有知識テーブル + 親密度テーブルを初期化。"""
    SHARED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sk_connect()
    conn.executescript("""
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
        CREATE TABLE IF NOT EXISTS intimacy (
            persona TEXT PRIMARY KEY,
            score INTEGER DEFAULT 25,
            phase TEXT DEFAULT 'secretary',
            consecutive_days INTEGER DEFAULT 0,
            days_in_target_phase INTEGER DEFAULT 0,
            last_interaction TEXT,
            last_push_reply INTEGER DEFAULT 0,
            today_reply_count INTEGER DEFAULT 0,
            today_delta_sum INTEGER DEFAULT 0,
            today_date TEXT,
            consecutive_silent_days INTEGER DEFAULT 0,
            last_push_ts REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
    """)
    # 初期データ（存在しなければ）
    conn.execute("INSERT OR IGNORE INTO intimacy (persona, score, phase) VALUES ('ritsu', 25, 'secretary')")
    conn.execute("INSERT OR IGNORE INTO intimacy (persona, score, phase) VALUES ('kogane', 15, 'secretary')")
    # 既存テーブルにlast_push_tsカラムがなければ追加（マイグレーション）
    try:
        conn.execute("ALTER TABLE intimacy ADD COLUMN last_push_ts REAL DEFAULT 0")
    except Exception:
        pass  # already exists
    # push履歴テーブル
    conn.execute("""
        CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            ts REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Shared knowledge DB initialized: %s", SHARED_DB_PATH)


def sk_save(content: str, category: str = "fact", source: str = "auto",
            source_persona: str = "unknown", confidence: float = 0.8):
    """共有知識に保存。重複は無視、上限超過時は最古のauto知識を削除。"""
    ts = int(time.time())
    with _sk_lock:
        conn = _sk_connect()
        existing = conn.execute(
            "SELECT id FROM knowledge WHERE content=? AND is_active=1",
            (content,)).fetchone()
        if existing:
            conn.close()
            return
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE is_active=1").fetchone()[0]
        if count >= MAX_KNOWLEDGE:
            conn.execute(
                "DELETE FROM knowledge WHERE id = "
                "(SELECT id FROM knowledge WHERE is_active=1 AND source != 'explicit' "
                "ORDER BY updated_at ASC LIMIT 1)")
        conn.execute(
            "INSERT INTO knowledge "
            "(category, content, source, source_persona, confidence, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (category, content, source, source_persona, confidence, ts, ts))
        conn.commit()
        conn.close()
    logger.info("[%s] 知識保存: [%s] %s", source_persona, category, content[:50])


def sk_get(limit: int = 50) -> list[dict]:
    """共有知識を取得。"""
    with _sk_lock:
        conn = _sk_connect()
        rows = conn.execute(
            "SELECT category, content, confidence, source_persona FROM knowledge "
            "WHERE is_active=1 ORDER BY updated_at DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
    return [{"category": r[0], "content": r[1], "confidence": r[2],
             "source_persona": r[3]} for r in rows]


def sk_deactivate(content_match: str) -> int:
    """知識を無効化（完全一致）。"""
    with _sk_lock:
        conn = _sk_connect()
        cur = conn.execute(
            "UPDATE knowledge SET is_active=0 WHERE content=? AND is_active=1",
            (content_match,))
        conn.commit()
        count = cur.rowcount
        conn.close()
    return count


# ── 親密度 CRUD ──

PHASE_THRESHOLDS = {
    'secretary':    (0, 25),
    'friend':       (26, 50),
    'close_friend': (51, 75),
    'lover':        (76, 100),
}
PHASE_ORDER = ['secretary', 'friend', 'close_friend', 'lover']
DAILY_DELTA_CAP_PLUS = 15
DAILY_DELTA_CAP_MINUS = -10


def intimacy_get(persona: str) -> dict | None:
    """親密度レコードを取得。"""
    with _sk_lock:
        conn = _sk_connect()
        row = conn.execute(
            "SELECT persona, score, phase, consecutive_days, days_in_target_phase, "
            "last_interaction, last_push_reply, today_reply_count, today_delta_sum, "
            "today_date, consecutive_silent_days, last_push_ts FROM intimacy WHERE persona=?",
            (persona,)).fetchone()
        conn.close()
    if not row:
        return None
    keys = ["persona", "score", "phase", "consecutive_days", "days_in_target_phase",
            "last_interaction", "last_push_reply", "today_reply_count", "today_delta_sum",
            "today_date", "consecutive_silent_days", "last_push_ts"]
    return dict(zip(keys, row))


def intimacy_update(persona: str, delta: int, reason: str = "") -> dict | None:
    """親密度スコアを加減算。日次上限チェック + フェーズ遷移判定。"""
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")

    with _sk_lock:
        conn = _sk_connect()
        row = conn.execute(
            "SELECT score, phase, today_delta_sum, today_date, days_in_target_phase, "
            "today_reply_count, consecutive_days, last_interaction, consecutive_silent_days "
            "FROM intimacy WHERE persona=?",
            (persona,)).fetchone()
        if not row:
            conn.close()
            return None

        score, phase, today_sum, row_date, days_in_phase, reply_count, consec_days, last_int, silent_days = row

        # 日次リセット
        if row_date != today:
            today_sum = 0
            reply_count = 0

        # 上限チェック
        if delta > 0:
            remaining = DAILY_DELTA_CAP_PLUS - today_sum
            delta = max(0, min(delta, remaining))
        elif delta < 0:
            remaining = DAILY_DELTA_CAP_MINUS - today_sum
            delta = min(0, max(delta, remaining))

        if delta == 0:
            conn.close()
            return intimacy_get(persona)

        new_score = max(0, min(100, score + delta))
        new_sum = today_sum + delta
        new_reply_count = reply_count + (1 if delta > 0 else 0)

        # フェーズ遷移判定
        new_phase = phase
        target_phase = _score_to_phase(new_score)
        if target_phase != phase:
            new_days = days_in_phase + 1
            phase_idx = PHASE_ORDER.index
            if phase_idx(target_phase) > phase_idx(phase) and new_days >= 3:
                new_phase = target_phase
                new_days = 0
            elif phase_idx(target_phase) < phase_idx(phase) and new_days >= 5:
                new_phase = target_phase
                new_days = 0
        else:
            new_days = 0  # 同じフェーズ範囲内 → カウントリセット

        now_str = _dt.now().isoformat()
        conn.execute(
            "UPDATE intimacy SET score=?, phase=?, today_delta_sum=?, today_date=?, "
            "today_reply_count=?, days_in_target_phase=?, last_interaction=?, "
            "consecutive_silent_days=0, updated_at=datetime('now','localtime') "
            "WHERE persona=?",
            (new_score, new_phase, new_sum, today, new_reply_count, new_days, now_str, persona))
        conn.commit()
        conn.close()

    logger.info("[intimacy] %s: %d→%d (%+d) phase=%s reason=%s",
                persona, score, new_score, delta, new_phase, reason)
    return intimacy_get(persona)


def intimacy_daily_decay():
    """日次減衰処理（23:30にcronまたはtimerで実行）。"""
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")
    results = {}

    for persona in ['ritsu', 'kogane']:
        data = intimacy_get(persona)
        if not data:
            continue

        rival = 'kogane' if persona == 'ritsu' else 'ritsu'
        rival_data = intimacy_get(rival)

        replied_today = data.get('today_reply_count', 0) > 0 and data.get('today_date') == today
        decay = 0

        if not replied_today:
            silent = data.get('consecutive_silent_days', 0) + 1
            if silent >= 7:
                decay = -5
            elif silent >= 3:
                decay = -3
            else:
                decay = -1
            # silent days更新
            with _sk_lock:
                conn = _sk_connect()
                conn.execute("UPDATE intimacy SET consecutive_silent_days=? WHERE persona=?",
                             (silent, persona))
                conn.commit()
                conn.close()
        else:
            # 連続会話日ボーナス
            consec = data.get('consecutive_days', 0) + 1
            with _sk_lock:
                conn = _sk_connect()
                conn.execute("UPDATE intimacy SET consecutive_days=?, consecutive_silent_days=0 WHERE persona=?",
                             (consec, persona))
                conn.commit()
                conn.close()
            if consec > 1:
                decay += 1  # 連続日ボーナス

        # 嫉妬減衰: 相手とだけ会話した日
        if rival_data and not replied_today:
            rival_replied = rival_data.get('today_reply_count', 0) > 0 and rival_data.get('today_date') == today
            if rival_replied:
                decay -= 2

        # 救済: スコア差30以上で低い方にボーナス
        if rival_data and rival_data['score'] - data['score'] >= 30:
            decay += 3

        if decay != 0:
            intimacy_update(persona, decay, "daily_decay")

        results[persona] = decay

    logger.info("[intimacy] daily decay: %s", results)
    return results


def _score_to_phase(score: int) -> str:
    """スコアから対応フェーズを返す。"""
    for phase, (lo, hi) in PHASE_THRESHOLDS.items():
        if lo <= score <= hi:
            return phase
    return 'lover' if score > 100 else 'secretary'


# ── Push調整 ──

def intimacy_record_push(persona: str, message: str = ""):
    """pushを送った時刻とメッセージを記録。"""
    import time as _time
    with _sk_lock:
        conn = _sk_connect()
        conn.execute("UPDATE intimacy SET last_push_ts=? WHERE persona=?",
                     (_time.time(), persona))
        # push_historyに保存
        conn.execute(
            "INSERT INTO push_history (persona, message, ts) VALUES (?,?,?)",
            (persona, message, _time.time()))
        # 古い履歴を削除（各ペルソナ最新10件のみ保持）
        conn.execute(
            "DELETE FROM push_history WHERE id NOT IN "
            "(SELECT id FROM push_history WHERE persona=? ORDER BY ts DESC LIMIT 10) "
            "AND persona=?", (persona, persona))
        conn.commit()
        conn.close()


def intimacy_rival_pushed_recently(persona: str, within_sec: int = 3600) -> bool:
    """相手(rival)が直近within_sec秒以内にpushしたか。"""
    import time as _time
    rival = 'kogane' if persona == 'ritsu' else 'ritsu'
    with _sk_lock:
        conn = _sk_connect()
        try:
            row = conn.execute("SELECT last_push_ts FROM intimacy WHERE persona=?",
                               (rival,)).fetchone()
        except Exception:
            row = None
        conn.close()
    if not row or not row[0]:
        return False
    return (_time.time() - row[0]) < within_sec


def intimacy_get_rival_recent_pushes(persona: str, limit: int = 3) -> list[str]:
    """相手の直近pushメッセージを取得（内容被り防止用）。"""
    rival = 'kogane' if persona == 'ritsu' else 'ritsu'
    with _sk_lock:
        conn = _sk_connect()
        try:
            rows = conn.execute(
                "SELECT message FROM push_history WHERE persona=? ORDER BY ts DESC LIMIT ?",
                (rival, limit)).fetchall()
        except Exception:
            rows = []
        conn.close()
    return [r[0] for r in rows if r[0]]
