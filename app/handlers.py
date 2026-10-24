import logging

from maxapi import Router
from maxapi.filters.command import Command, CommandStart
from maxapi.types import BotStarted, MessageCreated
from openai import AsyncOpenAI

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL

router = Router()

client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


async def ask_gpt(prompt: str) -> str:
    response = await client.chat.completions.create(
        model=AI_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or "Модель не вернула ответ."


@router.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text="Привет! Я бот для MAX 💙\nНапиши /gpt и свой вопрос.",
    )


@router.message_created(CommandStart())
async def hello(event: MessageCreated):
    await event.message.answer(
        "Привет! Я бот для MAX 💙\nНапиши /gpt и свой вопрос, чтобы начать."
    )


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
        answer = await ask_gpt(prompt)
        await event.message.answer(answer)
    except Exception:
        logging.exception("Ошибка при запросе к модели")
        await event.message.answer("Не удалось получить ответ от ИИ. Попробуй позже.")