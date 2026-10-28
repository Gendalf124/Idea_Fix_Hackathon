import asyncio
import logging
import time
from datetime import datetime

from maxapi import F, Router
from maxapi.filters.command import Command, CommandStart
from maxapi.types import BotAdded, BotStarted, MessageCallback, MessageCreated
from openai import AsyncOpenAI

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from app.db import get_message, get_title, is_member, save_message, set_title, touch_member, user_chats
from app.extractor import extract_tasks, looks_useful
from app.menu import (back_menu, bottom_buttons_dm, bottom_buttons_group, chat_picker,
                      group_menu, main_menu)
from app.render import build_source_keyboard, build_source_text, render_tasks
from app.sync import ensure_synced, sync_members
from app.tasks_db import TZ, add_task, delete_expired, get_task, get_tasks

router = Router()

client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


# ============ 1. Связь с ИИ ============

async def ask_gpt(prompt: str, system: str | None = None) -> str:
    """Отправляет вопрос ИИ и возвращает текст ответа."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(model=AI_MODEL, messages=messages)
    return response.choices[0].message.content or "Модель не вернула ответ."


# ============ 2. Мелкие помощники ============

def is_group(message) -> bool:
    """True — сообщение из группового чата, False — личная переписка с ботом."""
    chat_type = getattr(message.recipient, "chat_type", "")
    chat_type = str(getattr(chat_type, "value", chat_type)).lower()
    return chat_type == "chat"


def sender_info(message):
    """Возвращает (id пользователя, его имя)."""
    sender = message.sender
    user_id = getattr(sender, "user_id", None)
    name = " ".join(
        x for x in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if x
    )
    return user_id, name


_background = set()


def run_in_background(coro):
    """Запускает долгое дело (запрос к ИИ) и не заставляет бота ждать его."""
    task = asyncio.create_task(coro)
    _background.add(task)  # держим ссылку, иначе Python может "забыть" задачу
    task.add_done_callback(_background.discard)


# ============ 3. Разбор нового сообщения: найти задание и записать ============

async def process_message(chat_id: int, mid: str, text: str, ts: int):
    """Спрашивает ИИ, есть ли в сообщении ДЗ или тема урока, и сохраняет найденное."""
    sent_day = datetime.fromtimestamp(ts / 1000, tz=TZ).date()
    try:
        items = await extract_tasks(text, sent_day, ask_gpt)
    except Exception:
        logging.exception("Не удалось разобрать сообщение")
        return
    for item in items:
        add_task(chat_id, mid, item["kind"], item["subject"], item["text"], item["due"], ts)
    if items:
        logging.info("Чат %s: найдено записей %d", chat_id, len(items))


# ============ 4. Показ сводки ============

async def send_summary(message, chat_id: int, in_group: bool, title: str = ""):
    """Показывает актуальные записи ОДНОГО чата. Другие чаты сюда не попадают."""
    delete_expired()  # сначала убираем устаревшее
    tasks = get_tasks(chat_id)
    head = f"━━ {title} ━━\n" if title else ""
    bottom = bottom_buttons_group(chat_id) if in_group else bottom_buttons_dm()
    if not tasks:
        await message.answer(
            head + "Пока записей нет.\nКак только в чате появится задание, я его запишу.",
            attachments=[build_source_keyboard([], extra=bottom)],
        )
        return
    text, ordered = render_tasks(tasks)
    await message.answer(head + text, attachments=[build_source_keyboard(ordered, extra=bottom)])


async def show_my_chats(message, user_id: int):
    """Личка: если чат один — сразу сводка, если несколько — выбор."""
    chats = user_chats(user_id)
    if not chats:
        await message.answer(
            "Я пока не знаю твоих чатов 🤔\n"
            "Добавь меня в чат класса, сделай администратором и нажми там «🔄 Обновить участников».",
            attachments=[back_menu()],
        )
    elif len(chats) == 1:
        await send_summary(message, chats[0][0], in_group=False, title=chats[0][1])
    else:
        await message.answer("Какой класс показать?", attachments=[chat_picker(chats)])


async def show_all_chats(message, user_id: int):
    """Личка: сводки всех чатов подряд, каждая со своим заголовком."""
    chats = user_chats(user_id)
    if not chats:
        await show_my_chats(message, user_id)
        return
    for chat_id, label in chats[:8]:
        await send_summary(message, chat_id, in_group=False, title=label)


WELCOME = (
    "Привет! Я Сферум.Сводка 💙\n"
    "Читаю чаты класса и собираю главное: что проходили, домашку и дедлайны.\n\n"
    "Выбери, что показать 👇"
)

HELP = (
    "Как подключить класс:\n"
    "1. Добавь меня в чат класса.\n"
    "2. Сделай меня администратором — иначе я не вижу сообщения.\n"
    "3. Нажми в чате «🔄 Обновить участников».\n\n"
    "Потом любой ученик класса может открыть меня здесь и нажать «📋 Моя сводка».\n"
    "У каждой строки домашки есть кнопка 🔎 — она покажет исходное сообщение."
)


# ============ 5. События и кнопки ============

@router.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(chat_id=event.chat_id, text=WELCOME, attachments=[main_menu()])


@router.bot_added()
async def bot_added(event: BotAdded):
    """Бота добавили в чат: сразу пробуем подтянуть всех участников."""
    chat_id = event.chat_id
    try:
        count = await sync_members(event.bot, chat_id)
        title = get_title(chat_id)
        text = (
            f"Привет! Я Сферум.Сводка 💙\nНашёл участников: {count}."
            + (f"\nНазвание чата: «{title}». Если нужно другое — кнопка ✏️." if title else "")
        )
    except Exception:
        logging.exception("Не удалось получить участников")
        text = (
            "Привет! Я Сферум.Сводка 💙\n"
            "Сделайте меня администратором, а потом нажмите «🔄 Обновить участников»."
        )
    await event.bot.send_message(chat_id=chat_id, text=text, attachments=[group_menu(chat_id)])


@router.message_created(CommandStart())
async def start(event: MessageCreated):
    if is_group(event.message):
        await event.message.answer("Выбери действие 👇", attachments=[group_menu(event.message.recipient.chat_id)])
    else:
        await event.message.answer(WELCOME, attachments=[main_menu()])


@router.message_created(Command("menu"))
async def menu_command(event: MessageCreated):
    await start(event)


@router.message_created(Command("summary"))
async def summary_command(event: MessageCreated):
    """Команда осталась как запасной вариант, основной путь — кнопки."""
    message = event.message
    user_id, name = sender_info(message)
    if is_group(message):
        chat_id = message.recipient.chat_id
        touch_member(chat_id, user_id, name)
        await ensure_synced(event.bot, chat_id)
        await send_summary(message, chat_id, in_group=True)
    else:
        await show_my_chats(message, user_id)


@router.message_created(Command("scan"))
async def scan_command(event: MessageCreated):
    message = event.message
    if not is_group(message):
        await message.answer("Это работает в чате класса.")
        return
    await do_scan(event.bot, message, message.recipient.chat_id)


@router.message_created(Command("class"))
async def set_class(event: MessageCreated):
    """Название чата: /class 7Б (чтобы в личке было «7Б», а не «Чат 123»)."""
    message = event.message
    if not is_group(message):
        await message.answer("Название задаётся в чате класса: /class 7Б")
        return
    title = (message.body.text or "").replace("/class", "", 1).strip()
    if not title:
        await message.answer("Напиши название после команды, например: /class 7Б")
        return
    user_id, name = sender_info(message)
    touch_member(message.recipient.chat_id, user_id, name)
    set_title(message.recipient.chat_id, title)
    await message.answer(f"Готово, этот чат называется «{title[:60]}».")


async def do_scan(bot, message, chat_id: int):
    """Подтягивает участников и сообщает результат."""
    try:
        count = await sync_members(bot, chat_id)
        await message.answer(f"Готово! Участников в классе: {count} ✅", attachments=[group_menu(chat_id)])
    except Exception:
        logging.exception("Не удалось получить участников")
        await message.answer("Не получилось 😕 Сделайте меня администратором чата и нажмите кнопку ещё раз.")


pending_title = {}  # (chat_id, user_id) -> когда нажали ✏️. Ждём от этого человека название.
TITLE_WAIT_S = 300  # ждём 5 минут


def callback_user(event):
    """(id, имя) того, кто нажал кнопку."""
    user = event.callback.user
    name = " ".join(x for x in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if x)
    return getattr(user, "user_id", None), name


@router.message_callback(F.callback.payload.startswith("menu:"))
async def menu_buttons(event: MessageCallback):
    action = event.callback.payload.split(":")[1]
    user_id, _name = callback_user(event)
    await event.answer(notification="Секунду…")
    if action == "summary":
        await show_my_chats(event.message, user_id)
    elif action == "all":
        await show_all_chats(event.message, user_id)
    elif action == "help":
        await event.message.answer(HELP, attachments=[back_menu()])
    else:  # home
        await event.message.answer(WELCOME, attachments=[main_menu()])


@router.message_callback(F.callback.payload.startswith("sum:"))
async def summary_button(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, name = callback_user(event)
    in_group = is_group(event.message)
    if in_group:  # нажал в самом чате — значит он там точно есть
        touch_member(chat_id, user_id, name)
    if not is_member(chat_id, user_id):  # чужие классы не показываем
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Показываю сводку")
    if in_group:
        await ensure_synced(event.bot, chat_id)
    await send_summary(event.message, chat_id, in_group=in_group)


@router.message_callback(F.callback.payload.startswith("title:"))
async def title_button(event: MessageCallback):
    """Кнопка ✏️: следующее сообщение этого человека станет названием чата."""
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, name = callback_user(event)
    touch_member(chat_id, user_id, name)
    pending_title[(chat_id, user_id)] = time.time()
    await event.answer(notification="Жду название")
    await event.message.answer("Напиши название следующим сообщением, например: 7Б")


@router.message_callback(F.callback.payload.startswith("scan:"))
async def scan_button(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    await event.answer(notification="Обновляю список…")
    await do_scan(event.bot, event.message, chat_id)


@router.message_callback(F.callback.payload.startswith("src:"))
async def show_source(event: MessageCallback):
    """Кнопка 🔎: показывает исходное сообщение, из которого взята запись."""
    task_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    task = get_task(task_id)
    if task is None:
        await event.answer(notification="Запись уже устарела и удалена")
        return
    chat_id, mid = task[0], task[1]
    if not is_member(chat_id, user_id):
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Ищу источник")
    await event.message.answer(build_source_text(task, get_message(chat_id, mid)))


# ============ 6. Каждое новое сообщение чата ============
# ВАЖНО: этот обработчик должен стоять ПОСЛЕДНИМ — он ловит всё, что не команда.

@router.message_created()
async def collect(event: MessageCreated):
    message = event.message
    if not is_group(message):  # личные сообщения боту не сохраняем
        return
    text = (message.body.text or "").strip()
    if not text or text.startswith("/"):
        return
    if getattr(message.sender, "is_bot", False):
        return

    user_id, name = sender_info(message)
    chat_id = message.recipient.chat_id

    # Человек нажал ✏️ и теперь пишет название чата.
    asked_at = pending_title.pop((chat_id, user_id), None)
    if asked_at and time.time() - asked_at < TITLE_WAIT_S:
        set_title(chat_id, text)
        await message.answer(f"Готово, чат называется «{text[:60]}» ✅")
        return

    ts = message.timestamp or int(time.time() * 1000)
    mid = message.body.mid or f"{chat_id}:{ts}"

    touch_member(chat_id, user_id, name)  # запомнили участника
    save_message(chat_id, mid, name, ts, text)  # сохранили оригинал (пригодится для «источника»)
    if looks_useful(text):  # похоже на задание или тему — отдаём ИИ
        run_in_background(process_message(chat_id, mid, text, ts))