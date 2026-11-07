import asyncio
import time
import types
import unittest

from tests import _setup
from app import db, sync, tasks_db
from app.menu import teacher_actions
from app.render import build_source_keyboard, build_source_text, render_tasks


def run(coro):
    return asyncio.run(coro)


def member(uid, owner=False):
    return types.SimpleNamespace(user_id=uid, is_bot=False, is_owner=owner)


class Bot:
    """Чат, где участники 1 (ученик), 2 (владелец), 3 (учитель по коду)."""
    def __init__(self, members=None):
        self.members = members if members is not None else [member(1), member(2, owner=True), member(3)]

    async def get_chat_members(self, chat_id, marker=None, count=100, user_ids=None):
        found = [m for m in self.members if user_ids is None or m.user_id in user_ids]
        return types.SimpleNamespace(members=found, marker=None)


class Verification(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.now = int(time.time() * 1000)

    def test_verify_all_and_marks(self):
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", None, self.now)
        tasks_db.add_task(1, "b", "homework", "Физика", "§2", None, self.now)
        text, _ = render_tasks(tasks_db.get_tasks(1))
        self.assertIn("ИИ · не проверено", text)
        self.assertEqual(tasks_db.verify_all(1), 2)
        self.assertEqual(tasks_db.verify_all(1), 0)  # повторно подтверждать нечего
        text, _ = render_tasks(tasks_db.get_tasks(1))
        self.assertIn("✅ Всё проверено", text)

    def test_partly_verified_footer(self):
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", None, self.now)
        tasks_db.add_task(1, "b", "homework", "Физика", "§2", None, self.now)
        tasks_db.verify_task(tasks_db.get_tasks(1)[0][0])
        text, _ = render_tasks(tasks_db.get_tasks(1))
        self.assertIn("остальное: ИИ · не проверено", text)

    def test_changed_deadline_removes_verification(self):
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", "2999-01-01", self.now)
        tasks_db.verify_all(1)
        tasks_db.add_task(1, "b", "homework", "Алгебра", "№1", None, self.now)         # срок не назван — остаётся ✅
        self.assertEqual(tasks_db.get_tasks(1)[0][6], 1)
        tasks_db.add_task(1, "c", "homework", "Алгебра", "№1", "2999-02-02", self.now)  # срок другой — ✅ снимается
        self.assertEqual(tasks_db.get_tasks(1)[0][6], 0)

    def test_delete_task_removes_orphan_message(self):
        db.save_message(1, "a", "Анна", self.now, "Алгебра №1")
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", None, self.now)
        task_id = tasks_db.get_tasks(1)[0][0]
        self.assertTrue(tasks_db.delete_task(task_id))
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertIsNone(db.get_message(1, "a"))

    def test_verify_button_only_for_teacher_and_only_if_needed(self):
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", None, self.now)
        _, ordered = render_tasks(tasks_db.get_tasks(1))
        student = _setup.rows_of(build_source_keyboard(ordered))
        teacher_kb = _setup.rows_of(build_source_keyboard(ordered, verify_chat=1))
        self.assertNotIn(["okall:1"], student)
        self.assertIn(["okall:1"], teacher_kb)
        tasks_db.verify_all(1)
        _, ordered = render_tasks(tasks_db.get_tasks(1))
        self.assertNotIn(["okall:1"], _setup.rows_of(build_source_keyboard(ordered, verify_chat=1)))

    def test_teacher_actions_buttons(self):
        self.assertEqual(_setup.rows_of(teacher_actions(7)), [["ok:7", "del:7"]])

    def test_source_text_mentions_verification(self):
        tasks_db.add_task(1, "a", "homework", "Алгебра", "№1", None, self.now)
        tasks_db.verify_all(1)
        task = tasks_db.get_task(tasks_db.get_tasks(1)[0][0])
        self.assertIn("Проверено", build_source_text(task, ("Анна", self.now, "Алгебра №1")))


class WhoCanVerify(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        for uid in (1, 2, 3):
            db.touch_member(100, uid)

    def test_student_cannot(self):
        self.assertFalse(run(sync.can_verify(Bot(), 100, 1)))

    def test_owner_can(self):
        self.assertTrue(run(sync.can_verify(Bot(), 100, 2)))

    def test_teacher_by_code_can(self):
        db.add_teacher(3)
        self.assertTrue(run(sync.can_verify(Bot(), 100, 3)))

    def test_teacher_who_left_chat_cannot(self):
        db.add_teacher(3)
        bot = Bot([member(1), member(2, owner=True)])  # 3 вышел
        self.assertFalse(run(sync.can_verify(bot, 100, 3)))

    def test_teacher_in_other_chat_is_still_member_check(self):
        db.add_teacher(5)  # учитель, но не состоит в чате 100
        self.assertFalse(run(sync.can_verify(Bot(), 100, 5)))


if __name__ == "__main__":
    unittest.main()
