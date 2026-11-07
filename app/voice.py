"""Голосовые сообщения: скачать → расшифровать → оценить уверенность.

Аудио держится только в памяти и после расшифровки забывается: на диск и в базу оно не попадает.
Дальше расшифровка идёт тем же путём, что и обычный текст.
"""
import base64
import logging
import os
import re
from urllib.parse import urlparse

from app import config
from app.ai import client

MAX_BYTES = 15 * 1024 * 1024
KNOWN_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".mp4", ".aac", ".amr", ".flac"}


def _enum_str(value) -> str:
    return str(getattr(value, "value", value)).lower()


def audio_info(message):
    """(ссылка на аудио, расшифровка от MAX) первого аудио-вложения. Если аудио нет — (None, None).

    По документации MAX у вложения audio есть url и необязательное поле transcription.
    Имена полей читаем осторожно: у разных версий maxapi они могут чуть отличаться.
    """
    body = getattr(message, "body", None)
    for att in getattr(body, "attachments", None) or []:
        if _enum_str(getattr(att, "type", "")) in ("audio", "voice"):
            payload = getattr(att, "payload", None)
            url = getattr(payload, "url", None)
            transcription = (getattr(payload, "transcription", None) or "").strip() or None
            if url or transcription:
                return url, transcription
    return None, None


def audio_url(message):
    """Ссылка на аудио, если оно есть в сообщении. Иначе None."""
    return audio_info(message)[0]


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def confidence_from(response) -> str:
    """Оценка уверенности по данным распознавания: high | medium | low | unknown.

    Берём средний avg_logprob по фрагментам и наибольшую вероятность «тишины» (no_speech_prob).
    Пороги эвристические; если сервис не отдаёт эти поля, уверенность «unknown».
    """
    segments = _get(response, "segments") or []
    logprobs = [_get(s, "avg_logprob") for s in segments if _get(s, "avg_logprob") is not None]
    if not logprobs:
        return "unknown"
    avg = sum(logprobs) / len(logprobs)
    silence = max([_get(s, "no_speech_prob") or 0.0 for s in segments])
    if silence > 0.6 or avg < -1.0:
        return "low"
    if avg >= -0.4:
        return "high"
    return "medium"


def confidence_percent(conf: str):
    """Для показа: примерная «вероятность» из logprob не считаем, только словесная метка."""
    return {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(conf, "неизвестна")


async def download(url: str):
    """Скачивает аудио в память. Возвращает (байты, расширение). Бросает ошибку, если файл слишком большой."""
    if not url.startswith("https://"):
        raise ValueError("ссылка на аудио не https")
    import httpx  # приходит вместе с openai

    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in KNOWN_EXT:
        ext = ".ogg"
    buf = bytearray()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise ValueError("аудио слишком большое")
    return bytes(buf), ext


CHAT_PROMPT = (
    "Расшифруй это голосовое сообщение школьного чата дословно, на русском языке. "
    "Выведи ТОЛЬКО текст расшифровки, без пояснений и кавычек. "
    "Если речи нет или она неразборчива, выведи пустую строку. "
    "Последней строкой напиши оценку своей уверенности в расшифровке: CONFIDENCE: high, medium или low."
)
_CONF_LINE = re.compile(r"\n?\s*CONFIDENCE\s*:\s*(high|medium|low)\s*\.?\s*$", re.IGNORECASE)
_MIME = {".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg",
         ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
         ".webm": "audio/webm", ".amr": "audio/amr", ".flac": "audio/flac"}


def split_confidence(answer: str):
    """Отделяет от ответа модели строку «CONFIDENCE: …». Возвращает (текст, уверенность)."""
    answer = (answer or "").strip()
    match = _CONF_LINE.search(answer)
    if not match:
        return answer, "unknown"
    return answer[: match.start()].strip().strip("«»\"'"), match.group(1).lower()


def _audio_parts(data: bytes, ext: str):
    """Варианты, как передать аудио в запросе к чату (разные сервисы принимают разный вид)."""
    b64 = base64.b64encode(data).decode()
    fmt = {"oga": "ogg", "opus": "ogg", "mp4": "m4a"}.get(ext.lstrip("."), ext.lstrip("."))
    mime = _MIME.get(ext, "audio/ogg")
    yield "input_audio", {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
    yield "file", {"type": "file", "file": {"file_data": f"data:{mime};base64,{b64}", "filename": "voice" + ext}}
    yield "image_url", {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


async def transcribe_chat(data: bytes, ext: str = ".ogg"):
    """Расшифровка моделью с входом «Аудио» через обычный запрос к чату."""
    last_error = None
    for name, part in _audio_parts(data, ext):
        try:
            response = await client.chat.completions.create(
                model=config.STT_MODEL,
                messages=[{"role": "user", "content": [{"type": "text", "text": CHAT_PROMPT}, part]}],
            )
            answer = response.choices[0].message.content or ""
            logging.info("STT: аудио принято способом «%s»", name)
            return split_confidence(answer)
        except Exception as exc:  # сервис не принял этот вид — пробуем следующий
            last_error = exc
            logging.info("STT: способ «%s» не подошёл (%s)", name, exc)
    raise last_error


async def transcribe(data: bytes, ext: str = ".ogg"):
    """Расшифровывает аудио. Возвращает (текст, уверенность). Способ задаётся STT_MODE."""
    if config.STT_MODE == "chat":
        return await transcribe_chat(data, ext)
    return await transcribe_whisper(data, ext)


async def transcribe_whisper(data: bytes, ext: str = ".ogg"):
    """Отдельный метод распознавания речи (/audio/transcriptions), если у сервиса он есть."""
    kwargs = dict(model=config.STT_MODEL, file=("voice" + ext, data), language="ru")
    try:
        response = await client.audio.transcriptions.create(response_format="verbose_json", **kwargs)
    except Exception as exc:  # не все сервисы умеют verbose_json — пробуем простой ответ
        logging.info("STT: verbose_json недоступен (%s), пробую обычный ответ", exc)
        response = await client.audio.transcriptions.create(**kwargs)
    text = response if isinstance(response, str) else (_get(response, "text") or "")
    return text.strip(), confidence_from(response)