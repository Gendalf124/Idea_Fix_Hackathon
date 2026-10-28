import logging
import time

from app.db import get_title, last_synced, mark_synced, set_auto_title, touch_member

SYNC_EVERY_MS = 6 * 3600 * 1000  # обновляем список участников раз в 6 часов
RETRY_EVERY_S = 1800             # после неудачи (например, нет прав) повторяем не чаще раза в 30 минут
_last_try = {}                   # chat_id -> время последней попытки


async def refresh_title(bot, chat_id: int):
    """Узнаёт название чата из MAX и запоминает его (если человек не задал своё).

    Возвращает текущее название или None. Ошибки не роняют бота.
    """
    try:
        chat = await bot.get_chat_by_id(chat_id)
        set_auto_title(chat_id, getattr(chat, "title", None))
    except Exception as exc:
        logging.warning("Чат %s: не удалось узнать название (%s)", chat_id, exc)
    return get_title(chat_id)


async def sync_members(bot, chat_id: int) -> int:
    """Подтягивает ВСЕХ участников чата и записывает их в базу.

    Работает, только если бот — администратор чата (так требует MAX).
    Возвращает число найденных людей. При ошибке (например, 403) бросает исключение.
    """
    total = 0
    marker = None
    for _ in range(50):  # защита от вечного цикла: не больше 5000 участников
        response = await bot.get_chat_members(chat_id=chat_id, marker=marker, count=100)
        members = getattr(response, "members", None) or []
        for member in members:
            if getattr(member, "is_bot", False):
                continue
            user_id = getattr(member, "user_id", None)
            if user_id is None:
                continue
            name = " ".join(
                x for x in (getattr(member, "first_name", ""), getattr(member, "last_name", "")) if x
            )
            touch_member(chat_id, user_id, name)
            total += 1
        marker = getattr(response, "marker", None)
        if not marker or not members:
            break
    mark_synced(chat_id)
    await refresh_title(bot, chat_id)  # заодно обновим название
    return total


async def ensure_synced(bot, chat_id: int):
    """Тихо обновляет список участников, если он давно не обновлялся. Ошибки только пишет в лог."""
    if bot is None:
        return
    now = time.time()
    synced = last_synced(chat_id)
    if synced and now * 1000 - synced < SYNC_EVERY_MS:
        return
    if now - _last_try.get(chat_id, 0) < RETRY_EVERY_S:
        return
    _last_try[chat_id] = now
    try:
        count = await sync_members(bot, chat_id)
        logging.info("Чат %s: участников подтянуто %d", chat_id, count)
    except Exception as exc:
        logging.warning("Чат %s: не удалось получить участников (%s). Бот администратор?", chat_id, exc)