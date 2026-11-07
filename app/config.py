import os
import sys


def need(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"В файле .env не заполнено: {name}")
    return value


BOT_TOKEN = need("BOT_TOKEN")
AI_API_KEY = need("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai.d1n0tf.ru/v1").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.6-luna").strip()

# --- необязательные настройки ---
# Код учителя: кто введёт его боту в личке, получает право подтверждать записи. Пусто — функция выключена.
TEACHER_CODE = os.getenv("TEACHER_CODE", "").strip()
# Расшифровка голосовых.
# STT_MODE=chat    — обычная модель с входом «Аудио» (glm-5.3-flash, gemini-…-flash, mimo-…), запрос как к чату;
# STT_MODE=whisper — отдельный метод распознавания речи /audio/transcriptions (whisper-1).
STT_MODE = os.getenv("STT_MODE", "chat").strip().lower()
STT_MODEL = os.getenv("STT_MODEL", "glm-5.3-flash").strip()
STT_ENABLED = os.getenv("STT_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")

# --- фото и контекст ---
# Когда читать фото: hinted — только если рядом есть подсказка (подпись или сообщение про ДЗ);
# all — смотреть каждое фото чата; off — фото не читать.
PHOTO_SCAN = os.getenv("PHOTO_SCAN", "hinted").strip().lower()
# Модель с входом «Изображение» (для чтения фото тетрадей, учебников, доски).
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.8-flash").strip()

# --- история, материалы, сводка ---
# Сколько дней истории читать при добавлении бота и потолки, чтобы не тратить много токенов.
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "7") or 7)
HISTORY_MAX_MESSAGES = int(os.getenv("HISTORY_MAX_MESSAGES", "300") or 300)  # сообщений за одно чтение
HISTORY_MAX_PLANS = int(os.getenv("HISTORY_MAX_PLANS", "40") or 40)          # обращений к ИИ за одно чтение
HISTORY_MAX_PHOTOS = int(os.getenv("HISTORY_MAX_PHOTOS", "5") or 5)          # фото за одно чтение
# Вечерняя сводка и утреннее напоминание (включаются кнопкой «🔔 Напоминания» в личке).
DIGEST_ENABLED = os.getenv("DIGEST_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")