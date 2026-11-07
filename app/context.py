"""Память о последних сообщениях чата (только в оперативной памяти, на 10 минут).

Зачем: задание часто разбито на части — в одном сообщении «ДЗ на завтра», в другом номер упражнения
или фото страницы. Чтобы собрать их в одну запись, боту нужно помнить ближайшие сообщения.
В базу эта память НЕ попадает и пропадает при перезапуске; хранится не дольше TTL_S секунд.
"""
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

TTL_S = int(os.getenv("CONTEXT_TTL_S", "600") or 600)  # помним 10 минут (меняется в .env)
MAX_ITEMS = 4   # и не больше 4 последних сообщений на чат


@dataclass
class Entry:
    mid: str
    ts: int                       # время сообщения, мс
    author: str
    text: str = ""
    images: list = field(default_factory=list)  # [{"url": ..., "token": ...}]
    done: bool = False            # фото из этого сообщения уже прочитано


class ChatContext:
    def __init__(self, ttl_s: int = TTL_S, max_items: int = MAX_ITEMS):
        self.ttl_ms = ttl_s * 1000
        self.max_items = max_items
        self._items = defaultdict(lambda: deque(maxlen=max_items))  # chat_id -> сообщения

    def push(self, chat_id: int, entry: Entry):
        self._items[chat_id].append(entry)

    def window(self, chat_id: int, now_ms: int = None):
        """Свежие сообщения чата от старых к новым. Устаревшие сразу забываются."""
        now_ms = now_ms or int(time.time() * 1000)
        items = self._items.get(chat_id)
        if not items:
            return []
        fresh = [e for e in items if now_ms - e.ts <= self.ttl_ms]
        if len(fresh) != len(items):
            self._items[chat_id] = deque(fresh, maxlen=self.max_items)
        if not fresh:
            self._items.pop(chat_id, None)
        return fresh

    def mark_done(self, chat_id: int, mids):
        mids = set(mids)
        for e in self._items.get(chat_id, ()):
            if e.mid in mids:
                e.done = True

    def forget_chat(self, chat_id: int):
        self._items.pop(chat_id, None)


buffer = ChatContext()