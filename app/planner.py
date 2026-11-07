"""Решает, когда сообщение (или фото) стоит отправить на разбор и что взять вместе с ним.

Правила простые и проверяются тестами:
- текст с «подсказкой» (ДЗ, задали, №, стр. …) — разбираем вместе с ближайшими сообщениями и фото;
- короткий текст с цифрами без подсказки («№112 стр. 45») — разбираем, если в последние 10 минут
  было сообщение с подсказкой («ДЗ на завтра»): это его продолжение;
- фото — режим PHOTO_SCAN: hinted (по умолчанию) читаем, если рядом есть подсказка, all — всегда, off — никогда;
- фото без подсказки не пропадает: оно остаётся в памяти на 10 минут, и если следом придёт «ДЗ на завтра»,
  фото будет прочитано вместе с этим сообщением.
"""
import re
from dataclasses import dataclass

from app.extractor import looks_useful

MAX_IMAGES = 3  # не больше трёх фото за один разбор


@dataclass
class Plan:
    entries: list   # сообщения для разбора, от старых к новым
    anchor: object  # последнее сообщение: к нему привязывается запись
    images: list    # [(сообщение, {"url":…, "token":…})] — какие фото отправить модели


def _hint(entry) -> bool:
    return bool(entry.text) and looks_useful(entry.text)


def make_plan(window, entry, scan_mode: str = "hinted"):
    """window — свежие сообщения чата (включая entry). Возвращает Plan или None."""
    prior = [e for e in window if e is not entry]
    own_hint = _hint(entry)
    context_hint = any(_hint(e) for e in prior)

    if entry.images:
        if scan_mode == "off":
            go = own_hint or context_hint  # фото читать нельзя, но текст разобрать можно
        elif scan_mode == "all":
            go = True
        else:
            go = own_hint or context_hint
    else:
        go = own_hint or (
            context_hint and bool(re.search(r"\d", entry.text)) and 0 < len(entry.text) <= 200
        )
    if not go:
        return None

    images = []
    if scan_mode != "off":
        for e in window:
            if not e.done:
                images.extend((e, info) for info in e.images)
        images = images[-MAX_IMAGES:]
    return Plan(entries=list(window), anchor=entry, images=images)