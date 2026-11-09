"""Веб-сервер мини-аппа: страница, стикеры, карточки и API.

Каждый запрос к API несёт initData от Telegram. Подпись проверяется по
токену бота — подделать чужой id без токена нельзя.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import hmac
import json
import logging
import time
from types import SimpleNamespace
from urllib.parse import parse_qsl

from aiogram import Bot
from aiohttp import web

import config
import db
import facts
import share
import sponsors

log = logging.getLogger("itogi.web")

#: Сколько живёт initData. Мини-апп могут держать открытым долго.
INIT_TTL = 24 * 3600

STICKERS_OUT = config.WEBAPP_DIR / "stickers"


def check_init(raw: str) -> dict | None:
    """Проверить initData. Вернёт поля с разобранным user или None."""
    if not raw:
        return None
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    if time.time() - int(pairs.get("auth_date", "0") or 0) > INIT_TTL:
        return None
    try:
        pairs["user"] = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    if not pairs["user"].get("id"):
        return None
    return pairs


def prepare_stickers() -> None:
    """Разжать TGS уточек в JSON для lottie в браузере (один раз)."""
    STICKERS_OUT.mkdir(parents=True, exist_ok=True)
    for tgs in config.TGS_DIR.glob("*.tgs"):
        out = STICKERS_OUT / f"{tgs.stem}.json"
        if not out.exists() or out.stat().st_mtime < tgs.stat().st_mtime:
            out.write_bytes(gzip.decompress(tgs.read_bytes()))


def _sponsor_view(rows: list[dict]) -> list[dict]:
    return [
        {"id": r["id"], "title": r["title"] or "Канал", "link": sponsors.link_of(r), "check": bool(r["chat_id"])}
        for r in rows
        if sponsors.link_of(r)
    ]


async def _auth(request: web.Request) -> tuple[dict, dict]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    init = check_init(body.get("init", ""))
    if init is None:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "bad_init"}), content_type="application/json")
    return init, body


def _ref(init: dict) -> int | None:
    param = init.get("start_param") or ""
    if param.startswith("r") and param[1:].isdigit():
        return int(param[1:])
    return None


async def _enter(request: web.Request, fresh_check: bool) -> web.Response:
    bot: Bot = request.app["bot"]
    init, body = await _auth(request)
    tg = init["user"]
    who = SimpleNamespace(
        id=int(tg["id"]),
        username=tg.get("username"),
        first_name=tg.get("first_name") or "",
        language_code=tg.get("language_code"),
        is_premium=bool(tg.get("is_premium")),
    )
    # Пользоваться можно только после /start в боте: без него человека нет в
    # базе — просим запустить бота (реф-ссылка сохраняется в кнопке).
    if await db.get_user(who.id) is None:
        return web.json_response({"state": "start", "link": share.bot_link(_ref(init))})
    row, _ = await db.touch_user(who)

    # Подписку проверяем при каждом входе, а не один раз: отписался — снова ОП.
    need = await sponsors.missing(bot, who.id, fresh=fresh_check)
    if need:
        return web.json_response({"state": "subscribe", "sponsors": _sponsor_view(need)})

    inviter = await db.mark_passed(who.id)
    if inviter:
        await _thank_inviter(bot, inviter, who.first_name)

    data = await share.results(bot, row, who.is_premium, tg.get("photo_url"), fresh=bool(body.get("fresh")))
    await db.mark_opened(who.id)
    share.warm(data)
    payload = dict(data)
    payload["links"] = {
        "invite": share.bot_link(who.id),
        "channel": config.PROJECT_CHANNEL,
        "bot": share.bot_link(),
    }
    payload["bonus"] = {"need": config.FRIENDS_FOR_BONUS, "have": data.get("invited", 0)}
    # План берём из базы, а не из кэша итогов: его могли только что сохранить.
    payload["plan"] = {
        "year": config.YEAR + 1,
        "max": facts.PLAN_MAX,
        "catalog": list(facts.PLAN_GOALS),
        "chosen": await db.get_plan(who.id, config.YEAR + 1),
        "last": await db.get_plan(who.id, config.YEAR),
    }
    return web.json_response({"state": "ready", "data": payload})


async def _thank_inviter(bot: Bot, inviter: int, name: str) -> None:
    n = await db.invited_count(inviter)
    try:
        await bot.send_message(
            inviter,
            f"👥 По твоей ссылке пришёл друг — <b>{html.escape(name or 'кто-то')}</b>!\n"
            f"Всего друзей: <b>{n}</b> ❤️",
        )
    except Exception:
        pass


async def api_start(request: web.Request) -> web.Response:
    return await _enter(request, fresh_check=True)


async def _allowed(bot: Bot, user_id: int) -> bool:
    """Запустил бота и подписан на спонсоров — можно делиться карточками."""
    if await db.get_user(user_id) is None:
        return False
    return not await sponsors.missing(bot, user_id)


def _locked() -> web.Response:
    return web.json_response({"error": "subscribe"}, status=403)


async def api_check(request: web.Request) -> web.Response:
    return await _enter(request, fresh_check=True)


async def api_share(request: web.Request) -> web.Response:
    """Подготовить сообщение с карточкой для Telegram.WebApp.shareMessage."""
    bot: Bot = request.app["bot"]
    init, _ = await _auth(request)
    user_id = int(init["user"]["id"])
    if not await _allowed(bot, user_id):
        return _locked()
    data = await db.get_results(user_id, 7 * 86400)
    if not data:
        return web.json_response({"error": "no_results"}, status=409)
    result = await share.inline_result(bot, data, f"m{user_id}{int(time.time())}")
    prepared = await bot.save_prepared_inline_message(
        user_id=user_id,
        result=result,
        allow_user_chats=True,
        allow_bot_chats=True,
        allow_group_chats=True,
        allow_channel_chats=True,
    )
    await db.mark_shared(user_id)
    return web.json_response({"id": prepared.id})


async def api_plan(request: web.Request) -> web.Response:
    """Сохранить цели на следующий год (до PLAN_MAX штук)."""
    init, body = await _auth(request)
    user_id = int(init["user"]["id"])
    if await db.get_user(user_id) is None:
        return _locked()
    raw = body.get("goals") or []
    goals = [g for g in dict.fromkeys(raw) if isinstance(g, str) and g in facts.PLAN_KEYS][: facts.PLAN_MAX]
    await db.save_plan(user_id, config.YEAR + 1, goals)
    return web.json_response({"ok": True, "goals": goals})


async def api_story(request: web.Request) -> web.Response:
    """Ссылка на вертикальное видео для Telegram.WebApp.shareToStory."""
    init, _ = await _auth(request)
    user_id = int(init["user"]["id"])
    if not await _allowed(request.app["bot"], user_id):
        return _locked()
    data = await db.get_results(user_id, 7 * 86400)
    if not data:
        return web.json_response({"error": "no_results"}, status=409)
    path = await share.card(data, "story")
    await db.mark_shared(user_id)
    return web.json_response({"url": share.public_url(path), "link": share.bot_link(user_id)})


async def index(request: web.Request) -> web.Response:
    resp = web.FileResponse(config.WEBAPP_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("ошибка в %s", request.path)
        return web.json_response({"error": "server"}, status=500)


def make_app(bot: Bot) -> web.Application:
    prepare_stickers()
    (config.WEBAPP_DIR / "gifts").mkdir(parents=True, exist_ok=True)
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application(middlewares=[errors], client_max_size=64 * 1024)
    app["bot"] = bot
    app.router.add_get("/", index)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/check", api_check)
    app.router.add_post("/api/share", api_share)
    app.router.add_post("/api/story", api_story)
    app.router.add_post("/api/plan", api_plan)
    app.router.add_static("/app/", config.WEBAPP_DIR)
    app.router.add_static("/cards/", config.CARDS_DIR)
    return app
