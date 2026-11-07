import asyncio
import time
import types
import unittest

from tests import _setup
from app import db, handlers, tasks_db


def run(coro):
    return asyncio.run(coro)


class Pipeline(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.ts = int(time.time() * 1000)
        self.real = handlers.extract_tasks

    def tearDown(self):
        handlers.extract_tasks = self.real

    def fake(self, items=None, error=False):
        async def f(text, day, ask):
            if error:
                raise RuntimeError("ИИ недоступен")
            return items
        handlers.extract_tasks = f

    def item(self, text="№215", due=None):
        return {"kind": "homework", "subject": "Алгебра", "text": text, "due": due}

    def test_record_and_source_saved(self):
        self.fake([self.item()])
        run(handlers.process_message(1, "m1", "Алгебра №215", self.ts, "Анна"))
        self.assertEqual(len(tasks_db.get_tasks(1)), 1)
        self.assertEqual(db.get_message(1, "m1")[2], "Алгебра №215")

    def test_no_items_means_nothing_stored(self):
        self.fake([])
        run(handlers.process_message(1, "m1", "ок, понял", self.ts, "Анна"))
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertIsNone(db.get_message(1, "m1"))

    def test_ai_error_keeps_old_records(self):
        self.fake([self.item()])
        run(handlers.process_message(1, "m1", "Алгебра №215", self.ts, "Анна"))
        self.fake(error=True)
        run(handlers.process_message(1, "m1", "Алгебра №215 (ред.)", self.ts, "Анна"))
        self.assertEqual(len(tasks_db.get_tasks(1)), 1)

    def test_edit_replaces_old(self):
        self.fake([self.item("№215")])
        run(handlers.process_message(1, "m1", "Алгебра №215", self.ts, "Анна"))
        self.fake([self.item("№216")])
        run(handlers.process_message(1, "m1", "Алгебра №216", self.ts, "Анна"))
        self.assertEqual([r[3] for r in tasks_db.get_tasks(1)], ["№216"])

    def test_edit_to_non_homework_removes_record(self):
        self.fake([self.item()])
        run(handlers.process_message(1, "m1", "Алгебра №215", self.ts, "Анна"))
        self.fake([])
        run(handlers.process_message(1, "m1", "Ой, это не мне", self.ts, "Анна"))
        self.assertEqual(tasks_db.get_tasks(1), [])


def make_message(text, chat_type="chat", is_bot=False):
    return types.SimpleNamespace(
        recipient=types.SimpleNamespace(chat_type=chat_type, chat_id=1),
        body=types.SimpleNamespace(text=text, mid="m"),
        sender=types.SimpleNamespace(user_id=5, first_name="А", last_name="Б", is_bot=is_bot),
        timestamp=int(time.time() * 1000),
    )


class Scheduling(unittest.TestCase):
    """schedule_message решает, отправлять ли сообщение на разбор (и вообще что-то с ним делать)."""

    def setUp(self):
        self.started = []
        self.real_run = handlers.run_in_background
        handlers.run_in_background = lambda coro: (self.started.append(1), coro.close())

    def tearDown(self):
        handlers.run_in_background = self.real_run

    def test_only_useful_group_messages_go_to_ai(self):
        self.assertTrue(handlers.schedule_message(make_message("Алгебра №215 до среды")))
        self.assertFalse(handlers.schedule_message(make_message("ок")))
        self.assertFalse(handlers.schedule_message(make_message("/summary")))
        self.assertFalse(handlers.schedule_message(make_message("Алгебра №215", chat_type="dialog")))
        self.assertFalse(handlers.schedule_message(make_message("Алгебра №215", is_bot=True)))
        self.assertEqual(len(self.started), 1)


if __name__ == "__main__":
    unittest.main()