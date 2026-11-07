import unittest

from tests import _setup
from app.demo import demo_rows, demo_source_text
from app.menu import bottom_buttons_dm, main_menu
from app.render import build_source_keyboard, render_tasks


class DemoAndRender(unittest.TestCase):
    def test_demo_summary_and_sources(self):
        text, ordered = render_tasks(demo_rows())
        self.assertIn("Домашнее задание", text)
        self.assertEqual(len(ordered), 5)
        kb = build_source_keyboard(ordered, extra=bottom_buttons_dm())
        rows = _setup.rows_of(kb)
        self.assertEqual(rows[0], ["src:-1", "src:-2", "src:-3", "src:-4"])
        self.assertEqual(rows[-1], ["menu:home"])
        for tid in range(-5, 0):
            self.assertIn("Источник", demo_source_text(tid))
        self.assertIn("✅", text)           # в демо есть проверенная запись
        self.assertIn("Учитель литературы", demo_source_text(-4))

    def test_main_menu_has_demo(self):
        self.assertIn(["menu:demo"], _setup.rows_of(main_menu()))

    def test_empty(self):
        text, ordered = render_tasks([])
        self.assertEqual(ordered, [])


if __name__ == "__main__":
    unittest.main()