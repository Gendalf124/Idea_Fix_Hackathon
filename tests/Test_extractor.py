import unittest

from tests import _setup  # noqa: F401
from app.extractor import looks_useful, parse_tasks


class ParseTasks(unittest.TestCase):
    def test_valid(self):
        raw = '{"items":[{"kind":"homework","subject":"Алгебра","text":"№215","due":"2026-11-04"}]}'
        self.assertEqual(parse_tasks(raw), [{"kind": "homework", "subject": "Алгебра", "text": "№215", "due": "2026-11-04"}])

    def test_markdown_fence_and_noise(self):
        raw = 'Вот ответ:\n```json\n{"items":[{"kind":"lesson","subject":"Физика","text":"Ньютон","due":"2026-11-04"}]}\n```'
        items = parse_tasks(raw)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["due"])  # у темы урока срока нет

    def test_garbage(self):
        for raw in ("", "не json", "{", '{"items": "x"}', "[1,2]", "null"):
            self.assertEqual(parse_tasks(raw), [], raw)

    def test_bad_date_keeps_record(self):
        raw = '{"items":[{"kind":"homework","subject":"Химия","text":"§3","due":"завтра"}]}'
        self.assertIsNone(parse_tasks(raw)[0]["due"])

    def test_unknown_kind_and_empty_text_dropped(self):
        raw = '{"items":[{"kind":"joke","subject":"X","text":"a"},{"kind":"homework","subject":"X","text":""}]}'
        self.assertEqual(parse_tasks(raw), [])

    def test_missing_subject(self):
        raw = '{"items":[{"kind":"homework","text":"решить задачи"}]}'
        self.assertEqual(parse_tasks(raw)[0]["subject"], "Без предмета")


class Prefilter(unittest.TestCase):
    def test_useful(self):
        for text in ("Алгебра №215 до среды", "Задали параграф 12 по биологии", "ДЗ: выучить стих"):
            self.assertTrue(looks_useful(text), text)

    def test_noise(self):
        for text in ("ок", "ахахах 😂😂😂", "кто идёт на физру?", ""):
            self.assertFalse(looks_useful(text), text)


if __name__ == "__main__":
    unittest.main()