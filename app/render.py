from datetime import date, datetime, timedelta, timezone

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

TZ = timezone(timedelta(hours=5))  # Учалы, UTC+5
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_BUTTONS = 16  # столько кнопок 🔎 максимум под одной сводкой
CONF_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}

# Запись из get_tasks: (id, kind, subject, text, due, created_ts, verified, src_kind, conf)


def fmt_due(due: str) -> str:
    d = date.fromisoformat(due)
    return f"до {WEEKDAYS[d.weekday()]}, {d:%d.%m}"


def _marks(t) -> str:
    """Значки после строки: ✅ проверено; 📷 из фото; 🎙 из голосового; ⚠ если расшифровка неуверенная."""
    verified, src_kind, conf = t[6], t[7], t[8]
    if src_kind == "photo":
        return " 📷 ✅" if verified else " 📷"
    if verified:
        return " ✅"
    if src_kind == "voice":
        return " 🎙" + (" ⚠ проверьте" if conf in ("low", "unknown", None) else "")
    return ""


def render_tasks(tasks):
    """Делает текст сводки. Возвращает (текст, записи в том порядке, как они пронумерованы)."""
    lessons = [t for t in tasks if t[1] == "lesson"]
    homework = [t for t in tasks if t[1] == "homework"]
    ordered = lessons + homework

    parts = []
    number = 0
    if lessons:
        lines = ["📚 Что проходили"]
        for t in lessons:
            number += 1
            lines.append(f"{number}. {t[2]}: {t[3]}{_marks(t)}")
        parts.append("\n".join(lines))
    if homework:
        lines = ["📝 Домашнее задание и дедлайны"]
        for t in homework:
            number += 1
            tail = f" — {fmt_due(t[4])}" if t[4] else ""
            lines.append(f"{number}. {t[2]}: {t[3]}{tail}{_marks(t)}")
        parts.append("\n".join(lines))

    if not parts:
        return "Пока ничего не нашёл: ни домашки, ни тем уроков.", []

    done = sum(1 for t in ordered if t[6])
    if done == len(ordered):
        footer = "✅ Всё проверено учителем или владельцем чата"
    elif done:
        footer = "✅ — проверено учителем, остальное: ИИ · не проверено"
    else:
        footer = "ИИ · не проверено учителем"
    text = "\n\n".join(parts) + "\n\n🔎 — показать исходное сообщение\n" + footer
    return text, ordered


def build_source_keyboard(ordered, extra=None, verify_chat=None):
    """Кнопки «🔎 1», «🔎 2»… — номер совпадает с номером строки в сводке.

    verify_chat — если задан (смотрит учитель), добавляется кнопка «Подтвердить всё»,
    пока есть непроверенные записи. extra — нижний ряд («Меню», «Обновить»).
    """
    builder = InlineKeyboardBuilder()
    buttons = [
        CallbackButton(text=f"🔎 {i}", payload=f"src:{task[0]}")
        for i, task in enumerate(ordered[:MAX_BUTTONS], start=1)
    ]
    for i in range(0, len(buttons), 4):  # по 4 кнопки в ряд
        builder.row(*buttons[i : i + 4])
    if verify_chat is not None and any(not t[6] for t in ordered):
        builder.row(CallbackButton(text="✅ Подтвердить всё", payload=f"okall:{verify_chat}"))
    if extra:
        builder.row(*extra)
    return builder.as_markup()


def build_source_text(task, original) -> str:
    """task — из get_task, original — из get_message (или None, если сообщения уже нет)."""
    _chat_id, _mid, _kind, subject, text, due, _ts, verified, src_kind, conf = task[:10]
    head = f"🔎 Источник записи\n{subject}: {text}" + (f" — {fmt_due(due)}" if due else "")
    if verified:
        head += "\n✅ Проверено учителем или владельцем чата"
    if original is None:
        return head + "\n\nИсходное сообщение уже удалено."
    user_name, ts, body = original
    when = datetime.fromtimestamp(ts / 1000, tz=TZ)
    who = user_name or "Участник"
    if src_kind == "photo":
        return (
            f"{head}\n\n📷 Запись составлена по фото и ближайшим сообщениям. ИИ мог ошибиться: "
            f"сверьтесь с фото.\n\n{who}, {when:%d.%m в %H:%M}:\n«{body[:1000]}»"
        )
    if src_kind == "voice":
        note = f"🎙 Голосовое сообщение ({who}, {when:%d.%m в %H:%M}). Расшифровка ИИ:\n«{body[:1000]}»"
        note += f"\nУверенность распознавания: {CONF_RU.get(conf, 'неизвестна')}."
        if conf in ("low", "unknown", None):
            note += " Лучше проверить на слух."
        note += "\nАудио не сохраняется — осталась только расшифровка."
        return f"{head}\n\n{note}"
    return f"{head}\n\n{who}, {when:%d.%m в %H:%M}:\n«{body[:1000]}»"