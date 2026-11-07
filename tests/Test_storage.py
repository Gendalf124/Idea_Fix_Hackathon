import time
import unittest

from tests import _setup
from app import db, tasks_db

DAY = 86400 * 1000


class Storage(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.now = int(time.time() * 1000)

    def test_duplicates_are_merged(self):
        a = tasks_db.add_task(1, "m1", "homework", "Алгебра", "№215", None, self.now)
        b = tasks_db.add_task(1, "m2", "homework", "алгебра", "№ 215!", "2030-01-01", self.now)
        self.assertEqual((a, b), ("added", "updated"))
        rows = tasks_db.get_tasks(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], "2030-01-01")  # срок обновился
        self.assertEqual(tasks_db.get_task(rows[0][0])[1], "m2")  # источник — последнее напоминание

    def test_same_text_in_other_chat_is_not_duplicate(self):
        tasks_db.add_task(1, "m1", "homework", "Алгебра", "№215", None, self.now)
        tasks_db.add_task(2, "m1", "homework", "Алгебра", "№215", None, self.now)
        self.assertEqual(len(tasks_db.get_tasks(1)), 1)
        self.assertEqual(len(tasks_db.get_tasks(2)), 1)

    def test_expiry(self):
        tasks_db.add_task(1, "a", "homework", "Алг", "прошлый срок", "2000-01-01", self.now)
        tasks_db.add_task(1, "b", "homework", "Алг", "будущий срок", "2999-01-01", self.now)
        tasks_db.add_task(1, "c", "lesson", "Физ", "старая тема", None, self.now - 8 * DAY)
        tasks_db.add_task(1, "d", "lesson", "Физ", "свежая тема", None, self.now - 1 * DAY)
        removed = tasks_db.delete_expired()
        self.assertEqual(removed, 2)
        texts = {r[3] for r in tasks_db.get_tasks(1)}
        self.assertEqual(texts, {"будущий срок", "свежая тема"})

    def test_original_message_lives_no_longer_than_record(self):
        db.save_message(1, "a", "Анна", self.now, "Алгебра до пятницы")
        tasks_db.add_task(1, "a", "homework", "Алг", "x", "2000-01-01", self.now)
        db.save_message(1, "orphan", "Боб", self.now, "сообщение без записи")
        tasks_db.delete_expired()
        self.assertIsNone(db.get_message(1, "a"))
        self.assertIsNone(db.get_message(1, "orphan"))

    def test_edit_replaces_records(self):
        tasks_db.add_task(1, "m", "homework", "Алг", "№215", None, self.now)
        tasks_db.delete_tasks_for_message(1, "m")
        tasks_db.add_task(1, "m", "homework", "Алг", "№216", None, self.now)
        self.assertEqual([r[3] for r in tasks_db.get_tasks(1)], ["№216"])

    def test_delete_chat_data(self):
        db.touch_member(1, 10)
        db.save_message(1, "a", "x", self.now, "t")
        tasks_db.add_task(1, "a", "homework", "Алг", "x", None, self.now)
        db.touch_member(2, 10)
        db.delete_chat_data(1)
        self.assertFalse(db.is_member(1, 10))
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertIsNone(db.get_message(1, "a"))
        self.assertTrue(db.is_member(2, 10))  # другой чат не тронут

    def test_member_names_not_stored(self):
        db.touch_member(1, 10, "Иван Петров")
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(_setup.DB_FILE)) as con:
            row = con.execute("SELECT user_name FROM members").fetchone()
        self.assertEqual(row[0], "")

    def test_init_is_repeatable(self):
        db.init_db()
        tasks_db.init_tasks()


if __name__ == "__main__":
    unittest.main()