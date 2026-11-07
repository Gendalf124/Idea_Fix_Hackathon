from dotenv import load_dotenv

load_dotenv()  # читаем .env ДО импорта остального

import asyncio  # noqa: E402
import logging  # noqa: E402

from maxapi import Bot, Dispatcher  # noqa: E402

from app.config import BOT_TOKEN  # noqa: E402
from app.db import init_db  # noqa: E402
from app.handlers import router  # noqa: E402
from app.maintenance import cleanup_loop, digest_loop, startup_checks  # noqa: E402
from app.tasks_db import init_tasks  # noqa: E402


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    init_tasks()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_routers(router)

    await startup_checks(bot)
    cleaner = asyncio.create_task(cleanup_loop())  # фоновая чистка данных
    digester = asyncio.create_task(digest_loop(bot))  # вечерняя сводка и напоминания
    try:
        await dp.start_polling(bot)
    finally:
        cleaner.cancel()
        digester.cancel()


if __name__ == "__main__":
    asyncio.run(main())