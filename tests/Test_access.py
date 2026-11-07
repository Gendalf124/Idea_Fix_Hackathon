import asyncio
import sqlite3
import types
import unittest
from contextlib import closing

from tests import _setup
from app import db, sync


def run(coro):
    return asyncio.run(coro)


def member(uid, is_bot=False):
    return types.SimpleNamespace(user_id=uid, is_bot=is_bot, first_name="Имя", last_name="Ф")


class FakeBot:
    """Умеет отдавать участников страницами и фильтровать по user_ids, как MAX."""
    def __init__(self, ids, fail_on_page=None, honor_filter=True, broken=False):
        self.ids, self.fail_on_page, self.honor_filter, self.broken = ids, fail_on_page, honor_filter, broken
        self.pages = 0

    async def get_chat_members(self, chat_id, marker=None, count=100, user_ids=None):
        if self.broken:
            raise RuntimeError("403")
        if user_ids is not None:
            found = [member(u) for u in self.ids if u in user_ids] if self.honor_filter else [member(u) for u in self.ids]
            return types.SimpleNamespace(members=found, marker=None)
        self.pages += 1
        if self.fail_on_page == self.pages:
            raise RuntimeError("сбой на странице")
        start = int(marker or 0)
        chunk = self.ids[start:start + 2]  # по 2 человека на страницу
        nxt = str(start + 2) if start + 2 < len(self.ids) else None
        return types.SimpleNamespace(members=[member(u) for u in chunk] + ([member(999, True)] if start == 0 else []), marker=nxt)

    async def get_chat_by_id(self, chat_id):
        return types.SimpleNamespace(title="7Б класс")


def age_member(chat_id, user_id, hours):
    with closing(sqlite3.connect(_setup.DB_FILE)) as con:
        with con:
            con.execute("UPDATE members SET checked_ts = checked_ts - ? WHERE chat_id=? AND user_id=?",
                        (hours * 3600 * 1000, chat_id, user_id))


class Sync(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()

    def test_all_pages_and_bots_skipped(self):
        n = run(sync.sync_members(FakeBot([1, 2, 3, 4, 5]), 100))
        self.assertEqual(n, 5)
        self.assertTrue(all(db.is_member(100, u) for u in range(1, 6)))
        self.assertFalse(db.is_member(100, 999))
        self.assertEqual(db.get_title(100), "7Б класс")

    def test_left_member_is_removed(self):
        run(sync.sync_members(FakeBot([1, 2, 3]), 100))
        run(sync.sync_members(FakeBot([1, 3]), 100))
        self.assertFalse(db.is_member(100, 2))
        self.assertTrue(db.is_member(100, 1))

    def test_failed_sync_removes_nobody(self):
        run(sync.sync_members(FakeBot([1, 2, 3, 4]), 100))
        with self.assertRaises(RuntimeError):
            run(sync.sync_members(FakeBot([1, 2, 3, 4, 5], fail_on_page=2), 100))
        self.assertTrue(all(db.is_member(100, u) for u in (1, 2, 3, 4)))

    def test_manual_title_wins(self):
        db.set_title(100, "Мой 7Б")
        run(sync.sync_members(FakeBot([1]), 100))
        self.assertEqual(db.get_title(100), "Мой 7Б")


class Access(unittest.TestCase):
    def setUp(self):
        _setup.reset_db()
        db.touch_member(100, 1)

    def test_present_in_chat(self):
        self.assertTrue(run(sync.has_access(FakeBot([1, 2]), 100, 1)))

    def test_left_chat_loses_access_immediately(self):
        self.assertFalse(run(sync.has_access(FakeBot([2, 3]), 100, 1)))
        self.assertFalse(db.is_member(100, 1))  # и из базы убран

    def test_other_class_is_closed(self):
        self.assertFalse(run(sync.has_access(FakeBot([1]), 200, 5)))
        self.assertEqual(db.user_chats(5), [])

    def test_api_error_uses_fresh_cache_only(self):
        broken = FakeBot([], broken=True)
        self.assertTrue(run(sync.has_access(broken, 100, 1)))  # запись свежая
        age_member(100, 1, hours=7)
        self.assertFalse(run(sync.has_access(broken, 100, 1)))  # запись устарела — отказ

    def test_filter_ignored_falls_back_to_cache(self):
        bot = FakeBot([2, 3], honor_filter=False)  # MAX вернул чужих
        self.assertTrue(run(sync.has_access(bot, 100, 1)))  # свежая запись в базе
        age_member(100, 1, hours=7)
        self.assertFalse(run(sync.has_access(bot, 100, 1)))

    def test_no_user(self):
        self.assertFalse(run(sync.has_access(FakeBot([1]), 100, None)))


if __name__ == "__main__":
    unittest.main()