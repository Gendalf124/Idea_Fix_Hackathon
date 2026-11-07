"""Хранилище для «Материалов» и подписок на вечернюю сводку.

Материалы: имя файла (или пометка «фото»), предмет, токен-ссылка на файл в MAX и дата.
Сами файлы бот не скачивает и не хранит. Авторов и текст сообщений здесь нет.
Подписки: числовой id человека, id его личного диалога с ботом, время сводки.
"""
import time

from app.db import _connect

MATERIAL_DAYS = 30  # материалы живут 30 дней


def init_extras():
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ukey TEXT NOT NULL,          -- уникальность: сообщение + файл
                subject TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,          -- file | photo
                token TEXT,
                created_ts INTEGER NOT NULL,
                UNIQUE (chat_id, ukey)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS ix_materials_chat ON materials (chat_id, subject)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS subs (
                user_id INTEGER PRIMARY KEY,
                dm_chat_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                hour INTEGER NOT NULL DEFAULT 19,       -- вечерняя сводка, час по Учалам
                morning INTEGER NOT NULL DEFAULT 1,     -- утреннее напоминание в 07:30
                last_evening TEXT,
                last_morning TEXT
            )
            """
        )


# ---------- материалы ----------

def add_material(chat_id: int, ukey: str, subject: str, title: str, kind: str, token, created_ts: int) -> bool:
    """Добавляет материал. Повтор того же файла игнорируется. Возвращает True, если добавили."""
    with _connect() as db:
        return db.execute(
            "INSERT OR IGNORE INTO materials (chat_id, ukey, subject, title, kind, token, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, ukey, subject, title[:120], kind, token, created_ts),
        ).rowcount > 0


def get_materials(chat_id: int):
    """Материалы чата: (id, subject, title, kind, created_ts). Сначала по предметам, внутри — новые сверху."""
    with _connect() as db:
        return db.execute(
            "SELECT id, subject, title, kind, created_ts FROM materials WHERE chat_id = ? "
            "ORDER BY subject = 'Без предмета', subject, created_ts DESC",
            (chat_id,),
        ).fetchall()


def get_material(material_id: int):
    """(chat_id, subject, title, kind, token, created_ts) или None."""
    with _connect() as db:
        return db.execute(
            "SELECT chat_id, subject, title, kind, token, created_ts FROM materials WHERE id = ?",
            (material_id,),
        ).fetchone()


def delete_old_materials(now_ms=None, days: int = MATERIAL_DAYS) -> int:
    now_ms = now_ms or int(time.time() * 1000)
    with _connect() as db:
        return db.execute(
            "DELETE FROM materials WHERE created_ts < ?", (now_ms - days * 86400 * 1000,)
        ).rowcount


def count_materials_since(chat_id: int, since_ms: int) -> int:
    with _connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM materials WHERE chat_id = ? AND created_ts >= ?", (chat_id, since_ms)
        ).fetchone()[0]


# ---------- подписки на сводку ----------

def get_sub(user_id):
    """(user_id, dm_chat_id, enabled, hour, morning, last_evening, last_morning) или None."""
    with _connect() as db:
        return db.execute(
            "SELECT user_id, dm_chat_id, enabled, hour, morning, last_evening, last_morning "
            "FROM subs WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def set_sub(user_id, dm_chat_id: int, enabled=None, hour=None, morning=None):
    """Создаёт подписку или меняет только переданные поля."""
    with _connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO subs (user_id, dm_chat_id) VALUES (?, ?)", (user_id, dm_chat_id)
        )
        db.execute("UPDATE subs SET dm_chat_id = ? WHERE user_id = ?", (dm_chat_id, user_id))
        for column, value in (("enabled", enabled), ("hour", hour), ("morning", morning)):
            if value is not None:
                db.execute(f"UPDATE subs SET {column} = ? WHERE user_id = ?", (int(value), user_id))


def all_enabled_subs():
    with _connect() as db:
        return db.execute(
            "SELECT user_id, dm_chat_id, enabled, hour, morning, last_evening, last_morning "
            "FROM subs WHERE enabled = 1"
        ).fetchall()


def mark_sent(user_id, mode: str, day: str):
    column = "last_evening" if mode == "evening" else "last_morning"
    with _connect() as db:
        db.execute(f"UPDATE subs SET {column} = ? WHERE user_id = ?", (day, user_id))


def delete_sub(user_id):
    with _connect() as db:
        db.execute("DELETE FROM subs WHERE user_id = ?", (user_id,))