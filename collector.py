"""Аккаунт-сборщик (MTProto через Telethon) — подход из mogbattle.

Бот через Bot API не видит ни номера телефона, ни рыночных цен NFT. Обычный
аккаунт Telegram видит: открытый номер (+888 — анонимный номер с Fragment),
выставленные цены NFT-подарков и флор каждой коллекции. Сборщик подключается
из админки (/admin → Сборщик), его сессия хранится в базе строкой
(StringSession) — поэтому переживает пересборку контейнера на хостинге.

Если сборщика нет или Telegram его ограничил — бот работает как раньше:
NFT оцениваются по редкости, +888 не учитывается.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import config
import db

log = logging.getLogger("itogi.collector")

KEY_SESSION = "collector_session"
KEY_NAME = "collector_name"
KEY_API_ID = "collector_api_id"
KEY_API_HASH = "collector_api_hash"

_client: Any = None
_paused_until = 0.0
_lock = asyncio.Lock()
_floors: dict[int, int] = {}
_floors_at = 0.0


# --------------------------------------------------------------- ключи и сессия

async def api_keys() -> tuple[int, str]:
    """api_id/api_hash (my.telegram.org): из .env или сохранённые в админке."""
    if config.TG_API_ID and config.TG_API_HASH:
        return config.TG_API_ID, config.TG_API_HASH
    raw_id = await db.get_setting(KEY_API_ID)
    raw_hash = await db.get_setting(KEY_API_HASH)
    return (int(raw_id) if raw_id and raw_id.isdigit() else 0), (raw_hash or "")


async def connected() -> str | None:
    """Имя подключённого сборщика или None."""
    if not await db.get_setting(KEY_SESSION):
        return None
    return await db.get_setting(KEY_NAME) or "аккаунт"


async def save(session: str, name: str) -> None:
    await db.set_setting(KEY_SESSION, session)
    await db.set_setting(KEY_NAME, name)
    await reload()


async def drop() -> None:
    """Отключить сборщика: выйти из сессии и стереть её из базы."""
    global _client
    async with _lock:
        if _client is not None:
            try:
                await _client.log_out()
            except Exception:
                pass
            _client = None
    await db.set_setting(KEY_SESSION, None)
    await db.set_setting(KEY_NAME, None)


async def reload() -> None:
    global _client, _paused_until
    async with _lock:
        if _client is not None:
            try:
                await _client.disconnect()
            except Exception:
                pass
        _client = None
        _paused_until = 0.0


def make_client(session: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session), api_id, api_hash)


async def _get() -> Any:
    """Подключённый клиент сборщика или None (нет, на паузе, не вошёл)."""
    global _client
    if time.time() < _paused_until:
        return None
    async with _lock:
        if _client is not None and _client.is_connected():
            return _client
        session = await db.get_setting(KEY_SESSION)
        api_id, api_hash = await api_keys()
        if not session or not api_id or not api_hash:
            return None
        try:
            cli = make_client(session, api_id, api_hash)
            await cli.connect()
            if not await cli.is_user_authorized():
                log.warning("сессия сборщика больше не действует — подключи заново в /admin")
                await cli.disconnect()
                return None
            _client = cli
            return cli
        except Exception as error:
            log.warning("сборщик не подключился: %s", error)
            return None


def _flood_wait(error: Exception) -> int:
    seconds = getattr(error, "seconds", None)
    return int(seconds) if isinstance(seconds, int) else 0


def _pause(error: Exception) -> bool:
    """Telegram ограничил сборщика — отдыхаем, бот работает без него."""
    global _paused_until
    wait = _flood_wait(error)
    if wait:
        _paused_until = time.time() + wait + 5
        log.warning("сборщик на паузе %s сек (флуд-лимит Telegram)", wait)
        return True
    return False


# --------------------------------------------------------------- цены NFT (как в mogbattle)

def _stars_value(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, list):
        return max((_stars_value(x) for x in raw), default=0)
    amount = getattr(raw, "amount", None)
    return int(amount) if isinstance(amount, int) else 0


def _amount_in_stars(amount: Any, currency: Any) -> int:
    """Сумма в звёздах по валюте: звёзды штуками, доллары центами, TON нанотонами."""
    import fragment

    value = _stars_value(amount)
    if not value:
        return 0
    code = str(currency or "").upper()
    if code in ("", "XTR", "STARS"):
        return int(value)
    if code == "USD":
        return int(value / 100 / config.USD_PER_STAR)
    if code == "TON":
        return int(value / 1_000_000_000 * fragment.rate() / config.USD_PER_STAR)
    return 0


def _resell(gift: Any) -> int:
    """Выставленная цена: список сумм, где бывают и звёзды (StarsAmount),
    и TON (StarsTonAmount, в нанотонах) — переводим каждую по своей валюте."""
    best = 0
    for item in list(getattr(gift, "resell_amount", None) or []):
        kind = type(item).__name__
        amount = getattr(item, "amount", None)
        if not isinstance(amount, int) or amount <= 0:
            continue
        stars = _amount_in_stars(amount, "TON" if "Ton" in kind else "XTR")
        best = max(best, stars)
    return best


def _quoted(gift: Any) -> int:
    """Цена NFT, которую назвал сам Telegram: выставленная на продажу или оценка."""
    resell = _resell(gift)
    if resell:
        return resell
    for amount_field, currency_field in (
        ("value_amount", "value_currency"),
        ("price_amount", "price_currency"),
    ):
        price = _amount_in_stars(getattr(gift, amount_field, None), getattr(gift, currency_field, None))
        if price:
            return price
    usd_cents = getattr(gift, "value_usd_amount", None)
    if isinstance(usd_cents, int) and usd_cents > 0:
        return int(usd_cents / 100 / config.USD_PER_STAR)
    return 0


RARITY_TIERS = {"Uncommon": 150.0, "Rare": 50.0, "Epic": 15.0, "Legendary": 2.0}
KIND_NAMES = {"Backdrop": "фон", "Model": "модель", "Pattern": "узор"}


def _permille(item: Any) -> float:
    direct = getattr(item, "rarity_permille", None)
    if isinstance(direct, int) and direct > 0:
        return float(direct)
    rarity = getattr(item, "rarity", None)
    if isinstance(rarity, int):
        return float(rarity) if rarity > 0 else 0.0
    permille = getattr(rarity, "permille", None)
    if isinstance(permille, int) and permille > 0:
        return float(permille)
    name = type(rarity).__name__ if rarity is not None else ""
    for tier, value in RARITY_TIERS.items():
        if name.endswith(tier):
            return value
    return 0.0


def _attributes(gift: Any) -> tuple[str, float, float]:
    """Самый редкий признак NFT: «Black (фон)», редкость 0..1, доля в %."""
    import facts

    best = ("", 0.0, 0.0)
    for item in list(getattr(gift, "attributes", None) or []):
        permille = _permille(item)
        score = facts._rarity_score(permille)
        label = str(getattr(item, "name", "") or "").strip()
        if score <= best[1] or not label:
            continue
        kind = next((k for k in KIND_NAMES if k in type(item).__name__), "")
        best = (f"{label} ({KIND_NAMES[kind]})" if kind else label, score, round(permille / 10, 1))
    return best


def _is_nft(gift: Any) -> bool:
    return "Unique" in type(gift).__name__ or getattr(gift, "num", None) is not None


def nft_stars(gift: Any, floors: dict[int, int]) -> int:
    """Цена NFT: названная Telegram (если не дороже 10 флоров), иначе флор с
    надбавкой за редкость, иначе — оценка по редкости, как без сборщика."""
    import facts

    rarity = _attributes(gift)[1]
    gift_id = getattr(gift, "gift_id", None)
    floor = floors.get(gift_id, 0) if isinstance(gift_id, int) else 0
    worth = int(round(floor * (1.0 + facts.RARITY_BOOST * rarity))) if floor else 0
    price = _quoted(gift)
    if price:
        return worth if worth and price > worth * 10 else price
    if worth:
        return worth
    price = facts.NFT_STARS * (1.0 + facts.RARITY_BOOST * rarity)
    num = getattr(gift, "num", None)
    if isinstance(num, int) and 0 < num <= 100:
        price *= 1.8 if num <= 10 else 1.3
    return int(round(price))


async def _catalogue(cli: Any) -> dict[int, int]:
    """Флор каждой коллекции (самый дешёвый выставленный экземпляр), кэш на час."""
    global _floors, _floors_at
    if _floors_at and time.time() - _floors_at < 3600:
        return _floors
    try:
        from telethon.tl.functions.payments import GetStarGiftsRequest

        result = await cli(GetStarGiftsRequest(hash=0))
    except Exception as error:
        _pause(error)
        _floors_at = time.time()
        return _floors
    found = {}
    for item in list(getattr(result, "gifts", []) or []):
        floor = _stars_value(getattr(item, "resell_min_stars", None))
        if isinstance(getattr(item, "id", None), int) and floor > 0:
            found[item.id] = floor
    _floors, _floors_at = (found or _floors), time.time()
    return _floors


# --------------------------------------------------------------- что отдаём боту

async def lookup(user_id: int, username: str | None) -> dict:
    """Что видит сборщик: +888, NFT по рынку, самый редкий признак.

    Пусто — сборщика нет, человек ему недоступен или Telegram ограничил.
    """
    cli = await _get()
    if cli is None:
        return {}
    try:
        return await asyncio.wait_for(_lookup(cli, user_id, username), 25)
    except Exception as error:
        if not _pause(error):
            log.debug("сборщик не отдал %s: %s", user_id, error)
        return {}


async def _lookup(cli: Any, user_id: int, username: str | None) -> dict:
    from telethon.tl.functions.payments import GetSavedStarGiftsRequest

    entity = None
    for key in ([username] if username else []) + [user_id]:
        try:
            entity = await cli.get_entity(key)
            break
        except Exception as error:
            if _pause(error):
                return {}
    if entity is None:
        return {}

    phone = str(getattr(entity, "phone", "") or "").lstrip("+")
    out: dict = {"anon": phone.startswith("888")}

    floors = await _catalogue(cli)
    nft = nft_value = seen = 0
    rare = ("", 0.0, 0.0)
    offset = ""
    for _ in range(3):
        try:
            result = await cli(GetSavedStarGiftsRequest(peer=entity, offset=offset, limit=100))
        except Exception as error:
            _pause(error)
            break
        chunk = list(getattr(result, "gifts", []) or [])
        for saved in chunk:
            gift = getattr(saved, "gift", None)
            if gift is None or not _is_nft(gift):
                continue
            seen += 1
            nft += 1
            nft_value += nft_stars(gift, floors)
            attr = _attributes(gift)
            if attr[1] > rare[1]:
                rare = attr
        offset = getattr(result, "next_offset", "") or ""
        if not offset or not chunk:
            break
    if nft:
        out.update(nft=nft, nft_value=nft_value)
        if rare[0]:
            out["rare"] = {"name": rare[0], "pct": rare[2]}
    return out
