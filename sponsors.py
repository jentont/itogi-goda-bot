"""Обязательная подписка на спонсоров.

Спонсоры правятся в админке и живут в базе. Бывают двух видов:

* канал или чат (есть chat_id) — подписку проверяем через getChatMember.
  Бот должен быть админом в канале, иначе Telegram не ответит. Такой канал
  считаем пройденным: не держим людей за дверью из-за своей настройки.
* простая ссылка (бот, сайт) — проверить нельзя, просто показываем кнопку.

Ответ кэшируется на SUBSCRIBE_TTL: человек жмёт кнопки подряд, а лимиты
Bot API общие на весь бот.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot

import config
import db

log = logging.getLogger("itogi.sponsors")

INSIDE = ("member", "administrator", "creator", "restricted")

_passed: dict[int, float] = {}


def forget(user_id: int | None = None) -> None:
    if user_id is None:
        _passed.clear()
    else:
        _passed.pop(user_id, None)


def link_of(row: dict) -> str:
    if row.get("link"):
        return row["link"]
    if row.get("username"):
        return f"https://t.me/{row['username']}"
    return ""


async def missing(bot: Bot, user_id: int, fresh: bool = False) -> list[dict]:
    """Спонсоры, на которых человек не подписан. Пусто — можно дальше.

    Ссылки без chat_id в ответ попадают только пока человек не прошёл
    проверку целиком: их всё равно не проверить, а показывать их после
    прохождения — значит гонять человека по кругу.
    """
    if not fresh and _passed.get(user_id, 0) > time.time():
        return []
    rows = await db.sponsors()
    if not rows:
        return []

    out: list[dict] = []
    for row in rows:
        if not row["chat_id"]:
            continue
        try:
            member = await bot.get_chat_member(row["chat_id"], user_id)
        except Exception as error:
            log.warning("не проверить подписку на %s: %s", row["title"], error)
            continue
        if member.status in INSIDE or getattr(member, "is_member", False):
            if await db.sponsor_joined(row["id"], user_id):
                await _check_goal(bot, row["id"])
        else:
            out.append(row)

    if out:
        # Непроверяемые ссылки показываем вместе с каналами.
        out += [r for r in rows if not r["chat_id"]]
        return out
    _passed[user_id] = time.time() + config.SUBSCRIBE_TTL
    return []


async def everyone() -> list[dict]:
    """Все активные спонсоры — для первого показа."""
    return await db.sponsors()


async def _check_goal(bot: Bot, sponsor_id: int) -> None:
    row = await db.sponsor(sponsor_id)
    if not row or not row["goal"] or row["joined"] < row["goal"]:
        return
    await db.set_sponsor(sponsor_id, active=0)
    forget()
    await tell_admins(
        bot,
        f"🎉 Спонсор <b>{html.escape(row['title'] or '')}</b> набрал цель: "
        f"{row['joined']} из {row['goal']}. Снял его из подписки.",
    )


async def tell_admins(bot: Bot, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as error:
            log.warning("админу %s не отправить: %s", admin_id, error)


async def report_text() -> str:
    rows = await db.sponsors(active_only=False)
    if not rows:
        return "Спонсоров пока нет."
    lines = ["📊 <b>Спонсоры за сутки</b>", ""]
    for row in rows:
        today = await db.sponsor_today(row["id"])
        goal = f" / цель {row['goal']}" if row["goal"] else ""
        state = "" if row["active"] else " · снят"
        kind = "" if row["chat_id"] else " · ссылка"
        lines.append(
            f"• <b>{html.escape(row['title'] or '—')}</b>{kind}{state}\n"
            f"   за сутки +{today}, всего {row['joined']}{goal}"
        )
    return "\n".join(lines)


async def report_loop(bot: Bot) -> None:
    """Раз в сутки в REPORT_HOUR по Москве прислать отчёт админам."""
    while True:
        try:
            now = time.gmtime(time.time() + 3 * 3600)
            today = time.strftime("%Y-%m-%d", now)
            if now.tm_hour >= config.REPORT_HOUR and await db.get_setting("report_day") != today:
                await db.set_setting("report_day", today)
                if await db.sponsors(active_only=False):
                    await tell_admins(bot, await report_text())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("отчёт не ушёл")
        await asyncio.sleep(300)
