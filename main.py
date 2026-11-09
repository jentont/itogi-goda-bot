"""Запуск: бот (long polling) и веб-сервер мини-аппа в одном процессе.

    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiohttp import web

import admin
import config
import db
import emoji
import handlers
import share
import sponsors
import web as webapp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if not config.BOT_TOKEN:
        sys.exit("BOT_TOKEN не задан — заполни .env (пример в .env.example)")
    if not config.PUBLIC_URL.startswith("https://"):
        logging.warning("PUBLIC_URL должен быть https://… — без него мини-апп и инлайн-карточки не откроются")

    await db.open_db()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.me()
    share.set_username(me.username)
    emoji.register(bot)
    pack = await db.get_setting("emoji_pack") or config.EMOJI_PACK or emoji.pack_name(me.username)
    await emoji.load(bot, pack)

    if config.PUBLIC_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=f"Итоги {config.YEAR}", web_app=WebAppInfo(url=config.PUBLIC_URL))
            )
        except Exception as error:
            logging.warning("кнопку меню не поставить: %s", error)
    await bot.set_my_commands([BotCommand(command="start", description=f"Мои итоги {config.YEAR}")])
    try:
        import texts_bot
        await bot.set_my_description(texts_bot.DESCRIPTION)
        await bot.set_my_short_description(texts_bot.SHORT_DESCRIPTION)
    except Exception as error:
        logging.warning("описание бота не поставить: %s", error)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)
    dp.include_router(handlers.router)

    app = webapp.make_app(bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT).start()
    logging.info("мини-апп слушает http://%s:%s, бот @%s", config.WEB_HOST, config.WEB_PORT, me.username)

    report = asyncio.create_task(sponsors.report_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        report.cancel()
        await runner.cleanup()
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as stop:
        if isinstance(stop, SystemExit) and stop.code not in (None, 0):
            raise
