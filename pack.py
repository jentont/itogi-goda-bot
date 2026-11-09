"""Пак премиум-эмодзи бота: собрать или дособрать через Bot API.

Используется в трёх местах: сам бот при запуске (если пака нет), кнопка
«Собрать пак» в /admin и tools/make_pack.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputSticker

import config
import emoji

log = logging.getLogger("itogi.pack")

Notify = Callable[[str], Awaitable[None]] | None


def _sticker(file: str, plain: str, folder=None) -> InputSticker:
    from admin import _tgs_for_emoji  # там же конвертер под правила Telegram (100×100, ≤3 с)

    return InputSticker(
        sticker=BufferedInputFile(_tgs_for_emoji((folder or config.TGS_DIR) / file), filename=file),
        format="animated",
        emoji_list=[plain],
    )


async def _retry(call, notify: Notify, tries: int = 4):
    """Вызов Bot API, который на флуд-контроле сам ждёт и пробует снова."""
    for attempt in range(tries):
        try:
            return await call()
        except TelegramRetryAfter as wait:
            if attempt == tries - 1:
                raise
            if notify:
                await notify(f"⏳ Telegram просит подождать {wait.retry_after} сек — дособеру пак сам.")
            log.info("флуд-контроль, жду %s сек", wait.retry_after)
            await asyncio.sleep(wait.retry_after + 2)


async def ensure(bot: Bot, name: str, owner: int, notify: Notify = None, icons: bool = False) -> int:
    """Довести пак до полного: создать, если нет, или добавить недостающие эмодзи.

    icons=True — пак белых иконок кнопок: needs_repainting, Telegram красит
    их в цвет текста. Возвращает, сколько эмодзи в паке. Полный пак не трогает.
    """
    folder = config.ICONS_DIR if icons else config.TGS_DIR
    source = emoji.ICON_ITEMS if icons else emoji.PACK_ITEMS
    items = [(f, e) for f, e in source if (folder / f).exists()]
    try:
        have = await bot.get_sticker_set(name)
    except TelegramBadRequest:
        have = None
    title = f"Итоги {config.YEAR} · Иконки" if icons else f"Итоги {config.YEAR} · Уточки"
    if have is None:
        # За раз Telegram принимает до 50 штук, остальное — по одной.
        first, rest = items[:50], items[50:]
        await _retry(lambda: bot.create_new_sticker_set(
            user_id=owner, name=name, title=title,
            stickers=[_sticker(f, e, folder) for f, e in first], sticker_type="custom_emoji",
            needs_repainting=icons or None,
        ), notify)
        present = {e for _, e in first}
    else:
        present = {s.emoji for s in have.stickers}
        rest = [(f, e) for f, e in items if e not in present]
    for f, e in rest:
        if e in present:
            continue
        await _retry(lambda f=f, e=e: bot.add_sticker_to_set(user_id=owner, name=name, sticker=_sticker(f, e, folder)), notify)
        present.add(e)
        await asyncio.sleep(0.4)
    got = await bot.get_sticker_set(name)
    return len(got.stickers)
