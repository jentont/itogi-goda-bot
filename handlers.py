"""Пользовательская часть бота: старт, подписка, меню, инлайн."""

from __future__ import annotations

import asyncio
import html
import logging
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChosenInlineResult,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultsButton,
    Message,
    WebAppInfo,
)

import cards
import config
import db
import share
import sponsors

log = logging.getLogger("itogi.handlers")
router = Router(name="user")


# ------------------------------------------------------------- тексты

def welcome_text(name: str) -> str:
    return (
        f"👋 Привет, <b>{html.escape(name or 'друг')}</b>!\n\n"
        f"🦆 Подведём твои <b>итоги {config.YEAR}</b> в Telegram:\n"
        "😱 раньше скольких людей ты пришёл в Telegram\n"
        "🎁 сколько подарков пришло за год и сколько стоит коллекция\n"
        "⚙️ какой у тебя уровень в Telegram\n"
        "🔥 твой итог года, номинации и 🌙 предсказание\n\n"
    )


def gate_text(name: str) -> str:
    return welcome_text(name) + "📢 Чтобы открыть итоги, подпишись на каналы наших друзей 👇"


def menu_text(name: str) -> str:
    return welcome_text(name) + "✨ Всё готово — жми кнопку и смотри свои итоги!"


# ------------------------------------------------------------- клавиатуры

def gate_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, row in enumerate(rows, 1):
        link = sponsors.link_of(row)
        if link:
            title = row["title"] or f"Спонсор {i}"
            buttons.append([InlineKeyboardButton(text=f"📢 {title}", url=link)])
    buttons.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def menu_kb(user_id: int) -> InlineKeyboardMarkup:
    invite = share.bot_link(user_id)
    text = quote(f"Посмотри свои итоги {config.YEAR} в Telegram! 🦆")
    rows = [
        [InlineKeyboardButton(text="✨ Смотреть итоги года", web_app=WebAppInfo(url=config.PUBLIC_URL), style="primary")],
        [InlineKeyboardButton(text="🎁 Отправить другу", switch_inline_query="")],
        [InlineKeyboardButton(text="👥 Пригласить друзей", url=f"https://t.me/share/url?url={quote(invite)}&text={text}")],
    ]
    extra = []
    if config.PROJECT_CHANNEL:
        extra.append(InlineKeyboardButton(text="📢 Наш канал", url=config.PROJECT_CHANNEL))
    if config.ADS_CONTACT:
        extra.append(InlineKeyboardButton(text="💰 Реклама", url=config.ADS_CONTACT))
    if extra:
        rows.append(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------------------------------------------------- приветствие

async def _send_with_card(bot: Bot, chat_id: int, text: str, kb: InlineKeyboardMarkup,
                          kind: str = "menu", data: dict | None = None) -> None:
    """Сообщение бота с баннером 16:9.

    kind="gate" — баннер просьбы подписаться, "menu" — главное меню. Если у
    человека уже есть итоги, в меню — его личный баннер. Общие баннеры
    заливаются в Telegram один раз, дальше уходят по file_id.
    """
    try:
        if data:
            path = await share.photo(data)
            await bot.send_photo(chat_id, FSInputFile(path), caption=text, reply_markup=kb)
            return
        key = f"banner_{kind}_{cards.PROMO_VERSION}"
        cached = await db.get_setting(key)
        if cached:
            try:
                await bot.send_photo(chat_id, cached, caption=text, reply_markup=kb)
                return
            except Exception:
                await db.set_setting(key, None)
        path = await share.promo(kind)
        msg = await bot.send_photo(chat_id, FSInputFile(path), caption=text, reply_markup=kb)
        if msg.photo:
            await db.set_setting(key, msg.photo[-1].file_id)
        return
    except Exception as error:
        log.warning("сообщение с баннером не ушло: %s", error)
    await bot.send_message(chat_id, text, reply_markup=kb)


async def show_menu(bot: Bot, user) -> None:
    data = await db.get_results(user.id, 7 * 86400)
    await _send_with_card(bot, user.id, menu_text(user.first_name), menu_kb(user.id), "menu", data)


async def passed(bot: Bot, user) -> None:
    """Человек прошёл подписку: засчитать другу и открыть меню."""
    inviter = await db.mark_passed(user.id)
    if inviter:
        n = await db.invited_count(inviter)
        try:
            await bot.send_message(
                inviter,
                f"👥 По твоей ссылке пришёл друг — <b>{html.escape(user.first_name or 'кто-то')}</b>!\n"
                f"Всего друзей: <b>{n}</b> ❤️",
            )
        except Exception:
            pass
    await show_menu(bot, user)


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start(message: Message, command: CommandObject, bot: Bot) -> None:
    arg = (command.args or "").strip()
    ref = int(arg[1:]) if arg.startswith("r") and arg[1:].isdigit() else None
    await db.touch_user(message.from_user, ref)
    need = await sponsors.missing(bot, message.from_user.id, fresh=True)
    if need:
        await _send_with_card(bot, message.chat.id, gate_text(message.from_user.first_name), gate_kb(need), "gate")
        return
    await passed(bot, message.from_user)


@router.callback_query(F.data == "check")
async def check(call: CallbackQuery, bot: Bot) -> None:
    need = await sponsors.missing(bot, call.from_user.id, fresh=True)
    if need:
        left = ", ".join(r["title"] or "канал" for r in need if r["chat_id"])
        await call.answer(f"Ещё не подписан(-а): {left}" if left else "Подпишись на всех спонсоров", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=gate_kb(need))
        except Exception:
            pass
        return
    await call.answer("Готово! Открываю итоги ✨")
    try:
        await call.message.delete()
    except Exception:
        pass
    await passed(bot, call.from_user)


# ------------------------------------------------------------- инлайн

@router.inline_query()
async def inline(query: InlineQuery, bot: Bot) -> None:
    """@бот в любом чате — живая карточка «Мои итоги».

    Если человек уже смотрел итоги, карточка его собственная. Если нет —
    общая «узнай свои итоги» и кнопка открыть бота.
    """
    user = query.from_user
    data = None
    row = await db.get_user(user.id)
    # Личная карточка — только если человек запускал бота и сейчас подписан
    # на спонсоров. Иначе общая карточка и кнопка «в бота» (там /start и ОП).
    subscribed = bool(row and row["passed_at"]) and not await sponsors.missing(bot, user.id)
    if subscribed:
        data = await db.get_results(user.id, 7 * 86400)
        if data is None:
            try:
                data = await asyncio.wait_for(
                    share.results(bot, row, bool(user.is_premium), None), timeout=6
                )
            except Exception as error:
                log.debug("инлайн без личных итогов: %s", error)
    try:
        result = await asyncio.wait_for(share.inline_result(bot, data, f"i{user.id}"), timeout=9)
    except Exception as error:
        log.warning("инлайн-карточка не готова: %s", error)
        result = await share.inline_result(bot, None, "generic")
    button = None
    if data is None:
        button = InlineQueryResultsButton(text="🦆 Проверить свои итоги", start_parameter="inline")
    await query.answer([result], cache_time=60, is_personal=True, button=button)


@router.chosen_inline_result()
async def chosen(result: ChosenInlineResult, bot: Bot) -> None:
    """Человек отправил карточку в чат — сразу правим подпись от имени бота.

    В самом инлайн-ответе Telegram заменяет премиум-эмодзи обычными (они
    разрешены только в сообщениях, которые бот шлёт или правит сам). Поэтому
    тут же редактируем подпись: прослойка emoji расставит иконки из пака, и
    в чате карточка окажется уже с премиум-эмодзи и иконкой на кнопке.
    Нужно включить у @BotFather: /setinlinefeedback → 100%.
    """
    if not result.inline_message_id:
        return
    data = None
    if result.result_id != "generic":
        data = await db.get_results(result.from_user.id, 7 * 86400)
    ref = result.from_user.id if data else None
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✨ Посмотреть свои", url=share.bot_link(ref), style="primary")
    ]])
    try:
        await bot.edit_message_caption(
            inline_message_id=result.inline_message_id,
            caption=share.caption(data),
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as error:
        log.info("подпись инлайн-карточки не обновить: %s", error)


@router.chat_join_request()
async def join_request(request: ChatJoinRequest, bot: Bot) -> None:
    """Заявки на вступление бот принимает сам — в любом чате и канале, где он
    админ с правом «Добавление участников». Человеку не нужно ждать админа,
    а проверка подписки на спонсора сразу видит его участником."""
    try:
        await request.approve()
    except Exception as error:
        log.info("заявку %s в %s не принять: %s", request.from_user.id, request.chat.id, error)
        return
    sponsors.forget(request.from_user.id)
