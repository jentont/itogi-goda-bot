"""Из чего складываются итоги года.

Только то, что Telegram действительно отдаёт боту, плюс номинации и
предсказание, которые честно подаются как игра:

* возраст аккаунта — оценка по id (точность — месяцы, как у всех ботов
  «сколько тебе лет в телеге»);
* Premium — из данных мини-аппа;
* подарки — getUserGifts: у каждого есть дата, поэтому считаем «было на
  1 января → стало сейчас» и лучший подарок года;
* уровень — getChat → rating: уровень, очки и сколько до следующего;
* место среди пользователей бота и приглашённые друзья.

Номинации и предсказание выпадают от id человека и года: у одного и того
же человека они не меняются, а у друзей разные — их интересно сравнить.
"""

from __future__ import annotations

import asyncio
import calendar
import gzip
import hashlib
import json
import logging
import math
import random
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot

import config
import db

log = logging.getLogger("itogi.facts")

GIFTS_DIR = config.WEBAPP_DIR / "gifts"

MONTHS_SHORT = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
MONTHS_IN = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

# --------------------------------------------------------------- возраст

#: Опорные точки «id → когда примерно заведён аккаунт». Id выдаются по
#: возрастанию, между точками интерполируем. Таблица из mogbattle.
ID_ANCHORS: tuple[tuple[int, str], ...] = (
    (1_000, "2013-08"), (200_000, "2013-09"), (1_000_000, "2013-10"),
    (10_000_000, "2014-02"), (40_000_000, "2014-10"), (60_000_000, "2014-12"),
    (100_000_000, "2015-11"), (150_000_000, "2016-08"), (200_000_000, "2017-03"),
    (300_000_000, "2018-02"), (400_000_000, "2019-06"), (500_000_000, "2020-02"),
    (700_000_000, "2020-12"), (900_000_000, "2021-03"), (1_000_000_000, "2021-05"),
    (1_300_000_000, "2021-09"), (1_500_000_000, "2022-01"), (1_800_000_000, "2022-06"),
    (2_000_000_000, "2022-12"), (5_000_000_000, "2023-08"), (6_000_000_000, "2024-01"),
    (7_000_000_000, "2024-08"), (7_500_000_000, "2025-01"), (8_000_000_000, "2025-08"),
    (8_500_000_000, "2026-04"),
)


def _ts(label: str) -> float:
    y, m = (int(p) for p in label.split("-"))
    return calendar.timegm((y, m, 1, 0, 0, 0))


def registered_at(user_id: int) -> float:
    a = ID_ANCHORS
    if user_id <= a[0][0]:
        return _ts(a[0][1])
    for (lo_id, lo), (hi_id, hi) in zip(a, a[1:]):
        if user_id <= hi_id:
            k = (user_id - lo_id) / (hi_id - lo_id)
            return _ts(lo) + (_ts(hi) - _ts(lo)) * k
    (p_id, p), (l_id, l) = a[-2], a[-1]
    speed = (_ts(l) - _ts(p)) / (l_id - p_id)
    return min(time.time() - 86400, _ts(l) + (user_id - l_id) * speed)


#: Аудитория Telegram в месяц по официальным заявлениям компании (млн).
#: Между точками интерполируем, после последней — продолжаем тот же темп.
TG_USERS: tuple[tuple[str, int], ...] = (
    ("2013-08", 0), ("2013-10", 1), ("2014-03", 35), ("2014-12", 50),
    ("2016-02", 100), ("2017-05", 150), ("2018-03", 200), ("2019-10", 300),
    ("2020-04", 400), ("2021-01", 500), ("2022-06", 700), ("2023-07", 800),
    ("2024-03", 900), ("2024-07", 950), ("2025-03", 1000),
)


def tg_users_at(ts: float) -> float:
    """Сколько миллионов человек было в Telegram в момент ts (оценка)."""
    pts = [(_ts(label), n) for label, n in TG_USERS]
    if ts <= pts[0][0]:
        return 0.0
    for (t0, n0), (t1, n1) in zip(pts, pts[1:]):
        if ts <= t1:
            return n0 + (n1 - n0) * (ts - t0) / (t1 - t0)
    (t0, n0), (t1, n1) = pts[-2], pts[-1]
    return n1 + (n1 - n0) * (ts - t1) / (t1 - t0)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def age(user_id: int) -> dict:
    at = registered_at(user_id)
    days = max(1, int((time.time() - at) // 86400))
    d = datetime.fromtimestamp(at, timezone.utc)
    years = days // 365
    if years >= 1:
        vibe = f"{years} {plural(years, 'год', 'года', 'лет')} с Telegram"
    else:
        vibe = "ты у нас совсем недавно"
    if d.year >= config.YEAR:
        title, sub = "Добро пожаловать!", "Ты пришёл(-ла) в Telegram в этом году"
    elif years >= 8:
        title, sub = "Старожил Telegram", "Ты был(-а) здесь, когда это ещё не было модно"
    elif years >= 4:
        title, sub = "Бывалый", "Ты видел(-а) Telegram без историй и подарков"
    else:
        title, sub = "Восходящая звезда", "Ты с нами уже не первый год"
    then = tg_users_at(at)
    now_users = tg_users_at(time.time())
    return {
        "days": days,
        "hours": days * 24,
        "users_then": int(round(then)),
        "users_now": int(round(now_users)),
        "earlier": max(0, min(99, int(100 * (1 - then / now_users)))) if now_users else 0,
        "since": f"{MONTHS_SHORT[d.month - 1]} {d.year}",
        "year": d.year,
        "vibe": vibe,
        "title": title,
        "sub": sub,
        "word": plural(days, "день", "дня", "дней"),
    }


# --------------------------------------------------------------- номинации

#: Номинации года. Это не статистика, а награды: подаются как «бот
#: присудил тебе», без выдуманных чисел про переписку.
NOMINATIONS: tuple[dict, ...] = (
    {"key": "night", "title": "Ночная уточка", "text": "Лучшие мысли приходят к тебе в 3 часа ночи — и сразу в чат", "sticker": "025", "theme": "night"},
    {"key": "devil", "title": "Дьявол переписок", "text": "Твоими голосовыми можно пугать кружочки", "sticker": "009", "theme": "purple"},
    {"key": "ghost", "title": "Призрак онлайна", "text": "Зашёл, прочитал, исчез. Легенда", "sticker": "021", "theme": "ice"},
    {"key": "speed", "title": "Скорость света", "text": "Отвечаешь раньше, чем тебе дописали", "sticker": "030", "theme": "red"},
    {"key": "fire", "title": "Огонь года", "text": "Всё, к чему ты прикасаешься в чате, — горит", "sticker": "014", "theme": "fire"},
    {"key": "agent", "title": "Тайный агент", "text": "Читаешь всё, пишешь редко, знаешь больше всех", "sticker": "028", "theme": "dark"},
    {"key": "soul", "title": "Душа чата", "text": "Без твоих «ахахах» общий чат бы заскучал", "sticker": "037", "theme": "orange"},
    {"key": "zen", "title": "Мастер дзена", "text": "Сто непрочитанных? Ты спокоен как уточка в пруду", "sticker": "011", "theme": "green"},
    {"key": "shock", "title": "Шок-контент", "text": "Каждая новость для тебя — новость года", "sticker": "004", "theme": "blue"},
    {"key": "chill", "title": "Король чилла", "text": "Пока все спорят в чате, ты лежишь у бассейна", "sticker": "040", "theme": "teal"},
    {"key": "storm", "title": "Под зонтом", "text": "Пережил(-а) все драмы года и даже не промок(-ла)", "sticker": "033", "theme": "blue"},
    {"key": "caps", "title": "Капс-лок года", "text": "ТВОИ ЭМОЦИИ ВИДНО ДАЖЕ БЕЗ ГОЛОСОВЫХ", "sticker": "008", "theme": "red"},
    {"key": "drama", "title": "Драма-квин", "text": "Из любого «ок» ты делаешь сериал на три сезона", "sticker": "038", "theme": "pink"},
    {"key": "shy", "title": "Скромняга", "text": "Сто раз начал(-а) печатать — и отправил(-а) стикер", "sticker": "023", "theme": "pink"},
    {"key": "clean", "title": "Чистюля чатов", "text": "Архивируешь чаты быстрее, чем они успевают надоесть", "sticker": "026", "theme": "teal"},
    {"key": "mask", "title": "Человек-загадка", "text": "Аватарка меняется чаще, чем погода", "sticker": "035", "theme": "dark"},
)

#: Цели на следующий год: человек выбирает до трёх, бот их помнит и
#: через год спрашивает, получилось ли.
PLAN_GOALS: tuple[dict, ...] = (
    {"key": "travel", "title": "Больше путешествовать", "sticker": "040"},
    {"key": "sport", "title": "Спорт и здоровье", "sticker": "030"},
    {"key": "sleep", "title": "Высыпаться", "sticker": "025"},
    {"key": "money", "title": "Накопить денег", "sticker": "018"},
    {"key": "love", "title": "Найти любовь", "sticker": "024"},
    {"key": "friends", "title": "Чаще видеть друзей", "sticker": "029"},
    {"key": "learn", "title": "Выучить новое", "sticker": "017"},
    {"key": "job", "title": "Рост в работе", "sticker": "032"},
    {"key": "project", "title": "Свой проект", "sticker": "013"},
    {"key": "calm", "title": "Меньше стресса", "sticker": "011"},
    {"key": "phone", "title": "Меньше телефона", "sticker": "027"},
    {"key": "read", "title": "Читать книги", "sticker": "012"},
)
PLAN_KEYS = {g["key"] for g in PLAN_GOALS}
PLAN_MAX = 3

PREDICTIONS: tuple[str, ...] = (
    "В 2027 тебе подарят подарок, о котором ты даже не мечтал(-а)",
    "В 2027 ты наконец-то разберёшь все непрочитанные. Ну, почти",
    "В 2027 твой стикер станет мемом в общем чате",
    "В 2027 кто-то очень важный напишет тебе первым",
    "В 2027 ты поднимешь уровень в Telegram выше, чем друзья",
    "В 2027 у тебя будет больше поводов для кружочков",
    "В 2027 тебя ждёт путешествие и куча фото в истории",
    "В 2027 твоё голосовое кто-то переслушает десять раз",
    "В 2027 удача будет отвечать тебе быстрее всех",
    "В 2027 ты найдёшь канал, который изменит твою ленту",
    "В 2027 ты получишь Premium — или он получит тебя",
    "В 2027 твой общий чат наконец соберётся вживую",
)


def fun_stats(user_id: int) -> list[dict]:
    """Шуточная статистика «по версии уточек». Числа выпадают от id
    (у одного человека всегда одни и те же) и на слайде подписаны как шутка."""
    r = _rng(user_id, "fun")
    sec = round(r.uniform(0.4, 3.9), 1)
    return [
        {"key": "ignore", "sticker": "023", "label": "Игнорил(-а) сообщения", "value": r.randint(1, 123), "unit": "дн."},
        {"key": "fast", "sticker": "030", "label": "Самый быстрый ответ", "value": str(sec).replace(".", ","), "unit": "сек"},
        {"key": "ahah", "sticker": "037", "label": "Написал(-а) «ахахах»", "value": r.randint(240, 4800), "unit": "раз"},
        {"key": "night", "sticker": "025", "label": "Сообщений после 3:00", "value": r.randint(12, 690), "unit": "шт."},
        {"key": "voice", "sticker": "036", "label": "Голосовых наговорил(-а)", "value": r.randint(18, 940), "unit": "мин"},
        {"key": "read", "sticker": "027", "label": "Прочитал(-а) и не ответил(-а)", "value": r.randint(30, 2100), "unit": "раз"},
    ]


def _rng(user_id: int, salt: str) -> random.Random:
    seed = hashlib.sha256(f"{user_id}:{config.YEAR}:{salt}".encode()).digest()
    return random.Random(int.from_bytes(seed[:8], "big"))


def nominations(user_id: int, count: int = 3) -> list[dict]:
    return _rng(user_id, "nom").sample(list(NOMINATIONS), count)


def prediction(user_id: int) -> str:
    return _rng(user_id, "pred").choice(PREDICTIONS)


# --------------------------------------------------------------- подарки


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(int(value), timezone.utc)
    except Exception:
        return None


async def _save_sticker(bot: Bot, sticker: Any) -> str | None:
    """Скачать TGS подарка и разжать в JSON для мини-аппа. Вернёт ключ."""
    if sticker is None or not getattr(sticker, "is_animated", False):
        return None
    key = sticker.file_unique_id
    target = GIFTS_DIR / f"{key}.json"
    if target.exists():
        return key
    try:
        GIFTS_DIR.mkdir(parents=True, exist_ok=True)
        file = await bot.get_file(sticker.file_id)
        buf = await bot.download_file(file.file_path)
        raw = buf.read() if buf else b""
        target.write_bytes(gzip.decompress(raw))
        return key
    except Exception as error:
        log.debug("стикер подарка не скачать: %s", error)
        return None


#: Цены подарков из каталога Telegram: id → (цена в звёздах, цена улучшения в NFT).
_prices: dict[str, tuple[int, int]] = {}
_prices_at = 0.0


async def _gift_prices(bot: Bot) -> dict[str, tuple[int, int]]:
    """Каталог подарков Telegram (getAvailableGifts), кэш на час."""
    global _prices_at
    if _prices and time.time() - _prices_at < 3600:
        return _prices
    try:
        got = await bot.get_available_gifts()
        for gift in got.gifts or []:
            _prices[str(gift.id)] = (int(gift.star_count or 0), int(getattr(gift, "upgrade_star_count", 0) or 0))
        _prices_at = time.time()
    except Exception as error:
        log.debug("каталог подарков не получить: %s", error)
    return _prices


#: Оценка NFT как в mogbattle: цена × (1 + RARITY_BOOST × редкость самого
#: редкого признака). Почти у каждого NFT признаки встречаются в 1–3% —
#: это норма, она уже сидит в флоре и надбавки не даёт. Дороже только
#: по-настоящему редкое: от 10‰ (редкость 0) до 1‰ (редкость 1); чёрный
#: фон (1,5‰) выходит ×3,5 к обычному экземпляру.
COMMON_PERMILLE = 10.0
RARITY_BOOST = 3.0
#: Сколько стоит обычный NFT, если рынка не видно: дешёвые массовые NFT
#: идут примерно по 200 ⭐ (~350 ₽). Лучше недооценить, чем обещать лишнее.
NFT_STARS = 200
ATTR_NAMES = (("backdrop", "фон"), ("model", "модель"), ("symbol", "узор"))


def _rarity_score(permille: float | None) -> float:
    """Редкость признака 0..1 по логарифмической шкале: 1‰ и 5‰ — огромная
    разница, 100‰ и 150‰ — никакой."""
    if not permille or permille <= 0:
        return 0.0
    scale = math.log10(COMMON_PERMILLE / float(permille)) / math.log10(COMMON_PERMILLE)
    return max(0.0, min(1.0, scale))


def rarest(gift) -> tuple[str, float, float]:
    """Самый редкий признак NFT: «Black (фон)», его редкость 0..1 и доля в %."""
    best = ("", 0.0, 0.0)
    for field, label in ATTR_NAMES:
        attr = getattr(gift, field, None)
        permille = getattr(attr, "rarity_per_mille", None)
        score = _rarity_score(permille)
        if attr is not None and score > best[1]:
            best = (f"{getattr(attr, 'name', '')} ({label})", score, round(permille / 10, 1))
    return best


def nft_estimate(gift, prices: dict[str, tuple[int, int]]) -> int:
    """Оценка NFT-подарка в звёздах.

    Рыночной цены и флора Telegram ботам не отдаёт, поэтому — прикидка по
    редкости, как в mogbattle: экземпляр с чёрным фоном (1,5‰) стоит в разы
    дороже такого же в обычной раскраске. Первые номера тиража — дороже.
    """
    star, upgrade = prices.get(str(getattr(gift, "gift_id", "") or ""), (0, 0))
    # Цена выпуска и улучшения — не рынок: массовые NFT стоят дешевле, чем
    # в них вложено. Поэтому база — типичный флор, а не сумма трат.
    base = NFT_STARS
    price = base * (1.0 + RARITY_BOOST * rarest(gift)[1])
    num = getattr(gift, "number", None)
    if isinstance(num, int) and 0 < num <= 100:
        price *= 1.8 if num <= 10 else 1.3
    return int(round(price, -1))


async def gifts(bot: Bot, user_id: int) -> dict | None:
    """Подарки в профиле: было на 1 января, сколько пришло за год, лучший."""
    items: list[Any] = []
    total = 0
    offset = ""
    try:
        for _ in range(5):
            got = await bot.get_user_gifts(user_id=user_id, offset=offset or None, limit=100)
            total = max(total, int(got.total_count or 0))
            items.extend(got.gifts or [])
            offset = got.next_offset or ""
            if not offset:
                break
    except Exception as error:
        log.debug("подарки %s не отдались: %s", user_id, error)
        return None

    year_start = datetime(config.YEAR, 1, 1, tzinfo=timezone.utc)
    this_year = [g for g in items if (_when(g.send_date) or year_start) >= year_start]
    seen = len(items)
    total = max(total, seen)
    before = max(0, total - len(this_year))

    months = [0] * 12
    stars_year = 0
    nft = 0
    prices = await _gift_prices(bot)
    nft_value = 0      # оценка NFT-подарков по редкости
    rare: tuple[str, float, float] = ("", 0.0, 0.0)  # самый редкий признак среди NFT
    value = 0          # сколько звёзд стоят обычные подарки в профиле
    limited = 0        # лимитированные (с тиражом)
    with_text = 0      # с подписью от дарителя
    hidden = 0         # анонимные (даритель скрыт)
    kinds: set[str] = set()
    senders: dict[int, dict] = {}
    best = None
    best_score = -1.0
    for g in items:
        when = _when(g.send_date)
        gift = g.gift
        unique = getattr(g, "type", "") == "unique"
        if unique:
            nft += 1
            nft_value += nft_estimate(gift, prices)
            r_name, r_score, r_pct = rarest(gift)
            if r_score > rare[1]:
                rare = (r_name, r_score, r_pct)
            kinds.add(str(getattr(gift, "gift_id", "") or getattr(gift, "base_name", "")))
        else:
            value += int(getattr(gift, "star_count", 0) or 0)
            kinds.add(str(getattr(gift, "id", "")))
            if getattr(gift, "total_count", None):
                limited += 1
            if getattr(g, "text", None):
                with_text += 1
        sender = getattr(g, "sender_user", None)
        if sender is None:
            hidden += 1
        elif not getattr(sender, "is_bot", False):
            row = senders.setdefault(sender.id, {"name": sender.first_name or "Друг", "count": 0})
            row["count"] += 1
        if when and when >= year_start:
            months[when.month - 1] += 1
            stars_year += int(getattr(gift, "star_count", 0) or 0)
        # Лучший подарок: NFT редкой модели важнее любых звёзд.
        if unique:
            model = getattr(gift, "model", None)
            permille = float(getattr(model, "rarity_per_mille", 0) or 1000)
            score = 100000 + (1000 - permille)
        else:
            score = float(getattr(gift, "star_count", 0) or 0)
        if score > best_score:
            best_score, best = score, g

    top = None
    if best is not None:
        gift = best.gift
        if getattr(best, "type", "") == "unique":
            model = getattr(gift, "model", None)
            backdrop = getattr(gift, "backdrop", None)
            colors = getattr(backdrop, "colors", None)
            permille = getattr(model, "rarity_per_mille", None)
            top = {
                "title": f"{gift.base_name} #{gift.number}",
                "nft": True,
                "estimate": nft_estimate(gift, prices),
                "backdrop": getattr(backdrop, "name", ""),
                "backdrop_rarity": round(getattr(backdrop, "rarity_per_mille", 0) / 10, 1) if getattr(backdrop, "rarity_per_mille", 0) else None,
                "rarity": round(permille / 10, 1) if permille else None,
                "stars": 0,
                "model": getattr(model, "name", ""),
                "sticker": await _save_sticker(bot, getattr(model, "sticker", None)),
                "colors": [
                    f"#{colors.center_color:06x}", f"#{colors.edge_color:06x}"
                ] if colors else None,
            }
        else:
            top = {
                "title": "Подарок",
                "nft": False,
                "stars": int(getattr(gift, "star_count", 0) or 0),
                "sticker": await _save_sticker(bot, getattr(gift, "sticker", None)),
                "colors": None,
            }
        when = _when(best.send_date)
        if when:
            top["date"] = f"{when.day} {MONTHS_GEN[when.month - 1]} {when.year}"

    best_month = None
    if any(months):
        idx = max(range(12), key=lambda i: months[i])
        best_month = {"name": MONTHS_IN[idx], "count": months[idx]}

    top_sender = None
    if senders:
        best_sender = max(senders.values(), key=lambda r: r["count"])
        top_sender = {"name": best_sender["name"][:32], "count": best_sender["count"]}

    now = datetime.now(timezone.utc)
    return {
        "months": months[: now.month] if now.year == config.YEAR else months,
        "value": value + nft_value,
        "value_regular": value,
        "value_nft": nft_value,
        "rare": {"name": rare[0], "pct": rare[2]} if rare[0] else None,
        "rub": int(round((value + nft_value) * config.STAR_RUB, -1)),
        "limited": limited,
        "with_text": with_text,
        "hidden": hidden,
        "kinds": len(kinds - {""}),
        "senders": len(senders),
        "top_sender": top_sender,
        "now": total,
        "before": before,
        "year": len(this_year) if seen >= total else max(0, total - before),
        "stars_year": stars_year,
        "nft": nft,
        "top": top,
        "best_month": best_month,
    }


# --------------------------------------------------------------- уровень


async def profile(bot: Bot, user_id: int) -> dict:
    """Уровень и пара мелочей профиля из getChat."""
    try:
        chat = await bot.get_chat(user_id)
    except Exception as error:
        log.debug("getChat %s: %s", user_id, error)
        return {}
    out: dict[str, Any] = {}
    rating = getattr(chat, "rating", None)
    if rating is not None:
        cur = int(rating.current_level_rating or 0)
        nxt = rating.next_level_rating
        have = int(rating.rating or 0)
        progress = 1.0
        if nxt:
            span = max(1, int(nxt) - cur)
            progress = max(0.0, min(1.0, (have - cur) / span))
        out["rating"] = {
            "level": int(rating.level or 0),
            "points": have,
            "next": int(nxt) if nxt else None,
            "left": max(0, int(nxt) - have) if nxt else 0,
            "progress": round(progress, 3),
        }
    birth = getattr(chat, "birthdate", None)
    if birth is not None:
        out["birthday"] = f"{birth.day} {MONTHS_GEN[birth.month - 1]}"
        out["bday"] = birthday_info(birth.day, birth.month, getattr(birth, "year", None))

    names = [n for n in (getattr(chat, "active_usernames", None) or []) if n]
    channel = getattr(chat, "personal_chat", None)
    out["profile"] = {
        "usernames": len(names),
        "bio": bool((getattr(chat, "bio", None) or "").strip()),
        "status": bool(getattr(chat, "emoji_status_custom_emoji_id", None)),
        "channel": (getattr(channel, "title", None) or None) if channel else None,
        "paid": int(getattr(chat, "paid_message_star_count", 0) or 0),
        "colors": bool(getattr(chat, "profile_accent_color_id", None) is not None
                       or getattr(chat, "unique_gift_colors", None)),
        "music": bool(getattr(chat, "first_profile_audio", None)),
        "photos": None,
    }
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        out["profile"]["photos"] = int(photos.total_count or 0)
    except Exception as error:
        log.debug("аватарки %s: %s", user_id, error)
    out["nft_names"] = await nft_usernames(names)
    return out


async def nft_usernames(names: list[str]) -> list[dict]:
    """Какие из юзернеймов куплены на Fragment и почём такие уходят (TON)."""
    import fragment

    async def one(name: str) -> dict | None:
        found = await fragment.look(name)
        if not found.collectible:
            return None
        return {"name": name, "ton": round(found.ton), "exact": found.exact}

    try:
        got = await asyncio.wait_for(asyncio.gather(*(one(n) for n in names[:5])), 15)
    except Exception as error:
        log.debug("Fragment не ответил вовремя: %s", error)
        return []
    return [x for x in got if x]


def worth(g: dict | None, nft_names: list[dict], anon: bool = False) -> dict:
    """Стоимость профиля в звёздах: подарки (NFT — оценка по редкости),
    коллекционные юзернеймы по Fragment."""
    import fragment

    usd_ton = fragment.rate()
    gifts_stars = int((g or {}).get("value") or 0)
    names_stars = int(sum(n["ton"] for n in nft_names) * usd_ton / config.USD_PER_STAR)
    anon_stars = int(config.ANON_NUMBER_USD / config.USD_PER_STAR) if anon else 0
    total = gifts_stars + names_stars + anon_stars
    return {
        "gifts": gifts_stars, "names": names_stars, "anon": anon_stars, "total": total,
        "rub": int(round(total * config.STAR_RUB, -1)),
        "usd": int(round(total * config.USD_PER_STAR)),
    }


ZODIAC = (
    (1, 20, "Козерог"), (2, 19, "Водолей"), (3, 20, "Рыбы"), (4, 20, "Овен"),
    (5, 21, "Телец"), (6, 21, "Близнецы"), (7, 22, "Рак"), (8, 23, "Лев"),
    (9, 23, "Дева"), (10, 23, "Весы"), (11, 22, "Скорпион"), (12, 22, "Стрелец"),
    (12, 31, "Козерог"),
)


def birthday_info(day: int, month: int, year: int | None, today: date | None = None) -> dict:
    """Знак зодиака, сколько дней до дня рождения и сколько исполнится."""
    today = today or datetime.now(timezone.utc).date()
    sign = next(name for m, d, name in ZODIAC if (month, day) <= (m, d))
    try:
        nxt = date(today.year, month, day)
    except ValueError:  # 29 февраля
        nxt = date(today.year, 3, 1)
    if nxt < today:
        try:
            nxt = date(today.year + 1, month, day)
        except ValueError:
            nxt = date(today.year + 1, 3, 1)
    turns = (nxt.year - year) if year else None
    return {"sign": sign, "left": (nxt - today).days, "turns": turns}


# --------------------------------------------------------------- всё вместе


async def build(bot: Bot, user: dict, premium: bool, photo_url: str | None) -> dict:
    """Собрать итоги человека. Медленные запросы — параллельно."""
    user_id = int(user["id"])
    gift_task = asyncio.create_task(gifts(bot, user_id))
    prof_task = asyncio.create_task(profile(bot, user_id))
    rank_task = asyncio.create_task(db.rank_of(user_id))
    inv_task = asyncio.create_task(db.invited_count(user_id))
    old_task = asyncio.create_task(db.older_share(user_id))
    import collector

    col_task = asyncio.create_task(collector.lookup(user_id, user.get("username")))
    g, prof, rank, invited, older, seen = await asyncio.gather(
        gift_task, prof_task, rank_task, inv_task, old_task, col_task
    )
    # Сборщик видит рынок: NFT по выставленной цене/флору вместо оценки по
    # редкости, и открытый номер +888.
    if g and seen.get("nft_value"):
        g["value_nft"] = seen["nft_value"]
        g["value"] = int(g.get("value_regular") or 0) + seen["nft_value"]
        g["rub"] = int(round(g["value"] * config.STAR_RUB, -1))
        g["nft"] = max(int(g.get("nft") or 0), int(seen.get("nft") or 0))
        if seen.get("rare"):
            g["rare"] = seen["rare"]
        g["market"] = True

    rating = prof.get("rating")
    if rating and int(rating.get("level") or 0) <= 0:
        rating = None  # отрицательный/нулевой уровень на карточке не показываем
    await db.save_snapshot(
        user_id,
        rating["level"] if rating else None,
        rating["points"] if rating else None,
        g["now"] if g else None,
    )
    # Снимок с прошлого года — для «был → стал» в следующих итогах.
    first = await db.first_snapshot(user_id)
    if rating and first and first["level"] is not None and first["day"] < f"{config.YEAR}-01-01":
        rating["was"] = int(first["level"])

    noms = nominations(user_id)
    name = (user.get("first_name") or "").strip() or "Уточка"
    nft_names = prof.get("nft_names") or []
    return {
        "year": config.YEAR,
        "user": {
            "id": user_id,
            "name": name,
            "username": user.get("username"),
            "photo": photo_url,
        },
        "age": age(user_id),
        "premium": bool(premium),
        "gifts": g,
        "rating": rating,
        "birthday": prof.get("birthday"),
        "bday": prof.get("bday"),
        "profile": prof.get("profile"),
        "older": older,
        "rank": rank,
        "invited": invited,
        "nominations": noms,
        "prediction": prediction(user_id),
        "fun": fun_stats(user_id),
        "nft_names": nft_names,
        "anon": bool(seen.get("anon")),
        "worth": worth(g, nft_names, bool(seen.get("anon"))),
        "at": int(time.time()),
    }


# --------------------------------------------------------------- итог года

def verdict(data: dict) -> dict:
    """Главный итог года — из настоящих данных, без выдумок.

    Берём самый сильный факт человека и называем его. Возвращает
    {"title", "why", "sticker"}: заголовок, пояснение цифрой и уточку.
    """
    a = data.get("age") or {}
    g = data.get("gifts") or {}
    r = data.get("rating") or {}
    options: list[tuple[float, str, str, str]] = []  # (сила, заголовок, почему, уточка)

    earlier = int(a.get("earlier") or 0)
    if earlier >= 95:
        options.append((97, "Легенда Telegram", f"в Telegram раньше {earlier}% людей", "028"))
    elif earlier >= 80:
        options.append((86, "Старожил Telegram", f"в Telegram раньше {earlier}% людей", "011"))
    elif earlier >= 50:
        options.append((62, "Бывалый", f"в Telegram раньше {earlier}% людей", "031"))
    if (a.get("days") or 9999) < 365:
        options.append((70, "Новичок года", f"в Telegram с {a.get('since', '')}", "002"))

    nft = int(g.get("nft") or 0)
    if nft:
        options.append((88 + min(nft, 9), "NFT-коллекционер",
                        f"{nft} {plural(nft, 'NFT-подарок', 'NFT-подарка', 'NFT-подарков')} в профиле", "010"))
    year_gifts = int(g.get("year") or 0)
    if year_gifts >= 10:
        options.append((90, "Магнит для подарков", f"+{year_gifts} подарков за {config.YEAR}", "018"))
    elif year_gifts >= 3:
        options.append((66, "Любимчик года", f"+{year_gifts} {plural(year_gifts, 'подарок', 'подарка', 'подарков')} за {config.YEAR}", "024"))
    rub = int(g.get("rub") or 0)
    if rub >= 10000:
        options.append((92, "Коллекция на миллион", f"подарки в профиле ≈ {rub:,} ₽".replace(",", " "), "018"))

    level = int(r.get("level") or 0)
    if level >= 15:
        options.append((89, "Звезда Telegram", f"уровень {level}", "030"))
    elif level >= 8:
        options.append((64, "На прокачке", f"уровень {level} в Telegram", "032"))
    if data.get("premium"):
        options.append((58, "Premium-житель", "весь год со звёздочкой", "010"))

    if not options:
        return {"title": "Уточка года", "why": f"мой {config.YEAR} в Telegram", "sticker": "039"}
    _, title, why, sticker = max(options, key=lambda o: o[0])
    return {"title": title, "why": why, "sticker": sticker}


def digest(data: dict) -> str:
    """Отпечаток того, что нарисовано на карточке, — для кэша."""
    keys = {
        "v": 6,
        "name": data["user"]["name"],
        "days": data["age"]["days"] // 7,
        "prem": data["premium"],
        "gifts": (data.get("gifts") or {}).get("now"),
        "lvl": (data.get("rating") or {}).get("level"),
        "verdict": verdict(data)["title"],
    }
    return hashlib.sha1(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:12]


def sticker_path(code: str) -> Path:
    return config.TGS_DIR / f"{code}.tgs"
