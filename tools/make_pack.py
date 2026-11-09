"""Собрать или дособрать пак премиум-эмодзи через Bot API (токен из .env).

Запуск: python tools/make_pack.py
Обычно не нужен: бот сам собирает пак при запуске. Владелец пака — первый
id из ADMIN_IDS (он должен был нажать /start в боте).
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aiogram import Bot  # noqa: E402

import config  # noqa: E402
import emoji  # noqa: E402
import pack  # noqa: E402


async def main() -> None:
    if not config.BOT_TOKEN or not config.ADMIN_IDS:
        sys.exit("Нужны BOT_TOKEN и ADMIN_IDS в .env")
    bot = Bot(config.BOT_TOKEN)
    try:
        me = await bot.get_me()
        name = emoji.pack_name(me.username)

        async def say(text: str) -> None:
            print(text)

        count = await pack.ensure(bot, name, config.ADMIN_IDS[0], say)
        print(f"готово: {count} эмодзи — https://t.me/addemoji/{name}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
