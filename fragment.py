"""Юзернейм на Fragment: коллекционный ли он и сколько такие стоят.

Подход из mogbattle. Telegram не говорит, откуда у человека имя, но Fragment
публикует статус каждого юзернейма открытой страницей:

    tm-status-taken">Taken       — обычное имя
    tm-status-unavail">Sold      — продано на аукционе, это NFT
    tm-status-avail">Available   — свободно

Цена — из открытой истории продаж: сделка по самому имени, а если её нет —
медиана продаж похожих имён той же длины. Ответы кэшируются на сутки.
Сеть закрыта или Fragment молчит — имя считается обычным: приписать
человеку NFT, которого нет, хуже, чем не заметить настоящий.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

log = logging.getLogger("itogi.fragment")

URL = "https://fragment.com/username/{name}"
SEARCH_URL = "https://fragment.com/?query={query}&type=usernames&filter=sold"
STATUS = re.compile(r'tm-status-(taken|unavail|avail)"[^>]*>([^<]*)', re.IGNORECASE)
COLLECTIBLE = ("sold", "auction", "sale")
ROW_RE = re.compile(r'<tr class="tm-row-selectable">(.*?)</tr>', re.S)
NAME_RE = re.compile(r"/username/([a-z0-9_]+)\"")
PRICE_RE = re.compile(r'icon-ton">([\d,\s]+)</div>')
RATE_SOURCES = (
    ("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd",
     lambda data: data["the-open-network"]["usd"]),
    ("https://tonapi.io/v2/rates?tokens=ton&currencies=usd",
     lambda data: data["rates"]["TON"]["prices"]["USD"]),
)
TON_USD_FALLBACK = 3.0
TIMEOUT = 6.0
TTL = 86400


@dataclass
class Name:
    username: str = ""
    collectible: bool = False
    ton: float = 0.0
    exact: bool = False


_cache: dict[str, tuple[Name, float]] = {}
_session = None
_rate = 0.0
_rate_at = 0.0


async def _get(url: str) -> str | None:
    global _session
    for attempt in range(2):
        try:
            if _session is None:
                import aiohttp

                _session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; itogi-bot)"},
                )
            async with _session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.text(errors="ignore")
        except Exception as error:
            log.debug("Fragment недоступен (%s, попытка %s): %s", url, attempt + 1, error)
    return None


def rate() -> float:
    """Курс TON в долларах. Никогда не ноль."""
    return _rate or TON_USD_FALLBACK


async def refresh_rate() -> float:
    global _rate, _rate_at
    if _rate and time.time() - _rate_at < 3600:
        return _rate
    for url, pick in RATE_SOURCES:
        body = await _get(url)
        if not body:
            continue
        try:
            value = float(pick(json.loads(body)))
        except Exception:
            continue
        if value > 0:
            _rate, _rate_at = value, time.time()
            return _rate
    _rate_at = time.time()
    return rate()


def parse(body: str) -> bool:
    found = STATUS.search(body)
    if not found:
        return False
    label = (found.group(2) or "").strip().lower()
    return found.group(1).lower() == "unavail" and any(m in label for m in COLLECTIBLE)


def parse_sales(body: str) -> list[tuple[str, float]]:
    out = []
    for chunk in ROW_RE.findall(body):
        name, price = NAME_RE.search(chunk), PRICE_RE.search(chunk)
        if not (name and price):
            continue
        try:
            out.append((name.group(1), float(price.group(1).replace(",", "").replace(" ", ""))))
        except ValueError:
            continue
    return out


def price_of(username: str, sales: list[tuple[str, float]]) -> tuple[float, bool]:
    """Сделка по самому имени или медиана соседей той же длины."""
    for name, ton in sales:
        if name == username:
            return ton, True
    pool = [t for n, t in sales if len(n) == len(username)] or \
           [t for n, t in sales if abs(len(n) - len(username)) <= 1]
    if not pool:
        return 0.0, False
    pool.sort()
    mid = len(pool) // 2
    return (pool[mid] if len(pool) % 2 else (pool[mid - 1] + pool[mid]) / 2), False


async def _price(name: str) -> tuple[float, bool]:
    stem = name[: max(3, min(4, len(name) - 1))]
    body = await _get(SEARCH_URL.format(query=stem))
    sales = parse_sales(body) if body else []
    if len(sales) < 5 and len(stem) > 3:
        body = await _get(SEARCH_URL.format(query=stem[:3]))
        seen = {n for n, _ in sales}
        sales += [s for s in (parse_sales(body) if body else []) if s[0] not in seen]
    return price_of(name, sales)


async def look(username: str | None) -> Name:
    """Коллекционное ли имя и почём такие уходят (TON)."""
    if not username:
        return Name()
    name = username.lstrip("@").lower()
    cached = _cache.get(name)
    if cached and time.time() - cached[1] < TTL:
        return cached[0]
    body = await _get(URL.format(name=name))
    if body is None:
        return Name(username=name)
    found = Name(username=name, collectible=parse(body))
    if found.collectible:
        try:
            await refresh_rate()
            found.ton, found.exact = await asyncio.wait_for(_price(name), TIMEOUT * 2)
        except Exception as error:
            log.debug("цена @%s не узналась: %s", name, error)
    _cache[name] = (found, time.time())
    return found
