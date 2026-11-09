from dotenv import load_dotenv

load_dotenv()  

import asyncio  
import logging  

from maxapi import Bot, Dispatcher  

from app.config import BOT_TOKEN  
from app.db import init_db  
from app.handlers import router  
from app.maintenance import cleanup_loop, digest_loop, startup_checks  
from app.tasks_db import init_tasks  


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    init_tasks()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_routers(router)

    await startup_checks(bot)
    cleaner = asyncio.create_task(cleanup_loop())  
    digester = asyncio.create_task(digest_loop(bot)) 
    try:
        await dp.start_polling(bot)
    finally:
        cleaner.cancel()
        digester.cancel()


if __name__ == "__main__":
    asyncio.run(main())