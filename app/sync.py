import logging
import time

from app.db import (
    get_title, is_member, is_owner, is_teacher, last_synced, mark_synced, remove_absent_members,
    remove_member, set_auto_title, touch_member,
)

SYNC_EVERY_MS = 6 * 3600 * 1000  # список участников обновляем раз в 6 часов
RETRY_EVERY_S = 1800             # после неудачи повторяем не чаще раза в 30 минут
CACHE_TTL_S = 6 * 3600           # если MAX не ответил, верим записи о человеке не старше 6 часов
_last_try = {}                   # chat_id -> время последней попытки


async def refresh_title(bot, chat_id: int):
    """Узнаёт название чата из MAX (если человек не задал своё). Ошибки не роняют бота."""
    try:
        chat = await bot.get_chat_by_id(chat_id)
        set_auto_title(chat_id, getattr(chat, "title", None))
    except Exception as exc:
        logging.warning("Чат %s: не удалось узнать название (%s)", chat_id, exc)
    return get_title(chat_id)


async def sync_members(bot, chat_id: int) -> int:
    """Подтягивает ВСЕХ участников чата и записывает в базу. Вышедших и исключённых — удаляет.

    Удаляем только если список получен ЦЕЛИКОМ: при сбое на середине никого не вычёркиваем.
    Работает, если бот — администратор. Возвращает число людей; при ошибке бросает исключение.
    """
    seen = set()
    marker = None
    complete = False
    for _ in range(50):  # защита от вечного цикла: не больше 5000 участников
        response = await bot.get_chat_members(chat_id=chat_id, marker=marker, count=100)
        members = getattr(response, "members", None) or []
        for member in members:
            user_id = getattr(member, "user_id", None)
            if user_id is None or getattr(member, "is_bot", False):
                continue
            seen.add(user_id)
            touch_member(chat_id, user_id, owner=bool(getattr(member, "is_owner", False)))
        marker = getattr(response, "marker", None)
        if not marker or not members:
            complete = True
            break
    if complete and seen:
        removed = remove_absent_members(chat_id, seen)
        if removed:
            logging.info("Чат %s: убрано вышедших участников: %d", chat_id, removed)
    mark_synced(chat_id)
    await refresh_title(bot, chat_id)
    return len(seen)


async def ensure_synced(bot, chat_id: int):
    """Тихо обновляет список участников, если он давно не обновлялся."""
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


async def has_access(bot, chat_id: int, user_id) -> bool:
    """Главная проверка доступа: человек СЕЙЧАС состоит в этом чате?

    1. Спрашиваем MAX напрямую — так вышедший ученик теряет доступ сразу.
    2. Если MAX недоступен или ответ непонятный — верим базе, но только свежим записям (до 6 часов).
    """
    if user_id is None:
        return False
    try:
        response = await bot.get_chat_members(chat_id=chat_id, user_ids=[user_id])
        members = getattr(response, "members", None) or []
        if any(getattr(m, "user_id", None) == user_id for m in members):
            touch_member(chat_id, user_id)
            return True
        if not members:  # MAX ответил по фильтру: такого участника в чате нет
            remove_member(chat_id, user_id)
            return False
        # Пришли чужие участники — фильтр не сработал, уверенности нет: идём к запасному варианту.
    except Exception as exc:
        logging.warning("Чат %s: не удалось проверить участника (%s)", chat_id, exc)
    return is_member(chat_id, user_id, max_age_s=CACHE_TTL_S)


async def can_verify(bot, chat_id: int, user_id) -> bool:
    """Может ли человек подтверждать записи этого чата.

    Да, если он сейчас состоит в чате и при этом: ввёл код учителя ИЛИ является владельцем чата в MAX.
    """
    if not await has_access(bot, chat_id, user_id):
        return False
    if is_teacher(user_id):
        return True
    try:
        response = await bot.get_chat_members(chat_id=chat_id, user_ids=[user_id])
        for m in getattr(response, "members", None) or []:
            if getattr(m, "user_id", None) == user_id:
                owner = bool(getattr(m, "is_owner", False))
                touch_member(chat_id, user_id, owner=owner)
                return owner
    except Exception as exc:
        logging.warning("Чат %s: не удалось узнать роль участника (%s)", chat_id, exc)
    return is_owner(chat_id, user_id)