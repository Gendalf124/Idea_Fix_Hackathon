"""Связь с ИИ. Вынесена в отдельный файл, чтобы её было легко заменить или проверить."""
import base64

from openai import AsyncOpenAI

from app import config
from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL

# timeout — не ждём ИИ дольше 30 секунд; max_retries — при сбое сети повторяем сами (до 2 раз).
client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL, timeout=30.0, max_retries=2)
# Для фото ждём дольше: модель с картинкой отвечает медленнее. Повтор всего один, чтобы не ждать минутами.
vision_client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL, timeout=75.0, max_retries=1)


async def ask_gpt(prompt: str, system: str | None = None) -> str:
    """Отправляет вопрос ИИ и возвращает текст ответа."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(model=AI_MODEL, messages=messages)
    return response.choices[0].message.content or ""


async def ask_vision(prompt: str, system: str | None, images) -> str:
    """Вопрос ИИ вместе с фото. images — список (байты, mime-тип). Модель берётся из VISION_MODEL."""
    content = [{"type": "text", "text": prompt}]
    for data, mime in images:
        b64 = base64.b64encode(data).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    response = await vision_client.chat.completions.create(model=config.VISION_MODEL, messages=messages)
    return response.choices[0].message.content or ""