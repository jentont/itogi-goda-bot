"""Админка: /admin.

* 📢 Спонсоры — добавить канал (переслать пост, @юзернейм, ссылку или id),
  добавить простую ссылку (бот, сайт), цель по подпискам, вкл/выкл, удалить;
* 📊 Статистика — люди, прошедшие подписку, открывшие итоги, поделившиеся;
* 📨 Рассылка — любое сообщение всем, с подтверждением;
* 🦆 Пак эмодзи — собрать свой пак премиум-эмодзи из уточек (/makepack).
"""

from __future__ import annotations

import asyncio
import gzip
import html
import json
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    Message,
)

import config
import db
import collector
import emoji
import pack
import sponsors

log = logging.getLogger("itogi.admin")
router = Router(name="admin")


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


router.message.filter(F.from_user.func(lambda u: is_admin(u.id)), F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.from_user.func(lambda u: is_admin(u.id)))


class Form(StatesGroup):
    channel = State()
    link = State()
    goal = State()
    post = State()


def B(text: str, data: str, **kw) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, **kw)


def panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B("📢 Спонсоры", "a:sp")],
        [B("📊 Статистика", "a:stats"), B("📨 Рассылка", "a:post")],
        [B("🦆 Пак эмодзи", "a:pack"), B("🕵️ Сборщик", "a:col")],
    ])


BACK = [B("⬅️ Назад", "a:main")]


@router.message(Command("admin", "panel"))
async def panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("⚙️ <b>Админка «Итоги года»</b>", reply_markup=panel_kb())


@router.callback_query(F.data == "a:main")
async def to_panel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(call, "⚙️ <b>Админка «Итоги года»</b>", panel_kb())


async def _edit(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None) -> None:
    await call.answer()
    try:
        await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest:
        # Сообщение могло быть с картинкой или не измениться.
        await call.message.answer(text, reply_markup=kb, disable_web_page_preview=True)


# ------------------------------------------------------------- статистика

@router.callback_query(F.data == "a:stats")
async def stats(call: CallbackQuery) -> None:
    s = await db.stats()
    conv = lambda a, b: f"{(a / b * 100):.0f}%" if b else "—"  # noqa: E731
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего: <b>{s['users']}</b> (+{s['today']} за сутки)\n"
        f"✅ Прошли подписку: <b>{s['passed']}</b> ({conv(s['passed'], s['users'])})\n"
        f"✨ Открыли итоги: <b>{s['opened']}</b> ({conv(s['opened'], s['passed'])})\n"
        f"🎉 Поделились: <b>{s['shared']}</b> ({conv(s['shared'], s['opened'])})\n"
        f"❤️ Пришли по ссылкам: <b>{s['refs']}</b>\n"
        f"😎 Premium: <b>{s['premium']}</b>\n"
        f"🗑 Заблокировали бота: <b>{s['blocked']}</b>"
    )
    await _edit(call, text, InlineKeyboardMarkup(inline_keyboard=[[B("📊 Отчёт по спонсорам", "a:report")], BACK]))


@router.callback_query(F.data == "a:report")
async def report(call: CallbackQuery) -> None:
    await _edit(call, await sponsors.report_text(), InlineKeyboardMarkup(inline_keyboard=[BACK]))


# ------------------------------------------------------------- спонсоры

async def sponsors_screen() -> tuple[str, InlineKeyboardMarkup]:
    rows = await db.sponsors(active_only=False)
    lines = ["📢 <b>Спонсоры</b>", ""]
    kb = []
    if not rows:
        lines.append("Пока пусто — итоги открываются без подписки.")
    for r in rows:
        mark = "🟢" if r["active"] else "⏸"
        kind = "канал" if r["chat_id"] else "ссылка"
        goal = f" · {r['joined']}/{r['goal']}" if r["goal"] else f" · {r['joined']}"
        lines.append(f"{mark} <b>{html.escape(r['title'] or '—')}</b> — {kind}{goal}")
        kb.append([B(f"{mark} {r['title'] or '—'}", f"a:s:{r['id']}")])
    lines += ["", "Бот должен быть <b>админом</b> в канале, иначе подписку не проверить."]
    kb.append([B("➕ Канал", "a:add"), B("➕ Ссылка", "a:addlink")])
    kb.append(BACK)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data == "a:sp")
async def sp_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await sponsors_screen()
    await _edit(call, text, kb)


async def sponsor_card(sponsor_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    r = await db.sponsor(sponsor_id)
    if not r:
        return None
    today = await db.sponsor_today(sponsor_id)
    text = (
        f"📢 <b>{html.escape(r['title'] or '—')}</b>\n\n"
        f"Ссылка: {html.escape(sponsors.link_of(r) or '—')}\n"
        f"Тип: {'канал/чат, подписка проверяется' if r['chat_id'] else 'ссылка без проверки'}\n"
        f"Статус: {'активен' if r['active'] else 'на паузе'}\n"
        f"Вступили через бота: <b>{r['joined']}</b> (за сутки +{today})\n"
        f"Цель: <b>{r['goal'] or 'нет'}</b>"
    )
    toggle = "⏸ Пауза" if r["active"] else "▶️ Включить"
    kb = [
        [B("🎯 Цель", f"a:goal:{sponsor_id}"), B(toggle, f"a:tog:{sponsor_id}")],
        [B("🗑 Удалить", f"a:del:{sponsor_id}", style="danger")],
        [B("⬅️ Назад", "a:sp")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.startswith("a:s:"))
async def sp_open(call: CallbackQuery) -> None:
    got = await sponsor_card(int(call.data.split(":")[2]))
    if got:
        await _edit(call, *got)


@router.callback_query(F.data.startswith("a:tog:"))
async def sp_toggle(call: CallbackQuery) -> None:
    sid = int(call.data.split(":")[2])
    r = await db.sponsor(sid)
    if r:
        await db.set_sponsor(sid, active=0 if r["active"] else 1)
        sponsors.forget()
    got = await sponsor_card(sid)
    if got:
        await _edit(call, *got)


@router.callback_query(F.data.startswith("a:del:"))
async def sp_del(call: CallbackQuery) -> None:
    sid = int(call.data.split(":")[2])
    await _edit(call, "Точно удалить спонсора?", InlineKeyboardMarkup(inline_keyboard=[
        [B("🗑 Да, удалить", f"a:del!:{sid}", style="danger"), B("⬅️ Нет", f"a:s:{sid}")],
    ]))


@router.callback_query(F.data.startswith("a:del!:"))
async def sp_del_go(call: CallbackQuery) -> None:
    await db.drop_sponsor(int(call.data.split(":")[2]))
    sponsors.forget()
    text, kb = await sponsors_screen()
    await _edit(call, "✅ Удалил.\n\n" + text, kb)


@router.callback_query(F.data.startswith("a:goal:"))
async def sp_goal(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.goal)
    await state.update_data(sid=int(call.data.split(":")[2]))
    await _edit(call, "🎯 Сколько вступлений набрать? Пришли число. 0 — без цели.\n"
                      "Когда цель наберётся, бот сам снимет канал и напишет тебе.",
                InlineKeyboardMarkup(inline_keyboard=[[B("⬅️ Отмена", "a:sp")]]))


@router.message(Form.goal, F.text)
async def sp_goal_set(message: Message, state: FSMContext) -> None:
    if not message.text.strip().isdigit():
        await message.answer("Нужно число, например 5000.")
        return
    sid = (await state.get_data())["sid"]
    await db.set_sponsor(sid, goal=int(message.text.strip()))
    await state.clear()
    got = await sponsor_card(sid)
    if got:
        await message.answer(*got[:1], reply_markup=got[1])


@router.callback_query(F.data == "a:add")
async def sp_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.channel)
    await _edit(call, (
        "➕ <b>Новый канал-спонсор</b>\n\n"
        "Сначала сделай бота админом в канале. Потом пришли сюда одно из:\n"
        "• любой пост из канала (переслать)\n"
        "• @юзернейм или ссылку t.me/…\n"
        "• id канала (-100…)\n\n"
        "Можно добавить цель через пробел: <code>@channel 5000</code>"
    ), InlineKeyboardMarkup(inline_keyboard=[[B("⬅️ Отмена", "a:sp")]]))


def _target(message: Message) -> tuple[str | int | None, int]:
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) or getattr(message, "forward_from_chat", None)
    goal = 0
    if chat is not None:
        return chat.id, goal
    raw = (message.text or "").strip()
    parts = raw.split()
    if len(parts) > 1 and parts[-1].isdigit():
        goal = int(parts[-1])
        raw = " ".join(parts[:-1])
    m = re.search(r"(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{4,})", raw)
    if m and m.group(1) not in ("joinchat", "c"):
        return "@" + m.group(1), goal
    if raw.lstrip("-").isdigit():
        return int(raw), goal
    return None, goal


@router.message(Form.channel)
async def sp_add_save(message: Message, state: FSMContext, bot: Bot) -> None:
    target, goal = _target(message)
    if target is None:
        await message.answer("Не понял, что это за канал. Перешли пост или пришли @юзернейм.")
        return
    try:
        chat = await bot.get_chat(target)
    except Exception as error:
        await message.answer(f"Telegram не отдал канал: {html.escape(str(error))}\nБот добавлен в канал?")
        return
    warn = ""
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        if me.status not in ("administrator", "creator"):
            warn = "\n⚠️ Бот не админ в канале — подписку проверить не получится."
    except Exception:
        warn = "\n⚠️ Бот не видит канал — сделай его админом."
    link = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not link:
        try:
            invite = await bot.create_chat_invite_link(chat.id, name="Итоги года")
            link = invite.invite_link
        except Exception:
            warn += "\n⚠️ У канала нет юзернейма, а ссылку-приглашение бот создать не смог."
    await db.add_sponsor(chat.id, chat.title or chat.username or str(chat.id), chat.username, link, goal)
    sponsors.forget()
    await state.clear()
    text, kb = await sponsors_screen()
    await message.answer(f"✅ Добавил <b>{html.escape(chat.title or '')}</b>{warn}\n\n" + text, reply_markup=kb)


@router.callback_query(F.data == "a:addlink")
async def sp_addlink(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.link)
    await _edit(call, (
        "➕ <b>Спонсор-ссылка</b> (бот, сайт — без проверки подписки)\n\n"
        "Пришли в формате:\n<code>Название | https://t.me/somebot?start=ref</code>"
    ), InlineKeyboardMarkup(inline_keyboard=[[B("⬅️ Отмена", "a:sp")]]))


@router.message(Form.link, F.text)
async def sp_addlink_save(message: Message, state: FSMContext) -> None:
    if "|" not in message.text:
        await message.answer("Нужно «Название | ссылка».")
        return
    title, link = (p.strip() for p in message.text.split("|", 1))
    if not link.startswith(("https://", "http://", "tg://")):
        await message.answer("Ссылка должна начинаться с https://")
        return
    await db.add_sponsor(None, title[:64], None, link)
    sponsors.forget()
    await state.clear()
    text, kb = await sponsors_screen()
    await message.answer("✅ Ссылка добавлена\n\n" + text, reply_markup=kb)


# ------------------------------------------------------------- рассылка

@router.callback_query(F.data == "a:post")
async def post_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.post)
    await _edit(call, "📨 Пришли сообщение для рассылки — текст, фото, видео, что угодно. "
                      "Кнопки и премиум-эмодзи сохранятся.",
                InlineKeyboardMarkup(inline_keyboard=[[B("⬅️ Отмена", "a:main")]]))


@router.message(Form.post)
async def post_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(chat=message.chat.id, msg=message.message_id)
    n = len(await db.audience())
    await message.answer(
        f"Отправить это сообщение <b>{n}</b> людям?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [B("🚀 Отправить", "a:post!", style="success"), B("⬅️ Отмена", "a:main")],
        ]),
    )


@router.callback_query(F.data == "a:post!")
async def post_go(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    if "msg" not in data:
        await call.answer("Сообщение потерялось, начни заново", show_alert=True)
        return
    await call.answer("Рассылка пошла")
    status = await call.message.answer("📨 Рассылаю…")
    asyncio.create_task(_broadcast(bot, data["chat"], data["msg"], status))


async def _broadcast(bot: Bot, chat: int, msg: int, status: Message) -> None:
    ids = await db.audience()
    ok = bad = 0
    for i, uid in enumerate(ids, 1):
        while True:
            try:
                await bot.copy_message(uid, chat, msg)
                ok += 1
                break
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after + 1)
            except (TelegramForbiddenError, TelegramBadRequest):
                await db.mark_blocked(uid)
                bad += 1
                break
            except Exception:
                bad += 1
                break
        await asyncio.sleep(0.04)
        if i % 500 == 0:
            try:
                await status.edit_text(f"📨 Рассылаю… {i}/{len(ids)}")
            except Exception:
                pass
    await status.edit_text(f"✅ Рассылка закончена\nДоставлено: <b>{ok}</b>\nНе дошло: <b>{bad}</b>")


# ------------------------------------------------------------- сборщик (как в mogbattle)

class Col(StatesGroup):
    keys = State()
    phone = State()
    code = State()
    password = State()


#: Начатые входы: id админа → {"client", "phone", "hash"}.
_logins: dict[int, dict] = {}

CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[[B("✖️ Отмена", "a:colx")]])


async def _drop_login(admin_id: int) -> None:
    login = _logins.pop(admin_id, None)
    if login and login.get("client") is not None:
        try:
            await login["client"].disconnect()
        except Exception:
            pass


@router.callback_query(F.data == "a:col")
async def col_screen(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    name = await collector.connected()
    text = (
        "🕵️ <b>Аккаунт-сборщик</b>\n\n"
        + (f"Подключён: <b>{html.escape(name)}</b> ✅\n\n" if name else "Не подключён.\n\n")
        + "Это обычный аккаунт Telegram, через который бот видит то, чего не отдаёт Bot API:\n"
        "• открытый номер <b>+888</b> — прибавляется к стоимости профиля;\n"
        "• <b>рыночные цены NFT</b>: выставленная цена и флор коллекции вместо оценки по редкости.\n\n"
        "⚠️ Заведи для этого <b>отдельный</b> аккаунт: если Telegram сочтёт активность "
        "подозрительной, ограничат именно его. Сессия хранится в базе бота, "
        "код и пароль бот удаляет из чата сразу."
    )
    rows = [[B("🔄 Подключить заново", "a:colgo"), B("🗑 Отключить", "a:coldel")]] if name else [[B("➕ Подключить", "a:colgo")]]
    await _edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows + [BACK]))


@router.callback_query(F.data == "a:colx")
async def col_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await _drop_login(call.from_user.id)
    await col_screen(call, state)


@router.callback_query(F.data == "a:coldel")
async def col_delete(call: CallbackQuery) -> None:
    await _edit(call, "Отключить сборщика? Бот выйдет из его сессии.", InlineKeyboardMarkup(inline_keyboard=[
        [B("🗑 Да, отключить", "a:coldel2"), B("⬅️ Нет", "a:col")],
    ]))


@router.callback_query(F.data == "a:coldel2")
async def col_delete_ok(call: CallbackQuery, state: FSMContext) -> None:
    await collector.drop()
    await col_screen(call, state)


@router.callback_query(F.data == "a:colgo")
async def col_go(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    api_id, api_hash = await collector.api_keys()
    if not api_id or not api_hash:
        await state.set_state(Col.keys)
        await call.message.answer(
            "1️⃣ Нужны ключи приложения: зайди на <b>my.telegram.org</b> → API development tools "
            "и пришли сюда <b>api_id</b> и <b>api_hash</b> через пробел.\n\n"
            "Например: <code>1234567 0123456789abcdef0123456789abcdef</code>",
            reply_markup=CANCEL_KB,
        )
        return
    await state.set_state(Col.phone)
    await call.message.answer("📱 Пришли номер аккаунта-сборщика, например <code>+79991234567</code>",
                              reply_markup=CANCEL_KB)


@router.message(Col.keys)
async def col_keys(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    try:
        await message.delete()
    except Exception:
        pass
    if len(parts) != 2 or not parts[0].isdigit() or len(parts[1]) < 20:
        await message.answer("Не похоже на api_id и api_hash. Пришли два значения через пробел.", reply_markup=CANCEL_KB)
        return
    await db.set_setting(collector.KEY_API_ID, parts[0])
    await db.set_setting(collector.KEY_API_HASH, parts[1])
    await state.set_state(Col.phone)
    await message.answer("✅ Ключи сохранены.\n\n📱 Теперь номер аккаунта-сборщика, например <code>+79991234567</code>",
                         reply_markup=CANCEL_KB)


@router.message(Col.phone)
async def col_phone(message: Message, state: FSMContext) -> None:
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if not 10 <= len(digits) <= 15:
        await message.answer("Это не номер. Пример: <code>+79991234567</code>", reply_markup=CANCEL_KB)
        return
    phone = f"+{digits}"
    await _drop_login(message.from_user.id)
    api_id, api_hash = await collector.api_keys()
    client = collector.make_client("", api_id, api_hash)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except Exception as error:
        try:
            await client.disconnect()
        except Exception:
            pass
        await message.answer(f"❌ Код не отправился: {html.escape(str(error))}", reply_markup=CANCEL_KB)
        return
    _logins[message.from_user.id] = {"client": client, "phone": phone, "hash": sent.phone_code_hash}
    await state.set_state(Col.code)
    await message.answer(
        "2️⃣ Telegram прислал код — обычно в приложение, в чат «Telegram» этого аккаунта.\n\n"
        "Пришли его <b>с пробелами</b>: <code>1 2 3 4 5</code>. Без пробелов Telegram заметит "
        "код в переписке и отменит его — это его защита от мошенников.",
        reply_markup=CANCEL_KB,
    )


@router.message(Col.code)
async def col_code(message: Message, state: FSMContext) -> None:
    code = "".join(ch for ch in (message.text or "") if ch.isdigit())
    try:
        await message.delete()
    except Exception:
        pass
    login = _logins.get(message.from_user.id)
    if login is None:
        await state.clear()
        await message.answer("Вход потерялся, начни заново: /admin → Сборщик.")
        return
    from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

    try:
        await login["client"].sign_in(phone=login["phone"], code=code, phone_code_hash=login["hash"])
    except SessionPasswordNeededError:
        await state.set_state(Col.password)
        await message.answer("3️⃣ На аккаунте облачный пароль — пришли его (сообщение удалю сразу).",
                             reply_markup=CANCEL_KB)
        return
    except PhoneCodeInvalidError:
        await message.answer("Код не подошёл, пришли ещё раз (с пробелами).", reply_markup=CANCEL_KB)
        return
    except PhoneCodeExpiredError:
        await _drop_login(message.from_user.id)
        await state.clear()
        await message.answer("Код устарел — начни заново: /admin → Сборщик.")
        return
    except Exception as error:
        await message.answer(f"❌ Не вошёл: {html.escape(str(error))}", reply_markup=CANCEL_KB)
        return
    await _col_finish(message, state)


@router.message(Col.password)
async def col_password(message: Message, state: FSMContext) -> None:
    password = message.text or ""
    try:
        await message.delete()
    except Exception:
        pass
    login = _logins.get(message.from_user.id)
    if login is None:
        await state.clear()
        await message.answer("Вход потерялся, начни заново: /admin → Сборщик.")
        return
    try:
        await login["client"].sign_in(password=password)
    except Exception:
        await message.answer("Пароль не подошёл, пришли ещё раз.", reply_markup=CANCEL_KB)
        return
    await _col_finish(message, state)


async def _col_finish(message: Message, state: FSMContext) -> None:
    login = _logins.pop(message.from_user.id)
    client = login["client"]
    try:
        me = await client.get_me()
        name = f"@{me.username}" if getattr(me, "username", None) else login["phone"]
    except Exception:
        name = login["phone"]
    session = client.session.save()
    try:
        await client.disconnect()
    except Exception:
        pass
    await collector.save(session, name)
    await state.clear()
    log.info("сборщик %s подключён из админки", name)
    await message.answer(
        f"✅ Сборщик <b>{html.escape(name)}</b> подключён. Теперь в итогах учитываются открытый "
        "+888 и рыночные цены NFT.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[BACK]),
    )


# ------------------------------------------------------------- пак эмодзи

@router.callback_query(F.data == "a:pack")
async def pack_screen(call: CallbackQuery, bot: Bot) -> None:
    me = await bot.me()
    name = await db.get_setting("emoji_pack") or config.EMOJI_PACK or emoji.pack_name(me.username)
    text = (
        "🦆 <b>Пак премиум-эмодзи</b>\n\n"
        f"{html.escape(emoji.status())}\n\n"
        f"Ссылка: https://t.me/addemoji/{name}\n\n"
        "«Собрать» — бот создаст свой пак из уточек (владелец — ты). "
        "После этого уточки появятся на кнопках и в текстах.\n\n"
        "⚠️ Telegram показывает премиум-эмодзи у ботов только если у бота есть "
        "купленный на Fragment юзернейм. Без него бот сам откатится на обычные эмодзи."
    )
    await _edit(call, text, InlineKeyboardMarkup(inline_keyboard=[
        [B("🦆 Собрать пак", "a:mk"), B("🔄 Перечитать", "a:rl")],
        BACK,
    ]))


def _tgs_for_emoji(path) -> bytes:
    """TGS для премиум-эмодзи по правилам Telegram: холст 100×100, до 3 секунд.

    Стикеры нарисованы на 512×512 — уносим все слои в прекомп и кладём его
    одним слоем с масштабом 100/512. Картинка и анимация не меняются.
    """
    data = json.loads(gzip.decompress(path.read_bytes()))
    fr = float(data.get("fr", 60))
    op = min(float(data.get("op", fr * 3)), fr * 3 - 1)
    data["op"] = op
    w, h = int(data.get("w", 512)), int(data.get("h", 512))
    if (w, h) != (100, 100):
        data.setdefault("assets", []).append({"id": "__emoji_root", "layers": data["layers"]})
        data["layers"] = [{
            "ddd": 0, "ind": 1, "ty": 0, "nm": "emoji", "refId": "__emoji_root", "sr": 1, "ao": 0,
            "ks": {
                "o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [50, 50, 0]}, "a": {"a": 0, "k": [w / 2, h / 2, 0]},
                "s": {"a": 0, "k": [10000 / w, 10000 / h, 100]},
            },
            "w": w, "h": h, "ip": 0, "op": op, "st": 0, "bm": 0,
        }]
        data["w"] = data["h"] = 100
    return gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9)


@router.callback_query(F.data == "a:mk")
@router.message(Command("makepack"))
async def make_pack(event, bot: Bot) -> None:
    user = event.from_user
    reply = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    me = await bot.me()
    name = emoji.pack_name(me.username)
    status = await reply.answer(f"🦆 Собираю пак из {len(emoji.PACK_ITEMS)} эмодзи…")

    async def notify(text: str) -> None:
        try:
            await status.edit_text(text)
        except Exception:
            pass

    try:
        count = await pack.ensure(bot, name, user.id, notify)
    except Exception as error:
        await status.edit_text(f"❌ Пак не собрался: {html.escape(str(error))}\n\n"
                               "Чтобы бот мог создать пак, напиши ему /start со своего аккаунта.")
        return
    await db.set_setting("emoji_pack", name)
    loaded = await emoji.load(bot, name)
    await status.edit_text(f"✅ Готово, {count} эмодзи: https://t.me/addemoji/{name}\n{html.escape(loaded)}",
                           disable_web_page_preview=True)


@router.callback_query(F.data == "a:rl")
async def pack_reload(call: CallbackQuery, bot: Bot) -> None:
    me = await bot.me()
    name = await db.get_setting("emoji_pack") or config.EMOJI_PACK or emoji.pack_name(me.username)
    await emoji.load(bot, name)
    await pack_screen(call, bot)
