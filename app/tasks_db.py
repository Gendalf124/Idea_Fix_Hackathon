import time
from datetime import datetime, timedelta, timezone

from app.db import _connect

TZ = timezone(timedelta(hours=5))  # Учалы, UTC+5
KEEP_DAYS = 7  


def init_tasks():
    """Создаёт таблицу записей (заданий и тем уроков)."""
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                mid TEXT NOT NULL,            -- сообщение-источник
                kind TEXT NOT NULL,           -- homework | lesson
                subject TEXT NOT NULL,
                text TEXT NOT NULL,
                due TEXT,                     -- срок, ГГГГ-ММ-ДД, может быть пустым
                created_ts INTEGER NOT NULL,  -- время сообщения, миллисекунды
                UNIQUE (chat_id, mid, kind, text)
            )
            """
        )


def add_task(chat_id: int, mid: str, kind: str, subject: str, text: str, due, created_ts: int):
    with _connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO tasks (chat_id, mid, kind, subject, text, due, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, mid, kind, subject, text, due, created_ts),
        )


def delete_expired(now_ms=None) -> int:
    """Удаляет устаревшее: срок прошёл, либо записи без срока старше 7 дней."""
    now_ms = now_ms or int(time.time() * 1000)
    today = datetime.fromtimestamp(now_ms / 1000, tz=TZ).date().isoformat()
    old_ts = now_ms - KEEP_DAYS * 86400 * 1000
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM tasks WHERE (due IS NOT NULL AND due < ?) "
            "OR (due IS NULL AND created_ts < ?)",
            (today, old_ts),
        )
        return cur.rowcount


def get_tasks(chat_id: int):
    """Все актуальные записи ОДНОГО чата."""
    with _connect() as db:
        return db.execute(
            "SELECT id, kind, subject, text, due, created_ts FROM tasks "
            "WHERE chat_id = ? ORDER BY due IS NULL, due, created_ts",
            (chat_id,),
        ).fetchall()