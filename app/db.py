import os
import sqlite3
import time

DB_PATH = os.getenv("DB_PATH", "svodka.db")


def _connect():
    return sqlite3.connect(DB_PATH)


def _now_ms() -> int:
    return int(time.time() * 1000)


def init_db():
    """Создаёт таблицы, если их ещё нет (и дополняет старые базы новыми колонками)."""
    with _connect() as db:
        # Исходные сообщения. Хранятся ТОЛЬКО те, из которых получилась запись (нужны для кнопки 🔎).
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                mid TEXT NOT NULL,
                user_name TEXT,
                ts INTEGER NOT NULL,
                text TEXT NOT NULL,
                UNIQUE (chat_id, mid)
            )
            """
        )
        db.execute("CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, title TEXT)")
        # Участники: только id (имена не храним).
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
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_sync (chat_id INTEGER PRIMARY KEY, synced_ts INTEGER NOT NULL)"
        )
        # Учителя: те, кто ввёл код учителя. Хранится только числовой id.
        db.execute("CREATE TABLE IF NOT EXISTS teachers (user_id INTEGER PRIMARY KEY)")
        # Колонки, добавленные позже. Если они уже есть — SQLite скажет об этом, это нормально.
        for sql in (
            "ALTER TABLE chats ADD COLUMN manual INTEGER NOT NULL DEFAULT 0",   # название задано вручную
            "ALTER TABLE members ADD COLUMN checked_ts INTEGER NOT NULL DEFAULT 0",  # когда видели в чате
            "ALTER TABLE members ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0",    # владелец чата (по данным MAX)
        ):
            try:
                db.execute(sql)
            except sqlite3.OperationalError:
                pass
    from app.extras_db import init_extras  # материалы и подписки (импорт здесь, чтобы не было круга)

    init_extras()


# ---------- исходные сообщения ----------

def save_message(chat_id: int, mid: str, user_name: str, ts: int, text: str):
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO messages (chat_id, mid, user_name, ts, text) VALUES (?, ?, ?, ?, ?)",
            (chat_id, mid, user_name, ts, text),
        )


def get_message(chat_id: int, mid: str):
    """Оригинал сообщения: (имя автора, время в мс, текст) или None, если его уже нет."""
    with _connect() as db:
        return db.execute(
            "SELECT user_name, ts, text FROM messages WHERE chat_id = ? AND mid = ?",
            (chat_id, mid),
        ).fetchone()


def delete_orphan_messages() -> int:
    """Удаляет сообщения, на которые не ссылается ни одна запись. Так оригинал живёт не дольше записи."""
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM messages WHERE NOT EXISTS ("
            "SELECT 1 FROM tasks t WHERE t.chat_id = messages.chat_id AND t.mid = messages.mid)"
        )
        return cur.rowcount


# ---------- участники (доступ к сводкам) ----------

def touch_member(chat_id: int, user_id, user_name: str = "", owner=None):
    """Запоминает, что пользователь СЕЙЧАС есть в чате. Имя не сохраняем (параметр оставлен для совместимости).

    owner: True/False — отметить, владелец ли он чата (из данных MAX); None — не менять."""
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        if user_id is not None:
            db.execute(
                "INSERT INTO members (chat_id, user_id, user_name, checked_ts) VALUES (?, ?, '', ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET checked_ts = excluded.checked_ts",
                (chat_id, user_id, _now_ms()),
            )
            if owner is not None:
                db.execute(
                    "UPDATE members SET is_owner = ? WHERE chat_id = ? AND user_id = ?",
                    (1 if owner else 0, chat_id, user_id),
                )


def is_member(chat_id: int, user_id, max_age_s=None) -> bool:
    """Есть ли человек в списке. max_age_s — насколько свежей должна быть отметка «видели в чате»."""
    sql = "SELECT 1 FROM members WHERE chat_id = ? AND user_id = ?"
    args = [chat_id, user_id]
    if max_age_s is not None:
        sql += " AND checked_ts >= ?"
        args.append(_now_ms() - int(max_age_s * 1000))
    with _connect() as db:
        return db.execute(sql, args).fetchone() is not None


def is_owner(chat_id: int, user_id) -> bool:
    """Владелец ли человек чата (по последним данным MAX)."""
    with _connect() as db:
        row = db.execute(
            "SELECT is_owner FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
    return bool(row and row[0])


def add_teacher(user_id):
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO teachers (user_id) VALUES (?)", (user_id,))


def is_teacher(user_id) -> bool:
    with _connect() as db:
        return db.execute("SELECT 1 FROM teachers WHERE user_id = ?", (user_id,)).fetchone() is not None


def remove_member(chat_id: int, user_id):
    with _connect() as db:
        db.execute("DELETE FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))


def remove_absent_members(chat_id: int, keep_ids) -> int:
    """Удаляет всех, кого нет в актуальном списке чата (вышли или исключены). Возвращает сколько."""
    keep = set(keep_ids)
    with _connect() as db:
        rows = db.execute("SELECT user_id FROM members WHERE chat_id = ?", (chat_id,)).fetchall()
        gone = [r[0] for r in rows if r[0] not in keep]
        for user_id in gone:
            db.execute("DELETE FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    return len(gone)


def delete_chat_data(chat_id: int):
    """Полностью удаляет всё про чат (бота убрали из чата)."""
    with _connect() as db:
        for table in ("messages", "members", "chat_sync", "chats"):
            db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
        for table in ("tasks", "materials"):
            try:
                db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
            except sqlite3.OperationalError:
                pass  # таблицы ещё нет


# ---------- названия чатов ----------

def set_title(chat_id: int, title: str):
    """Название, которое ввёл человек. Оно главнее автоматического."""
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        db.execute("UPDATE chats SET title = ?, manual = 1 WHERE chat_id = ?", (title[:60], chat_id))


def set_auto_title(chat_id: int, title: str):
    """Название, которое бот сам узнал из MAX. Не перезаписывает ручное."""
    if not title:
        return
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        db.execute("UPDATE chats SET title = ? WHERE chat_id = ? AND manual = 0", (title[:60], chat_id))


def get_title(chat_id: int):
    with _connect() as db:
        row = db.execute("SELECT title FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    return row[0] if row else None


def chat_label(chat_id: int, title) -> str:
    return title or f"Чат …{str(abs(chat_id))[-4:]}"


def user_chats(user_id):
    """Чаты, где человек числится участником: список (chat_id, название)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT c.chat_id, c.title FROM members m JOIN chats c ON c.chat_id = m.chat_id "
            "WHERE m.user_id = ? ORDER BY c.chat_id",
            (user_id,),
        ).fetchall()
    return [(chat_id, chat_label(chat_id, title)) for chat_id, title in rows]


# ---------- синхронизация участников ----------

def mark_synced(chat_id: int, now_ms: int = None):
    with _connect() as db:
        db.execute(
            "INSERT INTO chat_sync (chat_id, synced_ts) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET synced_ts = excluded.synced_ts",
            (chat_id, now_ms or _now_ms()),
        )


def last_synced(chat_id: int):
    with _connect() as db:
        row = db.execute("SELECT synced_ts FROM chat_sync WHERE chat_id = ?", (chat_id,)).fetchone()
    return row[0] if row else None