"""Все кнопки бота в одном месте. Каждая кнопка — это (текст, payload).
Payload — «пароль» кнопки: по нему handlers.py понимает, что нажали.

  menu:summary — «Моя сводка»        menu:all  — «Все чаты сразу»
  menu:demo — «Демо»  menu:teacher — «Я учитель»  menu:help — «Как подключить»
  ok:<task> / okall:<chat> / del:<task> — подтвердить / подтвердить всё / удалить запись    menu:home — «Главное меню»
  sum:<chat>   — сводка одного чата  scan:<chat> — обновить участников  title:<chat> — задать название вручную
  src:<task>   — 🔎 источник записи
  mat:<chat>   — «Материалы» чата   mf:<id> — прислать материал ещё раз   hist:<chat> — прочитать историю за 7 дней
  menu:mat / menu:notify — «Материалы» и «Напоминания» в личке
  nt:on | nt:off | nt:h:<час> | nt:m | nt:now — настройки вечерней сводки
"""
from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def _keyboard(rows):
    """rows — список рядов, ряд — список кнопок (текст, payload)."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.row(*[CallbackButton(text=t, payload=p) for t, p in row])
    return builder.as_markup()


def main_menu():
    """Главное меню в личке с ботом."""
    return _keyboard([
        [("📋 Моя сводка", "menu:summary")],
        [("🗂 Все чаты сразу", "menu:all")],
        [("📎 Материалы", "menu:mat"), ("🔔 Напоминания", "menu:notify")],
        [("🎬 Демо", "menu:demo")],
        [("👩‍🏫 Я учитель", "menu:teacher")],
        [("❓ Как подключить класс", "menu:help")],
    ])


def group_menu(chat_id: int):
    """Кнопки в чате класса."""
    return _keyboard([
        [("📋 Сводка класса", f"sum:{chat_id}")],
        [("📎 Материалы", f"mat:{chat_id}"), ("📥 История за 7 дней", f"hist:{chat_id}")],
        [("✏️ Название чата", f"title:{chat_id}")],
        [("🔄 Обновить участников", f"scan:{chat_id}")],
    ])


def chat_picker(chats, prefix: str = "sum"):
    """Выбор чата: по кнопке на каждый + «все сразу» (для сводки) + «меню»."""
    rows = [[(label, f"{prefix}:{chat_id}")] for chat_id, label in chats[:8]]
    if prefix == "sum":
        rows.append([("🗂 Все чаты сразу", "menu:all")])
    rows.append([("🏠 Меню", "menu:home")])
    return _keyboard(rows)


def bottom_buttons_dm():
    """Нижний ряд под сводкой в личке (список кнопок для render)."""
    return [CallbackButton(text="🏠 Меню", payload="menu:home")]


def bottom_buttons_group(chat_id: int):
    """Нижний ряд под сводкой в чате."""
    return [CallbackButton(text="🔄 Обновить", payload=f"sum:{chat_id}")]


def bottom_buttons_materials(chat_id: int):
    """Нижний ряд под списком материалов."""
    return [CallbackButton(text="📋 Сводка", payload=f"sum:{chat_id}")]


def back_menu():
    return _keyboard([[("🏠 Меню", "menu:home")]])


def teacher_actions(task_id: int):
    """Кнопки под источником записи для учителя."""
    return _keyboard([[("✅ Подтвердить", f"ok:{task_id}"), ("🗑 Удалить", f"del:{task_id}")]])


def source_buttons(chat_id: int, task_id: int = None, teacher: bool = False, in_group: bool = False, demo: bool = False):
    """Кнопки под источником записи: вернуться к сводке и домой. Учителю сверху — ✅ и 🗑.

    В чате класса «Меню» не нужно (главное меню живёт в личке), там только возврат к сводке."""
    rows = []
    if teacher and task_id is not None:
        rows.append([("✅ Подтвердить", f"ok:{task_id}"), ("🗑 Удалить", f"del:{task_id}")])
    back = ("◀ К демо", "menu:demo") if demo else ("◀ К сводке", f"sum:{chat_id}")
    rows.append([back] if in_group else [back, ("🏠 Меню", "menu:home")])
    return _keyboard(rows)


def notify_menu(sub):
    """Настройки напоминаний. sub — строка из get_sub или None (ещё не включали)."""
    enabled = bool(sub and sub[2])
    hour = sub[3] if sub else 19
    morning = bool(sub[4]) if sub else True
    hours = [(f"{'✅ ' if h == hour else ''}{h}:00", f"nt:h:{h}") for h in (18, 19, 20, 21)]
    return _keyboard([
        [("🔕 Выключить", "nt:off") if enabled else ("🔔 Включить", "nt:on")],
        hours,
        [(("☀️ Утром 07:30: вкл" if morning else "☀️ Утром 07:30: выкл"), "nt:m")],
        [("📨 Прислать сейчас", "nt:now")],
        [("🏠 Меню", "menu:home")],
    ])


def digest_buttons():
    """Кнопки под вечерней сводкой."""
    return _keyboard([
        [("📋 Моя сводка", "menu:summary"), ("📎 Материалы", "menu:mat")],
        [("🔔 Настроить", "menu:notify")],
    ])