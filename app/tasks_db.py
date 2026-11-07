import re
import time
from datetime import datetime, timedelta, timezone

from app.db import _connect, delete_orphan_messages

TZ = timezone(timedelta(hours=5))  # Учалы, UTC+5
KEEP_DAYS = 7  # записи без срока и «что проходили» живут 7 дней


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
                norm TEXT,                    -- «отпечаток» записи для поиска дублей
                verified INTEGER NOT NULL DEFAULT 0,       -- 1 — подтвердил учитель или владелец чата
                src_kind TEXT NOT NULL DEFAULT 'text',     -- text | voice
                conf TEXT,                                 -- уверенность расшифровки: high | medium | low | unknown
                photo_token TEXT                           -- токен фото в MAX (само фото не хранится)
            )
            """
        )
        for sql in (  # для баз, созданных раньше
            "ALTER TABLE tasks ADD COLUMN norm TEXT",
            "ALTER TABLE tasks ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN src_kind TEXT NOT NULL DEFAULT 'text'",
            "ALTER TABLE tasks ADD COLUMN conf TEXT",
            "ALTER TABLE tasks ADD COLUMN photo_token TEXT",
        ):
            try:
                db.execute(sql)
            except Exception:
                pass
        db.execute("CREATE INDEX IF NOT EXISTS ix_tasks_chat_norm ON tasks (chat_id, norm)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_tasks_chat_mid ON tasks (chat_id, mid)")


def norm_key(kind: str, subject: str, text: str) -> str:
    """Отпечаток записи: без регистра, пробелов и знаков. «Алгебра: №215!» и «алгебра №215» — одно и то же."""
    def clean(s: str) -> str:
        return re.sub(r"[^0-9a-zа-я]+", "", s.lower().replace("ё", "е"))
    return f"{kind}|{clean(subject)}|{clean(text)}"


def add_task(chat_id: int, mid: str, kind: str, subject: str, text: str, due, created_ts: int,
             src_kind: str = "text", conf=None, photo_token=None) -> str:
    """Добавляет запись. Если такая уже есть — не дублирует, а обновляет срок и источник.
    Если срок изменился, отметка «проверено» снимается: учитель подтверждал другое.

    src_kind: "text" или "voice"; conf — уверенность расшифровки голосового.
    Возвращает "added" или "updated".
    """
    norm = norm_key(kind, subject, text)
    with _connect() as db:
        row = db.execute(
            "SELECT id, due FROM tasks WHERE chat_id = ? AND norm = ?", (chat_id, norm)
        ).fetchone()
        if row:
            due_changed = 1 if (due and due != row[1]) else 0
            db.execute(
                "UPDATE tasks SET mid = ?, due = COALESCE(?, due), created_ts = ?, "
                "verified = CASE WHEN ? THEN 0 ELSE verified END, "
                "photo_token = COALESCE(?, photo_token) WHERE id = ?",
                (mid, due, created_ts, due_changed, photo_token, row[0]),
            )
            return "updated"
        db.execute(
            "INSERT INTO tasks (chat_id, mid, kind, subject, text, due, created_ts, norm, src_kind, conf, "
            "photo_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, mid, kind, subject, text, due, created_ts, norm, src_kind, conf, photo_token),
        )
        return "added"


def delete_tasks_for_message(chat_id: int, mid: str) -> int:
    """Убирает записи, полученные из одного сообщения (нужно, когда сообщение отредактировали)."""
    with _connect() as db:
        return db.execute("DELETE FROM tasks WHERE chat_id = ? AND mid = ?", (chat_id, mid)).rowcount


def delete_expired(now_ms=None) -> int:
    """Удаляет устаревшее: срок прошёл, либо записи без срока старше 7 дней. Заодно — их оригиналы."""
    now_ms = now_ms or int(time.time() * 1000)
    today = datetime.fromtimestamp(now_ms / 1000, tz=TZ).date().isoformat()
    old_ts = now_ms - KEEP_DAYS * 86400 * 1000
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM tasks WHERE (due IS NOT NULL AND due < ?) "
            "OR (due IS NULL AND created_ts < ?)",
            (today, old_ts),
        )
        removed = cur.rowcount
    delete_orphan_messages()  # оригинал не должен жить дольше записи
    return removed


def get_tasks(chat_id: int):
    """Все актуальные записи ОДНОГО чата."""
    with _connect() as db:
        return db.execute(
            "SELECT id, kind, subject, text, due, created_ts, verified, src_kind, conf FROM tasks "
            "WHERE chat_id = ? ORDER BY due IS NULL, due, created_ts",
            (chat_id,),
        ).fetchall()


def get_task(task_id: int):
    """Одна запись целиком: (chat_id, mid, kind, subject, text, due, created_ts, verified, src_kind, conf,
    photo_token) или None."""
    with _connect() as db:
        return db.execute(
            "SELECT chat_id, mid, kind, subject, text, due, created_ts, verified, src_kind, conf, photo_token "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()


def verify_task(task_id: int) -> bool:
    """Учитель подтвердил одну запись."""
    with _connect() as db:
        return db.execute("UPDATE tasks SET verified = 1 WHERE id = ?", (task_id,)).rowcount > 0


def verify_all(chat_id: int) -> int:
    """Подтверждает все ещё не проверенные записи чата. Возвращает сколько подтвердили."""
    with _connect() as db:
        return db.execute(
            "UPDATE tasks SET verified = 1 WHERE chat_id = ? AND verified = 0", (chat_id,)
        ).rowcount


def delete_task(task_id: int) -> bool:
    """Удаляет ошибочную запись (и её оригинал, если на него больше никто не ссылается)."""
    with _connect() as db:
        removed = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0
    delete_orphan_messages()
    return removed


def delete_vague_for(chat_id: int, mids) -> int:
    """Убирает непроверенные «записи без предмета», полученные из этих сообщений.

    Нужна, когда сообщение «ДЗ на завтра» сначала дало неполную запись, а следующее сообщение
    или фото уточнили задание: старый черновик заменяется полной записью."""
    removed = 0
    with _connect() as db:
        for mid in mids:
            removed += db.execute(
                "DELETE FROM tasks WHERE chat_id = ? AND mid = ? AND verified = 0 AND subject = 'Без предмета'",
                (chat_id, mid),
            ).rowcount
    return removed