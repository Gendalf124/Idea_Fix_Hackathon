import asyncio
import time
import types
import unittest
from datetime import date

from tests import _setup
from app import ai, config, db, extractor, handlers, photos, tasks_db
from app.context import ChatContext, Entry, buffer
from app.planner import make_plan
from app.render import build_source_text, render_tasks


def run(coro):
    return asyncio.run(coro)


NOW = 1_800_000_000_000
MIN = 60_000


def entry(mid, minutes_ago=0, text="", images=0, author="Анна", done=False):
    imgs = [{"url": f"https://x/{mid}-{i}.jpg", "token": f"tok-{mid}-{i}"} for i in range(images)]
    return Entry(mid=mid, ts=NOW - minutes_ago * MIN, author=author, text=text, images=imgs, done=done)


class Memory(unittest.TestCase):
    def test_forgets_after_ten_minutes(self):
        ctx = ChatContext()
        ctx.push(1, entry("a", minutes_ago=11, text="старое"))
        ctx.push(1, entry("b", minutes_ago=2, text="свежее"))
        self.assertEqual([e.mid for e in ctx.window(1, NOW)], ["b"])
        self.assertEqual(ctx.window(2, NOW), [])  # другой чат пуст

    def test_limit_and_order(self):
        ctx = ChatContext(max_items=3)
        for i in range(5):
            ctx.push(1, entry(str(i), minutes_ago=5 - i, text="t"))
        self.assertEqual([e.mid for e in ctx.window(1, NOW)], ["2", "3", "4"])

    def test_mark_done_and_forget_chat(self):
        ctx = ChatContext()
        ctx.push(1, entry("a", text="t"))
        ctx.mark_done(1, ["a"])
        self.assertTrue(ctx.window(1, NOW)[0].done)
        ctx.forget_chat(1)
        self.assertEqual(ctx.window(1, NOW), [])


class Planning(unittest.TestCase):
    def plan(self, window, current, mode="hinted"):
        return make_plan(window, current, mode)

    def test_noise_is_ignored(self):
        e = entry("a", text="ахаха ок")
        self.assertIsNone(self.plan([e], e))

    def test_second_message_completes_first(self):
        first, second = entry("a", 3, "Ребята, ДЗ на завтра по алгебре"), entry("b", 0, "номер 112, 45 страница")
        plan = self.plan([first, second], second)
        self.assertEqual([e.mid for e in plan.entries], ["a", "b"])
        self.assertIs(plan.anchor, second)

    def test_short_numbers_alone_are_ignored(self):
        e = entry("b", 0, "112")
        self.assertIsNone(self.plan([e], e))

    def test_photo_after_hint_is_read(self):
        text, photo = entry("a", 2, "ДЗ на завтра"), entry("b", 0, images=1)
        plan = self.plan([text, photo], photo)
        self.assertEqual(len(plan.images), 1)

    def test_photo_without_hint_waits_in_hinted_mode(self):
        photo = entry("a", 0, images=1)
        self.assertIsNone(self.plan([photo], photo))

    def test_waiting_photo_is_read_when_text_comes(self):
        photo, text = entry("a", 3, images=1), entry("b", 0, "Задали параграф 12")
        plan = self.plan([photo, text], text)
        self.assertEqual([i[1]["token"] for i in plan.images], ["tok-a-0"])

    def test_photo_caption_hint(self):
        photo = entry("a", 0, "дз на завтра", images=1)
        self.assertEqual(len(self.plan([photo], photo).images), 1)

    def test_scan_all_reads_every_photo(self):
        photo = entry("a", 0, images=1)
        self.assertEqual(len(self.plan([photo], photo, "all").images), 1)

    def test_scan_off_never_reads_photos(self):
        text, photo = entry("a", 1, "ДЗ на завтра"), entry("b", 0, images=1)
        plan = self.plan([text, photo], photo, "off")
        self.assertEqual(plan.images, [])
        self.assertIsNone(self.plan([photo], photo, "off"))

    def test_done_photos_not_resent_and_limit(self):
        old = entry("a", 5, images=1, done=True)
        many = [entry(str(i), 4 - i, images=1) for i in range(5)]
        text = entry("t", 0, "ДЗ по физике")
        plan = self.plan([old] + many + [text], text)
        self.assertEqual(len(plan.images), 3)
        self.assertNotIn("tok-a-0", [i[1]["token"] for i in plan.images])


class Prompt(unittest.TestCase):
    def test_prompt_numbers_photos(self):
        text = extractor.build_context_prompt(
            [entry("a", 2, "ДЗ на завтра", author="Учитель"), entry("b", 0, images=2, author="Ученик")], date(2026, 11, 2)
        )
        self.assertIn("2026-11-02", text)
        self.assertIn("1. Учитель: ДЗ на завтра", text)
        self.assertIn("[фото №1 во вложении]", text)
        self.assertIn("[фото №2 во вложении]", text)

    def test_vision_used_only_with_images(self):
        calls = []

        async def ask(prompt, system=None):
            calls.append("text")
            return '{"items": []}'

        async def vision(prompt, system, images):
            calls.append(("vision", len(images)))
            return '{"items": [{"kind": "homework", "subject": "Алгебра", "text": "№112", "due": null}]}'

        e = [entry("a", 0, "дз")]
        self.assertEqual(run(extractor.extract_with_context(e, [], date.today(), ask, vision)), [])
        got = run(extractor.extract_with_context(e, [(b"img", "image/jpeg")], date.today(), ask, vision))
        self.assertEqual(calls, ["text", ("vision", 1)])
        self.assertEqual(got[0]["subject"], "Алгебра")

    def test_ask_vision_sends_images_and_model(self):
        captured = {}

        class Completions:
            async def create(inner, **kwargs):
                captured.update(kwargs)
                msg = types.SimpleNamespace(content="ok")
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        old = ai.vision_client
        ai.vision_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
        try:
            run(ai.ask_vision("что на фото", "система", [(b"abc", "image/png")]))
        finally:
            ai.vision_client = old
        self.assertEqual(captured["model"], config.VISION_MODEL)
        parts = captured["messages"][1]["content"]
        self.assertEqual(parts[0]["type"], "text")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))


class Photos(unittest.TestCase):
    def message(self, *attachments):
        return types.SimpleNamespace(body=types.SimpleNamespace(attachments=list(attachments)))

    def att(self, kind, url="https://x/p.jpg", token="tok"):
        return types.SimpleNamespace(type=kind, payload=types.SimpleNamespace(url=url, token=token))

    def test_image_infos(self):
        infos = photos.image_infos(self.message(self.att("image"), self.att("file"), self.att("audio")))
        self.assertEqual(infos, [{"url": "https://x/p.jpg", "token": "tok"}])
        self.assertEqual(photos.image_infos(types.SimpleNamespace(body=types.SimpleNamespace(attachments=None))), [])

    def test_only_https(self):
        with self.assertRaises(ValueError):
            run(photos.download_image("http://x/p.jpg"))
        with self.assertRaises(ValueError):
            run(photos.download_image(None))

    def test_no_token_no_attachment(self):
        self.assertIsNone(photos.photo_attachment(None))


class Processing(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.saved = (handlers.extract_with_context, photos.download_image, config.PHOTO_SCAN)
        self.seen = {}
        self.items = [{"kind": "homework", "subject": "Алгебра", "text": "№112, стр. 45", "due": None}]

        async def fake_extract(entries, images, day, ask, ask_vision):
            self.seen = {"entries": [e.mid for e in entries], "images": len(images)}
            return self.items

        async def fake_download(url):
            return b"img", "image/jpeg"

        handlers.extract_with_context, photos.download_image = fake_extract, fake_download
        buffer._items.clear()

    def tearDown(self):
        handlers.extract_with_context, photos.download_image, config.PHOTO_SCAN = self.saved
        buffer._items.clear()

    def go(self, window, current):
        for e in window:
            buffer.push(1, e)
        plan = make_plan(buffer.window(1, NOW), current, "hinted")
        run(handlers.process_plan(1, plan))
        return plan

    def test_text_then_text_becomes_one_record_with_context_source(self):
        first, second = entry("a", 3, "ДЗ на завтра по алгебре"), entry("b", 0, "номер 112 стр 45", author="Боб")
        self.go([first, second], second)
        row = tasks_db.get_tasks(1)[0]
        self.assertEqual((row[2], row[7]), ("Алгебра", "text"))
        source = db.get_message(1, "b")[2]
        self.assertIn("ДЗ на завтра", source)
        self.assertIn("номер 112", source)

    def test_photo_is_attached_by_token_and_not_stored(self):
        text, photo = entry("a", 2, "ДЗ на завтра"), entry("b", 0, images=1)
        self.go([text, photo], photo)
        row = tasks_db.get_tasks(1)[0]
        self.assertEqual(row[7], "photo")
        task = tasks_db.get_task(row[0])
        self.assertEqual(task[10], "tok-b-0")
        self.assertEqual(self.seen["images"], 1)
        self.assertTrue(buffer.window(1, NOW)[1].done)  # это фото больше не читаем
        self.assertIn("📷", render_tasks(tasks_db.get_tasks(1))[0])
        self.assertIn("фото", build_source_text(task, db.get_message(1, "b")))

    def test_download_failure_falls_back_to_text(self):
        async def broken(url):
            raise RuntimeError("нет сети")

        photos.download_image = broken
        text, photo = entry("a", 2, "ДЗ на завтра"), entry("b", 0, images=1)
        self.go([text, photo], photo)
        self.assertEqual(self.seen["images"], 0)
        self.assertEqual(tasks_db.get_tasks(1)[0][7], "text")

    def test_nothing_found_stores_nothing(self):
        self.items = []
        e = entry("a", 0, "ДЗ на завтра")
        self.go([e], e)
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertIsNone(db.get_message(1, "a"))

    def test_vague_draft_is_replaced(self):
        now = int(time.time() * 1000)
        tasks_db.add_task(1, "a", "homework", "Без предмета", "ДЗ на завтра", None, now)
        tasks_db.add_task(1, "x", "homework", "Физика", "§5", None, now)  # чужая запись не трогается
        first, second = entry("a", 3, "ДЗ на завтра"), entry("b", 0, "алгебра №112")
        self.go([first, second], second)
        subjects = sorted(r[2] for r in tasks_db.get_tasks(1))
        self.assertEqual(subjects, ["Алгебра", "Физика"])

    def test_verified_draft_is_kept(self):
        now = int(time.time() * 1000)
        tasks_db.add_task(1, "a", "homework", "Без предмета", "ДЗ на завтра", None, now)
        tasks_db.verify_all(1)
        first, second = entry("a", 3, "ДЗ на завтра"), entry("b", 0, "алгебра №112")
        self.go([first, second], second)
        self.assertEqual(len(tasks_db.get_tasks(1)), 2)

    def test_ai_error_changes_nothing(self):
        async def boom(*a, **k):
            raise RuntimeError("ИИ недоступен")

        handlers.extract_with_context = boom
        photo = entry("a", 0, "дз на завтра", images=1)
        self.go([photo], photo)
        self.assertEqual(tasks_db.get_tasks(1), [])
        self.assertFalse(buffer.window(1, NOW)[0].done)  # фото не помечено прочитанным: можно повторить


class PhotoDemo(unittest.TestCase):
    def test_demo_has_photo_record(self):
        from app.demo import demo_rows, demo_source_text
        text, _ = render_tasks(demo_rows())
        self.assertIn("📷", text)
        self.assertIn("фото", demo_source_text(-5))


if __name__ == "__main__":
    unittest.main()


class ShrinkTests(unittest.TestCase):
    def test_big_photo_becomes_small_jpeg(self):
        import io

        from PIL import Image

        img = Image.effect_noise((3000, 2000), 80).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        small, mime = photos.shrink(data, "image/png")
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(small), len(data))
        self.assertLessEqual(max(Image.open(io.BytesIO(small)).size), 1280)

    def test_broken_bytes_returned_as_is(self):
        self.assertEqual(photos.shrink(b"not an image", "image/jpeg"), (b"not an image", "image/jpeg"))