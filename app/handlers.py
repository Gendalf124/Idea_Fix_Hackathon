import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime

from maxapi import F, Router
from maxapi.filters.command import Command, CommandStart
from maxapi.types import BotAdded, BotStarted, MessageCallback, MessageCreated

from app import config, digest, history, materials, photos, teacher, voice
from app.ai import ask_gpt, ask_vision
from app.context import Entry, buffer
from app.db import (
    delete_chat_data, get_message, get_title, remove_member, save_message,
    set_title, touch_member, user_chats,
)
from app.extras_db import get_material, get_materials, get_sub, set_sub
from app.demo import DEMO_TITLE, demo_rows, demo_source_text
from app.extractor import extract_tasks, extract_with_context, looks_useful
from app.menu import (
    back_menu, bottom_buttons_dm, bottom_buttons_group, bottom_buttons_materials, chat_picker, group_menu,
    main_menu, notify_menu, source_buttons, teacher_actions,
)
from app.planner import make_plan
from app.render import build_source_keyboard, build_source_text, render_tasks
from app.sync import can_verify, ensure_synced, has_access, sync_members
from app.tasks_db import (
    TZ, add_task, delete_expired, delete_task, delete_tasks_for_message, delete_vague_for, get_task,
    get_tasks, verify_all, verify_task,
)

router = Router()


# ============ 1. Мелкие помощники ============

def is_group(message) -> bool:
    """True — сообщение из группового чата, False — личная переписка с ботом."""
    chat_type = getattr(message.recipient, "chat_type", "")
    chat_type = str(getattr(chat_type, "value", chat_type)).lower()
    return chat_type == "chat"


def sender_info(message):
    """Возвращает (id пользователя, его имя). Имя нужно только для подписи источника."""
    sender = message.sender
    user_id = getattr(sender, "user_id", None)
    name = " ".join(
        x for x in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if x
    )
    return user_id, name


def callback_user(event):
    """(id, имя) того, кто нажал кнопку."""
    user = event.callback.user
    name = " ".join(x for x in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if x)
    return getattr(user, "user_id", None), name


_background = set()


def run_in_background(coro):
    """Запускает долгое дело (запрос к ИИ) и не заставляет бота ждать его."""
    task = asyncio.create_task(coro)
    _background.add(task)  # держим ссылку, иначе Python может «забыть» задачу
    task.add_done_callback(_background.discard)


_ai_slots = asyncio.Semaphore(3)  # не больше 3 запросов к ИИ одновременно


# ============ 2. Разбор сообщения: найти задание и записать ============

async def process_message(chat_id: int, mid: str, text: str, ts: int, author: str,
                          src_kind: str = "text", conf=None):
    """Спрашивает ИИ, есть ли в сообщении ДЗ или тема урока, и сохраняет найденное.

    Оригинал сообщения сохраняется ТОЛЬКО если из него получилась запись.
    Если сообщение отредактировали — старые записи из него заменяются новыми.
    src_kind="voice": текст — это расшифровка голосового, conf — уверенность распознавания.
    """
    sent_day = datetime.fromtimestamp(ts / 1000, tz=TZ).date()
    try:
        async with _ai_slots:
            items = await extract_tasks(text, sent_day, ask_gpt)
    except Exception:
        logging.exception("Не удалось разобрать сообщение")
        return  # старые записи не трогаем

    delete_tasks_for_message(chat_id, mid)
    if not items:
        return
    save_message(chat_id, mid, author, ts, text)
    for item in items:
        add_task(chat_id, mid, item["kind"], item["subject"], item["text"], item["due"], ts,
                 src_kind=src_kind, conf=conf)
    logging.info("Чат %s: найдено записей %d", chat_id, len(items))


async def process_plan(chat_id: int, plan, ctx=None):
    """Разбирает сообщение вместе с ближайшими сообщениями и фото.

    Фото скачиваются в память, отправляются модели и забываются; в записи остаётся только токен MAX.
    """
    entries, anchor = plan.entries, plan.anchor
    downloaded = []  # [(info, (байты, mime))]
    for _entry, info in plan.images:
        try:
            downloaded.append((info, await photos.download_image(info.get("url"))))
        except Exception as exc:
            logging.warning("Фото не скачалось (%s)", exc)
    sent_ids = {id(info) for info, _ in downloaded}
    view = [replace(e, images=[i for i in e.images if id(i) in sent_ids]) for e in entries]
    sent_day = datetime.fromtimestamp(anchor.ts / 1000, tz=TZ).date()
    try:
        async with _ai_slots:
            items = await extract_with_context(
                view, [blob for _, blob in downloaded], sent_day, ask_gpt, ask_vision
            )
    except Exception:
        logging.exception("Не удалось разобрать сообщения")
        return
    (ctx or buffer).mark_done(chat_id, [e.mid for e, _ in plan.images])  # эти фото больше не читаем
    delete_vague_for(chat_id, [e.mid for e in entries])  # черновик «ДЗ на завтра» заменяется полной записью
    if not items:
        return

    with_text = [e for e in entries if e.text]
    if len(with_text) == 1 and with_text[0] is anchor and not downloaded:
        body = anchor.text
    else:
        body = "\n".join(
            f"{e.author or 'Участник'}: {e.text or '[фото]'}" for e in entries if e.text or e.images
        )[:1500]
    save_message(chat_id, anchor.mid, anchor.author, anchor.ts, body)
    token = next((info.get("token") for info, _ in downloaded if info.get("token")), None)
    src_kind = "photo" if downloaded else "text"
    for item in items:
        add_task(chat_id, anchor.mid, item["kind"], item["subject"], item["text"], item["due"], anchor.ts,
                 src_kind=src_kind, conf=None, photo_token=token)
    if downloaded:  # фото задания попадает ещё и в «Материалы» своего предмета
        materials.save_homework_photos(
            chat_id, anchor.mid, anchor.ts, items[0]["subject"],
            [info.get("token") for info, _ in downloaded], [e.text for e in entries if e.text],
        )
    logging.info("Чат %s: найдено записей %d (фото: %d)", chat_id, len(items), len(downloaded))


async def process_voice(chat_id: int, mid: str, url, ts: int, author: str, transcription=None):
    """Аудио: берём расшифровку MAX, а если её нет — скачиваем и расшифровываем сами.
    Дальше как обычный текст. Само аудио нигде не сохраняется."""
    if transcription:  # MAX уже расшифровал: ничего скачивать и платить за ИИ не нужно
        text, conf = transcription, "medium"
    else:
        if not config.STT_ENABLED or not url:
            return
        try:
            async with _ai_slots:
                data, ext = await voice.download(url)
                text, conf = await voice.transcribe(data, ext)
        except Exception:
            logging.exception("Не удалось расшифровать голосовое")
            return
        finally:
            data = None  # аудио забываем сразу
    if not text or not looks_useful(text):  # болтовню в голосовых не храним
        return
    await process_message(chat_id, mid, text, ts, author, src_kind="voice", conf=conf)


def schedule_voice(message):
    """Если сообщение голосовое — отправляет на расшифровку. Возвращает True, если отправили."""
    if not is_group(message) or getattr(message.sender, "is_bot", False):
        return False
    url, transcription = voice.audio_info(message)
    if not url and not transcription:
        return False
    _user_id, name = sender_info(message)
    chat_id = message.recipient.chat_id
    ts = message.timestamp or int(time.time() * 1000)
    mid = message.body.mid or f"{chat_id}:{ts}"
    run_in_background(process_voice(chat_id, mid, url, ts, name, transcription))
    return True


def schedule_message(message):
    """Общая часть для нового и отредактированного сообщения. Возвращает True, если отправили на разбор."""
    if not is_group(message):
        return False
    text = (message.body.text or "").strip()
    if not text or text.startswith("/") or getattr(message.sender, "is_bot", False):
        return False
    if not looks_useful(text):  # на «ок» и стикеры ИИ не тратим и ничего не храним
        return False
    user_id, name = sender_info(message)
    chat_id = message.recipient.chat_id
    ts = message.timestamp or int(time.time() * 1000)
    mid = message.body.mid or f"{chat_id}:{ts}"
    run_in_background(process_message(chat_id, mid, text, ts, name))
    return True


# ============ 3. Показ сводки ============

async def send_summary(message, chat_id: int, in_group: bool, title: str = "", bot=None, user_id=None):
    """Показывает актуальные записи ОДНОГО чата. Другие чаты сюда не попадают.

    Если сводку смотрит учитель (или владелец чата), появляется кнопка «✅ Подтвердить всё».
    """
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
    teacher_view = bool(bot and user_id is not None and await can_verify(bot, chat_id, user_id))
    keyboard = build_source_keyboard(
        ordered, extra=bottom, verify_chat=chat_id if teacher_view else None
    )
    await message.answer(head + text, attachments=[keyboard])


async def accessible_chats(bot, user_id):
    """Чаты человека, где он состоит ПРЯМО СЕЙЧАС (проверка у MAX, а не только по базе)."""
    result = []
    for chat_id, label in user_chats(user_id)[:8]:
        if await has_access(bot, chat_id, user_id):
            result.append((chat_id, label))
    return result


async def show_my_chats(bot, message, user_id: int):
    """Личка: если чат один — сразу сводка, если несколько — выбор."""
    chats = await accessible_chats(bot, user_id)
    if not chats:
        await message.answer(
            "Я пока не знаю твоих чатов 🤔\n"
            "Добавь меня в чат класса, сделай администратором и нажми там «🔄 Обновить участников».",
            attachments=[back_menu()],
        )
    elif len(chats) == 1:
        await send_summary(message, chats[0][0], in_group=False, title=chats[0][1], bot=bot, user_id=user_id)
    else:
        await message.answer("Какой класс показать?", attachments=[chat_picker(chats)])


async def show_all_chats(bot, message, user_id: int):
    """Личка: сводки всех чатов подряд, каждая со своим заголовком."""
    chats = await accessible_chats(bot, user_id)
    if not chats:
        await show_my_chats(bot, message, user_id)
        return
    for chat_id, label in chats:
        await send_summary(message, chat_id, in_group=False, title=label, bot=bot, user_id=user_id)


async def show_demo(message):
    """Демо без базы: вымышленный класс, чтобы можно было попробовать бота."""
    text, ordered = render_tasks(demo_rows())
    await message.answer(
        f"{DEMO_TITLE}\n\n{text}",
        attachments=[build_source_keyboard(ordered, extra=bottom_buttons_dm())],
    )
    sample = digest.build_digest([("7Б", demo_rows())], datetime.now(TZ).date(), "evening")
    if sample:
        await message.answer("🔔 А так выглядит вечерняя сводка (демо):\n\n" + sample)


# ---------- история за 7 дней ----------

_backfill_last = {}        # chat_id -> время последнего чтения
BACKFILL_COOLDOWN_S = 3600  # чаще раза в час читать историю не даём: это расходует токены


async def do_backfill(bot, chat_id: int, manual: bool = False):
    """Читает сообщения чата за последние 7 дней и сообщает результат в чат."""
    now = time.time()
    last = _backfill_last.get(chat_id)
    if manual and last and now - last < BACKFILL_COOLDOWN_S:
        await bot.send_message(
            chat_id=chat_id, text="Историю я уже читал недавно 🙂 Новые сообщения я и так записываю.",
            attachments=[group_menu(chat_id)],
        )
        return
    _backfill_last[chat_id] = now
    tasks_before, materials_before = len(get_tasks(chat_id)), len(get_materials(chat_id))
    try:
        stats = await history.run_backfill(bot, chat_id, process_plan)
    except Exception:
        logging.exception("Не удалось прочитать историю чата %s", chat_id)
        text = ("Историю прочитать не получилось 😕\n"
                "Сделайте меня администратором чата и нажмите «📥 История за 7 дней».")
    else:
        delete_expired()  # то, что уже устарело, не показываем
        tasks_new = max(0, len(get_tasks(chat_id)) - tasks_before)
        materials_new = max(0, len(get_materials(chat_id)) - materials_before)
        if stats["messages"] == 0:
            text = ("За последние 7 дней сообщений не нашёл.\n"
                    "Если они есть, сделайте меня администратором и нажмите «📥 История за 7 дней».")
        else:
            text = (f"📥 Прочитал историю за {config.HISTORY_DAYS} дн.: сообщений {stats['messages']}, "
                    f"новых записей {tasks_new}, новых материалов {materials_new}.")
    await bot.send_message(chat_id=chat_id, text=text, attachments=[group_menu(chat_id)])


# ---------- материалы ----------

async def send_materials(message, chat_id: int, in_group: bool, title: str = ""):
    """Список материалов ОДНОГО чата по предметам, с кнопками «📎 1, 2…»."""
    text, ordered = materials.render_materials(get_materials(chat_id))
    head = f"━━ {title} ━━\n" if title else ""
    bottom = bottom_buttons_materials(chat_id) if in_group else bottom_buttons_dm()
    await message.answer(head + text, attachments=[materials.build_keyboard(ordered, extra=bottom)])


async def show_materials_menu(bot, message, user_id: int):
    """Личка: если чат один — сразу материалы, если несколько — выбор."""
    chats = await accessible_chats(bot, user_id)
    if not chats:
        await show_my_chats(bot, message, user_id)
    elif len(chats) == 1:
        await send_materials(message, chats[0][0], in_group=False, title=chats[0][1])
    else:
        await message.answer("Материалы какого класса показать?", attachments=[chat_picker(chats, prefix="mat")])


# ---------- напоминания ----------

def notify_status(sub) -> str:
    enabled = bool(sub and sub[2])
    hour = sub[3] if sub else 19
    morning = bool(sub[4]) if sub else True
    return (
        "🔔 Напоминания о дедлайнах\n"
        f"Сейчас: {'включены ✅' if enabled else 'выключены'}\n\n"
        f"🌙 Вечером в {hour}:00 — что сдавать завтра и ближайшие сроки\n"
        f"☀️ Утром в 07:30 — что сдавать сегодня ({'вкл' if morning else 'выкл'})\n\n"
        "Если заданий нет, я ничего не пришлю. Время — по Учалам. Сводка собирается из всех ваших классов."
    )


async def send_digest_now(bot, message, user_id: int):
    """Кнопка «Прислать сейчас»: показывает, как выглядит вечерняя сводка, прямо сейчас."""
    now = datetime.now(TZ)
    blocks, new_materials = await digest.collect_blocks(bot, user_id, now)
    text = digest.build_digest(blocks, now.date(), "evening", new_materials)
    await message.answer(text or "Ближайших заданий со сроком нет 🎉", attachments=[digest.digest_keyboard()])


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
    "У каждой строки домашки есть кнопка 🔎 — она покажет исходное сообщение.\n"
    "📷 Фото с домашкой я тоже читаю, а соседние сообщения («ДЗ на завтра» + «№112») собираю в одну запись.\n"
    "✅ Запись, которую подтвердил учитель или владелец чата, помечается как проверенная.\n"
    "📎 Файлы и фото-материалы раскладываю по предметам: кнопка «Материалы».\n"
    "🔔 Кнопка «Напоминания» включает вечернюю сводку и утреннее напоминание о сроках.\n"
    "📥 При добавлении в чат я читаю сообщения за последние 7 дней.\n\n"
    "🔒 Сводку видят только те, кто сейчас состоит в чате. "
    "Храню только сообщения, из которых получилась запись, и не дольше 7 дней."
)


# ============ 4. События MAX ============

@router.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(chat_id=event.chat_id, text=WELCOME, attachments=[main_menu()])


@router.bot_added()
async def bot_added(event: BotAdded):
    """Бота добавили в чат: сразу пробуем подтянуть всех участников и название."""
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
    run_in_background(do_backfill(event.bot, chat_id))  # сразу читаем сообщения за последние 7 дней


def _event_ids(event):
    """(chat_id, user_id) из события об участниках. Поля читаем осторожно: версии maxapi различаются."""
    chat_id = getattr(event, "chat_id", None)
    user = getattr(event, "user", None)
    user_id = getattr(user, "user_id", None) or getattr(event, "user_id", None)
    return chat_id, user_id


# Эти события есть не во всех версиях maxapi, поэтому подключаем их только если они существуют.
_bot_removed = getattr(router, "bot_removed", None)
if _bot_removed:
    @_bot_removed()
    async def on_bot_removed(event):
        """Бота убрали из чата — удаляем все данные этого чата."""
        chat_id = getattr(event, "chat_id", None)
        if chat_id is not None:
            delete_chat_data(chat_id)
            buffer.forget_chat(chat_id)
            logging.info("Чат %s: бота убрали, данные удалены", chat_id)

_user_removed = getattr(router, "user_removed", None)
if _user_removed:
    @_user_removed()
    async def on_user_removed(event):
        """Участник вышел или исключён — сразу закрываем ему доступ."""
        chat_id, user_id = _event_ids(event)
        if chat_id is not None and user_id is not None:
            remove_member(chat_id, user_id)

_user_added = getattr(router, "user_added", None)
if _user_added:
    @_user_added()
    async def on_user_added(event):
        chat_id, user_id = _event_ids(event)
        if chat_id is not None and user_id is not None:
            touch_member(chat_id, user_id)

_message_edited = getattr(router, "message_edited", None)
if _message_edited:
    @_message_edited()
    async def on_message_edited(event):
        """Сообщение отредактировали — разбираем заново, старые записи из него заменятся."""
        message = getattr(event, "message", None)
        if message is not None:
            schedule_message(message)


# ============ 5. Команды (запасной вариант, основной путь — кнопки) ============

@router.message_created(CommandStart())
async def start(event: MessageCreated):
    if is_group(event.message):
        await event.message.answer(
            "Выбери действие 👇", attachments=[group_menu(event.message.recipient.chat_id)]
        )
    else:
        await event.message.answer(WELCOME, attachments=[main_menu()])


@router.message_created(Command("menu"))
async def menu_command(event: MessageCreated):
    await start(event)


@router.message_created(Command("summary"))
async def summary_command(event: MessageCreated):
    message = event.message
    user_id, _name = sender_info(message)
    if is_group(message):
        chat_id = message.recipient.chat_id
        touch_member(chat_id, user_id)
        await ensure_synced(event.bot, chat_id)
        await send_summary(message, chat_id, in_group=True, bot=event.bot, user_id=user_id)
    else:
        await show_my_chats(event.bot, message, user_id)


async def do_scan(bot, message, chat_id: int):
    """Подтягивает участников и сообщает результат."""
    try:
        count = await sync_members(bot, chat_id)
        await message.answer(f"Готово! Участников в классе: {count} ✅", attachments=[group_menu(chat_id)])
    except Exception:
        logging.exception("Не удалось получить участников")
        await message.answer("Не получилось 😕 Сделайте меня администратором чата и нажмите кнопку ещё раз.")


@router.message_created(Command("scan"))
async def scan_command(event: MessageCreated):
    message = event.message
    if not is_group(message):
        await message.answer("Это работает в чате класса.")
        return
    await do_scan(event.bot, message, message.recipient.chat_id)


@router.message_created(Command("class"))
async def set_class(event: MessageCreated):
    """Название чата: /class 7Б."""
    message = event.message
    if not is_group(message):
        await message.answer("Название задаётся в чате класса: /class 7Б")
        return
    title = (message.body.text or "").replace("/class", "", 1).strip()
    if not title:
        await message.answer("Напиши название после команды, например: /class 7Б")
        return
    user_id, _name = sender_info(message)
    touch_member(message.recipient.chat_id, user_id)
    set_title(message.recipient.chat_id, title)
    await message.answer(f"Готово, этот чат называется «{title[:60]}».")


# ============ 6. Кнопки ============

pending_title = {}  # (chat_id, user_id) -> когда нажали ✏️. Ждём от этого человека название.
TITLE_WAIT_S = 300  # ждём 5 минут


@router.message_callback(F.callback.payload.startswith("menu:"))
async def menu_buttons(event: MessageCallback):
    action = event.callback.payload.split(":")[1]
    user_id, _name = callback_user(event)
    await event.answer(notification="Секунду…")
    if action == "summary":
        await show_my_chats(event.bot, event.message, user_id)
    elif action == "all":
        await show_all_chats(event.bot, event.message, user_id)
    elif action == "mat":
        await show_materials_menu(event.bot, event.message, user_id)
    elif action == "notify":
        await event.message.answer(notify_status(get_sub(user_id)), attachments=[notify_menu(get_sub(user_id))])
    elif action == "demo":
        await show_demo(event.message)
    elif action == "teacher":
        if teacher.start_waiting(user_id) == "disabled":
            await event.message.answer(
                "Подтверждать записи может владелец чата: откройте сводку своего чата, и там появится "
                "кнопка «✅ Подтвердить всё».",
                attachments=[back_menu()],
            )
        else:
            await event.message.answer(
                "Введи код учителя одним сообщением (у тебя 5 минут). Его выдаёт администрация школы.",
                attachments=[back_menu()],
            )
    elif action == "help":
        await event.message.answer(HELP, attachments=[back_menu()])
    else:  # home
        await event.message.answer(WELCOME, attachments=[main_menu()])


@router.message_callback(F.callback.payload.startswith("sum:"))
async def summary_button(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    in_group = is_group(event.message)
    if in_group:  # нажал в самом чате — значит он там точно есть
        touch_member(chat_id, user_id)
    elif not await has_access(event.bot, chat_id, user_id):  # в личке: проверяем у MAX
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Показываю сводку")
    if in_group:
        await ensure_synced(event.bot, chat_id)
    await send_summary(
        event.message, chat_id, in_group=in_group,
        title="" if in_group else get_title(chat_id) or "", bot=event.bot, user_id=user_id,
    )


@router.message_callback(F.callback.payload.startswith("title:"))
async def title_button(event: MessageCallback):
    """Кнопка ✏️: следующее сообщение этого человека станет названием чата."""
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    touch_member(chat_id, user_id)
    pending_title[(chat_id, user_id)] = time.time()
    await event.answer(notification="Жду название")
    await event.message.answer("Напиши название следующим сообщением, например: 7Б")


@router.message_callback(F.callback.payload.startswith("scan:"))
async def scan_button(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    await event.answer(notification="Обновляю список…")
    await do_scan(event.bot, event.message, chat_id)


@router.message_callback(F.callback.payload.startswith("hist:"))
async def history_button(event: MessageCallback):
    """Кнопка «📥 История за 7 дней»: прочитать сообщения чата за неделю."""
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    touch_member(chat_id, user_id)
    await event.answer(notification="Читаю историю…")
    run_in_background(do_backfill(event.bot, chat_id, manual=True))


@router.message_callback(F.callback.payload.startswith("mat:"))
async def materials_button(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    in_group = is_group(event.message)
    if in_group:
        touch_member(chat_id, user_id)
    elif not await has_access(event.bot, chat_id, user_id):
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Показываю материалы")
    await send_materials(
        event.message, chat_id, in_group=in_group, title="" if in_group else get_title(chat_id) or ""
    )


@router.message_callback(F.callback.payload.startswith("mf:"))
async def material_file(event: MessageCallback):
    """Кнопка «📎 N»: присылает файл или фото ещё раз (по токену MAX, у нас файла нет)."""
    material = get_material(int(event.callback.payload.split(":")[1]))
    if material is None:
        await event.answer(notification="Материал уже удалён")
        return
    chat_id, subject, title, kind, token, _ts = material
    user_id, _name = callback_user(event)
    if not await has_access(event.bot, chat_id, user_id):
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Присылаю")
    attachment = materials.attachment_by_token(kind, token)
    if attachment is not None:
        try:
            await event.message.answer(f"📎 {subject}: {title}", attachments=[attachment])
            return
        except Exception:
            logging.exception("Не удалось отправить материал")
    await event.message.answer(
        f"📎 {subject}: {title}\nЗаново прислать не получилось — найдите файл в чате класса по названию."
    )


@router.message_callback(F.callback.payload.startswith("nt:"))
async def notify_buttons(event: MessageCallback):
    """Настройки вечерней сводки и утреннего напоминания (только в личке)."""
    if is_group(event.message):
        await event.answer(notification="Напоминания настраиваются в личке с ботом")
        return
    user_id, _name = callback_user(event)
    dm_chat_id = event.message.recipient.chat_id
    parts = event.callback.payload.split(":")
    action = parts[1]
    sub = get_sub(user_id)
    if action == "on":
        set_sub(user_id, dm_chat_id, enabled=1)
    elif action == "off":
        if sub:
            set_sub(user_id, dm_chat_id, enabled=0)
    elif action == "h" and len(parts) > 2 and parts[2].isdigit() and 18 <= int(parts[2]) <= 21:
        set_sub(user_id, dm_chat_id, hour=int(parts[2]), enabled=1)
    elif action == "m":
        set_sub(user_id, dm_chat_id, morning=0 if (sub is None or sub[4]) else 1)
    elif action == "now":
        await event.answer(notification="Собираю…")
        await send_digest_now(event.bot, event.message, user_id)
        return
    await event.answer(notification="Готово")
    sub = get_sub(user_id)
    await event.message.answer(notify_status(sub), attachments=[notify_menu(sub)])


@router.message_callback(F.callback.payload.startswith("src:"))
async def show_source(event: MessageCallback):
    """Кнопка 🔎: показывает исходное сообщение, из которого взята запись."""
    task_id = int(event.callback.payload.split(":")[1])
    if task_id < 0:  # демо-запись
        await event.answer(notification="Источник")
        await event.message.answer(demo_source_text(task_id), attachments=[source_buttons(0, demo=True)])
        return
    user_id, _name = callback_user(event)
    task = get_task(task_id)
    if task is None:
        await event.answer(notification="Запись уже устарела и удалена")
        return
    chat_id, mid = task[0], task[1]
    if not await has_access(event.bot, chat_id, user_id):  # проверка у MAX: вышедший не увидит
        await event.answer(notification="Нет доступа к этому чату")
        return
    await event.answer(notification="Ищу источник")
    text = build_source_text(task, get_message(chat_id, mid))
    teacher_view = not task[7] and await can_verify(event.bot, chat_id, user_id)  # учитель видит ✅ и 🗑
    buttons = source_buttons(chat_id, task_id, teacher=teacher_view, in_group=is_group(event.message))
    attachment = photos.photo_attachment(task[10] if len(task) > 10 else None)
    if attachment is not None:  # приложенное фото возвращаем по токену, у нас его нет
        try:
            await event.message.answer("📷 Фото из сообщения:", attachments=[attachment])
        except Exception:
            logging.exception("Не удалось отправить фото")
    # кнопки «К сводке» и «Меню» — под последним сообщением, чтобы они всегда были внизу
    await event.message.answer(text, attachments=[buttons])


async def _teacher_task(event, task_id: int):
    """Общая проверка для ✅ и 🗑: запись существует и нажавший имеет право. Возвращает запись или None."""
    user_id, _name = callback_user(event)
    task = get_task(task_id)
    if task is None:
        await event.answer(notification="Запись уже удалена")
        return None
    if not await can_verify(event.bot, task[0], user_id):
        await event.answer(notification="Подтверждать может учитель или владелец чата")
        return None
    return task


@router.message_callback(F.callback.payload.startswith("ok:"))
async def confirm_one(event: MessageCallback):
    task_id = int(event.callback.payload.split(":")[1])
    task = await _teacher_task(event, task_id)
    if task is None:
        return
    verify_task(task_id)
    await event.answer(notification="Подтверждено ✅")
    await event.message.answer(f"✅ Проверено: {task[3]}: {task[4]}")


@router.message_callback(F.callback.payload.startswith("del:"))
async def delete_one(event: MessageCallback):
    task_id = int(event.callback.payload.split(":")[1])
    task = await _teacher_task(event, task_id)
    if task is None:
        return
    delete_task(task_id)
    await event.answer(notification="Запись удалена")
    await event.message.answer(f"🗑 Запись удалена: {task[3]}: {task[4]}")


@router.message_callback(F.callback.payload.startswith("okall:"))
async def confirm_all(event: MessageCallback):
    chat_id = int(event.callback.payload.split(":")[1])
    user_id, _name = callback_user(event)
    if not await can_verify(event.bot, chat_id, user_id):
        await event.answer(notification="Подтверждать может учитель или владелец чата")
        return
    count = verify_all(chat_id)
    await event.answer(notification=f"Подтверждено записей: {count}")
    in_group = is_group(event.message)
    await send_summary(
        event.message, chat_id, in_group=in_group,
        title="" if in_group else get_title(chat_id) or "", bot=event.bot, user_id=user_id,
    )


# ============ 7. Каждое новое сообщение чата ============
# ВАЖНО: этот обработчик должен стоять ПОСЛЕДНИМ — он ловит всё, что не команда.

@router.message_created()
async def collect(event: MessageCreated):
    message = event.message
    text = (message.body.text or "").strip()

    if not is_group(message):  # личные сообщения боту не сохраняем; исключение — ввод кода учителя
        user_id, _name = sender_info(message)
        if text and not text.startswith("/") and teacher.is_waiting(user_id):
            result = teacher.try_code(user_id, text)
            answers = {
                "ok": "Готово! Теперь ты можешь подтверждать записи: открой сводку и нажми «✅ Подтвердить всё».",
                "wrong": "Код не подошёл. Попробуй ещё раз.",
                "locked": "Слишком много попыток. Попробуй через 15 минут.",
                "disabled": "Код учителя не настроен.",
            }
            await message.answer(answers[result], attachments=[back_menu()])
        return

    if getattr(message.sender, "is_bot", False):
        return
    user_id, name = sender_info(message)
    chat_id = message.recipient.chat_id
    touch_member(chat_id, user_id)  # автор точно в чате
    images = photos.image_infos(message) if config.PHOTO_SCAN != "off" else []
    ts = message.timestamp or int(time.time() * 1000)
    mid = message.body.mid or f"{chat_id}:{ts}"
    files = materials.file_infos(message)
    if files or images:  # файлы и «учебные» фото складываем в «Материалы» по предметам
        near = [e.text for e in buffer.window(chat_id, ts)[-3:] if e.text]
        materials.save_from_message(chat_id, mid, ts, text, files, images, near)
    if not text and not images:
        if not files:
            schedule_voice(message)  # голосовое — расшифровываем в фоне
        return
    if text.startswith("/"):
        return

    # Человек нажал ✏️ и теперь пишет название чата.
    if text:
        asked_at = pending_title.pop((chat_id, user_id), None)
        if asked_at and time.time() - asked_at < TITLE_WAIT_S:
            set_title(chat_id, text)
            await message.answer(f"Готово, чат называется «{text[:60]}» ✅")
            return

    # Сообщение запоминается в памяти на 10 минут: задание может быть разбито на части или с фото.
    entry = Entry(mid=mid, ts=ts, author=name, text=text, images=images)
    buffer.push(chat_id, entry)
    plan = make_plan(buffer.window(chat_id, ts), entry, config.PHOTO_SCAN)
    if plan:  # в базу попадёт только если из этого получится запись
        run_in_background(process_plan(chat_id, plan))