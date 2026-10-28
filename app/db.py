import os
import sqlite3
import time

DB_PATH = os.getenv("DB_PATH", "svodka.db")


def _connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    """Создаёт таблицы, если их ещё нет."""
    with _connect() as db:
        # Сообщения. Всегда с chat_id — это и разделяет разные классы.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                mid TEXT NOT NULL,
                user_name TEXT,
                ts INTEGER NOT NULL,          -- время сообщения, миллисекунды
                text TEXT NOT NULL,
                UNIQUE (chat_id, mid)
            )
            """
        )
        # Чаты (классы) и их названия.
        db.execute(
            "CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, title TEXT)"
        )
        # Когда мы в последний раз подтягивали список участников чата.
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_sync (chat_id INTEGER PRIMARY KEY, synced_ts INTEGER NOT NULL)"
        )
        # Кто в каком чате замечен. По этой таблице бот решает, чью сводку кому показывать.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )


def save_message(chat_id: int, mid: str, user_name: str, ts: int, text: str):
    with _connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO messages (chat_id, mid, user_name, ts, text) VALUES (?, ?, ?, ?, ?)",
            (chat_id, mid, user_name, ts, text),
        )


def get_messages(chat_id: int, since_ts: int, limit: int = 200):
    """Последние сообщения ОДНОГО чата начиная с since_ts (по порядку времени)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT ts, user_name, text FROM messages "
            "WHERE chat_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (chat_id, since_ts, limit),
        ).fetchall()
    return list(reversed(rows))


def delete_old(before_ts: int):
    """Удаляет сообщения старше указанного времени (срок хранения)."""
    with _connect() as db:
        db.execute("DELETE FROM messages WHERE ts < ?", (before_ts,))


# ---------- чаты и участники ----------

def touch_member(chat_id: int, user_id, user_name: str = ""):
    """Запоминает, что пользователь есть в этом чате."""
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        if user_id is not None:
            db.execute(
                "INSERT OR IGNORE INTO members (chat_id, user_id, user_name) VALUES (?, ?, ?)",
                (chat_id, user_id, user_name),
            )


def is_member(chat_id: int, user_id) -> bool:
    with _connect() as db:
        row = db.execute(
            "SELECT 1 FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
    return row is not None


def set_title(chat_id: int, title: str):
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        db.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (title[:60], chat_id))


def chat_label(chat_id: int, title) -> str:
    return title or f"Чат …{str(abs(chat_id))[-4:]}"


def user_chats(user_id):
    """Чаты, где замечен пользователь: список (chat_id, название)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT c.chat_id, c.title FROM members m JOIN chats c ON c.chat_id = m.chat_id "
            "WHERE m.user_id = ? ORDER BY c.chat_id",
            (user_id,),
        ).fetchall()
    return [(chat_id, chat_label(chat_id, title)) for chat_id, title in rows]


# ---------- оригиналы сообщений и синхронизация ----------

def get_message(chat_id: int, mid: str):
    """Оригинал сообщения: (имя автора, время в мс, текст) или None, если его уже нет."""
    with _connect() as db:
        return db.execute(
            "SELECT user_name, ts, text FROM messages WHERE chat_id = ? AND mid = ?",
            (chat_id, mid),
        ).fetchone()


def mark_synced(chat_id: int, now_ms: int = None):
    now_ms = now_ms or int(time.time() * 1000)
    with _connect() as db:
        db.execute(
            "INSERT INTO chat_sync (chat_id, synced_ts) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET synced_ts = excluded.synced_ts",
            (chat_id, now_ms),
        )


def last_synced(chat_id: int):
    """Время последней успешной синхронизации участников (мс) или None."""
    with _connect() as db:
        row = db.execute("SELECT synced_ts FROM chat_sync WHERE chat_id = ?", (chat_id,)).fetchone()
    return row[0] if row else None