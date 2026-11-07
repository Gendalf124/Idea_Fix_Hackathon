"""Подготовка тестов: временная база, фальшивые ключи и заглушки maxapi/openai (если не установлены).
Каждый тест начинается со строки:  from tests import _setup
"""
import os
import sys
import tempfile
import types

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("AI_API_KEY", "test-key")
DB_FILE = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DB_PATH"] = DB_FILE


class _Any:
    """Универсальная заглушка: любое обращение к атрибуту или вызов возвращает её же."""
    def __getattr__(self, name):
        return self
    def __call__(self, *a, **k):
        return self


class _Router:
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)  # @router.что_угодно() ничего не делает


class _Button:
    def __init__(self, text, payload):
        self.text, self.payload = text, payload


class _Builder:
    def __init__(self):
        self.rows = []
    def row(self, *buttons):
        self.rows.append([b.payload for b in buttons])
    def as_markup(self):
        return self.rows


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod


try:
    import maxapi  # noqa: F401
except ImportError:
    _stub("maxapi", F=_Any(), Router=_Router, Bot=lambda *a, **k: _Any(), Dispatcher=lambda *a, **k: _Any())
    _stub("maxapi.filters", )
    _stub("maxapi.filters.command", Command=lambda *a, **k: None, CommandStart=lambda *a, **k: None)
    _stub("maxapi.types", BotAdded=object, BotStarted=object, MessageCallback=object,
          MessageCreated=object, CallbackButton=_Button)
    _stub("maxapi.utils")
    _stub("maxapi.utils.inline_keyboard", InlineKeyboardBuilder=_Builder)

try:
    import openai  # noqa: F401
except ImportError:
    _stub("openai", AsyncOpenAI=lambda *a, **k: _Any())


def reset_db():
    """Пустая база с нуля. Файл не удаляем (на Windows он может быть занят), а очищаем таблицы."""
    import sqlite3
    from contextlib import closing

    from app import db, tasks_db

    with closing(sqlite3.connect(DB_FILE)) as con:
        with con:
            for table in ("messages", "chats", "members", "chat_sync", "tasks", "teachers", "materials", "subs"):
                con.execute(f"DROP TABLE IF EXISTS {table}")
    db.init_db()
    tasks_db.init_tasks()


def rows_of(keyboard):
    """Кнопки клавиатуры как список рядов из payload. Работает и с настоящей maxapi, и с заглушкой."""
    if isinstance(keyboard, list):
        return keyboard
    return [[b.payload for b in row] for row in keyboard.payload.buttons]