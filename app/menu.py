"""Все кнопки бота в одном месте. Каждая кнопка — это (текст, payload).
Payload — «пароль» кнопки: по нему handlers.py понимает, что нажали.

  menu:summary — «Моя сводка»        menu:all  — «Все чаты сразу»
  menu:help    — «Как подключить»    menu:home — «Главное меню»
  sum:<chat>   — сводка одного чата  scan:<chat> — обновить участников  title:<chat> — задать название вручную
  src:<task>   — 🔎 источник записи
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
        [("❓ Как подключить класс", "menu:help")],
    ])


def group_menu(chat_id: int):
    """Кнопки в чате класса."""
    return _keyboard([
        [("📋 Сводка класса", f"sum:{chat_id}")],
        [("✏️ Название чата", f"title:{chat_id}")],
        [("🔄 Обновить участников", f"scan:{chat_id}")],
    ])


def chat_picker(chats):
    """Выбор чата: по кнопке на каждый + «все сразу» + «меню»."""
    rows = [[(label, f"sum:{chat_id}")] for chat_id, label in chats[:8]]
    rows.append([("🗂 Все чаты сразу", "menu:all")])
    rows.append([("🏠 Меню", "menu:home")])
    return _keyboard(rows)


def bottom_buttons_dm():
    """Нижний ряд под сводкой в личке (список кнопок для render)."""
    return [CallbackButton(text="🏠 Меню", payload="menu:home")]


def bottom_buttons_group(chat_id: int):
    """Нижний ряд под сводкой в чате."""
    return [CallbackButton(text="🔄 Обновить", payload=f"sum:{chat_id}")]


def back_menu():
    return _keyboard([[("🏠 Меню", "menu:home")]])