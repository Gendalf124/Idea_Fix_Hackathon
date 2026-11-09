import asyncio
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")  # видно, какой способ передачи аудио подошёл
logging.getLogger("httpx").setLevel(logging.WARNING)

from app import config, voice  # noqa: E402


async def main():
    if len(sys.argv) < 2:
        sys.exit("Укажите файл: python test_stt.py файл.ogg")
    path = sys.argv[1]
    if len(sys.argv) > 2:
        config.STT_MODEL = sys.argv[2]
    ext = os.path.splitext(path)[1].lower() or ".ogg"
    data = open(path, "rb").read()
    print(f"Файл: {path} ({len(data) // 1024} КБ), режим: {config.STT_MODE}, модель: {config.STT_MODEL}")
    started = time.time()
    try:
        text, conf = await voice.transcribe(data, ext)
    except Exception as exc:
        print(f"\nОШИБКА: {exc}")
        print("Пришлите этот текст — по нему видно, что не принял сервис.")
        return
    print(f"\nГотово за {time.time() - started:.1f} с")
    print(f"Уверенность: {voice.confidence_percent(conf)} ({conf})")
    print(f"Текст: {text or '(пусто)'}")


asyncio.run(main())