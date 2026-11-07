"""Демо-режим: вымышленный класс 7Б. Ничего не пишется в базу и не связано с реальными чатами."""
from datetime import datetime, timedelta

from app.render import build_source_text
from app.tasks_db import TZ

DEMO_TITLE = "🎬 Демо — вымышленный класс 7Б. Данные придуманы."


def _day(days_ahead: int) -> str:
    return (datetime.now(TZ).date() + timedelta(days=days_ahead)).isoformat()


def demo_rows():
    """Записи в том же формате, что возвращает get_tasks:
    (id, kind, subject, text, due, created_ts, verified, src_kind, conf).
    id отрицательные, чтобы не спутать с настоящими записями."""
    return [
        (-1, "lesson", "Физика", "Законы Ньютона, первая часть темы", None, 0, 1, "text", None),
        (-2, "lesson", "История", "Реформы Петра I", None, 0, 0, "text", None),
        (-3, "homework", "Алгебра", "№215–218", _day(1), 0, 1, "text", None),
        (-4, "homework", "Литература", "Выучить стихотворение наизусть", _day(2), 0, 0, "text", None),
        (-5, "homework", "Биология", "Параграф 12, ответить на вопросы", _day(4), 0, 0, "photo", None),
    ]


DEMO_SOURCES = {
    -1: ("Учитель физики", "Сегодня разобрали первый и второй законы Ньютона, на следующем уроке третий."),
    -2: ("Ученик 1", "Проходили реформы Петра Первого, кто пропустил — смотрите параграф 20."),
    -3: ("Староста", "Алгебра: №215–218, сдаём завтра."),
    -4: ("Учитель литературы", "Не забудьте выучить стихотворение наизусть к четвергу."),
    -5: ("Ученик 2", "ДЗ на понедельник\n[фото страницы учебника: параграф 12, вопросы в конце]"),
}


def demo_source_text(task_id: int) -> str:
    """Текст для кнопки 🔎 в демо."""
    row = next((r for r in demo_rows() if r[0] == task_id), None)
    if row is None or task_id not in DEMO_SOURCES:
        return "Это демо-запись."
    _id, kind, subject, text, due, _ts, verified, src_kind, conf = row
    author, body = DEMO_SOURCES[task_id]
    task = (0, "demo", kind, subject, text, due, 0, verified, src_kind, conf)
    original = (author, int(datetime.now(TZ).timestamp() * 1000), body)
    return build_source_text(task, original)