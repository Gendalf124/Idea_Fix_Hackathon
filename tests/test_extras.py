"""Тесты новых функций: история за 7 дней, «Материалы», вечерняя сводка и напоминания."""
import asyncio
import time
import types
import unittest
from datetime import date, datetime, timedelta, timezone

from tests import _setup
from tests._setup import rows_of
from app import config, db, digest, extras_db, handlers, history, materials, menu, tasks_db

TZ = timezone(timedelta(hours=5))


class FakeBot:
    """Минимальный MAX: get_chat_members отвечает, кто сейчас состоит в чате (для проверки доступа)."""

    def __init__(self, ids):
        self.ids = ids

    async def get_chat_members(self, chat_id, marker=None, count=100, user_ids=None):
        found = [u for u in self.ids if user_ids is None or u in user_ids]
        members = [types.SimpleNamespace(user_id=u, is_bot=False, first_name="Имя", last_name="Ф") for u in found]
        return types.SimpleNamespace(members=members, marker=None)

    async def get_chat_by_id(self, chat_id):
        return types.SimpleNamespace(title="7Б класс")


def run(coro):
    return asyncio.run(coro)


def att(kind, token="tok", filename=None, url="https://x/f"):
    payload = types.SimpleNamespace(url=url, token=token)
    return types.SimpleNamespace(type=kind, payload=payload, filename=filename)


def msg(mid, ts, text="", attachments=(), user_id=7, is_bot=False):
    return types.SimpleNamespace(
        timestamp=ts,
        body=types.SimpleNamespace(mid=mid, text=text, attachments=list(attachments)),
        sender=types.SimpleNamespace(user_id=user_id, first_name="Аня", last_name="", is_bot=is_bot),
        recipient=types.SimpleNamespace(chat_id=1, chat_type="chat"),
    )


class Subjects(unittest.TestCase):
    def test_guess(self):
        self.assertEqual(materials.guess_subject("конспект по физике"), "Физика")
        self.assertEqual(materials.guess_subject("", "алгебра_кр_образец.pdf"), "Алгебра")
        self.assertEqual(materials.guess_subject("файл", "scan.pdf", "завтра биология"), "Биология")
        self.assertEqual(materials.guess_subject("вот", "scan.pdf"), "Без предмета")
        self.assertEqual(materials.guess_subject("геометрия и алгебра"), "Алгебра")  # порядок списка

    def test_material_words(self):
        self.assertTrue(materials.is_material_text("Конспект по истории"))
        self.assertFalse(materials.is_material_text("смотрите, как мы на перемене"))
        self.assertFalse(materials.is_material_text(""))


class MaterialsStorage(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.now = int(time.time() * 1000)

    def test_file_infos(self):
        m = msg("m1", self.now, attachments=[att("file", "t1", "Алгебра.pdf"), att("image"), att("file", "t2")])
        infos = materials.file_infos(m)
        self.assertEqual([i["title"] for i in infos], ["Алгебра.pdf", "Файл"])
        self.assertEqual(infos[0]["token"], "t1")

    def test_file_always_saved_photo_only_with_material_words(self):
        files = [{"title": "физика_лекция.pdf", "token": "t1"}]
        self.assertEqual(materials.save_from_message(1, "m1", self.now, "", files, []), 1)
        images = [{"url": "https://x/p", "token": "p1"}]
        self.assertEqual(materials.save_from_message(1, "m2", self.now, "мы на перемене", [], images), 0)
        self.assertEqual(materials.save_from_message(1, "m3", self.now, "конспект по химии", [], images), 1)
        rows = extras_db.get_materials(1)
        self.assertEqual(sorted((r[1], r[3]) for r in rows), [("Физика", "file"), ("Химия", "photo")])

    def test_same_file_not_duplicated(self):
        files = [{"title": "a.pdf", "token": "t1"}]
        materials.save_from_message(1, "m1", self.now, "физика", files, [])
        self.assertEqual(materials.save_from_message(1, "m1", self.now, "физика", files, []), 0)
        self.assertEqual(len(extras_db.get_materials(1)), 1)

    def test_homework_photo_goes_to_subject(self):
        materials.save_homework_photos(1, "m1", self.now, "Алгебра", ["p1", None])
        materials.save_homework_photos(1, "m2", self.now, "Без предмета", ["p2"], ["ДЗ по биологии"])
        self.assertEqual(sorted(r[1] for r in extras_db.get_materials(1)), ["Алгебра", "Биология"])

    def test_old_deleted_and_chat_removed(self):
        extras_db.add_material(1, "a", "Физика", "старый", "file", "t", self.now - 31 * 86400 * 1000)
        extras_db.add_material(1, "b", "Физика", "новый", "file", "t", self.now)
        self.assertEqual(extras_db.delete_old_materials(), 1)
        db.delete_chat_data(1)
        self.assertEqual(extras_db.get_materials(1), [])

    def test_chats_are_isolated(self):
        extras_db.add_material(1, "a", "Физика", "один", "file", "t", self.now)
        extras_db.add_material(2, "a", "Физика", "два", "file", "t", self.now)
        self.assertEqual([r[2] for r in extras_db.get_materials(1)], ["один"])

    def test_render_and_keyboard(self):
        extras_db.add_material(1, "a", "Физика", "лекция.pdf", "file", "t", self.now)
        extras_db.add_material(1, "b", "Алгебра", "Фото", "photo", "p", self.now)
        text, ordered = materials.render_materials(extras_db.get_materials(1))
        self.assertIn("📘 Алгебра", text)
        self.assertIn("📄 лекция.pdf", text)
        self.assertIn("📷 Фото", text)
        rows = rows_of(materials.build_keyboard(ordered, extra=menu.bottom_buttons_materials(1)))
        self.assertEqual(len(rows[0]), 2)
        self.assertTrue(rows[0][0].startswith("mf:"))
        self.assertEqual(rows[-1], ["sum:1"])
        self.assertIn("пока нет", materials.render_materials([])[0])

    def test_attachment_none_without_token(self):
        self.assertIsNone(materials.attachment_by_token("file", None))


class FakeHistoryBot:
    """get_messages как у MAX: сообщения в окне времени, новые первыми, не больше count."""

    def __init__(self, messages, fail=False):
        self.messages, self.fail, self.sent = messages, fail, []

    async def get_messages(self, chat_id, from_time, to_time, count):
        if self.fail:
            raise RuntimeError("нет прав")
        lo, hi = (int(t.timestamp() * 1000) if hasattr(t, "timestamp") else t for t in (from_time, to_time))
        inside = sorted((m for m in self.messages if lo <= m.timestamp <= hi), key=lambda m: -m.timestamp)
        return types.SimpleNamespace(messages=inside[:count])

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


HOUR = 3_600_000


class History(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.now = int(time.time() * 1000)

    def test_fetch_window_order_and_pagination(self):
        many = [msg(f"m{i}", self.now - i * 60_000, "t") for i in range(250)]
        old = msg("old", self.now - 8 * 24 * HOUR, "очень давно")
        bot = FakeHistoryBot(many + [old, many[0]])  # старое и дубль
        got = run(history.fetch_history(bot, 1, days=7, limit=300))
        self.assertEqual(len(got), 250)  # дубль и старое отброшены
        stamps = [m.timestamp for m in got]
        self.assertEqual(stamps, sorted(stamps))  # от старых к новым

    def test_limit(self):
        bot = FakeHistoryBot([msg(f"m{i}", self.now - i * 60_000, "t") for i in range(150)])
        self.assertEqual(len(run(history.fetch_history(bot, 1, days=7, limit=120))), 120)

    def test_int_time_fallback(self):
        class IntOnly(FakeHistoryBot):
            async def get_messages(self, chat_id, from_time, to_time, count):
                if hasattr(from_time, "timestamp"):
                    raise TypeError("нужно число")
                return await super().get_messages(chat_id, from_time, to_time, count)

        bot = IntOnly([msg("a", self.now - HOUR, "ДЗ")])
        self.assertEqual(len(run(history.fetch_history(bot, 1, days=7))), 1)

    def test_backfill_plans_materials_and_skips_noise(self):
        t = self.now - 5 * HOUR
        messages = [
            msg("a", t, "Ребята, ДЗ на завтра по алгебре"),
            msg("b", t + 60_000, "номер 112 стр 45"),
            msg("c", t + 120_000, "ахаха ок"),
            msg("d", t + 180_000, "", attachments=[att("file", "ft", "физика.pdf")]),
            msg("e", t + 240_000, "ДЗ по химии", user_id=9, is_bot=True),   # сообщение бота
            msg("f", t + 300_000, "/menu"),
        ]
        plans = []

        async def fake_process(chat_id, plan, ctx):
            plans.append((plan.anchor.mid, [e.mid for e in plan.entries]))

        stats = run(history.run_backfill(FakeHistoryBot(messages), 1, fake_process))
        self.assertEqual([p[0] for p in plans], ["a", "b"])
        self.assertIn("a", plans[1][1])  # продолжение собрано вместе с первым сообщением
        self.assertEqual(stats["materials"], 1)
        self.assertTrue(db.is_member(1, 7))  # автор сообщений точно был в чате
        self.assertFalse(db.is_member(1, 9))
        self.assertEqual(extras_db.get_materials(1)[0][1], "Физика")

    def test_plan_limit(self):
        old = config.HISTORY_MAX_PLANS
        config.HISTORY_MAX_PLANS = 2
        try:
            messages = [msg(f"m{i}", self.now - (10 - i) * 60_000, f"ДЗ на завтра номер {i}") for i in range(6)]
            calls = []

            async def fake_process(chat_id, plan, ctx):
                calls.append(plan.anchor.mid)

            stats = run(history.run_backfill(FakeHistoryBot(messages), 1, fake_process))
            self.assertEqual(len(calls), 2)
            self.assertEqual(stats["plans"], 2)
        finally:
            config.HISTORY_MAX_PLANS = old


class DoBackfill(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        self.saved = handlers.extract_with_context
        self.now = int(time.time() * 1000)
        handlers._backfill_last.clear()

        async def fake_extract(entries, images, day, ask, ask_vision):
            return [{"kind": "homework", "subject": "Алгебра", "text": "№112", "due": (day + timedelta(days=1)).isoformat()}]

        handlers.extract_with_context = fake_extract

    def tearDown(self):
        handlers.extract_with_context = self.saved

    def test_records_created_and_report_sent(self):
        bot = FakeHistoryBot([msg("a", self.now - HOUR, "ДЗ на завтра по алгебре")])
        run(handlers.do_backfill(bot, 1))
        self.assertEqual(len(tasks_db.get_tasks(1)), 1)
        self.assertIn("Прочитал историю", bot.sent[-1]["text"])
        self.assertIn("новых записей 1", bot.sent[-1]["text"])

    def test_error_gives_hint_and_does_not_crash(self):
        bot = FakeHistoryBot([], fail=True)
        run(handlers.do_backfill(bot, 1))
        self.assertIn("администратором", bot.sent[-1]["text"])

    def test_empty_history(self):
        bot = FakeHistoryBot([])
        run(handlers.do_backfill(bot, 1))
        self.assertIn("не нашёл", bot.sent[-1]["text"])

    def test_manual_repeat_is_limited(self):
        bot = FakeHistoryBot([msg("a", self.now - HOUR, "ДЗ на завтра по алгебре")])
        run(handlers.do_backfill(bot, 1, manual=True))
        run(handlers.do_backfill(bot, 1, manual=True))
        self.assertIn("уже читал", bot.sent[-1]["text"])
        self.assertEqual(len(tasks_db.get_tasks(1)), 1)


NOW = datetime(2026, 11, 5, 19, 5, tzinfo=TZ)
TODAY = NOW.date()


def hw(due, subject="Алгебра", text="№1", verified=0):
    """Запись в формате get_tasks."""
    return (1, "homework", subject, text, due.isoformat() if due else None, 0, verified, "text", None)


class DigestText(unittest.TestCase):
    def test_sections(self):
        tasks = [
            hw(TODAY + timedelta(days=1), "Алгебра", "№215", verified=1),
            hw(TODAY, "Физика", "§5"),
            hw(TODAY + timedelta(days=3), "Литература", "стих"),
            hw(None, "История", "читать"),
            hw(TODAY + timedelta(days=20), "Химия", "далеко"),
        ]
        text = digest.build_digest([("7Б", tasks)], TODAY, "evening", new_materials=2)
        self.assertIn("⏰ Сдавать завтра\n• Алгебра: №215 ✅", text)
        self.assertIn("🔥 Срок сегодня", text)
        self.assertIn("📅 Ближайшие дни\n• Литература: стих — до", text)
        self.assertIn("без срока: 1", text)
        self.assertIn("материалов за сутки: 2", text)
        self.assertNotIn("далеко", text)
        self.assertNotIn("(7Б)", text)  # один чат — название не нужно

    def test_chat_names_when_several(self):
        text = digest.build_digest(
            [("7Б", [hw(TODAY + timedelta(days=1))]), ("8А", [hw(TODAY + timedelta(days=1), "Физика")])], TODAY
        )
        self.assertIn("(7Б)", text)
        self.assertIn("(8А)", text)

    def test_nothing_to_say(self):
        self.assertIsNone(digest.build_digest([("7Б", [hw(None)])], TODAY))
        self.assertIsNone(digest.build_digest([("7Б", [])], TODAY))
        lesson = (1, "lesson", "Физика", "темы", None, 0, 0, "text", None)
        self.assertIsNone(digest.build_digest([("7Б", [lesson])], TODAY))

    def test_morning_only_today(self):
        tasks = [hw(TODAY, "Физика", "§5"), hw(TODAY + timedelta(days=1), "Алгебра")]
        text = digest.build_digest([("7Б", tasks)], TODAY, "morning")
        self.assertIn("Сегодня сдавать", text)
        self.assertIn("Физика", text)
        self.assertNotIn("Алгебра", text)
        self.assertIsNone(digest.build_digest([("7Б", [hw(TODAY + timedelta(days=1))])], TODAY, "morning"))


def at(hour, minute=0):
    return datetime(2026, 11, 5, hour, minute, tzinfo=TZ)


class Schedule(unittest.TestCase):
    sub = (1, 100, 1, 19, 1, None, None)  # user, dm, enabled, hour, morning, last_evening, last_morning

    def test_evening_window(self):
        self.assertEqual(digest.which_due(self.sub, at(18, 59)), [])
        self.assertEqual(digest.which_due(self.sub, at(19, 0)), ["evening"])
        self.assertEqual(digest.which_due(self.sub, at(21, 59)), ["evening"])  # бот был выключен — догоняем
        self.assertEqual(digest.which_due(self.sub, at(22, 0)), [])

    def test_morning_window(self):
        self.assertEqual(digest.which_due(self.sub, at(7, 29)), [])
        self.assertEqual(digest.which_due(self.sub, at(7, 30)), ["morning"])
        self.assertEqual(digest.which_due(self.sub, at(12, 0)), [])

    def test_once_a_day_and_switches(self):
        done = (1, 100, 1, 19, 1, "2026-11-05", "2026-11-05")
        self.assertEqual(digest.which_due(done, at(19, 30)), [])
        self.assertEqual(digest.which_due((1, 100, 0, 19, 1, None, None), at(19, 0)), [])
        no_morning = (1, 100, 1, 19, 0, None, None)
        self.assertEqual(digest.which_due(no_morning, at(8, 0)), [])
        self.assertEqual(digest.which_due((1, 100, 1, 20, 1, None, None), at(19, 30)), [])


class SenderBot(FakeBot):
    def __init__(self, ids):
        super().__init__(ids)
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class DigestSending(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        db.touch_member(100, 1)
        db.set_title(100, "7Б")
        tasks_db.add_task(100, "m", "homework", "Алгебра", "№215", "2026-11-06", 1)
        extras_db.set_sub(1, 555)

    def test_sent_once_with_buttons(self):
        bot = SenderBot([1])
        run(digest.run_once(bot, at(19, 5)))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["chat_id"], 555)
        self.assertIn("Алгебра: №215", bot.sent[0]["text"])
        self.assertIn("menu:summary", sum(rows_of(bot.sent[0]["attachments"][0]), []))
        run(digest.run_once(bot, at(19, 6)))
        self.assertEqual(len(bot.sent), 1)  # второй раз за день не шлём

    def test_nothing_sent_without_tasks_but_marked(self):
        tasks_db.delete_task(tasks_db.get_tasks(100)[0][0])
        bot = SenderBot([1])
        run(digest.run_once(bot, at(19, 5)))
        self.assertEqual(bot.sent, [])
        self.assertEqual(extras_db.get_sub(1)[5], "2026-11-05")

    def test_person_who_left_gets_nothing(self):
        bot = SenderBot([2, 3])  # человека уже нет в чате
        run(digest.run_once(bot, at(19, 5)))
        self.assertEqual(bot.sent, [])

    def test_send_error_does_not_repeat_every_minute(self):
        class Broken(SenderBot):
            async def send_message(self, **kwargs):
                raise RuntimeError("MAX недоступен")

        bot = Broken([1])
        run(digest.run_once(bot, at(19, 5)))
        self.assertEqual(extras_db.get_sub(1)[5], "2026-11-05")

    def test_morning_reminder(self):
        tasks_db.add_task(100, "m2", "homework", "Физика", "§5", "2026-11-05", 1)
        bot = SenderBot([1])
        run(digest.run_once(bot, at(7, 40)))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Сегодня сдавать", bot.sent[0]["text"])
        self.assertIn("Физика", bot.sent[0]["text"])


class Subscriptions(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()

    def test_toggle_and_hour(self):
        self.assertIsNone(extras_db.get_sub(1))
        extras_db.set_sub(1, 555, enabled=1)
        extras_db.set_sub(1, 555, hour=20, morning=0)
        sub = extras_db.get_sub(1)
        self.assertEqual((sub[1], sub[2], sub[3], sub[4]), (555, 1, 20, 0))
        extras_db.set_sub(1, 555, enabled=0)
        self.assertEqual(extras_db.all_enabled_subs(), [])

    def test_menu_and_status(self):
        off = [b for row in rows_of(menu.notify_menu(None)) for b in row]
        self.assertIn("nt:on", off)
        self.assertNotIn("nt:off", off)
        extras_db.set_sub(1, 555, enabled=1, hour=20)
        sub = extras_db.get_sub(1)
        on = [b for row in rows_of(menu.notify_menu(sub)) for b in row]
        self.assertIn("nt:off", on)
        self.assertIn("nt:now", on)
        self.assertIn("nt:h:21", on)
        self.assertIn("включены", handlers.notify_status(sub))
        self.assertIn("20:00", handlers.notify_status(sub))

    def test_menus_have_new_buttons(self):
        main = [b for row in rows_of(menu.main_menu()) for b in row]
        self.assertIn("menu:mat", main)
        self.assertIn("menu:notify", main)
        group = [b for row in rows_of(menu.group_menu(5)) for b in row]
        self.assertIn("mat:5", group)
        self.assertIn("hist:5", group)
        picker = [b for row in rows_of(menu.chat_picker([(5, "7Б")], prefix="mat")) for b in row]
        self.assertEqual(picker[0], "mat:5")
        self.assertNotIn("menu:all", picker)


class NaturalPhrasing(unittest.TestCase):
    def test_tasks_without_the_word_dz(self):
        from app.extractor import looks_useful
        for text in ("Алгебра до завтра сделать куб из бумаги", "Алгебра номера", "Биология 102 конспект",
                     "Завтра принести линейку и циркуль"):
            self.assertTrue(looks_useful(text), text)
        for text in ("кто идёт на физру?", "Завтра в кино пойдём?", "ахахах 😂😂😂"):
            self.assertFalse(looks_useful(text), text)

    def test_such_message_is_processed_at_once(self):
        from app.context import Entry
        from app.planner import make_plan
        e = Entry(mid="a", ts=1_800_000_000_000, author="Аня", text="Алгебра до завтра сделать куб из бумаги")
        self.assertIsNotNone(make_plan([e], e, "hinted"))  # без ожидания следующих сообщений


class SourceButtons(unittest.TestCase):
    def flat(self, kb):
        return [b for row in rows_of(kb) for b in row]

    def test_student_in_dm(self):
        rows = rows_of(menu.source_buttons(5, 9))
        self.assertEqual(rows, [["sum:5", "menu:home"]])

    def test_teacher_gets_confirm_and_back(self):
        rows = rows_of(menu.source_buttons(5, 9, teacher=True))
        self.assertEqual(rows[0], ["ok:9", "del:9"])
        self.assertEqual(rows[1], ["sum:5", "menu:home"])

    def test_group_has_no_home(self):
        self.assertEqual(rows_of(menu.source_buttons(5, 9, in_group=True)), [["sum:5"]])

    def test_demo_goes_back_to_demo(self):
        self.assertEqual(rows_of(menu.source_buttons(0, demo=True)), [["menu:demo", "menu:home"]])


if __name__ == "__main__":
    unittest.main()