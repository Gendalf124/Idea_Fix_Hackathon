import asyncio
import logging
import time
from datetime import datetime

from maxapi import F, Router
from maxapi.filters.command import Command, CommandStart
from maxapi.types import BotStarted, CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from app.db import is_member, save_message, set_title, touch_member, user_chats
from app.extractor import extract_tasks, looks_useful
from app.render import render_tasks
from app.tasks_db import TZ, add_task, delete_expired, get_tasks

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
    _background.add(task)
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

async def send_summary(message, chat_id: int):
    """Показывает актуальные записи ОДНОГО чата. Другие чаты сюда не попадают."""
    delete_expired() 
    tasks = get_tasks(chat_id)
    if not tasks:
        await message.answer(
            "Пока записей нет.\nПроверь, что я администратор чата, и напиши в нём, например: "
            "«Алгебра: №215 до среды»."
        )
        return
    await message.answer(render_tasks(tasks))


# ============ 5. Команды ============

@router.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text="Привет! Я бот для MAX 💙\nНапиши /start, чтобы узнать как рабоать со мной.",
    )


@router.message_created(CommandStart())
async def hello(event: MessageCreated):
    await event.message.answer(
        "Привет! Я Сферум.Сводка 💙\n"
        "Как подключить класс: добавь меня в чат класса и сделай администратором.\n\n"
        "/summary — актуальные задания и темы уроков\n"
        "/class 7Б — назвать этот чат (в чате класса)\n"
        "/gpt вопрос — спросить ИИ\n\n"
        "В личке я показываю сводки только тех чатов, где ты писал(а)."
    )

"""
@router.message_created(Command("gpt"))
async def gpt(event: MessageCreated):
    text = event.message.body.text or ""
    prompt = text.replace("/gpt", "", 1).strip()

    if not prompt:
        await event.message.answer(
            "Напиши вопрос после команды /gpt.\nНапример: /gpt Почему небо голубое?"
        )
        return

    try:
        await event.message.answer(await ask_gpt(prompt))
    except Exception:
        logging.exception("Ошибка при запросе к модели")
        await event.message.answer("Не удалось получить ответ от ИИ. Попробуй позже.")
"""

@router.message_created(Command("class"))
async def set_class(event: MessageCreated):
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


@router.message_created(Command("summary"))
async def summary(event: MessageCreated):
    message = event.message
    user_id, name = sender_info(message)


    if is_group(message):
        chat_id = message.recipient.chat_id
        touch_member(chat_id, user_id, name)
        await send_summary(message, chat_id)
        return

    chats = user_chats(user_id)
    if not chats:
        await message.answer(
            "Я пока не знаю твоих чатов.\n"
            "Напиши что-нибудь в чате класса, где я добавлен, и повтори /summary."
        )
    elif len(chats) == 1:
        await send_summary(message, chats[0][0])
    else:
        builder = InlineKeyboardBuilder()
        for chat_id, label in chats[:8]:
            builder.row(CallbackButton(text=label, payload=f"sum:{chat_id}"))
        await message.answer("Какой класс показать?", attachments=[builder.as_markup()])


@router.message_callback(F.callback.payload.startswith("sum:"))
async def choose_chat(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    user_id = getattr(event.callback.user, "user_id", None)

    # Проверка доступа: нельзя смотреть сводку чужого класса.
    if not is_member(chat_id, user_id):
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Показываю сводку")
    await send_summary(event.message, chat_id)


# ============ 6. Каждое новое сообщение чата ============
@router.message_created()
async def collect(event: MessageCreated):
    message = event.message
    if not is_group(message):
        return
    
    text = (message.body.text or "").strip()

    if not text or text.startswith("/"):
        return
    
    if getattr(message.sender, "is_bot", False):
        return

    user_id, name = sender_info(message)
    chat_id = message.recipient.chat_id
    ts = message.timestamp or int(time.time() * 1000)
    mid = message.body.mid or f"{chat_id}:{ts}"

    touch_member(chat_id, user_id, name)  # запомнили участника
    save_message(chat_id, mid, name, ts, text)  # сохранили оригинал (пригодится для «источника»)
    if looks_useful(text):  
        run_in_background(process_message(chat_id, mid, text, ts))