"""Роль учителя: человек вводит боту в личке код учителя и получает право подтверждать записи.

Код задаётся в .env (TEACHER_CODE) и выдаётся учителям администрацией школы или командой проекта.
Попытки ограничены: после 5 неверных вводов — пауза на 15 минут.
"""
import hmac
import time

from app import config
from app.db import add_teacher

WAIT_S = 300      # сколько ждём ввод кода после нажатия кнопки
MAX_TRIES = 5     # неверных попыток подряд
LOCK_S = 900      # пауза после превышения

_waiting = {}  # user_id -> когда нажал «Я учитель»
_fails = {}    # user_id -> времена неверных попыток


def start_waiting(user_id) -> str:
    """Человек нажал «Я учитель». Возвращает "disabled" (код не настроен) или "waiting"."""
    if not config.TEACHER_CODE:
        return "disabled"
    _waiting[user_id] = time.time()
    return "waiting"


def is_waiting(user_id) -> bool:
    started = _waiting.get(user_id)
    if started is None:
        return False
    if time.time() - started > WAIT_S:
        _waiting.pop(user_id, None)
        return False
    return True


def try_code(user_id, text: str) -> str:
    """Проверяет введённый код. Возвращает: ok | wrong | locked | disabled."""
    if not config.TEACHER_CODE:
        return "disabled"
    now = time.time()
    fails = [t for t in _fails.get(user_id, []) if now - t < LOCK_S]
    _fails[user_id] = fails
    if len(fails) >= MAX_TRIES:
        return "locked"
    if hmac.compare_digest(text.strip().encode(), config.TEACHER_CODE.encode()):
        add_teacher(user_id)
        _waiting.pop(user_id, None)
        _fails.pop(user_id, None)
        return "ok"
    fails.append(now)
    return "locked" if len(fails) >= MAX_TRIES else "wrong"