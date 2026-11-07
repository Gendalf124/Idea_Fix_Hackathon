"""Фоновые дела: проверка при запуске и регулярная чистка устаревших данных."""
import asyncio
import logging

from app import config
from app.ai import ask_gpt
from app.extras_db import delete_old_materials
from app.tasks_db import delete_expired

CLEAN_EVERY_S = 6 * 3600  # чистим раз в 6 часов


async def startup_checks(bot):
    """Проверяет токен и ИИ при запуске. Только предупреждает: бот продолжит работать."""
    try:
        me = await bot.get_me()
        logging.info("MAX: бот подключён (%s)", getattr(me, "username", None) or getattr(me, "name", "ok"))
    except Exception as exc:
        logging.error("MAX: не удалось проверить BOT_TOKEN (%s). Проверьте токен в .env", exc)
    logging.info("Голосовые: %s (модель %s)", "включены" if config.STT_ENABLED else "выключены", config.STT_MODEL)
    logging.info("Код учителя: %s", "настроен" if config.TEACHER_CODE else "не задан (подтверждать может владелец чата)")
    try:
        await asyncio.wait_for(ask_gpt("Ответь одним словом: ок"), timeout=25)
        logging.info("ИИ: подключение работает")
    except Exception as exc:
        logging.error("ИИ: нет ответа (%s). Проверьте AI_API_KEY, AI_BASE_URL и AI_MODEL в .env", exc)


async def cleanup_loop():
    """Раз в несколько часов удаляет устаревшие записи и оригиналы сообщений."""
    while True:
        try:
            removed = delete_expired()
            old_materials = delete_old_materials()
            if removed or old_materials:
                logging.info("Чистка: записей %d, материалов %d", removed, old_materials)
        except Exception:
            logging.exception("Ошибка при чистке")
        await asyncio.sleep(CLEAN_EVERY_S)


async def digest_loop(bot):
    """Раз в минуту смотрит, не пора ли отправить вечернюю сводку или утреннее напоминание."""
    from app import digest

    if not config.DIGEST_ENABLED:
        logging.info("Сводка и напоминания: выключены (DIGEST_ENABLED=false)")
        return
    while True:
        try:
            await digest.run_once(bot)
        except Exception:
            logging.exception("Ошибка в цикле сводки")
        await asyncio.sleep(60)