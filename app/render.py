from datetime import date, datetime, timedelta, timezone

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

TZ = timezone(timedelta(hours=5))  # Учалы, UTC+5
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_BUTTONS = 16  # столько кнопок 🔎 максимум под одной сводкой


def fmt_due(due: str) -> str:
    d = date.fromisoformat(due)
    return f"до {WEEKDAYS[d.weekday()]}, {d:%d.%m}"


def render_tasks(tasks):
    """Делает текст сводки. Возвращает (текст, записи в том порядке, как они пронумерованы)."""
    lessons = [t for t in tasks if t[1] == "lesson"]
    homework = [t for t in tasks if t[1] == "homework"]
    ordered = lessons + homework

    parts = []
    number = 0
    if lessons:
        lines = ["📚 Что проходили"]
        for _id, _kind, subject, text, _due, _ts in lessons:
            number += 1
            lines.append(f"{number}. {subject}: {text}")
        parts.append("\n".join(lines))
    if homework:
        lines = ["📝 Домашнее задание и дедлайны"]
        for _id, _kind, subject, text, due, _ts in homework:
            number += 1
            tail = f" — {fmt_due(due)}" if due else ""
            lines.append(f"{number}. {subject}: {text}{tail}")
        parts.append("\n".join(lines))

    if not parts:
        return "Пока ничего не нашёл: ни домашки, ни тем уроков.", []
    text = "\n\n".join(parts) + "\n\n🔎 — показать исходное сообщение\nИИ · не проверено учителем"
    return text, ordered


def build_source_keyboard(ordered):
    """Кнопки «🔎 1», «🔎 2»… — номер совпадает с номером строки в сводке."""
    builder = InlineKeyboardBuilder()
    buttons = [
        CallbackButton(text=f"🔎 {i}", payload=f"src:{task[0]}")
        for i, task in enumerate(ordered[:MAX_BUTTONS], start=1)
    ]
    for i in range(0, len(buttons), 4):  # по 4 кнопки в ряд
        builder.row(*buttons[i : i + 4])
    return builder.as_markup()


def build_source_text(task, original) -> str:
    """task — из get_task, original — из get_message (или None, если сообщения уже нет)."""
    _chat_id, _mid, _kind, subject, text, due, _ts = task
    head = f"🔎 Источник записи\n{subject}: {text}" + (f" — {fmt_due(due)}" if due else "")
    if original is None:
        return head + "\n\nИсходное сообщение уже удалено."
    user_name, ts, body = original
    when = datetime.fromtimestamp(ts / 1000, tz=TZ)
    return f"{head}\n\n{user_name or 'Участник'}, {when:%d.%m в %H:%M}:\n«{body[:1000]}»"