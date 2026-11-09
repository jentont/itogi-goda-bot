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
    cards.BOT_USERNAME = name or ""


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
    # Коротко и по-человечески: длинная подпись с призывами «жми кнопку»
    # антиспам-боты в чатах принимают за рекламу и ограничивают отправителя.
    # Все цифры и так на картинке.
    year = config.YEAR
    if not data:
        return (
            f"🦆 <b>Итоги {year} в Telegram</b>\n\n"
            "<blockquote>📅 Сколько ты в Telegram\n🎁 Сколько стоят твои подарки\n🏆 Твой итог года</blockquote>\n"
            "<i>У каждого свои</i> ✨"
        )
    v = facts.verdict(data)
    lines = [
        f"🦆 <b>Мой {year} в Telegram</b>",
        "",
        f"🏆 <b><u>{html.escape(v['title'])}</u></b>",
        f"<i>{html.escape(v['why'][:1].upper() + v['why'][1:])}</i>",
    ]
    # Цифры — цитатой, по строке на цифру: читается с одного взгляда.
    rows = []
    a = data.get("age") or {}
    g = data.get("gifts") or {}
    r = data.get("rating") or {}
    w = data.get("worth") or {}
    if a.get("days"):
        rows.append(f"📅 В Telegram — <b>{_num(a['days'])} {a.get('word', 'дней')}</b>")
    if g.get("now"):
        word = facts.plural(g["now"], "подарок", "подарка", "подарков")
        rows.append(f"🎁 В профиле — <b>{_num(g['now'])} {word}</b>")
    if r.get("level"):
        rows.append(f"⭐ Уровень — <b>{r['level']}</b>")
    if w.get("rub"):
        rows.append(f"💰 Профиль ≈ <b>{_num(w['rub'])} ₽</b>")
    if rows:
        lines += ["", "<blockquote>" + "\n".join(rows) + "</blockquote>"]
    if data.get("fun"):
        _, fun = cards._fun_line(data)
        label, _, value = fun.partition(": ")
        lines += ["", f"👀 <b>По версии уточек</b>\n<i>{html.escape(label)}</i> — <b>{html.escape(value)}</b>"]
    if data.get("prediction"):
        lines += ["", f"🌙 <b>Прогноз на {year + 1}</b>\n<tg-spoiler>{html.escape(data['prediction'])}</tg-spoiler>"]
    return "\n".join(lines)


def _markup(ref: int | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Посмотреть свои", url=bot_link(ref))]]
    )


async def inline_result(bot: Bot, data: dict | None, result_id: str = "itogi") -> Any:
    """Карточка-картинка как инлайн-результат (без GIF: просто фото с подписью и кнопкой)."""
    path = await photo(data)
    user_id = (data or {}).get("user", {}).get("id")
    digest = (facts.digest(data) if data else "v5") + "b2"
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
