"""Готовые итоги, карточка и инлайн-результат «Мои итоги».

Одна точка на всё, чем человек делится: инлайн @бот, кнопка «Отправить
друзьям» в мини-аппе (prepared inline message) и история.
"""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedPhoto,
    InlineQueryResultPhoto,
)

import cards
import config
import db
import facts

log = logging.getLogger("itogi.share")

#: Сколько держать посчитанные итоги, секунд.
RESULTS_TTL = 30 * 60

_locks: dict[int, asyncio.Lock] = {}
_bot_username = ""


def set_username(name: str) -> None:
    global _bot_username
    _bot_username = name


def bot_link(ref: int | None = None) -> str:
    tail = f"?start=r{ref}" if ref else ""
    return f"https://t.me/{_bot_username}{tail}"


async def results(bot: Bot, user: dict, premium: bool, photo: str | None, fresh: bool = False) -> dict:
    """Итоги человека из кэша или свежие. Параллельные запросы ждут один."""
    user_id = int(user["id"])
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if not fresh:
            cached = await db.get_results(user_id, RESULTS_TTL)
            if cached:
                if photo:
                    cached["user"]["photo"] = photo
                return cached
        data = await facts.build(bot, user, premium, photo)
        await db.put_results(user_id, data)
        return data


async def card(data: dict | None, kind: str = "card") -> Path:
    return await asyncio.to_thread(cards.ensure, data, kind)


async def promo(kind: str) -> Path:
    """Баннер бота: «menu» — главное меню, «gate» — просьба подписаться."""
    return await asyncio.to_thread(cards.ensure_promo, kind)


async def photo(data: dict | None) -> Path:
    """Статичная карточка для инлайна (JPG)."""
    return await asyncio.to_thread(cards.ensure_still, data)


def warm(data: dict) -> None:
    """Нарисовать карточки заранее, пока человек листает слайды."""

    async def go() -> None:
        try:
            await photo(data)
            await card(data, "story")
        except Exception:
            log.exception("карточка не нарисовалась")

    asyncio.create_task(go())


def public_url(path: Path) -> str:
    return f"{config.PUBLIC_URL}/cards/{path.name}"


async def _file_id(bot: Bot, user_id: int | None, digest: str, path: Path) -> str | None:
    """Залить карточку в Telegram один раз и запомнить file_id.

    Так инлайн-картинка открывается мгновенно и не зависит от PUBLIC_URL.
    Куда заливаем: служебный чат (STORAGE_CHAT_ID), а если его нет или туда
    не отправить — в личку первому админу, и сразу удаляем сообщение.
    """
    key = user_id or 0
    known = await db.card_file(key, digest)
    if known:
        return known
    targets = [(config.STORAGE_CHAT_ID, False)] if config.STORAGE_CHAT_ID else []
    targets += [(admin, True) for admin in config.ADMIN_IDS[:1]]
    for chat_id, temporary in targets:
        try:
            msg = await bot.send_photo(chat_id, FSInputFile(path), disable_notification=True)
        except Exception as error:
            log.warning("карточку не залить в %s: %s", chat_id, error)
            continue
        file_id = msg.photo[-1].file_id if msg.photo else None
        if temporary:
            try:
                await msg.delete()
            except Exception:
                pass
        if file_id:
            await db.put_card_file(key, digest, file_id)
            return file_id
    return None


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def caption(data: dict | None) -> str:
    """Подпись инлайн-карточки: только самое цепляющее, коротко.

    Эмодзи здесь — из пака уточек: мидлварь Premium превратит их в
    премиум-эмодзи (если у бота есть юзернейм с Fragment).
    """
    year = config.YEAR
    if not data:
        return (
            f"🦆 <b>Итоги {year} в Telegram</b>\n\n"
            "<blockquote>😱 Раньше скольких людей ты пришёл в Telegram\n"
            "🎁 Сколько подарков у тебя и сколько стоит коллекция\n"
            "⚙️ Твой уровень и сколько до следующего\n"
            f"🔥 Номинация года и 🌙 предсказание на {year + 1}</blockquote>\n\n"
            "✨ <b>Жми кнопку — и проверь свои!</b>"
        )
    a = data["age"]
    facts_lines: list[str] = []
    # Самое шокирующее — первой строкой.
    if a.get("earlier", 0) >= 10:
        facts_lines.append(f"😱 Я в Telegram раньше <b>{a['earlier']}%</b> всех людей")
    facts_lines.append(f"📅 <b>{_num(a['days'])}</b> {a['word']} вместе — <i>с {a['since']}</i>")
    g = data.get("gifts")
    if g and g.get("now"):
        parts = [f"🎁 <b>{g['now']}</b> {facts.plural(g['now'], 'подарок', 'подарка', 'подарков')}"]
        if g.get("year"):
            parts.append(f"+{g['year']} за год")
        if g.get("rub"):
            parts.append(f"коллекция ≈ <b>{_num(g['rub'])} ₽</b>")
        facts_lines.append(" · ".join(parts))
        top = g.get("top") or {}
        if top.get("nft") and top.get("rarity"):
            rarity = str(top["rarity"]).replace(".", ",")
            facts_lines.append(f"🎉 NFT <i>«{html.escape(top['title'])}»</i> — такая модель у <b>{rarity}%</b>")
    r = data.get("rating")
    if r and r.get("level"):
        facts_lines.append(f"⚙️ Уровень в Telegram: <b>{r['level']}</b>")
    if data.get("premium"):
        facts_lines.append("😎 <b>Premium</b>-легенда")
    v = facts.verdict(data)
    nom = html.escape(v["title"])
    lines = [
        f"🦆 <b>Мои итоги {year} в Telegram</b>",
        "",
        "<blockquote>" + "\n".join(facts_lines) + "</blockquote>",
        "",
        f"🔥 Итог года: <b><u>«{nom}»</u></b> — <i>{html.escape(v['why'])}</i>",
    ]
    if data.get("prediction"):
        lines.append(f"🌙 Предсказание на {year + 1}: <tg-spoiler>{html.escape(data['prediction'])}</tg-spoiler>")
    lines += ["", "✨ <b>А у тебя? Проверь свои итоги!</b>"]
    return "\n".join(lines)


def _markup(ref: int | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✨ Проверить свои итоги", url=bot_link(ref))]]
    )


async def inline_result(bot: Bot, data: dict | None, result_id: str = "itogi") -> Any:
    """Карточка-картинка как инлайн-результат (без GIF: просто фото с подписью и кнопкой)."""
    path = await photo(data)
    user_id = (data or {}).get("user", {}).get("id")
    digest = facts.digest(data) if data else "v5"
    title = "Мои итоги года" if data else f"Итоги {config.YEAR}"
    w, h = cards.SIZES["card"]
    file_id = await _file_id(bot, user_id, digest + "p", path)
    common: dict[str, Any] = dict(
        id=result_id[:64],
        title=title,
        caption=caption(data),
        parse_mode="HTML",
        reply_markup=_markup(user_id),
    )
    description = "Мои итоги: факты, подарки, номинация" if data else "Узнай, сколько ты в Telegram и что тебя ждёт"
    if file_id:
        return InlineQueryResultCachedPhoto(photo_file_id=file_id, description=description, **common)
    thumb = path.with_name(path.stem + "_thumb.jpg")
    return InlineQueryResultPhoto(
        photo_url=public_url(path),
        thumbnail_url=public_url(thumb) if thumb.exists() else public_url(path),
        photo_width=w,
        photo_height=h,
        description=description,
        **common,
    )
