"""Вечерняя сводка и утреннее напоминание о дедлайнах.

Включаются кнопкой «🔔 Напоминания» в личке с ботом. Вечером (по умолчанию в 19:00) приходит:
что сдавать завтра, что сегодня последний день, ближайшие сроки. Утром в 07:30 — только то, что сдавать сегодня.
Если заданий нет, сообщение не отправляется. Время — по Учалам (UTC+5).
"""
import logging
from datetime import date, datetime, timedelta

from app import config
from app.db import user_chats
from app.extras_db import all_enabled_subs, count_materials_since, mark_sent
from app.render import WEEKDAYS
from app.sync import has_access
from app.tasks_db import TZ, delete_expired, get_tasks

MORNING_AT = (7, 30)         # утреннее напоминание
EVENING_CATCHUP_H = 3        # если бот был выключен, вечернюю сводку ещё можно отправить в течение 3 часов
MORNING_UNTIL_H = 12         # утреннее — до полудня
SOON_DAYS = 4                # «ближайшие дни»: сколько дней после завтрашнего


def _fmt(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d:%d.%m}"


def _line(task, label: str, with_due: bool = False) -> str:
    mark = " ✅" if task[6] else ""
    due = f" — до {_fmt(date.fromisoformat(task[4]))}" if with_due and task[4] else ""
    chat = f" ({label})" if label else ""
    return f"• {task[2]}: {task[3]}{due}{mark}{chat}"


def build_digest(blocks, today: date, mode: str = "evening", new_materials: int = 0):
    """blocks — [(название чата, записи из get_tasks)]. Возвращает текст или None, если сказать нечего."""
    tomorrow = today + timedelta(days=1)
    multi = len(blocks) > 1
    due_today, due_tomorrow, soon, no_due = [], [], [], 0
    for label, tasks in blocks:
        label = label if multi else ""
        for t in tasks:
            if t[1] != "homework":
                continue
            if not t[4]:
                no_due += 1
                continue
            d = date.fromisoformat(t[4])
            if d == today:
                due_today.append(_line(t, label))
            elif d == tomorrow:
                due_tomorrow.append(_line(t, label))
            elif tomorrow < d <= tomorrow + timedelta(days=SOON_DAYS):
                soon.append((d, _line(t, label, with_due=True)))
    if mode == "morning":
        if not due_today:
            return None
        return "☀️ Доброе утро! Сегодня сдавать\n" + "\n".join(due_today) + "\n\nУдачного дня 💙"
    if not (due_today or due_tomorrow or soon):
        return None
    parts = [f"🌙 Вечерняя сводка · {_fmt(today)}"]
    if due_tomorrow:
        parts.append("⏰ Сдавать завтра\n" + "\n".join(due_tomorrow))
    if due_today:
        parts.append("🔥 Срок сегодня — последний день\n" + "\n".join(due_today))
    if soon:
        parts.append("📅 Ближайшие дни\n" + "\n".join(line for _d, line in sorted(soon)))
    tail = []
    if no_due:
        tail.append(f"📝 Ещё заданий без срока: {no_due}")
    if new_materials:
        tail.append(f"📎 Новых материалов за сутки: {new_materials}")
    if tail:
        parts.append("\n".join(tail))
    parts.append("🔎 Подробности и источники — в «Моей сводке».")
    return "\n\n".join(parts)


def which_due(sub, now: datetime):
    """Какие сообщения пора отправить подписчику: список из "evening" и/или "morning"."""
    _uid, _dm, enabled, hour, morning, last_evening, last_morning = sub
    if not enabled:
        return []
    today = now.date().isoformat()
    result = []
    if morning and last_morning != today and (MORNING_AT <= (now.hour, now.minute)) and now.hour < MORNING_UNTIL_H:
        result.append("morning")
    if last_evening != today and hour <= now.hour < hour + EVENING_CATCHUP_H:
        result.append("evening")
    return result


async def collect_blocks(bot, user_id: int, now: datetime):
    """Записи всех чатов, где человек состоит ПРЯМО СЕЙЧАС (проверка у MAX). Возвращает (блоки, новых материалов)."""
    blocks, new_materials = [], 0
    since = int((now - timedelta(days=1)).timestamp() * 1000)
    for chat_id, label in user_chats(user_id)[:8]:
        if await has_access(bot, chat_id, user_id):
            blocks.append((label, get_tasks(chat_id)))
            new_materials += count_materials_since(chat_id, since)
    return blocks, new_materials


def digest_keyboard():
    from app.menu import digest_buttons
    return digest_buttons()


async def send_digest(bot, sub, mode: str, now: datetime) -> bool:
    """Собирает и отправляет одно сообщение. Возвращает True, если отправили."""
    user_id, dm_chat_id = sub[0], sub[1]
    delete_expired(int(now.timestamp() * 1000))
    blocks, new_materials = await collect_blocks(bot, user_id, now)
    text = build_digest(blocks, now.date(), mode, new_materials)
    if not text:
        return False
    await bot.send_message(chat_id=dm_chat_id, text=text, attachments=[digest_keyboard()])
    return True


async def run_once(bot, now: datetime = None):
    """Один проход: отправляет всем, кому пора. Ошибки одного человека не мешают остальным."""
    now = now or datetime.now(TZ)
    for sub in all_enabled_subs():
        for mode in which_due(sub, now):
            try:
                await send_digest(bot, sub, mode, now)
            except Exception:
                logging.exception("Не удалось отправить сводку")
            mark_sent(sub[0], mode, now.date().isoformat())  # отметка и при сбое: не будем повторять каждую минуту