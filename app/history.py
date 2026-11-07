"""Чтение сообщений чата за последние 7 дней при добавлении бота (или по кнопке «📥 История»).

Бот видит историю только как администратор. Он проходит сообщения от старых к новым тем же путём,
что и живые: фильтр до ИИ, контекст из соседних сообщений, фото по правилам PHOTO_SCAN.
Чтобы не тратить много токенов, у чтения есть потолки (сообщений, обращений к ИИ, фото).
Оригиналы сообщений остаются только у тех записей, которые получились, как и обычно.
"""
import logging
import time
from datetime import datetime, timezone

from app import config, materials, photos
from app.context import ChatContext, Entry
from app.db import touch_member
from app.planner import make_plan

PAGE = 100


def _ts(message) -> int:
    ts = getattr(message, "timestamp", None)
    return int(ts) if ts else 0


def _is_bot(message) -> bool:
    return bool(getattr(getattr(message, "sender", None), "is_bot", False))


async def _get_batch(bot, chat_id: int, start_ms: int, end_ms: int, count: int):
    """Одна страница истории. Время передаём датой; если версия maxapi хочет число — пробуем числом."""
    attempts = (
        (datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc), datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)),
        (start_ms, end_ms),
    )
    last_error = None
    for from_time, to_time in attempts:
        try:
            result = await bot.get_messages(chat_id=chat_id, from_time=from_time, to_time=to_time, count=count)
            messages = getattr(result, "messages", None)
            if messages is None:  # некоторые версии возвращают сразу список
                messages = result if isinstance(result, (list, tuple)) else []
            return list(messages)
        except TypeError as exc:  # другой тип времени
            last_error = exc
    raise last_error


async def fetch_history(bot, chat_id: int, days: int = None, limit: int = None):
    """Сообщения чата за последние days дней, от старых к новым (не больше limit штук)."""
    days = days or config.HISTORY_DAYS
    limit = limit or config.HISTORY_MAX_MESSAGES
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    end_ms = now_ms
    got, seen = [], set()
    for _ in range(10):
        batch = await _get_batch(bot, chat_id, start_ms, end_ms, min(PAGE, limit - len(got)))
        fresh = []
        for m in batch:
            mid = getattr(getattr(m, "body", None), "mid", None) or f"{chat_id}:{_ts(m)}"
            if mid not in seen:
                seen.add(mid)
                fresh.append(m)
        got.extend(fresh)
        if not fresh or len(batch) < PAGE or len(got) >= limit:
            break
        oldest = min((_ts(m) for m in fresh if _ts(m)), default=0)
        if not oldest or oldest <= start_ms:
            break
        end_ms = oldest - 1
    got = [m for m in got if _ts(m) >= start_ms]
    got.sort(key=_ts)
    return got[-limit:]


async def run_backfill(bot, chat_id: int, process_plan, days: int = None):
    """Читает историю и разбирает её. process_plan(chat_id, plan, ctx) — функция разбора из handlers.

    Возвращает {"messages": прочитано сообщений, "plans": разборов у ИИ, "materials": найдено материалов}."""
    history = await fetch_history(bot, chat_id, days)
    ctx = ChatContext()
    photos_left = config.HISTORY_MAX_PHOTOS
    plans_left = config.HISTORY_MAX_PLANS
    stats = {"messages": len(history), "plans": 0, "materials": 0}
    for message in history:
        if _is_bot(message):
            continue
        body = getattr(message, "body", None)
        text = (getattr(body, "text", None) or "").strip()
        if text.startswith("/"):
            continue
        sender = getattr(message, "sender", None)
        user_id = getattr(sender, "user_id", None)
        if user_id is not None:
            touch_member(chat_id, user_id)  # автор сообщения точно был в чате
        name = " ".join(x for x in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if x)
        images = photos.image_infos(message) if config.PHOTO_SCAN != "off" else []
        files = materials.file_infos(message)
        ts = _ts(message)
        mid = getattr(body, "mid", None) or f"{chat_id}:{ts}"
        if files or images:
            stats["materials"] += materials.save_from_message(chat_id, mid, ts, text, files, images)
        if not text and not images:
            continue
        entry = Entry(mid=mid, ts=ts, author=name, text=text, images=images)
        ctx.push(chat_id, entry)
        if plans_left <= 0:
            continue
        mode = config.PHOTO_SCAN if photos_left > 0 else "off"
        plan = make_plan(ctx.window(chat_id, ts), entry, mode)
        if plan is None:
            continue
        plans_left -= 1
        photos_left -= len(plan.images)
        stats["plans"] += 1
        await process_plan(chat_id, plan, ctx)
    logging.info("Чат %s: история прочитана %s", chat_id, stats)
    return stats