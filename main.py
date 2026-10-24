import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from maxapi import Bot, Dispatcher

from app.handlers import router
from app.config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_routers(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")