import unittest

from tests import _setup
from app import config, db, teacher


class TeacherCode(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        teacher._waiting.clear()
        teacher._fails.clear()
        self.old = config.TEACHER_CODE
        config.TEACHER_CODE = "secret-123"

    def tearDown(self):
        config.TEACHER_CODE = self.old

    def test_disabled_without_code(self):
        config.TEACHER_CODE = ""
        self.assertEqual(teacher.start_waiting(1), "disabled")
        self.assertEqual(teacher.try_code(1, "что-угодно"), "disabled")
        self.assertFalse(db.is_teacher(1))

    def test_right_code_makes_teacher(self):
        self.assertEqual(teacher.start_waiting(1), "waiting")
        self.assertTrue(teacher.is_waiting(1))
        self.assertEqual(teacher.try_code(1, " secret-123 "), "ok")
        self.assertTrue(db.is_teacher(1))
        self.assertFalse(teacher.is_waiting(1))

    def test_wrong_code(self):
        teacher.start_waiting(1)
        self.assertEqual(teacher.try_code(1, "нет"), "wrong")
        self.assertFalse(db.is_teacher(1))

    def test_lock_after_five_wrong(self):
        teacher.start_waiting(1)
        results = [teacher.try_code(1, "x") for _ in range(5)]
        self.assertEqual(results[-1], "locked")
        self.assertEqual(teacher.try_code(1, "secret-123"), "locked")  # даже верный код в паузе не принимается
        self.assertFalse(db.is_teacher(1))

    def test_waiting_expires(self):
        teacher.start_waiting(1)
        teacher._waiting[1] -= teacher.WAIT_S + 1
        self.assertFalse(teacher.is_waiting(1))

    def test_not_waiting_by_default(self):
        self.assertFalse(teacher.is_waiting(42))


if __name__ == "__main__":
    unittest.main()
