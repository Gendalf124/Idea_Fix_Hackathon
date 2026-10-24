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